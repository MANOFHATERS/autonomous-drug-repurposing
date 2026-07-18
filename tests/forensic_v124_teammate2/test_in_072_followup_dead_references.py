"""Forensic test for IN-072 follow-up REAL ROOT FIX (v124).

IN-072 deleted the deprecated legacy runner files:
  - ``scripts/legacy/run_real_pipeline.py``
  - ``scripts/legacy/run_full_platform.py``
  - ``scripts/legacy/run_unified.py``
  - ``run_real_pipeline.py`` (root)
  - ``run_full_platform.py`` (root)
  - ``run_unified.py`` (root)

The canonical runner per ORCH-003 is ``run_4phase.py``.

The IN-072 fix introduced a NEW bug (the audit warned: "many of these
fixes introduced NEW bugs while patching old ones"):
  - ``.github/workflows/ci.yml`` build job still referenced the deleted
    files in ``compileall`` (silently exited 0 with "Can't list" warnings)
  - ``.github/workflows/ci.yml`` Phase 3/4 build job still referenced
    ``run_real_pipeline.py``
  - ``.github/workflows/ci.yml`` V31-8 verification step tried to
    ``import run_real_pipeline`` (which would fail with ImportError)
  - ``phase2/service.py`` user-facing error messages told operators to
    run ``python run_full_platform.py --phase 1`` (a non-existent file)

These tests verify the v124 follow-up root fix:
  1. No live code references the deleted legacy runner files.
  2. CI build jobs only compile files that actually exist.
  3. V31-8 verification imports the canonical ``run_4phase`` module.
  4. ``phase2/service.py`` error messages point to ``run_4phase.py``.
  5. The Makefile targets are aliases (not invocations of deleted files).
"""
from __future__ import annotations

import re
import subprocess
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
CI_WORKFLOW = REPO_ROOT / ".github" / "workflows" / "ci.yml"
SERVICE_PY = REPO_ROOT / "phase2" / "service.py"
MAKEFILE = REPO_ROOT / "Makefile"

# The deleted legacy runner files (per IN-072).
DELETED_FILES = [
    "run_real_pipeline.py",
    "run_full_platform.py",
    "run_unified.py",
]


def _git_tracked_files() -> list[str]:
    """Return the list of files tracked by git (relative to repo root)."""
    result = subprocess.run(
        ["git", "ls-files"],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        check=True,
    )
    return [line.strip() for line in result.stdout.splitlines() if line.strip()]


# ─── IN-072.1: deleted legacy runner files are actually gone ──────────────


def test_in_072_legacy_runners_not_tracked() -> None:
    """The 3 deleted legacy runner files must NOT be tracked in git.

    IN-072 deleted these files. This test catches any regression that
    re-adds them.
    """
    tracked = _git_tracked_files()
    legacy_tracked = [f for f in tracked if f in DELETED_FILES]
    assert not legacy_tracked, (
        f"IN-072 regression: the following deleted legacy runner files "
        f"are tracked in git again: {legacy_tracked}. The canonical "
        f"runner is `run_4phase.py` per ORCH-003. Delete the legacy files."
    )


def test_in_072_scripts_legacy_dir_not_tracked() -> None:
    """The ``scripts/legacy/`` directory must NOT exist in git.

    IN-072 deleted the entire ``scripts/legacy/`` directory (3 deprecated
    runner files). This test catches any regression that re-creates it.
    """
    tracked = _git_tracked_files()
    legacy_dir_files = [f for f in tracked if f.startswith("scripts/legacy/")]
    assert not legacy_dir_files, (
        f"IN-072 regression: the scripts/legacy/ directory is tracked in "
        f"git again: {legacy_dir_files}. IN-072 deleted this directory. "
        f"The canonical runner is `run_4phase.py`."
    )


# ─── IN-072.2: CI build job does NOT reference deleted files ──────────────


