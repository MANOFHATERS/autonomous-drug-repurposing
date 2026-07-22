"""Phase 4 — RL-Driven Hypothesis Ranker & Data Flywheel Writeback.

Team Cosmic / Autonomous Drug Repurposing Platform.

This package implements:
  - The RL agent that ranks drug-disease hypotheses (in rl/ submodule)
  - The writeback module that closes the data flywheel loop (DOCX §10)
    by feeding pharma partner validations back into Phases 1, 2, and 3.

P4-022 ROOT FIX: previously empty (0 bytes). Now exports the public API
and version metadata for provenance tracking.
"""
from __future__ import annotations

# P4-006 v142 FORENSIC ROOT FIX: aligned with rl/__init__.py,
# rl/rl_drug_ranker.py, and rl/service.py — all 5 version constants
# now hold "4.2.0" (was "4.1.0" here, conflicting with "4.2.0" in
# rl_drug_ranker.py and "1.0.0" in service.py).
__version__ = "4.2.0"
__schema_version__ = "4.2.0"

# Re-export public symbols from writeback.py so callers can do:
#   from phase4 import write_validated_hypothesis
# instead of:
#   from phase4.writeback import write_validated_hypothesis
from phase4.writeback import (
    WRITEBACK_VERSION,
    ValidationOutcome,
    ValidatedHypothesis,
    write_validated_hypothesis,
    writeback_to_phase1,
    writeback_to_phase2,
    writeback_to_phase3,
    list_validated_hypotheses,
)

__all__ = [
    "__version__",
    "__schema_version__",
    "WRITEBACK_VERSION",
    "ValidationOutcome",
    "ValidatedHypothesis",
    "write_validated_hypothesis",
    "writeback_to_phase1",
    "writeback_to_phase2",
    "writeback_to_phase3",
    "list_validated_hypotheses",
]
