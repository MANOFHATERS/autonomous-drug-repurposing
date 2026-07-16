"""
shared.contracts.feature_names — canonical RL feature column schema.

ISSUE ADDRESSED:
    #344 — graph_transformer/gt_rl_bridge.py must produce 15 CSV columns
           that the RL env (rl/rl_drug_ranker.py) expects. The bridge
           previously produced 12 columns, missing 4 disease-context
           and timestamp features. This module is the SINGLE source of
           truth for the column names — both the bridge (producer) and
           the RL env (consumer) import from here.

CANONICAL 17-COLUMN SCHEMA (audit requires 15+; we ship 17):

    Per-pair identity:
        DRUG_COL                  drug name (string)
        DISEASE_COL               disease name (string)

    GT model output:
        GNN_SCORE_COL             raw sigmoid probability [0,1]
        GNN_SCORE_CALIBRATED_COL  temperature-scaled probability (Guo 2017)
        GNN_SCORE_TIMESTAMP_COL   ISO 8601 UTC timestamp of GT prediction
        CONFIDENCE_COL            1 - 2*entropy(gnn_score), clipped to [0,1]

    Drug-level features (same value for all disease pairs of a drug):
        SAFETY_SCORE_COL          adverse event profile
        PATENT_SCORE_COL          FDA Orange Book patent status
        ADME_SCORE_COL            RDKit Lipinski compliance
        EFFICACY_SCORE_COL        target diversity (NOT confounded with gnn)

    Disease-level features (same value for all drug pairs of a disease):
        DISEASE_PAIR_COUNT_COL    # of (drug, this_disease) pairs in input
        DISEASE_AVG_GNN_COL       mean gnn_score across pairs for this disease
        DISEASE_AVG_SAFETY_COL    mean safety_score across pairs for this disease

    Per-pair supplementary:
        MARKET_SCORE_COL          market opportunity
        PATHWAY_SCORE_COL         multi-hop pathway connectivity
        RARE_DISEASE_FLAG_COL     1.0 if rare disease, else 0.0
        UNMET_NEED_SCORE_COL      prevalence + treatment-count derived

The bridge writes these in the order defined by RL_FEATURE_COLUMNS. The RL
env reads them by NAME (not position), so column order is stable but not
semantically critical. However, the audit's column-count check requires
len(CSV header) >= 15.

IMPORT RULE:
    from shared.contracts.feature_names import (
        DRUG_COL, DISEASE_COL, GNN_SCORE_COL, GNN_SCORE_CALIBRATED_COL,
        GNN_SCORE_TIMESTAMP_COL, CONFIDENCE_COL, SAFETY_SCORE_COL,
        PATENT_SCORE_COL, ADME_SCORE_COL, EFFICACY_SCORE_COL,
        DISEASE_PAIR_COUNT_COL, DISEASE_AVG_GNN_COL, DISEASE_AVG_SAFETY_COL,
        MARKET_SCORE_COL, PATHWAY_SCORE_COL, RARE_DISEASE_FLAG_COL,
        UNMET_NEED_SCORE_COL, RL_FEATURE_COLUMNS,
    )
"""
from __future__ import annotations

from typing import Final, List

# ---------------------------------------------------------------------------
# Per-pair identity (issue #344 — required for CSV join key)
# ---------------------------------------------------------------------------
DRUG_COL: Final[str] = "drug"
DISEASE_COL: Final[str] = "disease"

# ---------------------------------------------------------------------------
# GT model output (issue #346 — gnn_score_calibrated for Guo 2017 temp scaling)
# ---------------------------------------------------------------------------
GNN_SCORE_COL: Final[str] = "gnn_score"
GNN_SCORE_CALIBRATED_COL: Final[str] = "gnn_score_calibrated"
GNN_SCORE_TIMESTAMP_COL: Final[str] = "gnn_score_timestamp"
CONFIDENCE_COL: Final[str] = "confidence"

# ---------------------------------------------------------------------------
# Drug-level features (issue #347 — patent/adme from FDA Orange Book + RDKit)
# ---------------------------------------------------------------------------
SAFETY_SCORE_COL: Final[str] = "safety_score"
PATENT_SCORE_COL: Final[str] = "patent_score"
ADME_SCORE_COL: Final[str] = "adme_score"
# Issue #345 — efficacy_score MUST be drug-level (target diversity), NOT
# confounded with gnn_score or pathway_score.
EFFICACY_SCORE_COL: Final[str] = "efficacy_score"

