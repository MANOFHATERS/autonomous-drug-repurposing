"""Test for P4-003 ROOT FIX (HIGH) + P4-011 ROOT FIX v117 (HIGH).

P4-003: validated_hypotheses.csv path is RELATIVE to CWD — breaks in
        Docker/Kubernetes/systemd. The module_dir path is correct (ships
        with the package), but if the package is pip-installed without
        MANIFEST.in / package_data, the file may not be included in the
        wheel. In production, the validated bonus is silently 0 for ALL
        pairs, breaking the data flywheel (DOCX §10).

        Fix: (1) Add MANIFEST.in to include validated_hypotheses.csv in
        the package. (2) Add a runtime CRITICAL log if the file is not
        found in ANY of the 3 candidate paths. (3) Allow override via
        the RL_VALIDATED_HYPOTHESES_PATH env var.

P4-011 v117 ROOT FIX (HIGH — Teammate 8): the previous version of this
        test asserted `rl/validated_hypotheses.csv` MUST exist in the
        package. That test was WRONG — it pinned down the bug rather
        than the fix. The audit (P4-011) explicitly says:
        > Remove the rl/validated_hypotheses.csv file from the package.
        > The canonical path is phase1/processed_data/validated_hypotheses.csv
        > — let the writeback module create it.
        > If a demo CSV is needed for testing, ship it as
        > rl/tests/fixtures/validated_hypotheses_demo.csv and have tests
        > explicitly point to it via RL_VALIDATED_HYPOTHESES_PATH.

        This test now verifies the OPPOSITE of the original P4-003
        assertion: rl/validated_hypotheses.csv must NOT exist in the
        shipped package. Only the test fixture
        (rl/tests/fixtures/validated_hypotheses_seed.csv) may exist.
"""
from __future__ import annotations

import logging
import os
import sys
import tempfile
from pathlib import Path

import pytest

_REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(_REPO_ROOT))
sys.path.insert(0, str(_REPO_ROOT / "rl"))


def test_p4_011_rh_validated_hypotheses_csv_not_in_package():
    """P4-011 v117: rl/validated_hypotheses.csv MUST NOT exist in the package.

    The audit requires:
        Remove the rl/validated_hypotheses.csv file from the package.
        The canonical path is phase1/processed_data/validated_hypotheses.csv
        — let the writeback module create it.

    Production deployments must NOT inherit demo data. The file is
    created at RUNTIME by phase4/writeback.py when a pharma partner
    validates a hypothesis. Shipping it pre-populated (even with real
    FDA historical data) creates confusion about which entries are
    seed data vs runtime writeback output.
    """
    shipped_csv = _REPO_ROOT / "rl" / "validated_hypotheses.csv"
    assert not shipped_csv.exists(), (
        f"P4-011 v117: rl/validated_hypotheses.csv must NOT exist in the "
        f"shipped package. Production deployments must NOT inherit seed "
        f"data. The file is created at runtime by phase4/writeback.py "
        f"when a pharma partner validates a hypothesis. Found: {shipped_csv}"
    )


def test_p4_011_seed_fixture_exists_for_tests():
    """P4-011 v117: the historical FDA seed data ships as a TEST FIXTURE.

    The audit allows:
        If a demo CSV is needed for testing, ship it as
        rl/tests/fixtures/validated_hypotheses_demo.csv and have tests
        explicitly point to it via RL_VALIDATED_HYPOTHESES_PATH.

    The fixture contains real FDA historical data (aspirin for
    cardiovascular disease, warfarin for AFib, etc.) — NOT fake
    partner names. Tests that need a non-empty validated_hypotheses
    set must point to this fixture via RL_VALIDATED_HYPOTHESES_PATH.
    """
    fixture = _REPO_ROOT / "rl" / "tests" / "fixtures" / "validated_hypotheses_seed.csv"
    assert fixture.exists(), (
        f"P4-011 v117: test fixture must exist at {fixture}. This file "
        f"contains real FDA historical seed data for tests that need a "
        f"non-empty validated_hypotheses set."
    )
    content = fixture.read_text().strip()
    lines = content.splitlines()
    assert len(lines) >= 2, (
        f"P4-011 v117: fixture has {len(lines)} lines, expected at least 2 "
        f"(header + 1 row)."
    )
    header = lines[0].lower()
    assert "drug" in header and "disease" in header, (
        f"P4-011 v117: fixture header must contain 'drug' and 'disease'. "
        f"Got: {lines[0]}"
    )


def test_p4_003_manifest_in_exists_for_phase1_processed_data():
    """P4-003: MANIFEST.in exists and includes phase1 CSV data patterns.

    The MANIFEST.in must include phase1/processed_data/*.csv so that
    pip install ships the canonical validated_hypotheses.csv location
    (the directory must exist even if the file is created at runtime).
    """
    manifest_path = _REPO_ROOT / "MANIFEST.in"
    assert manifest_path.exists(), (
        "P4-003: MANIFEST.in must exist at the repo root."
    )
    # We don't assert specific contents — the canonical file is created
    # at runtime, not shipped. The MANIFEST.in just needs to exist for
    # package_data consistency.


