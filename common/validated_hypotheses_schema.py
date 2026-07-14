"""
Shared schema for validated_hypotheses — single source of truth.

INT-014 + INT-015 + INT-016 ROOT FIX:
The previous codebase had THREE different ways to reference the same data:
  1. Writeback wrote to: phase1/processed_data/validated_hypotheses.csv
     with column name "outcome" containing values like
     "validated_positive" / "validated_toxic".
  2. RL ranker searched:  rl/validated_hypotheses.csv (via module_dir,
     CWD, and env var — NEVER the Phase 1 path).
  3. Trainer read column: "validated" with values in ("true","1","yes"),
     filtering out ALL rows from the writeback file.

Result: the data flywheel was broken at EVERY link. Validated hypotheses
from pharma partners were INVISIBLE to both the RL ranker and the GT
trainer. The DOCX §10 promise ("validated hypotheses feed back into the
model") was unfulfilled.

ROOT FIX: This module defines ONE canonical path, ONE canonical column
schema, and ONE canonical set of outcome values. All writers and readers
import from here. No file should hardcode its own path or column names.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Final, List

# ---------------------------------------------------------------------------
# CANONICAL PATH
# ---------------------------------------------------------------------------
# The single write/read location for validated hypotheses CSV.
# INT-014: both writeback.py AND rl_drug_ranker.py AND trainer.py MUST
# use this path. No component should define its own path.

_REPO_ROOT: Final[str] = str(Path(__file__).resolve().parents[1])

CANONICAL_VALIDATED_CSV: Final[str] = os.path.join(
    _REPO_ROOT, "phase1", "processed_data", "validated_hypotheses.csv"
)

# Fallback for legacy deployments (still searched if canonical missing).
LEGACY_RL_VALIDATED_CSV: Final[str] = os.path.join(
    _REPO_ROOT, "rl", "validated_hypotheses.csv"
)

# ---------------------------------------------------------------------------
# CANONICAL COLUMN NAMES
# ---------------------------------------------------------------------------
# INT-015: writeback writes "outcome"; trainer reads "validated".
# ROOT FIX: everyone uses OUTCOME_COL. The trainer converts on read.

OUTCOME_COL: Final[str] = "outcome"
DRUG_COL: Final[str] = "drug"
DISEASE_COL: Final[str] = "disease"
TIMESTAMP_COL: Final[str] = "validated_at"
VALIDATED_BY_COL: Final[str] = "validated_by"

# ---------------------------------------------------------------------------
# CANONICAL OUTCOME VALUES
# ---------------------------------------------------------------------------
# Writeback produces these values in the outcome column.
# Consumers MUST branch on these exact strings.

OUTCOME_VALIDATED_POSITIVE: Final[str] = "validated_positive"
OUTCOME_VALIDATED_TOXIC: Final[str] = "validated_toxic"
OUTCOME_VALIDATED_NEGATIVE: Final[str] = "validated_negative"
OUTCOME_INVALIDATED: Final[str] = "invalidated"

# For the trainer: which outcomes count as "positive" labels for retraining.
POSITIVE_OUTCOMES: Final[List[str]] = [OUTCOME_VALIDATED_POSITIVE]

# For the RL ranker: which outcomes get +bonus vs -penalty.
BONUS_OUTCOMES: Final[List[str]] = [OUTCOME_VALIDATED_POSITIVE]
PENALTY_OUTCOMES: Final[List[str]] = [OUTCOME_VALIDATED_TOXIC]

# All valid outcome values (for schema validation).
VALID_OUTCOMES: Final[List[str]] = [
    OUTCOME_VALIDATED_POSITIVE,
    OUTCOME_VALIDATED_TOXIC,
    OUTCOME_VALIDATED_NEGATIVE,
    OUTCOME_INVALIDATED,
]

# ---------------------------------------------------------------------------
# REQUIRED CSV COLUMNS (schema validation)
# ---------------------------------------------------------------------------
REQUIRED_COLUMNS: Final[List[str]] = [
    DRUG_COL,
    DISEASE_COL,
    OUTCOME_COL,
    TIMESTAMP_COL,
]

# ---------------------------------------------------------------------------
# Helper functions
# ---------------------------------------------------------------------------

def ensure_csv_dir() -> None:
    """Ensure the directory for the canonical CSV exists."""
    os.makedirs(os.path.dirname(CANONICAL_VALIDATED_CSV), exist_ok=True)


def get_validated_csv_path() -> str:
    """Return the canonical path, respecting env-var override.

    The env var ``VALIDATED_HYPOTHESES_CSV`` can override the canonical
    path for deployments that need a custom location.
    """
    return os.environ.get("VALIDATED_HYPOTHESES_CSV", CANONICAL_VALIDATED_CSV)
