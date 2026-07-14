"""rl.reward — Reward Function & Config (P4-008/P4-021 modular wrapper).

P4-021 ROOT FIX: this module is a RE-EXPORT SHIM. The real
RewardConfig class (~350 lines) and RewardFunction class (~700 lines)
are in rl_drug_ranker.py. A full extraction is planned — see
rl/env.py for the extraction plan.

RewardConfig is relatively self-contained (lines 1138-1483) and can
be extracted first. RewardFunction depends on RewardConfig, column
constants, and the withdrawn-drug sets — it should be extracted after
RewardConfig.

Callers can import:
    from rl.reward import RewardFunction, RewardConfig, compute_reward
    from rl.reward import load_reward_weights_for_tenant, apply_tenant_reward_weights
"""
from __future__ import annotations

# P4-021: re-export from the monolith (backward compat).
from .rl_drug_ranker import (
    RewardConfig,
    RewardFunction,
    compute_reward,
    # P4-005: per-tenant reward weights
    load_reward_weights_for_tenant,
    save_reward_weights_for_tenant,
    apply_tenant_reward_weights,
    DEFAULT_REWARD_WEIGHTS_DIR,
    # Feature column list (used by reward function)
    FEATURE_COLS,
    REQUIRED_COLUMNS,
    # Constants used by the reward function
    WITHDRAWN_DRUGS,
    INDICATION_WITHDRAWN_DRUGS,
    CONTROLLED_SUBSTANCES,
    DEFAULT_PROPRIETARY_PREFIXES,
)

__all__ = [
    "RewardConfig",
    "RewardFunction",
    "compute_reward",
    "load_reward_weights_for_tenant",
    "save_reward_weights_for_tenant",
    "apply_tenant_reward_weights",
    "DEFAULT_REWARD_WEIGHTS_DIR",
    "FEATURE_COLS",
    "REQUIRED_COLUMNS",
    "WITHDRAWN_DRUGS",
    "INDICATION_WITHDRAWN_DRUGS",
    "CONTROLLED_SUBSTANCES",
    "DEFAULT_PROPRIETARY_PREFIXES",
]
