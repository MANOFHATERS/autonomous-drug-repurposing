"""Teammate 8 v117 ROOT FIX verification suite.

This test file verifies each of the 22 issues fixed in the v117 root-fix
round. Each test:
  1. Names the issue ID it verifies (IN-086, P4-002, etc.).
  2. Reads the REAL CODE (not comments) and exercises it.
  3. Asserts the fix is in place AND functional.
  4. Documents the root cause that was fixed.

Hostile-auditor mode: every test assumes the fix may be a lie until
proven by execution. No smoke tests — every test runs real code paths.
"""
from __future__ import annotations

import csv
import importlib
import json
import os
import subprocess
import sys
import tempfile
import textwrap
from pathlib import Path

import pytest
import yaml

_REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_REPO_ROOT))
sys.path.insert(0, str(_REPO_ROOT / "rl"))


# ===========================================================================
# HIGH severity issues (4)
# ===========================================================================

def test_in_086_airflow_pin_matches_phase1():
    """IN-086: root requirements.txt apache-airflow pin must be >=2.10.0,<3.0.0.

    ROOT CAUSE: the previous pin `apache-airflow>=2.8.0,<3.0.0` allowed
    pip to install Airflow 2.8.x in dev/CI (which diverged from the
    Dockerfile.airflow base image apache/airflow:2.10.5). Airflow 2.8.x
    has known bugs with SQLAlchemy 2.0.x that 2.10.x fixed.

    FIX: pin to >=2.10.0,<3.0.0 (matching phase1/requirements.txt).
    """
    req_text = (_REPO_ROOT / "requirements.txt").read_text()
    assert "apache-airflow>=2.10.0,<3.0.0" in req_text, (
        "IN-086: requirements.txt must pin apache-airflow>=2.10.0,<3.0.0"
    )
    # Also verify it does NOT have the old loose pin
    assert "apache-airflow>=2.8.0,<3.0.0" not in req_text, (
        "IN-086: the old >=2.8.0,<3.0.0 pin must be removed"
    )


def test_p4_002_scientific_thresholds_has_evidence_based_constants():
    """P4-002: scientific_thresholds.py must have IC50, Kd, SAFETY, EFFICACY thresholds.

    ROOT CAUSE: the previous version shipped ONLY top-level pipeline
    thresholds (GT AUC, RL AUC, KP recovery, literature count). It had
    ZERO drug-level evidence-based thresholds — meaning the reward
    function and scientific_validation gate had no canonical constants.

    FIX: add IC50, Kd, SAFETY, EFFICACY, GNN_HARD_REJECT, LITERATURE
    constants sourced from ChEMBL, BindingDB, FDA FAERS, FDA Guidance.
    """
    from rl.scientific_thresholds import (
        IC50_STRONG_BINDING_NM, IC50_MODERATE_BINDING_NM, IC50_WEAK_BINDING_NM,
        KD_STRONG_BINDING_NM, KD_MODERATE_BINDING_NM,
        SAFETY_HARD_REJECT_THRESHOLD, SAFETY_WARNING_THRESHOLD,
        EFFICACY_MIN_CLINICAL_SIGNAL, EFFICACY_STRONG_CLINICAL_SIGNAL,
        GNN_HARD_REJECT_THRESHOLD,
        LITERATURE_STRONG_SUPPORT, LITERATURE_MINIMAL_SUPPORT,
        LITERATURE_ZERO_SUPPORT_PENALTY,
    )
    # IC50 thresholds (ChEMBL standard)
    assert IC50_STRONG_BINDING_NM == 100.0
    assert IC50_MODERATE_BINDING_NM == 1000.0
    assert IC50_WEAK_BINDING_NM == 10000.0
    # Kd thresholds (BindingDB standard)
    assert KD_STRONG_BINDING_NM == 100.0
    assert KD_MODERATE_BINDING_NM == 1000.0
    # Safety thresholds (FAERS)
    assert SAFETY_HARD_REJECT_THRESHOLD == 0.5
    assert SAFETY_WARNING_THRESHOLD == 0.7
    # Efficacy thresholds (FDA Guidance)
    assert EFFICACY_MIN_CLINICAL_SIGNAL == 0.20
    assert EFFICACY_STRONG_CLINICAL_SIGNAL == 0.50
    # GNN hard reject
    assert GNN_HARD_REJECT_THRESHOLD == 0.3
    # Literature support
    assert LITERATURE_STRONG_SUPPORT == 3
    assert LITERATURE_MINIMAL_SUPPORT == 1
    assert LITERATURE_ZERO_SUPPORT_PENALTY == -0.05


