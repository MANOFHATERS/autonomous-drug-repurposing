"""TEAMMATE-3 verification: reward function uses Phase 1 safety signals
(withdrawn drugs).

UPDATED v131: the function signatures have been updated to match the
issue spec:
  - ``load_phase1_safety_signals`` now returns 4 values:
    (withdrawn_names, withdrawn_reasons, withdrawn_countries, withdrawn_years)
  - ``build_reward_function_with_phase1_safety`` now returns a SINGLE
    RewardFunction (not a 3-tuple), and sets the following attributes
    on it:
      _withdrawn_drugs (frozenset, merged Phase 1 + hardcoded)
      _withdrawn_reasons (dict[name -> reason])
      _withdrawn_countries (dict[name -> country])
      _withdrawn_years (dict[name -> year])
      _treat_unknown_as_withdrawn (bool — default True, conservative)
      _safety_source (literal 'phase1' | 'hardcoded' | 'merged')
  - ``load_phase1_safety_signals`` now raises ``FileNotFoundError`` when
    the CSV is missing (instead of returning empty sets — the previous
    behavior silently disabled the safety guardrail).

The task spec requires:
  (1) load safety signals from Phase 1 (is_withdrawn, withdrawn_reason,
      withdrawn_country, withdrawn_year)
  (2) set safety_score=0.0 for withdrawn drugs
  (3) verify the reward function penalizes them
"""
import os
import sys
import csv
import gzip
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
            "withdrawn_country": "withdrawn_country", "withdrawn_year": "withdrawn_year",
        },
        # Approved, not withdrawn (control).
        {
            "name": "Aspirin", "inchikey": "BSYNRYMUTXBXSQ-UHFFFAOYSA-N",
            "drugbank_id": "DB00945", "is_withdrawn": "False",
            "groups": "approved", "withdrawn_reason": "",
            "withdrawn_country": "", "withdrawn_year": "",
        },
        # Withdrawn via is_withdrawn=True (rofecoxib / Vioxx).
        {
            "name": "Rofecoxib", "inchikey": "RZYAOLQLOIELDH-UHFFFAOYSA-N",
            "drugbank_id": "DB00795", "is_withdrawn": "True",
            "groups": "approved;withdrawn", "withdrawn_reason": "cardiovascular risk",
            "withdrawn_country": "US", "withdrawn_year": "2004",
        },
        # Withdrawn via groups token only (terfenadine — legacy).
        {
            "name": "Terfenadine", "inchikey": "GUGOYVSPIZAFGO-UHFFFAOYSA-N",
            "drugbank_id": "DB00842", "is_withdrawn": "False",
            "groups": "withdrawn", "withdrawn_reason": "QT prolongation",
            "withdrawn_country": "US", "withdrawn_year": "1998",
        },
        # Approved, in neither set.
        {
            "name": "Ibuprofen", "inchikey": "HEFNNWSXXWATIW-UHFFFAOYSA-N",
            "drugbank_id": "DB01050", "is_withdrawn": "False",
            "groups": "approved", "withdrawn_reason": "",
            "withdrawn_country": "", "withdrawn_year": "",
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
    withdrawn, reasons, countries, years = load_phase1_safety_signals(fake_phase1_dir)
    # Rofecoxib has is_withdrawn=True.
    assert "rofecoxib" in withdrawn, f"rofecoxib not in {withdrawn}"
    # Terfenadine is in via the groups column (handled by the next test).
    assert "terfenadine" in withdrawn, f"terfenadine not in {withdrawn}"


def test_load_phase1_safety_signals_loads_groups_column(fake_phase1_dir):
    """(1) load_phase1_safety_signals reads the groups column (legacy format).

    Terfenadine has is_withdrawn=False but groups="withdrawn". The loader
    must catch this — DrugBank sometimes populates only the groups column.
    """
    withdrawn, _, _, _ = load_phase1_safety_signals(fake_phase1_dir)
    assert "terfenadine" in withdrawn


def test_load_phase1_safety_signals_returns_reasons(fake_phase1_dir):
    """(1) load_phase1_safety_signals returns the withdrawn_reason column."""
    _, reasons, _, _ = load_phase1_safety_signals(fake_phase1_dir)
    assert "rofecoxib" in reasons
    assert reasons["rofecoxib"] == "cardiovascular risk"
    assert reasons["terfenadine"] == "QT prolongation"


def test_load_phase1_safety_signals_returns_countries_and_years(fake_phase1_dir):
    """TEAMMATE-3 v131: returns withdrawn_country and withdrawn_year columns."""
    _, _, countries, years = load_phase1_safety_signals(fake_phase1_dir)
    assert countries["rofecoxib"] == "US"
    assert countries["terfenadine"] == "US"
    assert years["rofecoxib"] == 2004
    assert years["terfenadine"] == 1998


def test_load_phase1_safety_signals_raises_on_missing_csv():
    """TEAMMATE-3 v131 ROOT FIX: missing CSV raises FileNotFoundError.

    The previous behavior returned empty sets, which silently disabled
    the safety guardrail. The root fix raises so the caller can decide
    whether to fall back to the hardcoded set or fail loudly.
    """
    tmp = tempfile.mkdtemp(prefix="phase1_empty_")
    # No CSV in the directory.
    with pytest.raises(FileNotFoundError, match="DrugBank drugs CSV not found"):
        load_phase1_safety_signals(tmp)


def test_load_phase1_safety_signals_handles_gz():
    """TEAMMATE-3 v131 ROOT FIX: handles .csv.gz files transparently."""
    import pandas as pd
    tmp = tempfile.mkdtemp(prefix="phase1_gz_")
    df = pd.DataFrame({
        "name": ["thalidomide", "aspirin"],
        "is_withdrawn": [True, False],
        "groups": ["withdrawn", "approved"],
        "withdrawn_reason": ["teratogenicity", ""],
        "withdrawn_country": ["DE", ""],
        "withdrawn_year": [1961, ""],
    })
    gz_path = os.path.join(tmp, "drugbank_drugs.csv.gz")
    with gzip.open(gz_path, "wt", encoding="utf-8") as f:
        df.to_csv(f, index=False)
    withdrawn, reasons, countries, years = load_phase1_safety_signals(tmp)
    assert "thalidomide" in withdrawn
    assert "aspirin" not in withdrawn
    assert reasons["thalidomide"] == "teratogenicity"
    assert countries["thalidomide"] == "DE"
    assert years["thalidomide"] == 1961


def test_load_phase1_safety_signals_raises_on_missing_directory():
    """A missing DIRECTORY is a configuration error (raise FileNotFoundError)."""
    with pytest.raises(FileNotFoundError, match="Phase 1 directory not found"):
        load_phase1_safety_signals("/nonexistent/path/that/does/not/exist")


def test_compute_safety_score_with_phase1_returns_zero_for_withdrawn(fake_phase1_dir):
    """(2) compute_safety_score_with_phase1 returns 0.0 for withdrawn drugs."""
    withdrawn, _, _, _ = load_phase1_safety_signals(fake_phase1_dir)
    # Rofecoxib is withdrawn via Phase 1 — must get 0.0.
    assert compute_safety_score_with_phase1("rofecoxib", withdrawn) == 0.0
    # Case-insensitive.
    assert compute_safety_score_with_phase1("ROFECOXIB", withdrawn) == 0.0
    assert compute_safety_score_with_phase1("  Rofecoxib  ", withdrawn) == 0.0


def test_compute_safety_score_with_phase1_returns_one_for_safe_drugs(fake_phase1_dir):
    """(2) compute_safety_score_with_phase1 returns 1.0 for safe drugs."""
    withdrawn, _, _, _ = load_phase1_safety_signals(fake_phase1_dir)
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
    phase1_withdrawn, _, _, _ = load_phase1_safety_signals(fake_phase1_dir)
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
    """(3) build_reward_function_with_phase1_safety returns a RewardFunction.

    TEAMMATE-3 v131: now returns a SINGLE RewardFunction (not a 3-tuple),
    with the safety attributes set.
    """
    reward_fn = build_reward_function_with_phase1_safety(
        fake_phase1_dir, config=RewardConfig(),
    )
    assert isinstance(reward_fn, RewardFunction)
    # Safety attributes must be set.
    assert hasattr(reward_fn, "_withdrawn_drugs")
    assert hasattr(reward_fn, "_withdrawn_reasons")
    assert hasattr(reward_fn, "_withdrawn_countries")
    assert hasattr(reward_fn, "_withdrawn_years")
    assert hasattr(reward_fn, "_treat_unknown_as_withdrawn")
    assert hasattr(reward_fn, "_safety_source")
    # Phase 1 data must be merged in.
    assert "rofecoxib" in reward_fn._withdrawn_drugs
    assert reward_fn._withdrawn_reasons["rofecoxib"] == "cardiovascular risk"
    assert reward_fn._withdrawn_countries["rofecoxib"] == "US"
    assert reward_fn._withdrawn_years["rofecoxib"] == 2004
    # Conservative default.
    assert reward_fn._treat_unknown_as_withdrawn is True
    # Safety source must reflect Phase 1 data was used.
    assert reward_fn._safety_source in ("merged", "phase1")


def test_build_reward_function_with_phase1_safety_falls_back_gracefully():
    """TEAMMATE-3 v131: when Phase 1 CSV is missing, falls back to hardcoded.

    The function must NOT raise — it must return a RewardFunction with
    _safety_source='hardcoded' so the caller can detect degraded mode.
    """
    tmp = tempfile.mkdtemp(prefix="phase1_no_csv_")
    # No CSV in the directory.
    reward_fn = build_reward_function_with_phase1_safety(tmp)
    assert isinstance(reward_fn, RewardFunction)
    assert reward_fn._safety_source == "hardcoded"
    assert "rofecoxib" in reward_fn._withdrawn_drugs  # from hardcoded set
    assert reward_fn._withdrawn_reasons == {}
    assert reward_fn._withdrawn_countries == {}
    assert reward_fn._withdrawn_years == {}


def test_build_reward_function_with_phase1_safety_handles_no_withdrawn_rows():
    """When Phase 1 has no withdrawn drugs, the RewardFunction still builds."""
    tmp = tempfile.mkdtemp(prefix="phase1_no_withdrawn_")
    # Build a CSV with NO withdrawn rows.
    csv_path = os.path.join(tmp, "drugbank_drugs.csv")
    with open(csv_path, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=["name", "inchikey", "is_withdrawn", "groups"])
        writer.writeheader()
        writer.writerow({"name": "Aspirin", "inchikey": "BSYNRYMUTXBXSQ", "is_withdrawn": "False", "groups": "approved"})
    reward_fn = build_reward_function_with_phase1_safety(tmp)
    assert isinstance(reward_fn, RewardFunction)
    # No Phase 1 withdrawn drugs, so safety_source is 'hardcoded'.
    assert reward_fn._safety_source == "hardcoded"
    # Hardcoded set must still be present.
    assert "rofecoxib" in reward_fn._withdrawn_drugs
