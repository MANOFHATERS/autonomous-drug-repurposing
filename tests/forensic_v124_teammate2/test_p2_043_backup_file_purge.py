"""Forensic test for P2-043 REAL ROOT FIX (v124).

The audit's P2-043 issue:
    ``bridge_fallbacks.jsonl`` last 6 entries show ``"layer": "thread_3"``,
    ``"reason": "write_16"`` through ``"write_18"`` -- nonsensical audit
    entries from a concurrent test.

The v113 "fix" purged the LIVE file (replaced with a 1-line marker) but
kept the corrupted backup files (``*.pre-purge-bak``,
``v109_archived/bridge_fallbacks.jsonl.v108_pre_fix``) tracked in git.
Anyone who cloned the repo inherited 909 lines of nonsensical test-
pollution entries. The writer-side validation (rejecting ``^thread_\\d+$``
and ``^write_\\d+$`` patterns) was correct, but the historical corrupted
backup files were never deleted.

These tests verify the REAL root fix (v124):
  1. No ``*.pre-purge-bak`` files are tracked in git anywhere.
  2. No ``v109_archived/`` directory exists in ``phase2/logs/audit/``.
  3. No ``*.v108_pre_fix`` (or any ``*.v*_pre_fix``) backup files tracked.
  4. The LIVE ``bridge_fallbacks.jsonl`` has fewer than 10 entries (the
     v113 marker line is 1 entry -- any concurrent-test pollution would
     push this above 10).
  5. The writer-side validation in ``_log_bridge_fallback`` still rejects
     ``thread_N`` and ``write_N`` patterns (catches future regressions
     of the writer-side guard).
"""
from __future__ import annotations

import re
import subprocess
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
AUDIT_DIR = REPO_ROOT / "phase2" / "logs" / "audit"
BRIDGE_FALLBACKS_LIVE = AUDIT_DIR / "bridge_fallbacks.jsonl"
BRIDGE_FILE = REPO_ROOT / "phase2" / "drugos_graph" / "phase1_bridge.py"


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


# ─── P2-043.1: no .pre-purge-bak files tracked in git ─────────────────────


def test_p2_043_no_pre_purge_bak_files_tracked() -> None:
    """No ``*.pre-purge-bak`` files should be tracked in git.

    The v113 "fix" kept ``bridge_fallbacks.jsonl.p2-043-pre-purge-bak``
    tracked in git -- 909 lines of nonsensical thread_3/write_NN entries
    that anyone who cloned inherited. This test catches that regression.
    """
    tracked = _git_tracked_files()
    bak_files = [f for f in tracked if "pre-purge-bak" in f or f.endswith(".bak")]
    assert not bak_files, (
        f"P2-043 REAL ROOT FIX regression: the following backup files are "
        f"tracked in git: {bak_files}. These files contain corrupted audit "
        f"data from concurrent-test pollution (nonsensical thread_N/write_NN "
        f"entries). Delete them with `git rm` and add the .gitignore "
        f"patterns to prevent future backup files from being committed."
    )


# ─── P2-043.2: no v109_archived/ directory in audit dir ───────────────────


def test_p2_043_no_v109_archived_dir() -> None:
    """No ``v109_archived/`` (or any ``v*_archived/``) dir in audit logs.

    The v109 "fix" archived the corrupted pre-fix audit files to
    ``v109_archived/`` -- but tracked the archive in git. This defeated
    the purpose of the purge. The git history preserves the original
    state if anyone needs to audit; the working tree should be clean.
    """
    archived_dirs = list(AUDIT_DIR.glob("v*_archived"))
    # Also check any archived dir tracked in git anywhere under audit/.
    tracked = _git_tracked_files()
    archived_tracked = [f for f in tracked if "/v" in f and "_archived/" in f]
    assert not archived_dirs, (
        f"P2-043 REAL ROOT FIX regression: the following archived "
        f"directories still exist in {AUDIT_DIR}: {archived_dirs}. These "
        f"contain corrupted audit data from pre-v109 test runs. Delete "
        f"them with `git rm -r`."
    )
    assert not archived_tracked, (
        f"P2-043: the following archived files are still tracked in git: "
        f"{archived_tracked}. Delete them with `git rm`."
    )


# ─── P2-043.3: no *.v*_pre_fix backup files tracked ───────────────────────


def test_p2_043_no_v_pre_fix_files_tracked() -> None:
    """No ``*.v*_pre_fix`` backup files should be tracked in git.

    Catches the ``bridge_fallbacks.jsonl.v108_pre_fix`` and
    ``transe_prediction_complete.jsonl.v108_pre_fix`` patterns.
    """
    tracked = _git_tracked_files()
    pre_fix_files = [
        f for f in tracked
        if re.search(r"\.v\d+_pre_fix($|\.)", f)
    ]
    assert not pre_fix_files, (
        f"P2-043 REAL ROOT FIX regression: the following v*_pre_fix backup "
        f"files are tracked in git: {pre_fix_files}. These contain "
        f"corrupted audit data from pre-v109 test runs (nonsensical "
        f"thread_N/write_NN entries for bridge_fallbacks; TransE trained "
        f"on 10-drug toy dataset for transe_prediction_complete). Delete "
        f"them with `git rm`."
    )


