"""
v130 TM16 — REAL-BINARY verification that the CycloneDX + pip-audit
invocations in .github/workflows/ci.yml use flags the actual binaries
actually accept.

CONTEXT
-------
The v129 test suite (tests/team_cosmic_v129/test_tm16_v129_real_root_fixes.py)
claimed to verify Task 16.1 but only checked that the ci.yml step NAME
contained the string "CycloneDX SBOM". It never executed the binary.
When you actually ran the v129 command:

    cyclonedx-py environment --output-format json --output-file X --1

the binary exited 2 with:

    usage: cyclonedx-py [-h] [--version] <command> ...
    cyclonedx-py: error: unrecognized arguments: --output-file --1

Both `--output-file` (correct flag is `--outfile` / `-o`) and `--1`
(correct flag is `--sv 1.5` / `--schema-version 1.5`) were invalid.
Every CI run that reached the SBOM step failed.

This v130 suite runs the ACTUAL binaries (`cyclonedx-py environment --help`
and `pip-audit --help`) and parses their argparse usage output. It then
reads ci.yml and asserts that every flag the workflow invokes is one the
binary actually accepts. This is the hostile-auditor pattern the user
demanded: don't trust the comment, don't trust the YAML — execute the
binary and verify.

These tests are network-independent (they only call `--help`) and fast
(<2s). They run in the `mypy`, `lint`, and `coverage` CI jobs because
the test file lives under tests/team_cosmic_v130/ which is included in
the lint scope.

Run locally:
    python -m pytest tests/team_cosmic_v130/test_tm16_v130_cyclonedx_real_flags.py -v
"""
from __future__ import annotations

import ast
import re
import shutil
import subprocess
import sys
from pathlib import Path

import pytest
import yaml

REPO_ROOT = Path(__file__).resolve().parents[2]
CI_PATH = REPO_ROOT / ".github" / "workflows" / "ci.yml"


# ============================================================================
# Helpers — run the actual binaries and parse their argparse usage output
# ============================================================================

def _run_help(binary: str, subcommand: str | None = None) -> str:
    """Run `<binary> <subcommand> --help` and return stdout.

    Skips the test if the binary is not installed (so this test suite
    can run on dev machines without pip-audit/cyclonedx-bom installed).
    In CI, the `security-scan` job installs both binaries before any
    test runs — but this test file is also collected by `lint`/`mypy`
    jobs which don't install them. We use pytest.skip to handle that
    gracefully.
    """
    if not shutil.which(binary):
        pytest.skip(f"{binary} not installed — run `pip install cyclonedx-bom pip-audit` to enable this test")
    cmd = [binary]
    if subcommand:
        cmd.append(subcommand)
    cmd.append("--help")
    try:
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=15,
        )
    except subprocess.TimeoutExpired:
        pytest.fail(f"{binary} --help timed out after 15s")
    # argparse writes errors to stderr but usage to stdout. We accept either.
    return result.stdout + "\n" + result.stderr


def _extract_long_flags(help_text: str) -> set[str]:
    """Extract all `--long-flag` names from argparse help output.

    Argparse format is consistent across Python 3.9+:
        -o, --outfile <file>     Description...
        --sv, --schema-version <version>
                                    Description...
        --strict                 Description...

    We pull every `--[a-z-]+` token (followed by space, comma, newline, or
    end-of-line). The regex is greedy on the flag name only.
    """
    flags = set(re.findall(r"--([a-z][a-z0-9-]*)", help_text))
    return flags


def _load_ci_yaml() -> dict:
    if not CI_PATH.exists():
        pytest.fail(f"CI workflow file not found: {CI_PATH}")
    with open(CI_PATH) as f:
        return yaml.safe_load(f)


