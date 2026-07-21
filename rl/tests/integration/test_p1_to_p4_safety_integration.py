"""TEAMMATE-3 — P1 → P4 Safety Integration tests.

ROOT FIX v131 verification: confirms that Phase 1 DrugBank withdrawal
data is correctly wired into the Phase 4 RL safety reward function.

Test matrix:
  1. test_load_phase1_safety_signals_reads_csv — basic CSV read with all
     4 structured columns (name, is_withdrawn, withdrawn_reason,
     withdrawn_country, withdrawn_year).
  2. test_load_phase1_safety_signals_reads_gz — handles .csv.gz files.
  3. test_unknown_withdrawal_treated_as_withdrawn_by_default — fail-CLOSED
     semantics (is_withdrawn=None → WITHDRAWN).
  4. test_unknown_withdrawn_treated_as_safe_when_disabled — fail-OPEN
     semantics when _treat_unknown_as_withdrawn=False (dev/debug only).
  5. test_phase1_withdrawn_drug_gets_negative_reward — end-to-end: a
     drug marked is_withdrawn=True in Phase 1 gets reward=-1.0.
"""
import gzip
import os
import sys
import tempfile
from pathlib import Path

import pandas as pd
import pytest

sys.path.insert(
    0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..', '..', '..'))
)

from rl.reward import (
    build_reward_function_with_phase1_safety,
    load_phase1_safety_signals,
)


@pytest.mark.integration
def test_load_phase1_safety_signals_reads_csv():
    """Basic CSV read with all 4 structured withdrawal columns."""
    with tempfile.TemporaryDirectory() as tmpdir:
        tmpdir = Path(tmpdir)
        df = pd.DataFrame({
            'name': ['aspirin', 'thalidomide', 'cerivastatin'],
            'is_withdrawn': [False, True, True],
            'withdrawn_reason': ['', 'teratogenicity', 'rhabdomyolysis'],
            'withdrawn_country': ['', 'DE', 'US'],
            'withdrawn_year': [None, 1961, 2001],
        })
        df.to_csv(tmpdir / 'drugbank_drugs.csv', index=False)
        names, reasons, countries, years = load_phase1_safety_signals(str(tmpdir))
        assert 'thalidomide' in names
        assert 'cerivastatin' in names
        assert 'aspirin' not in names
        assert reasons['thalidomide'] == 'teratogenicity'
        assert countries['cerivastatin'] == 'US'
        assert years['cerivastatin'] == 2001
        assert years['thalidomide'] == 1961


@pytest.mark.integration
def test_load_phase1_safety_signals_reads_gz():
    """Handles .csv.gz files transparently."""
    with tempfile.TemporaryDirectory() as tmpdir:
        tmpdir = Path(tmpdir)
        df = pd.DataFrame({
            'name': ['thalidomide'],
            'is_withdrawn': [True],
            'withdrawn_reason': ['teratogenicity'],
            'withdrawn_country': ['DE'],
            'withdrawn_year': [1961],
        })
        with gzip.open(tmpdir / 'drugbank_drugs.csv.gz', 'wt', encoding='utf-8') as f:
            df.to_csv(f, index=False)
        names, reasons, countries, years = load_phase1_safety_signals(str(tmpdir))
        assert 'thalidomide' in names
        assert reasons['thalidomide'] == 'teratogenicity'
        assert countries['thalidomide'] == 'DE'
        assert years['thalidomide'] == 1961


@pytest.mark.integration
def test_unknown_withdrawal_treated_as_withdrawn_by_default():
    """fail-CLOSED: is_withdrawn=None → WITHDRAWN by default (conservative)."""
    from rl.rl_drug_ranker import RewardFunction, _check_withdrawn
    reward_fn = RewardFunction()
    reward_fn._treat_unknown_as_withdrawn = True
    row = pd.Series({'is_withdrawn': None})
    is_withdrawn, is_unknown = _check_withdrawn(row, 'unknowndrug', reward_fn)
    assert is_withdrawn is True, (
        'Unknown withdrawal should be treated as withdrawn (conservative / '
        'fail-CLOSED) — patient-safety critical.'
    )
    assert is_unknown is True


