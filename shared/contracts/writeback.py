"""
shared.contracts.writeback — canonical validated_hypotheses schema.

ISSUES ADDRESSED:
    #336 — Phase 4 writeback and Phase 3 trainer must read/write the SAME
           path. Both use CANONICAL_VALIDATED_CSV (phase1/processed_data/
           validated_hypotheses.csv). The legacy rl/validated_hypotheses.csv
           default is REMOVED.
    #337 — Both writeback and trainer use the SAME column name `outcome`
           (not `validated`). Both use the SAME outcome enum values
           (validated_positive / validated_toxic / validated_negative /
           invalidated), NOT "true"/"false" strings.
    #341 — Neo4j MERGE label and identifier. The TM 17 contract specifies
           :Drug with canonical drug_id. The Phase 2 kg_builder currently
           uses :Compound with name. This contract defines BOTH labels
           so the writeback can defensively MERGE against either schema
           (current KG and future TM 17 state) without fragmenting nodes.
    #342 — Edge label per outcome. validated_positive → VALIDATED_TREATS,
           validated_toxic → VALIDATED_TOXIC_FOR (NOT VALIDATED_TOXIC —
           the FOR suffix makes the semantics explicit: the drug is toxic
           FOR this disease, not just toxic in general).

This module is the AUTHORITATIVE source. The legacy
common.validated_hypotheses_schema module re-exports from here for backward
compatibility — do not duplicate constants.

IMPORT RULE:
    from shared.contracts.writeback import (
        CANONICAL_VALIDATED_CSV,
        OUTCOME_COL,
        OUTCOME_VALIDATED_POSITIVE,
        OUTCOME_VALIDATED_TOXIC,
        EDGE_LABEL_FOR_OUTCOME,
        NEO4J_DRUG_LABELS,
        NEO4J_DISEASE_LABEL,
    )
"""
from __future__ import annotations

import os
from pathlib import Path
from typing import Final, List, Tuple

# ---------------------------------------------------------------------------
# CANONICAL PATH (issue #336)
# ---------------------------------------------------------------------------
# The single write/read location for validated_hypotheses.csv. Both
# phase4/writeback.py AND rl/rl_drug_ranker.py AND graph_transformer/
# training/trainer.py MUST use this path. No component may hardcode its
# own path.

_REPO_ROOT: Final[str] = str(Path(__file__).resolve().parents[2])

CANONICAL_VALIDATED_CSV: Final[str] = os.path.join(
    _REPO_ROOT, "phase1", "processed_data", "validated_hypotheses.csv"
)

# Legacy fallback path (still searched if canonical missing — for old
# deployments that have not migrated). DO NOT write to this path; only
# read as a last resort.
LEGACY_RL_VALIDATED_CSV: Final[str] = os.path.join(
    _REPO_ROOT, "rl", "validated_hypotheses.csv"
)


def get_validated_csv_path() -> str:
    """Return the canonical path, respecting the env-var override.

    The env var ``VALIDATED_HYPOTHESES_CSV`` can override the canonical
    path for deployments that need a custom location. Read LAZILY at
    call time (not import time) so tests/runtime config can override
    without reloading the module.
    """
    return os.environ.get("VALIDATED_HYPOTHESES_CSV", CANONICAL_VALIDATED_CSV)


def ensure_csv_dir() -> None:
    """Ensure the directory for the canonical CSV exists."""
    os.makedirs(os.path.dirname(CANONICAL_VALIDATED_CSV), exist_ok=True)


# ---------------------------------------------------------------------------
# CANONICAL COLUMN NAMES (issue #337)
# ---------------------------------------------------------------------------
# Writeback writes the `outcome` column. The trainer reads the `outcome`
# column. The RL ranker reads the `outcome` column. NOBODY reads a
# `validated` column with "true"/"false" values anymore — that was the
# bug that broke the flywheel.

OUTCOME_COL: Final[str] = "outcome"
DRUG_COL: Final[str] = "drug"
DISEASE_COL: Final[str] = "disease"
TIMESTAMP_COL: Final[str] = "validated_at"
VALIDATED_BY_COL: Final[str] = "validated_by"
VALIDATION_STUDY_ID_COL: Final[str] = "validation_study_id"
NOTES_COL: Final[str] = "notes"
ORIGINAL_GT_SCORE_COL: Final[str] = "original_gt_score"
ORIGINAL_RL_RANK_COL: Final[str] = "original_rl_rank"
WRITEBACK_VERSION_COL: Final[str] = "writeback_version"

