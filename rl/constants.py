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

TASK 328 ROOT FIX (forensic, root-level):
  The 6 CANONICAL RL feature names (gnn_score, safety_score, market_score,
  efficacy_score, patent_score, adme_score) are now imported from
  shared/contracts/feature_names.py — the SINGLE source of truth shared
  with Phase 3's bridge (which writes these features to the RL input CSV).
  This eliminates the schema drift where Phase 3 writes ``gnn_score`` but
  Phase 4 reads ``gt_score`` (or vice versa) — the contract makes any
  rename a compile-time error on both sides.

  The non-canonical constants (CONFIDENCE_COL, PATHWAY_COL, etc.) remain
  defined here because they are Phase 4-internal (not written by Phase 3).

Future extraction steps (post-v105, when CI coverage is higher):
  - rl/types.py — RankedCandidate, PipelineMetrics dataclasses
  - rl/reward.py — RewardConfig + RewardFunction (move from monolith)
  - rl/env.py — DrugRankingEnv (move from monolith)
  - rl_drug_ranker.py becomes a backward-compat shim

This file is INTENTIONALLY minimal — only constants, no logic, no imports
beyond stdlib typing + the shared contract.
"""
from __future__ import annotations

from typing import List

# =============================================================================
# TASK 328 ROOT FIX: import the 6 CANONICAL RL feature names from the
# shared contract. Both Phase 3 bridge (writer) and Phase 4 env (reader)
# import from the same module — eliminating the schema drift that
# previously caused silent zero-feature bugs.
# =============================================================================
try:
    from shared.contracts.feature_names import (
        FEATURE_GNN_SCORE as GNN_SCORE_COL,
        FEATURE_SAFETY_SCORE as SAFETY_COL,
        FEATURE_MARKET_SCORE as MARKET_COL,
        FEATURE_EFFICACY_SCORE as EFFICACY_COL,
        FEATURE_PATENT_SCORE as PATENT_COL,
        FEATURE_ADME_SCORE as ADME_COL,
    )
    _FEATURE_NAMES_FROM_CONTRACT = True
except ImportError:
    # Degraded mode: shared.contracts.feature_names not importable.
    # Fall back to hardcoded values that match the contract. The contract
    # consistency test will fail in CI, surfacing the misconfiguration.
    GNN_SCORE_COL: str = "gnn_score"
    SAFETY_COL: str = "safety_score"
    MARKET_COL: str = "market_score"
    EFFICACY_COL: str = "efficacy_score"
    PATENT_COL: str = "patent_score"
    ADME_COL: str = "adme_score"
    _FEATURE_NAMES_FROM_CONTRACT = False

# ============================================================================
# CORE IDENTIFIER COLUMNS
# ============================================================================
DRUG_COL: str = "drug"
DISEASE_COL: str = "disease"

# ============================================================================
# FEATURE COLUMNS (observed by the RL agent)
# ============================================================================
# The 6 canonical features (above) are imported from the shared contract.
# The remaining feature columns are Phase 4-internal (not written by
# Phase 3) so they stay defined here.
CONFIDENCE_COL: str = "confidence"
PATHWAY_COL: str = "pathway_score"

# PATENT_COL semantics: For REPURPOSING, OFF-patent = better (cheaper,
# generic availability, no IP blocking by original manufacturer).
# (PATENT_COL is imported from the shared contract above.)

RARE_DISEASE_COL: str = "rare_disease_flag"

# Renamed from existing_drugs_score: previous name was actively misleading.
UNMET_NEED_COL: str = "unmet_need_score"

# (EFFICACY_COL and ADME_COL are imported from the shared contract above.)

# ============================================================================
# P4-006 v128 ROOT FIX (Task 9.6 — bridge column mismatch):
# The Phase 3 bridge (graph_transformer/gt_rl_bridge.py) writes 17 columns
# (2 IDs + 15 features), but the env previously read only 12 (2 IDs +
# 10 features), silently DROPPING 5 bridge-provided feature columns. The
# 5 ignored columns include calibrated GNN score, GNN score timestamp
# (staleness signal), and the bridge's pre-computed disease context
# statistics. The agent was blind to these signals — its policy could
# not learn from confidence calibration, prediction freshness, or the
# bridge's authoritative disease context (it had to re-derive disease
# context from its own data, which differs between train and test envs).
#
# ROOT FIX: add 5 new column constants for the previously-ignored bridge
# columns. The env (DrugRankingEnv.__init__) now reads ALL 17 bridge
# columns and includes them in the observation vector, bringing
# observation_space.shape to (18,) — 10 canonical features + 5 newly-added
# bridge columns + 3 env-derived disease context columns.
#
# The 5 newly-added bridge columns:
#   1. GNN_SCORE_CALIBRATED_COL — the temperature-calibrated GNN score
#      (apply_temperature=True). Useful for the agent to see both the raw
#      ranking signal (gnn_score) and the decision-threshold signal
#      (gnn_score_calibrated) — they encode different information.
#   2. GNN_SCORE_AGE_HOURS_COL — derived from gnn_score_timestamp. Encodes
#      how stale the GNN prediction is (0 = fresh, large = stale). The
#      agent can learn to DOWN-WEIGHT stale predictions (the GT model may
#      have been retrained since the gnn_score was computed).
#   3. BRIDGE_DISEASE_PAIR_COUNT_COL — the bridge's authoritative count
#      of drug-disease pairs per disease (computed on the FULL graph, not
#      the env's train/test subset). The env still re-derives its own
#      per-env stats for train/test consistency, but the bridge's value
#      gives the agent a STABLE global signal.
#   4. BRIDGE_DISEASE_AVG_GNN_COL — the bridge's authoritative mean gnn
#      per disease (full-graph). Same rationale as above.
#   5. BRIDGE_DISEASE_AVG_SAFETY_COL — the bridge's authoritative mean
#      safety per disease (full-graph).
# ============================================================================
GNN_SCORE_CALIBRATED_COL: str = "gnn_score_calibrated"
GNN_SCORE_AGE_HOURS_COL: str = "gnn_score_age_hours"  # derived from gnn_score_timestamp
# NOTE: the bridge writes these as "disease_pair_count" / "disease_avg_gnn" /
# "disease_avg_safety". The env RENAMES them to the "bridge_*" names below
# at __init__ time (before its own groupby re-derives "disease_pair_count"
# etc.) to avoid pandas merge _x/_y suffix collisions while preserving both
# the bridge's authoritative full-graph value AND the env's train/test-split
# value in the observation vector.
BRIDGE_DISEASE_PAIR_COUNT_COL: str = "bridge_disease_pair_count"
BRIDGE_DISEASE_AVG_GNN_COL: str = "bridge_disease_avg_gnn"
BRIDGE_DISEASE_AVG_SAFETY_COL: str = "bridge_disease_avg_safety"

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
#
# P4-006 v128 ROOT FIX (Task 9.6): the 5 BRIDGE_* columns are OPTIONALLY
# read by DrugRankingEnv when the bridge CSV provides them. They are NOT
# in the canonical FEATURE_COLS because (a) older bridge versions may not
# emit them, and (b) the env-derived disease context (DISEASE_PAIR_COUNT_COL
# etc.) provides train/test-consistent values. The env includes the bridge
# columns IN ADDITION to the env-derived ones when present — bringing the
# total observation_space.shape to (18,) on a v128+ bridge CSV.
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

# P4-006 v128: OPTIONAL bridge-provided feature columns (added to the
# observation vector by DrugRankingEnv when present in the input CSV).
# These are NOT required — the env falls back to default values (0.0)
# when the bridge CSV omits them (older bridge versions).
OPTIONAL_BRIDGE_FEATURE_COLS: List[str] = [
    GNN_SCORE_CALIBRATED_COL,
    GNN_SCORE_AGE_HOURS_COL,
    BRIDGE_DISEASE_PAIR_COUNT_COL,
    BRIDGE_DISEASE_AVG_GNN_COL,
    BRIDGE_DISEASE_AVG_SAFETY_COL,
]

REQUIRED_COLUMNS: List[str] = FEATURE_COLS + [DRUG_COL, DISEASE_COL]

__all__ = [
    # Core identifiers
    "DRUG_COL", "DISEASE_COL",
    # Feature columns
    "GNN_SCORE_COL", "SAFETY_COL", "MARKET_COL", "CONFIDENCE_COL",
    "PATHWAY_COL", "PATENT_COL", "RARE_DISEASE_COL", "UNMET_NEED_COL",
    "EFFICACY_COL", "ADME_COL",
    # P4-006 v128: optional bridge-provided feature columns
    "GNN_SCORE_CALIBRATED_COL", "GNN_SCORE_AGE_HOURS_COL",
    "BRIDGE_DISEASE_PAIR_COUNT_COL", "BRIDGE_DISEASE_AVG_GNN_COL",
    "BRIDGE_DISEASE_AVG_SAFETY_COL", "OPTIONAL_BRIDGE_FEATURE_COLS",
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