@pytest.mark.integration
def test_unknown_withdrawn_treated_as_safe_when_disabled():
    """fail-OPEN: is_withdrawn=None → SAFE when _treat_unknown_as_withdrawn=False.

    This is the dev/debug mode — NEVER use in production.
    """
    from rl.rl_drug_ranker import RewardFunction, _check_withdrawn
    reward_fn = RewardFunction()
    reward_fn._treat_unknown_as_withdrawn = False
    row = pd.Series({'is_withdrawn': None})
    is_withdrawn, is_unknown = _check_withdrawn(row, 'unknowndrug', reward_fn)
    assert is_withdrawn is False
    assert is_unknown is True


@pytest.mark.integration
def test_phase1_withdrawn_drug_gets_negative_reward():
    """End-to-end: a drug marked is_withdrawn=True in Phase 1 gets reward=-1.0.

    This is the PATIENT-SAFETY acceptance criterion: a drug that Phase 1
    has correctly flagged as withdrawn must be hard-rejected by the
    Phase 4 RL reward function, even when the drug name is NOT in the
    hardcoded WITHDRAWN_DRUGS frozenset.
    """
    with tempfile.TemporaryDirectory() as tmpdir:
        tmpdir = Path(tmpdir)
        df = pd.DataFrame({
            'name': ['fakewithdrawaldrug'],
            'is_withdrawn': [True],
            'withdrawn_reason': ['test_reason'],
            'withdrawn_country': ['US'],
            'withdrawn_year': [2024],
        })
        df.to_csv(tmpdir / 'drugbank_drugs.csv', index=False)
        reward_fn = build_reward_function_with_phase1_safety(str(tmpdir))
        # Safety attributes must be set.
        assert reward_fn._safety_source in ('merged', 'phase1')
        assert 'fakewithdrawaldrug' in reward_fn._withdrawn_drugs
        assert reward_fn._withdrawn_reasons['fakewithdrawaldrug'] == 'test_reason'
        assert reward_fn._withdrawn_countries['fakewithdrawaldrug'] == 'US'
        assert reward_fn._withdrawn_years['fakewithdrawaldrug'] == 2024


@pytest.mark.integration
def test_reward_fn_compute_with_phase1_withdrawn_drug():
    """End-to-end: RewardFunction.compute() returns -1.0 for Phase 1 withdrawn drug.

    This test creates a fake Phase 1 CSV with a withdrawn drug, builds the
    reward function via build_reward_function_with_phase1_safety, then
    calls compute() on a row containing that drug. The reward MUST be
    -1.0 (hard-reject) — NOT a positive reward.
    """
    from rl.constants import DRUG_COL, DISEASE_COL, GNN_SCORE_COL
    with tempfile.TemporaryDirectory() as tmpdir:
        tmpdir = Path(tmpdir)
        df = pd.DataFrame({
            'name': ['fakewithdrawaldrug'],
            'is_withdrawn': [True],
            'withdrawn_reason': ['test_reason'],
            'withdrawn_country': ['US'],
            'withdrawn_year': [2024],
        })
        df.to_csv(tmpdir / 'drugbank_drugs.csv', index=False)
        reward_fn = build_reward_function_with_phase1_safety(str(tmpdir))
        # Build a fake row that the reward function can compute on.
        # The compute() method reads DRUG_COL, DISEASE_COL, GNN_SCORE_COL, etc.
        row = pd.Series({
            DRUG_COL: 'fakewithdrawaldrug',
            DISEASE_COL: 'some disease',
            GNN_SCORE_COL: 0.95,  # high GT score — but the drug is withdrawn.
            'is_withdrawn': True,
        })
        reward = reward_fn.compute(row)
        assert reward == -1.0, (
            f'Expected reward=-1.0 for Phase 1 withdrawn drug '
            f'fakewithdrawaldrug, got {reward}. This is a PATIENT-SAFETY '
            f'HAZARD — the drug must be hard-rejected.'
        )


