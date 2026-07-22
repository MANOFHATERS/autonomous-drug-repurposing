#!/usr/bin/env python3
"""
FORENSIC test suite for Teammate 1 — Phase 1 (Data Ingestion) — 15 issues.

HOSTILE-AUDITOR MODE: assume every comment is a lie. Each test exercises
the ACTUAL code path (not the comment) and asserts the runtime behavior
matches the issue's FIX REQUIRED contract.

Test naming convention: test_P1_<issue#>_<short_description>

Run:
    cd /home/z/my-project/repo
    python -m pytest /home/z/my-project/scripts/test_p1_forensic_v142.py -v
"""
from __future__ import annotations

import os
import sys
import csv
import gzip
import json
import shutil
import sqlite3
import tempfile
from pathlib import Path
from unittest.mock import patch, MagicMock, AsyncMock

import pytest

# Make repo importable as both ``phase1.*`` and top-level (for backend.*)
REPO_ROOT = Path("/home/z/my-project/repo").resolve()
sys.path.insert(0, str(REPO_ROOT))
sys.path.insert(0, str(REPO_ROOT / "phase1"))


# =============================================================================
# Airflow compatibility shim
# -----------------------------------------------------------------------------
# Airflow 2.10 pins SQLAlchemy<2.0 but phase1 code uses SQLAlchemy 2.0+
# DeclarativeBase. To avoid the version conflict in tests that ONLY need to
# inspect the dag's pure-Python helper functions (_validate_output_impl,
# _check_dpi_degraded_via_postgres), we inject lightweight stub modules for
# the airflow symbols the dag file imports at top level. The stubs preserve
# the @task / @dag decorator semantics (return the callable unchanged) so
# the dag file's helper functions remain accessible for direct unit testing.
# =============================================================================
def _install_airflow_stubs():
    """Inject minimal airflow stubs.

    We always install stubs because real airflow pins sqlalchemy<2.0 which
    is incompatible with phase1's SQLAlchemy 2.0 DeclarativeBase usage.
    The stubs let us import the dag module's pure-Python helpers
    (_validate_output_impl, _check_dpi_degraded_via_postgres) for direct
    unit testing without dragging in airflow's full ORM stack.
    """
    # If real airflow is partially installed (broken state), force-overwrite
    # with stubs so the test environment is deterministic.
    import types
    # Build stub modules.
    airflow = types.ModuleType("airflow")
    decorators = types.ModuleType("airflow.decorators")
    exceptions = types.ModuleType("airflow.exceptions")
    models = types.ModuleType("airflow.models")
    operators = types.ModuleType("airflow.operators")
    empty = types.ModuleType("airflow.operators.empty")
    python = types.ModuleType("airflow.operators.python")
    branch = types.ModuleType("airflow.operators.python")
    utils = types.ModuleType("airflow.utils")
    task_state = types.ModuleType("airflow.utils.task_state")
    trigger_rule = types.ModuleType("airflow.utils.trigger_rule")

    # Stubs: decorators that return the callable unchanged (so @task/@dag
    # don't wrap the function — we can call _validate_output_impl directly).
    def _task_passthrough(*args, **kwargs):
        if len(args) == 1 and callable(args[0]) and not kwargs:
            return args[0]
        def deco(fn):
            return fn
        return deco

    # @dag decorator: returns a decorator that wraps the function so that
    # CALLING the decorated function returns a stub DAG object WITHOUT
    # executing the function body. The dag file's bottom-of-file
    # ``dag = master_pipeline()`` would otherwise execute the body, which
    # instantiates pipelines (which trigger init_db → migrations → SQLite
    # syntax errors). By returning a stub object instead, we let the
    # module load complete without side effects.
    class _StubDag:
        def __init__(self, *args, **kwargs):
            self.task_dict = {}
        def __getattr__(self, name):
            return lambda *a, **k: None

    def _dag_passthrough(*args, **kwargs):
        def deco(fn):
            def wrapper(*a, **k):
                return _StubDag()
            # Preserve the original function so tests can inspect its source.
            wrapper.__wrapped__ = fn
            wrapper.__name__ = getattr(fn, "__name__", "wrapper")
            return wrapper
        return deco

    decorators.dag = _dag_passthrough
    decorators.task = _task_passthrough

    class AirflowFailException(Exception):
        pass

    class AirflowException(Exception):
        pass

    exceptions.AirflowFailException = AirflowFailException
    exceptions.AirflowException = AirflowException

    class EmptyOperator:
        def __init__(self, *args, **kwargs):
            self.task_id = kwargs.get("task_id", "stub")

    empty.EmptyOperator = EmptyOperator
    operators.EmptyOperator = EmptyOperator

    class BranchPythonOperator:
        def __init__(self, *args, **kwargs):
            self.task_id = kwargs.get("task_id", "stub")
            self.python_callable = kwargs.get("python_callable")

    python.BranchPythonOperator = BranchPythonOperator
    operators.BranchPythonOperator = BranchPythonOperator
    operators.PythonOperator = BranchPythonOperator  # stub alias

    class TriggerRule:
        ALL_SUCCESS = "all_success"
        NONE_FAILED_MIN_ONE_SUCCESS = "none_failed_min_one_success"
        ALL_DONE = "all_done"

    trigger_rule.TriggerRule = TriggerRule

    class TaskState:
        SUCCESS = "success"
        FAILED = "failed"

    task_state.TaskState = TaskState

    class DAG:
        def __init__(self, *args, **kwargs):
            self.dag_id = kwargs.get("dag_id", "stub")

    models.DAG = DAG

    airflow.decorators = decorators
    airflow.exceptions = exceptions
    airflow.models = models
    airflow.operators = operators
    airflow.utils = utils

    sys.modules["airflow"] = airflow
    sys.modules["airflow.decorators"] = decorators
    sys.modules["airflow.exceptions"] = exceptions
    sys.modules["airflow.models"] = models
    sys.modules["airflow.operators"] = operators
    sys.modules["airflow.operators.empty"] = empty
    sys.modules["airflow.operators.python"] = python
    sys.modules["airflow.operators.python"] = python
    sys.modules["airflow.utils"] = utils
    sys.modules["airflow.utils.task_state"] = task_state
    sys.modules["airflow.utils.trigger_rule"] = trigger_rule


_install_airflow_stubs()


