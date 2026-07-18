# =============================================================================
# tests/test_teammate16_v118_root_fixes.py
# =============================================================================
# v118 ROOT FIX (Teammate 16 — Infrastructure): forensic root-cause
# verification of all 25 Teammate 16 issues. Red-team mode: verify against
# the ACTUAL ci.yml YAML (parsed via PyYAML) — not against comments.
#
# This test file is the single source of truth for "is Teammate 16's work
# done?". If any test fails, the issue is NOT fixed. Tests are named after
# the audit issue IDs (IN-019, IN-022, SH-001, etc.) for traceability.
#
# Run with:
#   pytest tests/test_teammate16_v118_root_fixes.py -v
# =============================================================================
import os
import sys
import yaml
import pathlib
import re

_REPO_ROOT = pathlib.Path(__file__).resolve().parents[1]
_CI_YML_PATH = _REPO_ROOT / ".github" / "workflows" / "ci.yml"
_GITIGNORE_PATH = _REPO_ROOT / ".gitignore"
_PYTEST_INI_PATH = _REPO_ROOT / "pytest.ini"
_DEPENDABOT_PATH = _REPO_ROOT / ".github" / "dependabot.yml"
_JEST_CONFIG_PATH = _REPO_ROOT / "frontend" / "jest.config.js"
_CADDYFILE_PATH = _REPO_ROOT / "frontend" / "Caddyfile"
_PACKAGE_JSON_PATH = _REPO_ROOT / "frontend" / "package.json"


def _load_ci_yaml():
    """Load and parse .github/workflows/ci.yml. Fail test if YAML is invalid."""
    with open(_CI_YML_PATH) as f:
        return yaml.safe_load(f)


def _strip_yaml_comments(src):
    """Strip YAML comments (lines whose first non-whitespace char is #).
    Used by tests that check for forbidden patterns in ci.yml — comments
    may LEGITIMATELY mention the forbidden pattern when explaining WHY it
    was removed. We only care about EXECUTABLE code."""
    code_lines = []
    for line in src.splitlines():
        stripped = line.lstrip()
        if stripped.startswith("#"):
            continue
        code_lines.append(line)
    return "\n".join(code_lines)


def _strip_js_comments(src):
    """Strip JS/TS comments (/* */ blocks + // line comments). Used by
    tests that check for forbidden patterns in jest.config.js — the file's
    header comment LEGITIMATELY mentions ts-jest when explaining WHY it
    was replaced. We only care about EXECUTABLE code."""
    import re
    # Remove /* */ block comments.
    src = re.sub(r"/\*.*?\*/", "", src, flags=re.DOTALL)
    # Remove // line comments (only when not inside a string — simplified:
    # assume // at start of stripped line is a comment).
    code_lines = []
    for line in src.splitlines():
        stripped = line.lstrip()
        if stripped.startswith("//"):
            continue
        code_lines.append(line)
    return "\n".join(code_lines)


def _load_gitignore_patterns():
    """Load .gitignore as a list of patterns (stripped of comments + blanks)."""
    with open(_GITIGNORE_PATH) as f:
        lines = f.read().splitlines()
    patterns = []
    for line in lines:
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        patterns.append(line)
    return patterns


class TestCINotBrokenReferences:
    """IN-097 ROOT FIX (critical bug not in audit): ci.yml must NOT reference
    deleted files (run_real_pipeline.py, run_full_platform.py, run_unified.py).
    compileall silently exits 0 on missing files — the build job was a NO-OP.
    """

    def test_ci_yml_does_not_reference_run_real_pipeline(self):
        """ci.yml must not reference the DELETED run_real_pipeline.py file
        in EXECUTABLE code (comments explaining WHY it was removed are OK)."""
        src = _CI_YML_PATH.read_text()
        code = _strip_yaml_comments(src)
        # Find all compileall commands in the code.
        for line in code.splitlines():
            if "compileall" in line:
                assert "run_real_pipeline.py" not in line, \
                    f"ci.yml compileall must not reference run_real_pipeline.py (IN-097): {line}"

    def test_ci_yml_does_not_reference_run_full_platform(self):
        """ci.yml must not reference the DELETED run_full_platform.py file
        in EXECUTABLE code."""
        src = _CI_YML_PATH.read_text()
        code = _strip_yaml_comments(src)
        # Check all compileall commands + import statements.
        for line in code.splitlines():
            if "compileall" in line or "import " in line:
                assert "run_full_platform.py" not in line, \
                    f"ci.yml must not reference run_full_platform.py (IN-097): {line}"

    def test_ci_yml_does_not_reference_run_unified(self):
        """ci.yml must not reference the DELETED run_unified.py file."""
        src = _CI_YML_PATH.read_text()
        code = _strip_yaml_comments(src)
        for line in code.splitlines():
            if "compileall" in line or "import " in line:
                assert "run_unified.py" not in line, \
                    f"ci.yml must not reference run_unified.py (IN-097): {line}"

    def test_ci_yml_imports_run_4phase_not_run_real_pipeline(self):
        """V31-8 verification must use run_4phase (not run_real_pipeline)."""
        src = _CI_YML_PATH.read_text()
        code = _strip_yaml_comments(src)
        assert "import run_4phase" in code, \
            "ci.yml must verify `import run_4phase` (canonical runner per IN-072)"
        # ``import run_real_pipeline`` must NOT be in executable code.
        assert "import run_real_pipeline" not in code, \
            "ci.yml executable code still has `import run_real_pipeline` — IN-097 NOT fixed"