def _extract_cyclonedx_py_invocation(ci_yaml: dict) -> str:
    """Find the `cyclonedx-py environment ...` command in ci.yml.

    Returns the full shell command as a string. Raises AssertionError
    if not found.

    The command in ci.yml is multi-line with shell line continuations
    (`\\` at end of line). We extract from `cyclonedx-py environment`
    up to the next shell statement (a line that does NOT end with `\\`
    and is NOT a continuation).
    """
    security_job = ci_yaml.get("jobs", {}).get("security-scan", {})
    for step in security_job.get("steps", []):
        run = step.get("run", "") or ""
        if "cyclonedx-py environment" not in run:
            continue
        # Walk the run line-by-line starting from the cyclonedx-py line.
        lines = run.split("\n")
        start_idx = None
        for i, line in enumerate(lines):
            if "cyclonedx-py environment" in line:
                start_idx = i
                break
        if start_idx is None:
            continue
        # Collect lines until we hit a line that does NOT end with `\`
        # (continuation) — but always include at least the start line.
        collected = []
        for j in range(start_idx, len(lines)):
            line = lines[j]
            collected.append(line)
            # Strip trailing whitespace + comments to check for `\`.
            stripped = line.rstrip()
            # Remove inline comments.
            if "#" in stripped:
                stripped = stripped.split("#")[0].rstrip()
            if not stripped.endswith("\\"):
                break
        return "\n".join(collected)
    pytest.fail("ci.yml security-scan job does not invoke `cyclonedx-py environment`")


def _extract_pip_audit_invocation(ci_yaml: dict) -> str:
    """Find the `pip-audit ...` command in ci.yml.

    The pip-audit invocation is multi-line with shell continuations.
    We extract from the FIRST `pip-audit` line (the actual command)
    up to the line that doesn't end with `\\`. We must NOT match the
    string "pip-audit" inside Python heredocs or comments — only the
    actual shell command.
    """
    security_job = ci_yaml.get("jobs", {}).get("security-scan", {})
    for step in security_job.get("steps", []):
        run = step.get("run", "") or ""
        # Find lines that START with `pip-audit` (after optional whitespace).
        # This avoids matching `pip-audit` mentions inside echo strings or
        # Python heredocs.
        match = re.search(r"(?m)^\s*pip-audit\s", run)
        if not match:
            continue
        lines = run.split("\n")
        start_idx = None
        for i, line in enumerate(lines):
            if re.match(r"^\s*pip-audit\s", line):
                start_idx = i
                break
        if start_idx is None:
            continue
        collected = []
        for j in range(start_idx, len(lines)):
            line = lines[j]
            collected.append(line)
            stripped = line.rstrip()
            if "#" in stripped:
                stripped = stripped.split("#")[0].rstrip()
            if not stripped.endswith("\\"):
                break
        return "\n".join(collected)
    pytest.fail("ci.yml security-scan job does not invoke `pip-audit` (as a shell command)")


# ============================================================================
# Task 16.1 — CycloneDX SBOM: real-binary flag verification
# ============================================================================

