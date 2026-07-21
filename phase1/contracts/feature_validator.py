"""Phase 1 -> Phase 3 feature completeness validator.

Teammate 2 — P1 to P3 Integration ROOT FIX:

The existing ``phase1.contracts.validate_output.validate_output_dir`` checks
SCHEMA-LEVEL correctness (file exists, columns present, dtypes match, non-
nullable required columns have NO nulls). It does NOT enforce NULL-RATE
thresholds on NULLABLE columns. A pipeline that populates a nullable column
with 90% NULLs (e.g. because the upstream API silently dropped a field) would
pass schema validation but produce silently degraded training data for the
Phase 3 Graph Transformer.

This module closes that gap. It enforces the TARGET STATE contract from the
issue:

  > (3) A contract validator runs after every pipeline and FAILS the run if
  > any required column has >5% NULL rate.

Behaviour
---------
``validate_feature_completeness(processed_dir, schema, max_null_rate)`` walks
every source in ``PHASE1_OUTPUT_SCHEMA`` (default). For each source whose
``min_rows >= 1`` (i.e. the source is REQUIRED — empty file is already a
schema-level failure):

  1. Resolve the CSV file via ``get_all_aliases`` (canonical filename + any
     aliases). If none exist, this is a HARD failure (already flagged by
     ``validate_output_dir`` too — duplicate detection is intentional defense
     in depth).
  2. Read the CSV with pandas (handles gzip transparently).
  3. For EVERY declared column — ``required_columns`` + ``optional_columns``
     + ``any_of_groups`` flattened — that is PRESENT in the CSV:
       * Compute the NULL rate (NaN for numeric columns, NaN/empty-string for
         string columns).
       * If NULL rate > ``max_null_rate`` (default 0.05 = 5%), append a
         descriptive failure.
     Missing columns are NOT flagged here — they are already flagged by
     ``validate_output_dir`` (Check 1). Flagging them here would double-count
     the same defect.
  4. Return ``(passed, failures)`` tuple. ``passed`` is True iff
     ``failures`` is empty.

The validator is INVARIANT to the schema's evolution: when a new column is
added to ``PHASE1_OUTPUT_SCHEMA`` with ``nullable=True``, it is automatically
subject to the NULL-rate threshold on the next pipeline run. No code change
required here.

Why this is the RIGHT design
----------------------------
* Uses the EXISTING ``SourceSpec``/``ColumnSpec`` schema (single source of
  truth). The issue's "EXACT FIX CODE" used bare strings — applying it would
  have created a divergent schema definition. We use the rich schema instead.
* Does NOT replace ``validate_output_dir`` — it COMPLEMENTS it. The DAG runs
  both: schema validation (structure) + feature validation (population).
* Threshold (5% NULL) is configurable per-call so different environments
  (production / staging / dev) can apply different strictness.
* Returns ``(passed, failures)`` tuple (matching the issue's exact contract)
  so the DAG can decide whether to fail the run or just log warnings.
"""
from __future__ import annotations

import logging
from pathlib import Path
from typing import Dict, List, Tuple

import pandas as pd

# Same dual-import pattern as validate_output.py — works both as
# ``phase1.contracts.feature_validator`` (from outside phase1/) and as
# ``contracts.feature_validator`` (from inside phase1/, e.g. DAGs / tests).
try:
    from phase1.contracts.phase1_schema import (
        PHASE1_OUTPUT_SCHEMA,
        SourceSpec,
        get_all_aliases,
    )
except ImportError:  # pragma: no cover -- exercised when imported inside phase1/
    from contracts.phase1_schema import (  # type: ignore[no-redef]
        PHASE1_OUTPUT_SCHEMA,
        SourceSpec,
        get_all_aliases,
    )

logger = logging.getLogger(__name__)


def _resolve_source_csv(spec: SourceSpec, processed_dir: Path) -> Path | None:
    """Return the first existing candidate CSV path for ``spec``, or None.

    Mirrors ``validate_output._resolve_source_file`` but kept private here so
    this module has no runtime dependency on ``validate_output`` (avoids a
    circular import if validate_output ever imports feature_validator).
    """
    for name in get_all_aliases(spec.key):
        candidate = processed_dir / name
        if candidate.exists() and candidate.is_file():
            return candidate
    return None


def _declared_columns(spec: SourceSpec) -> List[str]:
    """Flatten every declared column name for ``spec``.

    Includes required_columns + optional_columns + any_of_groups flattened.
    Duplicates (e.g. a column declared in both required and any_of) are
    de-duplicated while preserving first-seen order for deterministic failure
    messages.
    """
    seen: set[str] = set()
    ordered: list[str] = []
    for col_spec in spec.required_columns:
        if col_spec.name not in seen:
            seen.add(col_spec.name)
            ordered.append(col_spec.name)
    for col_spec in spec.optional_columns:
        if col_spec.name not in seen:
            seen.add(col_spec.name)
            ordered.append(col_spec.name)
    for group in spec.any_of_groups:
        for name in group:
            if name not in seen:
                seen.add(name)
                ordered.append(name)
    return ordered


