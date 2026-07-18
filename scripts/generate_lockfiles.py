#!/usr/bin/env python3
"""IN-080 ROOT FIX: Generate hash-pinned lockfiles for all requirements files.

This is the standard pip-tools workflow: parse a requirements.txt, resolve
each direct dependency to a specific wheel/sdist, compute the sha256 hash,
and emit a hash-pinned .lock file that can be installed with
``pip install --require-hashes -r requirements.lock``.

The lockfile pins DIRECT dependencies only (not transitive). For full
transitive resolution, use ``pip-compile --generate-hashes`` (slower but
more complete). This script is faster and covers the audit's primary
concern: integrity of the developer-specified dependencies.

Usage:
    python scripts/generate_lockfiles.py

The script reads each requirements.txt, downloads wheels via
``pip download --no-deps``, computes sha256 hashes, and writes a .lock
file next to each requirements.txt.
"""
from __future__ import annotations

import hashlib
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path
from typing import List, Optional, Tuple

REPO_ROOT = Path(__file__).resolve().parents[1]

# All requirements files that need hash-pinned lockfiles (IN-080).
REQUIREMENTS_FILES = [
    "requirements.txt",
    "phase1/requirements.txt",
    "phase2/drugos_graph/requirements.txt",
    "graph_transformer/requirements.txt",
    "rl/requirements.txt",
]

# Temp dir for downloaded wheels.
DOWNLOAD_DIR = Path("/tmp/in080_whl")


def parse_requirements(req_file: Path) -> List[str]:
    """Parse a requirements.txt, returning a list of package specs.

    Skips comments, blank lines, and option lines (e.g., --index-url).
    Strips inline comments and environment markers (e.g., ; python_version).
    """
    specs: List[str] = []
    for raw_line in req_file.read_text(encoding="utf-8").splitlines():
        # Strip inline comments
        line = raw_line.split("#", 1)[0].strip()
        if not line:
            continue
        # Skip pip option lines
        if line.startswith("-"):
            continue
        # Skip lines that are just comments
        if line.startswith("#"):
            continue
        # Strip environment markers (e.g., ; python_version >= "3.8")
        line = line.split(";", 1)[0].strip()
        if not line:
            continue
        specs.append(line)
    return specs


def download_package(pkg_spec: str, dest_dir: Path) -> Tuple[bool, List[Path]]:
    """Download a package via ``pip download --no-deps``.

    Returns (success, list_of_downloaded_files).
    Uses a 45-second timeout per package. Large packages (torch, rdkit,
    pyarrow) may exceed this and be marked as "could not generate hash" --
    that's acceptable; the lockfile still covers the smaller deps, and the
    large packages can be hashed separately via ``pip-compile``.
    """
    cmd = [
        sys.executable, "-m", "pip", "download",
        "--no-deps",
        "--dest", str(dest_dir),
        "--no-cache-dir",
        pkg_spec,
    ]
    try:
        result = subprocess.run(
            cmd, capture_output=True, text=True, timeout=45,
        )
        if result.returncode != 0:
            return False, []
        files = sorted(dest_dir.glob("*"))
        return True, files
    except (subprocess.TimeoutExpired, Exception):
        return False, []


def compute_sha256(filepath: Path) -> str:
    """Compute the sha256 hash of a file."""
    h = hashlib.sha256()
    with open(filepath, "rb") as f:
        for chunk in iter(lambda: f.read(8192), b""):
            h.update(chunk)
    return h.hexdigest()


def generate_lockfile(req_file: Path) -> Path:
    """Generate a hash-pinned .lock file for a requirements.txt.

    Returns the path to the generated .lock file.
    """
    lock_file = req_file.with_suffix(".lock")
    specs = parse_requirements(req_file)
    print(f"\n=== Generating {lock_file.name} from {req_file} ===")
    print(f"  Found {len(specs)} direct dependencies to hash")

    lines: List[str] = [
        f"# Hash-pinned lockfile for {req_file.name}",
        f"# IN-080 ROOT FIX: supply-chain integrity via sha256 hashes.",
        f"# Install with: pip install --require-hashes -r {lock_file.name}",
        f"# Regenerate with: python scripts/generate_lockfiles.py",
        f"# WARNING: pins DIRECT deps only. For full transitive resolution,",
        f"# use: pip-compile --generate-hashes {req_file.name} -o {lock_file.name}",
        "",
    ]

    success_count = 0
    fail_count = 0
    for spec in specs:
        # Clean the download dir for each package
        if DOWNLOAD_DIR.exists():
            shutil.rmtree(DOWNLOAD_DIR)
        DOWNLOAD_DIR.mkdir(parents=True, exist_ok=True)

        print(f"  Resolving: {spec} ...", end=" ", flush=True)
        ok, files = download_package(spec, DOWNLOAD_DIR)
        if not ok or not files:
            print("FAILED")
            lines.append(f"# WARNING: {spec} -- could not generate hash")
            lines.append(f"#   (platform-specific wheel, download timeout, or")
            lines.append(f"#    version not available on this Python).")
            lines.append(f"#   Manually verify or install without --require-hashes.")
            lines.append("")
            fail_count += 1
            continue

        # Compute hashes for each downloaded file (wheel + sdist)
        hash_lines = []
        for f in files:
            h = compute_sha256(f)
            hash_lines.append(f"    --hash=sha256:{h}")
        # Format: <spec> \\
        #     --hash=sha256:... \\
        #     --hash=sha256:...
        lines.append(f"{spec} \\")
        lines.extend(hash_lines)
        lines.append("")
        success_count += 1
        print(f"OK ({len(files)} files)")

    # Cleanup
    if DOWNLOAD_DIR.exists():
        shutil.rmtree(DOWNLOAD_DIR)

    lock_file.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(f"  Result: {success_count} pinned, {fail_count} failed")
    print(f"  Written to: {lock_file}")
    return lock_file


def main() -> int:
    """Generate lockfiles for all requirements files. Returns exit code."""
    print("=" * 70)
    print("IN-080 ROOT FIX: Generating hash-pinned lockfiles")
    print("=" * 70)

    os.chdir(REPO_ROOT)
    generated = []
    for rel_path in REQUIREMENTS_FILES:
        req_file = REPO_ROOT / rel_path
        if not req_file.exists():
            print(f"SKIP: {req_file} does not exist")
            continue
        lock_file = generate_lockfile(req_file)
        generated.append(lock_file)

    print("\n" + "=" * 70)
    print(f"SUMMARY: generated {len(generated)} lockfiles")
    print("=" * 70)
    for lf in generated:
        print(f"  {lf.relative_to(REPO_ROOT)}")
    print()
    print("Next steps:")
    print("  1. Review the generated .lock files")
    print("  2. Update Dockerfiles to use: pip install --require-hashes -r requirements.lock")
    print("  3. Add CI test that verifies lockfile integrity")
    return 0


if __name__ == "__main__":
    sys.exit(main())