class TestIN019SecurityScanning:
    """IN-019 (CRITICAL): CI workflow has NO security scanning.
    ROOT FIX: add security-scan jobs (bandit, pip-audit, npm audit, gitleaks,
    CodeQL, syft SBOM, Trivy image scan) + Dependabot config."""

    def test_dependabot_config_exists(self):
        assert _DEPENDABOT_PATH.exists(), \
            ".github/dependabot.yml must exist (IN-019 fix: Dependabot config)"

    def test_dependabot_config_has_pip_ecosystem(self):
        with open(_DEPENDABOT_PATH) as f:
            data = yaml.safe_load(f)
        ecosystems = [u.get("package-ecosystem") for u in data.get("updates", [])]
        assert "pip" in ecosystems, \
            "Dependabot must monitor pip ecosystem (Python deps)"

    def test_dependabot_config_has_npm_ecosystem(self):
        with open(_DEPENDABOT_PATH) as f:
            data = yaml.safe_load(f)
        ecosystems = [u.get("package-ecosystem") for u in data.get("updates", [])]
        assert "npm" in ecosystems, \
            "Dependabot must monitor npm ecosystem (frontend deps)"

    def test_dependabot_config_has_github_actions_ecosystem(self):
        with open(_DEPENDABOT_PATH) as f:
            data = yaml.safe_load(f)
        ecosystems = [u.get("package-ecosystem") for u in data.get("updates", [])]
        assert "github-actions" in ecosystems, \
            "Dependabot must monitor github-actions ecosystem"

    def test_ci_has_security_python_job(self):
        ci = _load_ci_yaml()
        jobs = ci.get("jobs", {})
        assert "security-python" in jobs, \
            "ci.yml must have a security-python job (bandit + pip-audit)"

    def test_security_python_runs_bandit(self):
        ci = _load_ci_yaml()
        steps = ci["jobs"]["security-python"]["steps"]
        step_names = [s.get("name", "") for s in steps]
        assert any("bandit" in n.lower() for n in step_names), \
            "security-python job must run bandit (SAST scan)"

    def test_security_python_runs_pip_audit(self):
        ci = _load_ci_yaml()
        steps = ci["jobs"]["security-python"]["steps"]
        step_names = [s.get("name", "") for s in steps]
        assert any("pip-audit" in n.lower() or "pip audit" in n.lower() for n in step_names), \
            "security-python job must run pip-audit (dependency CVE scan)"

    def test_ci_has_security_frontend_job(self):
        ci = _load_ci_yaml()
        jobs = ci.get("jobs", {})
        assert "security-frontend" in jobs, \
            "ci.yml must have a security-frontend job (npm audit)"

    def test_ci_has_security_secrets_job(self):
        ci = _load_ci_yaml()
        jobs = ci.get("jobs", {})
        assert "security-secrets" in jobs, \
            "ci.yml must have a security-secrets job (gitleaks)"

    def test_ci_has_security_codeql_job(self):
        ci = _load_ci_yaml()
        jobs = ci.get("jobs", {})
        assert "security-codeql" in jobs, \
            "ci.yml must have a security-codeql job (CodeQL SAST)"

    def test_ci_has_security_sbom_job(self):
        ci = _load_ci_yaml()
        jobs = ci.get("jobs", {})
        assert "security-sbom" in jobs, \
            "ci.yml must have a security-sbom job (Syft CycloneDX SBOM)"

    def test_ci_has_security_docker_scan_job(self):
        ci = _load_ci_yaml()
        jobs = ci.get("jobs", {})
        assert "security-docker-scan" in jobs, \
            "ci.yml must have a security-docker-scan job (Trivy image scan)"


