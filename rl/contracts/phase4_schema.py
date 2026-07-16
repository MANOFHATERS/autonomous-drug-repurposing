"""Canonical Phase 4 output schema — the SINGLE source of truth.

TASK 325 ROOT FIX (forensic, root-level):
  Phase 4's final output is a ``validated_hypotheses.csv`` file. This file
  is the WRITEBACK artifact that closes the data flywheel (DOCX §10):
  validated drug-disease predictions are fed back to the Phase 3 trainer
  as new labeled data points, the model retrains, predictions improve,
  more pharma partners validate, repeat.

  Previously the CSV schema was defined INLINE in
  ``rl/rl_drug_ranker.py``'s ``OUTPUT_SCHEMA`` dict — Phase 3's trainer
  had to reverse-engineer the schema from the writer's source code. When
  Phase 4 renamed a column (e.g. ``validated`` -> ``outcome``), Phase 3
  silently broke until someone noticed missing training rows.

  This module extracts the CSV schema into a CONTRACT that both sides
  import. Any change to the schema is a compile-time error on both
  sides — the contract consistency test (Task 330) verifies the writer's
  OUTPUT_SCHEMA matches this contract.

CSV schema (validated_hypotheses.csv)
-------------------------------------
Columns (in canonical order):

    drug_id           str   REQUIRED  Phase 1 canonical drug ID (InChIKey or DBxxxxx)
    disease_id        str   REQUIRED  Phase 1 canonical disease ID (DOID/MESH/CUI)
    drug_name         str   REQUIRED  Human-readable drug name (for display)
    disease_name      str   REQUIRED  Human-readable disease name (for display)
    score             float REQUIRED  RL composite score [0, 1]
    outcome           str   REQUIRED  One of: validated_positive | validated_toxic | validated_inconclusive
    validated_by      str   REQUIRED  Validator ID (e.g. "wet_lab:partner_A", "literature:pubmed")
    validated_at      str   REQUIRED  ISO 8601 timestamp of validation
    notes             str   OPTIONAL  Free-text notes from the validator

The ``outcome`` column drives the Phase 3 retraining label:
  - validated_positive     -> positive label (drug DOES treat disease)
  - validated_toxic        -> negative label with safety weight (drug is harmful)
  - validated_inconclusive -> excluded from retraining (no signal)
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Tuple


# =============================================================================
# Canonical filename
# =============================================================================
VALIDATED_HYPOTHESES_FILENAME: str = "validated_hypotheses.csv"

# =============================================================================
# Outcome enum — the EXACT set of allowed values for the ``outcome`` column
# =============================================================================
# These values are referenced by Phase 3's trainer to decide how to use
# each row in retraining. Renaming any of them silently breaks retraining.
OUTCOME_POSITIVE: str = "validated_positive"
OUTCOME_TOXIC: str = "validated_toxic"
OUTCOME_INCONCLUSIVE: str = "validated_inconclusive"

OUTCOME_VALUES: Tuple[str, ...] = (
    OUTCOME_POSITIVE,
    OUTCOME_TOXIC,
    OUTCOME_INCONCLUSIVE,
)

# Outcome -> human-readable label (for display in the frontend).
OUTCOME_TO_LABEL: Dict[str, str] = {
    OUTCOME_POSITIVE: "Validated Positive",
    OUTCOME_TOXIC: "Validated Toxic",
    OUTCOME_INCONCLUSIVE: "Validated Inconclusive",
}

# Reverse lookup.
LABEL_TO_OUTCOME: Dict[str, str] = {v: k for k, v in OUTCOME_TO_LABEL.items()}

# =============================================================================
# Validated-by enum — the allowed values for the ``validated_by`` column
# =============================================================================
# The validator ID encodes WHO validated the hypothesis and HOW. The
# prefix determines the trust level (wet_lab > clinical_study > literature).
VALIDATED_BY_VALUES: Tuple[str, ...] = (
    "wet_lab",          # in-vitro / in-vivo wet-lab validation
    "clinical_study",   # human clinical trial
    "literature",       # published paper support
    "expert_review",    # domain-expert manual review
    "automated",        # automated pipeline (e.g. literature search)
)


# =============================================================================
# ColumnSpec — typed specification for one validated_hypotheses column
# =============================================================================


@dataclass(frozen=True)
class ColumnSpec:
    """Specification for a single validated_hypotheses column.

    Attributes
    ----------
    name : str
        Canonical column name (case-sensitive, exact match required).
    dtype : str
        Pandas dtype hint: ``"string"``, ``"float64"``, ``"int64"``,
        ``"bool"``, ``"object"``.
    nullable : bool
        True if the column may contain NULL/NaN values.
    description : str
        Human-readable description.
    """

    name: str
    dtype: str = "string"
    nullable: bool = False
    description: str = ""


# =============================================================================
# REQUIRED columns — must be present and non-null in every row
# =============================================================================
VALIDATED_HYPOTHESES_REQUIRED_COLUMNS: Tuple[ColumnSpec, ...] = (
    ColumnSpec("drug_id", "string", nullable=False,
               description="Phase 1 canonical drug ID (InChIKey or DBxxxxx)."),
    ColumnSpec("disease_id", "string", nullable=False,
               description="Phase 1 canonical disease ID (DOID/MESH/CUI)."),
    ColumnSpec("drug_name", "string", nullable=False,
               description="Human-readable drug name (for display)."),
    ColumnSpec("disease_name", "string", nullable=False,
               description="Human-readable disease name (for display)."),
    ColumnSpec("score", "float64", nullable=False,
               description="RL composite score [0, 1]. Higher = better candidate."),
    ColumnSpec("outcome", "string", nullable=False,
               description="One of: validated_positive | validated_toxic | validated_inconclusive."),
    ColumnSpec("validated_by", "string", nullable=False,
               description="Validator ID prefix (wet_lab | clinical_study | literature | expert_review | automated)."),
    ColumnSpec("validated_at", "string", nullable=False,
               description="ISO 8601 timestamp of validation (UTC)."),
)

# =============================================================================
# OPTIONAL columns — may be present, may be null
# =============================================================================
VALIDATED_HYPOTHESES_OPTIONAL_COLUMNS: Tuple[ColumnSpec, ...] = (
    ColumnSpec("notes", "string", nullable=True,
               description="Free-text notes from the validator."),
)

# All columns in canonical order.
VALIDATED_HYPOTHESES_COLUMNS: Tuple[ColumnSpec, ...] = (
    *VALIDATED_HYPOTHESES_REQUIRED_COLUMNS,
    *VALIDATED_HYPOTHESES_OPTIONAL_COLUMNS,
)

# Flat list of column names (for pandas usecols / dtype construction).
VALIDATED_HYPOTHESES_COLUMN_NAMES: Tuple[str, ...] = tuple(
    c.name for c in VALIDATED_HYPOTHESES_COLUMNS
)

# Dtype dict for pandas.read_csv(dtype=...).
VALIDATED_HYPOTHESES_DTYPES: Dict[str, str] = {
    c.name: c.dtype for c in VALIDATED_HYPOTHESES_COLUMNS
}


# =============================================================================
# Validators
# =============================================================================


def is_valid_outcome(value: str) -> bool:
    """Return True if ``value`` is a valid outcome enum value."""
    return value in OUTCOME_VALUES


def is_validated_by(value: str) -> bool:
    """Return True if ``value`` is a valid validated_by prefix.

    Accepts both bare prefixes (``"wet_lab"``) and qualified IDs
    (``"wet_lab:partner_A"``) — the prefix before the first colon is
    what matters.
    """
    if not isinstance(value, str):
        return False
    prefix = value.split(":", 1)[0]
    return prefix in VALIDATED_BY_VALUES


def validate_validated_hypotheses_row(row: Dict[str, Any]) -> List[str]:
    """Validate a single validated_hypotheses row (as a dict).

    Returns a list of error messages. Empty list = valid row.
    """
    errors: List[str] = []

    # Check 1: all required columns present and non-null.
    for col in VALIDATED_HYPOTHESES_REQUIRED_COLUMNS:
        if col.name not in row:
            errors.append(f"Missing required column {col.name!r}.")
        elif row[col.name] is None:
            errors.append(f"Required column {col.name!r} is null.")
        elif isinstance(row[col.name], str) and not row[col.name].strip():
            errors.append(f"Required column {col.name!r} is empty.")

    # Check 2: outcome is a valid enum value.
    outcome = row.get("outcome")
    if outcome is not None and not is_valid_outcome(outcome):
        errors.append(
            f"outcome {outcome!r} is not valid. "
            f"Must be one of: {list(OUTCOME_VALUES)}."
        )

    # Check 3: validated_by is a valid prefix.
    vb = row.get("validated_by")
    if vb is not None and not is_validated_by(vb):
        errors.append(
            f"validated_by {vb!r} is not valid. "
            f"Prefix must be one of: {list(VALIDATED_BY_VALUES)}."
        )

    # Check 4: score is in [0, 1].
    score = row.get("score")
    if score is not None:
        try:
            score_f = float(score)
            if score_f < 0.0 or score_f > 1.0:
                errors.append(f"score {score_f} is out of range [0, 1].")
        except (TypeError, ValueError):
            errors.append(f"score {score!r} is not a valid float.")

    # Check 5: validated_at is ISO 8601 parseable.
    validated_at = row.get("validated_at")
    if validated_at is not None:
        try:
            from datetime import datetime
            # Try common ISO 8601 formats.
            datetime.fromisoformat(str(validated_at).replace("Z", "+00:00"))
        except (ValueError, TypeError):
            errors.append(
                f"validated_at {validated_at!r} is not a valid ISO 8601 timestamp."
            )

    return errors


def validate_validated_hypotheses_dataframe(df: Any) -> List[str]:
    """Validate a pandas DataFrame of validated_hypotheses rows.

    Returns a list of error messages. Empty list = valid DataFrame.
    """
    errors: List[str] = []

    if df is None:
        return ["DataFrame is None."]
    if not hasattr(df, "columns"):
        return [f"Expected a pandas DataFrame, got {type(df).__name__}."]

    actual_cols = set(df.columns)

    # Check 1: all required columns present.
    for col in VALIDATED_HYPOTHESES_REQUIRED_COLUMNS:
        if col.name not in actual_cols:
            errors.append(f"Missing required column {col.name!r}.")

    # Check 2: no unknown columns (strict).
    known_cols = set(VALIDATED_HYPOTHESES_COLUMN_NAMES)
    for col in actual_cols:
        if col not in known_cols:
            errors.append(
                f"WARNING: Unknown column {col!r}. "
                f"Known columns: {list(VALIDATED_HYPOTHESES_COLUMN_NAMES)}."
            )

    # Check 3: outcome column only contains valid enum values.
    if "outcome" in actual_cols:
        invalid = df.loc[df["outcome"].notna() & ~df["outcome"].isin(OUTCOME_VALUES), "outcome"]
        if len(invalid) > 0:
            unique_invalid = invalid.unique().tolist()
            errors.append(
                f"outcome column contains {len(invalid)} rows with invalid values: "
                f"{unique_invalid}. Must be one of: {list(OUTCOME_VALUES)}."
            )

    # Check 4: validated_by column only contains valid prefixes.
    if "validated_by" in actual_cols:
        invalid_mask = df["validated_by"].notna() & ~df["validated_by"].apply(
            lambda x: is_validated_by(x) if isinstance(x, str) else False
        )
        invalid_count = int(invalid_mask.sum())
        if invalid_count > 0:
            unique_invalid = df.loc[invalid_mask, "validated_by"].unique().tolist()
            errors.append(
                f"validated_by column contains {invalid_count} rows with invalid "
                f"prefixes: {unique_invalid}. Prefix must be one of: "
                f"{list(VALIDATED_BY_VALUES)}."
            )

    # Check 5: score column in [0, 1].
    if "score" in actual_cols:
        scores = df["score"].dropna()
        if len(scores) > 0:
            try:
                scores_f = scores.astype(float)
                bad = scores_f[(scores_f < 0.0) | (scores_f > 1.0)]
                if len(bad) > 0:
                    errors.append(
                        f"score column contains {len(bad)} rows outside [0, 1]. "
                        f"Min={scores_f.min()}, Max={scores_f.max()}."
                    )
            except (TypeError, ValueError):
                errors.append("score column contains non-numeric values.")

    return errors