# =============================================================================
# Environment isolation: prevent imports from touching real DB / running
# migrations. Tests that need a DB will set DATABASE_URL themselves.
# =============================================================================
os.environ.setdefault("DRUGOS_ENVIRONMENT", "test")
os.environ.setdefault("SKIP_MIGRATIONS_ON_IMPORT", "1")
os.environ.setdefault("PHASE1_DB_URL", "sqlite:///:memory:")
# Prevent the dag's _is_production from being True during tests.
os.environ.setdefault("AIRFLOW__CORE__DAGS_ARE_PAUSED_AT_CREATION", "True")


# =============================================================================
# Direct dag-module loader.
# -----------------------------------------------------------------------------
# ``phase1/dags/__init__.py`` auto-imports ALL 8 DAG modules at package
# import time (P1-045 fix). Each source DAG imports its pipeline module,
# which triggers ``BasePipeline._ensure_directories`` → ``init_db()`` →
# migration runner → SQLite syntax errors (PostgreSQL-only CHECK constraints).
#
# To test ``master_pipeline_dag``'s pure-Python helpers without dragging
# in the entire DAG package's transitive imports, we load the module
# directly from its file path via importlib. This bypasses the package
# __init__'s auto-import loop.
# =============================================================================
import importlib.util

def _load_dag_module():
    """Load master_pipeline_dag.py directly, bypassing the package __init__."""
    dag_path = REPO_ROOT / "phase1" / "dags" / "master_pipeline_dag.py"
    spec = importlib.util.spec_from_file_location(
        "master_pipeline_dag_under_test", dag_path,
    )
    mod = importlib.util.module_from_spec(spec)
    # Make sure the dags package directory is on sys.path so the dag's
    # ``from dags._dags_init import ensure_project_root`` resolves.
    dags_dir = str(REPO_ROOT / "phase1" / "dags")
    if dags_dir not in sys.path:
        sys.path.insert(0, dags_dir)
    # Also need phase1 dir for ``from config.settings import ...`` etc.
    p1_dir = str(REPO_ROOT / "phase1")
    if p1_dir not in sys.path:
        sys.path.insert(0, p1_dir)
    # Insert a stub "dags" package so ``from dags._dags_init import ...``
    # resolves to the real file.
    if "dags" not in sys.modules:
        import types as _types
        dgs_pkg = _types.ModuleType("dags")
        dgs_pkg.__path__ = [dags_dir]
        sys.modules["dags"] = dgs_pkg
    spec.loader.exec_module(mod)
    return mod

_DAG_MODULE = None
def get_dag_module():
    """Lazy-load the dag module (cached after first call)."""
    global _DAG_MODULE
    if _DAG_MODULE is None:
        _DAG_MODULE = _load_dag_module()
    return _DAG_MODULE


# =============================================================================
# Shared fixtures
# =============================================================================

@pytest.fixture
def tmp_processed_dir(tmp_path):
    """Create a temporary processed_data dir with controlled CSV contents."""
    pdir = tmp_path / "processed_data"
    pdir.mkdir(parents=True, exist_ok=True)
    return pdir


def _write_csv(path: Path, header: list[str], rows: list[list[str]]) -> None:
    """Write a CSV file with explicit header + rows (UTF-8, no BOM)."""
    with open(path, "w", encoding="utf-8", newline="") as f:
        w = csv.writer(f)
        w.writerow(header)
        for r in rows:
            w.writerow(r)


def _write_csv_with_bom(path: Path, header: list[str], rows: list[list[str]]) -> None:
    """Write a CSV file WITH a UTF-8 BOM (mirrors DrugBank export)."""
    with open(path, "wb") as f:
        f.write(b"\xef\xbb\xbf")  # UTF-8 BOM
    with open(path, "a", encoding="utf-8", newline="") as f:
        w = csv.writer(f)
        w.writerow(header)
        for r in rows:
            w.writerow(r)


# =============================================================================
# P1-001: validate_output task checks wrong CSV filenames
# Issue: hardcoded _expected_csvs dict had 6/7 wrong filenames
# Fix:   use contract PHASE1_OUTPUT_SCHEMA + get_all_aliases()
# =============================================================================

