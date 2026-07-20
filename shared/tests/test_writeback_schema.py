"""SH-003 v129 ROOT FIX — contract test for the writeback schema.

Verifies that shared.contracts.writeback and rl.contracts.phase4_schema
agree on the canonical column names and outcome enum values. This test
FAILS if the two modules drift — preventing the exact bug (SH-003) where
shared used (drug, disease) while rl used (drug_id, drug_name, disease_id,
disease_name, score).

v129 ROOT FIX (Teammate 14): the audit required the richer schema
(drug_id, drug_name, disease_id, disease_name, score) to be standardized.
This test verifies:
  1. shared.contracts.writeback defines the richer schema constants.
  2. WRITEBACK_CSV_COLUMNS includes the richer schema columns.
  3. rl.contracts.phase4_schema delegates to shared (no drift).
  4. Both modules agree on the outcome enum (4 values).
  5. The schema is well-defined (no duplicates, no None values).

Verification per audit Task 14.3:
    python -m pytest shared/tests/test_writeback_schema.py -v
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

# Ensure repo root is on sys.path.
REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))


# ---------------------------------------------------------------------------
# Test 1: shared.contracts.writeback defines the richer schema constants
# ---------------------------------------------------------------------------

def test_shared_contract_has_richer_schema_constants():
    """SH-003 v129: shared.contracts.writeback MUST define the richer schema.

    The audit requires the canonical schema to include:
        drug_id, drug_name, disease_id, disease_name, score, outcome
    """
    from shared.contracts.writeback import (
        DRUG_COL,
        DISEASE_COL,
        DRUG_ID_COL,
        DRUG_NAME_COL,
        DISEASE_ID_COL,
        DISEASE_NAME_COL,
        SCORE_COL,
        OUTCOME_COL,
    )

    # Simple schema (backward compat).
    assert DRUG_COL == "drug", f"DRUG_COL should be 'drug', got {DRUG_COL!r}"
    assert DISEASE_COL == "disease", f"DISEASE_COL should be 'disease', got {DISEASE_COL!r}"

    # Richer schema (SH-003 v129 ROOT FIX).
    assert DRUG_ID_COL == "drug_id", f"DRUG_ID_COL should be 'drug_id', got {DRUG_ID_COL!r}"
    assert DRUG_NAME_COL == "drug_name", f"DRUG_NAME_COL should be 'drug_name', got {DRUG_NAME_COL!r}"
    assert DISEASE_ID_COL == "disease_id", f"DISEASE_ID_COL should be 'disease_id', got {DISEASE_ID_COL!r}"
    assert DISEASE_NAME_COL == "disease_name", f"DISEASE_NAME_COL should be 'disease_name', got {DISEASE_NAME_COL!r}"
    assert SCORE_COL == "score", f"SCORE_COL should be 'score', got {SCORE_COL!r}"
    assert OUTCOME_COL == "outcome", f"OUTCOME_COL should be 'outcome', got {OUTCOME_COL!r}"


# ---------------------------------------------------------------------------
# Test 2: WRITEBACK_CSV_COLUMNS includes the richer schema columns
# ---------------------------------------------------------------------------

def test_writeback_csv_columns_includes_richer_schema():
    """SH-003 v129: WRITEBACK_CSV_COLUMNS MUST include the richer schema columns."""
    from shared.contracts.writeback import (
        WRITEBACK_CSV_COLUMNS,
        DRUG_COL,
        DISEASE_COL,
        DRUG_ID_COL,
        DRUG_NAME_COL,
        DISEASE_ID_COL,
        DISEASE_NAME_COL,
        SCORE_COL,
        OUTCOME_COL,
    )

    # The canonical schema MUST include all of these.
    required_in_schema = [
        DRUG_COL, DISEASE_COL,
        DRUG_ID_COL, DRUG_NAME_COL, DISEASE_ID_COL, DISEASE_NAME_COL, SCORE_COL,
        OUTCOME_COL,
    ]
    for col in required_in_schema:
        assert col in WRITEBACK_CSV_COLUMNS, (
            f"Column {col!r} is MISSING from WRITEBACK_CSV_COLUMNS. "
            f"The canonical schema must include the richer schema per SH-003 v129. "
            f"Current columns: {WRITEBACK_CSV_COLUMNS}"
        )

    # No duplicate columns.
    assert len(WRITEBACK_CSV_COLUMNS) == len(set(WRITEBACK_CSV_COLUMNS)), (
        f"WRITEBACK_CSV_COLUMNS has duplicates: {WRITEBACK_CSV_COLUMNS}"
    )

    # No None values.
    assert all(c is not None for c in WRITEBACK_CSV_COLUMNS), (
        f"WRITEBACK_CSV_COLUMNS has None values: {WRITEBACK_CSV_COLUMNS}"
    )


# ---------------------------------------------------------------------------
# Test 3: REQUIRED_COLUMNS stays backward compatible
# ---------------------------------------------------------------------------

def test_required_columns_backward_compatible():
    """SH-003 v129: REQUIRED_COLUMNS keeps the simple schema as REQUIRED.

    The richer columns (drug_id, drug_name, etc.) are OPTIONAL — existing
    writers that only populate drug/disease keep working.
    """
    from shared.contracts.writeback import REQUIRED_COLUMNS

    # The simple drug/disease columns MUST be in REQUIRED_COLUMNS (backward compat).
    assert "drug" in REQUIRED_COLUMNS
    assert "disease" in REQUIRED_COLUMNS
    assert "outcome" in REQUIRED_COLUMNS
    assert "validated_at" in REQUIRED_COLUMNS

    # The richer columns MUST NOT be in REQUIRED_COLUMNS (they're optional).
    assert "drug_id" not in REQUIRED_COLUMNS, (
        "drug_id should be OPTIONAL, not REQUIRED — existing writers don't populate it"
    )
    assert "drug_name" not in REQUIRED_COLUMNS
    assert "disease_id" not in REQUIRED_COLUMNS
    assert "disease_name" not in REQUIRED_COLUMNS


# ---------------------------------------------------------------------------
# Test 4: OPTIONAL_COLUMNS includes the richer schema
# ---------------------------------------------------------------------------

def test_optional_columns_includes_richer_schema():
    """SH-003 v129: OPTIONAL_COLUMNS includes the richer schema columns."""
    from shared.contracts.writeback import OPTIONAL_COLUMNS

    assert "drug_id" in OPTIONAL_COLUMNS
    assert "drug_name" in OPTIONAL_COLUMNS
    assert "disease_id" in OPTIONAL_COLUMNS
    assert "disease_name" in OPTIONAL_COLUMNS
    assert "score" in OPTIONAL_COLUMNS


# ---------------------------------------------------------------------------
# Test 5: outcome enum has 4 values (SH-002)
# ---------------------------------------------------------------------------

def test_outcome_enum_has_4_values():
    """SH-002: the outcome enum MUST have 4 values (not 3).

    The 4 values are:
      validated_positive, validated_toxic, validated_negative, invalidated
    """
    from shared.contracts.writeback import (
        VALID_OUTCOMES,
        OUTCOME_VALIDATED_POSITIVE,
        OUTCOME_VALIDATED_TOXIC,
        OUTCOME_VALIDATED_NEGATIVE,
        OUTCOME_INVALIDATED,
    )

    assert len(VALID_OUTCOMES) == 4, (
        f"VALID_OUTCOMES should have 4 values, got {len(VALID_OUTCOMES)}: {VALID_OUTCOMES}"
    )
    assert OUTCOME_VALIDATED_POSITIVE == "validated_positive"
    assert OUTCOME_VALIDATED_TOXIC == "validated_toxic"
    assert OUTCOME_VALIDATED_NEGATIVE == "validated_negative"
    assert OUTCOME_INVALIDATED == "invalidated"


# ---------------------------------------------------------------------------
# Test 6: rl.contracts.phase4_schema delegates to shared (no drift)
# ---------------------------------------------------------------------------

def test_rl_phase4_schema_delegates_to_shared():
    """SH-003: rl.contracts.phase4_schema MUST delegate to shared.contracts.writeback.

    This prevents drift between the two modules. If rl defines its own
    constants (instead of importing from shared), this test fails.
    """
    from shared.contracts.writeback import (
        DRUG_COL as SHARED_DRUG_COL,
        DISEASE_COL as SHARED_DISEASE_COL,
        OUTCOME_COL as SHARED_OUTCOME_COL,
        VALID_OUTCOMES as SHARED_VALID_OUTCOMES,
        WRITEBACK_CSV_COLUMNS as SHARED_WRITEBACK_CSV_COLUMNS,
    )
    from rl.contracts.phase4_schema import (
        DRUG_COL as RL_DRUG_COL,
        DISEASE_COL as RL_DISEASE_COL,
        OUTCOME_COL as RL_OUTCOME_COL,
        OUTCOME_VALUES as RL_OUTCOME_VALUES,
        VALIDATED_HYPOTHESES_COLUMN_NAMES as RL_COLUMN_NAMES,
    )

    # The column names MUST match (no drift).
    assert RL_DRUG_COL == SHARED_DRUG_COL, (
        f"DRUG_COL drift: shared={SHARED_DRUG_COL!r}, rl={RL_DRUG_COL!r}"
    )
    assert RL_DISEASE_COL == SHARED_DISEASE_COL, (
        f"DISEASE_COL drift: shared={SHARED_DISEASE_COL!r}, rl={RL_DISEASE_COL!r}"
    )
    assert RL_OUTCOME_COL == SHARED_OUTCOME_COL, (
        f"OUTCOME_COL drift: shared={SHARED_OUTCOME_COL!r}, rl={RL_OUTCOME_COL!r}"
    )

    # The outcome enum MUST match (4 values, no drift).
    assert set(RL_OUTCOME_VALUES) == set(SHARED_VALID_OUTCOMES), (
        f"Outcome enum drift: shared={SHARED_VALID_OUTCOMES}, rl={RL_OUTCOME_VALUES}"
    )
    assert len(RL_OUTCOME_VALUES) == 4, (
        f"rl OUTCOME_VALUES should have 4 values, got {len(RL_OUTCOME_VALUES)}: {RL_OUTCOME_VALUES}"
    )

    # The column list MUST match (rl's column names = shared's CSV columns).
    assert tuple(RL_COLUMN_NAMES) == tuple(SHARED_WRITEBACK_CSV_COLUMNS), (
        f"Column list drift:\n"
        f"  shared: {SHARED_WRITEBACK_CSV_COLUMNS}\n"
        f"  rl:     {list(RL_COLUMN_NAMES)}"
    )


# ---------------------------------------------------------------------------
# Test 7: WRITEBACK_DTYPES includes the richer schema
# ---------------------------------------------------------------------------

def test_writeback_dtypes_includes_richer_schema():
    """SH-003 v129: WRITEBACK_DTYPES includes dtypes for the richer schema columns."""
    from shared.contracts.writeback import WRITEBACK_DTYPES

    assert WRITEBACK_DTYPES.get("drug_id") == "str"
    assert WRITEBACK_DTYPES.get("drug_name") == "str"
    assert WRITEBACK_DTYPES.get("disease_id") == "str"
    assert WRITEBACK_DTYPES.get("disease_name") == "str"
    assert WRITEBACK_DTYPES.get("score") == "float"

    # Every column in WRITEBACK_CSV_COLUMNS has a dtype.
    from shared.contracts.writeback import WRITEBACK_CSV_COLUMNS
    for col in WRITEBACK_CSV_COLUMNS:
        assert col in WRITEBACK_DTYPES, (
            f"Column {col!r} is in WRITEBACK_CSV_COLUMNS but has no dtype in WRITEBACK_DTYPES"
        )


# ---------------------------------------------------------------------------
# Test 8: __all__ exports the richer schema constants
# ---------------------------------------------------------------------------

def test_all_exports_richer_schema():
    """SH-003 v129: __all__ exports the richer schema constants."""
    from shared.contracts import writeback as wb

    required_exports = [
        "DRUG_ID_COL",
        "DRUG_NAME_COL",
        "DISEASE_ID_COL",
        "DISEASE_NAME_COL",
        "SCORE_COL",
        "OPTIONAL_COLUMNS",
    ]
    for name in required_exports:
        assert name in wb.__all__, (
            f"{name!r} is MISSING from shared.contracts.writeback.__all__"
        )
        assert hasattr(wb, name), (
            f"{name!r} is in __all__ but not defined in the module"
        )
