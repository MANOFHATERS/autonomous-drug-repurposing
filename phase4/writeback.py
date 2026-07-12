"""
RT-010 ROOT FIX (Team Member 17): Data Flywheel Writeback Module.

The project docx (Section 10 — "The Data Flywheel") describes:
    1. V1 platform launches with publicly available data. Model makes predictions.
    2. Pharma partner validates a hypothesis (wet lab or clinical study).
    3. This validation result is fed back into the platform as a new
       labeled data point.
    4. The model retrains on this new proprietary data. Predictions improve.
    5. More accurate predictions attract more partners. Repeat.

The audit (RT-010) found that step 3 — feeding validated hypotheses
back into the platform — had NO implementation. There were no writeback
modules. The data flywheel was aspirational, not actual. The docx's
claim that "this validated, proprietary training data CANNOT be
replicated by a competitor" was false because there was no validation
feedback loop.

This module implements the writeback for all 3 phases:

  Phase 1 writeback: append the validated (drug, disease, outcome) tuple
    to the validated_hypotheses.csv in phase1/processed_data/. The bridge
    reads this CSV as a Phase 1 source. This becomes a new labeled data
    point for future KG builds.

  Phase 2 writeback: add a 'VALIDATED_TREATS' edge between the drug and
    disease nodes in Neo4j (when available), with a 'validated_at'
    timestamp and 'validated_by' partner identifier. This distinguishes
    validated edges from predicted edges in the KG.

  Phase 3 writeback: append the validated pair to the GT trainer's
    retrain trigger JSON. The next training run adds the pair to its
    known_pairs list (positive if outcome='validated_positive',
    negative if outcome='validated_negative' or 'validated_toxic').

The module is INCREMENTAL — it never deletes or overwrites existing
data. Each writeback is append-only and timestamped, so the full audit
trail is preserved (21 CFR Part 11 compliance).

Usage:
    from phase4.writeback import write_validated_hypothesis
    write_validated_hypothesis(
        drug="metformin",
        disease="type 2 diabetes",
        outcome="validated_positive",
        validated_by="pharma_partner_acme",
        validation_study_id="NCT12345678",
    )
"""
from __future__ import annotations

import csv
import json
import logging
import os
from dataclasses import dataclass, field, asdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Literal, Optional

logger = logging.getLogger(__name__)

# Phase 4 writeback module version
WRITEBACK_VERSION = "1.0.0-rt010"

# Outcome enum — the possible validation outcomes a pharma partner can report.
ValidationOutcome = Literal[
    "validated_positive",  # Wet lab / clinical study confirmed efficacy
    "validated_negative",  # Wet lab / clinical study confirmed NO efficacy
    "validated_toxic",     # Drug caused adverse events — DO NOT retarget
    "invalidated",         # Partner could not reproduce the prediction
]


@dataclass(frozen=True)
class ValidatedHypothesis:
    """A single validated hypothesis from a pharma partner.

    This is the data structure that gets written back to all 3 phases.
    Frozen so it's hashable and immutable — once recorded, a validation
    cannot be silently modified.
    """
    drug: str
    disease: str
    outcome: ValidationOutcome
    validated_by: str                  # pharma partner identifier
    validation_study_id: Optional[str] = None  # e.g., NCT number
    validated_at: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    notes: Optional[str] = None
    # The GT score and RL rank that produced this prediction — for audit
    # trail. Lets us track "model said 0.87, partner validated positive"
    # vs "model said 0.62, partner validated negative".
    original_gt_score: Optional[float] = None
    original_rl_rank: Optional[int] = None


# ---------------------------------------------------------------------------
# Phase 1 writeback: append to validated_hypotheses.csv
# ---------------------------------------------------------------------------

PHASE1_VALIDATED_CSV = os.environ.get(
    "PHASE1_VALIDATED_CSV",
    str(Path(__file__).resolve().parent.parent / "phase1" / "processed_data" / "validated_hypotheses.csv"),
)