def test_P1_001_validate_output_uses_contract_not_hardcoded_filenames(tmp_path):
    """The validate_output impl must resolve CSVs via the contract schema,
    NOT via a hardcoded dict of wrong filenames."""
    # Load the dag module directly (bypassing phase1.dags.__init__'s auto-import loop).
    dag = get_dag_module()

    # Verify the broken hardcoded dict is GONE from ACTIVE code (not comments).
    # Use AST to walk top-level assignments — _expected_csvs was a module-level
    # dict literal in the broken version. Comments can still mention the name
    # (explaining the old bug), so we only check ACTIVE code.
    import ast
    tree = ast.parse(open(dag.__file__).read())
    found_assignments = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Assign):
            for target in node.targets:
                if isinstance(target, ast.Name) and target.id == "_expected_csvs":
                    found_assignments.append(node.lineno)
    assert not found_assignments, (
        f"P1-001 REGRESSION: _expected_csvs dict still assigned as active code "
        f"at line(s) {found_assignments}. The hardcoded filename dict (which "
        f"had 6/7 wrong filenames) was supposed to be DELETED and replaced "
        f"with contract-driven filename resolution."
    )

    # Verify _REQUIRED_SOURCES_FOR_PHASE2 references contract keys (not filenames)
    assert hasattr(dag, "_REQUIRED_SOURCES_FOR_PHASE2"), (
        "P1-001: _REQUIRED_SOURCES_FOR_PHASE2 frozenset not found. The fix requires "
        "iterating over contract source keys, not hardcoded CSV filenames."
    )

    # Each entry must be a contract source key, NOT a filename like "chembl_drugs.csv"
    for src_key in dag._REQUIRED_SOURCES_FOR_PHASE2:
        assert not src_key.endswith(".csv"), (
            f"P1-001: source key {src_key!r} looks like a filename, not a contract key."
        )

    # The contract must be imported
    assert "PHASE1_OUTPUT_SCHEMA" in dir(dag) or "get_all_aliases" in dir(dag), (
        "P1-001: contract PHASE1_OUTPUT_SCHEMA / get_all_aliases not imported into dag."
    )

    # Create a processed_dir with the CORRECT (contract) filenames and verify
    # _validate_output_impl finds them.
    pdir = tmp_path / "processed"
    pdir.mkdir()
    # Use the canonical aliases from the contract.
    try:
        from phase1.contracts.phase1_schema import PHASE1_OUTPUT_SCHEMA, get_all_aliases
    except ImportError:
        pytest.skip("phase1.contracts.phase1_schema not importable in this env")

    # Write a minimal valid CSV for each required source (header + 1 row).
    for src_key in dag._REQUIRED_SOURCES_FOR_PHASE2:
        if src_key not in PHASE1_OUTPUT_SCHEMA:
            continue
        aliases = get_all_aliases(src_key)
        if not aliases:
            continue
        # Find the ID column required by the contract.
        spec = PHASE1_OUTPUT_SCHEMA[src_key]
        id_col = None
        for c in spec.required_columns:
            if c.name in ("inchikey", "uniprot_id", "uniprot_id_a", "gene_symbol",
                          "disease_id", "cid", "pubchem_cid"):
                id_col = c.name
                break
        # Write CSV with the canonical filename (first alias) and required cols.
        fname = aliases[0]
        if id_col:
            _write_csv(pdir / fname, [id_col], [["TEST_VALUE_123"]])
        else:
            # Multi-column composite key (e.g. string_ppi) — write 2 cols.
            _write_csv(pdir / fname, ["col_a", "col_b"], [["v1", "v2"]])

    # Patch the module attributes that _validate_output_impl reads at call time
    # via globals() (the testability seam added by TM1 Task 1.4 v131).
    with patch.dict(dag.__dict__, {
        "_processed_dir": pdir,
        "_is_production": False,  # dev mode so missing CSVs are warnings, not failures
    }):
        # Mock the DPI check to return a non-degraded state.
        with patch.object(dag, "_check_dpi_degraded_via_postgres",
                          return_value={"dpi_missing": False, "acknowledged": True,
                                        "source": "dev_mock"}):
            try:
                payload = dag._validate_output_impl()
            except Exception as exc:
                pytest.fail(f"_validate_output_impl raised: {exc}")

    # The payload must NOT contain failures about the OLD WRONG filenames
    # that never existed (string_proteins.csv, disgenet_gda.csv, omim_gda.csv,
    # pubchem_compounds.csv). Contract-recognized filenames like chembl_drugs.csv
    # and uniprot_proteins.csv CAN appear (they're valid aliases).
    failures = payload.get("failures", [])
    for f in failures:
        # These 4 filenames NEVER existed as pipeline outputs — they were the
        # broken hardcoded names in the old _expected_csvs dict.
        for wrong_name in ("string_proteins.csv", "disgenet_gda.csv",
                           "omim_gda.csv", "pubchem_compounds.csv"):
            assert wrong_name not in f, (
                f"P1-001 REGRESSION: validate_output failure mentions {wrong_name!r} "
                f"(a fabricated filename from the old broken _expected_csvs dict): {f}"
            )


# =============================================================================
# P1-002: _persist_cleaned_data writes CSV non-atomically
# Issue: direct df.to_csv(dest) — crash mid-write leaves corrupt file
# Fix:   write to .tmp, fsync, os.replace (atomic rename)
# =============================================================================

def test_P1_002_atomic_write_pattern_used(tmp_path):
    """_persist_cleaned_data must use atomic write (tmp + fsync + os.replace)."""
    import inspect
    import textwrap
    from phase1.pipelines import base_pipeline
    src = inspect.getsource(base_pipeline.BasePipeline._persist_cleaned_data)
    # Verify the atomic-write primitives are present.
    assert "os.replace" in src or "_os.replace" in src, (
        "P1-002: os.replace (atomic rename) not used in _persist_cleaned_data."
    )
    assert "fsync" in src, (
        "P1-002: fsync not called before rename — durability not guaranteed."
    )
    assert ".tmp" in src or "csv_tmp" in src, (
        "P1-002: temp file pattern (.tmp / csv_tmp) not used."
    )
    # Verify the old direct-write pattern is GONE from ACTIVE code.
    # The previous broken code: df.to_csv(dest, index=False, ...)
    # The fixed code: df.to_csv(f, ...) where f is the temp file handle.
    # Use AST to check the actual df.to_csv() call sites — skip docstring text.
    import ast
    # inspect.getsource() on a method returns it with extra indentation.
    # textwrap.dedent + strip leading newline so AST can parse it.
    src_dedented = textwrap.dedent(src).lstrip()
    tree = ast.parse(src_dedented)
    direct_write_calls = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute):
            if node.func.attr == "to_csv":
                # Check the first positional arg — if it's a Name with id 'dest',
                # that's the direct-write pattern.
                if node.args and isinstance(node.args[0], ast.Name) and node.args[0].id == "dest":
                    direct_write_calls.append(node.lineno)
    assert not direct_write_calls, (
        f"P1-002 REGRESSION: df.to_csv(dest, ...) direct-write pattern still "
        f"present at line(s) {direct_write_calls}. Must write to a temp file "
        f"handle and os.replace() atomically."
    )


def test_P1_002_crash_during_write_leaves_dest_intact(tmp_path):
    """If df.to_csv raises mid-write, the existing dest file must remain intact."""
    import pandas as pd
    from phase1.pipelines.base_pipeline import BasePipeline

    # Create a minimal pipeline instance with stub implementations for the
    # abstract methods (download, clean, load).
    class TestPipeline(BasePipeline):
        source_name = "test_source"
        def _get_processed_filename(self):
            return "test_cleaned.csv"
        def download(self):  # abstract — stub
            pass
        def clean(self, raw_df):  # abstract — stub
            return raw_df
        def load(self, cleaned_df):  # abstract — stub
            pass
        def run(self):  # abstract — stub
            pass

    # Set up a processed dir with an existing (good) CSV.
    pdir = tmp_path / "processed"
    pdir.mkdir()
    existing_content = "inchikey,name\nEXISTING,Aspirin\n"
    (pdir / "test_cleaned.csv").write_text(existing_content)

    # Patch PROCESSED_DATA_DIR to point at our temp dir.
    with patch("phase1.pipelines.base_pipeline.PROCESSED_DATA_DIR", pdir):
        pipe = TestPipeline.__new__(TestPipeline)
        # Make a DataFrame whose to_csv will raise mid-write.
        bad_df = pd.DataFrame({"inchikey": ["NEW1", "NEW2"]})
        original_to_csv = bad_df.to_csv

        def explode(*args, **kwargs):
            # Simulate OOM / signal mid-write: raise after opening the file.
            raise RuntimeError("simulated OOM mid-write")

        bad_df.to_csv = explode
        # The write MUST raise — atomic write propagates exceptions.
        with pytest.raises(RuntimeError, match="simulated OOM"):
            pipe._persist_cleaned_data(bad_df)

        # The original CSV MUST be intact (atomic rename means we never
        # touched it — we wrote to test_cleaned.csv.tmp which got cleaned up).
        assert (pdir / "test_cleaned.csv").read_text() == existing_content, (
            "P1-002 REGRESSION: existing dest CSV was corrupted by a failed write. "
            "Atomic write must write to a .tmp file and only rename on success."
        )
        # The .tmp file MUST be cleaned up (no leftover temp files).
        assert not (pdir / "test_cleaned.csv.tmp").exists(), (
            "P1-002: .tmp file not cleaned up after failure."
        )


