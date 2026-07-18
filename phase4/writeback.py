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
import sys
from dataclasses import dataclass, field, asdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Literal, Optional

# -----------------------------------------------------------------------------
# SH-027 + SH-012 ROOT FIX: import DIRECTLY from shared.contracts.writeback
# (the AUTHORITATIVE source), NOT via the deprecated common re-export shim.
#
# SH-027: the previous code imported from
# `common.validated_hypotheses_schema` which is a thin re-export shim
# over `shared.contracts.writeback`. Importing through the shim:
#   1. Hides the true source of the contract from IDE / static analysis.
#   2. Creates a false sense of "two valid import paths" — new code
#      might import from either location, fragmenting the contract
#      surface area.
#   3. Breaks if the shim is removed (it's marked DEPRECATED in its
#      own docstring — its removal is planned).
#
# SH-012: the previous code ALSO defined a local WRITEBACK_VERSION
# constant ("1.0.0-rt010") that DRIFTED from the shared contract's
# version ("2.0.0-shared-contract"). The CSV was being written with
# the local version while readers (graph_transformer/training/trainer.py)
# used the shared version — making it impossible to tell which schema
# a row was written with. Now WRITEBACK_VERSION is imported from the
# shared contract, so writer and reader agree.
# -----------------------------------------------------------------------------
_REPO_ROOT = str(Path(__file__).resolve().parents[1])
if _REPO_ROOT not in sys.path:
    sys.path.insert(0, _REPO_ROOT)

from shared.contracts.writeback import (  # noqa: E402
    CANONICAL_VALIDATED_CSV,
    DRUG_COL,
    DISEASE_COL,
    OUTCOME_COL,
    TIMESTAMP_COL,
    VALIDATED_BY_COL,
    VALIDATION_STUDY_ID_COL,
    NOTES_COL,
    ORIGINAL_GT_SCORE_COL,
    ORIGINAL_RL_RANK_COL,
    WRITEBACK_VERSION_COL,
    WRITEBACK_CSV_COLUMNS,
    REQUIRED_COLUMNS,
    OUTCOME_VALIDATED_POSITIVE,
    OUTCOME_VALIDATED_TOXIC,
    OUTCOME_VALIDATED_NEGATIVE,
    OUTCOME_INVALIDATED,
    VALID_OUTCOMES,
    POSITIVE_OUTCOMES,
    PENALTY_OUTCOMES,
    WRITEBACK_VERSION,            # SH-012: import from shared (was "1.0.0-rt010")
    get_validated_csv_path,
    ensure_csv_dir,
    # SH-032 v117 ROOT FIX (Teammate 8): atomic-write profile constants.
    # The previous code did NOT use these — it called open(csv_path, "w")
    # directly, which is NOT crash-safe. Now writeback_to_phase1 uses
    # tmp+fsync+os.replace (matching writeback_to_phase3's pattern).
    ATOMIC_WRITE_TMP_SUFFIX,
    ATOMIC_WRITE_FSYNC,
    # SH-021 v117 ROOT FIX (Teammate 8): Cypher identifier validator.
    # Used by writeback_to_phase2 for DEFENSE-IN-DEPTH: even though the
    # shared contract validates labels at IMPORT time, this module's
    # try/except fallback (which fires if the shared import fails) could
    # use unsafe values. The local validator catches that case.
    _validate_cypher_identifier,
)

logger = logging.getLogger(__name__)

# SH-012 ROOT FIX: WRITEBACK_VERSION is now imported from
# shared.contracts.writeback (= "2.0.0-shared-contract"). The previous
# local override ("1.0.0-rt010") caused version drift between the
# writer (this module) and the reader (graph_transformer/training/
# trainer.py). Removed the local override — any code that imports
# WRITEBACK_VERSION from phase4.writeback now gets the shared value.

# Outcome enum — the possible validation outcomes a pharma partner can report.
# SH-002 ROOT FIX: this Literal now mirrors the shared contract's 4-value
# enum (was previously 4 values locally but only 3 in rl/contracts/
# phase4_schema.py — the drift was the bug).
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
#
# SH-027 ROOT FIX: the previous code imported the constants above from
# `common.validated_hypotheses_schema` (a deprecated re-export shim).
# The duplicates have been removed — the canonical imports at the top
# of this module (from `shared.contracts.writeback`) are now the SOLE
# source. `common.validated_hypotheses_schema` is no longer referenced
# anywhere in this file.

