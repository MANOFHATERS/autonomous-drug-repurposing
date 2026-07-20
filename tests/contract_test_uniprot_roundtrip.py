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

    # ----- INV-5 (v130 ROOT FIX): Protein ORM model has new columns ---
    # Hostile-auditor finding: the Phase 1 schema declares ``function``
    # and ``subcellular_location`` as optional columns, and the UniProt
    # pipeline writes both to the CSV. But the ORM ``Protein`` model
    # declared only ``function_desc`` (legacy) and did NOT declare
    # ``function`` or ``subcellular_location``. As a result,
    # ``bulk_upsert_proteins`` silently dropped both columns before
    # INSERT. The v130 ROOT FIX adds both columns to the ORM model
    # (migration 020).

    def test_inv5_protein_model_has_function_column(self):
        """Protein ORM model MUST declare ``function`` column."""
        from database.models import Protein
        self.assertTrue(
            hasattr(Protein, "function"),
            "Protein model must have 'function' column (v130 ROOT FIX). "
            "Without this, bulk_upsert_proteins silently drops the field "
            "and the Phase 2 bridge gets NULL from the DB.",
        )
        # Verify it's a real column (not just any attribute).
        from sqlalchemy import inspect as sa_inspect
        mapper = sa_inspect(Protein)
        column_names = {c.key for c in mapper.columns}
        self.assertIn(
            "function", column_names,
            "Protein model's SQLAlchemy columns MUST include 'function'",
        )

    def test_inv5_protein_model_has_subcellular_location_column(self):
        """Protein ORM model MUST declare ``subcellular_location`` column."""
        from database.models import Protein
        self.assertTrue(
            hasattr(Protein, "subcellular_location"),
            "Protein model must have 'subcellular_location' column (v130 ROOT FIX)",
        )
        from sqlalchemy import inspect as sa_inspect
        mapper = sa_inspect(Protein)
        column_names = {c.key for c in mapper.columns}
        self.assertIn(
            "subcellular_location", column_names,
            "Protein model's SQLAlchemy columns MUST include 'subcellular_location'",
        )

    def test_inv5_protein_model_sequence_is_text_not_varchar(self):
        """Protein.sequence MUST be Text (not String(50000)) — removes
        latent truncation risk for proteins > 50,000 aa (v130 ROOT FIX).
        """
        from database.models import Protein
        from sqlalchemy import inspect as sa_inspect
        mapper = sa_inspect(Protein)
        seq_col = next(c for c in mapper.columns if c.key == "sequence")
        # Text type has no length limit; String(N) has length N.
        # We accept either Text or String with no length attribute.
        type_obj = seq_col.type
        type_name = type_obj.__class__.__name__.upper()
        # Accept TEXT or VARCHAR with length=None. Reject VARCHAR(50000).
        has_length = getattr(type_obj, "length", None) is not None
        self.assertTrue(
            type_name == "TEXT" or not has_length,
            f"Protein.sequence must be TEXT (unbounded), got {type_name} "
            f"with length={getattr(type_obj, 'length', None)}. VARCHAR(50000) "
            f"is a latent truncation risk for proteins > 50,000 aa.",
        )

    # ----- INV-6 (v130 ROOT FIX): .csv normalizer writes subcellular_location

    def test_inv6_csv_normalizer_source_code_writes_subcellular_location(self):
        """The .csv normalizer (embedded-sample fallback) MUST write
        ``subcellular_location`` as the 10th TSV field. The previous
        code wrote only 9 fields, causing every embedded-sample row's
        subcellular_location to be empty.
        """
        import inspect
        from pipelines.uniprot_pipeline import UniProtPipeline
        source = inspect.getsource(UniProtPipeline._normalize_v50_to_raw_tsv)
        # The .csv branch must include 'subcellular_location' in the
        # writerow list. We check the source code (not runtime) because
        # invoking the method requires a real prot_path file.
        self.assertIn(
            "subcellular_location", source,
            "UniProtPipeline._normalize_v50_to_raw_tsv source MUST "
            "include 'subcellular_location' in the .csv writerow list "
            "(v130 ROOT FIX). Without this, embedded-sample rows have "
            "empty subcellular_location, defeating Phase 3 node-feature "
            "extraction (TASK-141).",
        )

    # ----- INV-7 (v130 ROOT FIX): loaders updatable_cols --------------

    def test_inv7_loaders_updatable_cols_include_new_fields(self):
        """bulk_upsert_proteins updatable_cols MUST include ``function``
        and ``subcellular_location`` so a UniProt refresh actually
        updates these fields on CONFLICT/UPDATE (hostile-auditor finding).
        """
        import inspect
        from database.loaders import bulk_upsert_proteins
        source = inspect.getsource(bulk_upsert_proteins)
        for field in ("function", "subcellular_location"):
            self.assertIn(
                field, source,
                f"bulk_upsert_proteins source MUST include '{field}' in "
                f"updatable_cols (v130 ROOT FIX). Without this, the field "
                f"is INSERTed on first load but NEVER updated — meaning a "
                f"UniProt refresh that adds/changes FUNCTION or "
                f"Subcellular Location text would be silently dropped.",
            )

    # ----- INV-8 (v130 ROOT FIX): migration 020 exists ----------------

    def test_inv8_migration_020_file_exists(self):
        """Migration 020 (protein function + subcellular_location) MUST
        exist so the DB schema actually has the columns the ORM declares.
        """
        migration_path = _PHASE1_ROOT / "database" / "migrations" / "020_protein_function_subcellular_location.sql"
        self.assertTrue(
            migration_path.exists(),
            f"Migration file MUST exist at {migration_path} (v130 ROOT FIX). "
            "Without this migration, the ORM declares columns that don't "
            "exist in the DB — every INSERT/UPDATE would fail.",
        )
        # Verify the migration content includes the new columns.
        content = migration_path.read_text(encoding="utf-8")
        self.assertIn("function", content)
        self.assertIn("subcellular_location", content)
        self.assertIn("ALTER TABLE proteins", content)


if __name__ == "__main__":
    unittest.main()