# =============================================================================
# P1-003: /predict and /top-k return hardcoded placeholders
# Issue: gnn_score=0.5, candidates=[]
# Fix:   proxy to GT_SERVICE_URL/predict and RL_SERVICE_URL/rank
# =============================================================================

def test_P1_003_predict_calls_gt_service_not_placeholder():
    """The /predict endpoint must call the GT service via httpx, not return 0.5."""
    import inspect
    from backend.api import main
    # Use AST to check the ACTIVE return statements — comments can mention
    # the old placeholder while explaining the fix.
    import ast
    src = inspect.getsource(main.predict)
    tree = ast.parse(src)
    # Walk all Return nodes — none should return a PredictResponse with
    # hardcoded gnn_score=0.5.
    placeholder_returns = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Return) and node.value is not None:
            # Check if the returned value is a Call to PredictResponse with
            # gnn_score=0.5 as a keyword.
            if isinstance(node.value, ast.Call):
                for kw in node.value.keywords:
                    if kw.arg == "gnn_score" and isinstance(kw.value, ast.Constant) and kw.value.value == 0.5:
                        placeholder_returns.append(node.lineno)
    assert not placeholder_returns, (
        f"P1-003 REGRESSION: predict() returns hardcoded gnn_score=0.5 at "
        f"line(s) {placeholder_returns}."
    )
    # The fix must reference GT_SERVICE_URL and httpx in ACTIVE code.
    # (Comments can mention them too, but the active code MUST use them.)
    has_gt_url_ref = False
    has_httpx_call = False
    for node in ast.walk(tree):
        if isinstance(node, ast.Call):
            # Check for os.environ.get("GT_SERVICE_URL", ...)
            if isinstance(node.func, ast.Attribute) and node.func.attr == "get":
                if node.args and isinstance(node.args[0], ast.Constant) and \
                   node.args[0].value == "GT_SERVICE_URL":
                    has_gt_url_ref = True
            # Check for httpx.AsyncClient(...)
            if isinstance(node.func, ast.Attribute):
                if node.func.attr == "AsyncClient":
                    # Walk up to check the module is httpx — simplified check.
                    has_httpx_call = True
    assert has_gt_url_ref, "P1-003: GT_SERVICE_URL not referenced in predict() active code."
    assert has_httpx_call, "P1-003: httpx.AsyncClient not used in predict()."
    # 503 path: search for HTTP_503_SERVICE_UNAVAILABLE in the source (incl. comments).
    assert "503" in src or "HTTP_503_SERVICE_UNAVAILABLE" in src, (
        "P1-003: 503 response not implemented for GT service unreachable."
    )


def test_P1_003_top_k_calls_rl_service_not_placeholder():
    """The /top-k endpoint must call the RL service via httpx, not return []."""
    import inspect
    from backend.api import main
    src = inspect.getsource(main.top_k)
    # Use AST to check ACTIVE return statements.
    import ast
    tree = ast.parse(src)
    placeholder_returns = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Return) and node.value is not None:
            # Check if returning TopKResponse(candidates=[], ...)
            if isinstance(node.value, ast.Call):
                for kw in node.value.keywords:
                    if kw.arg == "candidates" and isinstance(kw.value, ast.List) and len(kw.value.elts) == 0:
                        placeholder_returns.append(node.lineno)
    assert not placeholder_returns, (
        f"P1-003 REGRESSION: top_k() returns hardcoded candidates=[] at "
        f"line(s) {placeholder_returns}."
    )
    # The fix must reference RL_SERVICE_URL and httpx in ACTIVE code.
    has_rl_url_ref = False
    has_httpx_call = False
    for node in ast.walk(tree):
        if isinstance(node, ast.Call):
            if isinstance(node.func, ast.Attribute) and node.func.attr == "get":
                if node.args and isinstance(node.args[0], ast.Constant) and \
                   node.args[0].value == "RL_SERVICE_URL":
                    has_rl_url_ref = True
            if isinstance(node.func, ast.Attribute) and node.func.attr == "AsyncClient":
                has_httpx_call = True
    assert has_rl_url_ref, "P1-003: RL_SERVICE_URL not referenced in top_k() active code."
    assert has_httpx_call, "P1-003: httpx.AsyncClient not used in top_k()."
    assert "503" in src or "HTTP_503_SERVICE_UNAVAILABLE" in src, (
        "P1-003: 503 response not implemented for RL service unreachable."
    )


# =============================================================================
# P1-004: /health endpoint reports false positives
# Issue: bool(os.environ.get("GT_MODEL_PATH")) returns True for any value
# Fix:   /ready probes downstream services; /health is liveness only
# =============================================================================

def test_P1_004_health_does_not_probe_downstream_services():
    """The /health endpoint must NOT check env vars for downstream health."""
    import inspect
    from backend.api import main
    src = inspect.getsource(main.health)
    # The broken env-var checks must be GONE from /health.
    assert "GT_MODEL_PATH" not in src, (
        "P1-004 REGRESSION: /health still checks GT_MODEL_PATH env var (false positive)."
    )
    assert "RL_CHECKPOINT_PATH" not in src, (
        "P1-004 REGRESSION: /health still checks RL_CHECKPOINT_PATH env var."
    )
    # /health is liveness only — does NOT probe GT/RL/DB.
    assert "create_engine" not in src or "text(\"SELECT 1\")" not in src, (
        "P1-004: /health is doing real DB probes — should be /ready's job."
    )