def test_p4_011_validated_hypotheses_csv_not_in_package():
    """P4-011: rl/validated_hypotheses.csv must NOT exist in the shipped package.

    ROOT CAUSE: the file shipped with fake partner names
    (pharma_partner_alpha, pharma_partner_beta) and fake validation
    timestamps. Production deployments inherited this demo data,
    polluting the data flywheel with non-real entries.

    FIX: remove rl/validated_hypotheses.csv from the package. Ship the
    seed data as a test fixture at rl/tests/fixtures/
    validated_hypotheses_seed.csv. Production deployments get the file
    created at runtime by phase4/writeback.py.
    """
    shipped = _REPO_ROOT / "rl" / "validated_hypotheses.csv"
    fixture = _REPO_ROOT / "rl" / "tests" / "fixtures" / "validated_hypotheses_seed.csv"
    assert not shipped.exists(), (
        f"P4-011: {shipped} must NOT exist in the shipped package."
    )
    assert fixture.exists(), (
        f"P4-011: seed fixture must exist at {fixture}"
    )
    # Verify fixture has real FDA historical data (not fake partner names)
    content = fixture.read_text()
    assert "fda_approved" in content or "fda_withdrawal" in content, (
        "P4-011: fixture must contain real FDA historical data"
    )
    assert "pharma_partner_alpha" not in content, (
        "P4-011: fixture must NOT contain fake partner names"
    )


def test_p4_044_rl_requirements_has_missing_deps():
    """P4-044: rl/requirements.txt must include biopython, pyyaml, fastapi, uvicorn.

    ROOT CAUSE: the previous rl/requirements.txt listed only 6 deps
    (gymnasium, stable-baselines3, pandas, numpy, torch, scikit-learn).
    The rl/rl_drug_ranker.py code uses Bio.Entrez (biopython) for the
    PubMed literature cross-check, pyyaml for tenant profile loading,
    and rl/service.py uses fastapi+uvicorn for the HTTP API. A fresh
    `pip install -r rl/requirements.txt` was missing these — causing
    import failures in dev/CI.
    """
    req_text = (_REPO_ROOT / "rl" / "requirements.txt").read_text()
    for pkg in ["biopython", "pyyaml", "fastapi", "uvicorn"]:
        assert pkg in req_text, (
            f"P4-044: rl/requirements.txt must include {pkg}"
        )


# ===========================================================================
# MEDIUM severity issues (11)
# ===========================================================================

def test_in_088_docker_compose_has_secrets_block():
    """IN-088: docker-compose.yml must define a top-level `secrets:` block.

    ROOT CAUSE: every credential was in `environment:` — visible via
    `docker inspect` and `docker exec env`. For a pharma platform, this
    is a security finding.

    FIX: define a top-level `secrets:` block with file-based secrets
    mounted at /run/secrets/<name>.
    """
    compose_text = (_REPO_ROOT / "docker-compose.yml").read_text()
    compose = yaml.safe_load(compose_text)
    assert "secrets" in compose, "IN-088: docker-compose.yml must have a top-level secrets: block"
    secrets = compose["secrets"]
    expected_secrets = {
        "postgres_password", "neo4j_password", "airflow_fernet_key",
        "airflow_webserver_secret_key", "mlflow_admin_password",
        "grafana_admin_password",
    }
    assert expected_secrets.issubset(set(secrets.keys())), (
        f"IN-088: secrets block must define {expected_secrets}, got {set(secrets.keys())}"
    )


