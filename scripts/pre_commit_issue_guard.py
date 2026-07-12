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
    # Format: ISSUE_ID | TITLE | PHASE | STATUS | AGENT_ID | BRANCH | CLAIMED_AT | FILES | MERGED | VERIFIED_AT | NOTES
    # FILES is comma-separated. VERIFIED_AT is "—" if not verified.
    # We match lines that start with a known issue ID pattern.
    issue_pattern = re.compile(
        r"^(P[1-4]-\d{3}|FE-\d{3}|BUG-#\d+|DEDUP-\d{3}|GAP-\d{3})\s*\|\s*([^|]*?)\s*\|\s*(\S+)\s*\|\s*(\S+)\s*\|\s*([^|]*?)\s*\|\s*([^|]*?)\s*\|\s*([^|]*?)\s*\|\s*([^|]*?)\s*\|\s*([^|]*?)\s*\|\s*([^|]*?)\s*\|\s*(.*)$",
        re.MULTILINE,
    )
    for m in issue_pattern.finditer(content):
        issue_id, title, phase, status, agent_id, branch, claimed_at, files_str, merged, verified_at, notes = m.groups()
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
            "verified_at": verified_at.strip(),
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
    """Entry point — handles both pre-commit hook mode and `verify` subcommand."""
    # Check for subcommand
    if len(sys.argv) > 1 and sys.argv[1] == "verify":
        return cmd_verify(sys.argv[2:])
    if len(sys.argv) > 1 and sys.argv[1] == "list":
        return cmd_list(sys.argv[2:])
    if len(sys.argv) > 1 and sys.argv[1] == "status":
        return cmd_status(sys.argv[2:])

    # Default: pre-commit hook mode
    return run_pre_commit_hook()


def run_pre_commit_hook() -> int:
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


# ====================================================================
# VERIFY SUBCOMMAND — the partial-fix solution
# ====================================================================

# Map of ISSUE_ID → verification test (file + function or pytest node)
# The verify command runs these tests and flips DONE → VERIFIED (pass)
# or DONE → REOPENED (fail).
#
# To add a new verification: add an entry here pointing to a test that
# exercises the fix. The test must return 0 on success, non-zero on fail.
VERIFICATION_TESTS: dict[str, dict] = {
    "P1-001": {
        "desc": "chembl dead second CSV read removed",
        "check": "import inspect; from pipelines.chembl_pipeline import ChEMBLPipeline; src = inspect.getsource(ChEMBLPipeline.clean); assert '_compression = \"gzip\" if raw_path.suffix' not in src",
    },
    "P1-002": {
        "desc": "neo4j schema-qualified table lookup",
        "check": "import inspect; from exporters.neo4j_exporter import check_neo4j_readiness; src = inspect.getsource(check_neo4j_readiness); assert 'autoload_with=_bind' in src and 'schema=_meta_name' in src",
    },
    "P1-003": {
        "desc": "docker-compose 3 new mounts",
        "check": "import yaml; dc = yaml.safe_load(open('docker-compose.yml')); assert all('./data:/opt/airflow/data' in dc['services'][s].get('volumes', []) and './exporters:/opt/airflow/exporters' in dc['services'][s].get('volumes', []) and './scripts:/opt/airflow/scripts' in dc['services'][s].get('volumes', []) for s in ('airflow-init', 'airflow-webserver', 'airflow-scheduler'))",
    },
    "P1-004": {
        "desc": "confidence tier sub_weak/weak/strong labels (7 sites)",
        "check": "from cleaning.confidence import DEFAULT_CONFIDENCE_TIERS, classify_confidence; assert DEFAULT_CONFIDENCE_TIERS == [(0.0, 'sub_weak'), (0.06, 'weak'), (0.3, 'strong')]; assert classify_confidence(0.01) == 'sub_weak'; assert classify_confidence(0.1) == 'weak'; assert classify_confidence(0.5) == 'strong'",
    },
    "P1-005": {
        "desc": "is_homodimer server_default=FALSE",
        "check": "from database.models import ProteinProteinInteraction; sd = ProteinProteinInteraction.__table__.c.is_homodimer.server_default; sd_text = sd.arg.text if hasattr(sd, 'arg') else str(sd); assert sd_text == 'FALSE'",
    },
    "P1-006": {
        "desc": "_CircuitBreaker.state pure observation",
        "check": "import time; from database.connection import _CircuitBreaker; cb = _CircuitBreaker(failure_threshold=2, recovery_timeout=0.1); cb.record_failure(); cb.record_failure(); assert cb.state == 'OPEN'; time.sleep(0.15); assert cb.state == 'OPEN'",
    },
    "P1-007": {
        "desc": "OMIM mapping_key column drives re-map",
        "check": "import pandas as pd; from cleaning.missing_values import validate_gda_scores; df = pd.DataFrame({'gene_symbol': ['BRCA1'], 'disease_id': ['C0001'], 'disease_name': ['Breast cancer'], 'association_type': ['therapeutic'], 'score': [0.5], 'mapping_key': [3], 'source': ['omim']}); r = validate_gda_scores(df, score_range=(0.0, 1.0), source='omim'); assert r['score'].iloc[0] == 0.9",
    },
    "P1-009": {
        "desc": "pubchem_load wire removed",
        "check": "import re; src = open('dags/master_pipeline_dag.py').read(); assert not re.compile(r'^\\s*pubchem_load\\s*>>\\s*trigger_phase2\\s*$', re.MULTILINE).search(src)",
    },
    "P1-012": {
        "desc": "normalizer circuit breaker thread safety",
        "check": "from cleaning.normalizer import _NormalizerCircuitBreaker, _LegacyLocalCircuitBreaker, _cb_convert; assert isinstance(_cb_convert, _NormalizerCircuitBreaker); legacy = _LegacyLocalCircuitBreaker('test', failure_threshold=2, reset_timeout=0.1); assert hasattr(legacy, '_lock')",
    },
    "P1-018": {
        "desc": "DisGeNET centered ±50% jitter",
        "check": "import inspect, ast, textwrap; from pipelines.disgenet_pipeline import DisGeNETPipeline; src = textwrap.dedent(inspect.getsource(DisGeNETPipeline._compute_retry_wait)); tree = ast.parse(src); calls = [[ast.unparse(a) for a in n.args] for n in ast.walk(tree) if isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute) and isinstance(n.func.value, ast.Name) and n.func.value.id == '_random' and n.func.attr == 'uniform']; assert calls == [['capped * 0.5', 'capped * 1.5']]",
    },
    "P1-021": {
        "desc": "stereo collapse WARNING + lineage",
        "check": "import inspect; from cleaning.deduplicator import dedup_by_inchikey; src = inspect.getsource(dedup_by_inchikey); assert '_stereo_collapsed' in src and 'logger.warning' in src",
    },
    "P1-024": {
        "desc": "SYNTH% CHECK LENGTH<=27 cap",
        "check": "from database.models import Drug; chk = next((c for c in Drug.__table__.constraints if getattr(c, 'name', None) == 'chk_drugs_inchikey_format'), None); assert chk is not None; assert 'LENGTH(inchikey) <= 27' in str(chk.sqltext)",
    },
    # P1-008, P1-010, P1-011, P1-013..P1-017, P1-019, P1-020, P1-022, P1-023
    # — add verification checks here as you go. Issues without a check
    # are reported as "NO TEST" in verify output.
}


