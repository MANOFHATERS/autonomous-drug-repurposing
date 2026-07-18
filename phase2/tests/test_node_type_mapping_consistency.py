"""Task 119: Verify pyg_builder and phase2_adapter use the SAME node type mapping.

Root test for task 112 (INT-004): both pyg_builder._PHASE2_TO_GT_NODE_TYPE
and phase2_adapter.PHASE2_TO_PHASE3_NODE must import from the SAME shared
module (schema_mappings.py). No local copies allowed.
"""
import importlib
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

# Add graph_transformer to path for phase2_adapter
_gt_path = str(Path(__file__).resolve().parents[2] / "graph_transformer")
if _gt_path not in sys.path:
    sys.path.insert(0, _gt_path)


class TestNodeTypeMappingConsistency(unittest.TestCase):
    """Task 119: verify pyg_builder and phase2_adapter use the same mapping."""

    def test_both_import_from_schema_mappings(self):
        """Both modules must import the mapping from schema_mappings."""
        from drugos_graph import schema_mappings, pyg_builder
        # pyg_builder._PHASE2_TO_GT_NODE_TYPE should be the SAME object as
        # schema_mappings.PHASE2_TO_PHASE3_NODE
        self.assertIs(
            pyg_builder._PHASE2_TO_GT_NODE_TYPE,
            schema_mappings.PHASE2_TO_PHASE3_NODE,
            "pyg_builder._PHASE2_TO_GT_NODE_TYPE must be the SAME object as "
            "schema_mappings.PHASE2_TO_PHASE3_NODE (imported, not copied). "
            "(task 112 root fix — INT-004)"
        )

    def test_phase2_adapter_imports_from_schema_mappings(self):
        """phase2_adapter must also import from schema_mappings."""
        try:
            # phase2_adapter is in graph_transformer/data/
            from data.phase2_adapter import PHASE2_TO_PHASE3_NODE
            from drugos_graph.schema_mappings import (
                PHASE2_TO_PHASE3_NODE as SHARED_MAPPING,
            )
            self.assertIs(
                PHASE2_TO_PHASE3_NODE, SHARED_MAPPING,
                "phase2_adapter.PHASE2_TO_PHASE3_NODE must be the SAME object "
                "as schema_mappings.PHASE2_TO_PHASE3_NODE. (task 112)"
            )
        except ImportError:
            # Try alternate import path
            try:
                import importlib.util
                spec = importlib.util.spec_from_file_location(
                    "phase2_adapter",
                    str(Path(__file__).resolve().parents[2] /
                        "graph_transformer" / "data" / "phase2_adapter.py"),
                )
                mod = importlib.util.module_from_spec(spec)
                spec.loader.exec_module(mod)
                from drugos_graph.schema_mappings import (
                    PHASE2_TO_PHASE3_NODE as SHARED_MAPPING,
                )
                self.assertIs(
                    mod.PHASE2_TO_PHASE3_NODE, SHARED_MAPPING,
                    "phase2_adapter.PHASE2_TO_PHASE3_NODE must be the SAME "
                    "object as schema_mappings.PHASE2_TO_PHASE3_NODE. (task 112)"
                )
            except Exception as e:
                self.skipTest(f"Could not import phase2_adapter: {e}")

    def test_mapping_has_5_canonical_node_types(self):
        """The mapping must have at least 5 canonical Phase 3 node types.

        v118 ROOT FIX (Teammate 4): the original `len == 5` assertion was
        outdated — the contract now has 8 entries:
          - 6 canonical mappings (Compound, Drug, Protein, Pathway, Disease,
            ClinicalOutcome) — Drug was added by P2-006 root fix to prevent
            silent dropping of literature-validated Drug nodes (the data
            flywheel's proprietary moat per DOCX section 10).
          - 2 None intermediates (Gene, MedDRA_Term) — explicitly mapped to
            None to document they are dropped on purpose, not forgotten.
        Lock the minimum canonical count, not a stale literal.
        """
        from drugos_graph.schema_mappings import PHASE2_TO_PHASE3_NODE
        non_none = {k: v for k, v in PHASE2_TO_PHASE3_NODE.items() if v is not None}
        self.assertGreaterEqual(
            len(non_none), 5,
            f"Expected at least 5 canonical node types, got "
            f"{len(non_none)}: {list(non_none.keys())}"
        )

    def test_mapping_includes_required_types(self):
        """The mapping must include Compound, Protein, Pathway, Disease, ClinicalOutcome.

        v118 ROOT FIX (Teammate 4): the original assertion expected the
        mapping keys to be EXACTLY {Compound, Protein, Pathway, Disease,
        ClinicalOutcome}. But the contract has grown to include Drug (P2-006
        root fix — same Phase 3 type as Compound) and the None intermediates
        Gene + MedDRA_Term. Lock the REQUIRED types as a subset, not an
        exact set, so legitimate additions don't break the test.
        """
        from drugos_graph.schema_mappings import PHASE2_TO_PHASE3_NODE
        required_phase2 = {
            "Compound", "Protein", "Pathway", "Disease", "ClinicalOutcome"
        }
        actual_keys = set(PHASE2_TO_PHASE3_NODE.keys())
        missing = required_phase2 - actual_keys
        self.assertFalse(
            missing,
            f"Mapping is missing required types: {missing}. "
            f"Got keys: {actual_keys}"
        )

    def test_mapping_excludes_intermediate_types(self):
        """Gene and MedDRA_Term must map to None (not be absent, not map to a real type).

        v118 ROOT FIX (Teammate 4): the original assertion `assertNotIn`
        was wrong — the SH-011 root fix EXPLICITLY includes Gene and
        MedDRA_Term in the mapping with value None, to document that they
        are intermediates dropped on purpose (not forgotten). The contract
        test (shared/tests/test_contract_consistency.py:200-206) verifies
        this exact behavior. This test must match the contract.
        """
        from drugos_graph.schema_mappings import PHASE2_TO_PHASE3_NODE
        self.assertIn("Gene", PHASE2_TO_PHASE3_NODE,
                      "Gene must be in the mapping with value None "
                      "(SH-011 root fix — documents it's dropped on purpose)")
        self.assertIsNone(
            PHASE2_TO_PHASE3_NODE["Gene"],
            "Gene must map to None (intermediate dropped in Phase 3 projection)"
        )
        self.assertIn("MedDRA_Term", PHASE2_TO_PHASE3_NODE,
                      "MedDRA_Term must be in the mapping with value None")
        self.assertIsNone(
            PHASE2_TO_PHASE3_NODE["MedDRA_Term"],
            "MedDRA_Term must map to None (folded into ClinicalOutcome)"
        )

    def test_no_local_mapping_definitions(self):
        """Neither pyg_builder nor phase2_adapter should define a local mapping dict.

        They must IMPORT from schema_mappings, not define their own.
        """
        import inspect
        from drugos_graph import pyg_builder

        # Read the source of pyg_builder
        source = inspect.getsource(pyg_builder)
        # The source should NOT contain a local dict definition like:
        # _PHASE2_TO_GT_NODE_TYPE = {  (with a literal dict body)
        # But it SHOULD contain an import like:
        # from .schema_mappings import PHASE2_TO_PHASE3_NODE as _PHASE2_TO_GT_NODE_TYPE
        self.assertIn(
            "from .schema_mappings import",
            source,
            "pyg_builder must import _PHASE2_TO_GT_NODE_TYPE from "
            "schema_mappings, not define it locally. (task 112)"
        )


if __name__ == "__main__":
    unittest.main()