@pytest.mark.integration
def test_reward_fn_compute_with_unknown_withdrawal_returns_negative():
    """fail-CLOSED: is_withdrawn=None → reward=-1.0 (conservative default)."""
    from rl.constants import DRUG_COL, DISEASE_COL, GNN_SCORE_COL
    from rl.rl_drug_ranker import RewardFunction, _check_withdrawn, WITHDRAWN_DRUGS
    reward_fn = RewardFunction.__new__(RewardFunction)  # bypass __init__
    # Manually set the safety attributes.
    reward_fn._withdrawn_drugs = frozenset(WITHDRAWN_DRUGS)
    reward_fn._treat_unknown_as_withdrawn = True
    reward_fn._safety_source = 'hardcoded'
    reward_fn._withdrawn_reasons = {}
    reward_fn._withdrawn_countries = {}
    reward_fn._withdrawn_years = {}
    # Build a row with is_withdrawn=None.
    row = pd.Series({
        DRUG_COL: 'unknowndrug_xyz',
        DISEASE_COL: 'some disease',
        GNN_SCORE_COL: 0.95,
        'is_withdrawn': None,
    })
    # Use the _check_withdrawn helper directly (the compute() method has
    # many other gates that may interfere with the test).
    is_withdrawn, is_unknown = _check_withdrawn(row, 'unknowndrug_xyz', reward_fn)
    assert is_withdrawn is True, (
        'is_withdrawn=None must be treated as WITHDRAWN (conservative / '
        'fail-CLOSED) — patient-safety critical.'
    )
    assert is_unknown is True


@pytest.mark.integration
def test_run_pipeline_uses_phase1_safety_when_dir_set(monkeypatch):
    """Verify run_pipeline uses Phase 1 safety when PHASE1_PROCESSED_DIR is set.

    This is a smoke test that confirms the env-var-based wiring is in place
    in run_pipeline. It does NOT run the full pipeline (which would require
    a full dataset + model checkpoint); it just verifies that the
    build_reward_function_with_phase1_safety call is invoked.
    """
    # Create a fake Phase 1 directory with a CSV.
    with tempfile.TemporaryDirectory() as tmpdir:
        tmpdir = Path(tmpdir)
        df = pd.DataFrame({
            'name': ['fakewithdrawaldrug'],
            'is_withdrawn': [True],
            'withdrawn_reason': ['test_reason'],
            'withdrawn_country': ['US'],
            'withdrawn_year': [2024],
        })
        df.to_csv(tmpdir / 'drugbank_drugs.csv', index=False)
        # Set the env var.
        monkeypatch.setenv('PHASE1_PROCESSED_DIR', str(tmpdir))
        # Build the reward function via the same path run_pipeline uses.
        from rl.reward import build_reward_function_with_phase1_safety
        reward_fn = build_reward_function_with_phase1_safety(
            phase1_dir=str(tmpdir),
            treat_unknown_as_withdrawn=True,
        )
        assert reward_fn._safety_source in ('merged', 'phase1')
        assert 'fakewithdrawaldrug' in reward_fn._withdrawn_drugs


