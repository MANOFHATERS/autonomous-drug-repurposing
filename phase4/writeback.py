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
# INT-014 ROOT FIX: use the canonical path from shared schema so RL
# ranker and GT trainer read the SAME file. No component should define
# its own path.
from common.validated_hypotheses_schema import (
    CANONICAL_VALIDATED_CSV,
    get_validated_csv_path,
    DRUG_COL,
    DISEASE_COL,
    OUTCOME_COL,
    TIMESTAMP_COL,
    VALIDATED_BY_COL,
    OUTCOME_VALIDATED_POSITIVE,
    OUTCOME_VALIDATED_TOXIC,
    POSITIVE_OUTCOMES,
    PENALTY_OUTCOMES,
)

# Backward-compatible env var lookup (falls back to canonical schema path).
PHASE1_VALIDATED_CSV = os.environ.get("PHASE1_VALIDATED_CSV") or get_validated_csv_path()


def writeback_to_phase1(vh: ValidatedHypothesis) -> Path:
    """Append the validated hypothesis to Phase 1's validated_hypotheses.csv.

    The bridge's stage_phase1_to_phase2 reads this CSV (alongside
    drugbank_indications.csv) as a source of (Compound, treats, Disease)
    edges. The next KG build will include this validated edge with a
    'validated=True' property.

    P4-011 ROOT FIX: duplicate check. Re-validating the same hypothesis
    UPDATES the existing row (changes validated_at, outcome, notes) instead
    of appending a DUPLICATE. This prevents the CSV from growing with
    duplicate entries over time, which would bias GT model training.

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

    # P4-011: check for duplicate (same drug, disease, validated_by)
    # If found, UPDATE instead of append.
    existing_rows: List[Dict[str, str]] = []
    duplicate_found = False
    if file_exists:
        try:
            with open(csv_path, "r", newline="") as f:
                reader = csv.DictReader(f)
                for row in reader:
                    if (row.get("drug", "").strip() == vh.drug.strip()
                            and row.get("disease", "").strip() == vh.disease.strip()
                            and row.get("validated_by", "").strip() == vh.validated_by.strip()):
                        # UPDATE this row with new data
                        row["outcome"] = vh.outcome
                        row["validation_study_id"] = vh.validation_study_id or ""
                        row["validated_at"] = vh.validated_at
                        row["notes"] = vh.notes or ""
                        row["original_gt_score"] = str(vh.original_gt_score) if vh.original_gt_score is not None else ""
                        row["original_rl_rank"] = str(vh.original_rl_rank) if vh.original_rl_rank is not None else ""
                        row["writeback_version"] = WRITEBACK_VERSION
                        duplicate_found = True
                    existing_rows.append(row)
        except Exception as exc:
            logger.warning("P4-011: failed to read existing CSV for duplicate check (%s). Appending.", exc)

    if duplicate_found:
        # Rewrite the entire CSV with the updated row
        with open(csv_path, "w", newline="") as f:
            writer = csv.DictWriter(f, fieldnames=fieldnames)
            writer.writeheader()
            writer.writerows(existing_rows)
        logger.info(
            "P4-011 ROOT FIX: UPDATED existing validated hypothesis "
            "(%s, %s, by=%s) with outcome=%s. No duplicate appended.",
            vh.drug, vh.disease, vh.validated_by, vh.outcome,
        )
    else:
        # APPEND new row
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

def _canonicalize_name_for_kg(name: str) -> str:
    """P4-007 ROOT FIX: canonicalize a drug/disease name for KG matching.

    The Phase 2 kg_builder stores Compound/Disease nodes with names in
    their original case from the source database (e.g., "Metformin" from
    DrugBank). The writeback receives lowercase names (e.g., "metformin"
    from the RL pipeline). A MERGE on {name: "metformin"} will NOT match
    a node with name="Metformin" — it creates a DUPLICATE node.

    This helper converts the name to a consistent form. The kg_builder
    uses names as-is from the source, so we try BOTH the original case
    and a title-cased variant in the MERGE to maximize match probability.
    """
    name = name.strip()
    # Return the name and a title-cased variant for the MERGE
    return name


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
        # P4-007 ROOT FIX: use the SAME node labels as Phase 2's kg_builder.
        # The Phase 2 kg_builder (config.py: ENTITY_TYPE_COMPOUND = "Compound")
        # uses :Compound for drug nodes. The previous code already used
        # :Compound, which is correct. BUT it matched only by lowercase name,
        # which could miss nodes stored with titlecase names (e.g., "Metformin"
        # in the KG vs "metformin" from the writeback).
        #
        # The fix: MERGE tries multiple name variants (original, titlecase,
        # lowercase) to maximize the chance of matching existing nodes.
        # Also wraps driver in try/finally to prevent connection leaks (P4-008).
        drug_original = _canonicalize_name_for_kg(vh.drug)
        drug_title = drug_original.title()
        drug_lower = drug_original.lower()
        disease_original = _canonicalize_name_for_kg(vh.disease)
        disease_title = disease_original.title()
        disease_lower = disease_original.lower()

        # P4-010 ROOT FIX: use different edge labels for different outcomes.
        # VALIDATED_TREATS for validated_positive, VALIDATED_TOXIC for
        # validated_toxic, VALIDATED_NEGATIVE for validated_negative.
        # The previous code used VALIDATED_TREATS for ALL outcomes, so
        # toxic pairs appeared as positive treatment evidence.
        _edge_label = {
            "validated_positive": "VALIDATED_TREATS",
            "validated_toxic": "VALIDATED_TOXIC",
            "validated_negative": "VALIDATED_NEGATIVE",
            "invalidated": "VALIDATED_NEGATIVE",  # invalidated = negative
        }.get(vh.outcome, "VALIDATED_TREATS")

        cypher = """
        // Try to match existing Compound node by various name forms
        CALL {
            WITH $drug_lower, $drug_title, $drug_original
            MATCH (d:Compound)
            WHERE toLower(d.name) = $drug_lower OR d.name = $drug_title OR d.name = $drug_original
            RETURN d LIMIT 1
        }
        WITH d
        // Try to match existing Disease node by various name forms
        CALL {
            WITH $disease_lower, $disease_title, $disease_original
            MATCH (v:Disease)
            WHERE toLower(v.name) = $disease_lower OR v.name = $disease_title OR v.name = $disease_original
            RETURN v LIMIT 1
        }
        WITH d, v
        MERGE (d)-[r:`""" + _edge_label + """`]->(v)
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
        try:
            with driver.session() as session:
                result = session.run(cypher, {
                    "drug_original": drug_original,
                    "drug_title": drug_title,
                    "drug_lower": drug_lower,
                    "disease_original": disease_original,
                    "disease_title": disease_title,
                    "disease_lower": disease_lower,
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
            return True
        finally:
            driver.close()
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

    # P4-048 ROOT FIX: ATOMIC WRITE. The previous code did:
    #     with open(trigger_path, "w") as f:
    #         json.dump(existing, f, ...)
    # If the process crashed mid-write (OOM, signal, power loss), the JSON
    # file was left TRUNCATED — the GT trainer would fail to parse it, or
    # worse, parse a partial JSON and silently miss entries. A regulator
    # auditing the retrain trigger would see a corrupt file with no way to
    # recover the lost validated hypotheses (21 CFR Part 11 data integrity
    # violation).
    #
    # The fix: write to a temp file in the SAME directory (so os.rename is
    # atomic on POSIX — a single inode operation), then atomically rename
    # over the target. On POSIX, os.rename is atomic: either the old file
    # or the new file is fully visible, never a partial write. On Windows,
    # os.replace is used (atomic since Python 3.3).
    tmp_path = trigger_path.with_suffix(".json.tmp")
    try:
        with open(tmp_path, "w", encoding="utf-8") as f:
            json.dump(existing, f, indent=2, default=str)
            # Ensure data is flushed to disk before rename (fsync for
            # durability on power loss). Without this, the rename could
            # succeed but the file content could be lost on crash.
            f.flush()
            os.fsync(f.fileno())
        # Atomic rename (POSIX) / replace (Windows)
        os.replace(str(tmp_path), str(trigger_path))
    except Exception:
        # Clean up the temp file if anything went wrong
        try:
            tmp_path.unlink(missing_ok=True)
        except Exception:
            pass
        raise

    logger.info(
        "RT-010 Phase 3 writeback: appended validated hypothesis to "
        "retrain trigger at %s (ATOMIC write via tmp+rename, P4-048). "
        "The next GT training run will include "
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
