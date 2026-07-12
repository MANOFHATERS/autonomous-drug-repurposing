#!/usr/bin/env python3
"""Pre-commit guard: ISSUE_OWNERSHIP.md contract enforcement.

Runs in <0.1 seconds locally. No CI needed. Blocks commits that violate
the issue-ownership contract:

  1. If you edit a file owned by an issue CLAIMED by another agent → BLOCK
  2. If you edit an immutable file (migration 001-011) → BLOCK
  3. If you edit a file owned by an issue marked DONE → WARN (did you
     mean to reopen the issue? claim it first)
  4. If you add a new code file not in the FILE→ISSUE map → REMIND
     (add it to the map so others know who owns it)

HOW IT WORKS:
  - Reads ISSUE_OWNERSHIP.md (the single source of truth)
  - Builds FILE → ISSUE_ID → (STATUS, AGENT_ID) mapping
  - For each staged file, checks who owns it
  - If owned by a CLAIMED issue and the claimer is NOT you → BLOCK

INSTALL (run once per clone, from repo root):
    cp scripts/pre_commit_issue_guard.py .git/hooks/pre-commit
    chmod +x .git/hooks/pre-commit

Or run manually:
    python scripts/pre_commit_issue_guard.py
"""

from __future__ import annotations

import os
import re
import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
OWNERSHIP_FILE = REPO_ROOT / "ISSUE_OWNERSHIP.md"


def get_staged_files() -> set[str]:
    """Get the list of files staged for commit (relative to repo root)."""
    result = subprocess.run(
        ["git", "diff", "--cached", "--name-only"],
        capture_output=True, text=True, cwd=REPO_ROOT,
    )
    return {f for f in result.stdout.strip().split("\n") if f}


def get_current_agent() -> str:
    """Get the current agent's identity from git config user.name."""
    result = subprocess.run(
        ["git", "config", "user.name"], capture_output=True, text=True,
    )
    return result.stdout.strip() or "unknown"


def parse_ownership_map() -> tuple[dict[str, dict], dict[str, str]]:
    """Parse ISSUE_OWNERSHIP.md.

    Returns:
      issues: {ISSUE_ID: {status, agent_id, files, ...}}
      file_to_issues: {file_path: [ISSUE_ID, ...]}
    """
    if not OWNERSHIP_FILE.exists():
        return {}, {}

    content = OWNERSHIP_FILE.read_text()
    issues = {}
    file_to_issues = {}

    # Parse the ISSUE OWNERSHIP TABLE rows.
    # Format: ISSUE_ID | TITLE | PHASE | STATUS | AGENT_ID | BRANCH | CLAIMED_AT | FILES | MERGED | NOTES
    # FILES is comma-separated.
    # We match lines that start with a known issue ID pattern.
    issue_pattern = re.compile(
        r"^(P[1-4]-\d{3}|FE-\d{3}|BUG-#\d+|DEDUP-\d{3}|GAP-\d{3})\s*\|\s*([^|]*?)\s*\|\s*(\S+)\s*\|\s*(\S+)\s*\|\s*([^|]*?)\s*\|\s*([^|]*?)\s*\|\s*([^|]*?)\s*\|\s*([^|]*?)\s*\|\s*([^|]*?)\s*\|\s*(.*)$",
        re.MULTILINE,
    )
    for m in issue_pattern.finditer(content):
        issue_id, title, phase, status, agent_id, branch, claimed_at, files_str, merged, notes = m.groups()
        files = [f.strip() for f in files_str.split(",") if f.strip() and f.strip() != "(see" and "see " not in f.strip()]
        issues[issue_id] = {
            "title": title.strip(),
            "phase": phase.strip(),
            "status": status.strip(),
            "agent_id": agent_id.strip(),
            "branch": branch.strip(),
            "claimed_at": claimed_at.strip(),
            "files": files,
            "merged": merged.strip(),
            "notes": notes.strip(),
        }
        for f in files:
            file_to_issues.setdefault(f, []).append(issue_id)

    # Also parse the FILE → ISSUE MAP section (the inverse mapping)
    # Format: <file_path> → <ISSUE_ID> (STATUS), <ISSUE_ID> (STATUS)
    file_map_pattern = re.compile(
        r"^(\S+)\s*→\s*(.+)$",
        re.MULTILINE,
    )
    for m in file_map_pattern.finditer(content):
        file_path, rhs = m.groups()
        if file_path.startswith("#") or file_path.startswith("---"):
            continue
        # Parse "ISSUE_ID (STATUS), ISSUE_ID (STATUS)" or "IMMUTABLE (...)"
        for piece in re.finditer(r"(\S+)\s*\(([^)]+)\)", rhs):
            issue_id, status = piece.groups()
            if issue_id == "IMMUTABLE":
                file_to_issues.setdefault(file_path, []).append("IMMUTABLE")
            elif issue_id == "SHARED":
                file_to_issues.setdefault(file_path, []).append("SHARED")
            elif issue_id == "DEPRECATED":
                file_to_issues.setdefault(file_path, []).append("DEPRECATED")
            else:
                # It's an issue ID — make sure it's in the issues dict
                if issue_id not in issues:
                    issues[issue_id] = {
                        "status": status,
                        "agent_id": "—",
                        "files": [file_path],
                    }
                file_to_issues.setdefault(file_path, []).append(issue_id)

    return issues, file_to_issues


