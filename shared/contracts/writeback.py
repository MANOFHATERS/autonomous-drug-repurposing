"""shared.contracts.writeback — writeback contract (Phase 4 -> Phase 3).

TASK 329 ROOT FIX (forensic, root-level):
  The writeback loop closes the data flywheel (DOCX §10): validated
  drug-disease predictions from Phase 4 are fed back to the Phase 3
  trainer as new labeled data points, the model retrains, predictions
  improve, repeat.

  Previously the writeback CSV schema was defined INLINE in
  ``rl/rl_drug_ranker.py``'s ``OUTPUT_SCHEMA`` dict — Phase 3's trainer
  had to reverse-engineer the schema from the writer's source code. When
  Phase 4 renamed the ``outcome`` column or added a new outcome value,
  Phase 3 silently broke until someone noticed missing training rows.

  This module extracts the writeback contract into a SHARED module that
  both sides import. The Phase 4 writer (``rl.validate`` /
  ``rl.rl_drug_ranker``) writes the CSV per this contract; the Phase 3
  trainer reader (``graph_transformer.training.trainer``) reads it per
  this contract. Any change is a compile-time error on both sides —
  the contract consistency test (Task 330) verifies writer/reader match.

Note
----
This contract intentionally re-exports the canonical names from
``rl/contracts/phase4_schema.py`` (Task 325) so that there is ONE
canonical definition of the validated_hypotheses CSV schema. The
writeback contract adds the WRITER_PATH and READER_PATH that determine
WHERE on disk the file lives — these are the only writeback-specific
fields.
"""
from __future__ import annotations

import os
from pathlib import Path
from typing import Tuple


# =============================================================================
# Re-export the canonical validated_hypotheses schema from Phase 4 contract
# =============================================================================
# This ensures ONE source of truth for the CSV column schema. The
# writeback contract adds only the writer/reader path information.
from rl.contracts.phase4_schema import (
    VALIDATED_HYPOTHESES_FILENAME as WRITEBACK_FILENAME,
    VALIDATED_HYPOTHESES_COLUMN_NAMES as WRITEBACK_CSV_COLUMNS,
    VALIDATED_HYPOTHESES_REQUIRED_COLUMNS as _WRITEBACK_REQUIRED_COLS,
    VALIDATED_HYPOTHESES_DTYPES as WRITEBACK_DTYPES,
    OUTCOME_VALUES as WRITEBACK_OUTCOME_VALUES,
    OUTCOME_POSITIVE as _OUTCOME_POSITIVE,
    OUTCOME_TOXIC as _OUTCOME_TOXIC,
    OUTCOME_INCONCLUSIVE as _OUTCOME_INCONCLUSIVE,
    OUTCOME_TO_LABEL as WRITEBACK_OUTCOME_TO_LABEL,
)


# =============================================================================
# Writer / reader paths
# =============================================================================
# The writer (Phase 4) writes to WRITEBACK_WRITER_PATH.
# The reader (Phase 3 trainer) reads from WRITEBACK_READER_PATH.
#
# By default these are the SAME path (the writer writes, the reader reads
# the same file). Operators can override via env vars to put the writer
# and reader on different filesystems (e.g. writer on a RL worker node,
# reader on a GT training node, synced via S3).

_DEFAULT_WRITEBACK_DIR = Path("data/writeback")

WRITEBACK_WRITER_PATH: Path = Path(
    os.environ.get("WRITEBACK_WRITER_PATH",
                   str(_DEFAULT_WRITEBACK_DIR / WRITEBACK_FILENAME))
)

WRITEBACK_READER_PATH: Path = Path(
    os.environ.get("WRITEBACK_READER_PATH",
                   str(_DEFAULT_WRITEBACK_DIR / WRITEBACK_FILENAME))
)


# =============================================================================
# Outcome classification (for Phase 3 retraining)
# =============================================================================
# Phase 3's trainer uses these sets to decide how to use each writeback row:
#   - POSITIVE outcomes -> add to positive training examples
#   - NEGATIVE outcomes -> add to negative training examples (with safety weight)
#   - INCONCLUSIVE outcomes -> EXCLUDE from retraining (no signal)
WRITEBACK_POSITIVE_OUTCOMES: Tuple[str, ...] = (_OUTCOME_POSITIVE,)
WRITEBACK_NEGATIVE_OUTCOMES: Tuple[str, ...] = (_OUTCOME_TOXIC,)
WRITEBACK_INCONCLUSIVE_OUTCOMES: Tuple[str, ...] = (_OUTCOME_INCONCLUSIVE,)


# =============================================================================
# Atomic write helper (used by Phase 4 writer)
# =============================================================================


def get_writer_path() -> Path:
    """Return the current writer path (re-read from env on each call).

    Phase 4's writer should call this at write time (not import time) so
    that env-var changes during a long-running process are picked up.
    """
    return Path(
        os.environ.get("WRITEBACK_WRITER_PATH",
                       str(_DEFAULT_WRITEBACK_DIR / WRITEBACK_FILENAME))
    )


def get_reader_path() -> Path:
    """Return the current reader path (re-read from env on each call).

    Phase 3's trainer should call this at read time so that env-var
    changes during a long-running process are picked up.
    """
    return Path(
        os.environ.get("WRITEBACK_READER_PATH",
                       str(_DEFAULT_WRITEBACK_DIR / WRITEBACK_FILENAME))
    )


__all__ = [
    "WRITEBACK_FILENAME",
    "WRITEBACK_WRITER_PATH",
    "WRITEBACK_READER_PATH",
    "WRITEBACK_CSV_COLUMNS",
    "WRITEBACK_DTYPES",
    "WRITEBACK_OUTCOME_VALUES",
    "WRITEBACK_POSITIVE_OUTCOMES",
    "WRITEBACK_NEGATIVE_OUTCOMES",
    "WRITEBACK_INCONCLUSIVE_OUTCOMES",
    "WRITEBACK_OUTCOME_TO_LABEL",
    "get_writer_path",
    "get_reader_path",
]
