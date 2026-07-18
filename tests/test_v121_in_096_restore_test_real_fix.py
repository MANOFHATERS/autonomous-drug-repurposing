"""v121 IN-096 REAL ROOT FIX verification tests.

Red-team audit (per the user's directive: "comments are fakes"):
    The v113 "fix" for IN-096 added ``scripts/restore_test.py`` but did
    NOT wire it into CI. The script existed but never ran. This is the
    exact "comments are fakes" pattern: the script LOOKED like a fix,
    but with no CI job to execute it, the backup-verification promise
    was aspirational only.

REAL ROOT FIX (v121):
    1. Added a weekly ``restore-test`` job to ``.github/workflows/ci.yml``
       that runs ``scripts/restore_test.py`` every Monday 06:00 UTC.
    2. Added the ``schedule`` trigger to the workflow's ``on:`` block.
    3. Added Alertmanager + alert rules + pushgateway to docker-compose
       + observability — the audit explicitly required "Add alerting
       for backup failures (Alertmanager)".
    4. Enhanced ``scripts/restore_test.py`` to push backup-health
       metrics to pushgateway (so the alert rules have data to fire on).

VERIFICATION (these tests):
    1. CI workflow has a ``restore-test`` job.
    2. CI workflow has a ``schedule`` trigger with a weekly cron.
    3. The ``restore-test`` job runs ``scripts/restore_test.py``.
    4. The ``restore-test`` job creates a GitHub issue on failure
       (alerting requirement).
    5. ``docker-compose.yml`` defines an ``alertmanager`` service.
    6. ``docker-compose.yml`` defines a ``pushgateway`` service.
    7. ``observability/alerts.yml`` defines a ``BackupRestoreFailed`` alert.
    8. ``observability/alerts.yml`` defines a ``BackupAgeExceededRPO`` alert.
    9. ``observability/alerts.yml`` defines a ``BackupJobNotRunning`` alert.
    10. ``observability/prometheus.yml`` loads ``alerts.yml`` via ``rule_files``.
    11. ``observability/prometheus.yml`` scrapes pushgateway.
    12. ``scripts/restore_test.py`` pushes metrics to pushgateway
        (the v113 fake fix did NOT — the alerts would have no data).
"""
from __future__ import annotations

import ast
import inspect
import re
import sys
from pathlib import Path

import pytest
import yaml

REPO = Path(__file__).resolve().parents[1]


def _strip_comments(text: str) -> str:
    """Strip `# ...` comment lines from any source file."""
    return '\n'.join(
        line for line in text.splitlines()
        if not line.lstrip().startswith('#')
    )


def _read(path: str) -> str:
    return (REPO / path).read_text()


def _read_code(path: str) -> str:
    return _strip_comments(_read(path))


def _parse_yaml(path: str) -> dict:
    return yaml.safe_load(_read(path))


# ──────────────────────────────────────────────────────────────────────────
# Test 1: CI workflow has a restore-test job.
# ──────────────────────────────────────────────────────────────────────────
def test_ci_workflow_has_restore_test_job():
    ci = _parse_yaml(".github/workflows/ci.yml")
    assert "restore-test" in ci.get("jobs", {}), (
        "v121 IN-096 REAL FIX FAILED: .github/workflows/ci.yml has no "
        "'restore-test' job. The v113 fake fix added scripts/restore_test.py "
        "but no CI job to actually run it — the script existed but never "
        "executed, so backup verification was aspirational only."
    )


