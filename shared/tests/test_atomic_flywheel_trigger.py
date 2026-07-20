"""SH-013 v129 ROOT FIX — atomic data flywheel trigger E2E test.

Verifies that trigger_flywheel_retrain_atomically() in
shared/monitoring/flywheel_monitor.py:
  1. Atomically writes a validated hypothesis to CSV + JSON.
  2. Calls retrain_on_validated to fine-tune the GT model.
  3. ROLLS BACK the CSV + JSON if retrain fails (no silent data loss).
  4. Validates inputs (empty drug/disease, invalid outcome).
  5. Cleans up backup files on success.

This test does NOT mock the writeback, the CSV/JSON reads/writes, or the
atomic rename. It DOES mock retrain_on_validated (via monkeypatch) to
simulate success and failure scenarios, because building a real GT
checkpoint + graph_state requires torch + a trained model (too heavy for
a contract test).

Verification per audit Task 14.7:
    python -m pytest shared/tests/test_atomic_flywheel_trigger.py -v
"""
from __future__ import annotations

import csv
import json
import os
import sys
from pathlib import Path

import pytest

# Ensure repo root is on sys.path.
REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def isolated_flywheel_env(tmp_path, monkeypatch):
    """Isolate the atomic flywheel trigger to a tmp directory.

    Sets VALIDATED_HYPOTHESES_CSV to a path inside tmp_path so the test
    does not pollute the repo's checked-in CSV.
    """
    validated_csv = tmp_path / "validated_hypotheses.csv"
    retrain_trigger = tmp_path / "retrain_triggered.json"
    checkpoint = tmp_path / "gt_checkpoint.pt"

    # Create a fake checkpoint (just a marker file — the test mocks retrain).
    checkpoint.write_text("fake checkpoint")

    monkeypatch.setenv("VALIDATED_HYPOTHESES_CSV", str(validated_csv))

    return {
        "tmp_path": tmp_path,
        "validated_csv": validated_csv,
        "retrain_trigger": retrain_trigger,
        "checkpoint": str(checkpoint),
    }