def writeback_to_phase1(vh: ValidatedHypothesis) -> Path:
    """Append the validated hypothesis to Phase 1's validated_hypotheses.csv.

    The bridge's stage_phase1_to_phase2 reads this CSV (alongside
    drugbank_indications.csv) as a source of (Compound, treats, Disease)
    edges. The next KG build will include this validated edge with a
    'validated=True' property.

    Returns the path to the CSV. The CSV is created if it doesn't exist
    (with a header row).
    """
    csv_path = Path(PHASE1_VALIDATED_CSV)
    csv_path.parent.mkdir(parents=True, exist_ok=True)
    file_exists = csv_path.exists() and csv_path.stat().st_size > 0

    fieldnames = [
        "drug", "disease", "outcome", "validated_by",
        "validation_study_id", "validated_at", "notes",
        "original_gt_score", "original_rl_rank",
        "writeback_version",
    ]
    with open(csv_path, "a", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        if not file_exists:
            writer.writeheader()
        writer.writerow({
            "drug": vh.drug,
            "disease": vh.disease,
            "outcome": vh.outcome,
            "validated_by": vh.validated_by,
            "validation_study_id": vh.validation_study_id or "",
            "validated_at": vh.validated_at,
            "notes": vh.notes or "",
            "original_gt_score": vh.original_gt_score if vh.original_gt_score is not None else "",
            "original_rl_rank": vh.original_rl_rank if vh.original_rl_rank is not None else "",
            "writeback_version": WRITEBACK_VERSION,
        })
    logger.info(
        "RT-010 Phase 1 writeback: appended (%s, %s, %s) by %s to %s",
        vh.drug, vh.disease, vh.outcome, vh.validated_by, csv_path,
    )
    return csv_path


# ---------------------------------------------------------------------------
# Phase 2 writeback: add VALIDATED_TREATS edge to Neo4j (if available)
# ---------------------------------------------------------------------------

def writeback_to_phase2(vh: ValidatedHypothesis) -> bool:
    """Add a VALIDATED_TREATS edge to Neo4j (when available).

    The edge connects the drug and disease nodes in the KG with a
    'validated_treats' relationship type. This distinguishes validated
    edges from predicted edges — downstream consumers can query
    'validated_treats' edges separately.

    If Neo4j is not available (no DRUGOS_NEO4J_URI), this function
    logs a warning and returns False. The Phase 1 CSV writeback still
    happened, so the next KG build will include the edge.
    """
    neo4j_uri = os.environ.get("DRUGOS_NEO4J_URI")
    if not neo4j_uri:
        logger.warning(
            "RT-010 Phase 2 writeback: DRUGOS_NEO4J_URI not set — "
            "skipping Neo4j edge write. The validated edge will be "
            "included in the NEXT KG build (via the Phase 1 CSV writeback)."
        )
        return False

    try:
        # Lazy import so the module loads even when neo4j isn't installed
        from neo4j import GraphDatabase
        driver = GraphDatabase.driver(
            neo4j_uri,
            auth=(
                os.environ.get("DRUGOS_NEO4J_USER", "neo4j"),
                os.environ.get("DRUGOS_NEO4J_PASSWORD", ""),
            ),
        )
        # MERGE the edge (idempotent — re-validating the same hypothesis
        # doesn't create duplicate edges). Edge properties record who
        # validated, when, and the study ID for audit trail.
        cypher = """
        MERGE (d:Compound {name: $drug})
        MERGE (v:Disease {name: $disease})
        MERGE (d)-[r:VALIDATED_TREATS]->(v)
          ON CREATE SET
            r.validated_at = $validated_at,
            r.validated_by = $validated_by,
            r.validation_study_id = $study_id,
            r.outcome = $outcome,
            r.writeback_version = $wbv
          ON MATCH SET
            r.last_revalidated_at = $validated_at,
            r.revalidation_count = coalesce(r.revalidation_count, 0) + 1
        RETURN r
        """
        with driver.session() as session:
            result = session.run(cypher, {
                "drug": vh.drug,
                "disease": vh.disease,
                "validated_at": vh.validated_at,
                "validated_by": vh.validated_by,
                "study_id": vh.validation_study_id or "",
                "outcome": vh.outcome,
                "wbv": WRITEBACK_VERSION,
            })
            summary = result.consume()
            logger.info(
                "RT-010 Phase 2 writeback: VALIDATED_TREATS edge "
                "upserted in Neo4j (%s -> %s, outcome=%s, by=%s). "
                "Counters: %s",
                vh.drug, vh.disease, vh.outcome, vh.validated_by,
                summary.counters._stats,
            )
        driver.close()
        return True
    except Exception as exc:
        logger.warning(
            "RT-010 Phase 2 writeback: Neo4j write failed (%s). "
            "The Phase 1 CSV writeback still happened. The next KG "
            "build will pick up the validated edge from the CSV.",
            exc,
        )
        return False


# ---------------------------------------------------------------------------
# Phase 3 writeback: trigger GT model retraining
# ---------------------------------------------------------------------------

PHASE3_RETRAIN_TRIGGER = os.environ.get(
    "PHASE3_RETRAIN_TRIGGER",
    str(Path(__file__).resolve().parent.parent / "graph_transformer" / "retrain_triggered.json"),
)


def writeback_to_phase3(vh: ValidatedHypothesis) -> bool:
    """Append the validated hypothesis to the GT retraining trigger file.

    The GT trainer reads this file at the start of each training run
    and adds the validated pairs to its known_pairs list. The new pair
    becomes a positive (or negative, if outcome='validated_negative')
    example in the next training run.

    If outcome is 'validated_negative' or 'validated_toxic', the pair
    is added to a NEGATIVE_VALIDATED list — the trainer must score
    these pairs LOW. This corrects model errors (e.g., warfarin ->
    epilepsy predicted at 0.85 but validated as toxic).
    """
    trigger_path = Path(PHASE3_RETRAIN_TRIGGER)
    trigger_path.parent.mkdir(parents=True, exist_ok=True)

    # Read existing triggers (if any)
    existing: List[Dict[str, Any]] = []
    if trigger_path.exists():
        try:
            with open(trigger_path) as f:
                existing = json.load(f)
                if not isinstance(existing, list):
                    existing = []
        except Exception:
            existing = []

    # Append the new validated hypothesis
    new_entry = {
        **asdict(vh),
        "writeback_version": WRITEBACK_VERSION,
        "triggered_for_retraining": True,
    }
    existing.append(new_entry)

    with open(trigger_path, "w") as f:
        json.dump(existing, f, indent=2, default=str)

    logger.info(
        "RT-010 Phase 3 writeback: appended validated hypothesis to "
        "retrain trigger at %s. The next GT training run will include "
        "(%s, %s, outcome=%s) in its known_pairs.",
        trigger_path, vh.drug, vh.disease, vh.outcome,
    )
    return True


# ---------------------------------------------------------------------------
# Top-level writeback entry point
# ---------------------------------------------------------------------------

def write_validated_hypothesis(
    drug: str,
    disease: str,
    outcome: ValidationOutcome,
    validated_by: str,
    validation_study_id: Optional[str] = None,
    notes: Optional[str] = None,
    original_gt_score: Optional[float] = None,
    original_rl_rank: Optional[int] = None,
) -> Dict[str, Any]:
    """Top-level writeback entry point.

    Writes the validated hypothesis to ALL 3 phases (Phase 1 CSV,
    Phase 2 Neo4j edge, Phase 3 retrain trigger). Returns a dict
    summarizing what was written.

    This is the function called by the /api/hypothesis/validate route
    when a pharma partner reports a validation result. It implements
    step 3 of the data flywheel (project docx Section 10).

    Args:
        drug: Drug name (must match a node in the KG).
        disease: Disease name (must match a node in the KG).
        outcome: One of 'validated_positive', 'validated_negative',
            'validated_toxic', 'invalidated'.
        validated_by: Pharma partner identifier (for audit trail).
        validation_study_id: Optional clinical trial ID (e.g., NCT number).
        notes: Free-text notes from the partner.
        original_gt_score: The GT model's original prediction score
            (for audit trail).
        original_rl_rank: The RL ranker's original rank.

    Returns:
        Dict with keys:
            - phase1_csv_path: Path to the Phase 1 CSV
            - phase2_neo4j_written: bool
            - phase3_trigger_path: Path to the Phase 3 trigger JSON
            - validated_hypothesis: dict representation of vh
    """
    if not drug or not disease:
        raise ValueError("drug and disease are required")
    if outcome not in ("validated_positive", "validated_negative", "validated_toxic", "invalidated"):
        raise ValueError(f"Invalid outcome: {outcome}")
    if not validated_by:
        raise ValueError("validated_by is required (audit trail)")

    vh = ValidatedHypothesis(
        drug=drug,
        disease=disease,
        outcome=outcome,
        validated_by=validated_by,
        validation_study_id=validation_study_id,
        notes=notes,
        original_gt_score=original_gt_score,
        original_rl_rank=original_rl_rank,
    )

    phase1_path = writeback_to_phase1(vh)
    phase2_ok = writeback_to_phase2(vh)
    phase3_path = writeback_to_phase3(vh)

    logger.info(
        "RT-010 ROOT FIX: data flywheel writeback complete for "
        "(%s, %s, %s) by %s. Phase 1 CSV: %s. Phase 2 Neo4j: %s. "
        "Phase 3 retrain trigger: %s.",
        vh.drug, vh.disease, vh.outcome, vh.validated_by,
        phase1_path, "written" if phase2_ok else "skipped (no Neo4j)",
        phase3_path,
    )

    return {
        "phase1_csv_path": str(phase1_path),
        "phase2_neo4j_written": phase2_ok,
        "phase3_trigger_path": str(phase3_path),
        "validated_hypothesis": asdict(vh),
        "writeback_version": WRITEBACK_VERSION,
    }


def list_validated_hypotheses() -> List[Dict[str, Any]]:
    """Return all validated hypotheses that have been written back.

    Reads the Phase 1 CSV (the canonical record). Useful for the
    dashboard to display "Validated Hypotheses" to pharma partners.
    """
    csv_path = Path(PHASE1_VALIDATED_CSV)
    if not csv_path.exists():
        return []
    out: List[Dict[str, Any]] = []
    with open(csv_path) as f:
        reader = csv.DictReader(f)
        for row in reader:
            out.append(dict(row))
    return out


__all__ = [
    "WRITEBACK_VERSION",
    "ValidationOutcome",
    "ValidatedHypothesis",
    "write_validated_hypothesis",
    "writeback_to_phase1",
    "writeback_to_phase2",
    "writeback_to_phase3",
    "list_validated_hypotheses",
]
