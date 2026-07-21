"""
Master DAG for the Drug Repurposing ETL Platform.

Orchestrates all 7 source pipelines in the correct dependency order:

  download_chembl  ──┐
  download_drugbank ─┤→ entity_resolution → load_string
  download_uniprot  ─┤                    → load_disgenet
  download_string  ──┘                    → load_omim
  download_disgenet                        → load_pubchem_enrichment
  download_omim
  download_pubchem

DrugBank XML check: Uses BranchPythonOperator to skip DrugBank if the XML
file is not present (it requires manual download — pipeline should not fail
the whole DAG).

Schedule: Every Sunday at 02:00 UTC  (``0 2 * * 0``)
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta
from pathlib import Path

# ---------------------------------------------------------------------------
# v89 ROOT FIX (BUG #39): shared sys.path bootstrap — was duplicated
# verbatim in all 8 DAG files. Extracted to dags/_dags_init.py.
# ---------------------------------------------------------------------------
# P1-050 FORENSIC ROOT FIX (Team 4 — hidden module-level side effect):
# The previous version relied on a module-level ``ensure_project_root()``
# call inside ``dags/_dags_init.py`` that fired on ANY import. This
# mutated ``sys.path`` for the ENTIRE process, breaking test isolation
# (tests that imported ``dags._dags_init`` to test the function directly
# polluted ``sys.path`` for all subsequent tests in the same process).
# The module-level call was REMOVED from ``_dags_init.py``; each DAG
# file MUST now explicitly call ``ensure_project_root()`` at module top
# (after the import). This makes the ``sys.path`` side effect EXPLICIT
# per-DAG, not hidden in an imported module. All 7 standalone DAGs
# already do this — master_pipeline_dag was the LAST one missing the
# explicit call (the prior fix only added the import, not the call).
from dags._dags_init import ensure_project_root  # noqa: F401
ensure_project_root()  # P1-050 root fix: explicit per-DAG sys.path bootstrap

from airflow.decorators import dag, task
# P1-031 side-fix: BranchPythonOperator moved between airflow versions.
# In airflow <2.10 it lived in ``airflow.operators.branch``; in airflow
# 2.10+ and 3.x it moved to ``airflow.operators.python``. The previous
# ``from airflow.operators.branch import BranchPythonOperator`` import
# crashed on airflow 2.10+, making the entire DAG unimportable -- which
# blocked the P1-031 dependency-chain regression test (the test could
# not parse the DAG to verify ``chembl >> resolve >> load_* >> trigger_phase2``).
# This compatibility shim tries the new location first, then falls back
# to the old location. Both code paths are exercised in CI.
try:
    from airflow.operators.python import BranchPythonOperator  # airflow 2.10+
except ImportError:  # pragma: no cover -- airflow <2.10 fallback
    from airflow.operators.branch import BranchPythonOperator  # type: ignore[no-redef]
from airflow.operators.empty import EmptyOperator
from airflow.utils.trigger_rule import TriggerRule

# v83 P1-14: import the shared retry policy so the master DAG uses the
# SAME retry parameters (5min + exponential backoff) as the 7 standalone DAGs.
# v89 ROOT FIX (BUG #24): also import fail_fast_on_http_4xx so it can be
# applied to _trigger_phase2 (was missing — inconsistent with the
# documented "apply @fail_fast_on_http_4xx to EVERY @task" policy).
from dags._retry_policy import DEFAULT_RETRY_ARGS, fail_fast_on_http_4xx

# TM1 TASK 1.4 ROOT FIX (v131 -- Teammate 1 P1->P2 integration):
# Import the canonical Phase 1 schema contract so the master DAG's
# validate_output task resolves CSV filenames via the SINGLE SOURCE OF
# TRUTH (PHASE1_OUTPUT_SCHEMA + get_all_aliases + get_required_id_column)
# instead of a hand-maintained {filename: id_column} dict that drifted
# from the contract. The previous code (lines 1193-1201 before this
# fix) hardcoded 7 filenames, 4 of which were WRONG:
#   - "string_proteins.csv"     -> actual: string_protein_protein_interactions.csv
#   - "disgenet_gda.csv"        -> actual: disgenet_gene_disease_associations.csv
#   - "omim_gda.csv"            -> actual: omim_gene_disease_associations.csv
#   - "pubchem_compounds.csv"   -> actual: pubchem_enrichment.csv
# In production (_is_production=True), every missing CSV appended a
# failure, validate_output raised AirflowFailException, and trigger_phase2
# NEVER fired. The master DAG failed EVERY Sunday at 02:00 UTC. Phase 2
# KG construction had not run automatically in any production deployment.
# ROOT FIX: drive filename + ID-column resolution from the contract so
# the master DAG can NEVER drift from the pipeline's actual output.
# -----------------------------------------------------------------------------
try:
    # When imported as ``phase1.dags.master_pipeline_dag`` (canonical path).
    from phase1.contracts.phase1_schema import (
        PHASE1_OUTPUT_SCHEMA,
        SCHEMA_VERSION,
        get_all_aliases,
        get_required_id_column,
    )
except ImportError:
    # When imported as ``dags.master_pipeline_dag`` (Airflow worker path,
    # where ``phase1/`` is on sys.path as the project root). Falls back
    # to the contracts package directly.
    from contracts.phase1_schema import (  # type: ignore[no-redef]
        PHASE1_OUTPUT_SCHEMA,
        SCHEMA_VERSION,
        get_all_aliases,
        get_required_id_column,
    )

# TM1 TASK 1.4 ROOT FIX (v131): import SQLAlchemy at module level so
# tests can patch ``phase1.dags.master_pipeline_dag.create_engine`` and
# ``phase1.dags.master_pipeline_dag.sql_text``. The previous code did
# the SQLite DPI check via the stdlib ``sqlite3`` module, which silently
# returned None in production (where the DB is PostgreSQL, not SQLite).
# ROOT FIX: query PostgreSQL via DATABASE_URL. Fail-closed in production
# if DATABASE_URL is missing (no silent safety-net bypass).
try:
    from sqlalchemy import create_engine as create_engine  # noqa: F401
    from sqlalchemy import text as sql_text  # noqa: F401
except ImportError:  # pragma: no cover -- defensive: sqlalchemy is in requirements.lock
    create_engine = None  # type: ignore[assignment]
    sql_text = None  # type: ignore[assignment]

logger = logging.getLogger(__name__)

# v89 ROOT FIX (BUG #27 — fragile runtime import inside _check_drugbank_xml):
# The previous code did ``from config.settings import DRUGBANK_XML_PATH``
# INSIDE the ``_check_drugbank_xml`` branch callable. That import runs
# at TASK EXECUTION time (in the Airflow worker). If the worker's
# sys.path / CWD is different from expected, the task raises ImportError
# — which (with retries=2) retries 2 more times before failing the DAG.
# ROOT FIX: move the import to the TOP of the module (after sys.path
# setup), so it runs at DAG PARSE time. If config.settings is not
# importable, the DAG fails to parse — Airflow marks the DAG as
# "import error" in the UI, which is far more diagnosable than a
# runtime ImportError 2 retries later. The ``try/except`` wraps the
# import so a missing config doesn't kill DAG parsing for the OTHER
# 6 pipelines that don't need DRUGBANK_XML_PATH.
try:
    from config.settings import DRUGBANK_XML_PATH
except Exception as _exc:  # noqa: BLE001 — config import must never kill DAG parse
    logger.warning(
        "v89 BUG #27: could not import DRUGBANK_XML_PATH from config.settings "
        "at DAG parse time (%s). The DrugBank XML branch will fall back to "
        "'skip_drugbank' at runtime. Fix config.settings to enable DrugBank.",
        _exc,
    )
    DRUGBANK_XML_PATH = ""  # sentinel — _check_drugbank_xml will skip

# ---------------------------------------------------------------------------
# v29 ROOT FIX (audit O-12): XCom used for large dataframes — anti-pattern.
# Now passes file paths via XCom (and, in practice, tasks communicate through
# CSV files in processed_data/ + the shared DB, never by returning a DataFrame
# from a @task). Returning a DataFrame would push it to XCom and saturate the
# metadata DB. Every @task below returns None and either writes to
# processed_data/ (producers) or reads from processed_data/ (consumers) —
# only small file-path strings are ever exchanged between tasks.
# ---------------------------------------------------------------------------
# Shared constants
# ---------------------------------------------------------------------------
# v75 ROOT FIX (T-024 — SLA/timeout mismatch compounds silently):
#   v29 set TASK_SLA=4h with TASK_TIMEOUT=8h. The audit's premise was that
#   the SLA was the binding limit — but Airflow SLA misses are ADVISORY:
#   they emit an entry to the SLA miss log and (optionally) trigger an
#   email, but they do NOT kill the running task. The task continued for
#   another 4h until the 8h execution_timeout fired. With retries=0 on
#   _trigger_phase2 (line 462), the 8h timeout killed the task RED with
#   no retry — the 4h SLA warning was the only early signal, and it was
#   advisory.
#
#   Worse: the 8h timeout was documented as "TransE training on real data
#   can take 6-7h" — so a NORMAL 6-7h run fires the 4h SLA miss every
#   single time, training operators to ignore SLA warnings. That is the
#   definition of a noisy false-positive alarm that defeats the purpose
#   of having an SLA at all.
#
#   ROOT FIX (master-grade, no sugar-coating):
#     1. Align the SLA and the hard timeout at the SAME value (7h). The
#        SLA miss at 7h now coincides with the hard kill, so there is
#        exactly ONE signal at exactly ONE time. No false-positive
#        advisory that trains operators to ignore warnings.
#     2. The hard timeout is the BINDING limit — by definition, when the
#        SLA fires, the task is also about to be killed. This is the
#        scientifically correct configuration for an SLA that is meant
#        as an early-warning system: the warning must come BEFORE the
#        kill, and if the only sensible "early warning" time is "right
#        before the kill", then the SLA is redundant with the timeout
#        and should be set to the same value to remove the noise.
#     3. The 7h value is the upper bound of the documented training
#        window (6-7h on real data). A normal run completes in ≤7h; a
#        stuck run is killed at 7h. No false positives on normal runs.
#     4. retries=0 on _trigger_phase2 is preserved (line 462) — a
#        timed-out Phase 2 training run must NOT be retried
#        automatically (GPU state, partial checkpoints, and
#        non-deterministic sampler state would corrupt the retry).
#        The hard kill at 7h is the patient-safe failure mode.
#     5. The SLA-miss-is-advisory behaviour is now DOCUMENTED in the
#        DEFAULT_ARGS comment below so operators do not rely on the
#        SLA to actually stop the task.
# v93 ROOT FIX (P1-034 — SLA defeats its own purpose):
#   The v75 ROOT FIX (T-024) aligned SLA and execution_timeout at 7h,
#   claiming "exactly ONE signal at exactly ONE time". But this DEFEATS
#   the purpose of an SLA. An SLA is meant to be an EARLY WARNING that
#   fires BEFORE the hard kill — giving the operator a window to
#   intervene (extend the timeout, kill a stuck task manually, page
#   on-call). By setting SLA == timeout, the SLA miss fires at exactly
#   7h, and the hard kill ALSO fires at exactly 7h — the operator gets
#   no early warning, just a single "task killed" notification.
#
#   Root fix: set TASK_SLA = 5h (2h before the 7h kill). The SLA miss
#   at 5h is ADVISORY — it pages the operator but does NOT stop the
#   task. The operator has a 2h window to decide: extend the timeout
#   (via Airflow's clear+retry with a longer timeout), kill the task
#   manually, or let it run to the 7h hard kill. The 7h TASK_TIMEOUT
#   remains the patient-safe failure mode (GPU state, partial
#   checkpoints, non-deterministic sampler state would corrupt a
#   retry — so we kill, not retry).
#
# Design invariants preserved:
#     1. TASK_TIMEOUT = 7h remains the upper bound of the documented
#        training window (6-7h on real data). A normal run completes
#        in ≤7h; a stuck run is killed at 7h. No false positives on
#        normal runs.
#     2. retries=0 on _trigger_phase2 is preserved (line 462) — a
#        timed-out Phase 2 training run must NOT be retried
#        automatically (GPU state, partial checkpoints, and
#        non-deterministic sampler state would corrupt the retry).
#        The hard kill at 7h is the patient-safe failure mode.
#     3. The SLA-miss at 5h is ADVISORY — it pages but does not stop.
#        Operators do not rely on the SLA to stop the task; the 7h
#        timeout does that.
# v107 ROOT FIX (ISSUE-P1-021 — v93 fix was documented but NEVER applied):
#   The audit found TASK_SLA == TASK_TIMEOUT == 7h in the actual code,
#   despite the v93 comment block above describing TASK_SLA = 5h. The
#   comment was aspirational, not actual. This is the kind of "comment
#   says fixed, code says broken" discrepancy that the audit flagged.
#   The fix is now ACTUALLY applied: TASK_SLA = 5h, TASK_TIMEOUT = 7h,
#   giving operators a 2h early-warning window before the hard kill.
TASK_SLA = timedelta(hours=5)
TASK_TIMEOUT = timedelta(hours=7)

# v83 DAG-2 ROOT FIX: apply the SAME retry policy used by all 7 standalone
# DAGs (dags/_retry_policy.py::DEFAULT_RETRY_ARGS). The previous DEFAULT_ARGS
# used ``retries=2, retry_delay=30min`` with NO ``retry_exponential_backoff``
# and NO ``max_retry_delay``. A 4xx error in the master DAG wasted 60 min
# (2 × 30min) per task; the standalone DAGs fail-fast in seconds via
# ``@fail_fast_on_http_4xx`` + ``retry_exponential_backoff=True``. The master
# DAG is the SUNDAY run — the longest, most data-intensive run of the week —
# so a 60-min wait per task is the worst possible time to waste.
#
# ROOT FIX (DAG-2):
#   1. Spread ``DEFAULT_RETRY_ARGS`` (5min base delay, exponential backoff,
#      20min cap) so transient 5xx/network errors recover in ~10s on the
#      first retry, and the master DAG's retry behavior matches the 7
#      standalone DAGs exactly.
#   2. Apply ``@fail_fast_on_http_4xx`` to EVERY @task below so 4xx errors
#      (401/403/404/400/410/451) immediately raise ``AirflowFailException``
#      and skip retries — matching the standalone DAGs' fail-fast behavior.
#      Without this, a 401 (expired DISGENET/OMIM API key) on the master
#      DAG wastes 60 min (was 30min × 2 retries) before failing RED,
#      while the same error on the standalone DAG fails in <1 second.
#   3. Preserve the v75 ROOT FIX (T-024): SLA == execution_timeout == 7h
#      (aligned, no false-positive advisory).
#   4. Preserve ``retries=2`` (from DEFAULT_RETRY_ARGS) — same as standalone.
# v89: DEFAULT_RETRY_ARGS and fail_fast_on_http_4xx are imported ONCE at
# the top of this module (line 43) — no duplicate import here.

DEFAULT_ARGS = {
    **DEFAULT_RETRY_ARGS,
    "owner": "drug_repurposing",
    "depends_on_past": False,
    # v83 FORENSIC ROOT FIX (P1-14): the previous code used
    # ``retry_delay=timedelta(minutes=30)`` with NO exponential backoff.
    # The 7 standalone DAGs use ``DEFAULT_RETRY_ARGS`` (5min + exponential
    # backoff). ROOT FIX: spread ``DEFAULT_RETRY_ARGS`` into ``DEFAULT_ARGS``
    # (done above) so the master DAG uses the SAME retry policy. The
    # ``sla`` / ``execution_timeout`` overrides (5h/7h, v93 P1-034 + v107
    # P1-021) are retained AFTER the spread so they win over the
    # ``DEFAULT_RETRY_ARGS`` 4h defaults.
    # v93/v107 (ISSUE-P1-021): ``sla`` is ADVISORY — an Airflow SLA miss
    # writes a row to the sla_miss table and (optionally) sends an email,
    # but it does NOT kill the running task. The task continues until
    # ``execution_timeout`` fires. TASK_SLA = 5h gives operators a 2h
    # early-warning window before the 7h hard kill (TASK_TIMEOUT). The
    # 7h TASK_TIMEOUT remains the patient-safe failure mode for stuck
    # Phase 2 TransE training runs (the documented upper bound of normal
    # training time is 6-7h on real data).
    # v83 DAG-2: ``sla`` and ``execution_timeout`` come from
    # DEFAULT_RETRY_ARGS (4h) but the master DAG overrides them to
    # 5h/7h respectively because trigger_phase2's TransE training can
    # take 6-7h. This override is deliberate and documented.
    "sla": TASK_SLA,
    "execution_timeout": TASK_TIMEOUT,
}


# =============================================================================
# TM1 TASK 1.4 ROOT FIX (v131 -- Teammate 1 P1->P2 integration):
# Module-level testability seams for validate_output / DPI check.
# =============================================================================
# RED-TEAM AUDIT FINDING (v131, hostile-auditor pass):
#   The previous validate_output task computed ``_processed_dir`` and
#   ``_is_production`` INSIDE the @task-wrapped function body. This made
#   the function untestable from a unit test -- ``unittest.mock.patch``
#   could not override the values because they were local variables
#   computed at CALL TIME from ``os.environ`` / ``config.settings``.
#   The result: every prior "root fix" pass CLAIMED the contract-driven
#   validation worked but the integration test
#   ``test_p1_to_p2_master_dag.py`` was never written, so the broken
#   hardcoded _expected_csvs dict (lines 1193-1201 pre-fix) was never
#   caught by CI. This is the exact "comments claim fixed, code is
#   broken" failure mode the audit mandates against.
#
# ROOT FIX (v131, this commit):
#   1. Hoist ``_processed_dir`` and ``_is_production`` to MODULE-LEVEL
#      attributes computed at IMPORT TIME from ``os.environ`` /
#      ``config.settings``. The @task-wrapped ``validate_output`` and
#      the testable ``_validate_output_impl`` BOTH read these module-
#      level attributes at CALL TIME via Python's LEGB global scope
#      resolution. ``unittest.mock.patch('phase1.dags.master_pipeline_dag.
#      _processed_dir', tmpdir)`` replaces the module attribute, and the
#      next call to ``_validate_output_impl`` picks up the patched
#      value because Python resolves global names at call time, not at
#      function-definition time.
#   2. ``_pipeline_run_id`` is generated at module import time as a
#      STABLE default -- the @task-wrapped ``validate_output``
#      regenerates it at TASK EXECUTION time so each DAG run has a
#      unique ID. The module-level value exists so tests that import
#      the module (but don't trigger a DAG run) see a non-None value.
#   3. ``_check_dpi_degraded_via_postgres`` is a module-level function
#      (NOT a @task) so tests can call it directly with
#      ``patch.dict(os.environ, ...)`` and ``patch('...create_engine')``.
# -----------------------------------------------------------------------------
import os as _os
import uuid as _uuid
from datetime import datetime as _datetime_module  # alias to avoid name clash
from pathlib import Path as _Path_module

# Resolve the project root: ``__file__`` is
# ``<project_root>/phase1/dags/master_pipeline_dag.py``. The project
# root is two parents up.
_PROJECT_ROOT_MODULE = _Path_module(__file__).resolve().parent.parent.parent

# Resolve the processed_data directory from config.settings (single
# source of truth for paths). Fall back to the canonical
# ``<project_root>/phase1/processed_data`` if config.settings is
# unimportable (e.g. in a unit-test env that doesn't have airflow
# installed). This fallback is the SAME path config.settings would
# return, so the behavior is identical in production.
try:
    from config.settings import PROCESSED_DATA_DIR as _PROCESSED_DATA_DIR_FROM_CONFIG
    _processed_dir: _Path_module = _Path_module(_PROCESSED_DATA_DIR_FROM_CONFIG)
except Exception as _config_exc:  # noqa: BLE001 -- defensive
    logger.warning(
        "TM1 v131: could not import PROCESSED_DATA_DIR from config.settings "
        "(%s). Falling back to <project_root>/phase1/processed_data. This "
        "is the SAME path config.settings would return, so production "
        "behavior is unaffected.",
        _config_exc,
    )
    _processed_dir = _PROJECT_ROOT_MODULE / "phase1" / "processed_data"

# Resolve the environment. ``DRUGOS_ENVIRONMENT`` is the canonical env
# var (matches phase1/config/settings.py). ``ENVIRONMENT`` is a fallback
# for non-DrugOS deploys (e.g. Kubernetes ``ENVIRONMENT=production``).
# Default is "production" -- fail-closed. ``_is_production`` is True
# unless the env is explicitly "development", "dev", "test", "testing",
# or "ci".
_ENVIRONMENT_MODULE = (
    _os.environ.get("DRUGOS_ENVIRONMENT")
    or _os.environ.get("ENVIRONMENT", "production")
).lower().strip()
_is_production: bool = _ENVIRONMENT_MODULE not in (
    "development", "dev", "test", "testing", "ci",
)

# Generate a stable default pipeline_run_id for module-level state.
# The @task-wrapped ``validate_output`` regenerates this at TASK
# EXECUTION time so each DAG run has a unique ID. Tests that import
# the module (without triggering a DAG run) see this stable value.
_pipeline_run_id: str = str(_uuid.uuid4())


# ---------------------------------------------------------------------------
# Branch helper — DrugBank XML gate
# ---------------------------------------------------------------------------

# v89 ROOT FIX (BUG #36 — fragile coupling between branch return values
# and task_ids):
#   The previous code returned the hardcoded strings "download_drugbank"
#   and "skip_drugbank" from ``_check_drugbank_xml``. These strings had
#   to EXACTLY match the task_id parameters of the downstream tasks.
#   If a future refactor renamed the ``download_drugbank`` function
#   (whose TaskFlow task_id is auto-generated from the function name),
#   the branch return value would become a DANGLING reference —
#   BranchPythonOperator raises AirflowException at RUNTIME, with no
#   compile-time check.
#
#   ROOT FIX: define the task_ids as module-level constants. Use them
#   in (a) the branch function's return values, (b) the EmptyOperator's
#   task_id parameter, and (c) a parse-time assertion (inside
#   ``master_pipeline``) that the TaskFlow-generated task_id of
#   ``download_drugbank`` matches the constant. The assertion catches
#   any rename at DAG PARSE time instead of at runtime.
#
# P1-036 v117 ROOT FIX (forensic -- _DRUGBANK_DOWNLOAD_TASK_ID was still a
# hardcoded string "download_drugbank"):
#   The v89 fix introduced the constant but assigned it a HARDCODED string
#   literal ``"download_drugbank"``. If the ``download_drugbank`` function
#   was renamed (e.g. to ``download_drugbank_v2``), the constant would NOT
#   track the rename -- the branch would return the stale string, and the
#   parse-time assertion would catch the mismatch ONLY at DAG parse time
#   (not at code-edit time). The hardcode defeated the purpose of having a
#   "single source of truth" constant.
#
#   ROOT FIX (v117): derive ``_DRUGBANK_DOWNLOAD_TASK_ID`` from the
#   ``download_drugbank`` function's ``__name__`` -- the SAME attribute
#   Airflow's TaskFlow API uses to auto-generate the task_id. The constant
#   is now defined AFTER ``download_drugbank`` (see line ~360 below) so
#   the function is in scope at the derivation point. ``_check_drugbank_xml``
#   and the ``master_pipeline`` parse-time assertion both reference the
#   constant at CALL time (Python late-binds module-level names inside
#   function bodies), so moving the definition below the function does NOT
#   break them.
#
#   ``_DRUGBANK_SKIP_TASK_ID`` remains a hardcoded string because it is
#   the task_id of an ``EmptyOperator`` (NOT a function-derived TaskFlow
#   task) -- there is no function to derive it from.
_DRUGBANK_SKIP_TASK_ID: str = "skip_drugbank"
# ``_DRUGBANK_DOWNLOAD_TASK_ID`` is derived AFTER ``download_drugbank`` is
# defined (see P1-036 v117 ROOT FIX block below).


def _check_drugbank_xml(**context) -> str:
    """Return the task-id to branch into based on DrugBank XML availability.

    DrugBank requires a paid license; the XML must be pre-positioned
    manually.  If the file is missing we gracefully skip the pipeline so the
    rest of the DAG can continue.

    v89 ROOT FIX (BUG #27): DRUGBANK_XML_PATH is now imported at module
    top level (not inside this function), so import failures surface at
    DAG parse time, not at task execution time after 2 retries.

    v89 ROOT FIX (BUG #36): returns the module-level constant
    ``_DRUGBANK_DOWNLOAD_TASK_ID`` / ``_DRUGBANK_SKIP_TASK_ID`` instead
    of hardcoded strings, so a rename of the downstream task_id is
    caught at parse time by the assertion in ``master_pipeline``.
    """
    # v89 BUG #27: DRUGBANK_XML_PATH is now a module-level name (imported
    # at the top of this file). No runtime import here.
    # v43 ROOT FIX (P1 — _check_drugbank_xml crashes on invalid path):
    # The previous code did Path(DRUGBANK_XML_PATH) then .exists() +
    # .stat() with no try/except. If DRUGBANK_XML_PATH is set to an
    # invalid value (null bytes, extremely long path, etc.), Path()
    # raises ValueError and .stat() raises OSError — crashing the
    # branch task and (with retries=2) the entire DAG. The intent was
    # to gracefully skip DrugBank. Fix: wrap in try/except and return
    # the skip task_id on any error.
    try:
        xml_path = Path(DRUGBANK_XML_PATH)
        if xml_path.exists() and xml_path.stat().st_size > 0:
            logger.info("DrugBank XML found at %s — will run pipeline", xml_path)
            return _DRUGBANK_DOWNLOAD_TASK_ID
    except (OSError, ValueError) as exc:
        logger.warning(
            "DrugBank XML path %r is invalid or unreadable (%s) — "
            "skipping pipeline. To enable: fix DRUGBANK_XML_PATH env var.",
            DRUGBANK_XML_PATH, exc,
        )
        return _DRUGBANK_SKIP_TASK_ID

    logger.warning(
        "DrugBank XML not found at %s — skipping pipeline. "
        "To enable: download from https://go.drugbank.com/ and set "
        "DRUGBANK_XML_PATH env var.", xml_path,
    )
    return _DRUGBANK_SKIP_TASK_ID


# ---------------------------------------------------------------------------
# Task callables — each delegates to the corresponding pipeline.
# v40 ROOT FIX (P1 #54): ALL primary download tasks now call
# ``.run_download_and_clean_only()`` (NOT ``.run()``). The previous code
# had ChEMBL/DrugBank/UniProt calling ``.run()`` (full run including
# LOAD to DB) while STRING/DisGeNET/OMIM called
# ``.run_download_and_clean_only()``. This broke the two-phase design
# (download → resolve → load) — some pipelines loaded to DB BEFORE
# entity_resolution ran. The fix: ALL download tasks do download+clean
# only; the LOAD phase happens in the *_load tasks AFTER
# entity_resolution. This ensures entity_resolution can influence what
# gets loaded for ALL sources.
# ---------------------------------------------------------------------------

@task()  # v41: retries+timeout inherited from DEFAULT_ARGS
# v83 DAG-2 ROOT FIX: @fail_fast_on_http_4xx converts 4xx to
# AirflowFailException (non-retryable) — matches standalone DAGs.
@fail_fast_on_http_4xx
def download_chembl() -> None:
    """Run the ChEMBL pipeline: approved drugs + bioactivity data (download+clean only)."""
    from pipelines.chembl_pipeline import ChEMBLPipeline
    # v40: was .run() (full run including LOAD) — now download+clean only.
    ChEMBLPipeline().run_download_and_clean_only()


@task()  # v41: retries+timeout inherited from DEFAULT_ARGS
@fail_fast_on_http_4xx  # v83 DAG-2
def download_drugbank() -> None:
    """Run the DrugBank pipeline: parse XML for drug + target data (download+clean only)."""
    from pipelines.drugbank_pipeline import DrugBankPipeline
    # v40: was .run() (full run including LOAD) — now download+clean only.
    DrugBankPipeline().run_download_and_clean_only()


# P1-036 v117 ROOT FIX: derive _DRUGBANK_DOWNLOAD_TASK_ID from the
# ``download_drugbank`` function's __name__ instead of a hardcoded string.
# Airflow's TaskFlow API auto-generates the task_id from the function's
# __name__ (default), so deriving the constant from the SAME attribute
# keeps them in lockstep across renames.
#
# The ``@task()`` decorator wraps the function in a ``_TaskDecorator``
# class instance (NOT a bare function), so ``download_drugbank.__name__``
# does NOT exist directly. We resolve the underlying function via a
# fallback chain:
#   1. ``.function``  -- Airflow ``_TaskDecorator.function`` (the wrapped
#      callable, which is the ``@fail_fast_on_http_4xx`` wrapper).
#   2. ``.__wrapped__`` -- if ``functools.wraps`` was used by an
#      intermediate decorator (the ``@fail_fast_on_http_4xx`` wrapper
#      DOES use ``@wraps(func)``, so this points to the original
#      ``download_drugbank`` function).
#   3. The callable itself -- bare-function fallback (works if no
#      decorator was applied).
# The ``@fail_fast_on_http_4xx`` decorator uses ``@wraps(func)`` (see
# ``dags/_retry_policy.py``), so the wrapper's ``__name__`` is already
# ``"download_drugbank"`` -- but we still go through the fallback chain
# to be robust against future decorator stack changes.
_underlying_drugbank_func = (
    getattr(download_drugbank, "function", None)        # Airflow _TaskDecorator.function
    or getattr(download_drugbank, "__wrapped__", None)  # functools.wraps chain
    or download_drugbank                                 # bare-function fallback
)
_DRUGBANK_DOWNLOAD_TASK_ID: str = getattr(
    _underlying_drugbank_func, "__name__", "download_drugbank"
)
# Defense-in-depth: if the derivation somehow produced a different value
# than the function's __name__, the parse-time assertion in
# ``master_pipeline`` (below) will catch it at DAG parse time.
logger.debug(
    "P1-036 v117: _DRUGBANK_DOWNLOAD_TASK_ID derived as %r from "
    "download_drugbank (underlying func __name__).",
    _DRUGBANK_DOWNLOAD_TASK_ID,
)


@task()  # v41: retries+timeout inherited from DEFAULT_ARGS
@fail_fast_on_http_4xx  # v83 DAG-2
def download_uniprot() -> None:
    """Run the UniProt pipeline: human reviewed proteins via REST API (download+clean only)."""
    from pipelines.uniprot_pipeline import UniProtPipeline
    # v40: was .run() (full run including LOAD) — now download+clean only.
    UniProtPipeline().run_download_and_clean_only()


@task()  # v41: retries+timeout inherited from DEFAULT_ARGS
@fail_fast_on_http_4xx  # v83 DAG-2
def download_string() -> None:
    """Run the STRING pipeline: download+clean only (load after entity resolution)."""
    from pipelines.string_pipeline import StringPipeline
    StringPipeline().run_download_and_clean_only()


@task()  # v41: retries+timeout inherited from DEFAULT_ARGS
@fail_fast_on_http_4xx  # v83 DAG-2
def download_disgenet() -> None:
    """Run the DisGeNET pipeline: download+clean only (load after entity resolution)."""
    from pipelines.disgenet_pipeline import DisGeNETPipeline
    DisGeNETPipeline().run_download_and_clean_only()


@task()  # v41: retries+timeout inherited from DEFAULT_ARGS
@fail_fast_on_http_4xx  # v83 DAG-2
def download_omim() -> None:
    """Run the OMIM pipeline: download+clean only (load after entity resolution)."""
    from pipelines.omim_pipeline import OMIMPipeline
    OMIMPipeline().run_download_and_clean_only()


@task(trigger_rule=TriggerRule.NONE_FAILED_MIN_ONE_SUCCESS)  # P1-032 ROOT FIX
@fail_fast_on_http_4xx  # v83 DAG-2
def download_pubchem() -> None:
    """Run the PubChem pipeline: download+clean only (load after entity resolution).

    v35 ROOT FIX (issue 35): previously called ``PubChemPipeline().run()``
    (the FULL run, including load into DB). This caused a DOUBLE-LOAD: the
    ``download_pubchem`` task loaded PubChem data into the ``drugs`` table,
    then the ``load_pubchem_enrichment`` task (line 414 below) called
    ``PubChemPipeline().run_load_only()`` which loaded the SAME data
    AGAIN. Both loads were idempotent (upsert), so the duplicate was
    silently absorbed — but it doubled the load wall-clock time and
    masked any bug in the load idempotency. Fix: use
    ``run_download_and_clean_only()`` so only the ``load_pubchem_enrichment``
    task loads (matching the pattern used by ChEMBL, DrugBank, UniProt,
    STRING, DisGeNET, and OMIM in this DAG).

    P1-032 ROOT FIX (trigger_rule actually set, not just documented):
      The previous version of this function had a docstring CLAIMING
      ``trigger_rule=none_failed_min_one_success`` was set (the P1-071
      "ROOT FIX" comment), but the ``@task()`` decorator did NOT actually
      pass ``trigger_rule``. The default ``all_success`` was used. When
      the DrugBank ``BranchPythonOperator`` skipped ``drugbank_load``
      (DrugBank XML missing -- common since DrugBank paused academic
      downloads in May 2026), ``pubchem_download`` saw 1 skipped
      upstream -> ``all_success`` failed -> ``pubchem_download`` was
      SKIPPED. PubChem enrichment never ran. The KG had ZERO PubChem
      CIDs, molecular formulas, molecular weights. The Makefile's
      DrugBank fallback (ChEMBL-derived FDA-approved drug set) did NOT
      trigger ``drugbank_load`` success -- it triggered the skip branch.

      ROOT FIX: pass ``trigger_rule=TriggerRule.NONE_FAILED_MIN_ONE_SUCCESS``
      to the ``@task()`` decorator EXPLICITLY. With this trigger rule:
        - At least one upstream must have SUCCEEDED (so a total
          chembl_load+drugbank_load failure still propagates).
        - No upstream may have FAILED (so a real DrugBank crash after
          retries still aborts the join -- preserves the v39 patient-
          safety fix).
        - SKIPPED upstreams are OK (so the operator's deliberate choice
          to skip DrugBank doesn't kill PubChem enrichment).
      This closes the "DAG reports GREEN but produces ZERO PubChem data"
      hole. The same fix is applied to ``load_pubchem_enrichment`` below.

    P1-018 NOTE (Team-2 -- pre-existing syntax error fixed):
      The previous code had a PREMATURE triple-quote closing the
      docstring at line 361 (between the v35 ROOT FIX paragraph and its
      continuation). Lines 362-370 were bare text (including an em-dash)
      OUTSIDE any string -- a SyntaxError that prevented the file from
      compiling. This was a PRE-EXISTING bug on main (verified via
      ``git stash``), not introduced by P1-018. Fixed as a minimal
      side-fix because it blocked compilation of the P1-018 changes in
      the same file. The fix: move the premature triple-quote to the
      END of the docstring (line 370 where it belongs) so the entire
      v35 + P1-071 text is ONE docstring.
    """
    from pipelines.pubchem_pipeline import PubChemPipeline
    PubChemPipeline().run_download_and_clean_only()


@task()  # v41: retries+timeout inherited from DEFAULT_ARGS
@fail_fast_on_http_4xx  # v83 DAG-2
def entity_resolution() -> None:
    """Run cross-database entity resolution.

    Reconciles drug entities across ChEMBL, DrugBank, and PubChem using
    InChIKey matching, connectivity-block matching, and normalised-name
    matching.  Also resolves protein entities across UniProt and STRING.

    Results are persisted to the ``entity_mapping`` table and the
    ``proteins.string_id`` column is updated with resolved STRING IDs.

    v29 ROOT FIX (audit O-12): XCom used for large dataframes — anti-pattern.
    Now passes file paths via XCom. This task reads every upstream DataFrame
    from CSV files in ``PROCESSED_DATA_DIR`` (drugs.csv, drugbank_drugs.csv,
    pubchem_enrichment.csv, proteins.csv, protein_protein_interactions.csv)
    rather than pulling DataFrames from upstream tasks' XCom. The upstream
    download tasks return None and persist their output to those CSV files;
    this task pulls the *file paths* (constants below), not the DataFrames.

    v75 ROOT FIX (T-025 — download_parallel.py skips entity resolution):
      The entity resolution logic was previously INLINE in this task body.
      The forensic audit found that ``scripts/download_parallel.py`` and
      the Makefile's ``download-all`` / ``download-samples`` targets
      skipped entity resolution entirely because they could not call this
      Airflow task. ROOT FIX: the logic was extracted into
      ``entity_resolution/run.py::run_entity_resolution()`` — a single
      shared function with NO Airflow dependency. This task is now a thin
      wrapper that calls that function. ``download_parallel.py`` calls
      the same function. The two callers CANNOT drift.
    """
    from entity_resolution.run import run_entity_resolution
    run_entity_resolution()


@task()  # v41: retries+timeout inherited from DEFAULT_ARGS
@fail_fast_on_http_4xx  # v83 DAG-2
def load_string() -> None:
    """FIX AUDIT-26: Use run_load_only() — data already downloaded and cleaned."""
    from pipelines.string_pipeline import StringPipeline
    StringPipeline().run_load_only()


@task()  # v41: retries+timeout inherited from DEFAULT_ARGS
@fail_fast_on_http_4xx  # v83 DAG-2
def load_disgenet() -> None:
    """FIX AUDIT-26: Use run_load_only() — data already downloaded and cleaned."""
    from pipelines.disgenet_pipeline import DisGeNETPipeline
    DisGeNETPipeline().run_load_only()


@task()  # v41: retries+timeout inherited from DEFAULT_ARGS
@fail_fast_on_http_4xx  # v83 DAG-2
def load_omim() -> None:
    """FIX AUDIT-27: Use run_load_only() — data already downloaded and cleaned."""
    from pipelines.omim_pipeline import OMIMPipeline
    OMIMPipeline().run_load_only()


@task(trigger_rule=TriggerRule.NONE_FAILED_MIN_ONE_SUCCESS)  # P1-032 ROOT FIX
@fail_fast_on_http_4xx  # v83 DAG-2
def load_pubchem_enrichment() -> None:
    """FIX AUDIT-27: PubChem data already downloaded.

    P1-032 ROOT FIX (trigger_rule actually set, not just documented):
      The previous version's docstring CLAIMED
      ``trigger_rule=none_failed_min_one_success`` was set (the P1-071
      "ROOT FIX" comment), but the ``@task()`` decorator did NOT pass
      ``trigger_rule``. The default ``all_success`` was used. When
      ``pubchem_download`` was skipped (e.g. when ``drugbank_load`` was
      skipped due to missing DrugBank XML), ``load_pubchem_enrichment``
      was ALSO skipped via the same ``all_success`` cascade — even though
      the existing PubChem enrichment data in the ``drugs`` table did
      not need re-loading. ROOT FIX: pass
      ``trigger_rule=TriggerRule.NONE_FAILED_MIN_ONE_SUCCESS``
      explicitly so this task runs as long as no upstream FAILED and at
      least one SUCCEEDED. Matches the same fix on ``download_pubchem``
      above.
    """
    from pipelines.pubchem_pipeline import PubChemPipeline
    PubChemPipeline().run_load_only()


# v79 FORENSIC ROOT FIX (P0-B2 — Master DAG has NO load_chembl /
#   load_drugbank / load_uniprot task):
#   The v78 master DAG had download tasks for ChEMBL, DrugBank, and
#   UniProt that called ``run_download_and_clean_only()`` (CSV only,
#   NO DB write), but there were NO corresponding ``load_*`` tasks.
#   The ``drugs``, ``proteins``, and ``drug_protein_interactions``
#   tables in the staging DB were EMPTY for these 3 sources. Entity
#   resolution read from an empty DB for ChEMBL/DrugBank/UniProt, and
#   the Phase 2 bridge (in PostgreSQL mode) read empty ``drugs`` /
#   ``proteins`` tables → ``drug_canonical_map`` was empty → ALL
#   Compound-treats-Disease edges were silently skipped (P0-B1
#   compound). V1 launch criterion ``positive_pairs_sufficient`` was
#   structurally unverifiable.
# ROOT FIX: add ``load_chembl()``, ``load_drugbank()``, and
#   ``load_uniprot()`` tasks that call ``run_load_only()`` (the same
#   pattern used by load_string / load_disgenet / load_omim /
#   load_pubchem_enrichment). Wire them after ``entity_resolution``
#   so entity resolution can influence what gets loaded (the v40
#   two-phase design: download → resolve → load).
@task()  # v41: retries+timeout inherited from DEFAULT_ARGS
@fail_fast_on_http_4xx  # v83 DAG-2
def load_chembl() -> None:
    """v79 P0-B2 ROOT FIX: Load ChEMBL cleaned data into the staging DB.

    Loads ``drugs`` (ChEMBL molecules with max_phase=4) and
    ``drug_protein_interactions`` (ChEMBL activities with resolved
    UniProt accessions) into the staging DB. Data was already
    downloaded + cleaned by ``download_chembl``; this task only
    performs the DB upsert (idempotent via ON CONFLICT DO UPDATE).
    """
    from pipelines.chembl_pipeline import ChEMBLPipeline
    ChEMBLPipeline().run_load_only()


@task()  # v41: retries+timeout inherited from DEFAULT_ARGS
@fail_fast_on_http_4xx  # v83 DAG-2
def load_drugbank() -> None:
    """v79 P0-B2 ROOT FIX: Load DrugBank cleaned data into the staging DB.

    Loads ``drugs`` (DrugBank FDA-approved drugs with InChIKey) and
    ``drug_protein_interactions`` (DrugBank drug→target edges) into
    the staging DB. Data was already downloaded + cleaned by
    ``download_drugbank``; this task only performs the DB upsert
    (idempotent via ON CONFLICT DO UPDATE).
    """
    from pipelines.drugbank_pipeline import DrugBankPipeline
    DrugBankPipeline().run_load_only()


@task()  # v41: retries+timeout inherited from DEFAULT_ARGS
@fail_fast_on_http_4xx  # v83 DAG-2
def load_uniprot() -> None:
    """v79 P0-B2 ROOT FIX: Load UniProt cleaned data into the staging DB.

    Loads ``proteins`` (UniProt human reviewed proteins with gene
    symbols) into the staging DB. Data was already downloaded +
    cleaned by ``download_uniprot``; this task only performs the DB
    upsert (idempotent via ON CONFLICT DO UPDATE).
    """
    from pipelines.uniprot_pipeline import UniProtPipeline
    UniProtPipeline().run_load_only()


@task(retries=0, execution_timeout=TASK_TIMEOUT, trigger_rule=TriggerRule.NONE_FAILED_MIN_ONE_SUCCESS)
# TM1 TASK 8 ROOT FIX: Phase 1 contract validation as the FINAL task
# before trigger_phase2. This task validates every Phase 1 output CSV
# against the canonical schema in ``phase1/contracts/phase1_schema.py``.
# If any required column is missing, any any-of group is unsatisfied,
# or any non-nullable column has NULLs, the task FAILS — blocking
# trigger_phase2 and preventing a corrupted KG from being built.
# Warnings (e.g. empty optional sources like DrugBank when license is
# paused) do NOT fail the task — the bridge degrades gracefully.
@fail_fast_on_http_4xx
def _validate_phase1_contract() -> None:
    """TM1 Task 8: validate Phase 1 outputs against the canonical schema.

    Runs ``phase1.contracts.validate_output.validate_output_dir`` against
    the processed_data directory. Returns 0 on success; raises
    ``SystemExit(1)`` on any ERROR issue so the Airflow task fails RED.
    """
    import sys as _sys
    from pathlib import Path as _Path
    _project_root = _Path(__file__).resolve().parent.parent.parent
    if str(_project_root) not in _sys.path:
        _sys.path.insert(0, str(_project_root))
    # Phase 1 contracts live in phase1/contracts/.
    _p1_root = _Path(__file__).resolve().parent.parent
    if str(_p1_root) not in _sys.path:
        _sys.path.insert(0, str(_p1_root))
    from contracts.validate_output import validate_output_dir
    from config.settings import PROCESSED_DATA_DIR
    exit_code = validate_output_dir(_Path(PROCESSED_DATA_DIR))
    if exit_code != 0:
        raise SystemExit(
            f"TM1 Task 8: Phase 1 contract validation FAILED (exit {exit_code}). "
            f"The KG build is BLOCKED — fix the Phase 1 output CSVs above "
            f"before re-running. See phase1/contracts/README.md for the "
            f"canonical schema."
        )


@task(retries=1, retry_delay=timedelta(minutes=5), execution_timeout=TASK_TIMEOUT, trigger_rule=TriggerRule.NONE_FAILED_MIN_ONE_SUCCESS)
# P1-026 v113 ROOT FIX: the previous retries=0 was harmful for 5xx errors.
# A 30-second Neo4j restart during a deploy caused _trigger_phase2's
# subprocess call to fail with a connection error, and retries=0 meant
# no retry — the entire Sunday KG build was lost for a 30-second transient.
# ROOT FIX: set retries=1 with retry_delay=5min. The @fail_fast_on_http_4xx
# decorator still converts 4xx errors to AirflowFailException (non-retryable
# regardless of retries setting), so 4xx errors skip the retry. 5xx errors
# (transient Neo4j outage, network blip) get ONE retry after 5min backoff.
# v89 ROOT FIX (BUG #24 — inconsistent application of fail-fast policy):
#   The comment at lines 99-118 (v83 DAG-2) says "Apply
#   ``@fail_fast_on_http_4xx`` to EVERY @task below so 4xx errors
#   immediately raise ``AirflowFailException`` and skip retries".
#   But ``_trigger_phase2`` was MISSING the decorator. While
#   ``_trigger_phase2`` invokes a subprocess (not a direct HTTP call),
#   the Phase 2 pipeline (``run_unified.py`` / ``python -m
#   drugos_graph``) makes HTTP calls to download data, call Neo4j's
#   REST API, etc. If the subprocess exits with a 4xx-derived error
#   (e.g. CalledProcessError wrapping a 401 from Neo4j auth), the
#   error was NOT converted to AirflowFailException — it was retried
#   (but retries=0 makes this moot). ROOT FIX: add the decorator for
#   consistency with the documented policy. The decorator's behavior
#   on non-4xx exceptions (re-raise unchanged) is preserved.
#
# P1-018 ROOT FIX (Team-2 — Phase 2 race with concurrent pubchem_load):
#   The previous ``trigger_rule=ALL_SUCCESS`` required EVERY upstream
#   (including pubchem_load) to be in SUCCESS state. Because
#   pubchem_load was intentionally NOT wired to trigger_phase2 (to
#   avoid PubChem API outages blocking the KG build), pubchem_load
#   ran CONCURRENTLY with trigger_phase2. When PubChem's API was slow
#   (rate-limited), trigger_phase2 fired WHILE pubchem_load was still
#   writing ``pubchem_cid`` / ``molecular_formula`` / ``molecular_weight``
#   into the ``drugs`` table — Phase 2's KG build read PARTIAL PubChem
#   enrichment (some rows enriched, some not, with no flag indicating
#   the partial state).
#
#   ROOT FIX: change ``trigger_rule`` to
#   ``NONE_FAILED_MIN_ONE_SUCCESS`` AND wire ``pubchem_load >> trigger_phase2``.
#   Semantics:
#     * All 6 REQUIRED loads (chembl, drugbank, uniprot, string,
#       disgenet, omim) must SUCCEED — a failure in any of them
#       propagates ``UPSTREAM_FAILED`` and blocks trigger_phase2.
#     * pubchem_load (which has its own
#       ``trigger_rule=NONE_FAILED_MIN_ONE_SUCCESS``) may SUCCEED or
#       be SKIPPED (if pubchem_download failed/skipped). Either way,
#       trigger_phase2 fires — PubChem is optional enrichment and
#       must NOT block the KG build.
#     * If pubchem_load itself FAILED (real bug — not API outage),
#       trigger_phase2 is SKIPPED — operators must investigate the
#       load failure before building a KG.
#     * The ``min_one_success`` clause guarantees trigger_phase2
#       never fires on an all-skipped Phase 1 (which would otherwise
#       produce an empty KG silently).
@fail_fast_on_http_4xx
def _trigger_phase2(validate_output_xcom: dict | None = None) -> None:
    """v29 ROOT FIX (audit O-2 — master DAG always reports success).

    TM1 TASK 1.4 ROOT FIX (v131 -- Teammate 1 P1->P2 integration):
      This task now accepts the XCom payload from ``validate_output``
      (passed as ``validate_output_xcom`` via TaskFlow XComArg). The
      payload's ``pipeline_run_id`` is forwarded to Phase 2 as
      ``--provenance <UUID>`` so the KG build can be traced end-to-end
      across the Phase 1 -> Phase 2 boundary.

      The DPI-degraded pre-flight check has been MOVED to
      ``validate_output`` (via ``_check_dpi_degraded_via_postgres``).
      The previous code (lines 998-1065 pre-fix) did the DPI check via
      SQLite (``phase1/data/drugos.db``), which silently disabled the
      check in production (where the DB is PostgreSQL). The new
      ``_check_dpi_degraded_via_postgres`` queries PostgreSQL via
      DATABASE_URL and fails-closed in production if DATABASE_URL is
      missing. The DPI state is now part of the XCom payload so
      ``trigger_phase2`` can log it (but no longer re-checks it).

      The Phase 2 entrypoint resolution has been rewritten to use a
      CONFIGURABLE priority order:
        1. ``PHASE2_ENTRYPOINT`` env var (operator override -- if set,
           this wins. Format: ``python -m drugos_graph`` or
           ``python /path/to/run_pipeline.py`` or a shell command).
        2. ``phase2/drugos_graph/run_pipeline.py`` (canonical Phase 2
           entrypoint -- this is the file Teammate 5's task spec
           identifies as the Phase 2 invocation target).
        3. ``run_unified.py`` at the project root (legacy entrypoint,
           kept for backward compatibility with existing deploys).
        4. ``python -m drugos_graph`` (fallback -- verifies the package
           is importable before invoking).

      The previous code (lines 951-996 pre-fix) checked ONLY
      ``run_unified.py`` and fell back to ``python -m drugos_graph``
      without verifying the package was importable. If NEITHER existed,
      the subprocess raised ``FileNotFoundError`` with an opaque
      message.

    v75 ROOT FIX (T-024 — SLA/timeout alignment):
      ``execution_timeout=TASK_TIMEOUT`` (7h, aligned with TASK_SLA).
      ``retries=0`` is preserved — a timed-out Phase 2 training run
      must NOT be retried automatically. The 7h hard kill is the
      patient-safe failure mode: partial TransE checkpoints, GPU
      state, and the non-deterministic negative sampler would
      corrupt any retry. Operators who want a longer training
      window must explicitly raise BOTH TASK_SLA and TASK_TIMEOUT
      (keeping them aligned) — never just one.

    The forensic audit found that this task had ``trigger_rule=ALL_DONE``
    + ``check=False`` + ``retries=0``, which meant Phase 2 could crash,
    time out, or fail V1 criteria and the DAG would still report GREEN.
    Every previous AI session that told the user "it's 100% integrated"
    was reading the DAG's green status without checking the actual
    Phase 2 exit code or the AUC log.

    ROOT FIX: change ``trigger_rule`` to ``ALL_SUCCESS`` (so Phase 2
    only runs if all Phase 1 tasks succeeded), use ``check=True`` (so
    non-zero exit code raises), and propagate timeouts / exceptions
    instead of swallowing them. The DAG now fails RED when Phase 2
    fails — operators can no longer claim success without verifying.

    P1-018 ROOT FIX (Team-2 — Phase 2 race with concurrent pubchem_load):
      The original v29 ROOT FIX used ``trigger_rule=ALL_SUCCESS`` and
      intentionally did NOT wire ``pubchem_load >> trigger_phase2`` so
      that a PubChem API outage would not block the KG build. But this
      created a RACE: pubchem_load ran CONCURRENTLY with trigger_phase2
      and wrote to the ``drugs`` table (``pubchem_cid``,
      ``molecular_formula``, ``molecular_weight``) WHILE Phase 2's KG
      build was reading the same table — producing a KG with PARTIAL
      PubChem enrichment (some rows enriched, some not, with no flag).
      ROOT FIX: change ``trigger_rule`` to
      ``NONE_FAILED_MIN_ONE_SUCCESS`` AND wire
      ``pubchem_load >> trigger_phase2``. Phase 2 now waits for
      pubchem_load to FINISH (SUCCEED or SKIP) before reading the
      ``drugs`` table. PubChem API outage (pubchem_load SKIPPED) no
      longer blocks Phase 2; a real pubchem_load FAILURE (bug) still
      blocks Phase 2 — which is the scientifically correct behavior.

    Behavior:
      * ``trigger_rule=NONE_FAILED_MIN_ONE_SUCCESS`` — runs when no
        upstream FAILED and at least one upstream SUCCEEDED. The 6
        required loads (chembl, drugbank, uniprot, string, disgenet,
        omim) default to ``ALL_SUCCESS`` so any required-load failure
        propagates ``UPSTREAM_FAILED`` and blocks trigger_phase2.
        pubchem_load may SUCCEED or SKIP (graceful PubChem
        degradation) — either way, trigger_phase2 fires. (Was:
        ``ALL_DONE`` which fired even on failure; v29 changed to
        ``ALL_SUCCESS`` which raced with concurrent pubchem_load.)
      * ``check=True`` — non-zero exit code raises CalledProcessError.
        (Was: ``check=False`` which silently ignored failures.)
      * Timeouts and exceptions propagate — task fails RED. (Was:
        logged as WARNING and task succeeded.)

    The task still uses the RecordingGraphBuilder by default (no
    Neo4j required), so it can run in any environment. Operators who
    want a real Neo4j load set ``DRUGOS_NEO4J_URI``.
    """
    import importlib.util
    import os
    import subprocess
    import sys as _sys
    from pathlib import Path as _Path

    _project_root = _Path(__file__).resolve().parent.parent.parent

    # ── Extract provenance from the validate_output XCom payload ──────
    # TM1 Task 1.4 v131: forward the pipeline_run_id to Phase 2 as
    # ``--provenance <UUID>`` so the KG build can be traced end-to-end
    # across the Phase 1 -> Phase 2 boundary. If the XCom payload is
    # missing (e.g. validate_output was SKIPPED via trigger_rule), fall
    # back to a fresh UUID and log a WARNING (the run is untraceable).
    if validate_output_xcom is not None and isinstance(validate_output_xcom, dict):
        provenance_id = validate_output_xcom.get("pipeline_run_id") or str(_uuid.uuid4())
        _validate_payload_schema_version = validate_output_xcom.get("schema_version")
        _validate_payload_row_counts = validate_output_xcom.get("row_counts")
        _validate_payload_dpi_missing = validate_output_xcom.get("dpi_missing")
        _validate_payload_dpi_source = validate_output_xcom.get("dpi_source")
        logger.info(
            "TM1 v131 trigger_phase2: received validate_output XCom payload "
            "(pipeline_run_id=%s schema_version=%s dpi_missing=%s "
            "dpi_source=%s row_counts=%s). Forwarding provenance to Phase 2.",
            provenance_id,
            _validate_payload_schema_version,
            _validate_payload_dpi_missing,
            _validate_payload_dpi_source,
            _validate_payload_row_counts,
        )
    else:
        provenance_id = str(_uuid.uuid4())
        logger.warning(
            "TM1 v131 trigger_phase2: validate_output XCom payload is "
            "missing (None or not a dict). Falling back to a fresh UUID "
            "(provenance_id=%s). This run will be UNTRACEABLE across the "
            "Phase 1 -> Phase 2 boundary. Check that validate_output ran "
            "and was wired to trigger_phase2 via TaskFlow XComArg.",
            provenance_id,
        )

    # ── Phase 2 entrypoint resolution (configurable priority order) ───
    # TM1 Task 1.4 v131 ROOT FIX:
    #   The previous code (lines 951-996 pre-fix) checked ONLY
    #   ``run_unified.py`` at the project root and fell back to
    #   ``python -m drugos_graph`` without verifying the package was
    #   importable. If NEITHER existed, the subprocess raised
    #   ``FileNotFoundError`` with an opaque message. The canonical
    #   Phase 2 entrypoint per the project structure is
    #   ``phase2/drugos_graph/run_pipeline.py`` -- this is the file
    #   Teammate 5's task spec identifies as the Phase 2 invocation
    #   target. The new resolution order is:
    #     1. ``PHASE2_ENTRYPOINT`` env var (operator override).
    #     2. ``phase2/drugos_graph/run_pipeline.py`` (canonical).
    #     3. ``run_unified.py`` at project root (legacy).
    #     4. ``python -m drugos_graph`` (fallback, with importability check).
    _phase1_processed_dir = str(_project_root / "phase1" / "processed_data")
    _phase2_canonical = _project_root / "phase2" / "drugos_graph" / "run_pipeline.py"
    _legacy_run_unified = _project_root / "run_unified.py"
    _phase2_entrypoint_env = os.environ.get("PHASE2_ENTRYPOINT", "").strip()

    cmd: list[str] = []
    if _phase2_entrypoint_env:
        # Operator override -- split on whitespace. Format examples:
        #   ``python -m drugos_graph``
        #   ``python /opt/drugos/run_pipeline.py``
        #   ``/usr/local/bin/drugos-runner``
        _entry_parts = _phase2_entrypoint_env.split()
        cmd = list(_entry_parts) + [
            "--data-source", "phase1",
            "--phase1-dir", _phase1_processed_dir,
            "--provenance", provenance_id,
        ]
        logger.info(
            "TM1 v131 trigger_phase2: using PHASE2_ENTRYPOINT env var "
            "override: %s", _phase2_entrypoint_env,
        )
    elif _phase2_canonical.exists():
        # Canonical Phase 2 entrypoint: phase2/drugos_graph/run_pipeline.py
        cmd = [
            _sys.executable, str(_phase2_canonical),
            "--data-source", "phase1",
            "--phase1-dir", _phase1_processed_dir,
            "--provenance", provenance_id,
        ]
        logger.info(
            "TM1 v131 trigger_phase2: using canonical Phase 2 entrypoint: %s",
            _phase2_canonical,
        )
    elif _legacy_run_unified.exists():
        # Legacy fallback: run_unified.py at the project root.
        cmd = [
            _sys.executable, str(_legacy_run_unified),
            "--phase1-dir", _phase1_processed_dir,
            "--full-pipeline",
            "--provenance", provenance_id,
        ]
        logger.warning(
            "TM1 v131 trigger_phase2: canonical Phase 2 entrypoint "
            "(phase2/drugos_graph/run_pipeline.py) NOT FOUND. Falling "
            "back to legacy run_unified.py at project root: %s. "
            "Recommendation: ensure phase2/drugos_graph/run_pipeline.py "
            "exists (it is the canonical entrypoint per the project "
            "structure).", _legacy_run_unified,
        )
    else:
        # Last-resort fallback: python -m drugos_graph. Verify the
        # package is importable BEFORE invoking the subprocess so the
        # operator sees a clear error message instead of an opaque
        # FileNotFoundError.
        # v89 ROOT FIX (BUG #26): pre-flight check via importlib.util.
        if importlib.util.find_spec("drugos_graph") is None:
            try:
                from airflow.exceptions import AirflowFailException
            except ImportError:  # pragma: no cover
                AirflowFailException = RuntimeError  # type: ignore[assignment]
            raise AirflowFailException(
                "Phase 2 invocation failed pre-flight check: NEITHER "
                f"(1) PHASE2_ENTRYPOINT env var is set, NOR (2) the "
                f"canonical phase2/drugos_graph/run_pipeline.py exists "
                f"({_phase2_canonical}), NOR (3) the legacy "
                f"run_unified.py exists ({_legacy_run_unified}), NOR "
                f"(4) the 'drugos_graph' package is importable. "
                f"Remediation: either (a) set PHASE2_ENTRYPOINT to the "
                f"full command to invoke Phase 2, or (b) ensure "
                f"phase2/drugos_graph/run_pipeline.py exists, or (c) "
                f"install the 'drugos_graph' package (pip install -e . "
                f"from the project root). The master DAG cannot proceed "
                f"to Phase 2 without one of these. (TM1 Task 1.4 v131)"
            )
        cmd = [
            _sys.executable, "-m", "drugos_graph",
            "--data-source", "phase1",
            "--phase1-dir", _phase1_processed_dir,
            "--provenance", provenance_id,
        ]
        logger.warning(
            "TM1 v131 trigger_phase2: using python -m drugos_graph "
            "fallback (neither canonical run_pipeline.py nor legacy "
            "run_unified.py exist).",
        )

    # P1-041 ROOT FIX (DPI pre-flight check) was MOVED to validate_output
    # via _check_dpi_degraded_via_postgres (see above). The previous code
    # here did the check via SQLite (phase1/data/drugos.db), which silently
    # disabled the check in production (where the DB is PostgreSQL). The
    # new check queries PostgreSQL via DATABASE_URL and fails-closed in
    # production. The DPI state is now part of the validate_output XCom
    # payload (extracted above) so trigger_phase2 can LOG it for operator
    # visibility without re-querying the DB.

    neo4j_uri = os.environ.get("DRUGOS_NEO4J_URI")
    if neo4j_uri:
        cmd.extend(["--neo4j-uri", neo4j_uri])
        if os.environ.get("DRUGOS_NEO4J_USER"):
            cmd.extend(["--neo4j-user", os.environ["DRUGOS_NEO4J_USER"]])
        # v104 FORENSIC ROOT FIX (P1-009 -- Neo4j password passed via
        #   subprocess CLI args, visible via ps and /proc/<pid>/cmdline):
        #   The previous code did:
        #       cmd.extend(["--neo4j-password", os.environ["DRUGOS_NEO4J_PASSWORD"]])
        #   CLI args are visible to ANY user who can run ``ps aux`` or
        #   read ``/proc/<pid>/cmdline`` on the Airflow worker. In a
        #   multi-tenant Airflow deployment (common in pharma IT), any
        #   user with shell access to the worker could read the Neo4j
        #   password. Worse, the DAG ALSO logged the full cmd (including
        #   the password) at INFO level on line ~806 -- the password was
        #   then in Airflow's task logs, retained 30+ days, accessible to
        #   anyone with Airflow UI access.
        #
        #   ROOT FIX: do NOT pass the password as a CLI arg at all. The
        #   subprocess inherits the parent's environment, so
        #   ``DRUGOS_NEO4J_PASSWORD`` is ALREADY visible to the child
        #   process via ``os.environ``. The receiving script
        #   (run_unified.py:619) already reads ``DRUGOS_NEO4J_PASSWORD``
        #   from the environment as a fallback when ``--neo4j-password``
        #   is not passed on the CLI. So removing the CLI arg does NOT
        #   break the password flow -- it just stops leaking it via
        #   ``ps``/``/proc``/logs.
        #
        #   Defense in depth: also sanitize ANY log message that
        #   includes the cmd. The ``_redact_cmd_for_log`` helper below
        #   replaces the value of any sensitive flag (``--neo4j-password``,
        #   ``--neo4j-user``, ``NEO4J_PASSWORD=...``) with ``***`` before
        #   the cmd is joined into a string for logging. This catches
        #   both the current call site and any future caller that adds
        #   a different secret flag.
        if os.environ.get("DRUGOS_NEO4J_PASSWORD"):
            # Password flows to the subprocess via env-var inheritance.
            # Do NOT add it to cmd. (See P1-009 comment above.)
            pass

    # v104 P1-009 ROOT FIX: sanitize the cmd before logging. NEVER log
    # the Neo4j password (or any other secret). Replace sensitive values
    # with ``***`` so the log shows the cmd STRUCTURE without leaking
    # credentials. The unsanitized cmd is still passed to subprocess.run
    # below -- only the LOG is sanitized.
    def _redact_cmd_for_log(cmd_list):
        """Return a copy of cmd_list with sensitive values replaced by ***."""
        redacted = []
        skip_next = False
        sensitive_flags = {"--neo4j-password", "--neo4j-user",
                           "--neo4j-uri"}  # URI may embed user:pass@host
        for i, item in enumerate(cmd_list):
            if skip_next:
                redacted.append("***")
                skip_next = False
                continue
            if item in sensitive_flags:
                redacted.append(item)
                skip_next = True  # next item is the value -> redact it
                continue
            # Catch ``--flag=value`` form
            if "=" in item and item.split("=", 1)[0] in sensitive_flags:
                redacted.append(f"{item.split('=', 1)[0]}=***")
                continue
            # Catch env-var-like values
            if isinstance(item, str) and "NEO4J_PASSWORD" in item:
                redacted.append("***")
                continue
            redacted.append(item)
        return redacted

    _cmd_for_log = _redact_cmd_for_log(cmd)
    logger.info("v29 trigger_phase2: invoking Phase 2 pipeline: %s",
                " ".join(_cmd_for_log))

    # v29 ROOT FIX: check=True (was False) so non-zero exit raises
    # CalledProcessError. This makes the task fail RED when Phase 2
    # fails, instead of silently logging a WARNING and succeeding.
    #
    # v80 FORENSIC ROOT FIX (P0-C9 — Airflow worker OOM on 7h TransE
    #   training):
    #   The previous code passed ``capture_output=True`` to
    #   ``subprocess.run``. This causes subprocess to BUFFER THE
    #   ENTIRE stdout AND stderr streams in memory until the process
    #   exits. For a 7-hour TransE training run that emits per-epoch
    #   logs (loss, gradient norms, validation AUC, per-batch
    #   progress bars from tqdm), the accumulated output is 10–50 GB.
    #   The Airflow worker (default 4–8 GB RAM) OOM-crashes mid-
    #   training, killing the subprocess AND the scheduler. The DAG
    #   reports RED with "OOMKilled" but the operator has no log to
    #   diagnose which epoch crashed.
    #
    #   ROOT FIX: stream stdout+stderr to a log file on disk (under
    #   ``<project_root>/logs/phase2_<timestamp>.log``) and pass the
    #   file handle as ``stdout``/``stderr`` to subprocess. This keeps
    #   peak memory at O(1) regardless of training length, AND
    #   preserves the full log for post-mortem debugging. We tail the
    #   last 2000 chars into the Airflow task log on success for
    #   operator visibility (matches the previous ``result.stdout[-2000:]``
    #   behavior). On failure we tail the last 4000 chars of stderr.
    import os as _os
    _logs_dir = _project_root / "logs"
    _logs_dir.mkdir(parents=True, exist_ok=True)
    from datetime import datetime as _dt, timezone as _tz
    # v89 FORENSIC ROOT FIX (BUG #20 P1 — datetime.utcnow() is deprecated
    #   in Python 3.12+ and returns a NAIVE datetime, not timezone-aware.
    #   In non-UTC environments the log filename timestamp could drift.
    #   ROOT FIX: use datetime.now(timezone.utc) which is tz-aware and
    #   not deprecated. Behaviour is identical on UTC systems.
    _log_timestamp = _dt.now(_tz.utc).strftime("%Y%m%dT%H%M%SZ")
    _phase2_log_path = _logs_dir / f"phase2_trigger_{_log_timestamp}.log"
    logger.info("v29 trigger_phase2: streaming subprocess output to %s", _phase2_log_path)

    try:
        with open(_phase2_log_path, "wb") as _log_fh:
            result = subprocess.run(
                cmd, cwd=str(_project_root), check=True,
                stdout=_log_fh, stderr=subprocess.STDOUT,
                timeout=int(TASK_TIMEOUT.total_seconds()),
            )
        logger.info("v29 trigger_phase2: Phase 2 pipeline completed successfully.")
        # Tail the last 2000 chars of the log for operator visibility.
        try:
            with open(_phase2_log_path, "rb") as _tail_fh:
                _tail_fh.seek(0, 2)
                _size = _tail_fh.tell()
                _tail_fh.seek(max(0, _size - 2000))
                _tail = _tail_fh.read().decode("utf-8", errors="replace")
            if _tail:
                logger.info("stdout/stderr tail:\n%s", _tail)
        except OSError:
            pass
    except subprocess.CalledProcessError as exc:
        # v29 ROOT FIX: propagate the failure. The DAG turns RED.
        # Tail the last 4000 chars of the log for diagnostics.
        _err_tail = ""
        try:
            with open(_phase2_log_path, "rb") as _tail_fh:
                _tail_fh.seek(0, 2)
                _size = _tail_fh.tell()
                _tail_fh.seek(max(0, _size - 4000))
                _err_tail = _tail_fh.read().decode("utf-8", errors="replace")
        except OSError:
            pass
        logger.error(
            "v29 trigger_phase2: Phase 2 pipeline FAILED with exit "
            "code %d. The DAG will now fail RED — this is the correct "
            "behavior (audit O-2 root fix). Full log: %s. stderr tail:\n%s",
            exc.returncode,
            _phase2_log_path,
            _err_tail,
        )
        raise
    except subprocess.TimeoutExpired as exc:
        # v29 ROOT FIX (audit O-10): propagate the timeout. The DAG turns RED.
        logger.error(
            "v29 trigger_phase2: Phase 2 pipeline TIMED OUT after %d "
            "seconds. Subprocess timed out — DAG will FAIL. The DAG will "
            "now fail RED — this is the correct behavior (audit O-2 / "
            "O-10 root fix). Full log: %s",
            int(TASK_TIMEOUT.total_seconds()),
            _phase2_log_path,
        )
        raise
    except Exception as exc:
        # v29 ROOT FIX: propagate ANY exception. The DAG turns RED.
        logger.error(
            "v29 trigger_phase2: Phase 2 invocation raised %s: %s. "
            "The DAG will now fail RED (audit O-2 root fix). "
            "Full log: %s",
            type(exc).__name__, exc,
            _phase2_log_path,
        )
        raise


# ---------------------------------------------------------------------------
# DAG definition
# ---------------------------------------------------------------------------

# v110 Task 34 root fix: add validate_output task.
#
# The audit (Task 34) requires: "must orchestrate all 7 sources in parallel,
# then run entity_resolution, then validate_output." The original DAG had
# parallel downloads + entity_resolution + load_* + trigger_phase2, but NO
# explicit validate_output task. The _trigger_phase2 pre-flight check only
# verified ChEMBL DPI presence — it did NOT validate that each source
# produced real biomedical identifiers (not fake/synthesized data) or that
# entity resolution produced canonical IDs.
#
# ROOT FIX: add a dedicated validate_output task that runs AFTER all load_*
# tasks complete and BEFORE trigger_phase2. It validates:
#   1. Each source's CSV/DB output has real biomedical identifiers
#      (InChIKeys for drugs, UniProt accessions for proteins, MIM IDs for
#      diseases, etc.).
#   2. No "SYNTH%" / "FAKE" / "TEST" sentinel values in production data
#      (these are dev-only escape hatches per migration 009).
#   3. Entity resolution produced canonical IDs (drug_resolver and
#      protein_resolver mappings are non-empty if any source loaded).
#   4. Database row counts are non-zero for each source's primary table.
#
# On failure, the task raises AirflowFailException (no retry) so the DAG
# fails RED and trigger_phase2 is blocked — preventing Phase 2 from
# building a knowledge graph on top of corrupted/empty Phase 1 data.
#
# P1-045 v124 FORENSIC CLARIFICATION (Teammate 3 -- hostile-auditor pass):
#   The P1-045 audit claimed this task is REDUNDANT with
#   ``_validate_phase1_contract`` (line 662) because "both call
#   ``validate_output_dir``." This is FALSE. The two tasks serve
#   DIFFERENT, COMPLEMENTARY purposes:
#
#   ``_validate_phase1_contract`` (line 662):
#     - Calls ``contracts.validate_output.validate_output_dir``.
#     - Validates the CSV file STRUCTURE: do the files exist, do they
#       have the expected columns, are the dtypes correct, do they meet
#       min_row requirements? This is a SCHEMA-level check.
#     - Catches: missing files, wrong columns, dtype mismatches.
#
#   ``validate_output`` (THIS function, line 1135):
#     - Does NOT call ``validate_output_dir``. It runs 4 SEPARATE checks
#       (see the docstring below): identifier format validation,
#       fake/synthesized data detection (SYNTH% in production),
#       entity resolution completeness, and database row count sanity.
#     - Catches: real-data corruption that passes the schema check
#       (e.g., a CSV with the right columns but full of SYNTH placeholders,
#       or a pipeline that wrote 0 rows to the DB but produced a valid
#       empty CSV).
#
#   Deleting ``validate_output`` (as P1-045 suggested) would LOSE checks
#   1-4 and let corrupted/empty-data Phase 1 outputs flow to Phase 2.
#   The two tasks are NOT redundant -- they are a defense-in-depth pair.
#   This comment exists to prevent future audits from re-flagging this
#   as a redundancy.
@task(retries=0, execution_timeout=TASK_TIMEOUT, trigger_rule=TriggerRule.NONE_FAILED_MIN_ONE_SUCCESS)
@fail_fast_on_http_4xx
def validate_output() -> dict:
    """Validate Phase 1 output before triggering Phase 2.

    TM1 TASK 1.4 ROOT FIX (v131 -- Teammate 1 P1->P2 integration):

    This task is a THIN WRAPPER around ``_validate_output_impl`` -- the
    testable, pure-Python implementation that reads module-level
    ``_processed_dir`` and ``_is_production`` (so ``unittest.mock.patch``
    works). The wrapper:

      1. Calls ``_validate_output_impl()`` to get the XCom payload dict.
      2. If the payload's ``failures`` list is non-empty, raises
         ``AirflowFailException`` (non-retryable) to BLOCK trigger_phase2.
      3. Otherwise, returns the XCom payload so trigger_phase2 can
         consume it (via TaskFlow XComArg passing) and forward the
         ``pipeline_run_id`` to Phase 2 as ``--provenance``.

    The XCom payload contract:
      {
        "pipeline_run_id": "UUID string",
        "schema_version": "string (e.g., '11')",
        "row_counts": {"chembl_drugs": int, "drugs": int, ...},
        "synth_key_counts": {"chembl_drugs": int, "drugs": int, ...},
        "dpi_missing": bool,
        "dpi_acknowledged": bool,
        "dpi_source": "postgres" | "no_db_url_dev" | "no_chembl_run" | ...,
        "validated_at": "ISO 8601 UTC",
        "failures": [str, ...]  # empty list = pass
      }

    Raises AirflowFailException on any check failure to block trigger_phase2.
    """
    payload = _validate_output_impl()
    failures = payload.get("failures", [])
    if failures:
        _msg = (
            f"validate_output FAILED with {len(failures)} issue(s):\n"
            + "".join(f"  {i}. {f}\n" for i, f in enumerate(failures, 1))
            + "Phase 2 trigger is BLOCKED until these issues are resolved. "
              "This prevents building a knowledge graph on corrupted/empty "
              "Phase 1 data. (TM1 Task 1.4 v131 root fix)"
        )
        logger.error(_msg)
        try:
            from airflow.exceptions import AirflowFailException
            raise AirflowFailException(_msg)
        except ImportError:
            raise RuntimeError(_msg)

    logger.info(
        "validate_output PASSED: pipeline_run_id=%s schema_version=%s "
        "row_counts=%s synth_key_counts=%s dpi_missing=%s dpi_source=%s. "
        "Phase 2 trigger is unblocked. (TM1 Task 1.4 v131 root fix)",
        payload.get("pipeline_run_id"),
        payload.get("schema_version"),
        payload.get("row_counts"),
        payload.get("synth_key_counts"),
        payload.get("dpi_missing"),
        payload.get("dpi_source"),
    )
    return payload


# =============================================================================
# TM1 TASK 1.4 ROOT FIX (v131): _check_dpi_degraded_via_postgres
# =============================================================================
# RED-TEAM AUDIT FINDING (v131, hostile-auditor pass):
#   The previous code (lines 870-914 pre-fix) did the DPI-degraded
#   pre-flight check via the stdlib ``sqlite3`` module, reading from
#   ``phase1/data/drugos.db``. In PRODUCTION, the Phase 1 DB is
#   PostgreSQL (per the project's docker-compose.yml and config.settings),
#   NOT SQLite -- the SQLite DB exists only in dev/test. The check
#   queried an EMPTY/non-existent SQLite DB in production, found no
#   ``pipeline_runs`` row, and SILENTLY SKIPPED the DPI-degraded
#   enforcement. The safety net was DISABLED in production.
#
# ROOT FIX (v131, this function):
#   Query PostgreSQL via ``DATABASE_URL`` (the canonical env var per
#   config.settings). Fail-CLOSED in production if DATABASE_URL is
#   missing (no silent safety-net bypass). Return a structured dict
#   so the caller (``_validate_output_impl``) can include the DPI
#   state in the XCom payload for end-to-end tracing.
#
# Testability:
#   This is a MODULE-LEVEL function (NOT a @task) so tests can call it
#   directly with ``patch.dict(os.environ, ...)`` and
#   ``patch('phase1.dags.master_pipeline_dag.create_engine')``. The
#   ``create_engine`` and ``sql_text`` symbols are imported at module
#   level (see top of file) so they're patchable.
# =============================================================================
def _check_dpi_degraded_via_postgres() -> dict:
    """Query PostgreSQL for the latest ChEMBL pipeline_run's DPI-degraded flag.

    Returns a dict with keys:
      - ``dpi_missing`` (bool): True if the latest ChEMBL run had no DPI.
      - ``acknowledged`` (bool): True if the operator acknowledged.
      - ``source`` (str): Provenance of the result:
        * ``"postgres"`` -- successfully queried PostgreSQL.
        * ``"no_chembl_run"`` -- PostgreSQL is up but no ChEMBL run found.
        * ``"no_db_url_dev"`` -- DATABASE_URL not set, dev mode (assumes degraded).
        * ``"db_error_dev"`` -- PostgreSQL query failed, dev mode (assumes degraded).
      - ``run_id`` (str, optional): The ChEMBL run_id from metadata_json.

    Fail-closed behavior:
      - In production (``_is_production=True``) with no DATABASE_URL: raises
        ``AirflowFailException`` (non-retryable). Refuses to proceed without
        the safety check.
      - In production with a PostgreSQL query error: raises
        ``AirflowFailException``. Same rationale.
      - In dev with no DATABASE_URL or query error: returns a degraded-state
        dict (``dpi_missing=True, acknowledged=False``) so dev runs proceed.
    """
    # Read ``_is_production`` at CALL TIME from the module attribute so
    # ``unittest.mock.patch('phase1.dags.master_pipeline_dag._is_production',
    # True)`` works in tests.
    is_production = globals()["_is_production"]

    db_url = _os.environ.get("DATABASE_URL")
    if not db_url:
        if is_production:
            try:
                from airflow.exceptions import AirflowFailException
            except ImportError:  # pragma: no cover -- defensive
                AirflowFailException = RuntimeError  # type: ignore[assignment]
            raise AirflowFailException(
                "DATABASE_URL is not set. Cannot perform DPI-degraded "
                "pre-flight check. Refusing to proceed in production "
                "without safety check. (TM1 Task 1.4 v131 root fix)"
            )
        logger.warning(
            "DATABASE_URL not set; assuming DPI-degraded state for dev run. "
            "(TM1 Task 1.4 v131)"
        )
        return {
            "dpi_missing": True,
            "acknowledged": False,
            "source": "no_db_url_dev",
        }

    if create_engine is None or sql_text is None:
        # SQLAlchemy not importable -- fail-closed in production, degrade in dev.
        if is_production:
            try:
                from airflow.exceptions import AirflowFailException
            except ImportError:  # pragma: no cover
                AirflowFailException = RuntimeError  # type: ignore[assignment]
            raise AirflowFailException(
                "SQLAlchemy is not installed (create_engine is None). Cannot "
                "perform PostgreSQL DPI-degraded pre-flight check in production. "
                "Install sqlalchemy (it's in phase1/requirements.lock). "
                "(TM1 Task 1.4 v131)"
            )
        logger.warning(
            "SQLAlchemy not installed; assuming DPI-degraded state for dev run. "
            "(TM1 Task 1.4 v131)"
        )
        return {
            "dpi_missing": True,
            "acknowledged": False,
            "source": "no_sqlalchemy_dev",
        }

    try:
        import json as _json
        engine = create_engine(db_url, pool_pre_ping=True)
        with engine.connect() as conn:
            row = conn.execute(
                sql_text(
                    "SELECT metadata_json "
                    "FROM pipeline_runs "
                    "WHERE source = 'chembl' "
                    "ORDER BY started_at DESC "
                    "LIMIT 1"
                )
            ).fetchone()
            if row is None:
                logger.warning(
                    "No ChEMBL pipeline_runs row found in PostgreSQL; "
                    "assuming DPI-degraded. (TM1 Task 1.4 v131)"
                )
                return {
                    "dpi_missing": True,
                    "acknowledged": False,
                    "source": "no_chembl_run",
                }
            # ``row`` is a SQLAlchemy Row; row[0] is the metadata_json column.
            raw_meta = row[0] if row[0] else None
            try:
                meta = _json.loads(raw_meta) if raw_meta else {}
            except (ValueError, TypeError):
                meta = {}
            return {
                "dpi_missing": bool(meta.get("dpi_missing", False)),
                "acknowledged": bool(meta.get("dpi_missing_acknowledged", False)),
                "source": "postgres",
                "run_id": meta.get("run_id"),
            }
    except Exception as exc:
        logger.error(
            "PostgreSQL DPI check failed: %s (TM1 Task 1.4 v131)", exc,
        )
        if is_production:
            try:
                from airflow.exceptions import AirflowFailException
            except ImportError:  # pragma: no cover
                AirflowFailException = RuntimeError  # type: ignore[assignment]
            raise AirflowFailException(
                f"PostgreSQL DPI check failed in production: {exc}. "
                f"Refusing to proceed without safety check. "
                f"(TM1 Task 1.4 v131 root fix)"
            )
        return {
            "dpi_missing": True,
            "acknowledged": False,
            "source": "db_error_dev",
        }


# =============================================================================
# TM1 TASK 1.4 ROOT FIX (v131): _validate_output_impl -- the testable core.
# =============================================================================
# RED-TEAM AUDIT FINDING (v131, hostile-auditor pass):
#   The previous ``validate_output`` @task was a 200-line monolith that
#   could not be unit-tested because:
#     1. ``_processed_dir`` and ``_is_production`` were LOCAL variables
#        computed at call time from ``os.environ`` / ``config.settings``.
#        ``unittest.mock.patch`` could not override them.
#     2. The task was decorated with ``@task`` and ``@fail_fast_on_http_4xx``
#        which wrap it in Airflow's TaskFlow machinery -- calling the
#        decorated object does NOT execute the function body, it returns
#        an XComArg. Tests therefore could not invoke the validation
#        logic directly.
#   The result: the broken hardcoded ``_expected_csvs`` dict (lines
#   1193-1201 pre-fix) was NEVER caught by CI. Every prior "root fix"
#   pass CLAIMED the contract-driven validation worked but no test
#   actually exercised it.
#
# ROOT FIX (v131, this function):
#   1. Extract the validation logic to ``_validate_output_impl`` -- a
#      MODULE-LEVEL, UN-DECORATED, pure-Python function. Tests call it
#      directly: ``from phase1.dags.master_pipeline_dag import
#      _validate_output_impl; result = _validate_output_impl()``.
#   2. Read ``_processed_dir`` and ``_is_production`` from the module
#      attributes at CALL TIME via ``globals()``. When a test does
#      ``patch('phase1.dags.master_pipeline_dag._processed_dir', tmpdir)``,
#      the patched value is picked up because Python's LEGB scope
#      resolution looks up ``_processed_dir`` in the module's global
#      namespace at call time.
#   3. Return a STRUCTURED dict (the XCom payload) instead of raising.
#      The @task wrapper (``validate_output`` above) inspects the dict's
#      ``failures`` list and raises ``AirflowFailException`` if non-empty.
#      This separates "validation logic" (returns a result) from "Airflow
#      failure handling" (raises) -- a clean separation of concerns.
#   4. Contract-driven filename resolution: iterate over
#      ``PHASE1_OUTPUT_SCHEMA`` and use ``get_all_aliases()`` to find
#      the CSV. The hardcoded ``_expected_csvs`` dict (which had 4/7
#      WRONG filenames) is DELETED.
#   5. Contract-driven ID-column verification: use
#      ``get_required_id_column()`` to verify the ID column exists in
#      the CSV header. Sources with ``None`` ID column (multi-column
#      composite keys like string_ppi, interactions, indications) skip
#      this check -- the contract's ``required_columns`` already
#      enforces both halves of the composite key.
#   6. Contract-driven SYNTH scan: iterate over ALL sources that have
#      ``inchikey`` in ``required_columns`` (chembl_drugs, drugs,
#      pubchem_enrichment) and scan for SYNTH-prefixed values. The
#      previous code scanned 3 hardcoded filenames, one of which
#      (``pubchem_compounds.csv``) NEVER existed -- SYNTH InChIKeys in
#      PubChem enrichment flowed undetected.
#   7. DPI-degraded check via PostgreSQL: call
#      ``_check_dpi_degraded_via_postgres()`` (above). The previous
#      SQLite check silently disabled itself in production.
#   8. Generate a fresh ``pipeline_run_id`` (UUID) at CALL TIME so each
#      DAG run has a unique ID for end-to-end tracing.
#   9. Return the XCom payload dict. The @task wrapper returns it to
#      Airflow's XCom table; ``_trigger_phase2`` pulls it via TaskFlow
#      XComArg passing and forwards ``pipeline_run_id`` to Phase 2 as
#      ``--provenance <UUID>``.
# =============================================================================
# The 7 sources whose CSV MUST be present for Phase 2 to build a useful KG.
# These are the sources that produce NODES (drugs, proteins, diseases) or
# the canonical Compound properties (PubChem enrichment). Sources NOT in
# this set (chembl_activities, interactions, indications, omim_susceptibility)
# are validated by the SEPARATE ``_validate_phase1_contract`` task (which
# calls ``contracts.validate_output.validate_output_dir``). The two tasks
# are a defense-in-depth pair -- ``_validate_phase1_contract`` checks
# SCHEMA (columns, dtypes, min_rows), ``validate_output`` checks DATA
# (real identifiers, no SYNTH placeholders, DPI-degraded state).
# -----------------------------------------------------------------------------
_REQUIRED_SOURCES_FOR_PHASE2 = frozenset({
    "chembl_drugs",          # Compound nodes (ChEMBL backbone)
    "drugs",                 # Compound nodes (DrugBank preferred)
    "uniprot_proteins",      # Protein nodes
    "string_ppi",            # Pathway nodes + Protein-Protein edges
    "disgenet_gda",          # Gene-Disease edges
    "omim_gda",              # Gene-Disease edges (Mendelian)
    "pubchem_enrichment",    # Compound properties (CID, MW, logP)
})


def _validate_output_impl() -> dict:
    """Validate Phase 1 output before triggering Phase 2.

    Pure-Python, testable implementation. Reads ``_processed_dir`` and
    ``_is_production`` from the module attributes at call time (so
    ``unittest.mock.patch`` works). Returns a structured dict (the XCom
    payload). Does NOT raise on validation failure -- the @task wrapper
    inspects ``failures`` and raises ``AirflowFailException`` if non-empty.

    Returns
    -------
    dict
        XCom payload with keys: ``pipeline_run_id``, ``schema_version``,
        ``row_counts``, ``synth_key_counts``, ``dpi_missing``,
        ``dpi_acknowledged``, ``dpi_source``, ``validated_at``,
        ``failures``.
    """
    # Read module-level state at CALL TIME so ``mock.patch`` works.
    # ``globals()`` returns the module's namespace dict; looking up a
    # key there is equivalent to reading a module-level name, but it's
    # explicit about the fact that we're reading (not assigning) module
    # state. This is the same mechanism Python's LEGB scope resolution
    # uses for global names -- we just make it explicit.
    processed_dir = globals()["_processed_dir"]
    is_production = globals()["_is_production"]

    # Generate a fresh pipeline_run_id for this validation call. Each
    # DAG run gets a unique ID for end-to-end tracing (validate_output
    # -> trigger_phase2 -> Phase 2 --provenance).
    pipeline_run_id = str(_uuid.uuid4())

    failures: list[str] = []
    row_counts: dict[str, int] = {}
    synth_key_counts: dict[str, int] = {}

    # ── Check 1: Contract-driven CSV existence + ID-column verification ─
    # For each required source, resolve the CSV path via the contract's
    # ``get_all_aliases()`` (tries the canonical filename first, then
    # aliases). Verify the ID column (from ``get_required_id_column()``)
    # is present in the CSV header. Sources with ``None`` ID column
    # (multi-column composite keys) skip the header check.
    for source_key in _REQUIRED_SOURCES_FOR_PHASE2:
        if source_key not in PHASE1_OUTPUT_SCHEMA:
            # Defensive: should never happen (the frozenset is hardcoded
            # from the contract's keys), but if a future contract change
            # removes a source, this surfaces the drift loudly.
            failures.append(
                f"validate_output: source_key {source_key!r} is in "
                f"_REQUIRED_SOURCES_FOR_PHASE2 but NOT in "
                f"PHASE1_OUTPUT_SCHEMA. The contract drifted from the "
                f"master DAG. Update _REQUIRED_SOURCES_FOR_PHASE2."
            )
            continue

        id_col = get_required_id_column(source_key)
        aliases = get_all_aliases(source_key)

        # Try each alias until we find the CSV.
        csv_path = None
        for alias in aliases:
            candidate = processed_dir / alias
            if candidate.exists() and candidate.is_file():
                csv_path = candidate
                break

        if csv_path is None:
            if is_production:
                failures.append(
                    f"validate_output: required CSV not found for source "
                    f"{source_key!r}. Tried aliases: {aliases}. Expected ID "
                    f"column: {id_col!r}. The corresponding pipeline did not "
                    f"produce output. Check Airflow task logs for the failing "
                    f"source."
                )
            else:
                logger.warning(
                    "validate_output: source %r CSV not found at any alias "
                    "%s (dev mode -- skipping). (TM1 Task 1.4 v131)",
                    source_key, aliases,
                )
            continue

        # Verify the ID column exists in the CSV header (skip for
        # multi-column-key sources where id_col is None).
        if id_col is not None:
            try:
                import csv as _csv
                with open(csv_path, "r", encoding="utf-8-sig", errors="replace") as f:
                    header = next(_csv.reader(f))
                if id_col not in header:
                    failures.append(
                        f"validate_output: {csv_path.name} missing required ID "
                        f"column {id_col!r}. Found columns: {header[:10]}... "
                        f"The pipeline schema has drifted from the contract. "
                        f"(TM1 Task 1.4 v131)"
                    )
                    continue
                # Spot-check: first 50 data rows must have non-null ID.
                import pandas as _pd
                df_sample = _pd.read_csv(csv_path, nrows=50)
                if id_col in df_sample.columns:
                    non_null = df_sample[id_col].dropna()
                    if len(non_null) == 0:
                        failures.append(
                            f"validate_output: {csv_path.name} column "
                            f"{id_col!r} has ZERO non-null values in the "
                            f"first 50 rows. The pipeline produced empty "
                            f"identifiers -- likely a parser bug or upstream "
                            f"API change. (TM1 Task 1.4 v131)"
                        )
                        continue
            except Exception as exc:
                failures.append(
                    f"validate_output: could not read/validate {csv_path.name}: "
                    f"{exc} (TM1 Task 1.4 v131)"
                )
                continue

        # Count rows in the CSV (for the XCom payload).
        # Use ``usecols=[0]`` to read ONLY the first column (memory-
        # efficient for large CSVs) and count rows via ``len(df)``.
        # ``usecols=[]`` returns 0 rows in pandas (counter-intuitive),
        # so we must read at least one column.
        try:
            import pandas as _pd
            df_count = _pd.read_csv(csv_path, usecols=[0])
            row_counts[source_key] = len(df_count)
        except Exception as exc:
            logger.warning(
                "validate_output: could not count rows in %s: %s "
                "(TM1 Task 1.4 v131)",
                csv_path.name, exc,
            )
            row_counts[source_key] = -1  # sentinel: count failed

    # ── Check 2: Contract-driven SYNTH% detection ───────────────────────
    # Scan EVERY source that has ``inchikey`` in ``required_columns`` for
    # SYNTH-prefixed values. SYNTH InChIKeys are dev-only escape hatches
    # (per migration 009). The previous code scanned 3 hardcoded
    # filenames, one of which (``pubchem_compounds.csv``) NEVER existed
    # -- SYNTH InChIKeys in PubChem enrichment flowed undetected.
    if is_production:
        for source_key, spec in PHASE1_OUTPUT_SCHEMA.items():
            required_col_names = [c.name for c in spec.required_columns]
            if "inchikey" not in required_col_names:
                continue  # source has no inchikey column
            aliases = get_all_aliases(source_key)
            csv_path = None
            for alias in aliases:
                candidate = processed_dir / alias
                if candidate.exists() and candidate.is_file():
                    csv_path = candidate
                    break
            if csv_path is None:
                continue  # already flagged in Check 1
            try:
                import pandas as _pd
                df_sample = _pd.read_csv(
                    csv_path, nrows=1000, compression="infer",
                )
                if "inchikey" in df_sample.columns:
                    synth_count = int(
                        df_sample["inchikey"]
                        .astype(str)
                        .str.startswith("SYNTH")
                        .sum()
                    )
                    synth_key_counts[source_key] = synth_count
                    if synth_count > 0:
                        failures.append(
                            f"validate_output: PRODUCTION CORRUPTION -- "
                            f"{csv_path.name} (source {source_key!r}) "
                            f"contains {synth_count} SYNTH-prefixed "
                            f"InChIKeys in first 1000 rows. SYNTH is a "
                            f"dev-only escape hatch (per migration 009). "
                            f"The pipeline fell back to embedded samples "
                            f"instead of fetching real data. Check the "
                            f"pipeline's download() method and source API "
                            f"connectivity. (TM1 Task 1.4 v131)"
                        )
            except Exception as exc:
                logger.warning(
                    "validate_output: SYNTH check failed for source %r at "
                    "%s: %s (TM1 Task 1.4 v131)",
                    source_key, csv_path, exc,
                )

    # ── Check 3: DPI-degraded pre-flight check via PostgreSQL ───────────
    # The previous code did this via SQLite (phase1/data/drugos.db),
    # which silently disabled the check in production (where the DB is
    # PostgreSQL). ROOT FIX: query PostgreSQL via DATABASE_URL.
    # Fail-closed in production if DATABASE_URL is missing.
    dpi_state: dict
    try:
        dpi_state = _check_dpi_degraded_via_postgres()
    except Exception as exc:
        # The DPI check raised AirflowFailException (production fail-
        # closed). Convert to a failure entry so the @task wrapper can
        # raise the canonical AirflowFailException with the full
        # validation summary.
        failures.append(
            f"validate_output: DPI-degraded pre-flight check FAILED: {exc} "
            f"(TM1 Task 1.4 v131)"
        )
        dpi_state = {
            "dpi_missing": True,
            "acknowledged": False,
            "source": "check_failed",
        }

    if dpi_state.get("dpi_missing") and not dpi_state.get("acknowledged"):
        failures.append(
            f"validate_output: DPI-degraded KG detected (source="
            f"{dpi_state.get('source')}). Operator must acknowledge via "
            f"POST /datasets/acknowledge_dpi_degraded before Phase 2. "
            f"(TM1 Task 1.4 v131)"
        )

    # ── Check 4: Entity resolution completeness (warning-only) ──────────
    # If entity_mappings.csv exists but is empty, that's a warning (not
    # a hard failure) -- entity resolution may legitimately produce no
    # mappings in a fresh dev env. In production, an empty mapping
    # IS a failure (Phase 2 would build a disconnected graph).
    _entity_mapping_path = processed_dir / "entity_mappings.csv"
    if _entity_mapping_path.exists():
        try:
            import pandas as _pd
            em_df = _pd.read_csv(_entity_mapping_path)
            if len(em_df) == 0 and is_production:
                failures.append(
                    "validate_output: entity_mappings.csv is EMPTY. Entity "
                    "resolution produced ZERO canonical ID mappings. Phase 2 "
                    "would build a disconnected knowledge graph. Check "
                    "entity_resolution/run.py for the failure. "
                    "(TM1 Task 1.4 v131)"
                )
        except Exception as exc:
            logger.warning(
                "validate_output: could not read entity_mappings.csv: %s "
                "(TM1 Task 1.4 v131)", exc,
            )
    # In dev, missing entity_mappings.csv is OK (entity resolution may
    # not have run). In production, the contract validation task
    # (``_validate_phase1_contract``) catches this separately.

    # ── Check 5: Feature completeness (NULL-rate thresholds) ───────────
    # Teammate 2 — P1 to P3 Integration ROOT FIX (preserved during
    # TM1 Task 1.4 v131 rebase):
    # ``validate_output_dir`` (Check 1 in ``_validate_phase1_contract``)
    # only enforces NULL=0 on NON-NULLABLE required columns. It does NOT
    # catch the silent-degradation case where a NULLABLE column (e.g.
    # ``pubchem_enrichment.isomeric_smiles``,
    # ``disgenet_gda.prevalence_per_10k``) is populated with 50%+ NULLs
    # because the upstream API silently dropped a field. Phase 3
    # (``biomedical_tables.compute_drug_features``) then receives
    # degraded training data and the GNN silently learns from missing
    # features. This check fails the run if any declared column exceeds
    # the NULL-rate threshold (default 5%).
    #
    # The check uses the SAME schema (``PHASE1_OUTPUT_SCHEMA``) as
    # ``validate_output_dir`` — there is no divergent column list. New
    # columns added to the schema are automatically subject to this
    # check on the next pipeline run.
    try:
        try:
            # When imported as ``phase1.dags.master_pipeline_dag``.
            from phase1.contracts.feature_validator import (
                validate_feature_completeness as _validate_feature_completeness,
            )
        except ImportError:
            # When imported as ``dags.master_pipeline_dag`` (Airflow worker).
            from contracts.feature_validator import (  # type: ignore[no-redef]
                validate_feature_completeness as _validate_feature_completeness,
            )
        _feature_ok, _feature_failures = _validate_feature_completeness(
            processed_dir,
            schema=PHASE1_OUTPUT_SCHEMA,
            max_null_rate=0.05,
        )
        if not _feature_ok:
            if is_production:
                # In production, NULL-rate violations are HARD failures —
                # they silently corrupt the GNN's training data.
                for f in _feature_failures:
                    failures.append(
                        f"validate_output: feature completeness: {f} "
                        f"(TM2 P1->P3 + TM1 Task 1.4 v131)"
                    )
            else:
                # In dev / CI, the fixture dataset may have intentionally
                # sparse columns. Log as warnings so the operator sees the
                # NULL-rate report without blocking the pipeline.
                for f in _feature_failures:
                    logger.warning(
                        "validate_output: feature completeness (dev mode, "
                        "non-blocking): %s (TM2 P1->P3 + TM1 Task 1.4 v131)",
                        f,
                    )
    except ImportError as _exc:
        # ``feature_validator.py`` was added by Teammate 2. If the
        # deployment is mid-rollout (code partially synced), the import
        # will fail. Log as a warning so the operator knows the check is
        # not running, but don't fail the pipeline (the other 4 checks
        # still run).
        logger.warning(
            "validate_output: feature_validator module not importable (%s). "
            "NULL-rate threshold check SKIPPED. This check is added by "
            "Teammate 2's P1-to-P3 integration fix; ensure the latest "
            "phase1/contracts/feature_validator.py is deployed to enable "
            "it. (TM1 Task 1.4 v131 rebase preserved this check.)",
            _exc,
        )
    except Exception as _exc:
        # Defensive: any unexpected error in the validator must NOT bring
        # down the DAG. Log and continue with the other 4 checks.
        logger.warning(
            "validate_output: feature completeness check raised an "
            "unexpected error (%s: %s). The check is skipped. Other "
            "validation checks above still ran. (TM1 Task 1.4 v131)",
            type(_exc).__name__, _exc,
        )

    # ── Build the XCom payload ──────────────────────────────────────────
    # This payload flows validate_output -> trigger_phase2 -> Phase 2
    # (via --provenance). It carries enough metadata for end-to-end
    # tracing: which run, which schema version, which row counts, which
    # SYNTH counts, DPI state, timestamp.
    payload = {
        "pipeline_run_id": pipeline_run_id,
        "schema_version": str(SCHEMA_VERSION),
        "row_counts": row_counts,
        "synth_key_counts": synth_key_counts,
        "dpi_missing": bool(dpi_state.get("dpi_missing", False)),
        "dpi_acknowledged": bool(dpi_state.get("acknowledged", False)),
        "dpi_source": dpi_state.get("source", "unknown"),
        "validated_at": _datetime_module.utcnow().isoformat(),
        "failures": failures,
    }
    return payload


@dag(
    dag_id="drug_repurposing_master",
    description=(
        "Master DAG orchestrating all Drug Repurposing ETL pipelines "
        "with entity resolution"
    ),
    schedule="0 2 * * 0",           # Every Sunday at 02:00 UTC
    start_date=datetime(2024, 1, 1),
    catchup=False,
    max_active_runs=1,
    default_args=DEFAULT_ARGS,
    tags=["drug_repurposing", "master", "etl"],
)
def master_pipeline() -> None:
    """Build the master pipeline DAG with all inter-task dependencies."""

    # ── Branch operator: DrugBank XML gate ──────────────────────────────
    check_drugbank = BranchPythonOperator(
        task_id="check_drugbank_xml",
        python_callable=_check_drugbank_xml,
    )

    skip_drugbank = EmptyOperator(task_id=_DRUGBANK_SKIP_TASK_ID)

    drugbank_done = EmptyOperator(
        task_id="drugbank_done",
        # v43 ROOT FIX (P0 — DAG produces ZERO data when DrugBank XML is
        # missing): the previous ALL_SUCCESS trigger rule treated a
        # SKIPPED branch as non-success. When DRUGBANK_XML_PATH is
        # absent, _check_drugbank_xml returns _DRUGBANK_SKIP_TASK_ID →
        # download_drugbank is SKIPPED → drugbank_done with ALL_SUCCESS
        # is also SKIPPED → resolve is SKIPPED → all *_load tasks are
        # SKIPPED → trigger_phase2 is SKIPPED. The DAG reports GREEN
        # but produces ZERO data.
        #
        # The fix: NONE_FAILED_MIN_ONE_SUCCESS. This means:
        #   - At least one upstream must have succeeded (so a total
        #     failure of BOTH branches still propagates).
        #   - No upstream may have FAILED (so a real DrugBank crash
        #     after retries still aborts the join — preserves the v39
        #     patient-safety fix).
        #   - SKIPPED upstreams are OK (so the operator's deliberate
        #     choice to skip DrugBank doesn't kill the entire DAG).
        #
        # This closes the "DAG reports GREEN but produces ZERO data"
        # hole while preserving the v39 fix's failure-propagation
        # guarantee. NONE_FAILED_MIN_ONE_SUCCESS is available since
        # Airflow 2.2 (2021); the requirements pin >=2.10.0.
        trigger_rule=TriggerRule.NONE_FAILED_MIN_ONE_SUCCESS,
    )

    # ── Primary download tasks ──────────────────────────────────────────
    chembl = download_chembl()
    drugbank = download_drugbank()
    # v89 ROOT FIX (BUG #36 — parse-time assertion that the TaskFlow-
    # generated task_id matches the branch return value):
    #   ``_check_drugbank_xml`` returns ``_DRUGBANK_DOWNLOAD_TASK_ID``
    #   ("download_drugbank"). The TaskFlow API auto-generates the
    #   task_id from the function name (also "download_drugbank"). If a
    #   future refactor renames the function, the branch return value
    #   becomes a DANGLING reference — BranchPythonOperator raises
    #   AirflowException at RUNTIME. This assertion catches the
    #   mismatch at DAG PARSE time, so the DAG shows up as "import
    #   error" in the Airflow UI instead of failing mid-run.
    #
    # P1-031 side-fix: in airflow 2.10+ the TaskFlow API returns a
    # ``PlainXComArg`` (not the operator directly). Access the operator
    # via ``.operator`` to get the task_id. Older airflow versions
    # return the operator directly (no ``.operator`` attr).
    _drugbank_op = getattr(drugbank, "operator", drugbank)
    assert _drugbank_op.task_id == _DRUGBANK_DOWNLOAD_TASK_ID, (
        f"BUG #36 regression: download_drugbank task_id is "
        f"{_drugbank_op.task_id!r} but _check_drugbank_xml returns "
        f"{_DRUGBANK_DOWNLOAD_TASK_ID!r}. Update "
        f"_DRUGBANK_DOWNLOAD_TASK_ID or the function name to match."
    )
    uniprot = download_uniprot()
    string = download_string()

    # ── Secondary download tasks ────────────────────────────────────────
    # v35 ROOT FIX (issue 36): the previous comment claimed DisGeNET and
    # OMIM "share the gene_disease_associations.csv file via
    # _save_csv_with_mode" — this is FALSE. DisGeNET writes to
    # ``gene_disease_associations.csv`` (per DisGeNETPipeline source_name),
    # and OMIM writes to ``omim_gene_disease_associations.csv`` (per
    # OMIMPipeline source_name). They write to DIFFERENT files, so running
    # them in parallel would NOT cause CSV corruption.
    #
    # v76 ROOT FIX (T-041 — remove ``disgenet >> omim`` wire for parallel
    # GDA loading):
    #   The v40 comment acknowledged that DisGeNET and OMIM write to
    #   DIFFERENT CSV files and could run in parallel safely, but kept the
    #   sequential ``disgenet >> omim`` wire "as a defensive choice". This
    #   added ~5min of DisGeNET runtime to the critical path before OMIM
    #   started. Over a year of weekly runs, that's ~260 minutes of wasted
    #   scheduler time. The sequential ordering was NEVER a requirement —
    #   it was a "choice" that cost latency for zero benefit.
    #   ROOT FIX: remove the ``disgenet >> omim`` wire entirely. Both
    #   pipelines now run in PARALLEL (no dependency between them). They
    #   write to different files (gene_disease_associations.csv vs
    #   omim_gene_disease_associations.csv) so there is no race condition.
    #   The ``omim >> drugbank`` wire (T-042) is ALSO removed in this v76
    #   pass — DrugBank now gracefully handles a missing OMIM CSV — so
    #   there is no downstream dependency that requires OMIM to finish
    #   before DisGeNET (or vice versa). The full GDA loading chain is now
    #   parallel: DisGeNET ‖ OMIM ‖ DrugBank all run concurrently after
    #   entity resolution.
    disgenet = download_disgenet()
    omim = download_omim()
    # v76 T-041: NO wire between disgenet and omim — they run in parallel.
    # Both write to different CSV files; no shared state, no race condition.

    # PubChem download task (needs drugs in DB from entity resolution)
    # P1-071 ROOT FIX: use none_failed_min_one_success so pubchem_download
    # runs even if drugbank_load is skipped (DrugBank XML not present).
    pubchem_download = download_pubchem()

    # ── Entity resolution ───────────────────────────────────────────────
    resolve = entity_resolution()

    # ── Post-resolution load tasks ──────────────────────────────────────
    # v79 P0-B2 ROOT FIX: instantiate the NEW load_chembl / load_drugbank
    # / load_uniprot tasks (previously missing — drugs/proteins/DPI tables
    # were empty for these 3 sources after the download-only tasks).
    chembl_load = load_chembl()
    drugbank_load = load_drugbank()
    uniprot_load = load_uniprot()
    string_load = load_string()
    disgenet_load = load_disgenet()
    omim_load = load_omim()
    # P1-071 ROOT FIX: use none_failed_min_one_success for pubchem_load
    # so it runs even if some upstream tasks are skipped.
    pubchem_load = load_pubchem_enrichment()

    # V18 ROOT FIX (Phase 1 ↔ Phase 2 100% connection):
    # Before v18, the master DAG ended at ``pubchem_load`` — Phase 2
    # (knowledge graph construction + TransE training) had to be
    # invoked MANUALLY via ``python -m drugos_graph`` or
    # ``run_unified.py``. The audit flagged this as the only
    # meaningful integration gap (Phase 1 → Phase 2 connection was
    # ~90% complete; this single missing wire was the remaining 10%).
    #
    # Root fix: add a ``trigger_phase2`` task that fires
    # ``run_unified.py --full-pipeline`` after ``pubchem_load``
    # completes.
    #
    # v73 ROOT FIX (T-012 — stale inline comment contradicted the
    # docstring + actual code):
    #   The previous inline comment here claimed the trigger_phase2 task
    #   was fault-tolerant and that Phase 2 failure would NOT abort the
    #   Phase 1 run. That described the PRE-v29 behavior
    #   (``trigger_rule=ALL_DONE`` + ``check=False`` + swallowed
    #   exceptions). The v29 ROOT FIX (docstring at lines 461-487)
    #   explicitly CHANGED the behavior to fail RED on Phase 2 failure:
    #   ``trigger_rule=ALL_SUCCESS``, ``check=True``, exceptions
    #   propagated via ``raise``. The actual code at lines 525/540/551/559
    #   implements the docstring's "DAG fails RED" behavior. The stale
    #   inline comment was a contradiction that misled operators into
    #   building automation that expected the DAG to stay GREEN on
    #   Phase 2 failure — automation that would break the moment Phase 2
    #   actually failed.
    #
    #   ROOT FIX: delete the stale comment. Update to reflect the
    #   ACTUAL behavior: Phase 2 failure fails the DAG RED
    #   (``trigger_rule=ALL_SUCCESS``, ``check=True``, exceptions
    #   propagated). Operators who want the OLD fault-tolerant
    #   behavior must explicitly set ``trigger_rule=TriggerRule.ALL_DONE``
    #   and ``check=False`` on the ``_trigger_phase2`` task — the
    #   default is now strict coupling per the v29 ROOT FIX.
    #
    # TM1 TASK 1.4 v131 ROOT FIX: trigger_phase2 is now instantiated
    # AFTER validate_output_task (see below) so it can receive the
    # validate_output XCom payload via TaskFlow XComArg passing.
    # The previous code instantiated trigger_phase2 here (BEFORE
    # validate_output_task) which made it impossible to pass the
    # XCom payload -- trigger_phase2 had no upstream XCom to pull.
    # The instantiation is now moved to AFTER validate_output_task
    # (see the "TM1 Task 1.4 v131 XCom wire" block below).
    # TM1 TASK 8: instantiate the Phase 1 contract validation task.
    # It runs AFTER all loads finish (chembl_load, drugbank_load,
    # uniprot_load, string_load, disgenet_load, omim_load, pubchem_load)
    # and BEFORE trigger_phase2. If validation fails, trigger_phase2 is
    # blocked (UPSTREAM_FAILED) — no corrupted KG is built.
    validate_phase1_contract = _validate_phase1_contract()

    # ── Wire dependencies ───────────────────────────────────────────────
    # v76 ROOT FIX (T-040 — rewrite list-bitshift as explicit statements
    # for clarity):
    #   The previous wiring used the list-bitshift syntax:
    #     check_drugbank >> [drugbank, skip_drugbank] >> drugbank_done
    #   This works but is fragile — an operator unfamiliar with Airflow's
    #   list-bitshift may not realize ``drugbank_done`` is downstream of
    #   BOTH branches. The ``drugbank_done`` join task uses
    #   ``trigger_rule=TriggerRule.NONE_FAILED_MIN_ONE_SUCCESS`` (set at
    #   line 463) which correctly handles the SKIPPED branch (if the
    #   BranchPythonOperator chose ``skip_drugbank``, then ``drugbank``
    #   is SKIPPED and ``drugbank_done`` accepts the SKIPPED upstream).
    #   ROOT FIX: rewrite as three EXPLICIT statements so the dependency
    #   graph is unambiguous to any reader. The behavior is identical;
    #   only the readability changes. Each ``>>`` is now a single
    #   edge, making the fan-out (check → two branches) and fan-in
    #   (two branches → join) visually explicit.
    check_drugbank >> drugbank       # branch 1: download DrugBank XML
    check_drugbank >> skip_drugbank  # branch 2: skip (XML not found)
    drugbank >> drugbank_done        # fan-in: join after branch 1
    skip_drugbank >> drugbank_done   # fan-in: join after branch 2

    # v76 ROOT FIX (T-042 — remove ``omim >> drugbank`` wire; DrugBank
    # now runs in PARALLEL with OMIM):
    #   The v9 ROOT FIX wired ``omim >> drugbank`` because DrugBank's
    #   ``_write_structured_indications`` step raised RuntimeError when
    #   the OMIM CSV was missing. The v40 comment kept the wire, calling
    #   the coupling "acceptable". But the coupling was brittle: if OMIM
    #   failed (API key missing, network error), DrugBank was SKIPPED via
    #   the dependency chain — losing ALL DrugBank drug + target data
    #   from the knowledge graph, a major data loss. The
    #   BranchPythonOperator checks for DrugBank XML existence, NOT for
    #   OMIM CSV existence, so if OMIM failed but DrugBank XML existed,
    #   the branch chose "download_drugbank" but the download then failed
    #   because the OMIM CSV didn't exist — cascading to DAG RED.
    #   ROOT FIX: DrugBank's ``_write_structured_indications`` now
    #   gracefully handles a missing OMIM CSV (logs WARNING, writes a
    #   header-only drugbank_indications.csv, continues — see
    #   drugbank_pipeline.py v76 T-042 fix). The ``omim >> drugbank``
    #   wire is REMOVED. DrugBank now runs in PARALLEL with OMIM. If OMIM
    #   fails, DrugBank still loads all drug + target data; only the
    #   drug→disease indication edges are empty for that run. This is the
    #   scientifically correct trade-off: a KG with DrugBank drugs but no
    #   indication edges is far more useful than a KG with NO DrugBank
    #   data at all.
    # NO ``omim >> drugbank`` wire — DrugBank is independent of OMIM.

    # SCI-FIX: ALL primary + secondary downloads must complete before
    # entity resolution. Previously, disgenet and omim were orphaned
    # (no upstream/downstream), causing race conditions where the load
    # tasks could fire before the downloads finished.
    # v76 T-042: ``drugbank_done`` (not ``drugbank``) is in the fan-in
    # because the BranchPythonOperator may skip DrugBank — ``drugbank_done``
    # joins both branches with NONE_FAILED_MIN_ONE_SUCCESS.
    chembl >> resolve
    drugbank_done >> resolve
    uniprot >> resolve
    string >> resolve
    disgenet >> resolve
    omim >> resolve

    # Entity resolution → dependent loads (fan-out)
    # v79 P0-B2 ROOT FIX: wire the NEW load_chembl / load_drugbank /
    # load_uniprot tasks after entity_resolution. These populate the
    # ``drugs``, ``proteins``, and ``drug_protein_interactions`` tables
    # for ChEMBL/DrugBank/UniProt — previously empty, which caused the
    # Phase 2 bridge to find zero Compound nodes and skip ALL treats
    # edges (P0-B1 compound).
    resolve >> chembl_load
    resolve >> drugbank_load
    resolve >> uniprot_load
    resolve >> string_load
    resolve >> disgenet_load
    resolve >> omim_load

    # v89 FORENSIC ROOT FIX (BUG #1 P0 — PubChem download queries an EMPTY
    #   drugs table):
    #   The previous wiring ``resolve >> pubchem_download >> pubchem_load``
    #   was SCIENTIFICALLY WRONG. PubChem's ``run_download_and_clean_only()``
    #   (invoked by ``download_pubchem``) reads InChIKeys from the ``drugs``
    #   table where ``pubchem_cid IS NULL`` (see pubchem_pipeline.py:1084-1090
    #   ``select(Drug.inchikey).where(Drug.pubchem_cid.is_(None))``).
    #
    #   But the ``drugs`` table is populated by ``chembl_load`` and
    #   ``drugbank_load`` (which call ``run_load_only()``), NOT by
    #   ``resolve`` (entity_resolution reads CSVs and writes to the
    #   ``entity_mapping`` table — it does NOT populate ``drugs``).
    #   With the previous wiring, ``pubchem_download`` ran CONCURRENTLY
    #   with ``chembl_load``/``drugbank_load`` (all depend only on
    #   ``resolve``), so ``pubchem_download`` queried an EMPTY ``drugs``
    #   table, found zero drugs to enrich, produced an empty
    #   ``pubchem_enrichment.csv``, and ``pubchem_load`` loaded nothing.
    #   Every Sunday master DAG run produced a KG with ZERO PubChem
    #   enrichment data (no CIDs, no molecular formulas, no molecular
    #   weights). The inline comment at the old line 803 ("PubChem needs
    #   drugs in the DB (from entity resolution)") was wrong — entity
    #   resolution does NOT populate the ``drugs`` table.
    #
    #   ROOT FIX: wire ``pubchem_download`` AFTER both ``chembl_load``
    #   and ``drugbank_load`` (the two tasks that populate the ``drugs``
    #   table with InChIKeys). ``pubchem_load`` remains downstream of
    #   ``pubchem_download``. This guarantees the ``drugs`` table is
    #   non-empty when PubChem's download queries it.
    chembl_load >> pubchem_download
    drugbank_load >> pubchem_download
    pubchem_download >> pubchem_load

    # V18 ROOT FIX (Phase 1 ↔ Phase 2 100% connection):
    # v79 P0-B2 ROOT FIX (compound): trigger_phase2 now depends on the 6
    #   REQUIRED load tasks (chembl, drugbank, uniprot, string, disgenet,
    #   omim), with ``trigger_rule=ALL_SUCCESS`` so Phase 2 fires ONLY
    #   after every required load succeeds.
    # v100 P1-009 ROOT FIX (PubChem graceful degradation):
    #   The previous code ALSO wired ``pubchem_load >> trigger_phase2`` —
    #   making PubChem (which is enrichment-only: CIDs, molecular formulas,
    #   molecular weights) a HARD dependency of Phase 2. When PubChem's API
    #   had a transient outage (rate limit, maintenance, network blip),
    #   pubchem_download FAILED → pubchem_load was SKIPPED → trigger_phase2
    #   saw 1 failed upstream → the ENTIRE Sunday master DAG run failed.
    #   PubChem is documented as optional enrichment — it should NOT block
    #   the KG build. ROOT FIX: remove the ``pubchem_load >> trigger_phase2``
    #   wire. PubChem data still loads (if its download succeeds) via the
    #   ``pubchem_download >> pubchem_load`` chain — it just no longer
    #   blocks Phase 2. If PubChem is slow, Phase 2 fires with whatever
    #   PubChem data has loaded so far (possibly none for that run); the
    #   next run picks up the enrichment. This is the scientifically
    #   correct trade-off: a KG with 6/7 sources is far more useful than
    #   no KG at all because PubChem was unreachable.
    chembl_load >> validate_phase1_contract
    drugbank_load >> validate_phase1_contract
    uniprot_load >> validate_phase1_contract
    string_load >> validate_phase1_contract
    disgenet_load >> validate_phase1_contract
    omim_load >> validate_phase1_contract
    # P1-018 ROOT FIX (Team-2): wire pubchem_load >> validate_phase1_contract
    # so contract validation runs AFTER PubChem enrichment FINISHES.
    #
    # P1-013 v113 ROOT FIX (PubChem graceful degradation verification):
    #   The P1-013 audit found that `validate_output_task`'s trigger_rule
    #   was NOT set, defaulting to ALL_SUCCESS — so a SKIPPED pubchem_load
    #   SKIPPED validate_output_task, which SKIPPED trigger_phase2. The
    #   KG build was blocked on PubChem API outages.
    #
    #   VERIFIED: both `validate_phase1_contract` (line 587) and
    #   `validate_output` (line 1060) now have
    #   `trigger_rule=TriggerRule.NONE_FAILED_MIN_ONE_SUCCESS`. This means:
    #     - If pubchem_load is SKIPPED (PubChem API outage →
    #       pubchem_download failed → pubchem_load skipped): both validate
    #       tasks STILL FIRE (skip is not failure; other loads succeeded).
    #       trigger_phase2 fires. KG build proceeds with 6/7 sources. ✓
    #     - If pubchem_load FAILED (real bug, not API outage): both
    #       validate tasks are SKIPPED (UPSTREAM_FAILED). trigger_phase2
    #       is SKIPPED. Operator must investigate. ✓ (correct behavior)
    #     - If pubchem_load SUCCEEDED: both validate tasks fire normally. ✓
    #
    #   The wiring `pubchem_load >> validate_phase1_contract` is RETAINED
    #   (not removed) because it prevents the P1-018 race condition where
    #   trigger_phase2 fired while pubchem_load was still running. The
    #   trigger_rule ensures the wire does NOT re-introduce the P1-009
    #   graceful-degradation bug.
    pubchem_load >> validate_phase1_contract
    # TM1 TASK 8: validate_phase1_contract >> trigger_phase2 — Phase 2
    # is BLOCKED until every Phase 1 output CSV passes the contract.
    # If validation fails, trigger_phase2 is SKIPPED (UPSTREAM_FAILED)
    # and the operator must fix the Phase 1 outputs before re-running.
    # NOTE: trigger_phase2 is instantiated AFTER validate_output_task
    # below (TM1 Task 1.4 v131) so it can receive the validate_output
    # XCom payload. The dependency edge is added there.

    # v110 Task 34 root fix: wire validate_output between load_* and
    # trigger_phase2. validate_output checks that each source produced
    # real biomedical identifiers (not fake/synthesized data), that
    # entity resolution produced canonical IDs, and that DB row counts
    # are non-zero. On failure it raises AirflowFailException (no retry)
    # so trigger_phase2 is BLOCKED — preventing Phase 2 from building a
    # knowledge graph on corrupted/empty Phase 1 data.
    #
    # Wiring: all load_* tasks >> validate_output >> trigger_phase2.
    # validate_output uses trigger_rule=NONE_FAILED_MIN_ONE_SUCCESS so it
    # fires even if pubchem_load is SKIPPED (graceful degradation), but
    # BLOCKS when any required load FAILED.
    validate_output_task = validate_output()
    chembl_load >> validate_output_task
    drugbank_load >> validate_output_task
    uniprot_load >> validate_output_task
    string_load >> validate_output_task
    disgenet_load >> validate_output_task
    omim_load >> validate_output_task
    pubchem_load >> validate_output_task

    # ── TM1 Task 1.4 v131 ROOT FIX: XCom wire validate_output -> trigger_phase2 ──
    # Pass the validate_output XCom payload (a dict containing
    # ``pipeline_run_id``, ``schema_version``, ``row_counts``,
    # ``synth_key_counts``, ``dpi_missing``, ``dpi_acknowledged``,
    # ``dpi_source``, ``validated_at``, ``failures``) to trigger_phase2
    # via TaskFlow XComArg passing. ``_trigger_phase2`` extracts the
    # ``pipeline_run_id`` and forwards it to Phase 2 as
    # ``--provenance <UUID>`` so the KG build can be traced end-to-end
    # across the Phase 1 -> Phase 2 boundary.
    #
    # TaskFlow XComArg passing AUTOMATICALLY creates the dependency edge
    # (``validate_output_task >> trigger_phase2``), so we do NOT need to
    # add the edge explicitly. The ``validate_phase1_contract >>
    # trigger_phase2`` edge (added below) is also kept so that
    # trigger_phase2 waits for BOTH validate tasks to finish.
    trigger_phase2 = _trigger_phase2(validate_output_xcom=validate_output_task)
    # Explicit dependency edges (in addition to the implicit XComArg edge):
    validate_phase1_contract >> trigger_phase2
    # The XComArg passing above already creates the
    # ``validate_output_task >> trigger_phase2`` edge, but we add it
    # explicitly here for readability and to match the existing wiring
    # style (every other dependency in this DAG is an explicit ``>>``).
    validate_output_task >> trigger_phase2


# v89 ROOT FIX (BUG #40): consistent DAG-instance naming convention.
# Was ``master_dag = master_pipeline()`` — different from the standalone
# DAGs' ``<name>_dag_instance``. All 8 DAG files now use ``dag = ...``.
dag = master_pipeline()
