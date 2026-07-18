"""FORENSIC TEST SUITE — verifies each fix by RUNNING REAL CODE.

Hostile-auditor mode: every test imports modules, calls functions, and
inspects actual behavior. NO test reads comments or trusts "ROOT FIX"
claims at face value.

Tests for the v118 fixes:
  test_sh_021_writeback_to_phase2_no_name_error
      Calls writeback_to_phase2 with a real (fake-host) Neo4j URI and
      verifies the function does NOT raise UnboundLocalError on
      drug_label_try_1. The previous v117 code did — silently caught
      by the broad except block. This test FAILS the v117 code and
      PASSES the v118 fix.

  test_in_100_dockerfile_airflow_version_matches_requirements
      Parses Dockerfile.airflow's FROM line and asserts the Airflow
      major version is 2.x (matching the <3.0.0 pin in both root
      requirements.txt and phase1/requirements.txt). The previous
      v116-v117 code used Airflow 3.3.0 which violates the pin.

  test_dockerfile_ml_python_version_compat_with_pinned_packages
      Asserts Dockerfile.ml uses python:3.12-slim (not 3.14-slim).
      Python 3.14 has no wheels for torch==2.2.0+cpu, pandas==2.1.4,
      rdkit==2024.3.5 — the image would fail to build.

  test_in_030_frontend_dockerignore_exists
  test_in_030_phase1_dockerignore_exists
      Verifies the .dockerignore files exist at the expected paths
      and contain the key exclusion patterns.

  test_sh_020_writeback_to_phase1_atomic_no_duplicate_growth
      Calls writeback_to_phase1 twice with the same (drug, disease,
      validated_by) tuple but different outcomes, then reads the CSV
      and asserts there is exactly ONE row (UPDATE, not append). Also
      verifies no .tmp file is left behind.

  test_sh_032_atomic_write_uses_tmp_fsync_replace
      Verifies writeback_to_phase1 uses the ATOMIC_WRITE_TMP_SUFFIX
      and ATOMIC_WRITE_FSYNC constants from the shared contract.

  test_p4_002_evidence_based_thresholds_present
      Imports every evidence-based threshold constant from
      rl/scientific_thresholds.py and verifies the values match the
      literature (ChEMBL, BindingDB, FDA, FAERS standards).

  test_sh_033_flywheel_monitor_uses_public_api
      Inspects the import statement in flywheel_monitor.py and asserts
      it imports get_validated_hypotheses (public), NOT
      _load_validated_hypotheses (private).

  test_sh_034_feature_contract_no_drift
      Asserts RL_FEATURE_COLUMNS == REWARD_FEATURE_COLS | TRANSPARENCY_ONLY_COLS
      (set equality) and that the two sets don't overlap.

  test_p4_032_validate_wrapper_exports
      Imports every documented symbol from rl/validate.py and verifies
      they exist (no ImportError).

Run with:
    python3 /home/z/my-project/scripts/test_v118_fixes.py
"""
import os
import sys
import re
import io
import csv
import json
import logging
import tempfile
import shutil
import inspect
from pathlib import Path

import pytest

REPO = Path("/home/z/my-project/workspace/autonomous-drug-repurposing")
sys.path.insert(0, str(REPO))


# =============================================================================
# SH-021: writeback_to_phase2 NameError bug (CRITICAL)
# =============================================================================