def _column_null_rate(df: pd.DataFrame, column: str) -> Tuple[float, int]:
    """Return ``(null_rate, total_rows)`` for ``column`` in ``df``.

    For string-dtype columns we treat both NaN AND the empty string ('' or
    whitespace-only) as NULL — a pipeline that writes ``""`` instead of
    leaving the cell empty is still a NULL for downstream purposes. For
    numeric/bool columns we only treat pandas-NaN as NULL.

    Returns ``(0.0, 0)`` if the dataframe has 0 rows (avoids div-by-zero).
    """
    total = len(df)
    if total == 0:
        return (0.0, 0)
    series = df[column]
    null_mask = series.isna()
    # Treat empty/whitespace strings as NULL too. ``series.astype(str)`` is
    # safe for any dtype; we only check string-equality on rows where the
    # value is NOT already NaN (NaN -> 'nan' string by astype, which would
    # be a false negative if we didn't pre-filter).
    try:
        str_repr = series.astype(str).str.strip()
        empty_str_mask = str_repr.isin(("", "nan", "None", "null", "NULL"))
        null_mask = null_mask | empty_str_mask
    except Exception:
        # If astype fails for some weird dtype, fall back to isna() only.
        pass
    null_count = int(null_mask.sum())
    return (null_count / total, total)


def validate_feature_completeness(
    processed_dir: Path,
    schema: Dict[str, SourceSpec] | None = None,
    max_null_rate: float = 0.05,
) -> Tuple[bool, List[str]]:
    """Validate that every declared column has acceptable NULL rate.

    Parameters
    ----------
    processed_dir : Path
        Directory containing the Phase 1 output CSVs.
    schema : Dict[str, SourceSpec], optional
        Schema to validate against. Defaults to ``PHASE1_OUTPUT_SCHEMA``.
        Callers may pass a subset (e.g. only ``pubchem_enrichment``) for
        targeted checks.
    max_null_rate : float, default 0.05
        Maximum NULL rate allowed for any declared column. Columns with a
        higher NULL rate are flagged as failures. Default 5% matches the
        issue's TARGET STATE contract.

    Returns
    -------
    (passed, failures) : Tuple[bool, List[str]]
        ``passed`` is True iff ``failures`` is empty. ``failures`` is a list
        of human-readable strings, one per defect. The list is ordered by
        source key (insertion order) then by column name (alphabetical) so
        the output is deterministic across runs.

    Failure modes covered
    ---------------------
    1. ``Source '<key>': CSV file not found`` — required source's CSV is
       missing entirely. (Already flagged by ``validate_output_dir`` —
       defense in depth.)
    2. ``Source '<key>': could not read CSV: <exc>`` — pandas raised on
       read (corrupt file, encoding issue, etc.).
    3. ``Source '<key>': CSV has 0 rows`` — file exists but is header-only.
    4. ``Source '<key>': column '<col>' is declared but MISSING from CSV``
       — pipeline forgot to emit the column. (Already flagged by
       ``validate_output_dir`` for required columns — defense in depth for
       optional columns.)
    5. ``Source '<key>': column '<col>' has X.X% NULL rate (max allowed:
       Y.Y%)`` — the actual NULL-rate failure mode this validator exists
       to catch.
    """
    if schema is None:
        schema = PHASE1_OUTPUT_SCHEMA
    processed_dir = Path(processed_dir)
    failures: List[str] = []

    for source_key, spec in schema.items():
        # Optional sources (min_rows == 0) are not subject to NULL-rate
        # checks — if the source produced 0 rows, that's allowed by the
        # contract. We can't compute a meaningful NULL rate on a 0-row file.
        if spec.min_rows < 1:
            continue

        csv_path = _resolve_source_csv(spec, processed_dir)
        if csv_path is None:
            failures.append(
                f"Source '{source_key}': CSV file not found "
                f"(searched: {get_all_aliases(source_key)} in {processed_dir})."
            )
            continue

        try:
            df = pd.read_csv(csv_path, compression="infer")
        except Exception as exc:
            failures.append(
                f"Source '{source_key}': could not read CSV '{csv_path.name}': "
                f"{type(exc).__name__}: {exc}"
            )
            continue

        if len(df) == 0:
            failures.append(
                f"Source '{source_key}': CSV '{csv_path.name}' has 0 data rows."
            )
            continue

        # Check NULL rate for every declared column. Sort columns
        # alphabetically for deterministic failure ordering.
        declared = sorted(_declared_columns(spec))
        for col_name in declared:
            if col_name not in df.columns:
                # Missing column — already flagged by validate_output_dir
                # for required columns. We still flag for optional columns
                # so the operator knows what's missing.
                failures.append(
                    f"Source '{source_key}': column '{col_name}' is declared "
                    f"in the contract but MISSING from CSV '{csv_path.name}'."
                )
                continue
            null_rate, total = _column_null_rate(df, col_name)
            if null_rate > max_null_rate:
                failures.append(
                    f"Source '{source_key}': column '{col_name}' has "
                    f"{null_rate:.1%} NULL rate "
                    f"({int(null_rate * total)}/{total} rows) in "
                    f"'{csv_path.name}' (max allowed: {max_null_rate:.1%})."
                )

    return (len(failures) == 0, failures)


__all__ = ["validate_feature_completeness"]