def check_immutable_files(staged: set[str], file_to_issues: dict) -> list[str]:
    """Block edits to immutable files (migrations 001-011)."""
    errors = []
    for f in staged:
        # Check explicit IMMUTABLE marker in the file map
        if "IMMUTABLE" in file_to_issues.get(f, []):
            errors.append(
                f"BLOCKED: {f} is IMMUTABLE. Editing it breaks the immutability "
                f"contract. Create a NEW migration/file instead. "
                f"See ISSUE_OWNERSHIP.md → FILE → ISSUE MAP."
            )
            continue
        # Also check by convention: migrations 001-011 are always immutable
        m = re.match(r".*migrations/(\d{3})_.*\.sql$", f)
        if m and 1 <= int(m.group(1)) <= 11:
            errors.append(
                f"BLOCKED: {f} is migration {m.group(1)} (001-011 are immutable). "
                f"Create a NEW migration (013+) instead. "
                f"See ISSUE_OWNERSHIP.md."
            )
    return errors


def check_claimed_by_other(staged: set[str], issues: dict, file_to_issues: dict, current_agent: str) -> list[str]:
    """Block edits to files owned by issues CLAIMED by another agent."""
    errors = []
    for f in staged:
        if f == "ISSUE_OWNERSHIP.md":
            continue  # editing the registry itself is always allowed
        owner_issues = file_to_issues.get(f, [])
        if not owner_issues:
            continue  # file not in the map — handled by check_unmapped_files
        for issue_id in owner_issues:
            if issue_id in ("IMMUTABLE", "SHARED", "DEPRECATED"):
                continue
            issue = issues.get(issue_id, {})
            status = issue.get("status", "").upper()
            agent = issue.get("agent_id", "—")
            if status == "CLAIMED" and agent and agent != "—" and agent != current_agent:
                errors.append(
                    f"BLOCKED: {f} is owned by issue {issue_id} which is "
                    f"CLAIMED by {agent} (since {issue.get('claimed_at', '?')}). "
                    f"Branch: {issue.get('branch', '?')}. "
                    f"DO NOT TOUCH — pick another issue or wait for {agent} to finish. "
                    f"See ISSUE_OWNERSHIP.md."
                )
    return errors