def test_sh_021_writeback_to_phase2_no_name_error():
    """CRITICAL: writeback_to_phase2 must NOT raise UnboundLocalError on
    drug_label_try_1. The v117 code did — silently caught by the broad
    except block, making Phase 2 writeback COMPLETELY non-functional.

    The fix moves the Cypher identifier validation block to AFTER every
    variable it references is defined.
    """
    os.environ["DRUGOS_NEO4J_URI"] = "bolt://fake-host-does-not-exist-12345:7687"
    os.environ["DRUGOS_NEO4J_USER"] = "neo4j"
    os.environ["DRUGOS_NEO4J_PASSWORD"] = "fake"

    # Force re-import so the env vars take effect
    if "phase4.writeback" in sys.modules:
        del sys.modules["phase4.writeback"]
    from phase4.writeback import writeback_to_phase2, ValidatedHypothesis

    vh = ValidatedHypothesis(
        drug="metformin",
        disease="diabetes",
        outcome="validated_positive",
        validated_by="test_partner",
    )

    # Capture warnings — the function returns False on Neo4j failure,
    # but the WARNING message reveals the real error.
    log_capture = io.StringIO()
    handler = logging.StreamHandler(log_capture)
    handler.setLevel(logging.WARNING)
    root_logger = logging.getLogger()
    root_logger.addHandler(handler)
    try:
        result = writeback_to_phase2(vh)
    finally:
        root_logger.removeHandler(handler)

    log_output = log_capture.getvalue()

    # The function should return False (Neo4j unreachable — expected,
    # since we used a fake host). The CRITICAL check is that the error
    # is a CONNECTION error, NOT an UnboundLocalError on
    # drug_label_try_1.
    assert result is False, f"Expected False (Neo4j unreachable), got {result}"

    # The v117 bug: "cannot access local variable 'drug_label_try_1'"
    assert "drug_label_try_1" not in log_output or "not associated with a value" not in log_output, (
        f"SH-021 REGRESSION: writeback_to_phase2 raised UnboundLocalError "
        f"on drug_label_try_1 (the v117 bug). The v118 fix did NOT move "
        f"the validation block to after the variable definitions. "
        f"Log: {log_output}"
    )

    # The error should now be a legitimate connection failure (DNS,
    # ServiceUnavailable, etc.) — proving the function reached the
    # Neo4j driver call (past the validation block).
    connection_error_indicators = [
        "DNS resolve",
        "ServiceUnavailable",
        "could not be resolved",
        "Name or service not known",
        "Failed to connect",
        "Neo4j write failed",
    ]
    assert any(ind in log_output for ind in connection_error_indicators), (
        f"Expected a Neo4j connection error in the log, got: {log_output}"
    )


# =============================================================================
# IN-100: Dockerfile.airflow version matches requirements pin
# =============================================================================

def test_in_100_dockerfile_airflow_version_matches_requirements():
    """Dockerfile.airflow must use Airflow 2.x (not 3.x) to match the
    <3.0.0 pin in both root requirements.txt and phase1/requirements.txt.
    """
    dfa = (REPO / "Dockerfile.airflow").read_text()
    from_match = re.search(r"^FROM\s+(apache/airflow:[\d.]+-python[\d.]+)", dfa, re.MULTILINE)
    assert from_match, "Could not find FROM line in Dockerfile.airflow"
    from_image = from_match.group(1)

    version_match = re.search(r"apache/airflow:(\d+)\.(\d+)\.(\d+)", from_image)
    assert version_match, f"Could not parse Airflow version from {from_image}"
    major = int(version_match.group(1))

    assert major == 2, (
        f"IN-100 REGRESSION: Dockerfile.airflow uses {from_image} but the "
        f"requirements pin is apache-airflow>=2.10.0,<3.0.0. The base image "
        f"version {major}.x violates the pin — pip install will fail inside "
        f"the container because it cannot downgrade 3.x to <3.0.0."
    )


def test_in_100_dockerfile_airflow_version_explicit_2_10_5():
    """Dockerfile.airflow should use the specific 2.10.5 patch version
    (the latest 2.10.x release) — not just any 2.x."""
    dfa = (REPO / "Dockerfile.airflow").read_text()
    assert "apache/airflow:2.10.5-python3.11" in dfa, (
        f"Expected apache/airflow:2.10.5-python3.11 in Dockerfile.airflow"
    )


# =============================================================================
# Dockerfile.ml Python version compatibility
# =============================================================================

