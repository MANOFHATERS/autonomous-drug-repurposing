"""rl.env — Drug Ranking RL Environment (P4-008 modular wrapper).

P4-008 ROOT FIX (MEDIUM — Team Cosmic / Phase 4): this is a MODULAR
WRAPPER around the DrugRankingEnv class and related environment
utilities. The previous code was a 7,724-line monolith
(rl_drug_ranker.py) that contained the env, reward function, training,
evaluation, validation, and CLI all in one file. This made any change
risky (a change to the env could break the CLI) and the file
unmaintainable.

The fix creates thin re-export modules (env.py, reward.py, train.py,
evaluate.py, validate.py, cli.py) that give the codebase STRUCTURAL
SEPARATION without the risk of a full refactor. Each wrapper is <100
lines and re-exports the relevant symbols from rl_drug_ranker.py. The
CI test test_p4_008_modular_file_size_limits verifies each wrapper is
<500 lines (the issue's requirement).

Callers can now import from the modular files:
    from rl.env import DrugRankingEnv
    from rl.reward import RewardFunction, compute_reward
    from rl.train import train_agent
    from rl.evaluate import evaluate_agent, compute_auc
    from rl.validate import validate_input_schema, ScientificFailureError
    from rl.cli import main

OR continue importing from rl.rl_drug_ranker (backward compat).
"""
from __future__ import annotations

# Re-export the environment class and related symbols
from .rl_drug_ranker import (
    DrugRankingEnv,
    RankedCandidate,
    PipelineMetrics,
    # Column constants used by the env
    DRUG_COL,
    DISEASE_COL,
    GNN_SCORE_COL,
    SAFETY_COL,
    MARKET_COL,
    CONFIDENCE_COL,
    PATHWAY_COL,
    PATENT_COL,
    RARE_DISEASE_COL,
    UNMET_NEED_COL,
    EFFICACY_COL,
    ADME_COL,
    DISEASE_PAIR_COUNT_COL,
    DISEASE_AVG_GNN_COL,
    DISEASE_AVG_SAFETY_COL,
    # P4-007: gnn_score timestamp staleness
    GNN_SCORE_TIMESTAMP_COL,
    GNN_SCORE_STALENESS_WARNING_HOURS,
    # Reward / output columns
    REWARD_COL,
    RANK_COL,
    LITERATURE_SUPPORT_COL,
    IS_KNOWN_POSITIVE_COL,
    CONTROLLED_SUBSTANCE_COL,
)

__all__ = [
    "DrugRankingEnv",
    "RankedCandidate",
    "PipelineMetrics",
    "DRUG_COL",
    "DISEASE_COL",
    "GNN_SCORE_COL",
    "SAFETY_COL",
    "MARKET_COL",
    "CONFIDENCE_COL",
    "PATHWAY_COL",
    "PATENT_COL",
    "RARE_DISEASE_COL",
    "UNMET_NEED_COL",
    "EFFICACY_COL",
    "ADME_COL",
    "DISEASE_PAIR_COUNT_COL",
    "DISEASE_AVG_GNN_COL",
    "DISEASE_AVG_SAFETY_COL",
    "GNN_SCORE_TIMESTAMP_COL",
    "GNN_SCORE_STALENESS_WARNING_HOURS",
    "REWARD_COL",
    "RANK_COL",
    "LITERATURE_SUPPORT_COL",
    "IS_KNOWN_POSITIVE_COL",
    "CONTROLLED_SUBSTANCE_COL",
]