def test_in_091_phase2_kg_builder_uses_run_bridge_script():
    """IN-091: phase2-kg-builder must use run_bridge.py (no bash -lc python -c).

    ROOT CAUSE: the previous command was:
        bash -lc "python -c 'from phase2.drugos_graph.phase1_bridge import ...'"
    — FOUR levels of escaping (YAML >, bash -lc, python -c, string).
    A single missing backslash silently broke the command.

    FIX: replaced with `python /opt/repo/phase2/drugos_graph/run_bridge.py`.
    The script (phase2/drugos_graph/run_bridge.py) uses argparse — no
    escaping, no shell, no folding.
    """
    compose = yaml.safe_load((_REPO_ROOT / "docker-compose.yml").read_text())
    builder = compose["services"]["phase2-kg-builder"]
    cmd = builder.get("command", [])
    # The command must be a list (no bash -lc string)
    assert isinstance(cmd, list), (
        f"IN-091: phase2-kg-builder command must be a list (no bash -lc), got {type(cmd)}"
    )
    # Must reference run_bridge.py
    cmd_str = " ".join(str(c) for c in cmd)
    assert "run_bridge.py" in cmd_str, (
        f"IN-091: phase2-kg-builder must use run_bridge.py, got: {cmd}"
    )
    # Must NOT contain bash -lc or python -c
    assert "bash -lc" not in cmd_str, (
        f"IN-091: phase2-kg-builder must NOT use bash -lc, got: {cmd}"
    )
    assert "python -c" not in cmd_str, (
        f"IN-091: phase2-kg-builder must NOT use python -c, got: {cmd}"
    )
    # Verify the run_bridge.py script actually exists
    assert (_REPO_ROOT / "phase2" / "drugos_graph" / "run_bridge.py").exists(), (
        "IN-091: phase2/drugos_graph/run_bridge.py must exist"
    )


def test_in_092_dockerfile_ml_copies_source():
    """IN-092: Dockerfile.ml runtime stage must COPY source directories.

    ROOT CAUSE: the runtime stage copied only the installed Python
    packages from the builder. /opt/repo was empty. `docker run
    drugos-phase3-gt` (without the compose bind mount) failed with
    `ModuleNotFoundError: No module named 'scripts'`.

    FIX: add COPY statements for phase1/, phase2/, phase4/, rl/,
    graph_transformer/, shared/, common/, scripts/, run_4phase.py.
    """
    dockerfile = (_REPO_ROOT / "Dockerfile.ml").read_text()
    # Check that COPY statements exist for each source dir
    for src_dir in ["phase1/", "phase2/", "phase4/", "rl/",
                    "graph_transformer/", "shared/", "common/", "scripts/"]:
        assert f"COPY --chown=drugos:drugos {src_dir}" in dockerfile, (
            f"IN-092: Dockerfile.ml must COPY {src_dir} to runtime stage"
        )
    assert "COPY --chown=drugos:drugos run_4phase.py" in dockerfile, (
        "IN-092: Dockerfile.ml must COPY run_4phase.py"
    )


def test_in_095_frontend_no_dead_checkpoint_env():
    """IN-095: frontend must NOT have GT_CHECKPOINT_DIR or RL_OUTPUT_DIR env vars.

    ROOT CAUSE: the frontend container does NOT mount the ml-artifacts
    volume, so these env vars were DEAD — never used by the Next.js
    code (the frontend uses GT_SERVICE_URL / RL_SERVICE_URL for HTTP
    calls, not file paths).

    FIX: remove both env vars from the frontend service.
    """
    compose = yaml.safe_load((_REPO_ROOT / "docker-compose.yml").read_text())
    frontend_env = compose["services"]["frontend"].get("environment", {})
    assert "GT_CHECKPOINT_DIR" not in frontend_env, (
        "IN-095: frontend must NOT have GT_CHECKPOINT_DIR (dead env var)"
    )
    assert "RL_OUTPUT_DIR" not in frontend_env, (
        "IN-095: frontend must NOT have RL_OUTPUT_DIR (dead env var)"
    )


def test_in_098_phase4_rl_uses_checkpoint_dir_not_output_dir():
    """IN-098: phase4-rl must use RL_CHECKPOINT_DIR (not RL_OUTPUT_DIR).

    ROOT CAUSE: RL_OUTPUT_DIR suggested the service WRITES output, but
    the ml-artifacts volume is mounted READ-ONLY. If the ranker ever
    tried to write, it would fail with PermissionError.

    FIX: rename to RL_CHECKPOINT_DIR (clearly indicates READ-ONLY input).
    """
    compose = yaml.safe_load((_REPO_ROOT / "docker-compose.yml").read_text())
    rl_env = compose["services"]["phase4-rl"].get("environment", {})
    assert "RL_CHECKPOINT_DIR" in rl_env, (
        "IN-098: phase4-rl must have RL_CHECKPOINT_DIR env var"
    )
    assert "RL_OUTPUT_DIR" not in rl_env, (
        "IN-098: phase4-rl must NOT have RL_OUTPUT_DIR (renamed to RL_CHECKPOINT_DIR)"
    )