# Full ordered list of columns written by writeback_to_phase1.
WRITEBACK_CSV_COLUMNS: Final[List[str]] = [
    DRUG_COL,
    DISEASE_COL,
    OUTCOME_COL,
    VALIDATED_BY_COL,
    VALIDATION_STUDY_ID_COL,
    TIMESTAMP_COL,
    NOTES_COL,
    ORIGINAL_GT_SCORE_COL,
    ORIGINAL_RL_RANK_COL,
    WRITEBACK_VERSION_COL,
]

# Subset required for the flywheel to function (other columns are
# optional audit metadata).
REQUIRED_COLUMNS: Final[List[str]] = [
    DRUG_COL,
    DISEASE_COL,
    OUTCOME_COL,
    TIMESTAMP_COL,
]


# ---------------------------------------------------------------------------
# CANONICAL OUTCOME VALUES (issue #337, #340)
# ---------------------------------------------------------------------------
# The exact strings written to the `outcome` column. Consumers MUST
# branch on these exact strings — never on "true"/"false" or 0/1.

OUTCOME_VALIDATED_POSITIVE: Final[str] = "validated_positive"
OUTCOME_VALIDATED_TOXIC: Final[str] = "validated_toxic"
OUTCOME_VALIDATED_NEGATIVE: Final[str] = "validated_negative"
OUTCOME_INVALIDATED: Final[str] = "invalidated"

VALID_OUTCOMES: Final[List[str]] = [
    OUTCOME_VALIDATED_POSITIVE,
    OUTCOME_VALIDATED_TOXIC,
    OUTCOME_VALIDATED_NEGATIVE,
    OUTCOME_INVALIDATED,
]

# Outcomes that count as POSITIVE labels for GT retraining.
POSITIVE_OUTCOMES: Final[List[str]] = [OUTCOME_VALIDATED_POSITIVE]

# Outcomes that count as TOXIC (negative penalty) for the RL ranker.
TOXIC_OUTCOMES: Final[List[str]] = [OUTCOME_VALIDATED_TOXIC]

# Outcomes that count as NEGATIVE labels for GT retraining (excluded
# from positive labels; could be used as label=0 in a future enhancement).
NEGATIVE_OUTCOMES: Final[List[str]] = [
    OUTCOME_VALIDATED_TOXIC,
    OUTCOME_VALIDATED_NEGATIVE,
]

# For the RL ranker: which outcomes get +bonus vs -penalty.
BONUS_OUTCOMES: Final[List[str]] = [OUTCOME_VALIDATED_POSITIVE]
PENALTY_OUTCOMES: Final[List[str]] = [OUTCOME_VALIDATED_TOXIC]

# Reward magnitudes (issues #340, #350).
VALIDATED_POSITIVE_BONUS: Final[float] = 0.1
VALIDATED_TOXIC_PENALTY: Final[float] = 0.5


# ---------------------------------------------------------------------------
# NEO4j EDGE LABELS (issue #342)
# ---------------------------------------------------------------------------
# Each outcome maps to a distinct edge label in the knowledge graph.
# The FOR suffix on VALIDATED_TOXIC_FOR makes the semantics explicit:
# the drug is toxic FOR this disease (drug→disease relationship), not
# just "toxic" in general (which would be a drug property, not an edge).

EDGE_VALIDATED_TREATS: Final[str] = "VALIDATED_TREATS"
EDGE_VALIDATED_TOXIC_FOR: Final[str] = "VALIDATED_TOXIC_FOR"
EDGE_VALIDATED_NEGATIVE_FOR: Final[str] = "VALIDATED_NEGATIVE_FOR"

_EDGE_LABEL_MAP: Final[dict] = {
    OUTCOME_VALIDATED_POSITIVE: EDGE_VALIDATED_TREATS,
    OUTCOME_VALIDATED_TOXIC: EDGE_VALIDATED_TOXIC_FOR,
    OUTCOME_VALIDATED_NEGATIVE: EDGE_VALIDATED_NEGATIVE_FOR,
    OUTCOME_INVALIDATED: EDGE_VALIDATED_NEGATIVE_FOR,  # invalidated = negative
}


def edge_label_for_outcome(outcome: str) -> str:
    """Return the Neo4j edge label for a given outcome.

    Falls back to EDGE_VALIDATED_TREATS for unknown outcomes (defensive —
    better to record the edge than to silently drop it). Logs a warning
    so the operator sees the unexpected outcome.
    """
    import logging
    _log = logging.getLogger(__name__)
    label = _EDGE_LABEL_MAP.get(outcome)
    if label is None:
        _log.warning(
            "shared.contracts.writeback: unknown outcome %r — falling back "
            "to EDGE_VALIDATED_TREATS. Update _EDGE_LABEL_MAP if this "
            "outcome should map to a different edge label.",
            outcome,
        )
        return EDGE_VALIDATED_TREATS
    return label