def test_in_072_ci_build_job_no_dead_references() -> None:
    """The CI build job's ``compileall`` MUST NOT reference deleted files.

    The IN-072 fix deleted the legacy runners, but the CI build job
    still listed them in the ``compileall`` command. ``compileall``
    silently exits 0 with "Can't list" warnings -- CI was "green" but
    the build check was effectively a no-op for the legacy entries.

    This is the "fixes introduced NEW bugs while patching old ones"
    pattern the audit warned about.
    """
    if not CI_WORKFLOW.exists():
        pytest.skip("CI workflow not found")
    content = CI_WORKFLOW.read_text()
    # Find the build job's compileall step (Phase 1/2 build).
    # The compileall command should NOT reference the deleted files.
    # We check the FULL workflow (not just the build job) because the
    # Phase 3/4 build job also had the same bug.
    for deleted in DELETED_FILES:
        # Look for the deleted filename as a compileall argument
        # (NOT in a comment). The pattern is: ``compileall -q ... {file}``
        # where {file} is one of the deleted files.
        # We accept the filename appearing in COMMENTS (lines starting
        # with #) -- those are historical context, not executable refs.
        for line in content.splitlines():
            stripped = line.lstrip()
            if stripped.startswith("#"):
                continue
            if "compileall" in line and deleted in line:
                pytest.fail(
                    f"IN-072 follow-up regression: the CI workflow still "
                    f"references `{deleted}` in a compileall command (non-"
                    f"comment line: {line.strip()!r}). This file was "
                    f"DELETED by IN-072. `compileall` silently exits 0 "
                    f"with 'Can't list' warnings, so CI is 'green' but "
                    f"the build check is a no-op for this entry. Remove "
                    f"the dead reference; only compile `run_4phase.py` "
                    f"(the canonical runner)."
                )


def test_in_072_ci_build_job_compiles_run_4phase() -> None:
    """The CI build job MUST compile ``run_4phase.py`` (the canonical runner).

    The compileall command is multi-line (backslash continuation), so we
    check the full workflow content (not just single lines) for the
    pattern `compileall -q ... run_4phase.py`.
    """
    if not CI_WORKFLOW.exists():
        pytest.skip("CI workflow not found")
    content = CI_WORKFLOW.read_text()
    # Find each compileall command block (may span multiple lines via
    # backslash continuation). Then check if `run_4phase.py` appears in
    # ANY compileall block.
    compileall_blocks = re.findall(
        r"python -m compileall\s+-q\s*\\?\s*\n(?:[ \t]+[^\n]*\n)*?",
        content,
    )
    # The above regex is fragile; simpler: just check that SOME compileall
    # command in the workflow includes run_4phase.py. The compileall
    # commands are bounded by `compileall -q` and the next `echo`/step.
    compileall_pattern = re.compile(
        r"python -m compileall\b.*?(?=echo |python |\Z)",
        re.DOTALL,
    )
    found = False
    for match in compileall_pattern.finditer(content):
        block = match.group(0)
        if "run_4phase.py" in block:
            found = True
            break
    assert found, (
        "IN-072: the CI build job does NOT compile `run_4phase.py` (the "
        "canonical runner per ORCH-003). Add `run_4phase.py` to the "
        "compileall command in the build job."
    )


# ─── IN-072.3: V31-8 verification imports run_4phase (not run_real_pipeline) ─


def test_in_072_v31_8_imports_run_4phase() -> None:
    """The V31-8 verification step MUST import ``run_4phase`` (not ``run_real_pipeline``).

    The IN-072 fix deleted ``run_real_pipeline.py`` but the V31-8 step
    still tried to ``import run_real_pipeline``. The surrounding
    try/except would catch the ImportError and mark the check as failed
    -- a real CI regression.
    """
    if not CI_WORKFLOW.exists():
        pytest.skip("CI workflow not found")
    content = CI_WORKFLOW.read_text()
    # The V31-8 step should import run_4phase, not run_real_pipeline.
    # Look for `import run_4phase` (executable, not in a comment).
    found_run_4phase = False
    for line in content.splitlines():
        stripped = line.lstrip()
        if stripped.startswith("#"):
            continue
        if "import run_4phase" in line:
            found_run_4phase = True
            break
    assert found_run_4phase, (
        "IN-072 follow-up regression: the V31-8 verification step does "
        "NOT import `run_4phase`. The canonical runner per ORCH-003 / "
        "IN-072 is `run_4phase` (the deleted `run_real_pipeline.py` is "
        "no longer importable). Update the V31-8 step to "
        "`import run_4phase`."
    )

    # Also verify NO executable `import run_real_pipeline` line exists.
    for line in content.splitlines():
        stripped = line.lstrip()
        if stripped.startswith("#"):
            continue
        if "import run_real_pipeline" in line:
            pytest.fail(
                f"IN-072 follow-up regression: the CI workflow has an "
                f"executable `import run_real_pipeline` line "
                f"({line.strip()!r}). This module was DELETED by IN-072. "
                f"The import would fail with ImportError. Update to "
                f"`import run_4phase`."
            )


