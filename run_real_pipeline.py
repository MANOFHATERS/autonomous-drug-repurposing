#!/usr/bin/env python3
"""
DEPRECATED — ORCH-003 ROOT FIX.

This file is now a thin deprecation shim that delegates to ``run_4phase.py``
with the production-scale hyperparameters (gt_epochs=500, rl_timesteps=50000)
pre-set on the command line. The previous standalone implementation has been
removed because it duplicated ``run_4phase.py`` with a DIFFERENT adapter path
(``from_phase1_staged_data`` instead of the correct ``graph_data=``).

ORCH-003 root cause:
  Three 4-phase runners existed (``run_4phase.py``, ``run_full_platform.py``,
  ``run_real_pipeline.py``) that all chain Phase 1→2→3→4 but used different
  adapter paths (P3-009) and different default hyperparameters. ``run_real_pipeline.py``
  used gt_epochs=500, rl_timesteps=50000 vs run_4phase.py's gt_epochs=80,
  rl_timesteps=5000 — silently producing different model quality for the
  same input data.

Fix:
  ``run_4phase.py`` is the single source of truth. This shim preserves the
  production-scale defaults by injecting ``--gt-epochs 500 --rl-timesteps 50000``
  into ``sys.argv`` BEFORE calling ``run_4phase.py``'s ``main()``, so an
  operator who runs ``python run_real_pipeline.py`` gets the SAME behavior
  as before (production-scale hyperparameters) but on the CORRECT adapter
  path.

  If the user explicitly passes --gt-epochs or --rl-timesteps, the explicit
  value wins (we don't double-inject).

Exit codes are passed through unchanged from ``run_4phase.py``.
"""
from __future__ import annotations

import sys
import warnings
from pathlib import Path

_ROOT = str(Path(__file__).resolve().parent)
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

from run_4phase import main as _run_4phase_main  # noqa: E402


def main() -> int:
    warnings.warn(
        "run_real_pipeline.py is DEPRECATED (ORCH-003). "
        "Use run_4phase.py with --gt-epochs 500 --rl-timesteps 50000 for "
        "production-scale training. This shim injects those defaults.",
        DeprecationWarning,
        stacklevel=2,
    )
    print(
        "[ORCH-003] run_real_pipeline.py is a deprecation shim. "
        "Forwarding to run_4phase.py with --gt-epochs 500 --rl-timesteps 50000.",
        file=sys.stderr,
    )

    # Inject production-scale defaults UNLESS the user already passed them.
    # We mutate sys.argv so run_4phase.main()'s argparse sees them.
    if "--gt-epochs" not in sys.argv:
        sys.argv.extend(["--gt-epochs", "500"])
    if "--rl-timesteps" not in sys.argv:
        sys.argv.extend(["--rl-timesteps", "50000"])

    return _run_4phase_main()


if __name__ == "__main__":
    raise SystemExit(main())
