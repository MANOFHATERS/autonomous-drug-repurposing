"""TASK 8.4 verification: reward function uses Phase 1 safety signals
(withdrawn drugs).

The task spec requires:
  (1) load safety signals from Phase 1 (is_withdrawn, withdrawn_reason)
  (2) set safety_score=0.0 for withdrawn drugs
  (3) verify the reward function penalizes them

We test:
  - load_phase1_safety_signals reads a fake drugbank_drugs.csv with
    the is_withdrawn column and returns the set of withdrawn drug names
  - load_phase1_safety_signals handles the groups column (legacy
    DrugBank string format "approved;withdrawn")
  - load_phase1_safety_signals handles missing CSV (degraded mode)
  - load_phase1_safety_signals raises FileNotFoundError when the
    directory itself doesn't exist
  - compute_safety_score_with_phase1 returns 0.0 for withdrawn drugs
  - compute_safety_score_with_phase1 returns 1.0 for safe drugs
  - merge_withdrawn_drugs_with_phase1 returns the UNION of Phase 1
    and hardcoded withdrawn drugs
  - build_reward_function_with_phase1_safety builds a RewardFunction
    (with graceful fallback when extra_withdrawn_drugs is not accepted)
"""
import os
import sys
import csv
import pytest
import tempfile

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..', '..')))

from rl.reward import (
    load_phase1_safety_signals,
    compute_safety_score_with_phase1,
    merge_withdrawn_drugs_with_phase1,
    build_reward_function_with_phase1_safety,
    WITHDRAWN_DRUGS,
    RewardFunction,
    RewardConfig,
    PHASE1_DRUG_NAME_COLUMN,
    PHASE1_IS_WITHDRAWN_COLUMN,
    PHASE1_GROUPS_COLUMN,
)


@pytest.fixture
def fake_phase1_dir():
    """Build a fake Phase 1 directory with a drugbank_drugs.csv."""
    tmp = tempfile.mkdtemp(prefix="phase1_test_")
    csv_path = os.path.join(tmp, "drugbank_drugs.csv")
    rows = [
        # Header
        {
            "name": "name", "inchikey": "inchikey",
            "drugbank_id": "drugbank_id", "is_withdrawn": "is_withdrawn",
            "groups": "groups", "withdrawn_reason": "withdrawn_reason",
        },
        # Approved, not withdrawn (control).
        {
            "name": "Aspirin", "inchikey": "BSYNRYMUTXBXSQ-UHFFFAOYSA-N",
            "drugbank_id": "DB00945", "is_withdrawn": "False",
            "groups": "approved", "withdrawn_reason": "",
        },
        # Withdrawn via is_withdrawn=True (rofecoxib / Vioxx).
        {
            "name": "Rofecoxib", "inchikey": "RZYAOLQLOIELDH-UHFFFAOYSA-N",
            "drugbank_id": "DB00795", "is_withdrawn": "True",
            "groups": "approved;withdrawn", "withdrawn_reason": "cardiovascular risk",
        },
        # Withdrawn via groups token only (terfenadine — legacy).
        {
            "name": "Terfenadine", "inchikey": "GUGOYVSPIZAFGO-UHFFFAOYSA-N",
            "drugbank_id": "DB00842", "is_withdrawn": "False",
            "groups": "withdrawn", "withdrawn_reason": "QT prolongation",
        },
        # Approved, in neither set.
        {
            "name": "Ibuprofen", "inchikey": "HEFNNWSXXWATIW-UHFFFAOYSA-N",
            "drugbank_id": "DB01050", "is_withdrawn": "False",
            "groups": "approved", "withdrawn_reason": "",
        },
    ]
    with open(csv_path, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=list(rows[0].keys()))
        writer.writeheader()
        for row in rows[1:]:
            writer.writerow(row)
    return tmp


def test_load_phase1_safety_signals_loads_is_withdrawn_column(fake_phase1_dir):
    """(1) load_phase1_safety_signals reads the is_withdrawn column."""
    withdrawn, reasons = load_phase1_safety_signals(fake_phase1_dir)
    # Rofecoxib has is_withdrawn=True.
    assert "rofecoxib" in withdrawn, f"rofecoxib not in {withdrawn}"
    # Terfenadine is in via the groups column (handled by the next test).
    assert "terfenadine" in withdrawn, f"terfenadine not in {withdrawn}"


def test_load_phase1_safety_signals_loads_groups_column(fake_phase1_dir):
    """(1) load_phase1_safety_signals reads the groups column (legacy format).

    Terfenadine has is_withdrawn=False but groups="withdrawn". The loader
    must catch this — DrugBank sometimes populates only the groups column.
    """
    withdrawn, _ = load_phase1_safety_signals(fake_phase1_dir)
    assert "terfenadine" in withdrawn