def check_done_files_warning(staged: set[str], issues: dict, file_to_issues: dict) -> list[str]:
    """Warn when editing a file owned by a DONE issue."""
    warnings = []
    for f in staged:
        if f == "ISSUE_OWNERSHIP.md":
            continue
        owner_issues = file_to_issues.get(f, [])
        done_issues = []
        for issue_id in owner_issues:
            if issue_id in ("IMMUTABLE", "SHARED", "DEPRECATED"):
                continue
            issue = issues.get(issue_id, {})
            if issue.get("status", "").upper() == "DONE":
                done_issues.append(issue_id)
        if done_issues:
            warnings.append(
                f"WARNING: {f} is owned by issue(s) {', '.join(done_issues)} "
                f"which are DONE. If you're fixing a NEW bug, create a new "
                f"issue ID and claim it in ISSUE_OWNERSHIP.md first. "
                f"If you're reverting, update the issue status to CLAIMED."
            )
    return warnings


def check_unmapped_files(staged: set[str], file_to_issues: dict) -> list[str]:
    """Remind agents to add new code files to the ownership map."""
    warnings = []
    code_extensions = {".py", ".sql", ".yml", ".yaml", ".json", ".ts", ".tsx"}
    for f in staged:
        if f == "ISSUE_OWNERSHIP.md":
            continue
        if f in file_to_issues:
            continue  # already mapped
        if not any(f.endswith(ext) for ext in code_extensions):
            continue
        # Test files are SHARED by convention
        if "/tests/" in f or f.startswith("tests/") or f.startswith("phase1/tests/"):
            continue
        warnings.append(
            f"REMINDER: {f} is not in ISSUE_OWNERSHIP.md → FILE → ISSUE MAP. "
            f"If this is a new code file, add an entry: claim a new issue ID "
            f"(or assign it to an existing issue) and add the file to that "
            f"issue's FILES column + the FILE → ISSUE MAP section."
        )
    return warnings


def check_deprecated_files(staged: set[str], file_to_issues: dict) -> list[str]:
    """Warn when editing a DEPRECATED file."""
    warnings = []
    for f in staged:
        if "DEPRECATED" in file_to_issues.get(f, []):
            warnings.append(
                f"WARNING: {f} is DEPRECATED. Use the replacement noted in "
                f"ISSUE_OWNERSHIP.md instead."
            )
    return warnings


def main() -> int:
    if not OWNERSHIP_FILE.exists():
        print("⚠ ISSUE_OWNERSHIP.md not found — skipping ownership guard (bootstrap mode)")
        return 0

    staged = get_staged_files()
    if not staged:
        return 0

    issues, file_to_issues = parse_ownership_map()
    current_agent = get_current_agent()

    errors = []
    warnings = []

    errors.extend(check_immutable_files(staged, file_to_issues))
    errors.extend(check_claimed_by_other(staged, issues, file_to_issues, current_agent))
    warnings.extend(check_done_files_warning(staged, issues, file_to_issues))
    warnings.extend(check_unmapped_files(staged, file_to_issues))
    warnings.extend(check_deprecated_files(staged, file_to_issues))

    if warnings:
        print("=" * 70)
        print("ISSUE OWNERSHIP GUARD — WARNINGS (commit will proceed):")
        print("=" * 70)
        for w in warnings:
            print(f"  ⚠️  {w}")
        print()

    if errors:
        print("=" * 70)
        print("ISSUE OWNERSHIP GUARD — ERRORS (commit BLOCKED):")
        print("=" * 70)
        for e in errors:
            print(f"  🚫 {e}")
        print()
        print("To fix: read ISSUE_OWNERSHIP.md and either:")
        print("  - claim the issue first (edit the table, commit, push, then retry)")
        print("  - pick a different issue that's AVAILABLE")
        print("  - if the claim is stale (>24h), ping the agent or claim it yourself")
        print(f"\nCurrent agent: {current_agent}")
        return 1

    if not warnings:
        print(f"✓ issue ownership guard: OK (agent={current_agent})")
    return 0


if __name__ == "__main__":
    sys.exit(main())
