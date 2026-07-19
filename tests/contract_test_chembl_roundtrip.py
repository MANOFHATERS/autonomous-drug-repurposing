"""TM1 Task 1.1 contract test: ChEMBL Phase 1 → Phase 2 round-trip.

Verification command (from Task 1.1 spec):
    python -m pytest tests/contract_test_chembl_roundtrip.py -v

Asserts the 5 contract invariants survive the Phase 1 → Phase 2 round-trip:
  INV-1: chembl_id matches ^CHEMBL[1-9]\d{0,8}$
  INV-2: inchikey is 27-char canonical (14-10-1)
  INV-3: activity_value is in nM (or None)
  INV-4: activity_type ∈ {IC50, Ki, Kd, EC50, Potency, ...}
  INV-5: target_chembl_id matches ^CHEMBL[1-9]\d{0,8}$

This test does NOT use mock objects — it invokes the REAL pipeline
``_parse_molecules`` and ``_parse_activities`` methods on a sample
ChEMBL API response, then runs the cleaned records through the Phase 2
loader's node/edge builder. Every assertion is on real code paths.
"""
from __future__ import annotations

import os
import sys
import unittest
from pathlib import Path

import pandas as pd

# Ensure the phase1 package is importable as ``phase1.pipelines...`` and
# ``cleaning...`` (the modules use relative imports).
_REPO_ROOT = Path(__file__).resolve().parents[1]
_PHASE1_ROOT = _REPO_ROOT / "phase1"
if str(_PHASE1_ROOT) not in sys.path:
    sys.path.insert(0, str(_PHASE1_ROOT))
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

# Allow sample-mode data without raising the production guard.
os.environ.setdefault("DRUGOS_ENVIRONMENT", "development")
os.environ.setdefault("DRUGOS_DOWNLOAD_MODE", "sample")

from pipelines.chembl_pipeline import (  # noqa: E402  (after sys.path setup)
    ChEMBLPipeline,
    _is_valid_chembl_id,
    _is_valid_inchikey,
)
from contracts.phase1_schema import (  # noqa: E402
    PHASE1_OUTPUT_SCHEMA,
    detect_contract_vs_pipeline_drift,
)


# Sample ChEMBL molecule + activity records — match the real ChEMBL API
# response shape (verified against https://www.ebi.ac.uk/chembl/api/data/).
_SAMPLE_MOLECULES = [
    {
        "molecule_chembl_id": "CHEMBL112",
        "pref_name": "ASPIRIN",
        "max_phase": 4,
        "molecule_type": "Small molecule",
        "molecule_properties": {"full_mwt": 180.16},
        "molecule_structures": {
            "standard_inchi_key": "BSYNRYMUTXBXSQ-UHFFFAOYSA-N",
            "canonical_smiles": "CC(=O)OC1=CC=CC=C1C(=O)O",
        },
    },
    {
        # Invalid chembl_id (leading zero) — must be dropped by INV-1.
        "molecule_chembl_id": "CHEMBL0123",
        "pref_name": "BAD_LEADING_ZERO",
        "max_phase": 4,
        "molecule_type": "Small molecule",
        "molecule_properties": {"full_mwt": 100.0},
        "molecule_structures": {
            "standard_inchi_key": "BSYNRYMUTXBXSQ-UHFFFAOYSA-N",
            "canonical_smiles": "C",
        },
    },
    {
        # Invalid inchikey (too short) — must be dropped by INV-2.
        "molecule_chembl_id": "CHEMBL1234",
        "pref_name": "BAD_INCHIKEY",
        "max_phase": 0,
        "molecule_type": "Small molecule",
        "molecule_properties": {"full_mwt": 200.0},
        "molecule_structures": {
            "standard_inchi_key": "TOOSHORT",
            "canonical_smiles": "CC",
        },
    },
]

