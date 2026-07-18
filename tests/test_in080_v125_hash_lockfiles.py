"""IN-080 v125 FORENSIC ROOT FIX verification tests.

Verifies that:
1. All 5 requirements.lock files exist (root + phase1 + phase2/drugos_graph
   + graph_transformer + rl).
2. Each lockfile contains sha256 hashes for the dependencies.
3. The Dockerfiles that install requirements use --require-hashes when a
   .lock file is present, OR fall back to plain requirements.txt for dev.
4. The pip index URL is pinned to https://pypi.org/simple/ in all
   Dockerfiles (prevents a misconfigured pip.conf from silently using an
   untrusted mirror).
5. The Makefile has valid syntax (uses TABS, not spaces — a pre-existing
   bug that broke ALL make commands before v125).
6. The Makefile exposes `make requirements-lock`, `make requirements-verify`,
   and `make requirements-audit` targets.

These tests are the CI gate for IN-080. If any of them fail, the build
is broken and the merge is blocked.
"""
from __future__ import annotations

import re
import subprocess
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]

# All 5 requirements files that MUST have a corresponding .lock file.
REQUIREMENTS_FILES = [
    "requirements.txt",
    "phase1/requirements.txt",
    "phase2/drugos_graph/requirements.txt",
    "graph_transformer/requirements.txt",
    "rl/requirements.txt",
]

# All Dockerfiles that install Python requirements.
DOCKERFILES_WITH_REQUIREMENTS = [
    "Dockerfile.airflow",
    "phase1/docker/Dockerfile.airflow",
    "Dockerfile.ml",
]


# =============================================================================
# Test 1: All 5 .lock files exist
# =============================================================================

@pytest.mark.parametrize("req_rel_path", REQUIREMENTS_FILES)
def test_lockfile_exists(req_rel_path: str) -> None:
    """IN-080: each requirements.txt MUST have a corresponding .lock file.

    The .lock file contains sha256 hashes for every dependency, enabling
    `pip install --require-hashes` for supply-chain integrity.
    """
    req_file = REPO_ROOT / req_rel_path
    if not req_file.exists():
        pytest.skip(f"{req_rel_path} does not exist (skipped)")
    lock_file = req_file.with_suffix(".lock")
    assert lock_file.exists(), (
        f"IN-080 REGRESSION: {lock_file} does not exist. "
        f"Run `make requirements-lock` to generate hash-pinned lockfiles. "
        f"Without .lock files, the Dockerfiles cannot use --require-hashes, "
        f"leaving the supply chain vulnerable to wheel substitution attacks."
    )


# =============================================================================
# Test 2: Each .lock file contains sha256 hashes
# =============================================================================

@pytest.mark.parametrize("req_rel_path", REQUIREMENTS_FILES)
def test_lockfile_contains_hashes(req_rel_path: str) -> None:
    """IN-080: each .lock file MUST contain sha256 hashes for the deps."""
    req_file = REPO_ROOT / req_rel_path
    lock_file = req_file.with_suffix(".lock")
    if not lock_file.exists():
        pytest.skip(f"{lock_file} does not exist (skipped)")

    content = lock_file.read_text(encoding="utf-8")
    hash_count = content.count("--hash=sha256:")
    assert hash_count > 0, (
        f"IN-080 REGRESSION: {lock_file} contains NO sha256 hashes. "
        f"The lockfile is useless without hashes -- pip install --require-hashes "
        f"would fail. Run `make requirements-lock` to regenerate."
    )
    # Each hash line should match the format: --hash=sha256:<64 hex chars>
    hash_pattern = re.compile(r"--hash=sha256:[0-9a-f]{64}")
    matches = hash_pattern.findall(content)
    assert len(matches) == hash_count, (
        f"IN-080 REGRESSION: {lock_file} has {hash_count} --hash=sha256: lines "
        f"but only {len(matches)} match the expected format (64 hex chars). "
        f"Some hashes may be malformed."
    )


