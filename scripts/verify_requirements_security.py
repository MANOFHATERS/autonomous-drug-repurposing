"""IN-080 v122 FORENSIC ROOT FIX (Teammate 7) — Requirements security audit.

The audit (IN-080) found that NO requirements file in the project uses
`--require-hashes` or has an associated `.lock` file with hashes. pip's
version resolver guarantees version resolution but NOT integrity — a
compromised PyPI mirror or a man-in-the-middle attack on the download
can substitute a malicious wheel with the same version number. Without
hash verification, the malicious wheel is installed silently.

FULL ROOT FIX requires generating locked requirements files with hashes:
    pip-compile --generate-hashes requirements.in -o requirements.lock
    pip install --require-hashes -r requirements.lock

That's a major infra change (pip-tools, CI integration, every dep needs
hash pinning). As an INTERIM forensic root fix, this script enforces:

1. Every requirements file in the repo has UPPER BOUNDS on every
   dependency (no unbounded `>=` without `<`). Unbounded pins allow pip
   to install breaking major versions silently — a "works in CI, breaks
   in prod" hazard (IN-018 fix scope).

2. No DUPLICATE declarations of the same package in the same file
   (graph_transformer/requirements.txt had `torch>=2.0` and
   `torch>=2.13.0` — pip silently picked one, the other was dead weight
   and confused maintainers).

3. No references to package versions that DO NOT EXIST on PyPI (e.g.,
   `scikit-learn>=1.9.0` when the latest scikit-learn is 1.5.x — pip
   would either error out OR pull an arbitrary version).

4. Apache-airflow is pinned to the EXACT base image version (`==2.10.5`)
   in BOTH root requirements.txt and phase1/requirements.txt (IN-068 fix).

5. Documents the path to full hash-based installs in a SECURITY.md note.

This script is intended to be run in CI (pre-merge gate) and locally
(pre-commit hook). It exits non-zero on any violation.

Usage:
    python scripts/verify_requirements_security.py
    python scripts/verify_requirements_security.py --strict  # fail on warnings

Exit codes:
    0 — all checks passed
    1 — one or more checks failed
"""
from __future__ import annotations

import argparse
import os
import re
import sys
from pathlib import Path
from typing import Dict, List, Set, Tuple

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

REPO_ROOT = Path(__file__).resolve().parent.parent

# All requirements files in the repo that this script audits.
REQUIREMENTS_FILES = [
    REPO_ROOT / "requirements.txt",
    REPO_ROOT / "requirements-dev.txt",
    REPO_ROOT / "phase1" / "requirements.txt",
    REPO_ROOT / "phase1" / "requirements-dev.txt",
    REPO_ROOT / "phase2" / "drugos_graph" / "requirements.txt",
    REPO_ROOT / "graph_transformer" / "requirements.txt",
    REPO_ROOT / "rl" / "requirements.txt",
]

# Apache Airflow base image version (IN-068). Both requirements files
# that declare apache-airflow MUST pin to this exact version.
AIRFLOW_BASE_IMAGE_VERSION = "2.10.5"

# Known PyPI package versions for sanity-checking declared minimums.
# This is a STATIC allowlist — it does NOT query PyPI (which would make
# the audit network-dependent and slow). When a package is upgraded
# beyond what's listed here, update the allowlist. Packages NOT in this
# list are skipped (the audit cannot know every package's latest version
# without a network call). The list focuses on packages that have been
# observed with non-existent version pins in this repo's history.
KNOWN_LATEST_VERSIONS: Dict[str, Tuple[int, ...]] = {
    # core ML
    "torch": (2, 5, 1),  # latest stable as of 2024-12
    "torch-geometric": (2, 6, 1),
    "torch-scatter": (2, 1, 2),
    "torch-sparse": (0, 6, 18),
    "scikit-learn": (1, 5, 2),  # 1.9.0 does NOT exist
    "scipy": (1, 14, 1),  # 1.18.0 does NOT exist
    "numpy": (2, 1, 3),
    "pandas": (2, 2, 3),
    # web
    "fastapi": (0, 115, 6),  # 0.139.2 does NOT exist
    "uvicorn": (0, 32, 1),
    # bio/chem
    "rdkit": (2024, 9, 6),  # 2026.3.4 does NOT exist
    "biopython": (1, 84),
    # data
    "requests": (2, 32, 3),
    "certifi": (2024, 8, 30),  # 2026.6.17 does NOT exist
    "sqlalchemy": (2, 0, 36),
    "psycopg2-binary": (2, 9, 10),
    "pyarrow": (17, 0, 0),
    # airflow
    "apache-airflow": (2, 10, 5),
    # misc
    "pyyaml": (6, 0, 2),
    "lxml": (5, 3, 0),
    "rapidfuzz": (3, 10, 1),
    "python-dotenv": (1, 0, 1),
    "prometheus-client": (0, 21, 0),
    "psutil": (6, 1, 0),
    "mlflow": (2, 17, 1),
    "transformers": (4, 46, 3),
    "networkx": (3, 4, 2),
    "neo4j": (5, 26, 0),
    "gymnasium": (1, 0, 0),
    "stable-baselines3": (2, 4, 0),
    "filelock": (3, 16, 1),
}