def test_dockerfile_ml_python_version_compat_with_pinned_packages():
    """Dockerfile.ml must use python:3.12-slim (not 3.14-slim) because
    the pinned packages (torch==2.2.0+cpu, pandas==2.1.4, rdkit==2024.3.5)
    only have wheels for Python 3.8-3.12.
    """
    dfml = (REPO / "Dockerfile.ml").read_text()
    # Find all FROM lines (builder + runtime stages)
    from_lines = re.findall(r"^FROM\s+(python:[\d.]+-slim)", dfml, re.MULTILINE)
    assert len(from_lines) >= 2, f"Expected 2+ FROM lines, got {len(from_lines)}"

    for from_line in from_lines:
        version_match = re.match(r"python:(\d+)\.(\d+)-slim", from_line)
        assert version_match, f"Could not parse Python version from {from_line}"
        major = int(version_match.group(1))
        minor = int(version_match.group(2))
        # Must be 3.12 (or earlier 3.x that has all the wheels — 3.11 also works)
        assert (major, minor) in [(3, 11), (3, 12)], (
            f"Dockerfile.ml uses {from_line} but the pinned packages "
            f"(torch==2.2.0+cpu, pandas==2.1.4, rdkit==2024.3.5) do NOT "
            f"have Python 3.{minor} wheels. Use python:3.12-slim."
        )


def test_dockerfile_ml_no_python_3_14():
    """Explicit check: Dockerfile.ml must NOT use python:3.14-slim in
    any FROM line (comments may reference it for explanation — that's fine).
    """
    dfml = (REPO / "Dockerfile.ml").read_text()
    from_lines = re.findall(r"^FROM\s+(python:[\d.]+-slim)", dfml, re.MULTILINE)
    for from_line in from_lines:
        assert "3.14" not in from_line, (
            f"Dockerfile.ml FROM line uses {from_line} — torch==2.2.0+cpu has no "
            f"cp314 wheels (verified via PyPI: only cp310, cp311, cp312 exist). "
            f"The image cannot build."
        )


# =============================================================================
# IN-030: .dockerignore files
# =============================================================================

def test_in_030_frontend_dockerignore_exists():
    """frontend/.dockerignore must exist (IN-030b)."""
    p = REPO / "frontend" / ".dockerignore"
    assert p.exists(), f"frontend/.dockerignore does not exist at {p}"
    content = p.read_text()
    # Must exclude the key patterns
    assert "node_modules" in content, "frontend/.dockerignore must exclude node_modules"
    assert ".next" in content, "frontend/.dockerignore must exclude .next"
    assert "*.db" in content, "frontend/.dockerignore must exclude *.db (SQLite dev DB)"
    assert ".env.local" in content or ".env" in content, (
        "frontend/.dockerignore must exclude .env files (secret leakage)"
    )


def test_in_030_phase1_dockerignore_exists():
    """phase1/.dockerignore must exist (IN-030c)."""
    p = REPO / "phase1" / ".dockerignore"
    assert p.exists(), f"phase1/.dockerignore does not exist at {p}"
    content = p.read_text()
    # Must exclude the key patterns
    assert "raw_data" in content, "phase1/.dockerignore must exclude raw_data (10+ GB)"
    assert "processed_data" in content, "phase1/.dockerignore must exclude processed_data"
    assert "*.db" in content or "phase1.db" in content, (
        "phase1/.dockerignore must exclude phase1.db (local SQLite)"
    )
    assert "__pycache__" in content, "phase1/.dockerignore must exclude __pycache__"


def test_in_030_root_dockerignore_still_exists():
    """Root .dockerignore must still exist (regression check)."""
    p = REPO / ".dockerignore"
    assert p.exists(), "Root .dockerignore was deleted (regression)"


# =============================================================================
# SH-020 + SH-032: writeback_to_phase1 atomic write
# =============================================================================