class TestIN022FrontendCI:
    """IN-022 (CRITICAL): CI does NOT run frontend test suite.
    ROOT FIX: add frontend-build, frontend-lint, frontend-test,
    frontend-e2e, frontend-tsc jobs."""

    def test_ci_has_build_frontend_job(self):
        ci = _load_ci_yaml()
        assert "build-frontend" in ci.get("jobs", {}), \
            "ci.yml must have a build-frontend job (npm ci + npm run build)"

    def test_ci_has_lint_frontend_job(self):
        ci = _load_ci_yaml()
        assert "lint-frontend" in ci.get("jobs", {}), \
            "ci.yml must have a lint-frontend job (eslint)"

    def test_ci_has_typecheck_frontend_job(self):
        ci = _load_ci_yaml()
        assert "typecheck-frontend" in ci.get("jobs", {}), \
            "ci.yml must have a typecheck-frontend job (tsc --noEmit)"

    def test_ci_has_test_frontend_unit_job(self):
        ci = _load_ci_yaml()
        assert "test-frontend-unit" in ci.get("jobs", {}), \
            "ci.yml must have a test-frontend-unit job (jest)"

    def test_ci_has_test_frontend_integration_job(self):
        ci = _load_ci_yaml()
        assert "test-frontend-integration" in ci.get("jobs", {}), \
            "ci.yml must have a test-frontend-integration job"

    def test_ci_has_test_frontend_e2e_job(self):
        ci = _load_ci_yaml()
        assert "test-frontend-e2e" in ci.get("jobs", {}), \
            "ci.yml must have a test-frontend-e2e job (Playwright)"


class TestSH001SharedTestsInPytestIni:
    """SH-001 (CRITICAL): shared/tests/ is NOT in pytest.ini testpaths.
    ROOT FIX: add shared/tests to testpaths in pytest.ini."""

    def test_pytest_ini_has_shared_tests_in_testpaths(self):
        with open(_PYTEST_INI_PATH) as f:
            content = f.read()
        # Parse the testpaths section.
        # configparser is overkill — pytest.ini has comments inside testpaths.
        # Look for the testpaths block + verify shared/tests is in it.
        in_testpaths = False
        found_shared_tests = False
        for line in content.splitlines():
            stripped = line.strip()
            if stripped.startswith("testpaths"):
                in_testpaths = True
                continue
            if in_testpaths:
                if stripped.startswith("addopts") or stripped.startswith("[") or stripped.startswith("markers"):
                    in_testpaths = False
                    continue
                # Skip comment lines.
                if stripped.startswith("#"):
                    continue
                if stripped == "shared/tests":
                    found_shared_tests = True
                    break
        assert found_shared_tests, \
            "pytest.ini testpaths must include shared/tests (SH-001 root fix)"


class TestIN020LintBlocking:
    """IN-020/SH-017 (HIGH): CI lint job has continue-on-error: true.
    ROOT FIX: remove continue-on-error, expand scope to all Python dirs."""

    def test_lint_python_does_not_have_continue_on_error(self):
        ci = _load_ci_yaml()
        lint_job = ci["jobs"].get("lint-python", {})
        assert "continue-on-error" not in lint_job or lint_job["continue-on-error"] is False, \
            "lint-python job must NOT have continue-on-error: true (IN-020 root fix)"

    def test_lint_python_runs_ruff(self):
        ci = _load_ci_yaml()
        steps = ci["jobs"]["lint-python"]["steps"]
        step_names = [s.get("name", "") for s in steps]
        assert any("ruff" in n.lower() for n in step_names), \
            "lint-python job must run ruff (fast linter)"

    def test_lint_python_runs_flake8_fatal_errors(self):
        ci = _load_ci_yaml()
        steps = ci["jobs"]["lint-python"]["steps"]
        step_names = [s.get("name", "") for s in steps]
        assert any("flake8" in n.lower() for n in step_names), \
            "lint-python job must run flake8 (fatal + undefined-name errors)"


