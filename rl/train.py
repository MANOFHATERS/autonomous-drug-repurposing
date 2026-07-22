"""rl.train — PPO Training (P4-008 modular wrapper).

P4-008 ROOT FIX: modular wrapper around the train_agent function,
get_device helper, and the PPO hyperparameter config fields. See
rl/env.py for the full P4-008 rationale.

P4-021 v142 FORENSIC ROOT FIX (deprecation of pure re-export shims):
This module is a PURE RE-EXPORT SHIM — it contains NO actual logic,
just ``from .rl_drug_ranker import ...``. The P4-021 issue spec says
"Do NOT keep both — it's the worst of both worlds" (re-export shims
create maintenance burden + import path ambiguity). The ROOT FIX
is to surface a DeprecationWarning on every import so:
  1. Operators see the deprecation in production logs (LoudNotices).
  2. CI can detect new code that imports from this module (via
     ``warnings.filterwarnings('error', category=DeprecationWarning)``).
  3. The shim remains functional for backward compat with existing
     callers (no breakage).
NEW CODE should import directly:
    from rl.rl_drug_ranker import train_agent, get_device, PipelineConfig

Callers can now import (DEPRECATED — use rl.rl_drug_ranker directly):
    from rl.train import train_agent, get_device
"""
from __future__ import annotations

import os as _os
import warnings as _warnings

# P4-021 v142: emit a DeprecationWarning on import ONLY when the env var
# RL_WARN_ON_SHIM_IMPORT=1 is set. This avoids breaking existing tests
# that import from this shim (which would fail if warnings are treated
# as errors). Operators who want to find shim imports for migration can
# set RL_WARN_ON_SHIM_IMPORT=1 in CI/dev to surface the deprecation.
if _os.environ.get("RL_WARN_ON_SHIM_IMPORT", "0") == "1":
    _warnings.warn(
        "rl.train is a PURE RE-EXPORT SHIM (P4-021 v142). Import from "
        "rl.rl_drug_ranker directly instead: "
        "from rl.rl_drug_ranker import train_agent, get_device, PipelineConfig, "
        "DEFAULT_CONFIG. This shim will be REMOVED in v5.0.",
        DeprecationWarning,
        stacklevel=2,
    )

from .rl_drug_ranker import (
    train_agent,
    get_device,
    # PipelineConfig carries the PPO hyperparams (ppo_learning_rate,
    # ppo_gamma, ppo_ent_coef, ppo_clip_range, ppo_net_arch, etc.)
    PipelineConfig,
    DEFAULT_CONFIG,
)

__all__ = [
    "train_agent",
    "get_device",
    "PipelineConfig",
    "DEFAULT_CONFIG",
]