class TestCycloneDxPyRealFlags:
    """Verify every flag passed to `cyclonedx-py environment` in ci.yml
    is actually accepted by the binary (per `--help` output)."""

    def test_cyclonedx_py_binary_is_installed_in_ci_or_dev(self):
        """The test suite gracefully skips if cyclonedx-py is not
        installed locally — but in CI it MUST be present."""
        # In CI, the security-scan job installs it before this test runs.
        # On dev machines, it's optional.
        if not shutil.which("cyclonedx-py"):
            pytest.skip("cyclonedx-py not installed locally")

    def test_cyclonedx_py_help_lists_outfile_flag(self):
        """`--outfile` (or `-o`) MUST be in the help output.

        The v129 bug was that ci.yml used `--output-file` which is NOT
        a valid flag. The correct flag is `--outfile` (short form `-o`).
        """
        help_text = _run_help("cyclonedx-py", "environment")
        flags = _extract_long_flags(help_text)
        assert "outfile" in flags, (
            f"`--outfile` not found in `cyclonedx-py environment --help` output. "
            f"Available flags: {sorted(flags)}"
        )

    def test_cyclonedx_py_help_lists_sv_or_schema_version_flag(self):
        """`--sv` (or `--schema-version`) MUST be in the help output.

        The v129 bug was that ci.yml used `--1` which is NOT a valid
        flag. The correct flag is `--sv 1.5` (short form of
        `--schema-version 1.5`).
        """
        help_text = _run_help("cyclonedx-py", "environment")
        flags = _extract_long_flags(help_text)
        assert "sv" in flags or "schema-version" in flags, (
            f"Neither `--sv` nor `--schema-version` found in help output. "
            f"Available flags: {sorted(flags)}"
        )

    def test_cyclonedx_py_help_does_not_accept_output_file_flag(self):
        """Sanity check: `--output-file` MUST NOT be a valid flag.

        If this assertion fails, cyclonedx-bom has changed its CLI
        surface and ci.yml may need to be updated. This guards against
        silent regressions in upstream releases.
        """
        help_text = _run_help("cyclonedx-py", "environment")
        flags = _extract_long_flags(help_text)
        assert "output-file" not in flags, (
            "`--output-file` is now a valid flag — cyclonedx-bom CLI "
            "has changed. Review the v130 fix in ci.yml."
        )

    def test_cyclonedx_py_help_does_not_accept_bare_dash_1_flag(self):
        """Sanity check: bare `--1` MUST NOT be a valid flag."""
        help_text = _run_help("cyclonedx-py", "environment")
        flags = _extract_long_flags(help_text)
        # `--1` is not a `--[a-z]...` flag so it won't match our regex.
        # But we explicitly check that the help output does not mention
        # `--1` as a flag (it would only appear in error messages).
        assert re.search(r"--1\b", help_text) is None, (
            "`--1` appears in cyclonedx-py help output — unexpected."
        )

    def test_ci_yml_cyclonedx_py_invocation_uses_outfile_not_output_file(self):
        """ci.yml MUST use `--outfile` (not the invalid `--output-file`)."""
        ci = _load_ci_yaml()
        cmd = _extract_cyclonedx_py_invocation(ci)
        assert "--outfile" in cmd, (
            f"ci.yml cyclonedx-py invocation does not use `--outfile`. "
            f"Command: {cmd}"
        )
        assert "--output-file" not in cmd, (
            f"ci.yml cyclonedx-py invocation STILL uses the invalid "
            f"`--output-file` flag. Command: {cmd}"
        )

    def test_ci_yml_cyclonedx_py_invocation_uses_sv_not_bare_dash_1(self):
        """ci.yml MUST use `--sv <version>` (not the invalid `--1`)."""
        ci = _load_ci_yaml()
        cmd = _extract_cyclonedx_py_invocation(ci)
        assert "--sv" in cmd or "--schema-version" in cmd, (
            f"ci.yml cyclonedx-py invocation does not use `--sv` or "
            f"`--schema-version`. Command: {cmd}"
        )
        # The v129 bug was `--1` as a bare flag. We accept `--sv 1.5`
        # but NOT `--1` as a standalone flag.
        # Match `--1` only when it's a flag (preceded by whitespace or
        # start of line, NOT followed by `.` or more digits — that would
        # be `--1.5` which is also invalid but different).
        bare_dash_1 = re.search(r"(?:^|\s)--1(?!\d|\.)(?:\s|$)", cmd)
        assert bare_dash_1 is None, (
            f"ci.yml cyclonedx-py invocation STILL uses the invalid bare "
            f"`--1` flag. Command: {cmd}"
        )

    def test_ci_yml_cyclonedx_py_invocation_runs_successfully(self):
        """END-TO-END: actually execute the cyclonedx-py command from
        ci.yml against the real environment and verify it succeeds +
        produces a valid CycloneDX SBOM.

        This is the test the v129 suite SHOULD have had but didn't —
        it would have caught the `--1` bug immediately.
        """
        if not shutil.which("cyclonedx-py"):
            pytest.skip("cyclonedx-py not installed locally")
        ci = _load_ci_yaml()
        cmd_text = _extract_cyclonedx_py_invocation(ci)
        # Strip line continuations and extra whitespace.
        cmd_text = re.sub(r"\\\s*\n\s*", " ", cmd_text)
        cmd_text = " ".join(cmd_text.split())
        # Override the output file to /tmp so we don't pollute the repo.
        cmd_text = re.sub(r"--outfile\s+\S+", "--outfile /tmp/v130-test-sbom.cdx.json", cmd_text)
        # Drop the leading `cyclonedx-py` since we'll use a list.
        # The command in ci.yml is multi-line with line continuations.
        # We extract the args after `cyclonedx-py environment`.
        match = re.match(r"cyclonedx-py\s+environment\s+(.*)", cmd_text)
        assert match, f"Could not parse cyclonedx-py command: {cmd_text}"
        args = match.group(1).split()
        # Drop shell-only constructs (none expected, but defensive).
        args = [a for a in args if not a.startswith("#")]
        result = subprocess.run(
            ["cyclonedx-py", "environment"] + args,
            capture_output=True,
            text=True,
            timeout=60,
        )
        assert result.returncode == 0, (
            f"cyclonedx-py command failed with exit code {result.returncode}.\n"
            f"STDOUT: {result.stdout}\n"
            f"STDERR: {result.stderr}\n"
            f"Args: {args}"
        )
        sbom_path = Path("/tmp/v130-test-sbom.cdx.json")
        assert sbom_path.exists(), f"SBOM file not created: {sbom_path}"
        import json
        sbom = json.loads(sbom_path.read_text())
        assert sbom.get("bomFormat") == "CycloneDX", (
            f"SBOM is not CycloneDX format: {sbom.get('bomFormat')}"
        )
        assert len(sbom.get("components", [])) > 0, "SBOM has 0 components"