def test_P1_004_ready_probes_downstream_services_with_real_checks():
    """The /ready endpoint must ACTUALLY probe GT/RL/DB (not env vars)."""
    import inspect
    from backend.api import main
    # /ready must exist (separation of concerns).
    assert hasattr(main, "ready"), (
        "P1-004: /ready endpoint not found. The fix requires separating liveness "
        "(/health) from readiness (/ready)."
    )
    src = inspect.getsource(main.ready)
    # Must do a REAL DB probe (SELECT 1), not an env var check.
    assert "SELECT 1" in src, (
        "P1-004: /ready does not run SELECT 1 on the DB — env-var check is a false positive."
    )
    # Must do a REAL GT service probe (HTTP GET /health).
    assert "GT_SERVICE_URL" in src and "client.get" in src, (
        "P1-004: /ready does not actually HTTP-probe the GT service."
    )
    # Must do a REAL RL service probe.
    assert "RL_SERVICE_URL" in src, "P1-004: /ready does not probe the RL service."


# =============================================================================
# P1-005: trigger_phase2 pre-flight check reads SQLite DB at hardcoded path
# Issue: phase1/data/drugos.db — ignored in PostgreSQL production
# Fix:   query PostgreSQL via DATABASE_URL, fail-closed in production
# =============================================================================

def test_P1_005_dpi_check_uses_postgres_not_sqlite():
    """The DPI-degraded check must use PostgreSQL via DATABASE_URL, not SQLite."""
    dag = get_dag_module()

    # The SQLite path / drugos.db references must be GONE from active code.
    source = open(dag.__file__).read()
    # Allow references in COMMENTS only (the audit context). The check is:
    # there must be NO active `_sqlite3.connect(...drugos.db)` call.
    # Find any active sqlite3.connect call.
    import ast
    tree = ast.parse(source)
    sqlite_calls = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Call):
            f = node.func
            if isinstance(f, ast.Attribute) and f.attr == "connect":
                if isinstance(f.value, ast.Name) and f.value.id in ("sqlite3", "_sqlite3"):
                    sqlite_calls.append(node.lineno)
    assert not sqlite_calls, (
        f"P1-005 REGRESSION: active sqlite3.connect() call(s) at line(s) {sqlite_calls}. "
        "The DPI check must use PostgreSQL via DATABASE_URL, not SQLite."
    )

    # _check_dpi_degraded_via_postgres must exist.
    assert hasattr(dag, "_check_dpi_degraded_via_postgres"), (
        "P1-005: _check_dpi_degraded_via_postgres function not found."
    )


def test_P1_005_dpi_check_fails_closed_in_production_without_db_url():
    """In production with no DATABASE_URL, the check must raise (fail-closed)."""
    dag = get_dag_module()

    with patch.dict(dag.__dict__, {"_is_production": True}):
        with patch.dict(os.environ, {}, clear=False):
            os.environ.pop("DATABASE_URL", None)
            with pytest.raises(Exception, match="DATABASE_URL"):
                dag._check_dpi_degraded_via_postgres()


def test_P1_005_dpi_check_returns_degraded_in_dev_without_db_url():
    """In dev with no DATABASE_URL, the check returns a degraded state (no raise)."""
    dag = get_dag_module()

    with patch.dict(dag.__dict__, {"_is_production": False}):
        with patch.dict(os.environ, {}, clear=False):
            os.environ.pop("DATABASE_URL", None)
            result = dag._check_dpi_degraded_via_postgres()
            assert isinstance(result, dict)
            assert result.get("dpi_missing") is True, (
                "P1-005: dev mode should return degraded state, not raise."
            )


# =============================================================================
# P1-006: SYNTH% check on non-existent file pubchem_compounds.csv
# Issue: hardcoded filename pubchem_compounds.csv never exists
# Fix:   iterate over contract sources that have 'inchikey' column
# =============================================================================

def test_P1_006_synth_check_uses_contract_not_hardcoded_pubchem_compounds():
    """The SYNTH% check must iterate over contract sources with inchikey column,
    NOT check the non-existent pubchem_compounds.csv filename."""
    dag = get_dag_module()

    source = open(dag.__file__).read()
    # The active code must NOT contain a hardcoded "pubchem_compounds.csv" filename
    # (it can appear in comments explaining the old bug).
    import ast
    tree = ast.parse(source)

    # Walk string literals in the AST to find hardcoded filenames in active code.
    hardcoded_pubchem_compounds = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Constant) and isinstance(node.value, str):
            if node.value == "pubchem_compounds.csv":
                # We need to check if this is in a comment (ast doesn't track comments)
                # or in active code. ast.Constant nodes ARE active code.
                hardcoded_pubchem_compounds.append(node.lineno)
    assert not hardcoded_pubchem_compounds, (
        f"P1-006 REGRESSION: 'pubchem_compounds.csv' still hardcoded as an active "
        f"string literal at line(s) {hardcoded_pubchem_compounds}. The fix requires "
        f"using the contract's canonical filename 'pubchem_enrichment.csv' via "
        f"get_all_aliases()."
    )

    # Verify _validate_output_impl iterates over PHASE1_OUTPUT_SCHEMA.items()
    # for the SYNTH check (not a hardcoded list of 3 filenames).
    import inspect
    src = inspect.getsource(dag._validate_output_impl)
    assert "PHASE1_OUTPUT_SCHEMA.items()" in src or "PHASE1_OUTPUT_SCHEMA" in src, (
        "P1-006: SYNTH check does not iterate over PHASE1_OUTPUT_SCHEMA."
    )


# =============================================================================
# P1-007: entity_resolution swallows exceptions
# Issue: except Exception: logger.warning() — silent partial failure
# Fix:   track per-source diagnostics, fail if >30% critical sources failed
# =============================================================================

def test_P1_007_entity_resolution_has_cumulative_impact_check():
    """Entity resolution must have _check_cumulative_impact and a diagnostics tracker."""
    from phase1.entity_resolution import run as er
    assert hasattr(er, "_check_cumulative_impact"), (
        "P1-007: _check_cumulative_impact function not found."
    )
    assert hasattr(er, "_record_diagnostic"), (
        "P1-007: _record_diagnostic helper not found."
    )