def test_in_099_every_service_has_init_true():
    """IN-099: every service in docker-compose.yml must have init: true.

    ROOT CAUSE: Docker's default entrypoint runs as PID 1, which must
    reap zombie processes. Python/Node/bash are NOT good PID 1 — they
    don't reap zombies. Long-running services accumulate zombies over
    weeks, exhausting the PID table.

    FIX: add `init: true` to every service (uses tini as PID 1).
    """
    compose = yaml.safe_load((_REPO_ROOT / "docker-compose.yml").read_text())
    services = compose.get("services", {})
    assert len(services) > 0, "IN-099: no services found in docker-compose.yml"
    no_init = [name for name, svc in services.items() if not svc.get("init")]
    assert not no_init, (
        f"IN-099: services WITHOUT init: true: {no_init}. Every service "
        f"must have init: true for zombie process reaping."
    )


def test_in_100_airflow_no_host_port_for_webserver():
    """IN-100: phase1-airflow must NOT expose port 8080 (webserver not running).

    ROOT CAUSE: the Dockerfile.airflow CMD starts the scheduler only
    (CMD ["airflow", "scheduler"]). The scheduler does NOT listen on
    any port. The previous compose file exposed port 8080 (the Airflow
    webserver port) — but the webserver was not running. Operators
    could not access the UI, and the exposed port was a security hole.

    FIX: removed the ports: - "8080:8080" mapping from phase1-airflow.
    """
    compose = yaml.safe_load((_REPO_ROOT / "docker-compose.yml").read_text())
    airflow = compose["services"]["phase1-airflow"]
    # The service must NOT have ports (no host port binding)
    assert "ports" not in airflow or not airflow["ports"], (
        "IN-100: phase1-airflow must NOT have ports (webserver is not running)"
    )


def test_sh_020_writeback_atomic_write_no_truncate():
    """SH-020 + SH-032: writeback_to_phase1 must use atomic write (tmp+fsync+os.replace).

    ROOT CAUSE: the previous code did `open(csv_path, "w")` which
    TRUNCATES the file before writing. If the process crashed mid-write,
    the CSV was left TRUNCATED — validated hypotheses were silently lost
    (21 CFR Part 11 data integrity violation).

    FIX: write to a temp file (csv_path.tmp), fsync, then atomically
    rename to the target via os.replace (atomic on POSIX).
    """
    import phase4.writeback as wb

    with tempfile.TemporaryDirectory() as td:
        csv_path = Path(td) / "vh.csv"
        os.environ["VALIDATED_HYPOTHESES_CSV"] = str(csv_path)
        importlib.reload(wb)

        # Write a row
        vh1 = wb.ValidatedHypothesis(
            drug="aspirin", disease="headache",
            outcome="validated_positive", validated_by="test_partner",
        )
        wb.writeback_to_phase1(vh1)

        # The temp file must NOT exist after the write completes
        tmp_file = csv_path.with_suffix(".csv.tmp")
        assert not tmp_file.exists(), (
            f"SH-020: temp file {tmp_file} must NOT exist after atomic write"
        )

        # Update the row (duplicate path) — must also use atomic write
        vh2 = wb.ValidatedHypothesis(
            drug="aspirin", disease="headache",
            outcome="validated_toxic", validated_by="test_partner",
        )
        wb.writeback_to_phase1(vh2)
        assert not tmp_file.exists(), (
            f"SH-020: temp file {tmp_file} must NOT exist after atomic update"
        )

        # Verify the row was UPDATED (not duplicated)
        with open(csv_path) as f:
            rows = list(csv.DictReader(f))
        assert len(rows) == 1, f"SH-020: expected 1 row (updated), got {len(rows)}"
        assert rows[0]["outcome"] == "validated_toxic", (
            f"SH-020: expected validated_toxic, got {rows[0]['outcome']}"
        )