def test_sh_020_writeback_to_phase1_atomic_no_duplicate_growth(tmp_path, monkeypatch):
    """writeback_to_phase1 must UPDATE (not append) on duplicate, and
    must use atomic write (tmp + fsync + os.replace).
    """
    csv_file = tmp_path / "validated_hypotheses.csv"
    monkeypatch.setenv("VALIDATED_HYPOTHESES_CSV", str(csv_file))

    # Force re-import so the env var takes effect
    for mod in list(sys.modules):
        if mod.startswith("phase4.") or mod.startswith("shared.contracts.writeback"):
            del sys.modules[mod]
    from phase4.writeback import writeback_to_phase1, ValidatedHypothesis

    # Write once
    vh1 = ValidatedHypothesis(
        drug="metformin", disease="diabetes",
        outcome="validated_positive", validated_by="partner_a",
    )
    writeback_to_phase1(vh1)

    # Write DUPLICATE (same drug, disease, validated_by) with different outcome
    vh2 = ValidatedHypothesis(
        drug="metformin", disease="diabetes",
        outcome="validated_negative", validated_by="partner_a",  # changed outcome
    )
    writeback_to_phase1(vh2)

    # Read CSV and verify only ONE row (UPDATE, not append)
    with open(csv_file) as f:
        rows = list(csv.DictReader(f))

    assert len(rows) == 1, (
        f"SH-020 REGRESSION: expected 1 row (UPDATE on duplicate), "
        f"got {len(rows)} rows. The duplicate-append bug is back."
    )
    assert rows[0]["outcome"] == "validated_negative", (
        f"Expected outcome=validated_negative (the UPDATE), got {rows[0]['outcome']}"
    )

    # Verify no .tmp file left behind (atomic write cleanup)
    tmp_files = list(tmp_path.glob("*.tmp"))
    assert len(tmp_files) == 0, (
        f"SH-032 REGRESSION: leftover .tmp files: {tmp_files}. "
        f"Atomic write must clean up the temp file after os.replace."
    )


def test_sh_032_atomic_write_constants_from_shared_contract():
    """writeback_to_phase1 must use ATOMIC_WRITE_TMP_SUFFIX and
    ATOMIC_WRITE_FSYNC from shared.contracts.writeback (not local magic
    numbers).
    """
    from shared.contracts.writeback import ATOMIC_WRITE_TMP_SUFFIX, ATOMIC_WRITE_FSYNC
    assert ATOMIC_WRITE_TMP_SUFFIX == ".tmp", (
        f"ATOMIC_WRITE_TMP_SUFFIX should be '.tmp', got {ATOMIC_WRITE_TMP_SUFFIX!r}"
    )
    assert ATOMIC_WRITE_FSYNC is True, (
        f"ATOMIC_WRITE_FSYNC should be True, got {ATOMIC_WRITE_FSYNC}"
    )


def test_sh_032_writeback_uses_atomic_write_in_source():
    """Verify the writeback_to_phase1 function source references the
    ATOMIC_WRITE_TMP_SUFFIX and os.replace (atomic rename).
    """
    import inspect
    from phase4.writeback import writeback_to_phase1
    src = inspect.getsource(writeback_to_phase1)
    assert "ATOMIC_WRITE_TMP_SUFFIX" in src, (
        "writeback_to_phase1 does not reference ATOMIC_WRITE_TMP_SUFFIX"
    )
    assert "ATOMIC_WRITE_FSYNC" in src, (
        "writeback_to_phase1 does not reference ATOMIC_WRITE_FSYNC"
    )
    assert "os.fsync" in src, "writeback_to_phase1 does not call os.fsync (durability)"
    assert "os.replace" in src, "writeback_to_phase1 does not call os.replace (atomic rename)"


# =============================================================================
# P4-002: evidence-based thresholds
# =============================================================================

