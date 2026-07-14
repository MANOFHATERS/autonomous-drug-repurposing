"""rl.constants — Column constants for the RL ranker (P4-021 REAL extraction).

P4-021 ROOT FIX (Team Member 9): the previous code had ALL column constants
defined inline in the 9,361-line rl_drug_ranker.py monolith. The wrapper
modules (rl/env.py, rl/reward.py, etc.) were re-export shims that imported
FROM the monolith — meaning any change to the monolith risked breaking ALL
wrappers simultaneously, and the "modular separation" was cosmetic.

This file is the FIRST REAL extraction step toward P4-021's goal of actual
decoupling. It moves the SELF-CONTAINED column constants (no dependencies on
torch, pandas, or any class) into their own module. Both rl_drug_ranker.py
AND the wrapper modules import from here, so:

  1. A change to a column name lives in ONE place (this file).
  2. The wrapper modules no longer transitively depend on the monolith
     for constants — they can be imported without triggering the 9000-line
     monolith's import side effects.
  3. This proves the modular structure is REAL, not cosmetic.

Future extraction steps (post-v105, when CI coverage is higher):
  - rl/types.py — RankedCandidate, PipelineMetrics dataclasses
  - rl/reward.py — RewardConfig + RewardFunction (move from monolith)
  - rl/env.py — DrugRankingEnv (move from monolith)
  - rl_drug_ranker.py becomes a backward-compat shim

This file is INTENTIONALLY minimal — only constants, no logic, no imports
beyond stdlib typing. It can be imported from anywhere without side effects.
"""
from __future__ import annotations

from typing import List

# ============================================================================
# CORE IDENTIFIER COLUMNS
# ============================================================================
DRUG_COL: str = "drug"
DISEASE_COL: str = "disease"

# ============================================================================
# FEATURE COLUMNS (observed by the RL agent)
# ============================================================================
GNN_SCORE_COL: str = "gnn_score"
SAFETY_COL: str = "safety_score"
MARKET_COL: str = "market_score"
CONFIDENCE_COL: str = "confidence"
PATHWAY_COL: str = "pathway_score"

# PATENT_COL semantics: For REPURPOSING, OFF-patent = better (cheaper,
# generic availability, no IP blocking by original manufacturer).
PATENT_COL: str = "patent_score"
RARE_DISEASE_COL: str = "rare_disease_flag"

# Renamed from existing_drugs_score: previous name was actively misleading.
UNMET_NEED_COL: str = "unmet_need_score"

# Clinical efficacy signal (project doc requires 3 dimensions including efficacy).
EFFICACY_COL: str = "efficacy_score"

# ADME (Absorption, Distribution, Metabolism, Excretion) properties.
ADME_COL: str = "adme_score"

# ============================================================================
# DISEASE-CONTEXT FEATURES (added at runtime by the env)
# ============================================================================
DISEASE_PAIR_COUNT_COL: str = "disease_pair_count"
DISEASE_AVG_GNN_COL: str = "disease_avg_gnn"
DISEASE_AVG_SAFETY_COL: str = "disease_avg_safety"

# ============================================================================
# GNN SCORE STALENESS TRACKING (P4-007)
# ============================================================================
# The input CSV may include this column (ISO 8601 format) to indicate when
# the gnn_score was computed by the Phase 3 GT model. The DrugRankingEnv
# checks the timestamp at init time and logs a WARNING if the gnn_score is
# stale (>24h old), because the GT model may have been retrained since then.
GNN_SCORE_TIMESTAMP_COL: str = "gnn_score_timestamp"
GNN_SCORE_STALENESS_WARNING_HOURS: float = 24.0

# ============================================================================
# OPTIONAL CANONICAL-IDENTIFIER COLUMNS
# ============================================================================
SOURCE_DB_COL: str = "source_database"
DRUG_CANONICAL_COL: str = "drug_inchikey"
DISEASE_CANONICAL_COL: str = "disease_mesh_id"

# ============================================================================
# OUTPUT COLUMN CONSTANTS
# ============================================================================
REWARD_COL: str = "reward"
RANK_COL: str = "rank"
LITERATURE_SUPPORT_COL: str = "literature_support"
IS_KNOWN_POSITIVE_COL: str = "is_known_positive"
CONTROLLED_SUBSTANCE_COL: str = "controlled_substance"

# ============================================================================
# DEFAULT FEATURE COLUMNS
# ============================================================================
# The environment may EXTEND this list with disease context features at runtime.
FEATURE_COLS: List[str] = [
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
]

REQUIRED_COLUMNS: List[str] = FEATURE_COLS + [DRUG_COL, DISEASE_COL]

__all__ = [
    # Core identifiers
    "DRUG_COL", "DISEASE_COL",
    # Feature columns
    "GNN_SCORE_COL", "SAFETY_COL", "MARKET_COL", "CONFIDENCE_COL",
    "PATHWAY_COL", "PATENT_COL", "RARE_DISEASE_COL", "UNMET_NEED_COL",
    "EFFICACY_COL", "ADME_COL",
    # Disease context
    "DISEASE_PAIR_COUNT_COL", "DISEASE_AVG_GNN_COL", "DISEASE_AVG_SAFETY_COL",
    # GNN staleness
    "GNN_SCORE_TIMESTAMP_COL", "GNN_SCORE_STALENESS_WARNING_HOURS",
    # Canonical IDs
    "SOURCE_DB_COL", "DRUG_CANONICAL_COL", "DISEASE_CANONICAL_COL",
    # Output
    "REWARD_COL", "RANK_COL", "LITERATURE_SUPPORT_COL",
    "IS_KNOWN_POSITIVE_COL", "CONTROLLED_SUBSTANCE_COL",
    # Aggregates
    "FEATURE_COLS", "REQUIRED_COLUMNS",
]