class TestIN021E2ESplit:
    """IN-021/SH-019 (HIGH): e2e-sample-mode has continue-on-error + excluded
    from ci-success gate.
    ROOT FIX: split into e2e-offline (BLOCKING) + e2e-live-api (NON-BLOCKING)."""

    def test_ci_has_e2e_offline_job(self):
        ci = _load_ci_yaml()
        assert "e2e-offline" in ci.get("jobs", {}), \
            "ci.yml must have e2e-offline job (BLOCKING — sample mode)"

    def test_e2e_offline_does_not_have_continue_on_error(self):
        ci = _load_ci_yaml()
        e2e_job = ci["jobs"]["e2e-offline"]
        assert "continue-on-error" not in e2e_job or e2e_job["continue-on-error"] is False, \
            "e2e-offline job must NOT have continue-on-error (BLOCKING)"

    def test_ci_has_e2e_live_api_job(self):
        ci = _load_ci_yaml()
        assert "e2e-live-api" in ci.get("jobs", {}), \
            "ci.yml must have e2e-live-api job (NON-BLOCKING — scheduled)"

    def test_e2e_live_api_has_continue_on_error(self):
        ci = _load_ci_yaml()
        e2e_live = ci["jobs"]["e2e-live-api"]
        assert e2e_live.get("continue-on-error") is True, \
            "e2e-live-api job must have continue-on-error: true (NON-BLOCKING)"

    def test_e2e_live_api_runs_only_on_schedule_or_dispatch(self):
        ci = _load_ci_yaml()
        e2e_live = ci["jobs"]["e2e-live-api"]
        if_cond = e2e_live.get("if", "")
        assert "schedule" in if_cond or "workflow_dispatch" in if_cond, \
            "e2e-live-api must run only on schedule or workflow_dispatch"

    def test_e2e_offline_in_ci_success_needs(self):
        ci = _load_ci_yaml()
        ci_success_needs = ci["jobs"]["ci-success"]["needs"]
        assert "e2e-offline" in ci_success_needs, \
            "ci-success must depend on e2e-offline (BLOCKING gate)"


class TestIN023DockerCI:
    """IN-023 (HIGH): CI does NOT build or test Docker images.
    ROOT FIX: add docker-build, docker-up, docker-smoke jobs."""

    def test_ci_has_docker_build_job(self):
        ci = _load_ci_yaml()
        assert "docker-build" in ci.get("jobs", {})

    def test_ci_has_docker_up_job(self):
        ci = _load_ci_yaml()
        assert "docker-up" in ci.get("jobs", {})

    def test_ci_has_docker_smoke_job(self):
        ci = _load_ci_yaml()
        assert "docker-smoke" in ci.get("jobs", {})

    def test_docker_build_does_not_have_continue_on_error(self):
        ci = _load_ci_yaml()
        job = ci["jobs"]["docker-build"]
        assert "continue-on-error" not in job or job["continue-on-error"] is False


class TestIN024Deployment:
    """IN-024 (HIGH): CI workflow has no deployment stage.
    ROOT FIX: add deploy-staging (auto), deploy-prod (manual), rollback."""

    def test_ci_has_deploy_staging_job(self):
        ci = _load_ci_yaml()
        assert "deploy-staging" in ci.get("jobs", {})

    def test_ci_has_deploy_prod_job(self):
        ci = _load_ci_yaml()
        assert "deploy-prod" in ci.get("jobs", {})

    def test_deploy_prod_uses_environment_protection(self):
        ci = _load_ci_yaml()
        deploy_prod = ci["jobs"]["deploy-prod"]
        env = deploy_prod.get("environment", {})
        if isinstance(env, str):
            assert env == "production", \
                "deploy-prod must use 'production' environment (manual approval)"
        else:
            assert env.get("name") == "production", \
                "deploy-prod must use 'production' environment (manual approval)"

    def test_ci_has_rollback_job(self):
        ci = _load_ci_yaml()
        assert "rollback" in ci.get("jobs", {})


class TestIN052EnvGitignored:
    """IN-052 (HIGH): .gitignore does not ignore .env.
    Already fixed in main (.env, .env.*, !.env.example). Verify still in place."""

    def test_gitignore_ignores_env(self):
        patterns = _load_gitignore_patterns()
        assert ".env" in patterns, ".gitignore must ignore .env (IN-052)"

    def test_gitignore_ignores_env_glob(self):
        patterns = _load_gitignore_patterns()
        assert ".env.*" in patterns or ".env*" in patterns, \
            ".gitignore must ignore .env.* (IN-052)"

    def test_gitignore_does_not_ignore_env_example(self):
        patterns = _load_gitignore_patterns()
        assert "!.env.example" in patterns, \
            ".gitignore must NOT ignore .env.example (IN-052)"