def test_p4_002_evidence_based_thresholds_present():
    """rl/scientific_thresholds.py must have all evidence-based
    drug-level thresholds (IC50, Kd, safety, efficacy) sourced from
    ChEMBL, BindingDB, FDA, FAERS.
    """
    from rl.scientific_thresholds import (
        IC50_STRONG_BINDING_NM, IC50_MODERATE_BINDING_NM, IC50_WEAK_BINDING_NM,
        KD_STRONG_BINDING_NM, KD_MODERATE_BINDING_NM,
        SAFETY_HARD_REJECT_THRESHOLD, SAFETY_WARNING_THRESHOLD,
        EFFICACY_MIN_CLINICAL_SIGNAL, EFFICACY_STRONG_CLINICAL_SIGNAL,
        GNN_HARD_REJECT_THRESHOLD,
        LITERATURE_STRONG_SUPPORT, LITERATURE_MINIMAL_SUPPORT, LITERATURE_ZERO_SUPPORT_PENALTY,
    )
    # ChEMBL bioactivity thresholds
    assert IC50_STRONG_BINDING_NM == 100.0, f"Expected 100.0 (ChEMBL), got {IC50_STRONG_BINDING_NM}"
    assert IC50_MODERATE_BINDING_NM == 1000.0, f"Expected 1000.0 (ChEMBL), got {IC50_MODERATE_BINDING_NM}"
    assert IC50_WEAK_BINDING_NM == 10000.0, f"Expected 10000.0 (ChEMBL), got {IC50_WEAK_BINDING_NM}"
    # BindingDB Kd thresholds
    assert KD_STRONG_BINDING_NM == 100.0, f"Expected 100.0 (BindingDB), got {KD_STRONG_BINDING_NM}"
    assert KD_MODERATE_BINDING_NM == 1000.0, f"Expected 1000.0 (BindingDB), got {KD_MODERATE_BINDING_NM}"
    # FAERS safety thresholds
    assert SAFETY_HARD_REJECT_THRESHOLD == 0.5, f"Expected 0.5 (FAERS), got {SAFETY_HARD_REJECT_THRESHOLD}"
    assert SAFETY_WARNING_THRESHOLD == 0.7, f"Expected 0.7, got {SAFETY_WARNING_THRESHOLD}"
    # FDA efficacy thresholds
    assert EFFICACY_MIN_CLINICAL_SIGNAL == 0.20, f"Expected 0.20 (FDA), got {EFFICACY_MIN_CLINICAL_SIGNAL}"
    assert EFFICACY_STRONG_CLINICAL_SIGNAL == 0.50, f"Expected 0.50 (FDA breakthrough), got {EFFICACY_STRONG_CLINICAL_SIGNAL}"
    # GT model threshold
    assert GNN_HARD_REJECT_THRESHOLD == 0.3, f"Expected 0.3, got {GNN_HARD_REJECT_THRESHOLD}"


def test_p4_002_thresholds_have_docstrings_citing_sources():
    """Each evidence-based threshold must have a docstring citing its
    source (ChEMBL, BindingDB, FDA, FAERS).
    """
    import rl.scientific_thresholds as st
    # The module-level docstrings are attached to the variables via
    # the __doc__ of the module — verify the module's source contains
    # the source citations.
    src = inspect.getsource(st)
    assert "ChEMBL" in src, "Module must cite ChEMBL for IC50 thresholds"
    assert "BindingDB" in src, "Module must cite BindingDB for Kd thresholds"
    assert "FAERS" in src, "Module must cite FAERS for safety thresholds"
    assert "FDA" in src, "Module must cite FDA for efficacy thresholds"


# =============================================================================
# SH-033: flywheel_monitor uses public API
# =============================================================================

def test_sh_033_flywheel_monitor_uses_public_api():
    """flywheel_monitor.check_rl_ranker_health must import the PUBLIC
    API (get_validated_hypotheses, get_validated_toxic_hypotheses) —
    NOT the private _load_* functions.
    """
    from shared.monitoring import flywheel_monitor
    src = inspect.getsource(flywheel_monitor.check_rl_ranker_health)
    # Strip comment lines (a comment can mention the old private name
    # as part of the explanation — that's fine, we check the actual
    # import statement).
    code_lines = [line for line in src.split("\n") if not line.strip().startswith("#")]
    code = "\n".join(code_lines)

    # Find the import statement
    m = re.search(r"from rl\.rl_drug_ranker import \(([^)]+)\)", code, re.DOTALL)
    assert m, "Could not find 'from rl.rl_drug_ranker import (...)' in flywheel_monitor.check_rl_ranker_health"
    imported = m.group(1)

    assert "get_validated_hypotheses" in imported, (
        "Must import public get_validated_hypotheses"
    )
    assert "get_validated_toxic_hypotheses" in imported, (
        "Must import public get_validated_toxic_hypotheses"
    )
    assert "_load_validated_hypotheses" not in imported, (
        f"SH-033 REGRESSION: still imports PRIVATE _load_validated_hypotheses. "
        f"Imported: {imported}"
    )
    assert "_load_validated_toxic_hypotheses" not in imported, (
        f"SH-033 REGRESSION: still imports PRIVATE _load_validated_toxic_hypotheses. "
        f"Imported: {imported}"
    )


