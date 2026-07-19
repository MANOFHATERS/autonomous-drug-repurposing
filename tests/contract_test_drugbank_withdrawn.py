"""TM1 Task 1.2 contract test: DrugBank withdrawn-drug Phase 1 → Phase 2 → Phase 4.

Verification command (from Task 1.2 spec):
    python -m pytest tests/contract_test_drugbank_withdrawn.py -v

Asserts the 5 contract invariants for the withdrawn-drug safety flow:
  INV-1: DrugBank XML <withdrawn-notice> is parsed for reason/country/year
  INV-2: Drug model (importable from phase1_schema) has is_withdrawn
  INV-3: drugbank_drugs.csv writes all 4 withdrawn columns
  INV-4: phase2 drugbank_parser reads all 4 withdrawn columns
  INV-5: RL ranker uses is_withdrawn from input row (not just frozenset)

This test exercises REAL code paths: parses the sample DrugBank XML,
runs the bridge, and verifies the RL reward function on a row with
is_withdrawn=True but a drug_name NOT in the hardcoded WITHDRAWN_DRUGS
frozenset.
"""
from __future__ import annotations

import os
import sys
import unittest
from pathlib import Path

import pandas as pd

_REPO_ROOT = Path(__file__).resolve().parents[1]
_PHASE1_ROOT = _REPO_ROOT / "phase1"
if str(_PHASE1_ROOT) not in sys.path:
    sys.path.insert(0, str(_PHASE1_ROOT))
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

os.environ.setdefault("DRUGOS_ENVIRONMENT", "development")
os.environ.setdefault("DRUGOS_DOWNLOAD_MODE", "sample")

from pipelines.drugbank_pipeline import DrugBankPipeline  # noqa: E402
from contracts.phase1_schema import (  # noqa: E402
    Drug,
    PHASE1_OUTPUT_SCHEMA,
    detect_contract_vs_pipeline_drift,
)

_DRUGBANK_SAMPLE_XML = _PHASE1_ROOT / "tests" / "fixtures" / "drugbank_sample.xml"