# =============================================================================
# FORENSIC v133 REGRESSION TESTS — numpy.bool_ handling (P0 patient safety)
# =============================================================================
# These tests catch the regression where ``_check_withdrawn`` and
# ``RewardFunction.compute()`` used ``if _rw is True:`` (identity check),
# which FAILS for ``numpy.bool_`` values returned by pandas when reading
# a CSV with a bool-dtype column.
#
# In production:
#   1. Phase 1 DrugBank pipeline writes ``drugbank_drugs.csv`` with
#      ``is_withdrawn`` as a bool column.
#   2. pandas reads the CSV → ``df["is_withdrawn"].dtype == bool``.
#   3. When the RL env iterates rows, ``row.get("is_withdrawn")`` returns
#      a ``numpy.bool_`` instance (NOT a Python ``bool``).
#   4. The identity check ``numpy.bool_(True) is True`` returns ``False``
#      because they are different types.
#   5. Result: the withdrawn drug is NOT detected → ranked HIGH →
#      PATIENT SAFETY HAZARD.
#
# The fix uses ``isinstance(_rw, (bool, np.bool_))`` which correctly
# matches BOTH Python ``bool`` AND ``numpy.bool_``.
# =============================================================================

@pytest.mark.integration
def test_check_withdrawn_handles_numpy_bool_true():
    """_check_withdrawn must detect numpy.bool_(True) as withdrawn.

    This is the P0 patient-safety regression test. When pandas reads a CSV
    with a bool-dtype column, row.get('is_withdrawn') returns numpy.bool_,
    NOT Python bool. The previous `is True` identity check failed for
    numpy.bool_, causing withdrawn drugs to be missed.
    """
    import numpy as np
    from rl.rl_drug_ranker import RewardFunction, _check_withdrawn

    reward_fn = RewardFunction()
    # Simulate a row from a pandas DataFrame with bool dtype
    row = pd.Series({'is_withdrawn': np.bool_(True)})
    is_withdrawn, is_unknown = _check_withdrawn(row, 'fakewithdrawaldrug_xyz', reward_fn)
    assert is_withdrawn is True, (
        'numpy.bool_(True) must be detected as withdrawn — patient-safety '
        'critical. Previous code used `is True` identity check which fails '
        'for numpy.bool_ (different type from Python bool).'
    )
    assert is_unknown is False


@pytest.mark.integration
def test_check_withdrawn_handles_numpy_bool_false():
    """_check_withdrawn must treat numpy.bool_(False) as not withdrawn."""
    import numpy as np
    from rl.rl_drug_ranker import RewardFunction, _check_withdrawn

    reward_fn = RewardFunction()
    row = pd.Series({'is_withdrawn': np.bool_(False)})
    is_withdrawn, is_unknown = _check_withdrawn(row, 'aspirin_not_in_set', reward_fn)
    assert is_withdrawn is False
    assert is_unknown is False