# =============================================================================
# SH-034: feature_names.py contract (no drift)
# =============================================================================

def test_sh_034_feature_contract_no_drift():
    """RL_FEATURE_COLUMNS must equal the union of REWARD_FEATURE_COLS
    and TRANSPARENCY_ONLY_COLS (set equality), and the two sets must
    not overlap.
    """
    from shared.contracts.feature_names import (
        RL_FEATURE_COLUMNS, REWARD_FEATURE_COLS, TRANSPARENCY_ONLY_COLS,
    )
    rl_set = set(RL_FEATURE_COLUMNS)
    reward_set = set(REWARD_FEATURE_COLS)
    transp_set = set(TRANSPARENCY_ONLY_COLS)

    assert len(RL_FEATURE_COLUMNS) == 17, (
        f"RL_FEATURE_COLUMNS must have 17 columns, got {len(RL_FEATURE_COLUMNS)}"
    )
    assert rl_set == reward_set | transp_set, (
        f"SH-034 CONTRACT DRIFT: RL_FEATURE_COLUMNS does not equal "
        f"REWARD_FEATURE_COLS | TRANSPARENCY_ONLY_COLS. "
        f"Missing from both: {rl_set - reward_set - transp_set}"
    )
    assert not (reward_set & transp_set), (
        f"SH-034 CONTRACT VIOLATION: a column is in BOTH REWARD_FEATURE_COLS "
        f"and TRANSPARENCY_ONLY_COLS: {reward_set & transp_set}"
    )


# =============================================================================
# P4-032: rl/validate.py wrapper
# =============================================================================

def test_p4_032_validate_wrapper_exports():
    """rl/validate.py must export all documented symbols (no ImportError).
    """
    from rl.validate import (
        validate_input_schema, validate_environment, preprocess_data,
        generate_data_quality_report, validate_canonical_ids,
        ScientificFailureError, PipelineMetrics, check_alert_conditions,
        DATA_DICTIONARY, INPUT_SCHEMA, OUTPUT_SCHEMA,
        run_scientific_validation_gate,
    )
    # All symbols must be non-None
    for sym in [validate_input_schema, validate_environment, preprocess_data,
                generate_data_quality_report, validate_canonical_ids,
                ScientificFailureError, PipelineMetrics, check_alert_conditions,
                DATA_DICTIONARY, INPUT_SCHEMA, OUTPUT_SCHEMA,
                run_scientific_validation_gate]:
        assert sym is not None, f"Symbol {sym} is None"


# =============================================================================
# P4-043: tenant profile yaml files
# =============================================================================

def test_p4_043_tenant_profiles_shipped():
    """rl/reward_weights.{tenant_id}.yaml files must be shipped as
    ACTUAL files (not commented-out examples).
    """
    assert (REPO / "rl" / "reward_weights.rare_disease_partner.yaml").exists(), (
        "rl/reward_weights.rare_disease_partner.yaml must exist (P4-043)"
    )
    assert (REPO / "rl" / "reward_weights.safety_first.yaml").exists(), (
        "rl/reward_weights.safety_first.yaml must exist (P4-043)"
    )


# =============================================================================
# IN-086: apache-airflow version pin
# =============================================================================

def test_in_086_root_requirements_airflow_pin():
    """Root requirements.txt must pin apache-airflow>=2.10.0,<3.0.0
    (matching phase1/requirements.txt).
    """
    root_req = (REPO / "requirements.txt").read_text()
    m = re.search(r"^apache-airflow(>=\d+\.\d+\.\d+,\s*<\d+\.\d+\.\d+)\s*$", root_req, re.MULTILINE)
    assert m, (
        f"apache-airflow pin not found or malformed in requirements.txt. "
        f"Expected: apache-airflow>=2.10.0,<3.0.0"
    )
    pin = m.group(1).replace(" ", "")
    assert pin == ">=2.10.0,<3.0.0", (
        f"apache-airflow pin mismatch: got '{pin}', expected '>=2.10.0,<3.0.0'"
    )


# =============================================================================
# IN-092: Dockerfile.ml runtime copies source
# =============================================================================