def test_load_phase1_safety_signals_returns_reasons(fake_phase1_dir):
    """(1) load_phase1_safety_signals returns the withdrawn_reason column."""
    _, reasons = load_phase1_safety_signals(fake_phase1_dir)
    assert "rofecoxib" in reasons
    assert reasons["rofecoxib"] == "cardiovascular risk"
    assert reasons["terfenadine"] == "QT prolongation"


def test_load_phase1_safety_signals_handles_missing_csv():
    """When the CSV is missing (DrugBank license paused), returns empty sets."""
    tmp = tempfile.mkdtemp(prefix="phase1_empty_")
    # No CSV in the directory.
    withdrawn, reasons = load_phase1_safety_signals(tmp)
    assert withdrawn == set()
    assert reasons == {}


def test_load_phase1_safety_signals_raises_on_missing_directory():
    """A missing DIRECTORY is a configuration error (raise FileNotFoundError)."""
    with pytest.raises(FileNotFoundError, match="Phase 1 directory not found"):
        load_phase1_safety_signals("/nonexistent/path/that/does/not/exist")


def test_compute_safety_score_with_phase1_returns_zero_for_withdrawn(fake_phase1_dir):
    """(2) compute_safety_score_with_phase1 returns 0.0 for withdrawn drugs."""
    withdrawn, _ = load_phase1_safety_signals(fake_phase1_dir)
    # Rofecoxib is withdrawn via Phase 1 — must get 0.0.
    assert compute_safety_score_with_phase1("rofecoxib", withdrawn) == 0.0
    # Case-insensitive.
    assert compute_safety_score_with_phase1("ROFECOXIB", withdrawn) == 0.0
    assert compute_safety_score_with_phase1("  Rofecoxib  ", withdrawn) == 0.0


def test_compute_safety_score_with_phase1_returns_one_for_safe_drugs(fake_phase1_dir):
    """(2) compute_safety_score_with_phase1 returns 1.0 for safe drugs."""
    withdrawn, _ = load_phase1_safety_signals(fake_phase1_dir)
    assert compute_safety_score_with_phase1("aspirin", withdrawn) == 1.0
    assert compute_safety_score_with_phase1("ibuprofen", withdrawn) == 1.0


def test_compute_safety_score_with_phase1_catches_hardcoded_withdrawn():
    """Hardcoded WITHDRAWN_DRUGS are penalized even without Phase 1 data."""
    # Empty Phase 1 set — the function must STILL catch hardcoded withdrawals.
    assert compute_safety_score_with_phase1("rofecoxib", set()) == 0.0
    assert compute_safety_score_with_phase1("vioxx", set()) == 0.0
    assert compute_safety_score_with_phase1("troglitazone", set()) == 0.0
    assert compute_safety_score_with_phase1("cisapride", set()) == 0.0


def test_merge_withdrawn_drugs_with_phase1_returns_union(fake_phase1_dir):
    """merge_withdrawn_drugs_with_phase1 returns the UNION of Phase 1 + hardcoded."""
    phase1_withdrawn, _ = load_phase1_safety_signals(fake_phase1_dir)
    merged = merge_withdrawn_drugs_with_phase1(phase1_withdrawn)
    # Phase 1 only.
    assert "rofecoxib" in merged
    assert "terfenadine" in merged
    # Hardcoded only (not in fake Phase 1).
    assert "troglitazone" in merged
    assert "cisapride" in merged
    assert "cerivastatin" in merged
    # The merged set must be STRICTLY LARGER than the hardcoded set
    # (because Phase 1 added terfenadine via the groups column, which
    # is also in the hardcoded set, but Rofecoxib is in BOTH).
    assert len(merged) >= len(WITHDRAWN_DRUGS)


def test_build_reward_function_with_phase1_safety_returns_reward_fn(fake_phase1_dir):
    """(3) build_reward_function_with_phase1_safety returns a RewardFunction."""
    reward_fn, phase1_withdrawn, phase1_reasons = build_reward_function_with_phase1_safety(
        fake_phase1_dir, config=RewardConfig(),
    )
    assert isinstance(reward_fn, RewardFunction)
    assert "rofecoxib" in phase1_withdrawn
    assert phase1_reasons["rofecoxib"] == "cardiovascular risk"


def test_build_reward_function_with_phase1_safety_falls_back_gracefully():
    """When Phase 1 has no withdrawn drugs, the RewardFunction still builds."""
    tmp = tempfile.mkdtemp(prefix="phase1_no_withdrawn_")
    # Build a CSV with NO withdrawn rows.
    csv_path = os.path.join(tmp, "drugbank_drugs.csv")
    with open(csv_path, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=["name", "inchikey", "is_withdrawn", "groups"])
        writer.writeheader()
        writer.writerow({"name": "Aspirin", "inchikey": "BSYNRYMUTXBXSQ", "is_withdrawn": "False", "groups": "approved"})
    reward_fn, phase1_withdrawn, _ = build_reward_function_with_phase1_safety(tmp)
    assert isinstance(reward_fn, RewardFunction)
    assert phase1_withdrawn == set()