# =============================================================================
# Test 3: Lockfile regenerator script exists
# =============================================================================

def test_lockfile_generator_scripts_exist() -> None:
    """IN-080: the lockfile generator scripts MUST exist in scripts/."""
    gen1 = REPO_ROOT / "scripts" / "generate_lockfiles.py"
    gen2 = REPO_ROOT / "scripts" / "generate_lockfiles_from_root.py"
    assert gen1.exists(), (
        f"IN-080: {gen1} does not exist. The lockfile generator is the "
        f"canonical way to regenerate .lock files when requirements.txt changes."
    )
    assert gen2.exists(), (
        f"IN-080: {gen2} does not exist. This script reuses hashes from the "
        f"root lockfile for the per-phase lockfiles (faster than re-downloading)."
    )


# =============================================================================
# Test 4: Dockerfiles use --require-hashes (when .lock present)
# =============================================================================

@pytest.mark.parametrize("dockerfile_rel_path", DOCKERFILES_WITH_REQUIREMENTS)
def test_dockerfile_uses_require_hashes(dockerfile_rel_path: str) -> None:
    """IN-080: Dockerfiles that install requirements MUST support --require-hashes.

    The Dockerfile.airflow and phase1/docker/Dockerfile.airflow install
    requirements.txt via `pip install -r`. They MUST be updated to:
      - COPY the .lock file alongside the .txt file
      - Use `pip install --require-hashes -r requirements.lock` when the
        .lock file is present (with fallback to plain requirements.txt
        for dev environments without a .lock file).
    """
    dockerfile = REPO_ROOT / dockerfile_rel_path
    if not dockerfile.exists():
        pytest.skip(f"{dockerfile_rel_path} does not exist (skipped)")

    content = dockerfile.read_text(encoding="utf-8")
    # The Dockerfile MUST mention --require-hashes (the install pattern).
    # We don't require it on EVERY pip install (the Dockerfile.ml uses
    # exact-version pins which is a partial mitigation), but the
    # requirements-install Dockerfiles MUST use it.
    if "requirements.txt" in content or "requirements.lock" in content:
        assert "--require-hashes" in content, (
            f"IN-080 REGRESSION: {dockerfile_rel_path} installs requirements "
            f"but does NOT use --require-hashes. Add the lockfile install "
            f"pattern: COPY requirements.lock, then "
            f"`pip install --require-hashes -r requirements.lock`."
        )


# =============================================================================
# Test 5: pip index URL is pinned in all Dockerfiles
# =============================================================================

@pytest.mark.parametrize("dockerfile_rel_path", DOCKERFILES_WITH_REQUIREMENTS)
def test_dockerfile_pins_pypi_index(dockerfile_rel_path: str) -> None:
    """IN-080: pip install commands MUST pin --index-url to a trusted mirror.

    Without --index-url, a misconfigured pip.conf can silently redirect
    pip to an untrusted mirror (e.g., a typosquatted PyPI domain). The
    Dockerfile MUST explicitly set --index-url https://pypi.org/simple/
    (or the pytorch.org mirror for torch wheels).
    """
    dockerfile = REPO_ROOT / dockerfile_rel_path
    if not dockerfile.exists():
        pytest.skip(f"{dockerfile_rel_path} does not exist (skipped)")

    content = dockerfile.read_text(encoding="utf-8")
    # The Dockerfile must mention either pypi.org or download.pytorch.org
    # (the two trusted mirrors used in this project).
    has_pinned_index = (
        "https://pypi.org/simple/" in content
        or "https://download.pytorch.org/whl/" in content
    )
    assert has_pinned_index, (
        f"IN-080 REGRESSION: {dockerfile_rel_path} does not pin the pip "
        f"index URL. Add `--index-url https://pypi.org/simple/` to every "
        f"`pip install` command."
    )


# =============================================================================
# Test 6: Makefile uses TABS (not spaces) -- CRITICAL pre-existing bug
# =============================================================================