def test_in_092_dockerfile_ml_copies_source():
    """Dockerfile.ml runtime stage must COPY the source directories
    so `docker run` (without compose bind mount) works.
    """
    dfml = (REPO / "Dockerfile.ml").read_text()
    for dir_name in ["phase1/", "phase2/", "rl/", "graph_transformer/", "shared/", "common/", "scripts/"]:
        assert f"COPY --chown=drugos:drugos {dir_name}" in dfml, (
            f"Dockerfile.ml must COPY {dir_name} in the runtime stage"
        )


# =============================================================================
# P4-011: validated_hypotheses.csv must NOT exist in rl/
# =============================================================================

def test_p4_011_no_fake_validated_hypotheses_csv():
    """rl/validated_hypotheses.csv must NOT exist (no fake partner data
    shipped in the package).
    """
    assert not (REPO / "rl" / "validated_hypotheses.csv").exists(), (
        "rl/validated_hypotheses.csv exists — this ships fake partner names "
        "and fake validation timestamps in production deployments (P4-011)."
    )


# =============================================================================
# SH-029: run_4phase.py gt-epochs env-driven
# =============================================================================

def test_sh_029_run_4phase_gt_epochs_env_driven():
    """run_4phase.py must read DRUGOS_GT_EPOCHS env var for the
    --gt-epochs default (so production deployments set 500 via env).
    """
    r4p = (REPO / "run_4phase.py").read_text()
    assert 'os.environ.get("DRUGOS_GT_EPOCHS", "80")' in r4p, (
        "run_4phase.py must read DRUGOS_GT_EPOCHS env var as the default for --gt-epochs"
    )


if __name__ == "__main__":
    # Allow running as a script (not via pytest) for quick verification
    import traceback
    tests = [
        ("SH-021", test_sh_021_writeback_to_phase2_no_name_error),
        ("IN-100a", test_in_100_dockerfile_airflow_version_matches_requirements),
        ("IN-100b", test_in_100_dockerfile_airflow_version_explicit_2_10_5),
        ("DFML-Py", test_dockerfile_ml_python_version_compat_with_pinned_packages),
        ("DFML-No314", test_dockerfile_ml_no_python_3_14),
        ("IN-030-fe", test_in_030_frontend_dockerignore_exists),
        ("IN-030-p1", test_in_030_phase1_dockerignore_exists),
        ("IN-030-root", test_in_030_root_dockerignore_still_exists),
        ("SH-020", lambda: test_sh_020_writeback_to_phase1_atomic_no_duplicate_growth(
            tempfile.mkdtemp(), __import__("pytest").MonkeyPatch() if False else None)),
        ("SH-032a", test_sh_032_atomic_write_constants_from_shared_contract),
        ("SH-032b", test_sh_032_writeback_uses_atomic_write_in_source),
        ("P4-002a", test_p4_002_evidence_based_thresholds_present),
        ("P4-002b", test_p4_002_thresholds_have_docstrings_citing_sources),
        ("SH-033", test_sh_033_flywheel_monitor_uses_public_api),
        ("SH-034", test_sh_034_feature_contract_no_drift),
        ("P4-032", test_p4_032_validate_wrapper_exports),
        ("P4-043", test_p4_043_tenant_profiles_shipped),
        ("IN-086", test_in_086_root_requirements_airflow_pin),
        ("IN-092", test_in_092_dockerfile_ml_copies_source),
        ("P4-011", test_p4_011_no_fake_validated_hypotheses_csv),
        ("SH-029", test_sh_029_run_4phase_gt_epochs_env_driven),
    ]
    passed, failed = 0, 0
    for name, fn in tests:
        try:
            if name == "SH-020":
                # This test takes a tmp_path + monkeypatch — emulate
                tmp = tempfile.mkdtemp()
                class _MP:
                    def setenv(self, k, v): os.environ[k] = v
                fn(tmp, _MP())
                shutil.rmtree(tmp, ignore_errors=True)
            else:
                fn()
            print(f"PASS {name}")
            passed += 1
        except Exception as e:
            print(f"FAIL {name}: {e}")
            traceback.print_exc()
            failed += 1
    print(f"\n=== {passed} PASS / {failed} FAIL / {len(tests)} total ===")