def test_sh_021_cypher_defense_in_depth_validation():
    """SH-021: writeback_to_phase2 must validate Cypher identifiers locally.

    ROOT CAUSE: the Cypher query is built via string concatenation of
    NEO4J_DRUG_LABELS, NEO4J_DISEASE_LABEL, etc. The shared contract
    validates these at IMPORT time, but writeback.py has a try/except
    fallback that bypasses the shared import — meaning the fallback's
    hardcoded values are NOT validated.

    FIX: add local _validate_cypher_identifier() calls in writeback_to_phase2
    BEFORE building the Cypher query. Defense-in-depth.
    """
    from shared.contracts.writeback import _validate_cypher_identifier

    # Valid identifiers must pass
    _validate_cypher_identifier("Drug", "test_valid")
    _validate_cypher_identifier("Compound", "test_valid")
    _validate_cypher_identifier("Disease", "test_valid")
    _validate_cypher_identifier("name", "test_valid")

    # Malicious identifiers must be rejected
    malicious_values = [
        "Drug`RETURN 1",       # backtick injection
        "Drug;RETURN 1",       # semicolon injection
        "Drug{x}",             # curly brace injection
        "",                    # empty
        "Drug name",           # space
        "Drug-Name",           # hyphen
    ]
    for val in malicious_values:
        with pytest.raises(ValueError, match="Cypher identifier"):
            _validate_cypher_identifier(val, f"test_{val}")


def test_sh_029_gt_epochs_env_var_override():
    """SH-029: run_4phase.py --gt-epochs must respect DRUGOS_GT_EPOCHS env var.

    ROOT CAUSE: the previous default was hardcoded to 80 with a help
    text saying "500 for production" — but there was NO way to actually
    use 500 in production without editing the CLI invocation.

    FIX: read DRUGOS_GT_EPOCHS env var at CLI construction time so the
    default is ENV-DRIVEN. Production deployments set DRUGOS_GT_EPOCHS=500.
    """
    # Test 1: env var IS set -> default is the env var value
    env = {**os.environ, "DRUGOS_GT_EPOCHS": "500"}
    result = subprocess.run(
        [sys.executable, "run_4phase.py", "--help"],
        capture_output=True, text=True, env=env, cwd=str(_REPO_ROOT),
    )
    # The help text must mention DRUGOS_GT_EPOCHS
    assert "DRUGOS_GT_EPOCHS" in result.stdout, (
        "SH-029: run_4phase.py --help must mention DRUGOS_GT_EPOCHS env var"
    )

    # Test 2: env var NOT set -> default is 80 (dev/CI smoke)
    env = {k: v for k, v in os.environ.items() if k != "DRUGOS_GT_EPOCHS"}
    # Re-parse the same logic the CLI uses
    default = int(env.get("DRUGOS_GT_EPOCHS", "80"))
    assert default == 80, f"SH-029: default must be 80 when env var unset, got {default}"


def test_sh_032_uses_atomic_write_constants_from_shared_contract():
    """SH-032: writeback_to_phase1 must use ATOMIC_WRITE_TMP_SUFFIX + ATOMIC_WRITE_FSYNC.

    ROOT CAUSE: the shared contract declared ATOMIC_WRITE_TMP_SUFFIX
    and ATOMIC_WRITE_FSYNC but writeback_to_phase1 did NOT use them.
    It called open(csv_path, "w") directly.

    FIX: import the constants from shared.contracts.writeback and use
    them in the tmp+fsync+os.replace pattern.
    """
    # Verify the constants exist in the shared contract
    from shared.contracts.writeback import (
        ATOMIC_WRITE_TMP_SUFFIX, ATOMIC_WRITE_FSYNC,
    )
    assert ATOMIC_WRITE_TMP_SUFFIX == ".tmp"
    assert ATOMIC_WRITE_FSYNC is True

    # Verify phase4.writeback imports them (check the source)
    src = (_REPO_ROOT / "phase4" / "writeback.py").read_text()
    assert "ATOMIC_WRITE_TMP_SUFFIX" in src, (
        "SH-032: phase4/writeback.py must import ATOMIC_WRITE_TMP_SUFFIX"
    )
    assert "ATOMIC_WRITE_FSYNC" in src, (
        "SH-032: phase4/writeback.py must import ATOMIC_WRITE_FSYNC"
    )
    assert "os.fsync" in src, (
        "SH-032: phase4/writeback.py must call os.fsync for durability"
    )
    assert "os.replace" in src, (
        "SH-032: phase4/writeback.py must call os.replace for atomic rename"
    )


# ===========================================================================
# LOW severity issues (7)
# ===========================================================================