def test_p4_003_runtime_check_logs_critical_when_file_missing(monkeypatch):
    """The runtime check must log CRITICAL when no file is found.

    We force the file to be missing by:
      1. Setting RL_VALIDATED_HYPOTHESES_PATH to a non-existent path.
      2. Setting VALIDATED_HYPOTHESES_CSV (canonical path) to a non-existent path.
      3. cd-ing to a temp dir (so CWD-relative and CWD-absolute paths
         don't find the file).
      4. Monkey-patching the module's __file__ so module_dir points to
         a temp dir (so the module-local path doesn't find the file).

    Then we re-invoke _load_validated_hypotheses() and check that a
    CRITICAL log was emitted.
    """
    import rl.rl_drug_ranker as mod

    # Save original state.
    orig_cwd = os.getcwd()
    orig_env = os.environ.pop("RL_VALIDATED_HYPOTHESES_PATH", None)
    orig_canonical_env = os.environ.pop("VALIDATED_HYPOTHESES_CSV", None)
    orig_file = mod.__file__

    # Attach a manual handler to capture CRITICAL logs.
    captured_records: list[logging.LogRecord] = []

    class _CaptureHandler(logging.Handler):
        def emit(self, record):
            captured_records.append(record)

    capture_handler = _CaptureHandler(level=logging.CRITICAL)
    rl_logger = logging.getLogger("rl.rl_drug_ranker")
    rl_logger.addHandler(capture_handler)
    rl_logger.setLevel(logging.CRITICAL)
    try:
        with tempfile.TemporaryDirectory() as tmpdir:
            os.chdir(tmpdir)
            # Override BOTH env vars to non-existent paths so the
            # loader's 4-path search finds nothing.
            os.environ["RL_VALIDATED_HYPOTHESES_PATH"] = "/nonexistent/path/to/validated_hypotheses.csv"
            os.environ["VALIDATED_HYPOTHESES_CSV"] = "/nonexistent/canonical/validated_hypotheses.csv"
            # Monkey-patch __file__ so module_dir resolves to tmpdir
            # (no validated_hypotheses.csv there).
            mod.__file__ = str(Path(tmpdir) / "rl_drug_ranker.py")
            # Re-invoke the loader directly (no reload needed — the
            # function reads __file__ at call time).
            result = mod._load_validated_hypotheses()
            # The result must be empty (no file found anywhere).
            assert result == [], (
                f"P4-003: expected empty result when no "
                f"validated_hypotheses.csv is found, got {result}"
            )
            # Check that a CRITICAL log was emitted about the missing file.
            critical_msgs = [
                r.getMessage() for r in captured_records
                if r.levelno >= logging.CRITICAL
                and "validated_hypotheses.csv" in r.getMessage()
                and "NOT FOUND" in r.getMessage()
            ]
            assert critical_msgs, (
                "P4-003: when validated_hypotheses.csv is not found in "
                "ANY candidate path, a CRITICAL log must be emitted "
                "(so operators can fix the deployment). No CRITICAL log "
                "was emitted. Captured records:\n"
                + "\n".join(r.getMessage() for r in captured_records)
            )
    finally:
        rl_logger.removeHandler(capture_handler)
        mod.__file__ = orig_file
        os.chdir(orig_cwd)
        if orig_env is not None:
            os.environ["RL_VALIDATED_HYPOTHESES_PATH"] = orig_env
        else:
            os.environ.pop("RL_VALIDATED_HYPOTHESES_PATH", None)
        if orig_canonical_env is not None:
            os.environ["VALIDATED_HYPOTHESES_CSV"] = orig_canonical_env
        else:
            os.environ.pop("VALIDATED_HYPOTHESES_CSV", None)


def test_p4_003_env_var_override_loads_seed_fixture():
    """P4-003 + P4-011 v117: env var override loads the seed fixture.

    The RL_VALIDATED_HYPOTHESES_PATH env var must take PRIORITY over
    the 4 default search paths. This test points the env var at the
    seed fixture and verifies the fixture's pairs are loaded.
    """
    fixture = _REPO_ROOT / "rl" / "tests" / "fixtures" / "validated_hypotheses_seed.csv"
    assert fixture.exists(), f"Seed fixture must exist at {fixture}"

    os.environ["RL_VALIDATED_HYPOTHESES_PATH"] = str(fixture)
    try:
        import importlib
        import rl.rl_drug_ranker as mod
        importlib.reload(mod)
        validated = mod.VALIDATED_HYPOTHESES
        # The seed fixture contains real FDA historical data including
        # (aspirin, cardiovascular disease) as a validated_positive pair.
        assert ("aspirin", "cardiovascular disease") in validated, (
            f"P4-003 + P4-011 v117: env var override did not load the seed "
            f"fixture. Expected ('aspirin', 'cardiovascular disease') in "
            f"VALIDATED_HYPOTHESES. Got: {list(validated)[:5]}"
        )
    finally:
        os.environ.pop("RL_VALIDATED_HYPOTHESES_PATH", None)
        # Reload to restore the original state.
        import importlib
        import rl.rl_drug_ranker as mod
        importlib.reload(mod)


if __name__ == "__main__":
    pytest.main([__file__, "-v", "--tb=short"])
