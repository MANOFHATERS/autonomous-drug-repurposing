"""shared.contracts.feature_names — canonical RL feature names.

TASK 328 ROOT FIX (forensic, root-level):
  Previously, the RL feature column names were defined INLINE in
  ``rl/constants.py`` (``GNN_SCORE_COL = "gnn_score"``, etc.) AND
  separately in ``graph_transformer/gt_rl_bridge.py`` (which writes the
  features to the RL input CSV). The two sides had to manually stay in
  sync — when Phase 3's bridge renamed ``gnn_score`` to ``gt_score``,
  Phase 4's env silently read all zeros for that column (because
  ``gnn_score`` was now missing from the CSV).

  This module extracts the 6 CANONICAL RL feature names into a CONTRACT
  that both sides import. The 6 features are the ones the project docx
  (§4, §6) specifies as the RL agent's observation dimensions:
    - gnn_score       (Phase 3 Graph Transformer output)
    - safety_score    (withdrawn / black-box / adverse event history)
    - market_score    (patent status, market size, competition)
    - efficacy_score  (known efficacy from clinical trials)
    - patent_score    (on-patent vs off-patent)
    - adme_score      (Absorption/Distribution/Metabolism/Excretion)

  Any change to these names is now a compile-time error on both sides —
  the contract consistency test (Task 330) verifies the bridge writes
  these exact names and the env reads these exact names.

Why this is shared (not just in rl/)
------------------------------------
The Phase 3 bridge (``graph_transformer/gt_rl_bridge.py``) WRITES these
features to the RL input CSV. The Phase 4 env (``rl/env.py``,
``rl/rl_drug_ranker.py``) READS them. If the names live in
``rl/constants.py``, the Phase 3 bridge has to import from ``rl/`` —
creating a circular dependency (rl/ imports graph_transformer/ for
checkpoint loading, graph_transformer/ imports rl/ for feature names).
Putting the names in ``shared/contracts/`` breaks the cycle.
"""
from __future__ import annotations

from typing import Dict, Tuple


# =============================================================================
# Canonical RL feature names — the 6 features per the project docx
# =============================================================================
# These are the EXACT column names that MUST appear in the RL input CSV
# (written by Phase 3 bridge, read by Phase 4 env). Renaming any of these
# silently breaks the RL agent's observation space.

FEATURE_GNN_SCORE: str = "gnn_score"
FEATURE_SAFETY_SCORE: str = "safety_score"
FEATURE_MARKET_SCORE: str = "market_score"
FEATURE_EFFICACY_SCORE: str = "efficacy_score"
FEATURE_PATENT_SCORE: str = "patent_score"
FEATURE_ADME_SCORE: str = "adme_score"


# =============================================================================
# Canonical feature order (for tensor construction)
# =============================================================================
# The RL agent's observation vector is built by concatenating these
# features in this exact order. Changing the order silently permutes
# the observation space — the agent sees a different vector than it
# was trained on.
CANONICAL_RL_FEATURE_ORDER: Tuple[str, ...] = (
    FEATURE_GNN_SCORE,
    FEATURE_SAFETY_SCORE,
    FEATURE_MARKET_SCORE,
    FEATURE_EFFICACY_SCORE,
    FEATURE_PATENT_SCORE,
    FEATURE_ADME_SCORE,
)

# Set for O(1) membership tests.
CANONICAL_RL_FEATURE_NAMES: Tuple[str, ...] = CANONICAL_RL_FEATURE_ORDER
CANONICAL_RL_FEATURE_SET: frozenset = frozenset(CANONICAL_RL_FEATURE_NAMES)


# =============================================================================
# Feature descriptions (for documentation and error messages)
# =============================================================================
FEATURE_DESCRIPTIONS: Dict[str, str] = {
    FEATURE_GNN_SCORE:
        "Phase 3 Graph Transformer link-prediction score [0, 1]. "
        "Higher = stronger predicted drug-disease therapeutic relationship.",
    FEATURE_SAFETY_SCORE:
        "Drug safety score [0, 1]. 1.0 = perfectly safe (no withdrawals, "
        "no black-box warnings, no severe adverse events). 0.0 = withdrawn "
        "from market. Phase 4 uses this to filter patient-harm candidates.",
    FEATURE_MARKET_SCORE:
        "Commercial opportunity score [0, 1]. Combines disease prevalence, "
        "existing-treatment competition, and reimbursement potential.",
    FEATURE_EFFICACY_SCORE:
        "Known clinical efficacy score [0, 1]. Derived from DrugBank "
        "indications + clinical trial outcomes (higher phase = higher score).",
    FEATURE_PATENT_SCORE:
        "Patent status score [0, 1]. For REPURPOSING: 1.0 = off-patent "
        "(generic, cheaper, no IP blocking). 0.0 = on-patent (limited "
        "licensing freedom).",
    FEATURE_ADME_SCORE:
        "ADME (Absorption, Distribution, Metabolism, Excretion) score [0, 1]. "
        "Higher = better pharmacokinetic profile (oral bioavailability, "
        "half-life, etc.).",
}


# =============================================================================
# Feature value ranges (for runtime validation)
# =============================================================================
FEATURE_RANGES: Dict[str, Tuple[float, float]] = {
    feature: (0.0, 1.0) for feature in CANONICAL_RL_FEATURE_NAMES
}


def is_canonical_feature(name: str) -> bool:
    """Return True if ``name`` is one of the 6 canonical RL features."""
    return name in CANONICAL_RL_FEATURE_SET


def validate_feature_vector(features: Dict[str, float]) -> "list[str]":
    """Validate a feature vector dict against the contract.

    Returns a list of error messages. Empty list = valid.
    """
    errors = []
    for name in CANONICAL_RL_FEATURE_NAMES:
        if name not in features:
            errors.append(f"Missing canonical feature {name!r}.")
            continue
        value = features[name]
        if value is None:
            errors.append(f"Feature {name!r} is null.")
            continue
        try:
            v = float(value)
            lo, hi = FEATURE_RANGES[name]
            if v < lo or v > hi:
                errors.append(
                    f"Feature {name!r} value {v} is out of range [{lo}, {hi}]."
                )
        except (TypeError, ValueError):
            errors.append(f"Feature {name!r} value {value!r} is not a float.")
    return errors