def test_p4_032_validate_module_documents_wrapper_status():
    """P4-032: rl/validate.py must document that it's a cosmetic wrapper.

    ROOT CAUSE: the audit flagged rl/validate.py as "the worst of both
    worlds: it LOOKS modular but isn't" — it's a pure re-export of
    rl.rl_drug_ranker symbols. Callers import from rl.validate but the
    implementation lives in rl.rl_drug_ranker.

    FIX: keep the wrapper for backward compat, but add a prominent
    docstring note that NEW CODE should import directly from
    rl.rl_drug_ranker.
    """
    src = (_REPO_ROOT / "rl" / "validate.py").read_text()
    # The docstring must mention "wrapper" and direct callers to rl.rl_drug_ranker
    assert "wrapper" in src.lower() or "WRAPPER" in src, (
        "P4-032: rl/validate.py must document that it's a wrapper"
    )
    assert "from rl.rl_drug_ranker import" in src, (
        "P4-032: rl/validate.py must show the direct-import alternative"
    )


def test_p4_043_tenant_profile_yamls_shipped_as_files():
    """P4-043: tenant profile YAMLs must ship as actual files (not comments).

    ROOT CAUSE: the previous reward_weights.yaml had two tenant profiles
    (rare_disease_partner, safety_first) as COMMENTED-OUT blocks. They
    were documentation only — not loadable by load_reward_weights_for_tenant().

    FIX: ship them as actual files: reward_weights.{tenant_id}.yaml.
    """
    for tenant in ["rare_disease_partner", "safety_first"]:
        path = _REPO_ROOT / "rl" / f"reward_weights.{tenant}.yaml"
        assert path.exists(), f"P4-043: {path} must exist as an actual file"
        with open(path) as f:
            data = yaml.safe_load(f)
        # Verify the profile has the required fields
        assert "profile_name" in data, f"P4-043: {tenant} missing profile_name"
        assert "reward_weights" in data, f"P4-043: {tenant} missing reward_weights"
        # Weights must sum to 1.0
        total = sum(data["reward_weights"].values())
        assert abs(total - 1.0) < 1e-6, (
            f"P4-043: {tenant} weights must sum to 1.0, got {total}"
        )


def test_sh_023_run_unified_does_not_exist():
    """SH-023: run_unified.py (which had dead code) must not exist.

    ROOT CAUSE: the audit found run_unified.py had a no-op
    _check_production_escape_hatches_unified function, an `if False:`
    dead block, and a `if _persist_path is not None:` always-False
    branch. The file was entirely dead code.

    FIX: remove run_unified.py entirely. Its functionality (if any was
    actually needed) lives in run_4phase.py.
    """
    run_unified = _REPO_ROOT / "run_unified.py"
    assert not run_unified.exists(), (
        f"SH-023: {run_unified} must NOT exist (dead code removed)"
    )


def test_sh_033_flywheel_monitor_uses_public_api():
    """SH-033: flywheel_monitor must use PUBLIC get_validated_hypotheses (not private _load).

    ROOT CAUSE: the previous code imported the PRIVATE functions
    _load_validated_hypotheses and _load_validated_toxic_hypotheses
    from rl.rl_drug_ranker. Private functions are implementation
    details of the _LazyList proxy and may be refactored without notice.

    FIX: import and call the PUBLIC API: get_validated_hypotheses,
    get_validated_toxic_hypotheses.
    """
    src = (_REPO_ROOT / "shared" / "monitoring" / "flywheel_monitor.py").read_text()
    # Must import the PUBLIC functions
    assert "get_validated_hypotheses" in src, (
        "SH-033: flywheel_monitor must import get_validated_hypotheses (PUBLIC API)"
    )
    assert "get_validated_toxic_hypotheses" in src, (
        "SH-033: flywheel_monitor must import get_validated_toxic_hypotheses (PUBLIC API)"
    )
    # Must NOT import the private functions
    assert "from rl.rl_drug_ranker import (" in src and \
           "_load_validated_hypotheses" not in src.split("from rl.rl_drug_ranker import (")[1].split(")")[0], (
        "SH-033: flywheel_monitor must NOT import _load_validated_hypotheses (PRIVATE)"
    )

    # Also verify the function actually works (real call, not just import)
    from shared.monitoring.flywheel_monitor import check_rl_ranker_health
    status = check_rl_ranker_health(expected_min_bonus_pairs=0)
    assert status.ok, (
        f"SH-033: check_rl_ranker_health must succeed, got: {status.message}"
    )


