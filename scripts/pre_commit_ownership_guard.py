#!/usr/bin/env python3
"""BE-080 DEPRECATED: This script is now a thin wrapper around
pre_commit_issue_guard.py, which is the SINGLE entry point for ownership
enforcement.

Previously two parallel ownership systems (ISSUE_OWNERSHIP.md and
AGENTS_FILE_OWNERSHIP.md) with different schemas created confusion. The
issue guard now checks BOTH files, making this script redundant.

For new installs, use:
    cp scripts/pre_commit_issue_guard.py .git/hooks/pre-commit

This wrapper is kept for backward compat with existing hooks that point
here. It simply delegates to the unified guard.
"""

from __future__ import annotations

import os
import re
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
OWNERSHIP_FILE = REPO_ROOT / "AGENTS_FILE_OWNERSHIP.md"
MIGRATIONS_DIR = REPO_ROOT / "phase1" / "database" / "migrations"


def get_staged_files() -> set[str]:
    """Get the list of files staged for commit."""
    import subprocess
    result = subprocess.run(
        ["git", "diff", "--cached", "--name-only"],
        capture_output=True, text=True, cwd=REPO_ROOT,
    )
    return {f for f in result.stdout.strip().split("\n") if f}


def parse_ownership_map() -> dict[str, dict]:
    """Parse the OWNERSHIP table into {file_path: {status, agent, ...}}."""
    if not OWNERSHIP_FILE.exists():
        return {}
    content = OWNERSHIP_FILE.read_text()
    ownership = {}
    # Match lines like:
    #   path | bug_ids | status | agent | branch | timestamp | note
    pattern = re.compile(
        r"^(\S+)\s*\|\s*([^|]+?)\s*\|\s*(\S+)\s*\|\s*([^|]*?)\s*\|\s*([^|]*?)\s*\|\s*([^|]*?)\s*\|\s*(.*)$",
        re.MULTILINE,
    )
    for m in pattern.finditer(content):
        path, bug_ids, status, agent, branch, timestamp, note = m.groups()
        # Skip header rows and separators
        if path.startswith("#") or path.startswith("---") or path.startswith("<"):
            continue
        if path in ("file_or_dir", "--"):
            continue
        ownership[path] = {
            "bug_ids": bug_ids.strip(),
            "status": status.strip(),
            "agent": agent.strip(),
            "branch": branch.strip(),
            "timestamp": timestamp.strip(),
            "note": note.strip(),
        }
    return ownership


def check_immutable_migrations(staged: set[str]) -> list[str]:
    """Block edits to migrations 001-011."""
    errors = []
    for f in staged:
        # Match phase1/database/migrations/00X_*.sql where 00X is 001-011
        m = re.match(r".*migrations/(\d{3})_.*\.sql$", f)
        if m and 1 <= int(m.group(1)) <= 11:
            errors.append(
                f"BLOCKED: {f} is an immutable migration (001-011). "
                f"Editing applied migrations breaks the immutability contract. "
                f"Create a NEW migration (013+) instead. "
                f"See AGENTS_FILE_OWNERSHIP.md -> IMMUTABLE FILES RULE."
            )
    return errors


def check_claimed_files(staged: set[str], ownership: dict) -> list[str]:
    """Block edits to files CLAIMED by another agent."""
    errors = []
    # Get the current agent's identity from git config
    import subprocess
    user_result = subprocess.run(
        ["git", "config", "user.name"], capture_output=True, text=True,
    )
    current_agent = user_result.stdout.strip() or "unknown"

    for f in staged:
        if f == "AGENTS_FILE_OWNERSHIP.md":
            continue  # editing the ownership file itself is always allowed
        # Find the ownership entry for this file (exact match or parent dir)
        entry = ownership.get(f)
        if entry and entry["status"] == "CLAIMED":
            claimer = entry["agent"]
            if claimer and claimer != current_agent and claimer != "--":
                errors.append(
                    f"BLOCKED: {f} is CLAIMED by {claimer} (since {entry['timestamp']}). "
                    f"Branch: {entry['branch']}. Bug: {entry['bug_ids']}. "
                    f"DO NOT TOUCH -- pick another file or wait for {claimer} to finish. "
                    f"See AGENTS_FILE_OWNERSHIP.md."
                )
    return errors


def check_done_files_warning(staged: set[str], ownership: dict) -> list[str]:
    """Warn (don't block) when editing a file marked DONE."""
    warnings = []
    for f in staged:
        if f == "AGENTS_FILE_OWNERSHIP.md":
            continue
        entry = ownership.get(f)
        if entry and entry["status"] == "DONE":
            warnings.append(
                f"WARNING: {f} is marked DONE (merged in {entry.get('note', 'unknown')}). "
                f"If you're fixing a NEW bug, claim it first by updating "
                f"AGENTS_FILE_OWNERSHIP.md. If you're reverting, update the status."
            )
    return warnings


def check_new_file_claimed(staged: set[str], ownership: dict) -> list[str]:
    """Warn when adding a new code file without claiming it."""
    warnings = []
    code_extensions = {".py", ".sql", ".yml", ".yaml", ".json"}
    for f in staged:
        if f == "AGENTS_FILE_OWNERSHIP.md":
            continue
        if not any(f.endswith(ext) for ext in code_extensions):
            continue
        # Is this file in the ownership map?
        if f not in ownership:
            # Is it a test file? Tests are SHARED, no claim needed.
            if "/tests/" in f or f.startswith("tests/"):
                continue
            warnings.append(
                f"REMINDER: {f} is not in AGENTS_FILE_OWNERSHIP.md. "
                f"If this is a new code file, add an entry with STATUS=CLAIMED "
                f"so other agents know you're working on it."
            )
    return warnings


def main() -> int:
    # BE-080: Delegate to the unified issue guard which checks BOTH
    # ISSUE_OWNERSHIP.md and AGENTS_FILE_OWNERSHIP.md. This script is
    # kept as a backward-compat wrapper.
    import subprocess
    result = subprocess.run(
        [sys.executable, str(REPO_ROOT / "scripts" / "pre_commit_issue_guard.py")],
        cwd=REPO_ROOT,
    )
    return result.returncode


if __name__ == "__main__":
    sys.exit(main())