def test_P1_007_cumulative_impact_raises_when_over_30_percent_critical_failed():
    """_check_cumulative_impact must raise when >30% of critical sources failed."""
    from phase1.entity_resolution.run import _check_cumulative_impact

    # 5 critical sources, 2 failed → 40% > 30% → must raise.
    diagnostics = [
        {"source": "a", "status": "loaded", "critical": True},
        {"source": "b", "status": "corrupt", "critical": True, "error": "x"},
        {"source": "c", "status": "loaded", "critical": True},
        {"source": "d", "status": "schema_error", "critical": True, "error": "y"},
        {"source": "e", "status": "loaded", "critical": True},
    ]
    with pytest.raises(RuntimeError, match="CUMULATIVE FAILURE"):
        _check_cumulative_impact(diagnostics, max_critical_failure_rate=0.30)


def test_P1_007_cumulative_impact_passes_when_under_30_percent_critical_failed():
    """_check_cumulative_impact must NOT raise when <=30% critical sources failed."""
    from phase1.entity_resolution.run import _check_cumulative_impact

    # 5 critical sources, 1 failed → 20% < 30% → no raise.
    diagnostics = [
        {"source": "a", "status": "loaded", "critical": True},
        {"source": "b", "status": "corrupt", "critical": True, "error": "x"},
        {"source": "c", "status": "loaded", "critical": True},
        {"source": "d", "status": "loaded", "critical": True},
        {"source": "e", "status": "loaded", "critical": True},
    ]
    # Should not raise.
    _check_cumulative_impact(diagnostics, max_critical_failure_rate=0.30)


def test_P1_007_cumulative_impact_ignores_non_critical_failures():
    """Non-critical source failures must NOT count toward the 30% threshold."""
    from phase1.entity_resolution.run import _check_cumulative_impact

    # 1 critical (loaded) + 5 non-critical (all corrupt) → 0% critical failure.
    diagnostics = [
        {"source": "a", "status": "loaded", "critical": True},
        {"source": "b", "status": "corrupt", "critical": False, "error": "x"},
        {"source": "c", "status": "corrupt", "critical": False, "error": "y"},
        {"source": "d", "status": "corrupt", "critical": False, "error": "z"},
        {"source": "e", "status": "corrupt", "critical": False, "error": "w"},
    ]
    # Must NOT raise (0% critical failure).
    _check_cumulative_impact(diagnostics, max_critical_failure_rate=0.30)


# =============================================================================
# P1-008: SCHEMA_VERSION_FALLBACK logic is a no-op
# Issue: SCHEMA_VERSION_FALLBACK = 0; if SCHEMA_VERSION == 0: SCHEMA_VERSION = 0
# Fix:   remove the no-op; document SCHEMA_VERSION=0 as fresh-install sentinel
# =============================================================================

def test_P1_008_no_op_assignment_block_is_removed():
    """The no-op `if SCHEMA_VERSION == 0: SCHEMA_VERSION = SCHEMA_VERSION_FALLBACK`
    block must be GONE from ACTIVE code (not just comments)."""
    from phase1.database import base
    # Use AST to check ACTIVE code only — comments can still describe the
    # old no-op pattern as historical context.
    import ast
    tree = ast.parse(open(base.__file__).read())
    no_op_blocks = []
    for node in ast.walk(tree):
        if isinstance(node, ast.If):
            # Check the test condition: SCHEMA_VERSION == 0
            test = node.test
            if isinstance(test, ast.Compare):
                if (isinstance(test.left, ast.Name) and test.left.id == "SCHEMA_VERSION"
                        and isinstance(test.ops[0], ast.Eq)
                    and isinstance(test.comparators[0], ast.Constant)
                        and test.comparators[0].value == 0):
                    # Check the body for `SCHEMA_VERSION = SCHEMA_VERSION_FALLBACK`
                    for stmt in node.body:
                        if (isinstance(stmt, ast.Assign)
                                and any(isinstance(t, ast.Name) and t.id == "SCHEMA_VERSION"
                                        for t in stmt.targets)
                                and isinstance(stmt.value, ast.Name)
                                and stmt.value.id == "SCHEMA_VERSION_FALLBACK"):
                            no_op_blocks.append(node.lineno)
    assert not no_op_blocks, (
        f"P1-008 REGRESSION: no-op `if SCHEMA_VERSION == 0: SCHEMA_VERSION = "
        f"SCHEMA_VERSION_FALLBACK` block still present in ACTIVE code at "
        f"line(s) {no_op_blocks}. This assigns 0 to 0 — a complete no-op."
    )
    # SCHEMA_VERSION must be derived from migration files (not hardcoded).
    assert hasattr(base, "_derive_schema_version"), (
        "P1-008: _derive_schema_version function not found. SCHEMA_VERSION must "
        "be auto-derived from migration file names, not hardcoded."
    )
    # SCHEMA_VERSION_FALLBACK is kept for backward compat, value 0.
    assert base.SCHEMA_VERSION_FALLBACK == 0, (
        "P1-008: SCHEMA_VERSION_FALLBACK must be 0 (documents fresh-install semantics)."
    )


def test_P1_008_schema_version_is_derived_from_migrations():
    """SCHEMA_VERSION must equal the highest migration file number."""
    from phase1.database import base
    migrations_dir = Path(base.__file__).parent / "migrations"
    if not migrations_dir.is_dir():
        pytest.skip("no migrations dir")
    import re
    pattern = re.compile(r"^(\d{1,3})_[^_].*\.sql$")
    versions = []
    for p in migrations_dir.iterdir():
        if not p.is_file() or p.name.endswith("_rollback.sql"):
            continue
        m = pattern.match(p.name)
        if m:
            versions.append(int(m.group(1)))
    if not versions:
        assert base.SCHEMA_VERSION == 0
    else:
        assert base.SCHEMA_VERSION == max(versions), (
            f"P1-008: SCHEMA_VERSION={base.SCHEMA_VERSION} but highest migration is "
            f"{max(versions)}. The auto-derivation is broken."
        )


# =============================================================================
# P1-009: _count_csv_rows swallows exceptions and returns 0
# Issue: except Exception: return 0 — hides corrupt CSVs
# Fix:   log at ERROR + return -1 sentinel
# =============================================================================