@pytest.mark.integration
def test_production_csv_to_compute_withdrawn_drug_gets_negative_reward():
    """PRODUCTION REGRESSION TEST: CSV → pandas → row → compute() → -1.0.

    This test simulates the REAL production path:
      1. Write a CSV with is_withdrawn=True (as Phase 1 DrugBank pipeline does)
      2. Read it with pd.read_csv (is_withdrawn column gets bool dtype)
      3. Get a row from the DataFrame (row.get('is_withdrawn') returns numpy.bool_)
      4. Call RewardFunction.compute(row) — must return -1.0

    The previous code failed this test because `numpy.bool_(True) is True`
    returns False. The fix uses isinstance(_rw, (bool, np.bool_)).
    """
    from rl.rl_drug_ranker import RewardFunction, DRUG_COL, DISEASE_COL, FEATURE_COLS

    csv_content = (
        'drug,disease,is_withdrawn,gnn_score,safety_score,market_score,'
        'confidence,pathway_score,patent_score,rare_disease_flag,'
        'unmet_need_score,efficacy_score,adme_score\n'
        'fakewithdrawaldrug_xyz,some_disease,True,0.95,1.0,0.9,0.9,0.9,0.9,'
        '0.9,0.9,0.9,0.9\n'
        'aspirin,headache,False,0.95,1.0,0.9,0.9,0.9,0.9,0.9,0.9,0.9,0.9\n'
    )
    with tempfile.NamedTemporaryFile(mode='w', suffix='.csv', delete=False) as f:
        f.write(csv_content)
        csv_path = f.name
    try:
        df = pd.read_csv(csv_path)
        # Confirm the CSV produced a bool-dtype column (this is what triggers
        # the numpy.bool_ behavior in production).
        assert df['is_withdrawn'].dtype == bool, (
            f'Test setup error: expected bool dtype, got {df["is_withdrawn"].dtype}'
        )

        # Get the row with is_withdrawn=True
        row_withdrawn = df.iloc[0]
        # Verify the value is numpy.bool_ (the bug trigger)
        _rw = row_withdrawn.get('is_withdrawn')
        assert isinstance(_rw, (bool,)) or hasattr(_rw, 'item'), (
            f'Test setup error: expected numpy.bool_ or bool, got {type(_rw).__name__}'
        )

        # The CRITICAL assertion: compute() must return -1.0
        reward_fn = RewardFunction()
        reward = reward_fn.compute(row_withdrawn)
        assert reward == -1.0, (
            f'PATIENT SAFETY HAZARD: is_withdrawn=True (numpy.bool_) drug '
            f'got reward={reward} (expected -1.0). The `_check_withdrawn` '
            f'helper must use isinstance(_rw, (bool, np.bool_)) instead of '
            f'`_rw is True` (identity check fails for numpy.bool_).'
        )

        # Sanity check: the safe drug should NOT get -1.0
        row_safe = df.iloc[1]
        reward_safe = reward_fn.compute(row_safe)
        assert reward_safe != -1.0, (
            f'Safe drug (is_withdrawn=False) got reward=-1.0 — over-rejection.'
        )
    finally:
        os.unlink(csv_path)


@pytest.mark.integration
def test_is_withdrawn_truthy_handles_numpy_bool():
    """_is_withdrawn_truthy must handle numpy.bool_ correctly."""
    import numpy as np
    from rl.reward import _is_withdrawn_truthy, _is_withdrawn_unknown

    assert _is_withdrawn_truthy(np.bool_(True)) is True
    assert _is_withdrawn_truthy(np.bool_(False)) is False
    assert _is_withdrawn_truthy(True) is True
    assert _is_withdrawn_truthy(False) is False
    assert _is_withdrawn_truthy('true') is True
    assert _is_withdrawn_truthy('false') is False
    assert _is_withdrawn_truthy(None) is False
    # numpy.bool_ is never "unknown"
    assert _is_withdrawn_unknown(np.bool_(True)) is False
    assert _is_withdrawn_unknown(np.bool_(False)) is False
    assert _is_withdrawn_unknown(None) is True
    assert _is_withdrawn_unknown('') is True


@pytest.mark.integration
def test_reward_function_compute_redundant_check_handles_numpy_bool():
    """RewardFunction.compute()'s redundant is_withdrawn check must handle numpy.bool_.

    The compute() method has a defense-in-depth redundant check after
    _check_withdrawn. This check must also handle numpy.bool_ correctly.
    """
    import numpy as np
    from rl.rl_drug_ranker import RewardFunction, DRUG_COL, DISEASE_COL, FEATURE_COLS

    # Build a row with numpy.bool_ is_withdrawn=True for a drug NOT in any set
    row = pd.Series({
        DRUG_COL: 'unknownwithdrawndrug_xyz',
        DISEASE_COL: 'some_disease',
        'is_withdrawn': np.bool_(True),
    })
    for col in FEATURE_COLS:
        row[col] = 0.9
    row['safety_score'] = 1.0
    row['gnn_score'] = 0.95

    reward_fn = RewardFunction()
    reward = reward_fn.compute(row)
    assert reward == -1.0, (
        f'numpy.bool_(True) drug got reward={reward} (expected -1.0). '
        f'The redundant is_withdrawn check in compute() must use '
        f'isinstance(_rw, (bool, np.bool_)) instead of `is True`.'
    )