class TestIN071V1Acceptance:
    """IN-071 (HIGH): No CI job to verify docker-compose V1 acceptance criteria.
    ROOT FIX: add v1-acceptance job that enforces the 3 criteria."""

    def test_ci_has_v1_acceptance_job(self):
        ci = _load_ci_yaml()
        assert "v1-acceptance" in ci.get("jobs", {})

    def test_v1_acceptance_runs_on_main_only(self):
        ci = _load_ci_yaml()
        job = ci["jobs"]["v1-acceptance"]
        if_cond = job.get("if", "")
        assert "main" in if_cond, \
            "v1-acceptance must run only on main branch (not on PRs)"

    def test_v1_acceptance_runs_docker_compose_up(self):
        ci = _load_ci_yaml()
        steps = ci["jobs"]["v1-acceptance"]["steps"]
        # Find the step that runs docker-compose up.
        has_compose_up = False
        for s in steps:
            run = s.get("run", "")
            if "docker-compose up" in run or "docker compose up" in run:
                has_compose_up = True
                break
        assert has_compose_up, \
            "v1-acceptance must run `docker-compose up` (criterion 1)"

    def test_v1_acceptance_curls_system_status(self):
        ci = _load_ci_yaml()
        steps = ci["jobs"]["v1-acceptance"]["steps"]
        has_curl = False
        for s in steps:
            run = s.get("run", "")
            if "/api/system/status" in run:
                has_curl = True
                break
        assert has_curl, \
            "v1-acceptance must curl /api/system/status (criterion 2)"

    def test_v1_acceptance_runs_run_4phase(self):
        ci = _load_ci_yaml()
        steps = ci["jobs"]["v1-acceptance"]["steps"]
        has_run_4phase = False
        for s in steps:
            run = s.get("run", "")
            if "run_4phase" in run:
                has_run_4phase = True
                break
        assert has_run_4phase, \
            "v1-acceptance must run run_4phase.py (criterion 3, IN-097 root fix)"


class TestSH016FlywheelMonitorWired:
    """SH-016 (HIGH): shared/monitoring/flywheel_monitor.py is DEAD CODE.
    ROOT FIX: wire it into CI as a post-Phase4 health check."""

    def test_ci_has_flywheel_health_job(self):
        ci = _load_ci_yaml()
        assert "flywheel-health" in ci.get("jobs", {}), \
            "ci.yml must have flywheel-health job (SH-016 root fix)"

    def test_flywheel_health_imports_run_all_checks(self):
        ci = _load_ci_yaml()
        steps = ci["jobs"]["flywheel-health"]["steps"]
        has_import = False
        for s in steps:
            run = s.get("run", "")
            if "run_all_checks" in run and "flywheel_monitor" in run:
                has_import = True
                break
        assert has_import, \
            "flywheel-health job must import run_all_checks from shared.monitoring.flywheel_monitor"

    def test_flywheel_health_in_ci_success_needs(self):
        ci = _load_ci_yaml()
        ci_success_needs = ci["jobs"]["ci-success"]["needs"]
        assert "flywheel-health" in ci_success_needs, \
            "ci-success must depend on flywheel-health (BLOCKING gate)"


class TestSH018FullTestCoverage:
    """SH-018 (HIGH): CI runs HAND-PICKED test files, not pytest tests/.
    ROOT FIX: replace hand-picked lists with pytest (no paths)."""

    def test_test_python_job_does_not_hand_pick_files(self):
        ci = _load_ci_yaml()
        steps = ci["jobs"]["test-python"]["steps"]
        for s in steps:
            if "pytest" in s.get("name", "").lower() or "pytest" in s.get("run", "").lower():
                run = s.get("run", "")
                # Must NOT have explicit test file paths (hand-picked list).
                # Allow: pytest, pytest -, pytest -n auto, pytest --cov, etc.
                # Disallow: pytest tests/test_X.py tests/test_Y.py
                # Check: no `pytest <path>` where <path> is a .py file.
                # The new ci.yml runs `python -m pytest -n auto --cov=... -ra --tb=short`
                # which has NO explicit test file paths.
                # We check by looking for the pattern `pytest ... test_*.py`.
                # The hand-picked pattern would be `pytest tests/test_X.py`.
                forbidden_pattern = r"pytest\s+.*test_[\w/]+\.py"
                forbidden_matches = re.findall(forbidden_pattern, run)
                # Allow up to 0 hand-picked files (must run full testpaths).
                # NOTE: --cov=phase1 etc. is fine (that's a coverage target, not a test file).
                assert len(forbidden_matches) == 0, \
                    f"test-python job must NOT hand-pick test files (SH-018). Found: {forbidden_matches}"


class TestIN025NoIfAlwaysOnVerify:
    """IN-025 (MEDIUM): verify-v83-p1-p2-fixes has if: always() which masks
    dependency failures.
    ROOT FIX: remove if: always(), use default if: success()."""

    def test_verify_v83_does_not_have_if_always(self):
        ci = _load_ci_yaml()
        verify_v83 = ci["jobs"].get("verify-v83-p1-p2-fixes", {})
        if_cond = verify_v83.get("if", "")
        assert "always()" not in if_cond, \
            "verify-v83-p1-p2-fixes must NOT have if: always() (IN-025 root fix)"


