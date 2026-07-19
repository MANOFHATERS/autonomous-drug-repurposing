"""Task 9.2 — retrain_on_validated reads 'outcome' column (BLOCKER #4).

P4-001 ROOT FIX verification.

retrain_on_validated previously read the WRONG CSV column ('validated'
instead of 'outcome'). The canonical schema
(shared.contracts.writeback.WRITEBACK_CSV_COLUMNS) uses an `outcome`
column with enum value "validated_positive" (NOT "validated"/"true").
As a result, retrain_on_validated ALWAYS returned an empty new_pairs
list against the real validated_hypotheses.csv — the data flywheel
(DOCX §10) was silently a no-op.

This test verifies the fix by:
  1. Writing a real validated_hypotheses.csv with 'outcome' column
     containing 'validated_positive' rows.
  2. Calling retrain_on_validated(csv_path).
  3. Asserting the returned dict has new_pairs_added > 0.

The test uses a TEMPORARY CSV in tmp_path — it does NOT modify the
production validated_hypotheses.csv.
"""
import csv
import inspect
import os
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

os.environ.setdefault("RL_REQUIRE_AUTH", "false")


def _write_validated_csv(path: Path, rows: list):
    """Write a validated_hypotheses.csv with the canonical 10-column schema."""
    headers = [
        "drug", "disease", "outcome", "validated_by",
        "validation_study_id", "validated_at", "notes",
        "original_gt_score", "original_rl_rank", "writeback_version",
    ]
    with open(path, "w", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)
        writer.writerow(headers)
        for row in rows:
            writer.writerow(row)


def test_task_9_2_retrain_reads_outcome_column_source():
    """P4-001: retrain_on_validated source MUST reference the 'outcome' column.

    Reads the actual function source (no mocks, no execution) and verifies
    the fix is present.
    """
    from rl.rl_drug_ranker import retrain_on_validated
    src = inspect.getsource(retrain_on_validated)

    # The function MUST reference the canonical 'outcome' column.
    assert "outcome" in src, (
        "P4-001 REGRESSION: retrain_on_validated does not reference the 'outcome' "
        "column. The canonical schema uses 'outcome' (not 'validated'). Without "
        "this fix, the data flywheel is silently a no-op."
    )

    # The function MUST test against "validated_positive" (the canonical enum).
    assert "validated_positive" in src, (
        "P4-001 REGRESSION: retrain_on_validated does not test against "
        "'validated_positive'. The canonical outcome enum values are "
        "'validated_positive' / 'validated_toxic' / 'validated_negative' / "
        "'invalidated' — NOT 'true' / '1' / 'yes'."
    )

    # The function MUST import the canonical column names from
    # shared.contracts.writeback.
    assert "shared.contracts.writeback" in src, (
        "P4-001 REGRESSION: retrain_on_validated does not import from "
        "shared.contracts.writeback. Hardcoded column names can drift from "
        "the canonical schema."
    )


def test_task_9_2_retrain_on_validated_real_csv(tmp_path, monkeypatch):
    """P4-001: retrain_on_validated reads 'outcome' and returns new pairs.

    Writes a real validated_hypotheses.csv with 3 validated_positive rows
    and 1 validated_toxic row. Calls retrain_on_validated(csv_path).
    Asserts new_pairs_added == 3 (toxic is skipped).
    """
    from rl.rl_drug_ranker import retrain_on_validated, VALIDATED_HYPOTHESES

    csv_path = tmp_path / "validated_hypotheses.csv"
    rows = [
        # 3 validated_positive pairs (should be added).
        ["aspirin", "cardiovascular disease", "validated_positive",
         "fda_approved", "FDALABEL-2019-aspirin-cv", "2019-04-15T00:00:00Z",
         "test", "0.92", "1", "2.0.0-shared-contract"],
        ["metformin", "type 2 diabetes", "validated_positive",
         "fda_approved", "FDALABEL-2017-metformin-t2dm", "2017-06-05T00:00:00Z",
         "test", "0.95", "2", "2.0.0-shared-contract"],
        ["warfarin", "atrial fibrillation", "validated_positive",
         "fda_approved", "FDALABEL-2018-warfarin-afib", "2018-03-20T00:00:00Z",
         "test", "0.90", "3", "2.0.0-shared-contract"],
        # 1 validated_toxic pair (should be SKIPPED — not added to bonus set).
        ["rofecoxib", "cardiovascular disease", "validated_toxic",
         "fda_withdrawal", "FDA-WITHDRAWAL-2004-rofecoxib", "2004-09-30T00:00:00Z",
         "test", "0.15", "40", "2.0.0-shared-contract"],
    ]
    _write_validated_csv(csv_path, rows)

    # Clear the module-level VALIDATED_HYPOTHESES so we can detect new additions.
    # We need to monkeypatch the global to an empty list.
    import rl.rl_drug_ranker as rdr
    monkeypatch.setattr(rdr, "VALIDATED_HYPOTHESES", [])

    result = retrain_on_validated(validated_csv_path=str(csv_path))

    # The 3 validated_positive pairs should be added. The 1 toxic pair is skipped.
    assert result["new_pairs_added"] == 3, (
        f"P4-001 REGRESSION: retrain_on_validated returned "
        f"new_pairs_added={result['new_pairs_added']}, expected 3. "
        f"The function is NOT reading the 'outcome' column correctly — "
        f"it may be reading 'validated' (the legacy column) which doesn't "
        f"exist in the canonical schema. Result: {result}"
    )
    assert result["validated_pairs_loaded"] == 3, (
        f"P4-001: validated_pairs_loaded={result['validated_pairs_loaded']}, "
        f"expected 3. Result: {result}"
    )