# ──────────────────────────────────────────────────────────────────────────
# Test 2: CI workflow has a schedule trigger with a weekly cron.
# ──────────────────────────────────────────────────────────────────────────
def test_ci_workflow_has_weekly_schedule():
    ci = _parse_yaml(".github/workflows/ci.yml")
    # YAML parses `on:` as either a dict or, if it has unhashable values,
    # as a dict with the trigger names. PyYAML may parse `on:` as `True`
    # because `on` is a YAML 1.1 boolean. Handle both.
    on_block = ci.get("on", ci.get(True, {}))
    assert "schedule" in on_block, (
        "v121 IN-096 REAL FIX FAILED: CI workflow has no 'schedule' "
        "trigger. The restore-test job needs a weekly cron to actually "
        "run — without it, the job is defined but never executes."
    )
    schedule = on_block["schedule"]
    assert isinstance(schedule, list) and len(schedule) > 0, (
        f"schedule must be a non-empty list, got {schedule!r}"
    )
    # Verify the cron is weekly (Monday) — pattern: '0 6 * * 1'.
    crons = [s.get("cron", "") for s in schedule]
    assert any("1" in c.split()[-1] for c in crons), (
        f"no weekly-Monday cron in schedule: {crons}. The audit required "
        f"a weekly restore test (FDA 21 CFR Part 11 — backup verification)."
    )


# ──────────────────────────────────────────────────────────────────────────
# Test 3: restore-test job runs scripts/restore_test.py.
# ──────────────────────────────────────────────────────────────────────────
def test_restore_test_job_runs_script():
    ci = _parse_yaml(".github/workflows/ci.yml")
    job = ci["jobs"]["restore-test"]
    steps = job.get("steps", [])
    # Concatenate all step `run` commands and check that
    # `scripts/restore_test.py` appears.
    all_runs = ""
    for step in steps:
        run_cmd = step.get("run", "")
        if isinstance(run_cmd, str):
            all_runs += run_cmd + "\n"
    assert "scripts/restore_test.py" in all_runs, (
        "v121 IN-096 REAL FIX FAILED: the restore-test job does NOT run "
        "scripts/restore_test.py. The script exists but is never invoked "
        "— the v113 fake-fix pattern."
    )


# ──────────────────────────────────────────────────────────────────────────
# Test 4: restore-test job creates a GitHub issue on failure (alerting).
# ──────────────────────────────────────────────────────────────────────────
def test_restore_test_job_creates_issue_on_failure():
    ci = _parse_yaml(".github/workflows/ci.yml")
    job = ci["jobs"]["restore-test"]
    steps = job.get("steps", [])
    # Look for peter-evans/create-issue-from-file action.
    has_issue_action = False
    for step in steps:
        uses = step.get("uses", "")
        if "create-issue-from-file" in str(uses):
            has_issue_action = True
            break
    assert has_issue_action, (
        "v121 IN-096 REAL FIX FAILED: the restore-test job does NOT create "
        "a GitHub issue on failure. The audit explicitly required 'Add "
        "alerting for backup failures (Alertmanager)' — a failed restore "
        "test without an alert is a silent patient-safety risk."
    )


# ──────────────────────────────────────────────────────────────────────────
# Test 5: docker-compose.yml defines an alertmanager service.
# ──────────────────────────────────────────────────────────────────────────
def test_docker_compose_has_alertmanager():
    dc = _parse_yaml("docker-compose.yml")
    services = dc.get("services", {})
    assert "alertmanager" in services, (
        "v121 IN-096 REAL FIX FAILED: docker-compose.yml has no 'alertmanager' "
        "service. The audit explicitly required 'Add alerting for backup "
        "failures (Alertmanager)' — without the service, the alerts in "
        "alerts.yml have nowhere to route to."
    )


# ──────────────────────────────────────────────────────────────────────────
# Test 6: docker-compose.yml defines a pushgateway service.
# ──────────────────────────────────────────────────────────────────────────
def test_docker_compose_has_pushgateway():
    dc = _parse_yaml("docker-compose.yml")
    services = dc.get("services", {})
    assert "pushgateway" in services, (
        "v121 IN-096 REAL FIX FAILED: docker-compose.yml has no 'pushgateway' "
        "service. scripts/restore_test.py is a batch job that exits — its "
        "metrics would be lost between Prometheus scrape intervals without "
        "a push-based buffer. The alerts in alerts.yml would have NO data."
    )