def run_verification_check(check_code: str) -> tuple[bool, str]:
    """Run a verification check (Python code string) and return (success, error_msg)."""
    import os
    import sys
    # Set up the environment so imports work
    phase1_dir = str(REPO_ROOT / "phase1")
    if phase1_dir not in sys.path:
        sys.path.insert(0, phase1_dir)
    os.chdir(phase1_dir)
    try:
        exec(check_code, {"__name__": "__verify__"})
        return True, ""
    except Exception as e:
        return False, f"{type(e).__name__}: {e}"


def cmd_verify(args: list[str]) -> int:
    """Verify DONE issues and flip their status.

    Usage:
        python scripts/pre_commit_issue_guard.py verify           # verify all DONE
        python scripts/pre_commit_issue_guard.py verify P1-004    # verify one issue
        python scripts/pre_commit_issue_guard.py verify --all     # verify all (DONE + VERIFIED)
    """
    if not OWNERSHIP_FILE.exists():
        print("✗ ISSUE_OWNERSHIP.md not found")
        return 1

    issues, _ = parse_ownership_map()
    verify_all = "--all" in args
    specific_issues = [a for a in args if not a.startswith("--")]

    # Decide which issues to verify
    to_verify = []
    for issue_id, issue in issues.items():
        status = issue["status"].upper()
        if specific_issues:
            if issue_id in specific_issues:
                to_verify.append((issue_id, issue))
        elif verify_all:
            if status in ("DONE", "VERIFIED"):
                to_verify.append((issue_id, issue))
        else:
            # Default: verify all DONE (not yet verified)
            if status == "DONE":
                to_verify.append((issue_id, issue))

    if not to_verify:
        print("No DONE issues to verify. (Use `verify --all` to re-verify VERIFIED issues too.)")
        return 0

    print(f"Verifying {len(to_verify)} issue(s)...")
    print("=" * 70)

    passed = []
    failed = []
    no_test = []

    for issue_id, issue in to_verify:
        title = issue.get("title", "")[:50]
        status = issue["status"].upper()
        print(f"\n[{issue_id}] {title}")
        print(f"  current status: {status}")

        if issue_id not in VERIFICATION_TESTS:
            print(f"  ⚠  NO TEST — no verification check defined for {issue_id}")
            print(f"     Add an entry to VERIFICATION_TESTS in scripts/pre_commit_issue_guard.py")
            no_test.append(issue_id)
            continue

        check_info = VERIFICATION_TESTS[issue_id]
        print(f"  desc: {check_info['desc']}")
        print(f"  running check...", end=" ")

        success, error = run_verification_check(check_info["check"])
        if success:
            print("PASS ✓")
            passed.append(issue_id)
        else:
            print(f"FAIL ✗")
            print(f"     error: {error}")
            failed.append((issue_id, error))

    print()
    print("=" * 70)
    print(f"RESULTS: {len(passed)} passed, {len(failed)} failed, {len(no_test)} no-test")

    if passed:
        print(f"\n✓ PASSED (flip DONE → VERIFIED):")
        for iid in passed:
            print(f"  - {iid}")
        # Update the file
        _update_issue_statuses(passed, "VERIFIED")
        print(f"\n  → ISSUE_OWNERSHIP.md updated. Commit + push to share.")

    if failed:
        print(f"\n✗ FAILED (flip DONE → REOPENED):")
        for iid, err in failed:
            print(f"  - {iid}: {err[:80]}")
        _update_issue_statuses([iid for iid, _ in failed], "REOPENED")
        print(f"\n  → ISSUE_OWNERSHIP.md updated. These issues are now AVAILABLE for re-claiming.")
        print(f"  → A second run should claim and re-fix these.")

    if no_test:
        print(f"\n⚠ NO TEST (no verification check defined):")
        for iid in no_test:
            print(f"  - {iid}")

    return 0 if not failed else 1