# ─── P2-043.4: LIVE bridge_fallbacks.jsonl has fewer than 10 entries ──────


def test_p2_043_live_bridge_fallbacks_is_clean() -> None:
    """The LIVE ``bridge_fallbacks.jsonl`` MUST have fewer than 10 entries.

    The v113 "fix" replaced 909 corrupted entries with a 1-line marker.
    If a future concurrent test pollutes the file again (and the writer-
    side guard fails), this test catches the regression.

    The writer-side guard in ``_log_bridge_fallback`` rejects
    ``^thread_\\d+$`` and ``^write_\\d+$`` patterns, so legitimate
    fallback events should be rare (<10 per run).
    """
    if not BRIDGE_FALLBACKS_LIVE.exists():
        pytest.skip("bridge_fallbacks.jsonl does not exist (fresh clone)")
    content = BRIDGE_FALLBACKS_LIVE.read_text()
    # Count non-empty lines.
    lines = [line for line in content.splitlines() if line.strip()]
    assert len(lines) < 10, (
        f"P2-043: bridge_fallbacks.jsonl has {len(lines)} entries "
        f"(expected < 10). The file may be polluted with concurrent-test "
        f"entries (thread_N/write_NN patterns). The writer-side guard in "
        f"`_log_bridge_fallback` should reject these patterns -- verify "
        f"the guard is still in place."
    )


# ─── P2-043.5: LIVE file does NOT contain thread_N or write_NN entries ────


def test_p2_043_live_file_no_thread_write_pollution() -> None:
    """The LIVE file MUST NOT contain thread_N or write_NN entries.

    Direct content check: even if the file grows past 10 entries, this
    test catches the specific pollution pattern.
    """
    if not BRIDGE_FALLBACKS_LIVE.exists():
        pytest.skip("bridge_fallbacks.jsonl does not exist (fresh clone)")
    content = BRIDGE_FALLBACKS_LIVE.read_text()
    thread_pattern = re.compile(r'"layer":\s*"thread_\d+"')
    write_pattern = re.compile(r'"reason":\s*"write_\d+"')
    assert not thread_pattern.search(content), (
        "P2-043: bridge_fallbacks.jsonl contains `thread_N` layer entries "
        "-- the writer-side guard in `_log_bridge_fallback` should have "
        "rejected these. The guard may have been regressed."
    )
    assert not write_pattern.search(content), (
        "P2-043: bridge_fallbacks.jsonl contains `write_NN` reason entries "
        "-- the writer-side guard in `_log_bridge_fallback` should have "
        "rejected these. The guard may have been regressed."
    )


# ─── P2-043.6: writer-side guard still rejects thread_N / write_NN ────────


def test_p2_043_writer_side_guard_present() -> None:
    """The ``_log_bridge_fallback`` function MUST still reject pollution patterns.

    The writer-side guard is the PRIMARY defense against future
    pollution. The .gitignore rules + backup-file deletion are
    secondary defenses (they prevent the corruption from being
    committed, but the writer guard prevents it from being WRITTEN
    in the first place).

    This test verifies the guard regex is still present in the source.
    """
    if not BRIDGE_FILE.exists():
        pytest.skip("phase1_bridge.py not found")
    src = BRIDGE_FILE.read_text()
    # Look for the test-pollution regex patterns in the source.
    # The patterns should be:
    #   _TEST_POLLUTION_LAYER_RE = _re.compile(r"^thread_\d+$")
    #   _TEST_POLLUTION_REASON_RE = _re.compile(r"^write_\d+$")
    assert re.search(r"thread_\\d\+", src), (
        "P2-043: the writer-side guard regex `^thread_\\d+$` is MISSING "
        "from phase1_bridge.py. The `_log_bridge_fallback` function can "
        "no longer reject thread_N layer entries. Restore the guard."
    )
    assert re.search(r"write_\\d\+", src), (
        "P2-043: the writer-side guard regex `^write_\\d+$` is MISSING "
        "from phase1_bridge.py. The `_log_bridge_fallback` function can "
        "no longer reject write_NN reason entries. Restore the guard."
    )


# ─── P2-043.7: .gitignore has rules for backup file patterns ──────────────


def test_p2_043_gitignore_blocks_backup_patterns() -> None:
    """The ``.gitignore`` MUST block future backup file patterns.

    Without .gitignore rules, a future agent could re-introduce
    ``*.pre-purge-bak`` or ``v*_archived/`` files. The .gitignore
    patterns catch these BEFORE they're committed.
    """
    gitignore = REPO_ROOT / ".gitignore"
    assert gitignore.exists(), ".gitignore not found at repo root"
    content = gitignore.read_text()
    required_patterns = [
        "pre-purge-bak",
        "v*_archived",
    ]
    missing = [p for p in required_patterns if p not in content]
    assert not missing, (
        f"P2-043: .gitignore is MISSING the following backup-file patterns: "
        f"{missing}. Without these rules, a future agent could re-introduce "
        f"corrupted backup files. Add the patterns to .gitignore."
    )