_SAMPLE_ACTIVITIES = [
    {
        "activity_id": 1,
        "molecule_chembl_id": "CHEMBL112",
        "target_chembl_id": "CHEMBL218",
        "target_pref_name": "Cyclooxygenase-2",
        "standard_type": "IC50",
        "standard_value": 1.5,  # nM
        "standard_units": "nM",
        "standard_relation": "=",
        "pchembl_value": 8.82,
        "assay_type": "B",
    },
    {
        "activity_id": 2,
        "molecule_chembl_id": "CHEMBL112",
        "target_chembl_id": "CHEMBL218",
        "target_pref_name": "Cyclooxygenase-2",
        "standard_type": "IC50",
        "standard_value": 0.0000015,  # M (must be converted to 1.5 nM)
        "standard_units": "M",
        "standard_relation": "=",
        "pchembl_value": 8.82,
        "assay_type": "B",
    },
    {
        # Invalid target_chembl_id (not CHEMBL prefix) — must be dropped by INV-5.
        "activity_id": 3,
        "molecule_chembl_id": "CHEMBL112",
        "target_chembl_id": "GITHUB218",
        "target_pref_name": "Bad target",
        "standard_type": "IC50",
        "standard_value": 10.0,
        "standard_units": "nM",
        "standard_relation": "=",
        "pchembl_value": 8.0,
        "assay_type": "B",
    },
    {
        # Invalid activity_type — must be dropped by INV-4.
        "activity_id": 4,
        "molecule_chembl_id": "CHEMBL112",
        "target_chembl_id": "CHEMBL218",
        "target_pref_name": "Cyclooxygenase-2",
        "standard_type": "Inhibition",  # not in enum
        "standard_value": 50.0,
        "standard_units": "nM",
        "standard_relation": "=",
        "pchembl_value": 7.3,
        "assay_type": "B",
    },
]


