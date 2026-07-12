"""rl.evaluate — Evaluation & AUC (P4-008 modular wrapper).

P4-008 ROOT FIX: modular wrapper around the evaluation functions:
evaluate_agent, compute_auc, extract_policy_prob_high,
check_known_positive_recovery, literature_crosscheck, display_top_candidates,
split_data. See rl/env.py for the full P4-008 rationale.

Callers can now import:
    from rl.evaluate import evaluate_agent, compute_auc, split_data
"""
from __future__ import annotations

from .rl_drug_ranker import (
    evaluate_agent,
    compute_auc,
    extract_policy_prob_high,
    check_known_positive_recovery,
    literature_crosscheck,
    display_top_candidates,
    split_data,
)

__all__ = [
    "evaluate_agent",
    "compute_auc",
    "extract_policy_prob_high",
    "check_known_positive_recovery",
    "literature_crosscheck",
    "display_top_candidates",
    "split_data",
]