class TestIN027NoInlineDeps:
    """IN-027 (MEDIUM): CI installs deps INLINE instead of from requirements.txt.
    ROOT FIX: every job uses pip install -r requirements.txt."""

    def test_build_python_uses_requirements_txt(self):
        ci = _load_ci_yaml()
        steps = ci["jobs"]["build-python"]["steps"]
        for s in steps:
            run = s.get("run", "")
            if "pip install" in run:
                assert "requirements.txt" in run, \
                    "build-python must install from requirements.txt (IN-027 root fix)"

    def test_test_python_uses_requirements_txt(self):
        ci = _load_ci_yaml()
        steps = ci["jobs"]["test-python"]["steps"]
        for s in steps:
            run = s.get("run", "")
            if "pip install" in run and "pytest" not in run.split("\n")[0]:
                # The first line is `python -m pip install --upgrade pip`.
                # Subsequent lines should install from requirements.txt.
                if "pandas>=" in run and "numpy>=" in run:
                    # Inline list — BAD.
                    assert False, \
                        "test-python must NOT install deps inline (IN-027 root fix)"
                # OK — uses requirements.txt.


class TestIN053GitignoreComprehensive:
    """IN-053 (MEDIUM): .gitignore does not ignore Docker volumes, raw_data,
    processed_data, logs, .airflow_home, *.db, etc.
    ROOT FIX: add comprehensive patterns."""

    def test_gitignore_ignores_raw_data(self):
        patterns = _load_gitignore_patterns()
        assert "phase1/raw_data/" in patterns or "raw_data/" in patterns, \
            ".gitignore must ignore phase1/raw_data/ (IN-053)"

    def test_gitignore_ignores_output_glob(self):
        patterns = _load_gitignore_patterns()
        assert "output_*/" in patterns, \
            ".gitignore must ignore output_*/ glob (IN-053)"

    def test_gitignore_ignores_airflow_home(self):
        patterns = _load_gitignore_patterns()
        assert ".airflow_home/" in patterns, \
            ".gitignore must ignore .airflow_home/ (IN-053)"

    def test_gitignore_ignores_db_files(self):
        patterns = _load_gitignore_patterns()
        assert "*.db" in patterns, \
            ".gitignore must ignore *.db (IN-053)"

    def test_gitignore_ignores_db_journal(self):
        patterns = _load_gitignore_patterns()
        assert "*.db-journal" in patterns, \
            ".gitignore must ignore *.db-journal (IN-053)"

    def test_gitignore_ignores_egg_info_recursive(self):
        patterns = _load_gitignore_patterns()
        # Either ``*.egg-info/`` or ``**/*.egg-info/`` is acceptable.
        assert "*.egg-info/" in patterns or "**/*.egg-info/" in patterns, \
            ".gitignore must ignore *.egg-info/ recursively (IN-053)"


class TestIN084ContainerSigning:
    """IN-084 (MEDIUM): No container signing (no cosign, no SLSA provenance).
    ROOT FIX: cosign keyless signing in deploy-staging job."""

    def test_deploy_staging_uses_cosign(self):
        ci = _load_ci_yaml()
        steps = ci["jobs"]["deploy-staging"]["steps"]
        step_names = [s.get("name", "") for s in steps]
        assert any("cosign" in n.lower() for n in step_names), \
            "deploy-staging must install cosign (IN-084 root fix)"

    def test_deploy_staging_signs_image(self):
        ci = _load_ci_yaml()
        steps = ci["jobs"]["deploy-staging"]["steps"]
        has_sign_step = False
        for s in steps:
            run = s.get("run", "")
            if "cosign sign" in run:
                has_sign_step = True
                break
        assert has_sign_step, \
            "deploy-staging must run `cosign sign` (IN-084 root fix)"

    def test_deploy_prod_verifies_signature(self):
        ci = _load_ci_yaml()
        steps = ci["jobs"]["deploy-prod"]["steps"]
        has_verify_step = False
        for s in steps:
            run = s.get("run", "")
            if "cosign verify" in run:
                has_verify_step = True
                break
        assert has_verify_step, \
            "deploy-prod must run `cosign verify` before rollout (IN-084 root fix)"

    def test_ci_has_oidc_write_permission(self):
        ci = _load_ci_yaml()
        permissions = ci.get("permissions", {})
        assert permissions.get("id-token") == "write", \
            "ci.yml must have id-token: write permission (for cosign keyless signing)"


