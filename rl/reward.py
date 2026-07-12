"""rl.reward — Reward Function & Config (P4-008 modular wrapper).

P4-008 ROOT FIX: modular wrapper around the RewardFunction class,
RewardConfig dataclass, compute_reward backward-compat wrapper, and
the per-tenant reward-weights system (P4-005). See rl/env.py for the
full P4-008 rationale.

Callers can now import:
    from rl.reward import RewardFunction, RewardConfig, compute_reward
    from rl.reward import load_reward_weights_for_tenant, apply_tenant_reward_weights
"""
from __future__ import annotations

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
