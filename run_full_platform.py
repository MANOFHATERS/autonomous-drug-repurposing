#!/usr/bin/env python3
"""
DEPRECATED — ORCH-003 ROOT FIX.

This file is now a thin deprecation shim that delegates to ``run_4phase.py``.
The previous standalone implementation has been removed because it duplicated
``run_4phase.py`` with a DIFFERENT adapter path (``from_phase1_staged_data``
instead of the correct ``graph_data=``) and DIFFERENT default hyperparameters,
producing inconsistent results for the same input data.

ORCH-003 root cause:
  Three 4-phase runners existed (``run_4phase.py``, ``run_full_platform.py``,
  ``run_real_pipeline.py``) that all chain Phase 1→2→3→4 but used different
  adapter paths (P3-009) and different default hyperparameters. CI may have
  tested one runner while production used another, leading to
  "works in CI, breaks in prod" situations.

Fix:
  ``run_4phase.py`` is the single source of truth for the 4-phase chain.
  This file (``run_full_platform.py``) is kept ONLY for backward
  compatibility with the Makefile default ``make run`` target. It forwards
  all CLI args to ``run_4phase.py``'s ``main()``.

  ``run_real_pipeline.py`` is the same shim but with the production-scale
  hyperparameters (gt_epochs=500, rl_timesteps=50000) pre-set.

To migrate:
  - ``make run``                  → still works (calls this shim)
  - ``python run_full_platform.py``   → forwards to run_4phase.py
  - ``python run_4phase.py``      → the canonical command

Exit codes are passed through unchanged from ``run_4phase.py``.
"""
from __future__ import annotations

import sys
import warnings
from pathlib import Path

# Ensure project root is on sys.path so run_4phase can be imported.
_ROOT = str(Path(__file__).resolve().parent)
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

# Import the canonical runner. The shim adds NO logic of its own — it only
# forwards args so that ``make run`` and existing scripts continue to work.
from run_4phase import main as _run_4phase_main  # noqa: E402


def main() -> int:
    # Deprecation warning to stderr so it doesn't pollute JSON output.
    warnings.warn(
        "run_full_platform.py is DEPRECATED (ORCH-003). "
        "Use run_4phase.py directly — it is the canonical 4-phase runner. "
        "This shim forwards all CLI args unchanged.",
        DeprecationWarning,
        stacklevel=2,
    )
    print(
        "[ORCH-003] run_full_platform.py is a deprecation shim. "
        "Forwarding to run_4phase.py with the same CLI args.",
        file=sys.stderr,
    )
    return _run_4phase_main()


if __name__ == "__main__":
    raise SystemExit(main())