# Pattern matching a pip requirement line with version specifier(s).
# Captures: package_name (group 1), version_specifier (group 2).
# Examples matched:
#   torch>=2.0
#   apache-airflow==2.10.5
#   pandas>=2.1.4,<3.0
#   rdkit>=2024.3.1,<2025.0; python_version>="3.8"
REQ_PATTERN = re.compile(
    r"""^
    (?P<name>[A-Za-z0-9_.\-]+)       # package name
    \s*
    (?P<specs>(?:[<>=!~]=?\s*[0-9A-Za-z.*+!\-]+(?:\s*,\s*)?)+)  # version specs
    (?P<rest>\s*(?:;.*)?)?            # optional env marker + trailing comment
    \s*$
    """,
    re.VERBOSE,
)

# Pattern matching just the package name (for unbounded `>=` lines like
# `requests>=2.31.0` with no upper bound).
UNBOUNDED_GE_PATTERN = re.compile(
    r"^([A-Za-z0-9_.\-]+)\s*>=\s*([0-9A-Za-z.*+!\-]+)\s*$"
)


# ---------------------------------------------------------------------------
# Audit logic
# ---------------------------------------------------------------------------


def _parse_version(v: str) -> Tuple[int, ...]:
    """Parse a version string like '2.10.5' into (2, 10, 5)."""
    parts: List[int] = []
    for part in re.split(r"[.\-]", v):
        if part.isdigit():
            parts.append(int(part))
    return tuple(parts)


def _strip_inline_comment(line: str) -> str:
    """Strip an inline comment from a requirements line."""
    # Comments start with `#` NOT inside a quoted string. requirements.txt
    # has no quoted strings, so a simple split is safe.
    if "#" in line:
        return line[: line.index("#")].rstrip()
    return line.rstrip()


def _extract_minimum_version(specs: str) -> str | None:
    """Extract the minimum version from a specifier string like '>=2.10.0,<3.0.0'.

    Returns the version string after the first `>=`, or None if no `>=`.
    """
    m = re.search(r">=\s*([0-9A-Za-z.*+!\-]+)", specs)
    if m:
        return m.group(1).strip()
    return None


def _check_upper_bound_present(specs: str) -> bool:
    """Return True if the specifier has an upper bound (`<` or `<=`)."""
    return "<" in specs


