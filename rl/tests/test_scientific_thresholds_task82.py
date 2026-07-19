"""TASK 8.2 verification: evidence-based scientific thresholds exist
with the EXACT names the task spec requires.

The task spec verification command is:
    python -c "from rl.scientific_thresholds import IC50_POTENT, IC50_ACTIVE, IC50_INACTIVE; print(IC50_POTENT, IC50_ACTIVE, IC50_INACTIVE)"

We test:
  - The names exist and are importable
  - The values match the ChEMBL convention (potent < 100 nM, active
    100 nM – 1 µM, inactive > 1 µM)
  - The Kd high-affinity threshold is < 10 nM (per Kd convention, NOT
    the 100 nM ChEMBL "active" cutoff that was previously mislabeled)
  - FDA black-box-warning safety threshold exists
  - Approved-drug efficacy benchmark exists
  - Each constant has a docstring citing its source
"""
import os
import sys
import inspect

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..', '..')))

from rl.scientific_thresholds import (
    IC50_POTENT,
    IC50_ACTIVE,
    IC50_INACTIVE,
    IC50_POTENT_NM,
    IC50_ACTIVE_NM,
    IC50_INACTIVE_NM,
    KD_HIGH_AFFINITY_NM,
    KD_MODERATE_AFFINITY_NM,
    KD_LOW_AFFINITY_NM,
    BLACK_BOX_WARNING_SAFETY_THRESHOLD,
    EFFICACY_APPROVED_DRUG_BENCHMARK,
    EFFICACY_BREAKTHROUGH_THERAPY_THRESHOLD,
    SAFETY_HARD_REJECT_THRESHOLD,
    EFFICACY_MIN_CLINICAL_SIGNAL,
    EFFICACY_STRONG_CLINICAL_SIGNAL,
)
import rl.scientific_thresholds as st


def test_ic50_potent_threshold():
    """IC50_POTENT = 100 nM (ChEMBL 'active' tier — < 100 nM = potent)."""
    assert IC50_POTENT == 100.0
    assert IC50_POTENT_NM == 100.0
    # Same value (alias).
    assert IC50_POTENT is IC50_POTENT_NM or IC50_POTENT == IC50_POTENT_NM


def test_ic50_active_threshold():
    """IC50_ACTIVE = 1000 nM (1 µM — ChEMBL 'weak active' tier upper bound)."""
    assert IC50_ACTIVE == 1000.0
    assert IC50_ACTIVE_NM == 1000.0


def test_ic50_inactive_threshold():
    """IC50_INACTIVE = 10000 nM (10 µM — ChEMBL 'inactive' tier upper bound)."""
    assert IC50_INACTIVE == 10000.0
    assert IC50_INACTIVE_NM == 10000.0


def test_kd_high_affinity_threshold():
    """Kd < 10 nM = HIGH AFFINITY (IUPAC / lead-optimization standard).

    The task spec requires <10 nM for high affinity. The previous code
    had KD_STRONG_BINDING_NM = 100 nM (the ChEMBL "active" cutoff
    misapplied to Kd). The fix adds KD_HIGH_AFFINITY_NM = 10 nM.
    """
    assert KD_HIGH_AFFINITY_NM == 10.0, (
        f"Kd high-affinity threshold must be 10 nM (per Kroepl 2015 / "
        f"IUPAC Gold Book), got {KD_HIGH_AFFINITY_NM}"
    )
    # Moderate and low-affinity bands must be ABOVE the high-affinity
    # threshold (the bands are non-overlapping).
    assert KD_MODERATE_AFFINITY_NM > KD_HIGH_AFFINITY_NM
    assert KD_LOW_AFFINITY_NM > KD_MODERATE_AFFINITY_NM


def test_fda_black_box_warning_threshold():
    """BLACK_BOX_WARNING_SAFETY_THRESHOLD exists and is below the
    FAERS-based hard-reject threshold (0.5)."""
    assert BLACK_BOX_WARNING_SAFETY_THRESHOLD == 0.3
    assert BLACK_BOX_WARNING_SAFETY_THRESHOLD < SAFETY_HARD_REJECT_THRESHOLD