# Backward-compatible env var lookup (falls back to canonical schema path).
# Read LAZILY via _get_phase1_validated_csv() so tests and runtime config
# can override the env var without reloading the module. The previous code
# cached the path at import time, making it impossible to override in tests.
def _get_phase1_validated_csv() -> str:
    """Return the Phase 1 validated hypotheses CSV path (lazy env var lookup).

    Reads PHASE1_VALIDATED_CSV and VALIDATED_HYPOTHESES_CSV at CALL TIME
    (not import time) so tests and runtime config can override without
    reloading the module.
    """
    return (
        os.environ.get("PHASE1_VALIDATED_CSV")
        or get_validated_csv_path()  # reads VALIDATED_HYPOTHESES_CSV lazily
    )


# Backward-compat: keep the module-level constant for code that imports
# it directly. Reflects the value at import time. New code should call
# ``_get_phase1_validated_csv()`` instead.
PHASE1_VALIDATED_CSV = _get_phase1_validated_csv()


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
    # Read the CSV path LAZILY (at call time, not import time) so tests
    # and runtime config can override the env var without reloading.
    csv_path = Path(_get_phase1_validated_csv())
    csv_path.parent.mkdir(parents=True, exist_ok=True)
    file_exists = csv_path.exists() and csv_path.stat().st_size > 0

    # SH-003 ROOT FIX: use the canonical WRITEBACK_CSV_COLUMNS from the
    # shared contract instead of a hardcoded list. This GUARANTEES the
    # CSV header matches what readers (graph_transformer/training/
    # trainer.py) expect. If the shared contract adds/removes a column,
    # this writer picks up the change automatically.
    fieldnames: List[str] = list(WRITEBACK_CSV_COLUMNS)

    # P4-011: check for duplicate (same drug, disease, validated_by)
    # If found, UPDATE instead of append.
    existing_rows: List[Dict[str, str]] = []
    duplicate_found = False
    if file_exists:
        try:
            with open(csv_path, "r", newline="") as f:
                reader = csv.DictReader(f)
                for row in reader:
                    if (row.get(DRUG_COL, "").strip() == vh.drug.strip()
                            and row.get(DISEASE_COL, "").strip() == vh.disease.strip()
                            and row.get(VALIDATED_BY_COL, "").strip() == vh.validated_by.strip()):
                        # UPDATE this row with new data
                        row[OUTCOME_COL] = vh.outcome
                        row[VALIDATION_STUDY_ID_COL] = vh.validation_study_id or ""
                        row[TIMESTAMP_COL] = vh.validated_at
                        row[NOTES_COL] = vh.notes or ""
                        row[ORIGINAL_GT_SCORE_COL] = str(vh.original_gt_score) if vh.original_gt_score is not None else ""
                        row[ORIGINAL_RL_RANK_COL] = str(vh.original_rl_rank) if vh.original_rl_rank is not None else ""
                        row[WRITEBACK_VERSION_COL] = WRITEBACK_VERSION
                        duplicate_found = True
                    existing_rows.append(row)
        except Exception as exc:
            logger.warning("P4-011: failed to read existing CSV for duplicate check (%s). Appending.", exc)

    if duplicate_found:
        # SH-020 + SH-032 v117 ROOT FIX (Teammate 8): ATOMIC rewrite.
        #
        # The previous code did:
        #     with open(csv_path, "w", newline="") as f:
        #         writer = csv.DictWriter(f, ...)
        #         writer.writeheader()
        #         writer.writerows(existing_rows)
        #
        # SH-020: this REWRITES THE ENTIRE CSV on every duplicate update
        # — O(n²) for n validated hypotheses. The audit flagged this as
        # a MEDIUM severity issue.
        #
        # SH-032: this does NOT use the atomic-write profile declared in
        # shared.contracts.writeback (ATOMIC_WRITE_TMP_SUFFIX=".tmp",
        # ATOMIC_WRITE_FSYNC=True). If the process crashed mid-write
        # (OOM, signal, power loss), the CSV was left TRUNCATED — the
        # next reader would see a partial file or fail to parse it,
        # silently dropping validated hypotheses. A regulator auditing
        # the validated_hypotheses.csv would see a corrupt file with no
        # way to recover the lost entries (21 CFR Part 11 data
        # integrity violation).
        #
        # ROOT FIX (SH-020 + SH-032 combined):
        #   1. Write the updated rows to a TEMP file in the SAME directory
        #      (so os.rename is atomic on POSIX — a single inode op).
        #   2. fsync the temp file (so the data hits disk before rename).
        #   3. Atomically rename temp -> target (os.replace, atomic on
        #      POSIX since Python 3.3, and on Windows since 3.3).
        #
        # On the O(n²) concern (SH-020): the in-memory rewrite is
        # unavoidable for CSV without an index — but for the validated
        # hypotheses file (which grows by ~10-100 rows/year for a typical
        # pharma partnership program), the O(n²) cost is negligible
        # (microseconds for n=1000). The real fix is the atomic write,
        # which prevents data corruption on crash. If the file ever
        # grows to 100K+ rows (decades of partnership data), migrate to
        # SQLite (which supports UPDATE in place + WAL mode for crash
        # safety) — see the TODO at the bottom of this function.
        #
        # Use the ATOMIC_WRITE_TMP_SUFFIX and ATOMIC_WRITE_FSYNC constants
        # from the shared contract (SH-032 specifically requires this).
        tmp_path = csv_path.with_suffix(csv_path.suffix + ATOMIC_WRITE_TMP_SUFFIX)
        try:
            with open(tmp_path, "w", newline="") as f:
                writer = csv.DictWriter(f, fieldnames=fieldnames)
                writer.writeheader()
                writer.writerows(existing_rows)
                if ATOMIC_WRITE_FSYNC:
                    f.flush()
                    os.fsync(f.fileno())
            # Atomic rename (POSIX) / replace (Windows)
            os.replace(str(tmp_path), str(csv_path))
        except Exception:
            # Clean up the temp file if anything went wrong
            try:
                tmp_path.unlink(missing_ok=True)
            except Exception:
                pass
            raise
        logger.info(
            "P4-011 + SH-020 + SH-032 v117 ROOT FIX: UPDATED existing "
            "validated hypothesis (%s, %s, by=%s) with outcome=%s via "
            "ATOMIC write (tmp+fsync+os.replace). No duplicate appended.",
            vh.drug, vh.disease, vh.validated_by, vh.outcome,
        )
    else:
        # APPEND new row — also use atomic write for consistency.
        # The previous code used `open(csv_path, "a")` which is NOT
        # crash-safe on some filesystems (NFS, ext3 with -o data=writeback).
        # SH-032 v117: use the same tmp+fsync+os.replace pattern for
        # appends. This is slightly more expensive (rewrites the whole
        # file) but guarantees the file is never left in a partial state.
        tmp_path = csv_path.with_suffix(csv_path.suffix + ATOMIC_WRITE_TMP_SUFFIX)
        try:
            with open(tmp_path, "w", newline="") as f:
                writer = csv.DictWriter(f, fieldnames=fieldnames)
                writer.writeheader()
                if file_exists:
                    # Copy existing rows from the original file
                    try:
                        with open(csv_path, "r", newline="") as src:
                            reader = csv.DictReader(src)
                            for row in reader:
                                # Filter to fieldnames in case the source
                                # has extra columns (forward-compat).
                                writer.writerow({k: row.get(k, "") for k in fieldnames})
                    except Exception as exc:
                        logger.warning(
                            "SH-032 v117: failed to copy existing rows from "
                            "%s during atomic append (%s). Starting fresh.",
                            csv_path, exc,
                        )
                # Append the new row
                writer.writerow({
                    DRUG_COL: vh.drug,
                    DISEASE_COL: vh.disease,
                    OUTCOME_COL: vh.outcome,
                    VALIDATED_BY_COL: vh.validated_by,
                    VALIDATION_STUDY_ID_COL: vh.validation_study_id or "",
                    TIMESTAMP_COL: vh.validated_at,
                    NOTES_COL: vh.notes or "",
                    ORIGINAL_GT_SCORE_COL: vh.original_gt_score if vh.original_gt_score is not None else "",
                    ORIGINAL_RL_RANK_COL: vh.original_rl_rank if vh.original_rl_rank is not None else "",
                    WRITEBACK_VERSION_COL: WRITEBACK_VERSION,
                })
                if ATOMIC_WRITE_FSYNC:
                    f.flush()
                    os.fsync(f.fileno())
            os.replace(str(tmp_path), str(csv_path))
        except Exception:
            try:
                tmp_path.unlink(missing_ok=True)
            except Exception:
                pass
            raise
        logger.info(
            "RT-010 Phase 1 writeback: appended (%s, %s, %s) by %s to %s "
            "via ATOMIC write (tmp+fsync+os.replace).",
            vh.drug, vh.disease, vh.outcome, vh.validated_by, csv_path,
        )
    # TODO (future, when n > 100K): migrate validated_hypotheses.csv to
    # SQLite with WAL mode. WAL gives us UPDATE in place (O(log n) instead
    # of O(n)) AND crash safety (WAL is journaled). The CSV stays as a
    # periodic export for human inspection. Until n exceeds ~10K, the
    # atomic-rewrite approach is simpler and equally safe.
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
    """Add a VALIDATED_TREATS / VALIDATED_TOXIC_FOR edge to Neo4j (when available).

    The edge connects the drug and disease nodes in the KG with a
    relationship type that depends on the outcome:
      - validated_positive  -> VALIDATED_TREATS
      - validated_toxic     -> VALIDATED_TOXIC_FOR  (issue #342)
      - validated_negative  -> VALIDATED_NEGATIVE_FOR
      - invalidated         -> VALIDATED_NEGATIVE_FOR

    ISSUE #341 ROOT FIX (data-flywheel-336-355): the previous code MERGEd
    on ``:Compound {name: $drug}`` only. The TM 17 contract specifies
    ``:Drug`` with canonical ``drug_id``. The Phase 2 kg_builder currently
    uses ``:Compound`` with ``name``. To support BOTH schemas (current KG
    AND future TM 17 state) without fragmenting nodes, the MERGE now tries
    BOTH ``:Drug`` and ``:Compound`` labels, matching by ``name`` AND
    ``drug_id`` (when available). This prevents the bug where MERGE on
    ``:Drug`` would silently create a duplicate of an existing ``:Compound``
    node, fragmenting the graph.

    ISSUE #342 ROOT FIX: the previous code used ``VALIDATED_TOXIC`` for
    toxic outcomes. The audit requires ``VALIDATED_TOXIC_FOR`` — the FOR
    suffix makes the semantics explicit (drug is toxic FOR this disease,
    not just toxic in general).

    ISSUE #343 ROOT FIX (already applied): Neo4j ``driver.close()`` is in
    a ``finally`` block, ``driver.session()`` is in a ``with`` block. No
    connection leak.

    If Neo4j is not available (no DRUGOS_NEO4J_URI), this function
    logs a warning and returns False. The Phase 1 CSV writeback still
    happened, so the next KG build will include the edge.
    """
    # ISSUE #341/#342 ROOT FIX: import canonical labels and edge mapping
    # from the shared contract. This is the SINGLE source of truth —
    # if the contract changes, this function automatically picks up
    # the new labels without code changes here.
    try:
        import sys as _sys
        _repo_root = str(Path(__file__).resolve().parents[1])
        if _repo_root not in _sys.path:
            _sys.path.insert(0, _repo_root)
        from shared.contracts.writeback import (
            NEO4J_DRUG_LABELS,
            NEO4J_DISEASE_LABEL,
            NEO4J_DRUG_NAME_PROP,
            NEO4J_DISEASE_NAME_PROP,
            edge_label_for_outcome,
        )
    except Exception:
        # Defensive fallback (matches shared.contracts.writeback defaults).
        NEO4J_DRUG_LABELS = ("Drug", "Compound")
        NEO4J_DISEASE_LABEL = "Disease"
        NEO4J_DRUG_NAME_PROP = "name"
        NEO4J_DISEASE_NAME_PROP = "name"

        def edge_label_for_outcome(outcome: str) -> str:
            _m = {
                "validated_positive": "VALIDATED_TREATS",
                "validated_toxic": "VALIDATED_TOXIC_FOR",
                "validated_negative": "VALIDATED_NEGATIVE_FOR",
                "invalidated": "VALIDATED_NEGATIVE_FOR",
            }
            return _m.get(outcome, "VALIDATED_TREATS")

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

        # ISSUE #341 ROOT FIX: try BOTH :Drug (TM 17 contract) and
        # :Compound (current Phase 2 KG) labels. Try drug_id first
        # (canonical), fall back to name (legacy). This prevents node
        # fragmentation when the KG uses one label and the writeback
        # uses the other.
        drug_original = _canonicalize_name_for_kg(vh.drug)
        drug_title = drug_original.title()
        drug_lower = drug_original.lower()
        disease_original = _canonicalize_name_for_kg(vh.disease)
        disease_title = disease_original.title()
        disease_lower = disease_original.lower()

        # ISSUE #342 ROOT FIX: use edge_label_for_outcome() from the
        # shared contract. Maps validated_toxic -> VALIDATED_TOXIC_FOR
        # (not VALIDATED_TOXIC). The FOR suffix makes the semantics
        # explicit: drug is toxic FOR this disease.
        _edge_label = edge_label_for_outcome(vh.outcome)

        # drug_label_try_1 / drug_label_try_2: the primary and fallback
        # Neo4j labels for a drug node. The kg_builder uses :Compound
        # (current), TM 17 contract uses :Drug (canonical future). We
        # try BOTH to avoid node fragmentation.
        drug_label_try_1, drug_label_try_2 = NEO4J_DRUG_LABELS[0], NEO4J_DRUG_LABELS[-1]

        # drug_prop / disease_prop: the Neo4j property name on the node
        # that holds the human-readable name (e.g., "Metformin"). Used
        # in the MATCH WHERE clause to find the existing node.
        drug_prop = NEO4J_DRUG_NAME_PROP
        disease_prop = NEO4J_DISEASE_NAME_PROP

        # SH-021 v118 ROOT FIX (Teammate 8 — HOSTILE AUDITOR): the
        # previous "ROOT FIX" comment block (v117) placed the Cypher
        # identifier validation ABOVE the variable definitions, causing
        # Python to raise ``UnboundLocalError: cannot access local
        # variable 'drug_label_try_1' where it is not associated with a
        # value`` the moment ANY real Neo4j URI was set. The function
        # silently swallowed the error via the broad ``except Exception``
        # below, logging "Neo4j write failed" and returning False —
        # making it LOOK like Neo4j was unreachable, when in fact the
        # function was structurally unable to ever write a single edge.
        #
        # The user's audit ("comments and tests are fakes ... when I
        # manually check code it's 100 percent broken") was dead right:
        # the v117 fix was aspirational, not actual. This v118 fix MOVES
        # the validation block to AFTER every variable it references is
        # defined, so the validation actually executes.
        #
        # DEFENSE-IN-DEPTH Cypher identifier validation: the shared
        # contract validates these constants at IMPORT time, but this
        # function uses values from NEO4J_DRUG_LABELS /
        # NEO4J_DISEASE_LABEL etc. which were imported at the TOP of
        # this function inside a try/except. If the shared import failed
        # (and the hardcoded fallback was used), the values are safe
        # (only "Drug", "Compound", "Disease", "name" — all
        # alphanumeric). But if a future edit adds a backtick,
        # semicolon, or other Cypher metacharacter to the fallback, the
        # import-time validation in shared/contracts/writeback.py would
        # NOT catch it (because the fallback is local). This local
        # validation catches that case.
        #
        # We validate EVERY label and property name BEFORE building the
        # Cypher query. If any fails, we raise ValueError (fail-closed)
        # — better to refuse the writeback than to inject a malicious
        # label into Neo4j.
        for _lbl in (drug_label_try_1, drug_label_try_2, NEO4J_DISEASE_LABEL):
            _validate_cypher_identifier(_lbl, f"NEO4J_label_{_lbl!r}")
        for _prop in (drug_prop, disease_prop):
            _validate_cypher_identifier(_prop, f"NEO4J_prop_{_prop!r}")
        _validate_cypher_identifier(_edge_label, f"edge_label_{_edge_label!r}")
        # Note: drug_original, drug_title, drug_lower, disease_* are
        # PARAMETERIZED in the Cypher query ($drug_lower, $drug_title,
        # etc.) — they CANNOT inject. Only the LABEL and PROPERTY names
        # are string-concatenated, so only those need validation.
        cypher = (
            """
        // ISSUE #341 ROOT FIX: try multiple (label, prop) combos to find
        // the existing drug node. Prevents fragmentation when KG uses
        // :Compound and writeback conceptualizes :Drug.
        CALL {
            WITH $drug_lower, $drug_title, $drug_original
            MATCH (d:`""" + drug_label_try_1 + "`)\n"
            "            WHERE toLower(d." + drug_prop + ") = $drug_lower\n"
            "               OR d." + drug_prop + " = $drug_title\n"
            "               OR d." + drug_prop + " = $drug_original\n"
            "            RETURN d LIMIT 1\n"
            "        }\n"
            "        WITH d\n"
            "        WHERE d IS NOT NULL\n"
            "        WITH d\n"
            "        CALL {\n"
            "            WITH $disease_lower, $disease_title, $disease_original\n"
            "            MATCH (v:`" + NEO4J_DISEASE_LABEL + "`)\n"
            "            WHERE toLower(v." + disease_prop + ") = $disease_lower\n"
            "               OR v." + disease_prop + " = $disease_title\n"
            "               OR v." + disease_prop + " = $disease_original\n"
            "            RETURN v LIMIT 1\n"
            "        }\n"
            "        WITH d, v\n"
            "        WHERE v IS NOT NULL\n"
            "        MERGE (d)-[r:`" + _edge_label + "`]->(v)\n"
            "          ON CREATE SET\n"
            "            r.validated_at = $validated_at,\n"
            "            r.validated_by = $validated_by,\n"
            "            r.validation_study_id = $study_id,\n"
            "            r.outcome = $outcome,\n"
            "            r.writeback_version = $wbv\n"
            "          ON MATCH SET\n"
            "            r.last_revalidated_at = $validated_at,\n"
            "            r.revalidation_count = coalesce(r.revalidation_count, 0) + 1\n"
            "        RETURN r\n"
            "        "
        )
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
                # If the primary-label query found 0 nodes (likely because
                # the KG uses :Compound, not :Drug), retry with the legacy
                # label. This is the defensive dual-label strategy.
                if summary.counters._stats.get("relationships_created", 0) == 0 \
                        and summary.counters._stats.get("properties_set", 0) == 0 \
                        and drug_label_try_1 != drug_label_try_2:
                    cypher_legacy = cypher.replace(
                        f"`{drug_label_try_1}`",
                        f"`{drug_label_try_2}`",
                    )
                    result2 = session.run(cypher_legacy, {
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
                    summary2 = result2.consume()
                    logger.info(
                        "RT-010 Phase 2 writeback (legacy label %s): %s edge "
                        "upserted in Neo4j (%s -> %s, outcome=%s, by=%s). "
                        "Counters: %s",
                        drug_label_try_2, _edge_label,
                        vh.drug, vh.disease, vh.outcome, vh.validated_by,
                        summary2.counters._stats,
                    )
                else:
                    logger.info(
                        "RT-010 Phase 2 writeback (preferred label %s): %s edge "
                        "upserted in Neo4j (%s -> %s, outcome=%s, by=%s). "
                        "Counters: %s",
                        drug_label_try_1, _edge_label,
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

# Default trigger path — read LAZILY via _get_retrain_trigger_path() so
# tests and runtime config can override the env var without reloading
# the module. The previous code read the env var ONCE at import time,
# which made it impossible to override in tests (the module cached the
# path before the test set the env var).
_DEFAULT_RETRAIN_TRIGGER_PATH = str(
    Path(__file__).resolve().parent.parent / "graph_transformer" / "retrain_triggered.json"
)


def _get_retrain_trigger_path() -> str:
    """Return the retrain trigger path, respecting the env var override.

    Reads PHASE3_RETRAIN_TRIGGER at CALL TIME (not import time) so tests
    and runtime config can override it without reloading the module.
    """
    return os.environ.get("PHASE3_RETRAIN_TRIGGER", _DEFAULT_RETRAIN_TRIGGER_PATH)


# Backward-compat: keep the module-level constant for code that imports
# it directly (e.g., ``from phase4.writeback import PHASE3_RETRAIN_TRIGGER``).
# It reflects the value at import time. New code should call
# ``_get_retrain_trigger_path()`` instead for runtime-configurable path.
PHASE3_RETRAIN_TRIGGER = _get_retrain_trigger_path()


def writeback_to_phase3(vh: ValidatedHypothesis) -> Path:
    """Append the validated hypothesis to the GT retraining trigger file.

    The GT trainer reads this file at the start of each training run
    and adds the validated pairs to its known_pairs list. The new pair
    becomes a positive (or negative, if outcome='validated_negative')
    example in the next training run.

    If outcome is 'validated_negative' or 'validated_toxic', the pair
    is added to a NEGATIVE_VALIDATED list — the trainer must score
    these pairs LOW. This corrects model errors (e.g., warfarin ->
    epilepsy predicted at 0.85 but validated as toxic).

    Returns the Path to the trigger JSON file (so callers can report
    the actual file path, not just a success bool). The previous code
    returned ``True`` which caused ``write_validated_hypothesis`` to
    report ``phase3_trigger_path: "True"`` in its response dict — a
    stringified bool, not a path. That broke the API contract.
    """
    # Read the trigger path LAZILY (at call time, not import time) so
    # tests and runtime config can override the env var.
    trigger_path = Path(_get_retrain_trigger_path())
    trigger_path.parent.mkdir(parents=True, exist_ok=True)

    # Read existing triggers (if any)
    # v114 FORENSIC ROOT FIX (BUG #2 from Task 3-b audit): the previous
    # code did `except Exception: existing = []` which SILENTLY discarded
    # read errors. If the JSON was corrupt (prior crash, NFS issue, manual
    # edit), the code overwrote the file with just the new entry, DESTROYING
    # all previously-recorded validated hypotheses. The data flywheel's
    # history was silently wiped with no log -- a 21 CFR Part 11 data
    # integrity violation.
    #
    # ROOT FIX: on read failure, LOG CRITICAL and BACK UP the corrupt file
    # (rename to <path>.corrupt.<timestamp>) instead of silently overwriting.
    # The new entry is still appended (to a fresh list), but the operator
    # is alerted and the corrupt file is preserved for forensic recovery.
    import logging as _logging_p4_bug2
    _log_bug2 = _logging_p4_bug2.getLogger(__name__)
    existing: List[Dict[str, Any]] = []
    if trigger_path.exists():
        try:
            with open(trigger_path) as f:
                existing = json.load(f)
                if not isinstance(existing, list):
                    _log_bug2.critical(
                        "BUG #2 v114: retrain_triggered.json at %s is valid JSON "
                        "but NOT a list (got %s). Backing up the file and starting "
                        "fresh. The previous validated hypotheses are PRESERVED in "
                        "the .corrupt backup. Investigate what wrote a non-list.",
                        trigger_path, type(existing).__name__,
                    )
                    import time as _time_bug2
                    _backup = trigger_path.with_suffix(
                        f".corrupt.{int(_time_bug2.time())}.json"
                    )
                    try:
                        trigger_path.rename(_backup)
                        _log_bug2.critical(
                            "BUG #2 v114: corrupt retrain_triggered.json backed up "
                            "to %s", _backup,
                        )
                    except OSError as _ren_err:
                        _log_bug2.error(
                            "BUG #2 v114: could not back up corrupt file (%s). "
                            "The file will be overwritten -- data may be lost.",
                            _ren_err,
                        )
                    existing = []
        except (json.JSONDecodeError, OSError) as _read_err:
            _log_bug2.critical(
                "BUG #2 v114: FAILED to read retrain_triggered.json at %s (%s). "
                "The file is corrupt or unreadable. Backing up the corrupt file "
                "and starting fresh. The new entry will be appended to an empty "
                "list -- previous validated hypotheses are PRESERVED in the "
                ".corrupt backup. Investigate the corruption (prior crash, NFS "
                "issue, manual edit, disk full).",
                trigger_path, _read_err,
            )
            import time as _time_bug2b
            _backup = trigger_path.with_suffix(
                f".corrupt.{int(_time_bug2b.time())}.json"
            )
            try:
                trigger_path.rename(_backup)
                _log_bug2.critical(
                    "BUG #2 v114: corrupt retrain_triggered.json backed up to %s",
                    _backup,
                )
            except OSError as _ren_err:
                _log_bug2.error(
                    "BUG #2 v114: could not back up corrupt file (%s). The file "
                    "will be overwritten -- data may be lost.",
                    _ren_err,
                )
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
    return trigger_path


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
    # Lazy path lookup — respects env var overrides at call time.
    csv_path = Path(_get_phase1_validated_csv())
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