class TestDrugBankWithdrawnContract(unittest.TestCase):
    """Verify the full Phase 1 → Phase 2 → Phase 4 withdrawn-drug flow."""

    @classmethod
    def setUpClass(cls):
        # Read the sample DrugBank XML (has multiple drugs including cerivastatin).
        # The cerivastatin entry now includes <withdrawn-notice> elements.
        cls.pipeline = DrugBankPipeline()
        from pipelines.drugbank_pipeline import NS
        from lxml import etree
        tree = etree.parse(str(_DRUGBANK_SAMPLE_XML))
        cls.drug_elements = tree.findall(".//db:drug", NS)
        cls.parsed_drugs = []
        for elem in cls.drug_elements:
            result = cls.pipeline._parse_drug_element(elem)
            # _parse_drug_element may return None for deliberately-malformed
            # fixture entries (e.g. drug with no drugbank-id). Skip those.
            if result is None:
                continue
            drug_rec, _ = result
            if drug_rec is None:
                continue
            cls.parsed_drugs.append(drug_rec)
        cls.drugs_df = pd.DataFrame(cls.parsed_drugs)

    # ----- INV-1: <withdrawn-notice> XML parsing ----------------------

    def test_inv1_withdrawn_notice_extracted_from_xml(self):
        """Cerivastatin (DB00463) has <withdrawn-notice> in the fixture."""
        cerivastatin = next(
            d for d in self.parsed_drugs if d.get("drugbank_id") == "DB00463"
        )
        self.assertTrue(cerivastatin["is_withdrawn"], "is_withdrawn must be True for cerivastatin")
        self.assertEqual(
            cerivastatin["withdrawn_reason"], "rhabdomyolysis",
            f"withdrawn_reason must be 'rhabdomyolysis', got {cerivastatin['withdrawn_reason']!r}",
        )
        self.assertEqual(
            cerivastatin["withdrawn_year"], 2001,
            f"withdrawn_year must be 2001, got {cerivastatin['withdrawn_year']!r}",
        )
        # withdrawn_country is "DE;US" (sorted, semicolon-separated).
        self.assertIsNotNone(cerivastatin["withdrawn_country"])
        self.assertIn("US", cerivastatin["withdrawn_country"])
        self.assertIn("DE", cerivastatin["withdrawn_country"])

    def test_inv1_non_withdrawn_drug_has_none_fields(self):
        """Aspirin (DB00645) is NOT withdrawn — withdrawn_* fields must be None."""
        aspirin = next(
            d for d in self.parsed_drugs if d.get("drugbank_id") == "DB00645"
        )
        self.assertFalse(aspirin["is_withdrawn"])
        self.assertIsNone(aspirin["withdrawn_reason"])
        self.assertIsNone(aspirin["withdrawn_country"])
        self.assertIsNone(aspirin["withdrawn_year"])

    # ----- INV-2: Drug model has is_withdrawn -------------------------

    def test_inv2_drug_class_importable_from_phase1_schema(self):
        """``from phase1.contracts.phase1_schema import Drug`` MUST succeed."""
        self.assertIsNotNone(Drug, "Drug class must be importable from phase1_schema")

    def test_inv2_drug_model_has_is_withdrawn(self):
        """Drug SQLAlchemy model must have is_withdrawn attribute."""
        self.assertTrue(
            hasattr(Drug, "is_withdrawn"),
            "Drug model must have 'is_withdrawn' column",
        )

    def test_inv2_drug_model_has_structured_withdrawn_fields(self):
        """Drug model must have withdrawn_reason / withdrawn_country / withdrawn_year."""
        for field in ("withdrawn_reason", "withdrawn_country", "withdrawn_year"):
            self.assertTrue(
                hasattr(Drug, field),
                f"Drug model must have '{field}' column (TM1 Task 1.2 ROOT FIX)",
            )

    # ----- INV-3: drugbank_drugs.csv has 4 withdrawn columns ----------

    def test_inv3_csv_columns_include_all_withdrawn_fields(self):
        """drugs_df must have is_withdrawn + withdrawn_reason/country/year columns."""
        for col in ("is_withdrawn", "withdrawn_reason", "withdrawn_country", "withdrawn_year"):
            self.assertIn(
                col, self.drugs_df.columns,
                f"Column {col!r} missing from drugbank_drugs.csv output",
            )

    def test_inv3_get_processed_columns_lists_withdrawn_fields(self):
        from pipelines.drugbank_pipeline import _get_processed_columns
        cols = _get_processed_columns("drugs")
        for col in ("is_withdrawn", "withdrawn_reason", "withdrawn_country", "withdrawn_year"):
            self.assertIn(col, cols)

    # ----- INV-4: phase2 drugbank_parser reads 4 withdrawn columns ----

    def test_inv4_phase2_loader_propagates_withdrawn_fields(self):
        """drugbank_to_node_records_from_phase1 must read all 4 fields."""
        # Add phase2 to path so drugos_graph is importable.
        _P2_ROOT = _REPO_ROOT / "phase2"
        if str(_P2_ROOT) not in sys.path:
            sys.path.insert(0, str(_P2_ROOT))
        from drugos_graph.drugbank_parser import drugbank_to_node_records_from_phase1
        nodes = drugbank_to_node_records_from_phase1(self.drugs_df)
        cerivastatin_node = next(n for n in nodes if n.get("drugbank_id") == "DB00463")
        self.assertTrue(cerivastatin_node["withdrawn"], "withdrawn must be True")
        self.assertEqual(cerivastatin_node["withdrawn_reason"], "rhabdomyolysis")
        self.assertEqual(cerivastatin_node["withdrawn_year"], 2001)
        self.assertIn("US", cerivastatin_node["withdrawn_country"])

    # ----- INV-5: RL ranker uses is_withdrawn from row ----------------

    def test_inv5_rl_ranker_rejects_row_with_is_withdrawn_true(self):
        """A row with is_withdrawn=True MUST be rejected even if drug_name
        is NOT in the hardcoded WITHDRAWN_DRUGS frozenset.

        This is the patient-safety critical test: a newly-withdrawn drug
        that Phase 1/Phase 2 have correctly flagged is_withdrawn=True
        must be rejected by the RL ranker, regardless of whether the
        drug name has been added to the manual frozenset.
        """
        # Add phase4 root to path.
        _P4_ROOT = _REPO_ROOT / "rl"
        if str(_P4_ROOT) not in sys.path:
            sys.path.insert(0, str(_P4_ROOT.parent))
        from rl.rl_drug_ranker import RewardFunction, RewardConfig, WITHDRAWN_DRUGS

        # A drug_name NOT in the frozenset.
        novel_drug_name = "fake_withdrawn_drug_xyz_not_in_frozenset"
        self.assertNotIn(novel_drug_name, WITHDRAWN_DRUGS,
                         "Test setup error: drug name should NOT be in frozenset")

        # Construct a row that would otherwise be a viable candidate.
        row = pd.Series({
            "drug_name": novel_drug_name,
            "disease_name": "headache",
            "gnn_score": 0.85,
            "safety_score": 0.9,
            "market_score": 0.5,
            "is_withdrawn": True,  # <-- Phase 1/Phase 2 flagged this!
        })
        cfg = RewardConfig()
        rf = RewardFunction(cfg)
        reward = rf.compute(row)
        self.assertEqual(
            reward, -1.0,
            f"RL ranker must return -1.0 for is_withdrawn=True row, got {reward}",
        )

    def test_inv5_rl_ranker_uses_frozenset_as_fallback(self):
        """A row WITHOUT is_withdrawn column but WITH a known withdrawn drug_name
        must still be rejected (frozenset backstop)."""
        _P4_ROOT = _REPO_ROOT / "rl"
        if str(_P4_ROOT) not in sys.path:
            sys.path.insert(0, str(_P4_ROOT.parent))
        from rl.rl_drug_ranker import RewardFunction, RewardConfig
        # Use a drug in the frozenset (rofecoxib = Vioxx).
        row = pd.Series({
            "drug_name": "rofecoxib",
            "disease_name": "headache",
            "gnn_score": 0.85,
            "safety_score": 0.9,
            "market_score": 0.5,
            # NOTE: no is_withdrawn column — frozenset must catch this.
        })
        cfg = RewardConfig()
        rf = RewardFunction(cfg)
        reward = rf.compute(row)
        self.assertEqual(reward, -1.0, "Frozenset backstop must still reject rofecoxib")

    # ----- Contract drift ---------------------------------------------

    def test_no_contract_vs_pipeline_drift_for_drugs(self):
        drift = detect_contract_vs_pipeline_drift()
        drugs_drift = [d for d in drift if "source 'drugs'" in d]
        self.assertEqual(
            drugs_drift, [],
            f"DrugBank 'drugs' contract-vs-pipeline drift: {drugs_drift}",
        )


if __name__ == "__main__":
    unittest.main()
