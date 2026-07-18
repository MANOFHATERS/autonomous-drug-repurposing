"""Forensic test for IN-096 REAL ROOT FIX (v124).

The audit's IN-096 fix list explicitly required:
    "Add a `restore-test` CI job (weekly cron) that: restores the latest
     Postgres backup to a staging DB, runs pg_dump --schema-only and
     diffs against the expected schema, runs a count query on critical
     tables, and verifies the row counts match."

The v113 "fix" added ``scripts/restore_test.py`` + ``observability/alerts.yml``
but NEVER wired the script into CI. The script's docstring falsely claimed
"See the ``restore-test`` job in ``.github/workflows/ci.yml`` (v121)" -- a
textbook "comments are fakes" pattern.

These tests verify the REAL root fix (v124):
  1. ``.github/workflows/ci.yml`` defines a ``restore-test:`` job.
  2. The workflow has a weekly ``schedule:`` cron trigger.
  3. The restore-test job actually invokes ``scripts/restore_test.py``.
  4. The job has forensic guards that fail loudly if
     ``scripts/restore_test.py`` or ``observability/alerts.yml`` are deleted.
  5. ``observability/alerts.yml`` defines the three required alert rules
     (BackupRestoreFailed, BackupAgeExceededRPO, BackupJobNotRunning).

These tests are FORENSIC: they read the actual file contents (not test
files, not comments) and assert the executable wiring is real.
"""
from __future__ import annotations

import re
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
CI_WORKFLOW = REPO_ROOT / ".github" / "workflows" / "ci.yml"
RESTORE_TEST_SCRIPT = REPO_ROOT / "scripts" / "restore_test.py"
ALERTS_YML = REPO_ROOT / "observability" / "alerts.yml"


@pytest.fixture(scope="module")
def ci_workflow_text() -> str:
    """Read the actual CI workflow file (not a copy, not a comment)."""
    assert CI_WORKFLOW.exists(), (
        f"CI workflow file not found at {CI_WORKFLOW}. "
        f"Without this file, NO CI checks run on PRs."
    )
    return CI_WORKFLOW.read_text()


# ─── IN-096.1: restore-test job exists in CI workflow ─────────────────────


def test_in_096_restore_test_job_defined(ci_workflow_text: str) -> None:
    """The CI workflow MUST define a ``restore-test:`` job.

    The v113 "fix" added the script but no CI job -- the restore test
    NEVER ran. This test catches that regression by reading the actual
    workflow YAML and asserting the job is defined at top level
    (2-space indent under ``jobs:``).
    """
    # Top-level job definition: 2-space indent, ``restore-test:`` followed
    # by either a newline or a space (for inline config).
    pattern = re.compile(r"^  restore-test:\s*$", re.MULTILINE)
    assert pattern.search(ci_workflow_text), (
        "IN-096 REAL ROOT FIX regression: the CI workflow does NOT define "
        "a `restore-test:` job. The v113 'fix' added scripts/restore_test.py "
        "but never wired it into CI -- the script's docstring falsely "
        "claimed 'See the `restore-test` job in .github/workflows/ci.yml'. "
        "This is the 'comments are fakes' pattern the audit warned about. "
        "Add a `restore-test:` job to .github/workflows/ci.yml that runs "
        "scripts/restore_test.py on a weekly cron."
    )


# ─── IN-096.2: weekly schedule cron trigger exists ────────────────────────


def test_in_096_weekly_cron_schedule(ci_workflow_text: str) -> None:
    """The CI workflow MUST have a ``schedule:`` trigger with a weekly cron.

    The audit explicitly required "weekly cron". Without the schedule,
    the restore-test job only runs on manual dispatch (which operators
    forget to do) -- the restore test silently never runs.
    """
    # Look for the schedule block in the `on:` section.
    # Format:
    #   schedule:
    #     - cron: '0 6 * * 1'
    schedule_block = re.search(
        r"^  schedule:\s*\n(?:\s+-\s+cron:\s*['\"][^'\"]+['\"]\s*\n?)+",
        ci_workflow_text,
        re.MULTILINE,
    )
    assert schedule_block is not None, (
        "IN-096 REAL ROOT FIX regression: the CI workflow does NOT have a "
        "`schedule:` cron trigger. The restore-test job would only run on "
        "manual dispatch (which operators forget). Add a weekly cron: "
        "`schedule: - cron: '0 6 * * 1'` to the `on:` block."
    )


# ─── IN-096.3: restore-test job actually invokes scripts/restore_test.py ──


def test_in_096_restore_test_job_invokes_script(ci_workflow_text: str) -> None:
    """The restore-test job MUST invoke ``scripts/restore_test.py``.

    A job that exists but doesn't run the script is useless. The v113
    "fix" had a docstring claiming the job exists -- but no actual
    job. We verify the EXECUTABLE step that runs the script.
    """
    # Find the restore-test job block. The job starts at `  restore-test:`
    # (2-space indent) and ends at the next top-level job
    # (`  <job-name>:` at 2-space indent) or end of file.
    # We use a non-greedy match to capture the job block.
    job_match = re.search(
        r"^  restore-test:\s*\n(.*?)(?=^  [a-z][a-z0-9_-]*:\s*$|\Z)",
        ci_workflow_text,
        re.MULTILINE | re.DOTALL,
    )
    assert job_match is not None, (
        "IN-096: could not isolate the `restore-test:` job block in the "
        "CI workflow. The job may be malformed or missing."
    )
    job_block = job_match.group(1)
    assert "scripts/restore_test.py" in job_block, (
        "IN-096 REAL ROOT FIX regression: the `restore-test:` job exists "
        "but does NOT invoke `scripts/restore_test.py`. The job is a "
        "no-op without the script invocation. Add a step like: "
        "`python3 scripts/restore_test.py`."
    )