def test_makefile_uses_tabs() -> None:
    """IN-080 v125 CRITICAL FIX: the Makefile MUST use TABS, not spaces.

    The pre-v125 Makefile used 8 spaces instead of tabs, which broke ALL
    `make` commands with `missing separator (did you mean TAB instead of
    8 spaces?)`. This was a silent CI/build blocker that no prior agent
    detected. The fix uses `unexpand -t 8 --first-only` to convert
    leading 8-space groups to tabs.
    """
    makefile = REPO_ROOT / "Makefile"
    assert makefile.exists(), "Makefile does not exist at repo root"

    # Run `make -n requirements-verify` -- if the Makefile uses spaces,
    # this exits non-zero with "missing separator".
    result = subprocess.run(
        ["make", "-n", "requirements-verify"],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        timeout=10,
    )
    assert result.returncode == 0, (
        f"IN-080 v125 REGRESSION: Makefile syntax is broken (make exited "
        f"with {result.returncode}). This is the pre-v125 bug where the "
        f"Makefile used 8 spaces instead of TABS. STDERR:\n{result.stderr}"
    )


# =============================================================================
# Test 7: Makefile has requirements-lock / requirements-verify / requirements-audit
# =============================================================================

@pytest.mark.parametrize(
    "target",
    ["requirements-lock", "requirements-verify", "requirements-audit"],
)
def test_makefile_has_lockfile_targets(target: str) -> None:
    """IN-080: the Makefile MUST expose lockfile management targets."""
    makefile = REPO_ROOT / "Makefile"
    content = makefile.read_text(encoding="utf-8")
    # The target should be defined as `target:` at the start of a line.
    pattern = re.compile(rf"^{re.escape(target)}:", re.MULTILINE)
    assert pattern.search(content), (
        f"IN-080 REGRESSION: Makefile is missing the `{target}` target. "
        f"This target is the canonical way to manage hash-pinned lockfiles."
    )


# =============================================================================
# Test 8: verify_requirements_security.py script exists
# =============================================================================

def test_verify_requirements_security_script_exists() -> None:
    """IN-080: the requirements security verifier script MUST exist."""
    script = REPO_ROOT / "scripts" / "verify_requirements_security.py"
    assert script.exists(), (
        f"IN-080: {script} does not exist. This script enforces the "
        f"IN-080 interim controls (upper bounds, no duplicates, no "
        f"non-existent versions, apache-airflow pinned to ==2.10.5)."
    )


# =============================================================================
# Test 9: No requirements.txt uses non-existent package versions
# =============================================================================