def _read_csv(csv_path: Path):
    """Read a CSV and return (rows, fieldnames). Returns ([], []) if not exists."""
    if not csv_path.exists():
        return [], []
    with open(csv_path, "r", newline="", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        rows = list(reader)
        return rows, reader.fieldnames or []


def _read_json(json_path: Path):
    """Read a JSON list. Returns [] if not exists or invalid."""
    if not json_path.exists():
        return []
    try:
        with open(json_path, "r", encoding="utf-8") as f:
            data = json.load(f)
        return data if isinstance(data, list) else []
    except (json.JSONDecodeError, OSError):
        return []


# ---------------------------------------------------------------------------
# Test 1: SUCCESS — writeback + retrain both succeed
# ---------------------------------------------------------------------------

def test_atomic_trigger_success(isolated_flywheel_env, monkeypatch):
    """SH-013 v129: when retrain succeeds, CSV + JSON are kept."""
    env = isolated_flywheel_env

    # Mock retrain_on_validated to return success.
    def _mock_retrain_success(checkpoint_path, validated_csv_path,
                              output_checkpoint_path=None,
                              fine_tune_epochs=10, learning_rate=1e-4):
        return {
            "validated_pairs_added": 1,
            "fine_tune_epochs": fine_tune_epochs,
            "val_auc_before": 0.80,
            "val_auc_after": 0.85,
            "output_checkpoint": output_checkpoint_path or checkpoint_path,
        }

    # Patch retrain_on_validated in the trainer module (where the trigger imports it).
    import graph_transformer.training.trainer as trainer_mod
    monkeypatch.setattr(trainer_mod, "retrain_on_validated", _mock_retrain_success)

    from shared.monitoring.flywheel_monitor import trigger_flywheel_retrain_atomically

    result = trigger_flywheel_retrain_atomically(
        drug="aspirin",
        disease="diabetes",
        outcome="validated_positive",
        checkpoint_path=env["checkpoint"],
        validated_by="wet_lab:partner_a",
        validation_study_id="NCT_TEST_001",
        csv_path=str(env["validated_csv"]),
        retrain_trigger_path=str(env["retrain_trigger"]),
        fine_tune_epochs=3,
    )

    # Verify the trigger returned success.
    assert result["status"] == "success", (
        f"Expected status='success', got {result['status']!r}. Error: {result.get('error')}"
    )
    assert result["validated_pair"] == ("aspirin", "diabetes")
    assert result["outcome"] == "validated_positive"
    assert result["rollback_performed"] is False
    assert result["error"] is None
    assert result["retrain_result"]["validated_pairs_added"] == 1

    # Verify the CSV was written.
    rows, fieldnames = _read_csv(env["validated_csv"])
    assert len(rows) == 1, f"Expected 1 CSV row, got {len(rows)}"
    assert rows[0]["drug"] == "aspirin"
    assert rows[0]["disease"] == "diabetes"
    assert rows[0]["outcome"] == "validated_positive"
    assert rows[0]["validated_by"] == "wet_lab:partner_a"
    assert rows[0]["validation_study_id"] == "NCT_TEST_001"

    # Verify the richer schema columns are present (SH-003 v129).
    assert "drug_id" in fieldnames
    assert "drug_name" in fieldnames
    assert "disease_id" in fieldnames
    assert "disease_name" in fieldnames
    assert "score" in fieldnames

    # Verify the JSON trigger was written.
    trigger_entries = _read_json(env["retrain_trigger"])
    assert len(trigger_entries) == 1
    assert trigger_entries[0]["drug"] == "aspirin"
    assert trigger_entries[0]["disease"] == "diabetes"
    assert trigger_entries[0]["outcome"] == "validated_positive"

    # Verify NO backup files are left behind.
    assert not env["validated_csv"].with_suffix(".csv.bak").exists()
    assert not Path(str(env["retrain_trigger"]) + ".bak").exists()


# ---------------------------------------------------------------------------
# Test 2: ROLLBACK — retrain fails, CSV + JSON are rolled back
# ---------------------------------------------------------------------------

def test_atomic_trigger_rollback_on_retrain_failure(isolated_flywheel_env, monkeypatch):
    """SH-013 v129: when retrain fails, CSV + JSON are ROLLED BACK.

    This is the CORE atomicity guarantee. The previous behavior was:
    writeback updates CSV + JSON, then retrain fails — the validated pair
    is recorded as "processed" but the model never learned it. The next
    run sees the pair in the CSV and skips it (idempotent), so the model
    NEVER learns from this validation. This is silent data loss.

    ROOT FIX: rollback the CSV + JSON to their pre-trigger state.
    """
    env = isolated_flywheel_env

    # Pre-populate the CSV with an existing row (to verify rollback preserves it).
    existing_csv = env["validated_csv"]
    with open(existing_csv, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=["drug", "disease", "outcome", "validated_at"])
        writer.writeheader()
        writer.writerow({
            "drug": "metformin", "disease": "pain",
            "outcome": "validated_positive", "validated_at": "2025-01-01T00:00:00Z",
        })

    # Pre-populate the JSON trigger with an existing entry.
    existing_json = env["retrain_trigger"]
    with open(existing_json, "w", encoding="utf-8") as f:
        json.dump([{
            "drug": "metformin", "disease": "pain",
            "outcome": "validated_positive",
            "validated_at": "2025-01-01T00:00:00Z",
        }], f)

    # Mock retrain_on_validated to return an ERROR.
    def _mock_retrain_failure(checkpoint_path, validated_csv_path,
                              output_checkpoint_path=None,
                              fine_tune_epochs=10, learning_rate=1e-4):
        return {
            "validated_pairs_added": 0,
            "fine_tune_epochs": 0,
            "val_auc_before": 0.0,
            "val_auc_after": 0.0,
            "output_checkpoint": checkpoint_path,
            "error": "simulated retrain failure (checkpoint corrupt)",
        }

    import graph_transformer.training.trainer as trainer_mod
    monkeypatch.setattr(trainer_mod, "retrain_on_validated", _mock_retrain_failure)

    from shared.monitoring.flywheel_monitor import trigger_flywheel_retrain_atomically

    result = trigger_flywheel_retrain_atomically(
        drug="aspirin",
        disease="diabetes",
        outcome="validated_positive",
        checkpoint_path=env["checkpoint"],
        csv_path=str(env["validated_csv"]),
        retrain_trigger_path=str(env["retrain_trigger"]),
        fine_tune_epochs=3,
    )

    # Verify the trigger returned rolled_back.
    assert result["status"] == "rolled_back", (
        f"Expected status='rolled_back', got {result['status']!r}"
    )
    assert result["rollback_performed"] is True, "rollback_performed should be True"
    assert result["error"] is not None
    assert "simulated retrain failure" in result["error"]

    # CORE ASSERTION: the CSV should be ROLLED BACK to its pre-trigger state.
    # The new (aspirin, diabetes) row should NOT be in the CSV.
    rows, _ = _read_csv(env["validated_csv"])
    assert len(rows) == 1, (
        f"Expected 1 row after rollback (the pre-existing metformin row), "
        f"got {len(rows)}. The rollback did NOT restore the CSV."
    )
    assert rows[0]["drug"] == "metformin", (
        f"Expected the pre-existing metformin row, got drug={rows[0]['drug']!r}"
    )

    # CORE ASSERTION: the JSON should be ROLLED BACK to its pre-trigger state.
    trigger_entries = _read_json(env["retrain_trigger"])
    assert len(trigger_entries) == 1, (
        f"Expected 1 JSON entry after rollback, got {len(trigger_entries)}"
    )
    assert trigger_entries[0]["drug"] == "metformin"

    # Verify NO backup files are left behind.
    assert not Path(str(env["validated_csv"]) + ".bak").exists()
    assert not Path(str(env["retrain_trigger"]) + ".bak").exists()


# ---------------------------------------------------------------------------
# Test 3: ROLLBACK — retrain raises an exception, CSV + JSON are rolled back
# ---------------------------------------------------------------------------

def test_atomic_trigger_rollback_on_retrain_exception(isolated_flywheel_env, monkeypatch):
    """SH-013 v129: when retrain RAISES, CSV + JSON are rolled back."""
    env = isolated_flywheel_env

    def _mock_retrain_raise(checkpoint_path, validated_csv_path,
                            output_checkpoint_path=None,
                            fine_tune_epochs=10, learning_rate=1e-4):
        raise RuntimeError("simulated crash in retrain_on_validated")

    import graph_transformer.training.trainer as trainer_mod
    monkeypatch.setattr(trainer_mod, "retrain_on_validated", _mock_retrain_raise)

    from shared.monitoring.flywheel_monitor import trigger_flywheel_retrain_atomically

    result = trigger_flywheel_retrain_atomically(
        drug="aspirin",
        disease="diabetes",
        outcome="validated_positive",
        checkpoint_path=env["checkpoint"],
        csv_path=str(env["validated_csv"]),
        retrain_trigger_path=str(env["retrain_trigger"]),
    )

    assert result["status"] == "rolled_back"
    assert result["rollback_performed"] is True
    assert "simulated crash" in result["error"]

    # CSV should be EMPTY (rolled back to non-existent state).
    rows, _ = _read_csv(env["validated_csv"])
    assert len(rows) == 0, (
        f"Expected 0 rows after rollback (CSV didn't exist before), got {len(rows)}"
    )

    # JSON should be EMPTY (rolled back to non-existent state).
    trigger_entries = _read_json(env["retrain_trigger"])
    assert len(trigger_entries) == 0


# ---------------------------------------------------------------------------
# Test 4: INPUT VALIDATION — empty drug/disease, invalid outcome
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("drug,disease,outcome,expected_error_substr", [
    ("", "diabetes", "validated_positive", "drug must be a non-empty string"),
    ("aspirin", "", "validated_positive", "disease must be a non-empty string"),
    ("aspirin", "diabetes", "invalid_outcome", "is not valid"),
    ("aspirin", "diabetes", "true", "is not valid"),
    ("aspirin", "diabetes", "validated", "is not valid"),
])
def test_atomic_trigger_input_validation(
    isolated_flywheel_env, drug, disease, outcome, expected_error_substr,
):
    """SH-013 v129: invalid inputs are rejected BEFORE any writeback."""
    env = isolated_flywheel_env

    from shared.monitoring.flywheel_monitor import trigger_flywheel_retrain_atomically

    result = trigger_flywheel_retrain_atomically(
        drug=drug,
        disease=disease,
        outcome=outcome,
        checkpoint_path=env["checkpoint"],
        csv_path=str(env["validated_csv"]),
        retrain_trigger_path=str(env["retrain_trigger"]),
    )

    assert result["status"] == "rolled_back"
    assert result["rollback_performed"] is False, (
        "rollback should NOT be performed for input validation errors "
        "(nothing was written yet)"
    )
    assert expected_error_substr in result["error"], (
        f"Expected error to contain {expected_error_substr!r}, got {result['error']!r}"
    )

    # CSV and JSON should NOT exist (nothing was written).
    assert not env["validated_csv"].exists()
    assert not env["retrain_trigger"].exists()


# ---------------------------------------------------------------------------
# Test 5: SKIP RETRAIN — writeback only (for testing the atomic write)
# ---------------------------------------------------------------------------

def test_atomic_trigger_skip_retrain(isolated_flywheel_env):
    """SH-013 v129: skip_retrain=True writes CSV + JSON but skips retrain."""
    env = isolated_flywheel_env

    from shared.monitoring.flywheel_monitor import trigger_flywheel_retrain_atomically

    result = trigger_flywheel_retrain_atomically(
        drug="aspirin",
        disease="diabetes",
        outcome="validated_positive",
        checkpoint_path=env["checkpoint"],
        csv_path=str(env["validated_csv"]),
        retrain_trigger_path=str(env["retrain_trigger"]),
        skip_retrain=True,
    )

    assert result["status"] == "writeback_only"
    assert result["rollback_performed"] is False
    assert result["error"] is None

    # CSV should have the row.
    rows, _ = _read_csv(env["validated_csv"])
    assert len(rows) == 1
    assert rows[0]["drug"] == "aspirin"

    # JSON should have the entry.
    trigger_entries = _read_json(env["retrain_trigger"])
    assert len(trigger_entries) == 1
    assert trigger_entries[0]["drug"] == "aspirin"


# ---------------------------------------------------------------------------
# Test 6: Atomic write primitives — temp files are cleaned up
# ---------------------------------------------------------------------------

def test_atomic_write_csv_no_temp_left_behind(isolated_flywheel_env):
    """SH-013 v129: _atomic_write_csv leaves no temp files behind."""
    from shared.monitoring.flywheel_monitor import _atomic_write_csv

    csv_path = str(isolated_flywheel_env["validated_csv"])
    _atomic_write_csv(csv_path, [{"drug": "aspirin", "disease": "pain"}], ["drug", "disease"])

    # The CSV should exist.
    assert os.path.exists(csv_path)

    # No temp files should be left in the directory.
    tmp_files = [
        f for f in os.listdir(os.path.dirname(csv_path))
        if f.startswith(".validated_hypotheses.csv.") and f.endswith(".tmp")
    ]
    assert len(tmp_files) == 0, f"Temp files left behind: {tmp_files}"


def test_atomic_write_json_no_temp_left_behind(isolated_flywheel_env):
    """SH-013 v129: _atomic_write_json leaves no temp files behind."""
    from shared.monitoring.flywheel_monitor import _atomic_write_json

    json_path = str(isolated_flywheel_env["retrain_trigger"])
    _atomic_write_json(json_path, [{"drug": "aspirin", "disease": "pain"}])

    assert os.path.exists(json_path)

    tmp_files = [
        f for f in os.listdir(os.path.dirname(json_path))
        if f.startswith(".retrain_triggered.json.") and f.endswith(".tmp")
    ]
    assert len(tmp_files) == 0, f"Temp files left behind: {tmp_files}"


# ---------------------------------------------------------------------------
# Test 7: Multiple triggers — CSV accumulates rows correctly
# ---------------------------------------------------------------------------

def test_atomic_trigger_multiple_accumulates(isolated_flywheel_env, monkeypatch):
    """SH-013 v129: multiple triggers accumulate rows in the CSV + JSON."""
    env = isolated_flywheel_env

    def _mock_retrain(checkpoint_path, validated_csv_path,
                      output_checkpoint_path=None,
                      fine_tune_epochs=10, learning_rate=1e-4):
        return {
            "validated_pairs_added": 1,
            "fine_tune_epochs": fine_tune_epochs,
            "val_auc_before": 0.80,
            "val_auc_after": 0.82,
            "output_checkpoint": output_checkpoint_path or checkpoint_path,
        }

    import graph_transformer.training.trainer as trainer_mod
    monkeypatch.setattr(trainer_mod, "retrain_on_validated", _mock_retrain)

    from shared.monitoring.flywheel_monitor import trigger_flywheel_retrain_atomically

    # Trigger 1.
    r1 = trigger_flywheel_retrain_atomically(
        drug="aspirin", disease="diabetes", outcome="validated_positive",
        checkpoint_path=env["checkpoint"],
        csv_path=str(env["validated_csv"]),
        retrain_trigger_path=str(env["retrain_trigger"]),
    )
    assert r1["status"] == "success"

    # Trigger 2.
    r2 = trigger_flywheel_retrain_atomically(
        drug="metformin", disease="pain", outcome="validated_positive",
        checkpoint_path=env["checkpoint"],
        csv_path=str(env["validated_csv"]),
        retrain_trigger_path=str(env["retrain_trigger"]),
    )
    assert r2["status"] == "success"

    # Trigger 3 (toxic outcome).
    r3 = trigger_flywheel_retrain_atomically(
        drug="warfarin", disease="epilepsy", outcome="validated_toxic",
        checkpoint_path=env["checkpoint"],
        csv_path=str(env["validated_csv"]),
        retrain_trigger_path=str(env["retrain_trigger"]),
    )
    assert r3["status"] == "success"

    # Verify the CSV has 3 rows.
    rows, _ = _read_csv(env["validated_csv"])
    assert len(rows) == 3
    drugs_in_csv = {r["drug"] for r in rows}
    assert drugs_in_csv == {"aspirin", "metformin", "warfarin"}

    # Verify the JSON has 3 entries.
    trigger_entries = _read_json(env["retrain_trigger"])
    assert len(trigger_entries) == 3
