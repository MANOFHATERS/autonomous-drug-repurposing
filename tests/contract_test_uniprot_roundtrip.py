"""TM1 Task 1.3 contract test: UniProt Phase 1 → Phase 2 → Phase 3.

Verification command (from Task 1.3 spec):
    python -m pytest tests/contract_test_uniprot_roundtrip.py -v

Asserts the 4 contract invariants for the UniProt protein-sequence flow:
  INV-1: uniprot_pipeline extracts sequence + organism + function + subcellular_location
  INV-2: phase1_schema "uniprot_proteins" SourceSpec has all 4 fields
  INV-3: phase2 uniprot_loader preserves all 4 fields
  INV-4: 10-protein sample round-trip — all rows have non-empty sequence

This test exercises REAL code paths: loads the embedded sample (10 proteins),
runs the Phase 2 loader on it, and verifies every protein has non-empty sequence.
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

from pipelines._dev_samples import embedded_uniprot_proteins  # noqa: E402
from contracts.phase1_schema import (  # noqa: E402
    PHASE1_OUTPUT_SCHEMA,
    detect_contract_vs_pipeline_drift,
)


class TestUniProtContractRoundTrip(unittest.TestCase):
    """Verify the full Phase 1 → Phase 2 → Phase 3 UniProt flow."""

    @classmethod
    def setUpClass(cls):
        # Load the 10-protein dev sample (Phase 1 output simulation).
        cls.proteins_df = embedded_uniprot_proteins()
        # Add phase2 to path so drugos_graph is importable.
        _P2_ROOT = _REPO_ROOT / "phase2"
        if str(_P2_ROOT) not in sys.path:
            sys.path.insert(0, str(_P2_ROOT))

    # ----- INV-1: pipeline extracts all 4 fields ----------------------

    def test_inv1_sample_has_10_proteins(self):
        """The dev sample MUST have at least 10 proteins (Task 1.3 spec)."""
        self.assertGreaterEqual(
            len(self.proteins_df), 10,
            f"Sample has {len(self.proteins_df)} proteins; expected >= 10",
        )

    def test_inv1_sample_has_sequence_column(self):
        """The dev sample MUST have a ``sequence`` column."""
        self.assertIn(
            "sequence", self.proteins_df.columns,
            "Dev sample is missing the 'sequence' column — Phase 3 cannot extract features",
        )

    def test_inv1_sample_has_subcellular_location_column(self):
        """The dev sample MUST have a ``subcellular_location`` column."""
        self.assertIn(
            "subcellular_location", self.proteins_df.columns,
            "Dev sample is missing the 'subcellular_location' column",
        )

    def test_inv1_sample_has_organism_column(self):
        self.assertIn("organism", self.proteins_df.columns)

    def test_inv1_sample_has_function_column(self):
        """The dev sample MUST have a ``function`` column (contract-canonical name)."""
        self.assertIn("function", self.proteins_df.columns)

    def test_inv1_all_sequences_non_empty(self):
        """Every protein in the sample MUST have a non-empty sequence."""
        for idx, row in self.proteins_df.iterrows():
            seq = row.get("sequence")
            self.assertIsNotNone(seq, f"Row {idx} has None sequence")
            self.assertIsInstance(seq, str, f"Row {idx} sequence is not a str")
            self.assertGreater(
                len(seq), 0,
                f"Row {idx} (uniprot_id={row.get('uniprot_id')}) has empty sequence",
            )

    # ----- INV-2: contract has all 4 fields ---------------------------

    def test_inv2_contract_has_sequence(self):
        spec = PHASE1_OUTPUT_SCHEMA["uniprot_proteins"]
        optional_names = [c.name for c in spec.optional_columns]
        self.assertIn("sequence", optional_names)

    def test_inv2_contract_has_organism(self):
        spec = PHASE1_OUTPUT_SCHEMA["uniprot_proteins"]
        optional_names = [c.name for c in spec.optional_columns]
        self.assertIn("organism", optional_names)

    def test_inv2_contract_has_function(self):
        spec = PHASE1_OUTPUT_SCHEMA["uniprot_proteins"]
        optional_names = [c.name for c in spec.optional_columns]
        self.assertIn("function", optional_names)

    def test_inv2_contract_has_subcellular_location(self):
        """TM1 Task 1.3 ROOT FIX: contract MUST declare subcellular_location."""
        spec = PHASE1_OUTPUT_SCHEMA["uniprot_proteins"]
        optional_names = [c.name for c in spec.optional_columns]
        self.assertIn(
            "subcellular_location", optional_names,
            "Contract 'uniprot_proteins' SourceSpec missing 'subcellular_location' column",
        )

    # ----- INV-3: phase2 uniprot_loader preserves fields --------------

    def test_inv3_phase2_loader_preserves_sequence(self):
        """drugos_graph.uniprot_loader MUST propagate sequence to the KG node."""
        # Add phase2 to path.
        _P2_ROOT = _REPO_ROOT / "phase2"
        if str(_P2_ROOT.parent) not in sys.path:
            sys.path.insert(0, str(_P2_ROOT.parent))
        from drugos_graph.uniprot_loader import uniprot_to_node_records_from_phase1
        records = self.proteins_df.to_dict(orient="records")
        nodes = uniprot_to_node_records_from_phase1(records)
        self.assertEqual(len(nodes), len(self.proteins_df))
        for node in nodes:
            self.assertIn("sequence", node)
            self.assertTrue(
                node["sequence"],
                f"Node {node.get('id')} has empty sequence after loader",
            )

    def test_inv3_phase2_loader_preserves_subcellular_location(self):
        """TM1 Task 1.3 ROOT FIX: loader MUST propagate subcellular_location."""
        _P2_ROOT = _REPO_ROOT / "phase2"
        if str(_P2_ROOT.parent) not in sys.path:
            sys.path.insert(0, str(_P2_ROOT.parent))
        from drugos_graph.uniprot_loader import uniprot_to_node_records_from_phase1
        records = self.proteins_df.to_dict(orient="records")
        nodes = uniprot_to_node_records_from_phase1(records)
        for node in nodes:
            self.assertIn(
                "subcellular_location", node,
                "Phase 2 loader does NOT propagate subcellular_location to KG node",
            )

    def test_inv3_phase2_loader_preserves_organism_and_function(self):
        _P2_ROOT = _REPO_ROOT / "phase2"
        if str(_P2_ROOT.parent) not in sys.path:
            sys.path.insert(0, str(_P2_ROOT.parent))
        from drugos_graph.uniprot_loader import uniprot_to_node_records_from_phase1
        records = self.proteins_df.to_dict(orient="records")
        nodes = uniprot_to_node_records_from_phase1(records)
        for node in nodes:
            self.assertIn("organism", node)
            self.assertIn("function", node)

    # ----- INV-4: 10-protein round-trip -------------------------------

    def test_inv4_10_protein_roundtrip(self):
        """End-to-end: 10 proteins → Phase 2 → all have non-empty sequence."""
        _P2_ROOT = _REPO_ROOT / "phase2"
        if str(_P2_ROOT.parent) not in sys.path:
            sys.path.insert(0, str(_P2_ROOT.parent))
        from drugos_graph.uniprot_loader import uniprot_to_node_records_from_phase1
        # Take first 10 proteins.
        sample = self.proteins_df.head(10)
        records = sample.to_dict(orient="records")
        nodes = uniprot_to_node_records_from_phase1(records)
        self.assertEqual(len(nodes), 10)
        for i, node in enumerate(nodes):
            self.assertTrue(
                node.get("sequence"),
                f"Protein {i} ({node.get('id')}) has empty sequence after round-trip",
            )

    # ----- Contract drift ---------------------------------------------

    def test_no_contract_vs_pipeline_drift_for_uniprot(self):
        drift = detect_contract_vs_pipeline_drift()
        uniprot_drift = [d for d in drift if "uniprot" in d.lower()]
        self.assertEqual(
            uniprot_drift, [],
            f"UniProt contract-vs-pipeline drift: {uniprot_drift}",
        )


if __name__ == "__main__":
    unittest.main()