# ──────────────────────────────────────────────────────────────────────────
# Test 7: alerts.yml defines BackupRestoreFailed.
# ──────────────────────────────────────────────────────────────────────────
def test_alerts_yml_defines_backup_restore_failed():
    alerts = _parse_yaml("observability/alerts.yml")
    groups = alerts.get("groups", [])
    all_alert_names = set()
    for group in groups:
        for rule in group.get("rules", []):
            if "alert" in rule:
                all_alert_names.add(rule["alert"])
    assert "BackupRestoreFailed" in all_alert_names, (
        "v121 IN-096 REAL FIX FAILED: observability/alerts.yml does not "
        f"define 'BackupRestoreFailed' alert. Defined: {all_alert_names}"
    )


# ──────────────────────────────────────────────────────────────────────────
# Test 8: alerts.yml defines BackupAgeExceededRPO.
# ──────────────────────────────────────────────────────────────────────────
def test_alerts_yml_defines_backup_age_exceeded_rpo():
    alerts = _parse_yaml("observability/alerts.yml")
    groups = alerts.get("groups", [])
    all_alert_names = set()
    for group in groups:
        for rule in group.get("rules", []):
            if "alert" in rule:
                all_alert_names.add(rule["alert"])
    assert "BackupAgeExceededRPO" in all_alert_names, (
        "v121 IN-096 REAL FIX FAILED: observability/alerts.yml does not "
        f"define 'BackupAgeExceededRPO' alert. Defined: {all_alert_names}"
    )


# ──────────────────────────────────────────────────────────────────────────
# Test 9: alerts.yml defines BackupJobNotRunning.
# ──────────────────────────────────────────────────────────────────────────
def test_alerts_yml_defines_backup_job_not_running():
    alerts = _parse_yaml("observability/alerts.yml")
    groups = alerts.get("groups", [])
    all_alert_names = set()
    for group in groups:
        for rule in group.get("rules", []):
            if "alert" in rule:
                all_alert_names.add(rule["alert"])
    assert "BackupJobNotRunning" in all_alert_names, (
        "v121 IN-096 REAL FIX FAILED: observability/alerts.yml does not "
        f"define 'BackupJobNotRunning' alert. Defined: {all_alert_names}"
    )


# ──────────────────────────────────────────────────────────────────────────
# Test 10: prometheus.yml loads alerts.yml via rule_files.
# ──────────────────────────────────────────────────────────────────────────
def test_prometheus_loads_alerts_yml():
    prom = _parse_yaml("observability/prometheus.yml")
    rule_files = prom.get("rule_files", [])
    assert "alerts.yml" in rule_files, (
        "v121 IN-096 REAL FIX FAILED: observability/prometheus.yml does "
        f"not load 'alerts.yml' via rule_files. Got: {rule_files}. "
        "Without this, the alert rules in alerts.yml are dead code — "
        "Prometheus would never evaluate them."
    )


# ──────────────────────────────────────────────────────────────────────────
# Test 11: prometheus.yml scrapes pushgateway.
# ──────────────────────────────────────────────────────────────────────────
def test_prometheus_scrapes_pushgateway():
    prom = _parse_yaml("observability/prometheus.yml")
    scrape_configs = prom.get("scrape_configs", [])
    job_names = [sc.get("job_name", "") for sc in scrape_configs]
    assert "pushgateway" in job_names, (
        "v121 IN-096 REAL FIX FAILED: observability/prometheus.yml does "
        f"not scrape 'pushgateway'. Got jobs: {job_names}. Without this, "
        "the metrics pushed by scripts/restore_test.py are never scraped, "
        "and the alerts have NO data to fire on."
    )