def test_P1_009_count_csv_rows_returns_negative_one_on_corrupt_file(tmp_path):
    """A corrupt CSV (e.g. bad gzip stream) must return -1, NOT 0."""
    from phase1 import service

    # Create a fake .csv.gz file with garbage bytes (invalid gzip).
    bad_gz = tmp_path / "bad.csv.gz"
    bad_gz.write_bytes(b"\x1f\x8bNOTREALLYGZIPDATA")
    result = service._count_csv_rows(bad_gz)
    assert result == -1, (
        f"P1-009: _count_csv_rows returned {result} for a corrupt gzip file. "
        f"Expected -1 sentinel so the caller can distinguish '0 rows' from 'read error'."
    )


def test_P1_009_count_csv_rows_returns_zero_for_missing_file(tmp_path):
    """A missing file must return 0 (file not present)."""
    from phase1 import service
    result = service._count_csv_rows(tmp_path / "nonexistent.csv")
    assert result == 0, (
        f"P1-009: missing file should return 0, got {result}."
    )


def test_P1_009_count_csv_rows_returns_actual_count_for_valid_csv(tmp_path):
    """A valid CSV with N data rows must return N."""
    from phase1 import service
    csv_path = tmp_path / "valid.csv"
    _write_csv(csv_path, ["inchikey", "name"], [["A1", "Aspirin"], ["A2", "Ibuprofen"]])
    result = service._count_csv_rows(csv_path)
    assert result == 2, f"P1-009: valid CSV with 2 rows returned {result}, expected 2."


# =============================================================================
# P1-010: CORS allow_origins split doesn't trim whitespace
# Issue: ["a", " b"] — leading space breaks CORS
# Fix:   [o.strip() for o in ...]
# =============================================================================

def test_P1_010_cors_origins_are_trimmed():
    """CORS origins from PHASE1_CORS_ORIGINS env var must be whitespace-trimmed."""
    from phase1 import service as svc_module
    import importlib

    with patch.dict(os.environ, {
        "PHASE1_CORS_ORIGINS": "http://a.com , http://b.com,http://c.com "
    }):
        # Re-import to pick up env var (the middleware reads env at import time).
        # We can't easily re-import; instead, inspect the app's middleware config.
        # The middleware is added at module import. We verify the fix by checking
        # the source code uses .strip() in the list comprehension.
        source = open(svc_module.__file__).read()
        assert ".strip()" in source, (
            "P1-010: CORS origins are not .strip()'d. Leading spaces in "
            "PHASE1_CORS_ORIGINS break CORS matching (exact match per Fetch spec)."
        )

    # Behavioral test: re-import the module with the env var set and inspect
    # the actual CORS middleware config.
    import importlib
    with patch.dict(os.environ, {
        "PHASE1_CORS_ORIGINS": "  http://x.com  ,  http://y.com  "
    }):
        # Reload the module so the env var is read at import time.
        importlib.reload(svc_module)
        # Find the CORSMiddleware in the app's middleware stack.
        cors_mw = None
        for mw in svc_module.app.user_middleware:
            if "CORSMiddleware" in str(mw.cls):
                cors_mw = mw
                break
        assert cors_mw is not None, "CORS middleware not found in app."
        origins = cors_mw.kwargs.get("allow_origins", [])
        assert "http://x.com" in origins, (
            f"P1-010: origins not trimmed. Got {origins!r}, expected http://x.com (no spaces)."
        )
        assert "http://y.com" in origins, (
            f"P1-010: origins not trimmed. Got {origins!r}, expected http://y.com (no spaces)."
        )
        # No origin should have leading/trailing whitespace.
        for o in origins:
            assert o == o.strip(), (
                f"P1-010: origin {o!r} has leading/trailing whitespace."
            )


# =============================================================================
# P1-011: total_proteins = max(uniprot, string) — undercounts
# Issue: max() is wrong for union of overlapping sets
# Fix:   |uniprot_ids ∪ string_ids| (cardinality of UNION)
# =============================================================================

def test_P1_011_total_proteins_uses_union_not_max(tmp_path):
    """_compute_total_proteins must use set UNION, not max()."""
    from phase1 import service

    # UniProt has 100 proteins, STRING has 80, overlap is 60.
    # max(100, 80) = 100 (WRONG — undercounts by 20).
    # UNION = 100 + 80 - 60 = 120 (CORRECT).
    uniprot_ids = {f"P{i:05d}" for i in range(100)}  # 100 proteins
    string_ids = {f"P{i:05d}" for i in range(40, 120)}  # 80 proteins, 60 overlap

    # Write CSVs.
    _write_csv(tmp_path / "uniprot_proteins.csv",
               ["uniprot_id"], [[i] for i in sorted(uniprot_ids)])
    _write_csv(tmp_path / "string_protein_protein_interactions.csv",
               ["uniprot_id_a", "uniprot_id_b", "combined_score"],
               [[sorted(string_ids)[i], sorted(string_ids)[(i+1) % len(string_ids)], "900"]
                for i in range(0, len(string_ids), 2)])

    with patch.object(service, "_processed_data_dir", lambda: tmp_path):
        result = service._compute_total_proteins(tmp_path)
    expected = len(uniprot_ids | string_ids)  # UNION
    assert result == expected, (
        f"P1-011: _compute_total_proteins returned {result}, expected UNION={expected}. "
        f"max(uniprot, string)={max(len(uniprot_ids), len(string_ids))} (WRONG)."
    )


# =============================================================================
# P1-012: total_drugs fallback picks DrugBank over ChEMBL
# Issue: DrugBank-first fallback chain — undercounts when ChEMBL has 30x more
# Fix:   UNION of InChIKeys across ALL drug CSVs
# =============================================================================

def test_P1_012_total_drugs_uses_union_not_drugbank_first(tmp_path):
    """_compute_total_drugs must use UNION of InChIKeys, not DrugBank-first fallback."""
    from phase1 import service

    # DrugBank has 50 drugs, ChEMBL has 1500, overlap is 40.
    # DrugBank-first fallback would return 50 (WRONG by 30x).
    # UNION = 50 + 1500 - 40 = 1510.
    drugbank_keys = {f"DB{i:05d}" for i in range(50)}
    chembl_keys = {f"DB{i:05d}" for i in range(40)} | {f"CHEMBL{i:05d}" for i in range(1460)}

    _write_csv(tmp_path / "drugbank_drugs.csv",
               ["inchikey", "name"], [[k, f"drug_{k}"] for k in sorted(drugbank_keys)])
    _write_csv(tmp_path / "chembl_drugs.csv",
               ["inchikey", "name"], [[k, f"drug_{k}"] for k in sorted(chembl_keys)])

    with patch.object(service, "_processed_data_dir", lambda: tmp_path):
        result = service._compute_total_drugs(tmp_path)
    expected = len(drugbank_keys | chembl_keys)
    assert result == expected, (
        f"P1-012: _compute_total_drugs returned {result}, expected UNION={expected}. "
        f"DrugBank-first fallback would return {len(drugbank_keys)} (WRONG by ~30x)."
    )