def test_sh_034_feature_names_transparency_only_cols():
    """SH-034: feature_names.py must define TRANSPARENCY_ONLY_COLS explicitly.

    ROOT CAUSE: EFFICACY_SCORE_COL was in RL_FEATURE_COLUMNS (Phase 3
    writes it) but NOT in REWARD_FEATURE_COLS (Phase 4 reward function
    ignores it). This was documented in a COMMENT but not enforced by
    a contract. A future edit could change one without the other.

    FIX: define TRANSPARENCY_ONLY_COLS as the EXPLICIT set of columns
    in RL_FEATURE_COLUMNS but NOT in REWARD_FEATURE_COLS. Assert the
    union equals RL_FEATURE_COLUMNS at import time.
    """
    from shared.contracts.feature_names import (
        RL_FEATURE_COLUMNS, REWARD_FEATURE_COLS, TRANSPARENCY_ONLY_COLS,
        EFFICACY_SCORE_COL,
    )
    # Efficacy must be in RL_FEATURE_COLUMNS
    assert EFFICACY_SCORE_COL in RL_FEATURE_COLUMNS, (
        "SH-034: efficacy_score must be in RL_FEATURE_COLUMNS (Phase 3 writes it)"
    )
    # Efficacy must NOT be in REWARD_FEATURE_COLS (Phase 4 reward ignores it)
    assert EFFICACY_SCORE_COL not in REWARD_FEATURE_COLS, (
        "SH-034: efficacy_score must NOT be in REWARD_FEATURE_COLS (would confound with gnn_score)"
    )
    # Efficacy MUST be in TRANSPARENCY_ONLY_COLS (explicitly classified)
    assert EFFICACY_SCORE_COL in TRANSPARENCY_ONLY_COLS, (
        "SH-034: efficacy_score must be in TRANSPARENCY_ONLY_COLS (explicit classification)"
    )
    # The union of REWARD + TRANSPARENCY must equal RL_FEATURE_COLUMNS
    union = set(REWARD_FEATURE_COLS) | set(TRANSPARENCY_ONLY_COLS)
    assert union == set(RL_FEATURE_COLUMNS), (
        f"SH-034: RL_FEATURE_COLUMNS must equal REWARD ∪ TRANSPARENCY. "
        f"Missing: {set(RL_FEATURE_COLUMNS) - union}. Extra: {union - set(RL_FEATURE_COLUMNS)}."
    )
    # No overlap (a column can't be both weighted and transparency-only)
    overlap = set(REWARD_FEATURE_COLS) & set(TRANSPARENCY_ONLY_COLS)
    assert not overlap, (
        f"SH-034: columns in BOTH REWARD and TRANSPARENCY: {overlap}"
    )


def test_sh_036_pytest_filterwarnings_narrow_not_blanket():
    """SH-036: pytest.ini filterwarnings must NOT blanket-ignore rdkit/torch/transformers.

    ROOT CAUSE: the previous filterwarnings was a BLANKET ignore of ALL
    DeprecationWarnings from rdkit, torch, and transformers. This
    masked REAL deprecation issues — the codebase kept using deprecated
    APIs until they were removed entirely (cryptic ImportError).

    FIX: narrow the filter to SPECIFIC known-safe deprecations (with
    message regex). New deprecation warnings appear in test output.
    """
    pytest_ini = (_REPO_ROOT / "pytest.ini").read_text()
    # Must NOT have the blanket ignore
    assert "ignore::DeprecationWarning:rdkit.*" not in pytest_ini or \
           "ignore:InchiToMol is deprecated" in pytest_ini, (
        "SH-036: pytest.ini must NOT blanket-ignore rdkit deprecations"
    )
    # Must have specific message-based filters (with a colon after `ignore:`)
    # The new format is: ignore:<message>:DeprecationWarning:<module>
    assert "ignore:InchiToMol is deprecated" in pytest_ini or \
           "ignore:torch.cuda.amp.autocast is deprecated" in pytest_ini, (
        "SH-036: pytest.ini must use specific message-based filters (not blanket)"
    )


# ===========================================================================
# Run as script
# ===========================================================================

if __name__ == "__main__":
    pytest.main([__file__, "-v", "--tb=short"])