# ──────────────────────────────────────────────────────────────────────────
# Test 12: scripts/restore_test.py pushes metrics to pushgateway.
# This is the CRITICAL test — the v113 fake fix did NOT push metrics,
# so the alerts would have had no data even after the v121 alert rules
# were added.
# ──────────────────────────────────────────────────────────────────────────
def test_restore_test_script_pushes_metrics():
    """The restore_test.py script MUST push metrics to pushgateway.

    The v113 fake fix added the script but no metric emission. The v121
    alert rules (BackupRestoreFailed, BackupAgeExceededRPO,
    BackupJobNotRunning) fire on these metrics — without the push, the
    alerts are dead code. This AST check verifies the script calls
    ``_push_metrics`` (or equivalent) from its main function.
    """
    src = _read("scripts/restore_test.py")
    # Strip comments + docstrings before checking, so a commented-out
    # ``_push_metrics`` call doesn't pass the test.
    tree = ast.parse(src)
    found_push_call = False
    found_push_definition = False
    for node in ast.walk(tree):
        # Look for the _push_metrics function definition.
        if isinstance(node, ast.FunctionDef) and node.name == "_push_metrics":
            found_push_definition = True
        # Look for any call to _push_metrics (in main() or anywhere).
        if (
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Name)
            and node.func.id == "_push_metrics"
        ):
            found_push_call = True
    assert found_push_definition, (
        "v121 IN-096 REAL FIX FAILED: scripts/restore_test.py does NOT "
        "define a _push_metrics function. The v113 fake fix had no "
        "metric emission — the alerts in alerts.yml would have NO data."
    )
    assert found_push_call, (
        "v121 IN-096 REAL FIX FAILED: scripts/restore_test.py defines "
        "_push_metrics but NEVER CALLS IT. The alerts in alerts.yml "
        "would have NO data — exactly the 'comments are fakes' pattern."
    )


# ──────────────────────────────────────────────────────────────────────────
# Test 13: scripts/restore_test.py emits the specific metric names
# the alert rules fire on.
# ──────────────────────────────────────────────────────────────────────────
def test_restore_test_emits_expected_metric_names():
    """The script MUST emit the metric names referenced in alerts.yml.

    alerts.yml fires on:
      - drugos_backup_restore_test_total{result="fail"}
      - drugos_backup_restore_test_timestamp_seconds
      - drugos_backup_age_hours
      - drugos_rpo_hours

    If the script emits DIFFERENT names, the alerts are dead code.
    """
    src = _read("scripts/restore_test.py")
    required_metrics = [
        "drugos_backup_restore_test_total",
        "drugos_backup_restore_test_timestamp_seconds",
        "drugos_backup_age_hours",
        "drugos_rpo_hours",
    ]
    missing = [m for m in required_metrics if m not in src]
    assert not missing, (
        "v121 IN-096 REAL FIX FAILED: scripts/restore_test.py does not "
        f"emit the required metrics: {missing}. The alert rules in "
        "alerts.yml fire on these names — without them, alerts are dead code."
    )


# ──────────────────────────────────────────────────────────────────────────
# Test 14: Behavioral — restore_test.py exits with code 2 when backup
# dir is missing (config error, not a silent pass).
# ──────────────────────────────────────────────────────────────────────────
def test_restore_test_exits_nonzero_when_backup_dir_missing(tmp_path):
    """The script MUST exit non-zero when the backup dir is missing.

    A silent pass (exit 0) on missing backups would hide the patient-
    safety risk. The script must FAIL LOUDLY so the CI job (and thus
    the GitHub issue alert) fires.
    """
    import subprocess
    import os
    # Use a non-existent backup dir.
    env = os.environ.copy()
    env["DRUGOS_BACKUP_DIR"] = str(tmp_path / "nonexistent")
    env.pop("DRUGOS_RESTORE_TEST_POSTGRES_URI", None)
    env.pop("DRUGOS_RESTORE_TEST_NEO4J_URI", None)
    env.pop("DRUGOS_PUSHGATEWAY_URL", None)  # don't try to push metrics
    result = subprocess.run(
        [sys.executable, str(REPO / "scripts/restore_test.py")],
        env=env, capture_output=True, text=True, timeout=30,
    )
    assert result.returncode != 0, (
        f"v121 IN-096 REAL FIX FAILED: restore_test.py exited 0 when "
        f"backup dir was missing. stdout: {result.stdout!r}, "
        f"stderr: {result.stderr!r}. The script must FAIL LOUDLY so "
        f"the CI alert fires."
    )


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-v"]))