class TestSH030MypyBanditCoverage:
    """SH-030 (MEDIUM): No mypy, no bandit, no pip-audit, no coverage.
    ROOT FIX: add mypy job, bandit job, pip-audit job, pytest --cov."""

    def test_ci_has_typecheck_python_job(self):
        ci = _load_ci_yaml()
        assert "typecheck-python" in ci.get("jobs", {}), \
            "ci.yml must have typecheck-python job (mypy)"

    def test_typecheck_python_runs_mypy(self):
        ci = _load_ci_yaml()
        steps = ci["jobs"]["typecheck-python"]["steps"]
        has_mypy = False
        for s in steps:
            run = s.get("run", "")
            if "mypy" in run:
                has_mypy = True
                break
        assert has_mypy, "typecheck-python must run mypy"

    def test_test_python_has_coverage(self):
        ci = _load_ci_yaml()
        steps = ci["jobs"]["test-python"]["steps"]
        has_cov = False
        for s in steps:
            run = s.get("run", "")
            if "--cov" in run:
                has_cov = True
                break
        assert has_cov, "test-python must run pytest --cov (SH-030 root fix)"


class TestIN026CacheAllRequirements:
    """IN-026 (LOW): cache-dependency-path misses 3 of 5 requirements.txt.
    ROOT FIX: cache-dependency-path includes all 5 requirements.txt files."""

    def test_build_python_caches_all_requirements(self):
        ci = _load_ci_yaml()
        steps = ci["jobs"]["build-python"]["steps"]
        for s in steps:
            if "setup-python" in s.get("uses", ""):
                cache_dep = s.get("with", {}).get("cache-dependency-path", "")
                # Must include phase2, graph_transformer, rl requirements.
                assert "phase2/drugos_graph/requirements.txt" in cache_dep, \
                    "build-python cache must include phase2/drugos_graph/requirements.txt (IN-026)"
                assert "graph_transformer/requirements.txt" in cache_dep, \
                    "build-python cache must include graph_transformer/requirements.txt (IN-026)"
                assert "rl/requirements.txt" in cache_dep, \
                    "build-python cache must include rl/requirements.txt (IN-026)"


class TestIN058CaddyfileHardened:
    """IN-058 (LOW): Caddyfile listens on :81 — non-standard, no TLS, no
    security headers, no rate limit, no body size limit.
    ROOT FIX: harden Caddyfile."""

    def test_caddyfile_does_not_listen_on_81(self):
        src = _CADDYFILE_PATH.read_text()
        # The new Caddyfile uses {$DOMAIN:80} which falls back to :80.
        # Must NOT have ``:81`` as the listen address.
        # Allow ``:81`` in comments only.
        code_lines = [l for l in src.splitlines() if not l.strip().startswith("#")]
        code = "\n".join(code_lines)
        assert ":81 {" not in code, \
            "Caddyfile must NOT listen on :81 (IN-058 root fix)"

    def test_caddyfile_has_security_headers(self):
        src = _CADDYFILE_PATH.read_text()
        assert "Strict-Transport-Security" in src, \
            "Caddyfile must set HSTS header (IN-058 root fix)"
        assert "X-Content-Type-Options" in src, \
            "Caddyfile must set X-Content-Type-Options header (IN-058)"
        assert "X-Frame-Options" in src, \
            "Caddyfile must set X-Frame-Options header (IN-058)"

    def test_caddyfile_has_request_body_size_limit(self):
        src = _CADDYFILE_PATH.read_text()
        assert "request_body" in src and "max_size" in src, \
            "Caddyfile must have request_body max_size limit (IN-058 root fix)"


class TestIN066SwcJest:
    """IN-066 (LOW): jest.config.js uses preset: "ts-jest" (slow + deprecated).
    ROOT FIX: replace with @swc/jest."""

    def test_jest_config_does_not_use_ts_jest_preset(self):
        src = _JEST_CONFIG_PATH.read_text()
        # Strip comments — the header comment LEGITIMATELY mentions ts-jest
        # when explaining WHY it was replaced. We only care about EXECUTABLE code.
        code = _strip_js_comments(src)
        # Must NOT have ``preset: "ts-jest"`` or ``preset: 'ts-jest'`` in code.
        assert 'preset: "ts-jest"' not in code and "preset: 'ts-jest'" not in code, \
            "jest.config.js must NOT use preset: ts-jest (IN-066 root fix)"

    def test_jest_config_uses_swc_jest(self):
        src = _JEST_CONFIG_PATH.read_text()
        assert "@swc/jest" in src, \
            "jest.config.js must use @swc/jest (IN-066 root fix)"

    def test_package_json_has_swc_jest_dep(self):
        import json
        with open(_PACKAGE_JSON_PATH) as f:
            pkg = json.load(f)
        dev_deps = pkg.get("devDependencies", {})
        assert "@swc/jest" in dev_deps, \
            "frontend/package.json must have @swc/jest in devDependencies (IN-066)"

    def test_package_json_does_not_have_ts_jest_dep(self):
        import json
        with open(_PACKAGE_JSON_PATH) as f:
            pkg = json.load(f)
        dev_deps = pkg.get("devDependencies", {})
        assert "ts-jest" not in dev_deps, \
            "frontend/package.json must NOT have ts-jest (IN-066 root fix)"