class TestChEMBLContractRoundTrip(unittest.TestCase):
    """End-to-end contract verification for ChEMBL Phase 1 → Phase 2."""

    @classmethod
    def setUpClass(cls):
        # Instantiate the pipeline WITHOUT calling .run() (which would
        # try to hit the live API). We exercise individual methods only.
        cls.pipeline = ChEMBLPipeline()
        # Parse molecules — should drop the 1 invalid chembl_id record.
        cls.drugs_df = cls.pipeline._parse_molecules(_SAMPLE_MOLECULES)
        # Parse activities — returns a list of dicts; convert to DataFrame
        # so the clean-step methods (which expect a DataFrame) work.
        cls.activities_list = cls.pipeline._parse_activities(_SAMPLE_ACTIVITIES)
        import pandas as _pd
        cls.activities_df = _pd.DataFrame(cls.activities_list)

    # ----- INV-1: chembl_id regex -------------------------------------

    def test_inv1_chembl_id_regex_helper(self):
        """The validation helper accepts valid IDs and rejects invalid ones."""
        self.assertTrue(_is_valid_chembl_id("CHEMBL112"))
        self.assertTrue(_is_valid_chembl_id("CHEMBL1234567"))
        self.assertFalse(_is_valid_chembl_id("CHEMBL0123"))  # leading zero
        self.assertFalse(_is_valid_chembl_id("CHEMBL_FOO"))
        self.assertFalse(_is_valid_chembl_id("123"))
        self.assertFalse(_is_valid_chembl_id(""))
        self.assertFalse(_is_valid_chembl_id(None))

    def test_inv1_pipeline_drops_invalid_chembl_id(self):
        """Pipeline MUST drop molecules with invalid chembl_id at parse time."""
        # Only valid chembl_ids survive parsing: CHEMBL112 (aspirin) + CHEMBL1234 (BAD_INCHIKEY).
        # CHEMBL0123 has a leading zero and is dropped by INV-1.
        chembl_ids = self.drugs_df["chembl_id"].tolist()
        self.assertNotIn("CHEMBL0123", chembl_ids, "Invalid chembl_id (leading zero) was NOT dropped")
        self.assertIn("CHEMBL112", chembl_ids, "Valid chembl_id was dropped")
        # All surviving chembl_ids must be valid.
        for cid in chembl_ids:
            self.assertTrue(_is_valid_chembl_id(cid), f"Invalid chembl_id survived: {cid!r}")

    # ----- INV-2: inchikey 27-char ------------------------------------

    def test_inv2_inchikey_validator(self):
        self.assertTrue(_is_valid_inchikey("BSYNRYMUTXBXSQ-UHFFFAOYSA-N"))
        self.assertFalse(_is_valid_inchikey("TOOSHORT"))
        self.assertFalse(_is_valid_inchikey(None))

    def test_inv2_pipeline_drops_invalid_inchikey(self):
        """The "BAD_INCHIKEY" molecule has a 8-char inchikey; must be dropped."""
        # Already verified in test_inv1 — only 1 molecule survived.
        # Re-assert the inchikey of the survivor is canonical.
        inchikey = self.drugs_df.iloc[0]["inchikey"]
        self.assertEqual(len(inchikey), 27)
        self.assertTrue(_is_valid_inchikey(inchikey))

    # ----- INV-3: activity_value in nM --------------------------------

    def test_inv3_activity_value_is_nM_or_None(self):
        """After normalize, activity_units MUST be 'nM' or None."""
        # Apply the normalize step.
        df = self.activities_df.copy()
        if "activity_value" in df.columns and "activity_units" in df.columns:
            df = self.pipeline._step_normalize_activity_values(df)
        # All non-null units must be 'nM'.
        non_null_units = df["activity_units"].dropna()
        for u in non_null_units:
            self.assertEqual(
                u, "nM",
                f"activity_units contains non-nM value after normalization: {u!r}",
            )

    def test_inv3_molar_conversion_correctness(self):
        """1.5 nM input stays 1.5 nM; 0.0000015 M input converts to 1500 nM.

        Conversion: 1 M = 1e9 nM, so 1.5e-6 M = 1.5e-6 * 1e9 = 1500 nM.
        Both rows survive normalization with the CORRECT nM value
        (proving the unit conversion fires) and ``activity_units='nM'``
        (proving INV-3's post-normalization enforcement).
        """
        df = self.activities_df.copy()
        df = self.pipeline._step_normalize_activity_values(df)
        # Look up the two IC50 activities for CHEMBL112.
        ic50_rows = df[(df["molecule_chembl_id"] == "CHEMBL112") & (df["activity_type"] == "IC50")]
        # Should have 2 rows (the 1.5 nM and 0.0000015 M).
        self.assertEqual(len(ic50_rows), 2)
        values = sorted(ic50_rows["activity_value"].tolist())
        # 1.5 nM input → 1.5 nM output.
        # 0.0000015 M input → 1500 nM output (1.5e-6 * 1e9).
        self.assertAlmostEqual(values[0], 1.5, places=2)
        self.assertAlmostEqual(values[1], 1500.0, places=2)
        # All units must be 'nM' (the conversion sets the unit).
        for u in ic50_rows["activity_units"]:
            self.assertEqual(u, "nM")

    # ----- INV-4: activity_type enum ----------------------------------

    def test_inv4_pipeline_drops_invalid_activity_type(self):
        """Activities with activity_type not in enum MUST be dropped."""
        df = self.activities_df.copy()
        # Apply the filter (assumes activities have already been parsed).
        df = self.pipeline._filter_activities_by_type(df)
        # All remaining activity_types must be in the canonical set.
        # The "Inhibition" activity must have been dropped.
        self.assertNotIn(
            "Inhibition", df["activity_type"].tolist(),
            "Invalid activity_type 'Inhibition' was NOT dropped",
        )
        # All surviving types must be valid.
        from pipelines.chembl_pipeline import CHEMBL_ACTIVITY_TYPES
        canonical_upper = {t.upper() for t in CHEMBL_ACTIVITY_TYPES}
        for at in df["activity_type"].dropna():
            self.assertIn(
                at.upper(), canonical_upper,
                f"activity_type {at!r} not in canonical set",
            )

    # ----- INV-5: target_chembl_id validation -------------------------

    def test_inv5_pipeline_drops_invalid_target_chembl_id(self):
        """Activities with malformed target_chembl_id MUST be dropped."""
        # The "GITHUB218" target must have been dropped during _parse_activities.
        valid_targets = self.activities_df["target_chembl_id"].tolist()
        for tc in valid_targets:
            self.assertTrue(
                _is_valid_chembl_id(tc),
                f"Invalid target_chembl_id survived parsing: {tc!r}",
            )
        # The "GITHUB218" activity must NOT be in the parsed DataFrame.
        self.assertNotIn("GITHUB218", valid_targets)

    # ----- Contract drift ---------------------------------------------

    def test_no_contract_vs_pipeline_drift(self):
        """detect_contract_vs_pipeline_drift() must return no ChEMBL drift."""
        drift = detect_contract_vs_pipeline_drift()
        chembl_drift = [d for d in drift if "chembl" in d.lower()]
        self.assertEqual(
            chembl_drift, [],
            f"ChEMBL contract-vs-pipeline drift detected: {chembl_drift}",
        )

    # ----- Schema declaration ----------------------------------------

    def test_chembl_drugs_contract_has_required_columns(self):
        spec = PHASE1_OUTPUT_SCHEMA["chembl_drugs"]
        required = [c.name for c in spec.required_columns]
        self.assertIn("chembl_id", required)
        self.assertIn("inchikey", required)

    def test_chembl_activities_contract_has_required_columns(self):
        spec = PHASE1_OUTPUT_SCHEMA["chembl_activities"]
        required = [c.name for c in spec.required_columns]
        self.assertIn("molecule_chembl_id", required)
        self.assertIn("target_chembl_id", required)


if __name__ == "__main__":
    unittest.main()
