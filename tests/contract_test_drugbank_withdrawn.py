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

    # ----- INV-6 (v130 ROOT FIX): <withdrawn> tag (real DrugBank 5.x) -
    # Hostile-auditor finding: the previous parser only tried
    # ``<db:withdrawn-notice>`` and ``<db:withdrawn_notice>``. The real
    # DrugBank 5.x production XML uses ``<db:withdrawn>`` per the
    # official schema (https://docs.drugbank.com/xml). The fixture uses
    # ``<withdrawn-notice>`` so existing tests passed, but production
    # would silently return None for withdrawn_reason/country/year on
    # every real DrugBank file. The v130 ROOT FIX adds
    # ``<db:withdrawn>`` as the FIRST tag to try.

    def test_inv6_parser_handles_real_drugbank_withdrawn_tag(self):
        """The parser MUST handle the real DrugBank 5.x ``<withdrawn>``
        tag (not just the ``<withdrawn-notice>`` fixture spelling).

        This test builds a synthetic DrugBank <drug> element that uses
        ``<db:withdrawn>`` (the production XML tag) and verifies the
        parser extracts reason/country/year from it. If the parser only
        tries ``<withdrawn-notice>``, this test FAILS — proving the
        production bug.
        """
        from lxml import etree
        from pipelines.drugbank_pipeline import NS

        # Build a minimal DrugBank <drug> element using the REAL
        # <withdrawn> tag (per DrugBank 5.x XML schema).
        # Use the SAME namespace the production parser expects
        # (http://drugbank.ca — see config.settings.DRUGBANK_XML_NAMESPACE).
        xml = b"""<?xml version="1.0" encoding="UTF-8"?>
<drugbank xmlns="http://drugbank.ca" version="5.1">
  <drug type="small-molecule" created="2005-06-13" updated="2024-01-15">
    <drugbank-id primary="true">DB99999</drugbank-id>
    <name>test_withdrawn_drug_v130</name>
    <description>A test drug using the real DrugBank 5.x withdrawn tag.</description>
    <groups>
      <group>approved</group>
      <group>withdrawn</group>
    </groups>
    <withdrawn>
      <country>US</country>
      <year>2020</year>
      <reason>cardiovascular events</reason>
    </withdrawn>
    <calculated-properties>
      <property>
        <kind>Molecular Weight</kind>
        <value>300.5</value>
        <source>ChemAxon</source>
      </property>
    </calculated-properties>
  </drug>
</drugbank>"""
        tree = etree.fromstring(xml)
        drug_elem = tree.find(".//db:drug", NS)
        self.assertIsNotNone(drug_elem, "Failed to find <db:drug> in test XML")

        result = self.pipeline._parse_drug_element(drug_elem)
        self.assertIsNotNone(result, "_parse_drug_element returned None for test drug")
        drug_rec, _ = result
        self.assertIsNotNone(drug_rec, "drug_rec is None")

        # The CRITICAL assertions: structured withdrawal metadata MUST
        # be extracted from the <withdrawn> tag.
        self.assertTrue(
            drug_rec["is_withdrawn"],
            "is_withdrawn must be True (drug has <withdrawn> + groups has 'withdrawn')",
        )
        self.assertEqual(
            drug_rec["withdrawn_reason"], "cardiovascular events",
            f"withdrawn_reason must be 'cardiovascular events', got "
            f"{drug_rec['withdrawn_reason']!r} — parser did NOT extract "
            f"from <withdrawn> tag (v130 ROOT FIX regression)",
        )
        self.assertEqual(
            drug_rec["withdrawn_year"], 2020,
            f"withdrawn_year must be 2020, got {drug_rec['withdrawn_year']!r}",
        )
        self.assertEqual(
            drug_rec["withdrawn_country"], "US",
            f"withdrawn_country must be 'US', got {drug_rec['withdrawn_country']!r}",
        )

    def test_inv6_parser_source_code_includes_withdrawn_tag(self):
        """The parser source code MUST include 'db:withdrawn' in the tag
        tuple (defensive check — if the v130 ROOT FIX is reverted, this
        test fails immediately).
        """
        import inspect
        from pipelines.drugbank_pipeline import DrugBankPipeline
        source = inspect.getsource(DrugBankPipeline._parse_drug_element)
        self.assertIn(
            '"db:withdrawn"', source,
            "DrugBankPipeline._parse_drug_element source MUST include "
            "'db:withdrawn' in the tag tuple (v130 ROOT FIX). Without "
            "this tag, the parser silently returns None for "
            "withdrawn_reason/country/year on every real DrugBank 5.x "
            "production XML file.",
        )

    # ----- INV-7 (v130 ROOT FIX): loaders updatable_cols includes withdrawn fields

    def test_inv7_loaders_updatable_cols_include_withdrawn_fields(self):
        """bulk_upsert_drugs updatable_cols MUST include withdrawn_reason,
        withdrawn_country, withdrawn_year so a DrugBank refresh actually
        updates these fields on CONFLICT/UPDATE (hostile-auditor finding).
        """
        import inspect
        from database.loaders import bulk_upsert_drugs
        source = inspect.getsource(bulk_upsert_drugs)
        for field in ("withdrawn_reason", "withdrawn_country", "withdrawn_year"):
            self.assertIn(
                field, source,
                f"bulk_upsert_drugs source MUST include '{field}' in "
                f"updatable_cols (v130 ROOT FIX). Without this, the field "
                f"is INSERTed on first load but NEVER updated — meaning a "
                f"DrugBank refresh that adds/changes withdrawal metadata "
                f"would be silently dropped.",
            )


if __name__ == "__main__":
    unittest.main()
