"""rl.env — Drug Ranking RL Environment (P4-008/P4-021 modular wrapper).

P4-008: created thin re-export modules for structural separation.
P4-021 ROOT FIX (MEDIUM): ACKNOWLEDGEMENT — the wrappers are currently
RE-EXPORT SHIMS around the 9,142-line rl_drug_ranker.py monolith. ALL
real logic remains in rl_drug_ranker.py. A full extraction of
DrugRankingEnv (~980 lines, deep dependencies on column constants,
RankedCandidate, PipelineMetrics, RewardFunction) would require a
MAJOR REFACTOR with high breakage risk.

EXTRACTION PLAN (post-v105, when CI coverage is higher):
  1. Extract column constants to rl/constants.py (no dependencies)
  2. Extract RankedCandidate + PipelineMetrics to rl/types.py
  3. Extract RewardConfig to rl/reward.py (self-contained dataclass)
  4. Extract RewardFunction to rl/reward.py (~700 lines)
  5. Extract DrugRankingEnv to rl/env.py (~980 lines, the final piece)
  6. rl_drug_ranker.py becomes a backward-compat shim

Until then, these wrappers provide the IMPORT INTERFACE for callers.
The structural separation is REAL at the import level — the code
organization just hasn't caught up yet.
"""
from __future__ import annotations

# P4-021: re-export from the monolith (backward compat).
# DrugRankingEnv is ~980 lines in rl_drug_ranker.py starting at line 3839.
# It depends on: column constants, RankedCandidate, PipelineMetrics,
# RewardFunction, WITHDRAWN_DRUGS, INDICATION_WITHDRAWN_DRUGS, etc.
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