# ─── IN-096.4: restore-test job has forensic guard for missing script ─────


def test_in_096_restore_test_job_guards_missing_script(ci_workflow_text: str) -> None:
    """The restore-test job MUST fail loudly if scripts/restore_test.py is deleted.

    Without this guard, a bad merge or ``git clean -xdf`` could delete
    the script and the restore-test job would silently skip the test
    (exit 0 with "file not found"). The audit's #1 warning was
    "comments are fakes" -- this guard catches the script-level
    equivalent.
    """
    job_match = re.search(
        r"^  restore-test:\s*\n(.*?)(?=^  [a-z][a-z0-9_-]*:\s*$|\Z)",
        ci_workflow_text,
        re.MULTILINE | re.DOTALL,
    )
    assert job_match is not None, (
        "IN-096: could not isolate the `restore-test:` job block."
    )
    job_block = job_match.group(1)
    # The guard checks `[ ! -f scripts/restore_test.py ]` and exits 1.
    assert "scripts/restore_test.py" in job_block and (
        "[ ! -f scripts/restore_test.py ]" in job_block
    ), (
        "IN-096: the restore-test job does NOT have a forensic guard for "
        "missing scripts/restore_test.py. If the script is deleted, the "
        "job would silently pass. Add a step: "
        "`if [ ! -f scripts/restore_test.py ]; then exit 1; fi`."
    )


# ─── IN-096.5: observability/alerts.yml exists and defines all 3 alerts ───


def test_in_096_alerts_yml_exists() -> None:
    """``observability/alerts.yml`` MUST exist.

    The restore-test script pushes metrics to pushgateway, but the
    alerts.yml file defines the Prometheus alert RULES that fire on
    those metrics. Without alerts.yml, the metrics are silent.
    """
    assert ALERTS_YML.exists(), (
        "IN-096: observability/alerts.yml NOT FOUND. The BackupRestoreFailed "
        "alert rules are MISSING. Restore the file from git: "
        "`git checkout HEAD -- observability/alerts.yml`."
    )


def test_in_096_alerts_yml_defines_required_alerts() -> None:
    """``observability/alerts.yml`` MUST define the 3 required backup alerts.

    The audit explicitly required "Add alerting for backup failures
    (Alertmanager)." The 3 alerts are:
      - BackupRestoreFailed (last restore test failed)
      - BackupAgeExceededRPO (newest backup older than RPO)
      - BackupJobNotRunning (no backup observed in 25h)
    """
    if not ALERTS_YML.exists():
        pytest.skip("alerts.yml missing -- covered by test_in_096_alerts_yml_exists")
    content = ALERTS_YML.read_text()
    required_alerts = [
        "BackupRestoreFailed",
        "BackupAgeExceededRPO",
        "BackupJobNotRunning",
    ]
    missing = [a for a in required_alerts if f"alert: {a}" not in content]
    assert not missing, (
        f"IN-096: observability/alerts.yml is MISSING the following alert "
        f"rules: {missing}. The restore-test script emits the metrics, but "
        f"without these alert rules Prometheus has nothing to fire on. "
        f"Add the missing alert rules to observability/alerts.yml."
    )


# ─── IN-096.6: scripts/restore_test.py exists and has main() entrypoint ───


def test_in_096_restore_test_script_exists() -> None:
    """``scripts/restore_test.py`` MUST exist with a callable entrypoint."""
    assert RESTORE_TEST_SCRIPT.exists(), (
        "IN-096: scripts/restore_test.py NOT FOUND. The CI restore-test "
        "job would fail. Restore the file from git: "
        "`git checkout HEAD -- scripts/restore_test.py`."
    )


def test_in_096_restore_test_script_has_main() -> None:
    """The restore_test.py script MUST have a ``main()`` entrypoint."""
    if not RESTORE_TEST_SCRIPT.exists():
        pytest.skip("restore_test.py missing -- covered by test_in_096_restore_test_script_exists")
    content = RESTORE_TEST_SCRIPT.read_text()
    # Look for `def main(` or `def main() -> int:` at column 0.
    assert re.search(r"^def main\s*\(", content, re.MULTILINE), (
        "IN-096: scripts/restore_test.py does NOT define a `main()` function. "
        "The CI restore-test job invokes `python3 scripts/restore_test.py` "
        "which requires a main() entrypoint (or __main__ guard)."
    )


# ─── IN-096.7: restore_test.py emits pushgateway metrics ──────────────────


def test_in_096_restore_test_script_emits_metrics() -> None:
    """restore_test.py MUST emit pushgateway metrics.

    The audit required "Add alerting for backup failures (Alertmanager)."
    The alerts.yml rules fire on metrics emitted by this script. Without
    the metrics emission, the alerts are dead code (the v113 "fix" had
    this exact bug -- alerts.yml existed but no metrics were emitted).
    """
    if not RESTORE_TEST_SCRIPT.exists():
        pytest.skip("restore_test.py missing")
    content = RESTORE_TEST_SCRIPT.read_text()
    # Look for the metric names defined in alerts.yml.
    required_metrics = [
        "drugos_backup_restore_test_total",
        "drugos_backup_restore_test_timestamp_seconds",
        "drugos_backup_age_hours",
    ]
    missing = [m for m in required_metrics if m not in content]
    assert not missing, (
        f"IN-096: scripts/restore_test.py does NOT emit the following "
        f"pushgateway metrics: {missing}. Without these metrics, the "
        f"alerts in observability/alerts.yml cannot fire. This is the "
        f"v113 'comments are fakes' pattern -- alerts.yml existed but "
        f"no metrics were emitted, so alerts NEVER fired."
    )
