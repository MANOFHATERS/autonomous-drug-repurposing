"""rl.cli — Command-Line Interface (P4-008 modular wrapper).

P4-008 ROOT FIX: modular wrapper around the CLI entry point (main)
and the argument parser (_build_arg_parser). See rl/env.py for the
full P4-008 rationale.

P4-021 v142 FORENSIC ROOT FIX (deprecation of pure re-export shims):
This module is a PURE RE-EXPORT SHIM (no actual logic). NEW CODE
should import directly from rl.rl_drug_ranker. The shim remains
functional for backward compat with existing callers. Set the env
var RL_WARN_ON_SHIM_IMPORT=1 to surface a DeprecationWarning on import
(for migration tracking). This shim will be REMOVED in v5.0.

Callers can now import (DEPRECATED — use rl.rl_drug_ranker directly):
    from rl.cli import main, _build_arg_parser

Or run directly:
    python -m rl.cli --timesteps 50000 --tenant rare_partner
"""
from __future__ import annotations

import os as _os
import warnings as _warnings

# P4-021 v142: emit DeprecationWarning ONLY when RL_WARN_ON_SHIM_IMPORT=1.
if _os.environ.get("RL_WARN_ON_SHIM_IMPORT", "0") == "1":
    _warnings.warn(
        "rl.cli is a PURE RE-EXPORT SHIM (P4-021 v142). Import from "
        "rl.rl_drug_ranker directly instead. This shim will be REMOVED in v5.0.",
        DeprecationWarning,
        stacklevel=2,
    )

from .rl_drug_ranker import main, _build_arg_parser

__all__ = ["main", "_build_arg_parser"]


if __name__ == "__main__":
    import sys
    sys.exit(main())