def test_efficacy_thresholds_exist():
    """Approved-drug efficacy benchmarks exist with FDA-cited sources."""
    assert EFFICACY_APPROVED_DRUG_BENCHMARK == 0.30
    assert EFFICACY_BREAKTHROUGH_THERAPY_THRESHOLD == 0.70
    # The existing tiers (kept for backward compat).
    assert EFFICACY_MIN_CLINICAL_SIGNAL == 0.20
    assert EFFICACY_STRONG_CLINICAL_SIGNAL == 0.50
    # Ordering: min < approved < strong < breakthrough.
    assert EFFICACY_MIN_CLINICAL_SIGNAL < EFFICACY_APPROVED_DRUG_BENCHMARK
    assert EFFICACY_APPROVED_DRUG_BENCHMARK < EFFICACY_STRONG_CLINICAL_SIGNAL
    assert EFFICACY_STRONG_CLINICAL_SIGNAL < EFFICACY_BREAKTHROUGH_THERAPY_THRESHOLD


def test_thresholds_have_source_citations_in_docstrings():
    """Every evidence-based threshold MUST cite its source in its docstring.

    The task spec requires "cite sources in docstrings." We verify each
    new constant has a docstring containing a URL or a peer-reviewed
    citation (journal name + year).

    NOTE: ``inspect.getdoc(constant)`` returns the float class docstring
    (because the constant IS a float instance), NOT the module-level
    docstring that follows the assignment. We read the source file
    directly and check that each constant assignment is followed by a
    triple-quoted docstring containing a source citation.
    """
    source_path = inspect.getsourcefile(st)
    assert source_path is not None, "cannot find rl.scientific_thresholds source"
    with open(source_path, "r", encoding="utf-8") as f:
        source = f.read()

    constants_to_check = [
        "IC50_POTENT_NM",
        "IC50_ACTIVE_NM",
        "IC50_INACTIVE_NM",
        "KD_HIGH_AFFINITY_NM",
        "BLACK_BOX_WARNING_SAFETY_THRESHOLD",
        "EFFICACY_APPROVED_DRUG_BENCHMARK",
        "EFFICACY_BREAKTHROUGH_THERAPY_THRESHOLD",
    ]
    for name in constants_to_check:
        # Find the assignment line, then check the next ~30 lines for a
        # triple-quoted docstring containing a source citation.
        assignment_idx = source.find(f"{name}:")
        if assignment_idx == -1:
            # Some constants may be assigned without annotation.
            assignment_idx = source.find(f"{name} =")
        assert assignment_idx != -1, f"{name} not found in source"

        # Take the 1500 chars after the assignment (covers the docstring).
        chunk = source[assignment_idx:assignment_idx + 1500]
        # Must contain a triple-quoted docstring.
        assert '"""' in chunk, f"{name} is not followed by a docstring"
        # Must cite either a URL or a journal/year reference.
        has_url = "http" in chunk or "https" in chunk
        has_journal = any(
            token in chunk
            for token in ["J. Med. Chem.", "FDA", "ChEMBL", "BindingDB",
                          "IUPAC", "Copeland", "Kroepl"]
        )
        assert has_url or has_journal, (
            f"{name} docstring does not cite a source (no URL or journal "
            f"reference found in the 1500 chars after the assignment). "
            f"Chunk: {chunk[:300]}..."
        )


def test_backward_compat_aliases_still_work():
    """Old names (IC50_STRONG_BINDING_NM, KD_STRONG_BINDING_NM) still import."""
    from rl.scientific_thresholds import (
        IC50_STRONG_BINDING_NM,
        IC50_MODERATE_BINDING_NM,
        IC50_WEAK_BINDING_NM,
        KD_STRONG_BINDING_NM,
        KD_MODERATE_BINDING_NM,
    )
    # Old IC50 names alias the new ones.
    assert IC50_STRONG_BINDING_NM == IC50_POTENT_NM
    assert IC50_MODERATE_BINDING_NM == IC50_ACTIVE_NM
    assert IC50_WEAK_BINDING_NM == IC50_INACTIVE_NM
    # KD_STRONG_BINDING_NM is aliased to KD_HIGH_AFFINITY_NM (10 nM)
    # — this is a BREAKING change from the previous 100 nM, but the
    # task spec required <10 nM for "high affinity" and the alias
    # name "STRONG_BINDING" was the same concept.
    assert KD_STRONG_BINDING_NM == KD_HIGH_AFFINITY_NM == 10.0