@pytest.mark.parametrize("req_rel_path", REQUIREMENTS_FILES)
def test_no_nonexistent_package_versions(req_rel_path: str) -> None:
    """IN-080: requirements files MUST NOT reference non-existent versions.

    Pre-v122 fixes had references like `torch>=2.13.0` (latest is 2.5.x),
    `scikit-learn>=1.9.0` (latest is 1.5.x), `scipy>=1.18.0` (latest is
    1.14.x), `rdkit>=2026.3.4` (doesn't exist). These caused `pip install`
    to fail with `No matching distribution found`.

    This test checks ONLY non-comment lines (comments may reference old
    version pins as part of the fix documentation).
    """
    req_file = REPO_ROOT / req_rel_path
    if not req_file.exists():
        pytest.skip(f"{req_rel_path} does not exist (skipped)")

    content = req_file.read_text(encoding="utf-8")
    # Strip comments (everything after #) and only check actual dep lines.
    actual_deps = []
    for raw_line in content.splitlines():
        line = raw_line.split("#", 1)[0].strip()
        if not line or line.startswith("-"):
            continue
        # Strip environment markers
        line = line.split(";", 1)[0].strip()
        if line:
            actual_deps.append(line)
    actual_content = "\n".join(actual_deps)

    # Known-bad version pins that were fixed in v122.
    bad_patterns = [
        r"torch\s*>=\s*2\.(6|7|8|9|1[0-9])",  # torch >= 2.6+ doesn't exist yet
        r"scikit-learn\s*>=\s*1\.(6|7|8|9|1[0-9])",  # sklearn >= 1.6 doesn't exist
        r"scipy\s*>=\s*1\.(15|16|17|18|19|2[0-9])",  # scipy >= 1.15 doesn't exist
        r"rdkit\s*>=\s*202[5-9]",  # rdkit >= 2025+ doesn't exist
        r"fastapi\s*>=\s*0\.1[2-9][0-9]",  # fastapi >= 0.120 doesn't exist
        r"certifi\s*>=\s*202[5-9]",  # certifi >= 2025 doesn't exist
        r"pyyaml\s*>=\s*6\.[1-9]",  # pyyaml >= 6.1 doesn't exist
        r"prometheus-client\s*>=\s*0\.(2[2-9]|[3-9])",  # prom-client >= 0.22 doesn't exist
        r"filelock\s*>=\s*3\.(1[7-9]|[2-9])",  # filelock >= 3.17 doesn't exist
        r"python-dotenv\s*>=\s*1\.[1-9]",  # python-dotenv >= 1.1 doesn't exist
        r"numpy\s*>=\s*2\.[2-9]",  # numpy >= 2.2 doesn't exist
        r"sqlalchemy\s*>=\s*2\.0\.(3[7-9]|[4-9][0-9])",  # sqlalchemy >= 2.0.37 doesn't exist
    ]
    for pattern in bad_patterns:
        match = re.search(pattern, actual_content)
        assert match is None, (
            f"IN-080 REGRESSION: {req_rel_path} contains a non-existent "
            f"version pin: '{match.group(0)}' matches pattern '{pattern}'. "
            f"This causes `pip install` to fail with "
            f"`No matching distribution found`."
        )


# =============================================================================
# Test 10: apache-airflow is pinned to ==2.10.5 (exact base image version)
# =============================================================================

@pytest.mark.parametrize(
    "req_rel_path",
    ["requirements.txt", "phase1/requirements.txt"],
)
def test_apache_airflow_exact_pin(req_rel_path: str) -> None:
    """IN-068 + IN-080: apache-airflow MUST be pinned to ==2.10.5 (exact).

    The base image apache/airflow:2.10.5-python3.11 ships with Airflow
    2.10.5 pre-installed. The requirements.txt MUST pin to the EXACT
    version (==2.10.5) so pip does not upgrade or downgrade it.
    """
    req_file = REPO_ROOT / req_rel_path
    if not req_file.exists():
        pytest.skip(f"{req_rel_path} does not exist (skipped)")

    content = req_file.read_text(encoding="utf-8")
    # Find the apache-airflow line (not in a comment).
    for line in content.splitlines():
        stripped = line.split("#", 1)[0].strip()
        if stripped.startswith("apache-airflow"):
            assert "==" in stripped, (
                f"IN-068 REGRESSION: {req_rel_path} has apache-airflow pin "
                f"without == (exact match): '{stripped}'. The pin MUST be "
                f"apache-airflow==2.10.5 to match the base image."
            )
            assert "2.10.5" in stripped, (
                f"IN-068 REGRESSION: {req_rel_path} has apache-airflow pinned "
                f"to a version other than 2.10.5: '{stripped}'. The base "
                f"image apache/airflow:2.10.5-python3.11 REQUIRES ==2.10.5."
            )
            return
    # If we get here, apache-airflow is not in the file -- that's OK for
    # files that don't need Airflow (graph_transformer, rl, phase2).
    if "phase1" in req_rel_path or req_rel_path == "requirements.txt":
        pytest.fail(
            f"IN-068 REGRESSION: {req_rel_path} does not contain apache-airflow. "
            f"The phase1 + root requirements MUST pin apache-airflow==2.10.5."
        )
