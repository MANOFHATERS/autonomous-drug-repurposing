#!/usr/bin/env python3
"""
Package the upgraded DrugOS codebase into a zip file for delivery.

Includes:
- src/ (frontend + API routes)
- prisma/ (schema)
- public/ (static assets)
- scripts/ (utility scripts)
- package.json, tsconfig.json, next.config.ts, tailwind.config.ts,
  postcss.config.mjs, eslint.config.mjs, components.json, Caddyfile
- .env.example, README.md, UPGRADE_NOTES.md, worklog.md

Excludes:
- node_modules/, .next/, .git/, .zscripts/, db/, dev.log, server.log
- src_backup/, prisma_backup/, public_backup/, scripts_backup/, uploaded_code/
- tests/, examples/ (kept separate, not required to run the app)

IN-078 ROOT FIX (Teammate 13, MEDIUM): the previous version hardcoded
`PROJECT_DIR = Path("/home/z/my-project")` — a path that exists only on
the original developer's machine. On any other machine (CI, another
laptop, Docker) the script failed with FileNotFoundError or wrote the
zip to the wrong location. It ALSO embedded an inline `.env.example`
template that hardcoded `DATABASE_URL=file:./db/custom.db` (SQLite),
which CONTRADICTS the docker-compose.yml Postgres DATABASE_URL — an
operator who unpacked the zip and used the embedded template would get
a broken Postgres setup.

ROOT FIX:
  1. `PROJECT_DIR = Path(__file__).resolve().parent.parent` computes the
     frontend directory relative to this script (frontend/scripts/), so
     the script is portable across machines.
  2. Removed the inline `.env.example` template entirely. The real
     `frontend/.env.example` file is included via INCLUDE_FILES, so the
     shipped template matches the actual deployment (Postgres, not
     SQLite). No more contradictory embedded template.
  3. Optional files (README.md, UPGRADE_NOTES.md, worklog.md) are
     skipped with a clear message if missing, not treated as fatal.
  4. Added `--output` CLI arg so CI can override the destination.
"""

import os
import sys
import zipfile
from pathlib import Path

# IN-078: portable root — frontend/ is the parent of scripts/.
SCRIPT_DIR = Path(__file__).resolve().parent
PROJECT_DIR = SCRIPT_DIR.parent  # frontend/
DEFAULT_OUT_ZIP = PROJECT_DIR / "download" / "drugos_v0.6.0_upgraded.zip"


def _parse_args() -> Path:
    """Parse --output to allow CI to override the zip destination."""
    out = DEFAULT_OUT_ZIP
    args = sys.argv[1:]
    for i, arg in enumerate(args):
        if arg == "--output" and i + 1 < len(args):
            out = Path(args[i + 1]).resolve()
        elif arg.startswith("--output="):
            out = Path(arg.split("=", 1)[1]).resolve()
    return out


OUT_ZIP = _parse_args()

INCLUDE_DIRS = [
    "src",
    "prisma",
    "public",
    "scripts",
]

INCLUDE_FILES = [
    "package.json",
    "tsconfig.json",
    "next.config.ts",
    "tailwind.config.ts",
    "postcss.config.mjs",
    "eslint.config.mjs",
    "components.json",
    "Caddyfile",
    # IN-078: ship the REAL .env.example file (Postgres-aligned) instead of
    # an inline SQLite template that contradicted docker-compose.yml.
    ".env.example",
    ".gitignore",
    "README.md",
    "UPGRADE_NOTES.md",
    "worklog.md",
]

EXCLUDE_PATTERNS = [
    "node_modules",
    ".next",
    ".git",
    "__pycache__",
    ".DS_Store",
    "*.log",
    "*.db",
    "*.db-journal",
]


def should_exclude(path: Path) -> bool:
    parts = path.parts
    for pattern in EXCLUDE_PATTERNS:
        if pattern.startswith("*"):
            if path.name.endswith(pattern[1:]):
                return True
        elif pattern in parts:
            return True
        elif path.name == pattern:
            return True
    return False


def main() -> int:
    # Make sure output dir exists
    OUT_ZIP.parent.mkdir(parents=True, exist_ok=True)

    if OUT_ZIP.exists():
        OUT_ZIP.unlink()

    file_count = 0
    with zipfile.ZipFile(OUT_ZIP, "w", zipfile.ZIP_DEFLATED, compresslevel=6) as zf:
        # Write a top-level folder `drugos_v0.6.0_upgraded/` so unzip creates a clean dir.
        TOP = "drugos_v0.6.0_upgraded"

        # Directories
        for d in INCLUDE_DIRS:
            src_dir = PROJECT_DIR / d
            if not src_dir.exists():
                print(f"  SKIP (missing): {d}/")
                continue
            for root, dirs, files in os.walk(src_dir):
                # Prune excluded dirs in-place so os.walk doesn't descend into them.
                dirs[:] = [x for x in dirs if x not in ("node_modules", ".next", ".git", "__pycache__")]
                for fname in files:
                    abs_fp = Path(root) / fname
                    # IN-078: guard against files vanishing mid-walk.
                    if not abs_fp.exists():
                        continue
                    rel = abs_fp.relative_to(PROJECT_DIR)
                    if should_exclude(rel):
                        continue
                    arcname = f"{TOP}/{rel.as_posix()}"
                    zf.write(abs_fp, arcname)
                    file_count += 1

        # Files (optional ones are skipped if missing — not fatal).
        for f in INCLUDE_FILES:
            src_fp = PROJECT_DIR / f
            if not src_fp.exists() or not src_fp.is_file():
                print(f"  SKIP (missing): {f}")
                continue
            arcname = f"{TOP}/{f}"
            zf.write(src_fp, arcname)
            file_count += 1

        # IN-078: the inline .env.example template that hardcoded
        # `DATABASE_URL=file:./db/custom.db` (SQLite, contradicting the
        # Postgres docker-compose setup) has been REMOVED. The real
        # frontend/.env.example is now shipped via INCLUDE_FILES above.

    size_mb = OUT_ZIP.stat().st_size / (1024 * 1024)
    print(f"\n✓ Created {OUT_ZIP}")
    print(f"  Files: {file_count}")
    print(f"  Size:  {size_mb:.2f} MB")
    return 0


if __name__ == "__main__":
    sys.exit(main())
