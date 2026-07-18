#!/usr/bin/env python3
"""IN-080 ROOT FIX: Generate lockfiles for phase2/drugos_graph, graph_transformer, rl.

These 3 requirements files share most deps with the root requirements.txt.
This script reuses hashes from the root requirements.lock (already generated)
and only downloads + hashes NEW deps not in the root lockfile.

Usage:
    python scripts/generate_lockfiles_from_root.py
"""
from __future__ import annotations

import hashlib
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Dict, List, Optional, Tuple

REPO_ROOT = Path(__file__).resolve().parents[1]
ROOT_LOCK = REPO_ROOT / "requirements.lock"
DOWNLOAD_DIR = Path("/tmp/in080_whl2")

# The 3 remaining requirements files to generate lockfiles for.
REQUIREMENTS_FILES = [
    "phase2/drugos_graph/requirements.txt",
    "graph_transformer/requirements.txt",
    "rl/requirements.txt",
]


def parse_root_lock() -> Dict[str, str]:
    """Parse the root requirements.lock and return a dict of {pkg_spec: hash_line}.

    The dict maps the FULL package spec (e.g., 'requests>=2.31.0,<3.0') to
    the hash line (e.g., '--hash=sha256:abc123...').
    """
    if not ROOT_LOCK.exists():
        print(f"ERROR: {ROOT_LOCK} does not exist. Run generate_lockfiles.py first.")
        sys.exit(1)

    hashes: Dict[str, str] = {}
    current_spec: Optional[str] = None
    current_hash: Optional[str] = None

    for line in ROOT_LOCK.read_text(encoding="utf-8").splitlines():
        line = line.rstrip()
        if not line or line.startswith("#"):
            continue
        # A line ending with backslash starts a new spec
        if line.endswith("\\") and not line.strip().startswith("--"):
            # Save the previous spec if any
            if current_spec and current_hash:
                hashes[current_spec] = current_hash
            # New spec
            current_spec = line[:-1].strip()
            current_hash = None
        elif line.strip().startswith("--hash="):
            # Hash line for the current spec
            if current_spec:
                if current_hash is None:
                    current_hash = line.strip()
                else:
                    # Multiple hashes (wheel + sdist) -- concatenate
                    current_hash = current_hash + " " + line.strip()
    # Save the last one
    if current_spec and current_hash:
        hashes[current_spec] = current_hash

    print(f"Loaded {len(hashes)} package hashes from root lockfile")
    return hashes


def parse_requirements(req_file: Path) -> List[str]:
    """Parse a requirements.txt, returning a list of package specs."""
    specs: List[str] = []
    for raw_line in req_file.read_text(encoding="utf-8").splitlines():
        line = raw_line.split("#", 1)[0].strip()
        if not line or line.startswith("-"):
            continue
        line = line.split(";", 1)[0].strip()
        if not line:
            continue
        specs.append(line)
    return specs


def download_and_hash(pkg_spec: str) -> Optional[str]:
    """Download a package and compute its sha256 hash.

    Returns the hash line (e.g., '--hash=sha256:abc...') or None on failure.
    """
    if DOWNLOAD_DIR.exists():
        shutil.rmtree(DOWNLOAD_DIR)
    DOWNLOAD_DIR.mkdir(parents=True, exist_ok=True)

    cmd = [
        sys.executable, "-m", "pip", "download",
        "--no-deps", "--dest", str(DOWNLOAD_DIR),
        "--no-cache-dir", pkg_spec,
    ]
    try:
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=45)
        if result.returncode != 0:
            return None
        files = sorted(DOWNLOAD_DIR.glob("*"))
        if not files:
            return None
        hash_lines = []
        for f in files:
            h = hashlib.sha256()
            with open(f, "rb") as fh:
                for chunk in iter(lambda: fh.read(8192), b""):
                    h.update(chunk)
            hash_lines.append(f"--hash=sha256:{h.hexdigest()}")
        return " ".join(hash_lines)
    except Exception:
        return None
    finally:
        if DOWNLOAD_DIR.exists():
            shutil.rmtree(DOWNLOAD_DIR)


def generate_lockfile(req_file: Path, root_hashes: Dict[str, str]) -> Path:
    """Generate a hash-pinned .lock file for a requirements.txt."""
    lock_file = req_file.with_suffix(".lock")
    specs = parse_requirements(req_file)
    print(f"\n=== Generating {lock_file.relative_to(REPO_ROOT)} ===")
    print(f"  {len(specs)} direct dependencies")

    lines: List[str] = [
        f"# Hash-pinned lockfile for {req_file.name}",
        f"# IN-080 ROOT FIX: supply-chain integrity via sha256 hashes.",
        f"# Install with: pip install --require-hashes -r {lock_file.name}",
        f"# Regenerate with: python scripts/generate_lockfiles_from_root.py",
        f"# NOTE: hashes are reused from the root requirements.lock where the",
        f"# same dep+version appears. New deps are downloaded + hashed fresh.",
        "",
    ]

    reused = 0
    fresh = 0
    failed = 0
    for spec in specs:
        # Normalize the spec for lookup (strip whitespace)
        if spec in root_hashes:
            # Reuse hash from root lockfile
            hash_line = root_hashes[spec]
            lines.append(f"{spec} \\")
            lines.append(f"    {hash_line}")
            lines.append("")
            reused += 1
            print(f"  REUSED: {spec}")
        else:
            # Try to download + hash
            print(f"  DOWNLOADING: {spec} ...", end=" ", flush=True)
            hash_line = download_and_hash(spec)
            if hash_line:
                lines.append(f"{spec} \\")
                lines.append(f"    {hash_line}")
                lines.append("")
                fresh += 1
                print("OK")
            else:
                lines.append(f"# WARNING: {spec} -- could not generate hash")
                lines.append(f"#   (platform-specific wheel or download timeout).")
                lines.append(f"#   Manually verify or install without --require-hashes.")
                lines.append("")
                failed += 1
                print("FAILED")

    lock_file.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(f"  Result: {reused} reused, {fresh} fresh, {failed} failed")
    return lock_file


def main() -> int:
    print("=" * 70)
    print("IN-080 ROOT FIX: Generating lockfiles for remaining 3 requirements files")
    print("=" * 70)

    os.chdir(REPO_ROOT)
    root_hashes = parse_root_lock()

    for rel_path in REQUIREMENTS_FILES:
        req_file = REPO_ROOT / rel_path
        if not req_file.exists():
            print(f"SKIP: {req_file} does not exist")
            continue
        generate_lockfile(req_file, root_hashes)

    print("\n" + "=" * 70)
    print("DONE")
    print("=" * 70)
    return 0


if __name__ == "__main__":
    sys.exit(main())