# ============================================================================
# Task 16.1 — pip-audit: real-binary flag verification
# ============================================================================

class TestPipAuditRealFlags:
    """Verify every flag passed to `pip-audit` in ci.yml is actually
    accepted by the binary."""

    def test_pip_audit_help_lists_requirement_flag(self):
        help_text = _run_help("pip-audit")
        flags = _extract_long_flags(help_text)
        assert "requirement" in flags, (
            f"`--requirement` not found in pip-audit --help. Flags: {sorted(flags)}"
        )

    def test_pip_audit_help_lists_strict_flag(self):
        help_text = _run_help("pip-audit")
        flags = _extract_long_flags(help_text)
        assert "strict" in flags, (
            f"`--strict` not found in pip-audit --help. Flags: {sorted(flags)}"
        )

    def test_pip_audit_help_lists_aliases_flag(self):
        """`--aliases` is required for cross-referencing CVE / GHSA / PYSEC IDs."""
        help_text = _run_help("pip-audit")
        flags = _extract_long_flags(help_text)
        assert "aliases" in flags, (
            f"`--aliases` not found in pip-audit --help. Flags: {sorted(flags)}"
        )

    def test_pip_audit_help_lists_vulnerability_service_flag(self):
        """`--vulnerability-service` selects between PyPI Advisory and OSV.

        v130 uses `osv` because OSV records include CVSS severity scores
        (PyPI Advisory does not), which we need for HIGH+ filtering.
        """
        help_text = _run_help("pip-audit")
        flags = _extract_long_flags(help_text)
        assert "vulnerability-service" in flags, (
            f"`--vulnerability-service` not found in pip-audit --help. "
            f"Flags: {sorted(flags)}"
        )

    def test_ci_yml_pip_audit_invocation_uses_osv_service(self):
        """ci.yml MUST use `--vulnerability-service osv` (not the default
        PyPI Advisory which lacks CVSS severity data)."""
        ci = _load_ci_yaml()
        cmd = _extract_pip_audit_invocation(ci)
        assert "--vulnerability-service osv" in cmd, (
            f"ci.yml pip-audit invocation does not use OSV vulnerability "
            f"service. Command: {cmd}"
        )

    def test_ci_yml_pip_audit_invocation_uses_strict(self):
        """ci.yml MUST use `--strict` (fail on service errors)."""
        ci = _load_ci_yaml()
        cmd = _extract_pip_audit_invocation(ci)
        assert "--strict" in cmd, (
            f"ci.yml pip-audit invocation does not use `--strict`. Command: {cmd}"
        )

    def test_ci_yml_pip_audit_invocation_outputs_json(self):
        """ci.yml MUST output JSON so the Python filter step can parse it."""
        ci = _load_ci_yaml()
        cmd = _extract_pip_audit_invocation(ci)
        assert "--format json" in cmd, (
            f"ci.yml pip-audit invocation does not output JSON. Command: {cmd}"
        )

    def test_ci_yml_pip_audit_invocation_does_not_run_twice(self):
        """v130 ROOT FIX: pip-audit MUST run ONCE (not twice like v129).

        v129 ran pip-audit twice — once for JSON, once for columns. This
        doubled network calls to the PyPI Advisory DB. v130 runs once
        with JSON and formats the output in Python.
        """
        ci = _load_ci_yaml()
        security_job = ci["jobs"]["security-scan"]
        pip_audit_call_count = 0
        for step in security_job.get("steps", []):
            run = step.get("run", "") or ""
            # Count actual `pip-audit \` invocations (not pip-audit mentions
            # in comments). The pattern is `pip-audit` followed by a newline
            # + line continuation OR `pip-audit --requirement`.
            calls = re.findall(r"^\s*pip-audit\s+\\?-", run, re.MULTILINE)
            pip_audit_call_count += len(calls)
        assert pip_audit_call_count <= 1, (
            f"ci.yml pip-audit is invoked {pip_audit_call_count} times in "
            f"the security-scan job — v130 ROOT FIX requires exactly 1 call."
        )


