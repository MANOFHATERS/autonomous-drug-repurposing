#!/usr/bin/env python3
"""RT-013 ROOT FIX (Team Member 17): deprecated shim.

run_real_pipeline.py has been moved to scripts/legacy/run_real_pipeline.py.
The canonical 4-phase runner is now run_4phase.py per ORCH-003.
"""
from __future__ import annotations

import sys
import subprocess
from pathlib import Path

HERE = Path(__file__).resolve().parent
CANONICAL = HERE / "run_4phase.py"


def main() -> int:
    print(
        "DEPRECATION WARNING (RT-013): run_real_pipeline.py is DEPRECATED. "
        "The canonical 4-phase runner is run_4phase.py. This shim "
        "delegates to run_4phase.py with default arguments. To silence "
        "this warning, update your scripts to call run_4phase.py "
        "directly. The original is preserved at scripts/legacy/run_real_pipeline.py.",
        file=sys.stderr,
    )
    if not CANONICAL.exists():
        print(f"ERROR: {CANONICAL} not found.", file=sys.stderr)
        return 2
    return subprocess.call([sys.executable, str(CANONICAL), *sys.argv[1:]])


if __name__ == "__main__":
    sys.exit(main())