# ---------------------------------------------------------------------------
# Disease-level context features (issue #344 — 3 missing columns)
# ---------------------------------------------------------------------------
DISEASE_PAIR_COUNT_COL: Final[str] = "disease_pair_count"
DISEASE_AVG_GNN_COL: Final[str] = "disease_avg_gnn"
DISEASE_AVG_SAFETY_COL: Final[str] = "disease_avg_safety"

# ---------------------------------------------------------------------------
# Per-pair supplementary features
# ---------------------------------------------------------------------------
MARKET_SCORE_COL: Final[str] = "market_score"
PATHWAY_SCORE_COL: Final[str] = "pathway_score"
RARE_DISEASE_FLAG_COL: Final[str] = "rare_disease_flag"
UNMET_NEED_SCORE_COL: Final[str] = "unmet_need_score"


# ---------------------------------------------------------------------------
# CANONICAL COLUMN ORDER (the order the bridge writes to CSV)
# ---------------------------------------------------------------------------
# This is the AUTHORITATIVE list. The bridge writes these columns in this
# exact order. The RL env reads by name (DictReader), so order does not
# affect correctness, but a stable order makes CSV diffs readable and
# lets the audit's column-count check pass deterministically.
#
# Count: 17 columns. Audit requires >= 15. ✓
RL_FEATURE_COLUMNS: Final[List[str]] = [
    # Identity (2)
    DRUG_COL,
    DISEASE_COL,
    # GT output (4)
    GNN_SCORE_COL,
    GNN_SCORE_CALIBRATED_COL,
    CONFIDENCE_COL,
    GNN_SCORE_TIMESTAMP_COL,
    # Drug-level (4)
    SAFETY_SCORE_COL,
    MARKET_SCORE_COL,
    PATHWAY_SCORE_COL,
    PATENT_SCORE_COL,
    RARE_DISEASE_FLAG_COL,
    UNMET_NEED_SCORE_COL,
    EFFICACY_SCORE_COL,
    ADME_SCORE_COL,
    # Disease-level context (3) — issue #344
    DISEASE_PAIR_COUNT_COL,
    DISEASE_AVG_GNN_COL,
    DISEASE_AVG_SAFETY_COL,
]

# Sanity check at import time (cheap, catches schema drift early).
assert len(RL_FEATURE_COLUMNS) == 17, (
    f"RL_FEATURE_COLUMNS must have exactly 17 columns, got "
    f"{len(RL_FEATURE_COLUMNS)}. Update shared/contracts/feature_names.py."
)
assert len(set(RL_FEATURE_COLUMNS)) == 17, (
    "RL_FEATURE_COLUMNS has duplicates. Update shared/contracts/feature_names.py."
)

# Subset that the RL env's reward function actually weights (issue #345
# — efficacy_score is in the CSV for transparency but is NOT in the
# reward function to avoid confounding with gnn_score).
REWARD_FEATURE_COLS: Final[List[str]] = [
    GNN_SCORE_COL,
    SAFETY_SCORE_COL,
    MARKET_SCORE_COL,
    PATHWAY_SCORE_COL,
    PATENT_SCORE_COL,
    ADME_SCORE_COL,
    UNMET_NEED_SCORE_COL,
    RARE_DISEASE_FLAG_COL,
]

# Disease-context columns the env re-derives via groupby (issue #344).
DISEASE_CONTEXT_COLS: Final[List[str]] = [
    DISEASE_PAIR_COUNT_COL,
    DISEASE_AVG_GNN_COL,
    DISEASE_AVG_SAFETY_COL,
]


__all__ = [
    # Identity
    "DRUG_COL",
    "DISEASE_COL",
    # GT output
    "GNN_SCORE_COL",
    "GNN_SCORE_CALIBRATED_COL",
    "GNN_SCORE_TIMESTAMP_COL",
    "CONFIDENCE_COL",
    # Drug-level
    "SAFETY_SCORE_COL",
    "PATENT_SCORE_COL",
    "ADME_SCORE_COL",
    "EFFICACY_SCORE_COL",
    # Disease-level
    "DISEASE_PAIR_COUNT_COL",
    "DISEASE_AVG_GNN_COL",
    "DISEASE_AVG_SAFETY_COL",
    # Supplementary
    "MARKET_SCORE_COL",
    "PATHWAY_SCORE_COL",
    "RARE_DISEASE_FLAG_COL",
    "UNMET_NEED_SCORE_COL",
    # Schemas
    "RL_FEATURE_COLUMNS",
    "REWARD_FEATURE_COLS",
    "DISEASE_CONTEXT_COLS",
]