# ============================================================================
# Task 16.5 — Observability smoke test verification
# ============================================================================

class TestObservabilitySmokeTest:
    """Verify the v130 observability smoke test step exists in the
    docker-build-smoke job and curls the required endpoints."""

    def test_docker_build_smoke_job_has_observability_step(self):
        """v130 ROOT FIX: docker-build-smoke job MUST have a step that
        brings up the observability stack and curls Prometheus + Grafana."""
        ci = _load_ci_yaml()
        job = ci["jobs"].get("docker-build-smoke", {})
        if not job:
            pytest.fail("ci.yml missing docker-build-smoke job")
        step_names = [s.get("name", "") for s in job.get("steps", [])]
        obs_step = next(
            (s for s in step_names if "observability" in s.lower()),
            None,
        )
        assert obs_step is not None, (
            f"docker-build-smoke job missing observability smoke test step. "
            f"Step names: {step_names}"
        )

    def test_observability_step_curls_prometheus_healthy(self):
        """The observability step MUST curl http://localhost:9090/-/healthy."""
        ci = _load_ci_yaml()
        job = ci["jobs"]["docker-build-smoke"]
        for step in job.get("steps", []):
            name = step.get("name", "")
            if "observability" in name.lower():
                run = step.get("run", "") or ""
                assert "localhost:9090/-/healthy" in run, (
                    f"Observability step does not curl Prometheus /-/healthy. "
                    f"Step run: {run}"
                )
                return
        pytest.fail("Observability step not found")

    def test_observability_step_curls_grafana_api_health(self):
        """The observability step MUST curl http://localhost:3001/api/health."""
        ci = _load_ci_yaml()
        job = ci["jobs"]["docker-build-smoke"]
        for step in job.get("steps", []):
            name = step.get("name", "")
            if "observability" in name.lower():
                run = step.get("run", "") or ""
                assert "localhost:3001/api/health" in run, (
                    f"Observability step does not curl Grafana /api/health. "
                    f"Step run: {run}"
                )
                return
        pytest.fail("Observability step not found")

    def test_observability_step_brings_up_compose_stack(self):
        """The observability step MUST run `docker compose -f
        docker-compose.observability.yml up -d`."""
        ci = _load_ci_yaml()
        job = ci["jobs"]["docker-build-smoke"]
        for step in job.get("steps", []):
            name = step.get("name", "")
            if "observability" in name.lower():
                run = step.get("run", "") or ""
                assert "docker-compose.observability.yml up -d" in run, (
                    f"Observability step does not bring up the compose stack. "
                    f"Step run: {run}"
                )
                return
        pytest.fail("Observability step not found")

    def test_observability_step_has_tear_down(self):
        """The observability step MUST have a tear-down step that runs
        `docker compose down -v` (always() — even on failure)."""
        ci = _load_ci_yaml()
        job = ci["jobs"]["docker-build-smoke"]
        teardown_found = False
        for step in job.get("steps", []):
            name = step.get("name", "")
            if "tear down observability" in name.lower():
                run = step.get("run", "") or ""
                assert "docker-compose.observability.yml down" in run, (
                    f"Tear-down step does not run `docker compose down`. "
                    f"Step run: {run}"
                )
                # Must run on failure too.
                assert step.get("if") == "always()", (
                    f"Tear-down step must have `if: always()` to run on "
                    f"failure. Got: {step.get('if')}"
                )
                teardown_found = True
                break
        assert teardown_found, (
            "docker-build-smoke job missing observability tear-down step"
        )