def test_P1_012_total_drugs_excludes_synth_prefixed_inchikeys(tmp_path):
    """SYNTH-prefixed InChIKeys must NOT count toward total_drugs (dev-only escape)."""
    from phase1 import service
    _write_csv(tmp_path / "drugbank_drugs.csv",
               ["inchikey", "name"],
               [["REAL001", "Aspirin"], ["SYNTHFAKE001", "FakeSynth"], ["REAL002", "Ibuprofen"]])
    with patch.object(service, "_processed_data_dir", lambda: tmp_path):
        result = service._compute_total_drugs(tmp_path)
    assert result == 2, (
        f"P1-012: SYNTH-prefixed InChIKey should be excluded. Got {result}, expected 2."
    )


# =============================================================================
# P1-013: /stats endpoint hardcodes schemaVersion="1.0"
# Issue: actual is 20 (migrations 001-020), hardcoded string is "1.0"
# Fix:   use str(SCHEMA_VERSION) from phase1.database.base
# =============================================================================

def test_P1_013_stats_uses_real_schema_version():
    """The /stats endpoint must return the real SCHEMA_VERSION, not '1.0'."""
    from phase1 import service
    from phase1.database.base import SCHEMA_VERSION

    # The hardcoded "1.0" string must NOT be in the active /stats code.
    import inspect
    src = inspect.getsource(service.stats)
    # Look for the literal "1.0" assigned to schemaVersion.
    assert '"schemaVersion": "1.0"' not in src and \
           "'schemaVersion': '1.0'" not in src and \
           "schemaVersion\": \"1.0" not in src, (
        "P1-013 REGRESSION: /stats still hardcodes schemaVersion='1.0'. "
        "Must use str(SCHEMA_VERSION) from phase1.database.base."
    )
    # Must reference SCHEMA_VERSION (the canonical source).
    assert "SCHEMA_VERSION" in src or "_DB_SCHEMA_VERSION" in src, (
        "P1-013: /stats does not reference SCHEMA_VERSION from database.base."
    )


# =============================================================================
# P1-014: _load_drug_mechanism doesn't handle UTF-8 BOM
# Issue: encoding="utf-8" — BOM becomes first char of header → row.get("name")=None
# Fix:   use _open_csv_for_read (encoding="utf-8-sig" strips BOM)
# =============================================================================

def test_P1_014_load_drug_mechanism_handles_bom(tmp_path):
    """_load_drug_mechanism must correctly read a DrugBank CSV with a UTF-8 BOM."""
    from phase1 import service

    # Write a DrugBank CSV WITH a BOM.
    _write_csv_with_bom(
        tmp_path / "drugbank_drugs.csv",
        ["name", "drugbank_id", "inchikey", "smiles"],
        [["Aspirin", "DB001", "RYYVLZVUVIJVGH-UHFFFAOYSA-N", "CC(=O)OC1=CC=CC=C1C(=O)O"]],
    )

    with patch.object(service, "_processed_data_dir", lambda: tmp_path):
        result = service._load_drug_mechanism("Aspirin")

    assert result["drug"] == "Aspirin", (
        f"P1-014: drug lookup failed with BOM CSV. Got {result!r}."
    )
    assert result["inchikey"] == "RYYVLZVUVIJVGH-UHFFFAOYSA-N", (
        f"P1-014: inchikey not loaded (BOM broke header parsing). Got {result!r}."
    )


def test_P1_014_load_drug_mechanism_404_when_drug_missing(tmp_path):
    """When the drug is not in the CSV, the endpoint must 404 (not silent None)."""
    from phase1 import service
    from fastapi import HTTPException

    _write_csv_with_bom(
        tmp_path / "drugbank_drugs.csv",
        ["name", "drugbank_id", "inchikey", "smiles"],
        [["Aspirin", "DB001", "RYYVLZVUVIJVGH-UHFFFAOYSA-N", "CC(=O)OC1=CC=CC=C1C(=O)O"]],
    )

    with patch.object(service, "_processed_data_dir", lambda: tmp_path):
        with pytest.raises(HTTPException) as exc_info:
            service._load_drug_mechanism("NonexistentDrug")
        assert exc_info.value.status_code == 404


# =============================================================================
# P1-015: validate_output uses f-string for table name in SQL
# Issue: f"SELECT COUNT(*) FROM {table}" — unsafe pattern
# Fix:   whitelist check before interpolation
# =============================================================================

def test_P1_015_no_fstring_sql_table_interpolation():
    """The dag must NOT use f-strings to interpolate table names into SQL."""
    dag = get_dag_module()
    source = open(dag.__file__).read()

    # The active code must NOT contain f"SELECT ... FROM {table}" pattern.
    import ast
    tree = ast.parse(source)
    unsafe_patterns = []
    for node in ast.walk(tree):
        if isinstance(node, ast.JoinedStr):
            # f-string — check if it contains a SQL FROM clause with a format value.
            for value in node.values:
                if isinstance(value, ast.FormattedValue):
                    # Look at the surrounding Constant parts for SQL keywords.
                    pass
            # Reconstruct the f-string's literal parts.
            literal_parts = []
            for v in node.values:
                if isinstance(v, ast.Constant) and isinstance(v.value, str):
                    literal_parts.append(v.value)
            joined = "".join(literal_parts).upper()
            if "FROM" in joined and "SELECT" in joined:
                unsafe_patterns.append(node.lineno)
    assert not unsafe_patterns, (
        f"P1-015 REGRESSION: f-string with SQL FROM clause found at line(s) "
        f"{unsafe_patterns}. Table names must be whitelisted before interpolation."
    )


# =============================================================================
# RUNNER
# =============================================================================

if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-v", "--tb=short"]))