def test_task_9_2_retrain_skips_toxic_pairs(tmp_path, monkeypatch):
    """P4-001 + INT-020: retrain_on_validated MUST skip validated_toxic pairs.

    Toxic pairs must NOT be added to the reward bonus set — they should
    be penalized, not rewarded (patient-safety requirement).
    """
    from rl.rl_drug_ranker import retrain_on_validated

    csv_path = tmp_path / "validated_hypotheses.csv"
    rows = [
        ["rofecoxib", "cardiovascular disease", "validated_toxic",
         "fda_withdrawal", "FDA-WITHDRAWAL-2004-rofecoxib", "2004-09-30T00:00:00Z",
         "toxic — withdrawn", "0.15", "40", "2.0.0-shared-contract"],
        ["cisapride", "cardiovascular disease", "validated_toxic",
         "fda_withdrawal", "FDA-WITHDRAWAL-2000-cisapride", "2000-07-14T00:00:00Z",
         "toxic — QT prolongation", "0.12", "41", "2.0.0-shared-contract"],
    ]
    _write_validated_csv(csv_path, rows)

    import rl.rl_drug_ranker as rdr
    monkeypatch.setattr(rdr, "VALIDATED_HYPOTHESES", [])

    result = retrain_on_validated(validated_csv_path=str(csv_path))
    assert result["new_pairs_added"] == 0, (
        f"INT-020 REGRESSION: retrain_on_validated added "
        f"{result['new_pairs_added']} TOXIC pairs to the reward bonus set. "
        f"Toxic pairs must be SKIPPED — they should be penalized, not rewarded."
    )


def test_task_9_2_retrain_writes_canonical_schema(tmp_path, monkeypatch):
    """P4-033: retrain_on_validated writes the canonical 10-column schema.

    The previous code wrote a 3-column stub ["drug","disease","validated"],
    losing audit metadata (validated_by, validated_at, notes, etc.).
    """
    from rl.rl_drug_ranker import retrain_on_validated

    csv_path = tmp_path / "validated_hypotheses.csv"
    rows = [
        ["aspirin", "cardiovascular disease", "validated_positive",
         "fda_approved", "FDALABEL-2019-aspirin-cv", "2019-04-15T00:00:00Z",
         "test notes", "0.92", "1", "2.0.0-shared-contract"],
    ]
    _write_validated_csv(csv_path, rows)

    import rl.rl_drug_ranker as rdr
    monkeypatch.setattr(rdr, "VALIDATED_HYPOTHESES", [])

    retrain_on_validated(validated_csv_path=str(csv_path))

    # Read the CSV back and verify it has the canonical 10-column schema.
    with open(csv_path, "r", encoding="utf-8") as f:
        reader = csv.reader(f)
        header = next(reader)
    assert len(header) == 10, (
        f"P4-033 REGRESSION: retrain_on_validated wrote a {len(header)}-column "
        f"CSV, expected 10 (canonical schema). Header: {header}"
    )
    assert "validated_by" in header, (
        f"P4-033 REGRESSION: 'validated_by' column missing from written CSV. "
        f"Header: {header}"
    )
    assert "validated_at" in header, (
        f"P4-033 REGRESSION: 'validated_at' column missing from written CSV. "
        f"Header: {header}"
    )
    assert "outcome" in header, (
        f"P4-033 REGRESSION: 'outcome' column missing from written CSV. "
        f"Header: {header}"
    )
    # The legacy 3-column stub used 'validated' — it must NOT be present.
    assert "validated" not in header, (
        f"P4-033 REGRESSION: legacy 'validated' column present in written CSV. "
        f"The canonical schema uses 'outcome' (with enum values like "
        f"'validated_positive'), NOT a boolean 'validated' column. Header: {header}"
    )


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