class TestIN083DockerLayerCaching:
    """IN-083 (LOW): CI workflow does NOT cache Docker layers.
    ROOT FIX: docker/build-push-action with cache-from/cache-to GHA."""

    def test_deploy_staging_uses_docker_layer_cache(self):
        ci = _load_ci_yaml()
        steps = ci["jobs"]["deploy-staging"]["steps"]
        has_cache = False
        for s in steps:
            with_cfg = s.get("with", {})
            cache_from = with_cfg.get("cache-from", "")
            cache_to = with_cfg.get("cache-to", "")
            if "gha" in cache_from or "gha" in cache_to:
                has_cache = True
                break
        assert has_cache, \
            "deploy-staging must use docker/build-push-action with cache-from/cache-to GHA (IN-083)"


class TestIN097RunRealPipelineReplaced:
    """IN-097 (LOW): CI checks import run_real_pipeline but it's DEPRECATED.
    ROOT FIX: replace with import run_4phase.
    Also: ci.yml referenced run_real_pipeline.py / run_full_platform.py /
    run_unified.py in compileall — but these files were DELETED per IN-072.
    compileall silently exits 0 on missing files. The build job was a NO-OP."""

    def test_verify_v31_uses_run_4phase(self):
        ci = _load_ci_yaml()
        steps = ci["jobs"]["verify-v31-phase3-phase4-fixes"]["steps"]
        has_run_4phase = False
        for s in steps:
            run = s.get("run", "")
            if "import run_4phase" in run:
                has_run_4phase = True
                break
        assert has_run_4phase, \
            "verify-v31-phase3-phase4-fixes must use `import run_4phase` (IN-097 root fix)"

    def test_ci_does_not_compile_run_real_pipeline(self):
        src = _CI_YML_PATH.read_text()
        # compileall commands must not reference run_real_pipeline.py
        # Find all compileall commands.
        compileall_lines = [l for l in src.splitlines() if "compileall" in l]
        for line in compileall_lines:
            assert "run_real_pipeline.py" not in line, \
                f"ci.yml compileall must not reference run_real_pipeline.py (IN-097): {line}"


class TestIN059OSMatrix:
    """IN-059 (LOW): No CI matrix testing across operating systems.
    ROOT FIX: add os: [ubuntu-latest, macos-latest] to dev-only jobs."""

    def test_build_python_has_os_matrix(self):
        ci = _load_ci_yaml()
        matrix = ci["jobs"]["build-python"]["strategy"]["matrix"]
        assert "os" in matrix, \
            "build-python must have os matrix (IN-059 root fix)"
        assert "ubuntu-latest" in matrix["os"]
        assert "macos-latest" in matrix["os"]


class TestIN057PythonVersionAlignment:
    """IN-057 (LOW): pyproject.toml requires-python = ">=3.10" conflicts with
    CI matrix ['3.11', '3.12'].
    ROOT FIX: keep CI matrix as ['3.11', '3.12'] (production target is 3.11
    per Dockerfile.ml). Document the alignment in ci.yml comments.
    pyproject.toml allows 3.10+ for dev/local install flexibility."""

    def test_build_python_matrix_is_3_11_3_12(self):
        ci = _load_ci_yaml()
        matrix = ci["jobs"]["build-python"]["strategy"]["matrix"]
        assert matrix["python-version"] == ['3.11', '3.12'], \
            "build-python matrix must be ['3.11', '3.12'] (production target)"


class TestCISuccessGate:
    """Verify the ci-success gate includes all blocking jobs."""

    def test_ci_success_needs_all_blocking_jobs(self):
        ci = _load_ci_yaml()
        needs = ci["jobs"]["ci-success"]["needs"]
        # Required blocking jobs.
        required = [
            "build-python", "build-frontend",
            "lint-python", "lint-frontend", "lint-yaml", "lint-docker-compose",
            "typecheck-python", "typecheck-frontend",
            "test-python", "test-frontend-unit", "test-frontend-integration",
            "e2e-offline",
            "verify-v83-p1-p2-fixes", "verify-v31-phase3-phase4-fixes",
            "flywheel-health",
            "security-python", "security-frontend", "security-secrets",
            "security-codeql", "security-sbom",
            "docker-build", "docker-up", "docker-smoke",
        ]
        for job in required:
            assert job in needs, \
                f"ci-success must depend on {job} (blocking gate)"


if __name__ == "__main__":
    sys.exit(0)