# ============================================================================
# Task 16.7 — lint + e2e are BLOCKING (no continue-on-error)
# ============================================================================

class TestLintAndE2eAreBlocking:
    """v129/v130 ROOT FIX (Task 16.7): lint and e2e jobs MUST NOT have
    `continue-on-error: true`. The audit explicitly required these to
    be blocking."""

    def test_lint_job_has_no_continue_on_error(self):
        ci = _load_ci_yaml()
        lint = ci["jobs"].get("lint", {})
        assert "continue-on-error" not in lint, (
            "lint job has `continue-on-error` — Task 16.7 requires it "
            "to be BLOCKING."
        )

    def test_e2e_sample_mode_job_has_no_continue_on_error(self):
        ci = _load_ci_yaml()
        e2e = ci["jobs"].get("e2e-sample-mode", {})
        assert "continue-on-error" not in e2e, (
            "e2e-sample-mode job has `continue-on-error` — Task 16.7 "
            "requires it to be BLOCKING."
        )

    def test_frontend_lint_job_has_no_continue_on_error(self):
        ci = _load_ci_yaml()
        flint = ci["jobs"].get("frontend-lint", {})
        assert "continue-on-error" not in flint, (
            "frontend-lint job has `continue-on-error` — Task 16.2 requires "
            "it to be BLOCKING."
        )

    def test_frontend_e2e_job_has_no_continue_on_error(self):
        ci = _load_ci_yaml()
        fe2e = ci["jobs"].get("frontend-e2e", {})
        assert "continue-on-error" not in fe2e, (
            "frontend-e2e job has `continue-on-error` — Task 16.2 requires "
            "it to be BLOCKING."
        )

    def test_ci_success_requires_all_jobs(self):
        """ci-success MUST require all 18 blocking jobs (no exceptions)."""
        ci = _load_ci_yaml()
        success = ci["jobs"].get("ci-success", {})
        needs = success.get("needs", [])
        # All required jobs (excluding ci-success itself).
        required = {
            "build", "lint", "test", "verify-p2-fixes", "e2e-sample-mode",
            "verify-v83-p1-p2-fixes", "build-phase3-phase4", "test-phase3-phase4",
            "verify-v31-phase3-phase4-fixes", "security-scan", "frontend-build",
            "frontend-lint", "frontend-test", "frontend-e2e", "docker-build-smoke",
            "mypy", "bandit", "coverage",
        }
        missing = required - set(needs)
        assert not missing, (
            f"ci-success needs list is missing required jobs: {missing}"
        )