# ---------------------------------------------------------------------------
# NEO4j NODE LABELS (issue #341)
# ---------------------------------------------------------------------------
# TM 17 contract specifies :Drug with canonical drug_id. The current
# Phase 2 kg_builder uses :Compound with name. The writeback defensively
# MERGEs against BOTH labels so it works regardless of which schema the
# KG is in. This prevents node fragmentation (the bug where MERGE on
# :Drug creates a duplicate of an existing :Compound node).

NEO4J_DRUG_LABEL_PREFERRED: Final[str] = "Drug"          # TM 17 contract
NEO4J_DRUG_LABEL_LEGACY: Final[str] = "Compound"          # Current Phase 2 KG
NEO4J_DRUG_LABELS: Final[Tuple[str, ...]] = (
    NEO4J_DRUG_LABEL_PREFERRED,
    NEO4J_DRUG_LABEL_LEGACY,
)

NEO4J_DISEASE_LABEL: Final[str] = "Disease"

# Canonical identifier properties.
NEO4J_DRUG_ID_PROP: Final[str] = "drug_id"     # TM 17 canonical ID
NEO4J_DRUG_NAME_PROP: Final[str] = "name"      # Current KG identifier
NEO4J_DISEASE_ID_PROP: Final[str] = "disease_id"
NEO4J_DISEASE_NAME_PROP: Final[str] = "name"


# ---------------------------------------------------------------------------
# ATOMIC WRITE PROFILE (issue #351)
# ---------------------------------------------------------------------------
# Both the CSV writeback and the checkpoint save MUST be atomic. The
# pattern: write to a temp file in the same directory, fsync, then
# os.replace (atomic on POSIX). This contract documents the requirement;
# the implementation lives in phase4/writeback.py and trainer.py.

ATOMIC_WRITE_TMP_SUFFIX: Final[str] = ".tmp"
ATOMIC_WRITE_FSYNC: Final[bool] = True   # fsync before rename for durability


# ---------------------------------------------------------------------------
# WRITEBACK VERSION
# ---------------------------------------------------------------------------
# Bumped when the CSV schema changes. Consumers should validate this on
# read and refuse to load incompatible versions (forward-compat).

WRITEBACK_VERSION: Final[str] = "2.0.0-shared-contract"


__all__ = [
    # Path
    "CANONICAL_VALIDATED_CSV",
    "LEGACY_RL_VALIDATED_CSV",
    "get_validated_csv_path",
    "ensure_csv_dir",
    # Columns
    "OUTCOME_COL",
    "DRUG_COL",
    "DISEASE_COL",
    "TIMESTAMP_COL",
    "VALIDATED_BY_COL",
    "VALIDATION_STUDY_ID_COL",
    "NOTES_COL",
    "ORIGINAL_GT_SCORE_COL",
    "ORIGINAL_RL_RANK_COL",
    "WRITEBACK_VERSION_COL",
    "WRITEBACK_CSV_COLUMNS",
    "REQUIRED_COLUMNS",
    # Outcomes
    "OUTCOME_VALIDATED_POSITIVE",
    "OUTCOME_VALIDATED_TOXIC",
    "OUTCOME_VALIDATED_NEGATIVE",
    "OUTCOME_INVALIDATED",
    "VALID_OUTCOMES",
    "POSITIVE_OUTCOMES",
    "TOXIC_OUTCOMES",
    "NEGATIVE_OUTCOMES",
    "BONUS_OUTCOMES",
    "PENALTY_OUTCOMES",
    "VALIDATED_POSITIVE_BONUS",
    "VALIDATED_TOXIC_PENALTY",
    # Edge labels
    "EDGE_VALIDATED_TREATS",
    "EDGE_VALIDATED_TOXIC_FOR",
    "EDGE_VALIDATED_NEGATIVE_FOR",
    "edge_label_for_outcome",
    # Neo4j labels
    "NEO4J_DRUG_LABEL_PREFERRED",
    "NEO4J_DRUG_LABEL_LEGACY",
    "NEO4J_DRUG_LABELS",
    "NEO4J_DISEASE_LABEL",
    "NEO4J_DRUG_ID_PROP",
    "NEO4J_DRUG_NAME_PROP",
    "NEO4J_DISEASE_ID_PROP",
    "NEO4J_DISEASE_NAME_PROP",
    # Atomic write
    "ATOMIC_WRITE_TMP_SUFFIX",
    "ATOMIC_WRITE_FSYNC",
    # Version
    "WRITEBACK_VERSION",
]