def _update_issue_statuses(issue_ids: list[str], new_status: str) -> None:
    """Update the STATUS (and VERIFIED_AT) column for the given issues in ISSUE_OWNERSHIP.md."""
    import datetime
    content = OWNERSHIP_FILE.read_text()
    now = datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%MZ")

    for issue_id in issue_ids:
        # Match the issue row and replace STATUS (4th column) with new_status
        # and VERIFIED_AT (10th column) with now (if VERIFIED) or "—" (if REOPENED)
        pattern = re.compile(
            r"^((" + re.escape(issue_id) + r")\s*\|\s*[^|]*?\s*\|\s*\S+\s*\|\s*)(\S+)(\s*\|\s*[^|]*?\s*\|\s*[^|]*?\s*\|\s*[^|]*?\s*\|\s*[^|]*?\s*\|\s*[^|]*?\s*\|\s*)([^|]*?)(\s*\|\s*.*)$",
            re.MULTILINE,
        )
        m = pattern.search(content)
        if m:
            prefix, _, _, middle, old_verified, suffix = m.groups()
            new_verified = now if new_status == "VERIFIED" else "—"
            content = content.replace(
                m.group(0),
                prefix + new_status + middle + new_verified + suffix,
            )

    OWNERSHIP_FILE.write_text(content)


def cmd_list(args: list[str]) -> int:
    """List issues by status. Usage: list [STATUS]"""
    if not OWNERSHIP_FILE.exists():
        print("✗ ISSUE_OWNERSHIP.md not found")
        return 1

    issues, _ = parse_ownership_map()
    filter_status = args[0].upper() if args else None

    by_status: dict[str, list] = {}
    for iid, issue in issues.items():
        status = issue["status"].upper()
        by_status.setdefault(status, []).append((iid, issue))

    for status in ("AVAILABLE", "CLAIMED", "DONE", "VERIFIED", "REOPENED", "WONTFIX"):
        if filter_status and status != filter_status:
            continue
        items = by_status.get(status, [])
        if not items:
            continue
        print(f"\n=== {status} ({len(items)}) ===")
        for iid, issue in items:
            title = issue.get("title", "")[:60]
            agent = issue.get("agent_id", "—")
            print(f"  {iid} | {title} | agent={agent}")

    return 0


def cmd_status(args: list[str]) -> int:
    """Show summary counts. Usage: status"""
    if not OWNERSHIP_FILE.exists():
        print("✗ ISSUE_OWNERSHIP.md not found")
        return 1

    issues, _ = parse_ownership_map()
    by_status: dict[str, int] = {}
    for issue in issues.values():
        status = issue["status"].upper()
        by_status[status] = by_status.get(status, 0) + 1

    total = sum(by_status.values())
    print(f"\nISSUE OWNERSHIP SUMMARY (total: {total})")
    print("=" * 40)
    for status in ("AVAILABLE", "CLAIMED", "DONE", "VERIFIED", "REOPENED", "WONTFIX"):
        count = by_status.get(status, 0)
        if count:
            bar = "█" * min(count, 30)
            print(f"  {status:12s} {count:3d} {bar}")
    print()
    print("Key:")
    print("  AVAILABLE  — claim it")
    print("  CLAIMED    — someone is working on it")
    print("  DONE       — fix merged, NOT verified (run `verify`)")
    print("  VERIFIED   — fix merged AND tests pass")
    print("  REOPENED   — was DONE/VERIFIED but tests now fail — re-claim")
    return 0


if __name__ == "__main__":
    sys.exit(main())