# ─── IN-072.4: phase2/service.py error messages point to run_4phase ───────


def test_in_072_service_no_dead_run_full_platform_references() -> None:
    """``phase2/service.py`` MUST NOT tell operators to run ``run_full_platform.py``.

    The IN-072 fix deleted ``run_full_platform.py`` but the service's
    user-facing error messages still told operators to run it. An
    operator hitting the error would try to run a non-existent file.
    """
    if not SERVICE_PY.exists():
        pytest.skip("phase2/service.py not found")
    content = SERVICE_PY.read_text()
    # Look for `run_full_platform.py` in non-comment, non-string lines.
    # We accept it in COMMENTS (historical context) but NOT in f-strings
    # that are shown to operators (e.g., `f"... run_full_platform.py ..."`).
    for line in content.splitlines():
        stripped = line.lstrip()
        if stripped.startswith("#"):
            continue
        if "run_full_platform.py" in line:
            # Check if this is inside an f-string (error message).
            # f-strings look like:  f"..." or f'...'
            if re.search(r'f["\'].*run_full_platform\.py', line):
                pytest.fail(
                    f"IN-072 follow-up regression: phase2/service.py line "
                    f"contains `run_full_platform.py` in an f-string "
                    f"(shown to operators): {line.strip()!r}. This file "
                    f"was DELETED by IN-072. Operators would try to run a "
                    f"non-existent file. Update the message to point to "
                    f"`run_4phase.py` (the canonical runner)."
                )


def test_in_072_service_error_messages_point_to_run_4phase() -> None:
    """The service error messages MUST point operators to ``run_4phase.py``."""
    if not SERVICE_PY.exists():
        pytest.skip("phase2/service.py not found")
    content = SERVICE_PY.read_text()
    # Count f-strings mentioning run_4phase.py.
    matches = re.findall(r'f["\'][^"\']*run_4phase\.py', content)
    assert len(matches) >= 1, (
        "IN-072: phase2/service.py error messages do NOT mention "
        "`run_4phase.py`. Operators hitting a FileNotFoundError would "
        "not know which command to run. Add `run_4phase.py` to the "
        "error messages."
    )


# ─── IN-072.5: Makefile targets are aliases (not invocations) ─────────────


def test_in_072_makefile_targets_are_aliases() -> None:
    """The Makefile targets ``run-full-platform``, ``run-unified``, ``run-real``
    MUST be aliases for ``make run`` (which invokes ``run_4phase.py``).

    The IN-072 fix deleted the legacy runners but the Makefile targets
    still exist as deprecated aliases. This test verifies they don't
    invoke the deleted files directly.
    """
    if not MAKEFILE.exists():
        pytest.skip("Makefile not found")
    content = MAKEFILE.read_text()
    # For each deprecated target, verify it doesn't invoke the deleted file.
    for deleted in DELETED_FILES:
        # Look for lines that invoke the deleted file (e.g., `$(PYTHON) run_real_pipeline.py`).
        # Comments are allowed (historical context).
        for line in content.splitlines():
            stripped = line.lstrip()
            if stripped.startswith("#"):
                continue
            if deleted in line and ("$(PYTHON)" in line or "python" in line.lower()):
                pytest.fail(
                    f"IN-072 regression: Makefile line invokes deleted "
                    f"file `{deleted}`: {line.strip()!r}. The file was "
                    f"DELETED by IN-072. Update the target to be an alias "
                    f"for `make run` (which invokes `run_4phase.py`)."
                )
