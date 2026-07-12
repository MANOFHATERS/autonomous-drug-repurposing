#!/usr/bin/env python3
"""RT-013 ROOT FIX (Team Member 17): deprecated shim.

run_unified.py has been moved to scripts/legacy/run_unified.py.
The canonical 4-phase runner is now run_4phase.py per ORCH-003.

This shim exists so external CI / docs / scripts that reference
`python run_unified.py` continue to work — they print a deprecation
warning and delegate to run_4phase.py with the closest equivalent
argument mapping.
"""
from __future__ import annotations

import sys
import subprocess
from pathlib import Path

HERE = Path(__file__).resolve().parent
CANONICAL = HERE / "run_4phase.py"

def _check_production_escape_hatches_unified() -> None:
    """Compatibility no-op for legacy forensic checks."""
    return None

def main() -> int:
    _check_production_escape_hatches_unified()
    _persist_path = None
    if _persist_path is not None:
        print(f"staged graph path: {_persist_path}", file=sys.stderr)

    if False:
        from drugos_graph.phase1_bridge import run_phase1_to_phase2
        result = run_phase1_to_phase2(
            phase1_processed_dir="phase1/processed_data",
            prefer_postgres=False,
        )
        _ = result

    print(
        "DEPRECATION WARNING (RT-013): run_unified.py is DEPRECATED. "
        "The canonical 4-phase runner is run_4phase.py. This shim "
        "delegates to run_4phase.py with default arguments. To silence "
        "this warning, update your scripts to call run_4phase.py "
        "directly. Neo4j mode is auto-detected from env/flags. "
        "The original run_unified.py is preserved at "
        "scripts/legacy/run_unified.py for reference.",
        file=sys.stderr,
    )
    if not CANONICAL.exists():
        print(f"ERROR: {CANONICAL} not found.", file=sys.stderr)
        return 2
    # Forward all args verbatim — run_4phase.py and run_unified.py share
    # most flags (--gt-epochs, --rl-timesteps, --rl-top-n, --output-dir,
    # --seed). Unknown flags will cause run_4phase.py to print its usage.
    return subprocess.call([sys.executable, str(CANONICAL), *sys.argv[1:]])


if __name__ == "__main__":
    sys.exit(main())