def audit_file(path: Path) -> Tuple[List[str], List[str], List[str]]:
    """Audit a single requirements file.

    Returns:
        Tuple of (errors, warnings, info_messages).
    """
    errors: List[str] = []
    warnings: List[str] = []
    info: List[str] = []

    if not path.exists():
        info.append(f"SKIP (file not found): {path}")
        return errors, warnings, info

    info.append(f"Auditing {path.relative_to(REPO_ROOT)}")

    seen_packages: Dict[str, str] = {}  # name -> first declaration line

    with open(path, "r", encoding="utf-8") as f:
        for lineno, raw_line in enumerate(f, 1):
            line = _strip_inline_comment(raw_line)
            if not line.strip():
                continue
            # Skip options like --index-url, -r, etc.
            if line.lstrip().startswith(("-", "git+", "http://", "https://")):
                continue
            # Skip environment markers like `; python_version >= "3.8"`
            # by stripping them from the line for matching (but keep the
            # original for error messages).
            base_line = re.sub(r"\s*;.*$", "", line).strip()

            # Check for unbounded `>=` (no upper bound).
            m_unbounded = UNBOUNDED_GE_PATTERN.match(base_line)
            if m_unbounded:
                pkg = m_unbounded.group(1)
                errors.append(
                    f"{path.name}:{lineno}: UNBOUNDED `>=` on `{pkg}` "
                    f"(no upper `<` bound). pip may install a breaking "
                    f"future major version. Add `,<{_next_major(m_unbounded.group(2))}.0` "
                    f"or similar. (IN-018/IN-080)"
                )

            # Full match for general specifier parsing.
            m = REQ_PATTERN.match(base_line)
            if not m:
                # Not a recognizable requirement line; skip silently
                # (could be a continuation, blank, or option).
                continue
            pkg_name = m.group("name")
            specs = m.group("specs")

            # Duplicate declaration check.
            if pkg_name.lower() in seen_packages:
                errors.append(
                    f"{path.name}:{lineno}: DUPLICATE declaration of "
                    f"`{pkg_name}`. First seen at line {seen_packages[pkg_name.lower()]}. "
                    f"Multiple declarations confuse pip (it picks one silently) "
                    f"and confuse maintainers. Consolidate into a single line."
                )
            else:
                seen_packages[pkg_name.lower()] = str(lineno)

            # Upper bound check (re-checked for non-`>=`-only specifiers).
            if not _check_upper_bound_present(specs) and not specs.startswith("=="):
                # Allow exact pins (==) without upper bound (they ARE the bound).
                if not specs.startswith("=="):
                    errors.append(
                        f"{path.name}:{lineno}: NO upper bound on `{pkg_name}` "
                        f"(specs=`{specs}`). Add `,<X.Y.Z` to prevent breaking "
                        f"upgrades. (IN-018/IN-080)"
                    )

            # Non-existent version check (only for `>=` minimums).
            min_ver = _extract_minimum_version(specs)
            if min_ver and pkg_name.lower() in KNOWN_LATEST_VERSIONS:
                latest = KNOWN_LATEST_VERSIONS[pkg_name.lower()]
                parsed_min = _parse_version(min_ver)
                if parsed_min > latest:
                    errors.append(
                        f"{path.name}:{lineno}: NON-EXISTENT version pin: "
                        f"`{pkg_name}>={min_ver}`. The latest released "
                        f"`{pkg_name}` is {'.'.join(map(str, latest))}. "
                        f"`pip install` will FAIL with "
                        f"`No matching distribution found for {pkg_name}`. "
                        f"This is a critical production-breaking bug."
                    )

            # IN-068: apache-airflow MUST be pinned to ==2.10.5.
            if pkg_name.lower() == "apache-airflow":
                if not re.search(r"==\s*2\.10\.5", specs):
                    errors.append(
                        f"{path.name}:{lineno}: apache-airflow is pinned to "
                        f"`{specs}` but MUST be `==2.10.5` to match the "
                        f"base image apache/airflow:2.10.5-python3.11. "
                        f"Looser pins allow pip to upgrade the base image's "
                        f"Airflow, breaking pre-installed provider packages. (IN-068)"
                    )

    return errors, warnings, info


def _next_major(version: str) -> str:
    """Return the next major version string. '2.10.5' -> '3'."""
    parts = _parse_version(version)
    if parts:
        return str(parts[0] + 1)
    return "X"


def main() -> int:
    parser = argparse.ArgumentParser(
        description="IN-080 v122: audit requirements files for security hazards."
    )
    parser.add_argument(
        "--strict",
        action="store_true",
        help="Treat warnings as failures (exit 1).",
    )
    parser.add_argument(
        "--quiet",
        action="store_true",
        help="Suppress INFO messages (only show errors/warnings).",
    )
    args = parser.parse_args()

    all_errors: List[str] = []
    all_warnings: List[str] = []

    for path in REQUIREMENTS_FILES:
        errors, warnings, info = audit_file(path)
        all_errors.extend(errors)
        all_warnings.extend(warnings)
        if not args.quiet:
            for line in info:
                print(line)
            for line in warnings:
                print(f"  WARN: {line}")
            for line in errors:
                print(f"  ERROR: {line}")

    print()
    print("=" * 70)
    print(f"AUDIT SUMMARY: {len(all_errors)} error(s), {len(all_warnings)} warning(s)")
    print("=" * 70)
    if all_errors:
        print("\nERRORS (must fix before merge):")
        for e in all_errors:
            print(f"  - {e}")
    if all_warnings:
        print("\nWARNINGS (review recommended):")
        for w in all_warnings:
            print(f"  - {w}")

    # IN-080 documentation note.
    print("\nIN-080 NOTE: full hash-based installs require generating locked")
    print("requirements files with hashes (pip-compile --generate-hashes) and")
    print("installing with `pip install --require-hashes -r requirements.lock`.")
    print("This script enforces the INTERIM controls (upper bounds, no")
    print("duplicates, no non-existent versions, exact airflow pin). The full")
    print("hash-based install is tracked as a separate infrastructure task.")
    print()
    print("Recommended next steps for full IN-080 closure:")
    print("  1. pip install pip-tools")
    print("  2. For each requirements.txt, create a requirements.in with the")
    print("     same content, then: pip-compile --generate-hashes requirements.in -o requirements.lock")
    print("  3. Install with: pip install --require-hashes -r requirements.lock")
    print("  4. Add `pip-audit` to CI to scan for known CVEs.")
    print("  5. Pin pip index URL to a trusted mirror: --index-url https://pypi.org/simple/")

    if all_errors or (args.strict and all_warnings):
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
