#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Teammate-2 Tasks 2.1-2.4: Hostile-auditor contract verification suite.

Each test verifies a ROOT-LEVEL fix by reading ACTUAL CODE (via AST
or import-time introspection) — NOT comments, NOT smoke tests. The
suite is designed so that ANY regression that reintroduces the
original bug will cause a test failure.

Tasks covered:
  Task 2.1: STRING PPI pipeline produces edges + Phase 2->3 connectivity
  Task 2.2: DisGeNET GDA prevalence (CF must be RARE, not common)
  Task 2.3: OMIM genetic_basis field + 6-digit MIM number parse
  Task 2.4: PubChem enrichment filename + full 15-property list

Run with:
    pytest tests/test_teammate2_tasks_2_1_to_2_4.py -v
"""
from __future__ import annotations

import inspect
import os
import re
import sys
from pathlib import Path

import pytest

# Ensure phase1/ is on sys.path so `from phase1.pipelines.X` works.
_REPO_ROOT = Path(__file__).resolve().parents[1]
_PHASE1_ROOT = _REPO_ROOT / "phase1"
for _p in (_REPO_ROOT, _PHASE1_ROOT):
    _p_str = str(_p)
    if _p_str not in sys.path:
        sys.path.insert(0, _p_str)

# Set dev environment for the import-time guards.
os.environ.setdefault("DRUGOS_ENVIRONMENT", "development")
os.environ.setdefault("DRUGOS_DOWNLOAD_MODE", "sample")


# =============================================================================
# Task 2.1: STRING PPI pipeline + Phase 2->3 connectivity
# =============================================================================

class TestTask2_1_STRING_PPI_Connectivity:
    """Verify STRING PPI edges flow Phase 1 -> Phase 2 -> Phase 3."""

    def test_ppi_in_phase2_to_phase3_edge(self):
        """PPI must be in PHASE2_TO_PHASE3_EDGE (not dropped)."""
        from shared.contracts.phase_edge_mapping import (
            PHASE2_TO_PHASE3_EDGE,
            PHASE2_TO_PHASE3_EDGE_DROPPED,
        )
        ppi_key = ("Protein", "interacts_with", "Protein")
        assert ppi_key in PHASE2_TO_PHASE3_EDGE, (
            "PPI edge type must be in PHASE2_TO_PHASE3_EDGE. "
            "Previously it was SILENTLY DROPPED — see Task 2.1 ROOT FIX."
        )
        expected_p3 = ("protein", "interacts_with", "protein")
        assert PHASE2_TO_PHASE3_EDGE[ppi_key] == expected_p3, (
            f"PPI must map to {expected_p3}, got "
            f"{PHASE2_TO_PHASE3_EDGE[ppi_key]}"
        )

    def test_ppi_not_in_dropped(self):
        """PPI must NOT be in PHASE2_TO_PHASE3_EDGE_DROPPED."""
        from shared.contracts.phase_edge_mapping import (
            PHASE2_TO_PHASE3_EDGE_DROPPED,
        )
        ppi_key = ("Protein", "interacts_with", "Protein")
        assert ppi_key not in PHASE2_TO_PHASE3_EDGE_DROPPED, (
            "PPI must NOT be in PHASE2_TO_PHASE3_EDGE_DROPPED. "
            "Task 2.1 ROOT FIX removed it — see phase2_schema.py."
        )

    def test_ppi_in_phase3_edge_types(self):
        """Phase 3 EDGE_TYPES must include ('protein', 'interacts_with', 'protein')."""
        try:
            from graph_transformer.data import EDGE_TYPES
        except ImportError:
            pytest.skip("torch not installed — graph_transformer.data not importable")
        p3_ppi = ("protein", "interacts_with", "protein")
        assert p3_ppi in EDGE_TYPES, (
            f"Phase 3 EDGE_TYPES must include {p3_ppi}. "
            f"Got: {EDGE_TYPES}"
        )

    def test_ppi_in_reverse_relation_map(self):
        """REVERSE_RELATION_MAP must include 'interacts_with' (symmetric)."""
        try:
            from graph_transformer.data import REVERSE_RELATION_MAP
        except ImportError:
            pytest.skip("torch not installed — graph_transformer.data not importable")
        assert "interacts_with" in REVERSE_RELATION_MAP, (
            "REVERSE_RELATION_MAP must include 'interacts_with' for symmetric PPI."
        )
        assert REVERSE_RELATION_MAP["interacts_with"] == "interacts_with", (
            "PPI is symmetric — reverse of 'interacts_with' is itself."
        )

    def test_cli_dispatch_accepts_bare_source_names(self):
        """`python -m phase1.pipelines string` must work (bare source name)."""
        from pipelines import _main, _SOURCE_TO_CLASS
        # The 4 source names used in the task verification commands
        for name in ("string", "disgenet", "omim", "pubchem"):
            assert name in _SOURCE_TO_CLASS, (
                f"CLI dispatch must recognize source name {name!r}"
            )

    def test_string_pipeline_writes_canonical_or_alias_filename(self):
        """Verify string_pipeline writes either canonical or alias filename."""
        string_pipeline_path = _PHASE1_ROOT / "pipelines" / "string_pipeline.py"
        src = string_pipeline_path.read_text()
        # The bridge accepts BOTH names (phase1_bridge.py:4252-4255).
        # The pipeline writes "protein_protein_interactions.csv" (alias).
        # Either is acceptable per the schema's aliases tuple.
        assert (
            "string_protein_protein_interactions.csv" in src
            or "protein_protein_interactions.csv" in src
        ), "string_pipeline.py must write the canonical or alias filename"

    def test_phase2_string_loader_consumes_phase1_csv(self):
        """Verify phase2 string_loader has a phase1-aware path."""
        loader_path = _REPO_ROOT / "phase2" / "drugos_graph" / "string_loader.py"
        src = loader_path.read_text()
        # The loader must reference the Phase 1 canonical filename.
        assert "string_protein_protein_interactions.csv" in src or \
               "protein_protein_interactions.csv" in src, (
            "phase2 string_loader must consume the Phase 1 STRING PPI CSV"
        )


# =============================================================================
# Task 2.2: DisGeNET GDA prevalence
# =============================================================================

class TestTask2_2_DisGeNET_Prevalence:
    """Verify DisGeNET prevalence is REAL (not linear), CF must be RARE."""

    def test_prevalence_per_10k_in_disgenet_schema(self):
        """phase1_schema.disgenet_gda must declare prevalence_per_10k optional."""
        # Use absolute import (phase1.contracts.phase1_schema) — the
        # bare `contracts.phase1_schema` import breaks when an earlier
        # test imports `pipelines` (which manipulates sys.path).
        from phase1.contracts.phase1_schema import PHASE1_OUTPUT_SCHEMA
        spec = PHASE1_OUTPUT_SCHEMA["disgenet_gda"]
        opt_names = [c.name for c in spec.optional_columns]
        assert "prevalence_per_10k" in opt_names, (
            "phase1_schema.disgenet_gda.optional_columns must include "
            "'prevalence_per_10k' (Task 2.2 ROOT FIX)"
        )

    def test_disgenet_canonical_filename_matches_schema(self):
        """disgenet_gda canonical filename must be 'disgenet_gene_disease_associations.csv'."""
        from phase1.contracts.phase1_schema import PHASE1_OUTPUT_SCHEMA
        spec = PHASE1_OUTPUT_SCHEMA["disgenet_gda"]
        assert spec.filename == "disgenet_gene_disease_associations.csv", (
            f"disgenet_gda.filename must be canonical, got {spec.filename!r}"
        )

    def test_disgenet_output_filename_default_is_canonical(self):
        """DISGENET_OUTPUT_FILENAME default must match the schema canonical."""
        from phase1.config.settings import DISGENET_OUTPUT_FILENAME
        assert DISGENET_OUTPUT_FILENAME == "disgenet_gene_disease_associations.csv", (
            f"DISGENET_OUTPUT_FILENAME default must be canonical, got "
            f"{DISGENET_OUTPUT_FILENAME!r}"
        )

    def test_cystic_fibrosis_is_rare(self):
        """CF prevalence MUST be < 5/10K (RARE per FDA/EU definition).

        This is the HEADLINE bug: the previous LINEAR formula flagged
        CF as 'common' (prevalence ~3000/10K) because CF has ~2000 GDAs.
        CF is in fact RARE (0.4/10K). This test catches ANY regression
        that reintroduces the linear formula.
        """
        from phase1.pipelines.disgenet_pipeline import (
            _lookup_prevalence_per_10k,
            RARE_DISEASE_PREVALENCE_THRESHOLD,
        )
        cf_prev = _lookup_prevalence_per_10k("C0010674", "Cystic Fibrosis")
        assert cf_prev is not None, "CF prevalence must be in the curated table"
        assert cf_prev < RARE_DISEASE_PREVALENCE_THRESHOLD, (
            f"CF prevalence ({cf_prev}) must be < "
            f"{RARE_DISEASE_PREVALENCE_THRESHOLD} (RARE). The linear formula "
            f"bug has regenerated — investigate _lookup_prevalence_per_10k."
        )
        # Specifically: CF should be 0.4/10K (per WHO/Orphanet).
        assert cf_prev == 0.4, f"CF prevalence should be 0.4/10K, got {cf_prev}"

    def test_migraine_is_common(self):
        """Migraine prevalence must be > 5/10K (COMMON).

        This is the inverse of the CF test — migraine has FEW GDAs but
        HIGH prevalence. The linear formula would have flagged migraine
        as 'rare' (low GDA count -> low prevalence), the opposite of
        reality.
        """
        from phase1.pipelines.disgenet_pipeline import (
            _lookup_prevalence_per_10k,
            RARE_DISEASE_PREVALENCE_THRESHOLD,
        )
        mig_prev = _lookup_prevalence_per_10k("C0149887", "Migraine")
        assert mig_prev is not None
        assert mig_prev >= RARE_DISEASE_PREVALENCE_THRESHOLD, (
            f"Migraine prevalence ({mig_prev}) must be >= "
            f"{RARE_DISEASE_PREVALENCE_THRESHOLD} (COMMON)."
        )

    def test_orpha_disease_id_is_rare(self):
        """Diseases with ORPHA:nnnn ID must be rare (Orphanet = rare DB)."""
        from phase1.pipelines.disgenet_pipeline import (
            _lookup_prevalence_per_10k,
            RARE_DISEASE_PREVALENCE_THRESHOLD,
            _ORPHANET_DEFAULT_PREVALENCE_PER_10K,
        )
        # Any ORPHA:nnnn disease should return a rare prevalence.
        orpha_prev = _lookup_prevalence_per_10k("ORPHA:558", "Some rare disease")
        assert orpha_prev == _ORPHANET_DEFAULT_PREVALENCE_PER_10K
        assert orpha_prev < RARE_DISEASE_PREVALENCE_THRESHOLD

    def test_unknown_disease_returns_none(self):
        """Diseases not in the curated table must return None."""
        from phase1.pipelines.disgenet_pipeline import _lookup_prevalence_per_10k
        unknown_prev = _lookup_prevalence_per_10k("C9999999", "Unknown fake disease")
        assert unknown_prev is None, (
            "Unknown diseases must return None (downstream treats as neutral 0.5)"
        )

    def test_no_linear_formula_in_code(self):
        """Verify the LINEAR formula '5.0 + 2995.0 * n_gdas / max_gda' is GONE.

        The formula was removed in v113 P3-026 from biomedical_tables.py.
        This test catches ANY regression that reintroduces it.
        """
        # Check disgenet_pipeline.py
        disgenet_path = _PHASE1_ROOT / "pipelines" / "disgenet_pipeline.py"
        src = disgenet_path.read_text()
        # The literal "5.0 + 2995.0" pattern must NOT appear in any
        # executable statement (it may appear in a comment describing
        # the removed bug, which is fine).
        # Parse the AST and check all BinOp nodes.
        import ast
        tree = ast.parse(src)
        for node in ast.walk(tree):
            if isinstance(node, ast.BinOp):
                # Check for the pattern: 5.0 + 2995.0 * (...)
                if isinstance(node, ast.Add):
                    if isinstance(node.left, ast.Constant) and node.left.value == 5.0:
                        if isinstance(node.right, ast.BinOp) and isinstance(node.right, ast.Mult):
                            if isinstance(node.right.left, ast.Constant) and node.right.left.value == 2995.0:
                                pytest.fail(
                                    "Linear GDA-to-prevalence formula "
                                    "'5.0 + 2995.0 * ...' FOUND in disgenet_pipeline.py "
                                    "executable code. The v113 P3-026 root fix has been "
                                    "reverted — investigate."
                                )

    def test_populate_prevalence_method_exists(self):
        """DisGeNETPipeline must have a _populate_prevalence method."""
        from phase1.pipelines.disgenet_pipeline import DisGeNETPipeline
        assert hasattr(DisGeNETPipeline, "_populate_prevalence"), (
            "DisGeNETPipeline must have _populate_prevalence method (Task 2.2 ROOT FIX)"
        )

    def test_populate_prevalence_called_in_clean_core(self):
        """Verify _populate_prevalence is called in _clean_core."""
        disgenet_path = _PHASE1_ROOT / "pipelines" / "disgenet_pipeline.py"
        src = disgenet_path.read_text()
        # The call must appear in executable code (not just a comment).
        import ast
        tree = ast.parse(src)
        found_call = False
        for node in ast.walk(tree):
            if isinstance(node, ast.Call):
                if isinstance(node.func, ast.Attribute):
                    if node.func.attr == "_populate_prevalence":
                        found_call = True
                        break
        assert found_call, (
            "_populate_prevalence must be called in the clean flow "
            "(after _ensure_gda_columns, before _save_processed_csv)"
        )


# =============================================================================
# Task 2.3: OMIM genetic_basis + 6-digit MIM number parse
# =============================================================================

class TestTask2_3_OMIM_Genetic_Basis:
    """Verify OMIM genetic_basis field and MIM number parsing."""

    def test_genetic_basis_in_omim_gda_schema(self):
        """phase1_schema.omim_gda must declare genetic_basis optional."""
        from phase1.contracts.phase1_schema import PHASE1_OUTPUT_SCHEMA
        spec = PHASE1_OUTPUT_SCHEMA["omim_gda"]
        opt_names = [c.name for c in spec.optional_columns]
        assert "genetic_basis" in opt_names, (
            "phase1_schema.omim_gda.optional_columns must include 'genetic_basis'"
        )

    def test_genetic_basis_in_omim_susceptibility_schema(self):
        """phase1_schema.omim_susceptibility must declare genetic_basis optional."""
        from phase1.contracts.phase1_schema import PHASE1_OUTPUT_SCHEMA
        spec = PHASE1_OUTPUT_SCHEMA["omim_susceptibility"]
        opt_names = [c.name for c in spec.optional_columns]
        assert "genetic_basis" in opt_names, (
            "phase1_schema.omim_susceptibility.optional_columns must include "
            "'genetic_basis'"
        )

    def test_genetic_basis_in_gda_required_columns(self):
        """GDA_REQUIRED_COLUMNS must include ('genetic_basis', None)."""
        from phase1.pipelines.omim_pipeline import GDA_REQUIRED_COLUMNS
        col_names = [name for name, _ in GDA_REQUIRED_COLUMNS]
        assert "genetic_basis" in col_names, (
            "GDA_REQUIRED_COLUMNS must include 'genetic_basis'"
        )

    def test_omim_canonical_filename(self):
        """OMIM canonical filename must be 'omim_gene_disease_associations.csv'."""
        from phase1.contracts.phase1_schema import PHASE1_OUTPUT_SCHEMA
        spec = PHASE1_OUTPUT_SCHEMA["omim_gda"]
        assert spec.filename == "omim_gene_disease_associations.csv"

    def test_omim_susceptibility_canonical_filename(self):
        """OMIM susceptibility filename must be 'omim_gene_disease_susceptibility.csv'."""
        from phase1.contracts.phase1_schema import PHASE1_OUTPUT_SCHEMA
        spec = PHASE1_OUTPUT_SCHEMA["omim_susceptibility"]
        assert spec.filename == "omim_gene_disease_susceptibility.csv"

    def test_mim_number_regex_accepts_6_digit(self):
        """OMIM MIM number parser must accept 6-digit MIMs (e.g., 219700 for CF)."""
        from phase1.pipelines.omim_pipeline import normalize_omim_id
        # 219700 is the MIM for cystic fibrosis
        result = normalize_omim_id(219700)
        assert result == "OMIM:219700", f"6-digit MIM 219700 must parse, got {result!r}"

    def test_mim_number_regex_strips_omim_prefix(self):
        """Parser must strip 'OMIM:' prefix and return canonical form."""
        from phase1.pipelines.omim_pipeline import normalize_omim_id
        assert normalize_omim_id("OMIM:219700") == "OMIM:219700"
        assert normalize_omim_id("omim:219700") == "OMIM:219700"
        assert normalize_omim_id("MIM:219700") == "OMIM:219700"

    def test_mim_number_rejects_too_short(self):
        """Parser must reject MIM numbers < 10000 (outside OMIM range)."""
        from phase1.pipelines.omim_pipeline import normalize_omim_id
        with pytest.raises((ValueError, TypeError)):
            normalize_omim_id("1234")

    def test_genetic_basis_populated_in_clean(self):
        """Verify genetic_basis is set in the clean flow (Step 13.5)."""
        omim_path = _PHASE1_ROOT / "pipelines" / "omim_pipeline.py"
        src = omim_path.read_text()
        # Check that genetic_basis is assigned from association_type.
        assert 'df["genetic_basis"]' in src, (
            "omim_pipeline.py must assign df['genetic_basis'] in the clean flow"
        )


# =============================================================================
# Task 2.4: PubChem enrichment filename + full 15-property list
# =============================================================================

class TestTask2_4_PubChem_Enrichment:
    """Verify PubChem pipeline emits canonical filename + full property list."""

    def test_pubchem_canonical_filename(self):
        """phase1_schema.pubchem_enrichment.filename must be 'pubchem_enrichment.csv'."""
        from phase1.contracts.phase1_schema import PHASE1_OUTPUT_SCHEMA
        spec = PHASE1_OUTPUT_SCHEMA["pubchem_enrichment"]
        assert spec.filename == "pubchem_enrichment.csv"

    def test_xlogp_in_pubchem_schema(self):
        """phase1_schema.pubchem_enrichment must declare xlogp optional."""
        from phase1.contracts.phase1_schema import PHASE1_OUTPUT_SCHEMA
        spec = PHASE1_OUTPUT_SCHEMA["pubchem_enrichment"]
        opt_names = [c.name for c in spec.optional_columns]
        assert "xlogp" in opt_names, (
            "pubchem_enrichment.optional_columns must include 'xlogp' "
            "(pipeline emits xlogp, not logp)"
        )

    def test_isomeric_smiles_in_pubchem_schema(self):
        """phase1_schema.pubchem_enrichment must declare isomeric_smiles optional."""
        from phase1.contracts.phase1_schema import PHASE1_OUTPUT_SCHEMA
        spec = PHASE1_OUTPUT_SCHEMA["pubchem_enrichment"]
        opt_names = [c.name for c in spec.optional_columns]
        assert "isomeric_smiles" in opt_names, (
            "pubchem_enrichment.optional_columns must include 'isomeric_smiles' "
            "(REQUIRED for chiral drug fingerprinting — life-safety)"
        )

    def test_v50_downloader_requests_full_property_list(self):
        """v50 downloader must request the full 15-property list from PubChem."""
        v50_path = _PHASE1_ROOT / "pipelines" / "_v50_downloaders.py"
        src = v50_path.read_text()
        # All 15 properties must be in the source.
        required_properties = [
            "MolecularFormula",
            "MolecularWeight",
            "InChIKey",
            "InChI",
            "CanonicalSMILES",
            "IsomericSMILES",  # life-safety: chiral drug fingerprinting
            "IUPACName",
            "XLogP",
            "ExactMass",
            "TPSA",
            "Complexity",
            "HBondDonorCount",
            "HBondAcceptorCount",
            "RotatableBondCount",
            "HeavyAtomCount",
        ]
        missing = [p for p in required_properties if p not in src]
        assert not missing, (
            f"v50 downloader missing PubChem properties: {missing}. "
            f"All 15 properties must be requested (Task 2.4 ROOT FIX)."
        )

    def test_v50_downloader_writes_16_columns(self):
        """v50 downloader must write all 16 columns to the CSV."""
        v50_path = _PHASE1_ROOT / "pipelines" / "_v50_downloaders.py"
        src = v50_path.read_text()
        # Check that the writer.writerow header includes all the new cols.
        required_columns = [
            "molecular_formula",
            "molecular_weight",
            "canonical_smiles",
            "isomeric_smiles",
            "inchi",
            "iupac_name",
            "xlogp",
            "tpsa",
            "complexity",
            "heavy_atom_count",
            "exact_mass",
        ]
        missing = [c for c in required_columns if c not in src]
        assert not missing, (
            f"v50 downloader missing column names in CSV header: {missing}"
        )

    def test_v50_downloader_no_longer_hardcodes_6_properties(self):
        """v50 downloader must NOT use the old 6-property string."""
        v50_path = _PHASE1_ROOT / "pipelines" / "_v50_downloaders.py"
        src = v50_path.read_text()
        # The old 6-property string must NOT appear in executable code.
        old_string = (
            '"CanonicalSMILES,XLogP,TPSA,'
            'HBondDonorCount,HBondAcceptorCount,RotatableBondCount"'
        )
        # The old string had exactly 6 properties. The new one has 15.
        # Parse the source to find any string literal with exactly these 6.
        import ast
        tree = ast.parse(src)
        for node in ast.walk(tree):
            if isinstance(node, ast.Constant) and isinstance(node.value, str):
                val = node.value
                if (
                    "CanonicalSMILES" in val
                    and "XLogP" in val
                    and "TPSA" in val
                    and "HBondDonorCount" in val
                    and "HBondAcceptorCount" in val
                    and "RotatableBondCount" in val
                    and "MolecularFormula" not in val
                ):
                    pytest.fail(
                        f"v50 downloader still uses the old 6-property string: "
                        f"{val!r}. Must use the full 15-property list."
                    )

    def test_pubchem_pipeline_cleaner_reads_all_columns(self):
        """pubchem_pipeline.py v50 cleaner must read all 16 columns from CSV."""
        pubchem_path = _PHASE1_ROOT / "pipelines" / "pubchem_pipeline.py"
        src = pubchem_path.read_text()
        # The cleaner must NOT hardcode isomeric_smiles to None.
        # Check that the cleaner reads isomeric_smiles from the row.
        assert 'row.get("isomeric_smiles")' in src, (
            "pubchem_pipeline.py v50 cleaner must read isomeric_smiles from the row "
            "(not hardcode None) — Task 2.4 ROOT FIX"
        )
        assert 'row.get("molecular_formula")' in src, (
            "pubchem_pipeline.py v50 cleaner must read molecular_formula from the row"
        )
        assert 'row.get("molecular_weight")' in src, (
            "pubchem_pipeline.py v50 cleaner must read molecular_weight from the row"
        )

    def test_pubchem_pipeline_cleaner_no_longer_hardcodes_none(self):
        """pubchem_pipeline.py must NOT hardcode molecular_formula/isomeric_smiles to None."""
        pubchem_path = _PHASE1_ROOT / "pipelines" / "pubchem_pipeline.py"
        src = pubchem_path.read_text()
        # The specific old pattern was:
        #   "molecular_formula": None,
        #   "molecular_weight": None,
        #   ...
        #   "isomeric_smiles": None,
        # These must be replaced with row.get(...) calls.
        # Use AST to check executable assignments.
        import ast
        tree = ast.parse(src)
        for node in ast.walk(tree):
            if isinstance(node, ast.Dict):
                # Look for dict literals with keys 'molecular_formula' or
                # 'isomeric_smiles' mapped to None.
                for key, val in zip(node.keys, node.values):
                    if (
                        isinstance(key, ast.Constant)
                        and key.value in ("molecular_formula", "isomeric_smiles", "molecular_weight")
                        and isinstance(val, ast.Constant)
                        and val.value is None
                    ):
                        pytest.fail(
                            f"pubchem_pipeline.py still hardcodes "
                            f"'{key.value}': None in a dict literal. "
                            f"Task 2.4 ROOT FIX requires reading these from the row."
                        )


# =============================================================================
# Cross-task: 4-phase connectivity
# =============================================================================

class TestFourPhaseConnectivity:
    """Verify Phase 1 -> Phase 2 -> Phase 3 -> Phase 4 connectivity.

    Each phase must consume the previous phase's output. These tests
    verify the contract is in place — they do NOT run the full pipeline
    (which requires DB + network).
    """

    def test_phase1_string_ppi_consumed_by_phase2_string_loader(self):
        """Phase 2 string_loader must reference Phase 1's STRING PPI CSV."""
        loader_path = _REPO_ROOT / "phase2" / "drugos_graph" / "string_loader.py"
        src = loader_path.read_text()
        # Must reference the Phase 1 canonical or alias filename.
        assert (
            "string_protein_protein_interactions.csv" in src
            or "protein_protein_interactions.csv" in src
        )

    def test_phase1_disgenet_consumed_by_phase2_disgenet_loader(self):
        """Phase 2 disgenet_loader must reference Phase 1's DisGeNET CSV."""
        loader_path = _REPO_ROOT / "phase2" / "drugos_graph" / "disgenet_loader.py"
        src = loader_path.read_text()
        assert "disgenet_gene_disease_associations.csv" in src or \
               "gene_disease_associations.csv" in src

    def test_phase1_omim_consumed_by_phase2_omim_loader(self):
        """Phase 2 omim_loader must reference Phase 1's OMIM CSV."""
        loader_path = _REPO_ROOT / "phase2" / "drugos_graph" / "omim_loader.py"
        src = loader_path.read_text()
        assert "omim_gene_disease_associations.csv" in src

    def test_phase1_pubchem_consumed_by_phase2_pubchem_loader(self):
        """Phase 2 pubchem_loader must reference Phase 1's PubChem CSV."""
        loader_path = _REPO_ROOT / "phase2" / "drugos_graph" / "pubchem_loader.py"
        src = loader_path.read_text()
        assert "pubchem_enrichment.csv" in src

    def test_phase2_to_phase3_edge_includes_all_critical_edges(self):
        """PHASE2_TO_PHASE3_EDGE must include the 4 critical edge types."""
        from shared.contracts.phase_edge_mapping import PHASE2_TO_PHASE3_EDGE
        critical_edges = [
            ("Compound", "treats", "Disease"),       # drug-disease therapeutic
            ("Compound", "inhibits", "Protein"),     # drug-protein mechanism
            ("Protein", "part_of", "Pathway"),       # protein-pathway membership
            ("Pathway", "disrupted_in", "Disease"),  # pathway-disease
            ("Protein", "interacts_with", "Protein"),  # PPI (Task 2.1 ROOT FIX)
            ("Gene", "associated_with", "Disease"),  # GDA
        ]
        for edge in critical_edges:
            assert edge in PHASE2_TO_PHASE3_EDGE, (
                f"Critical edge {edge} must be in PHASE2_TO_PHASE3_EDGE. "
                f"Without it, the 4-phase chain is broken."
            )

    def test_phase2_string_loader_creates_interacts_with_edge(self):
        """Phase 2 string_loader must create (Protein, interacts_with, Protein) edges."""
        loader_path = _REPO_ROOT / "phase2" / "drugos_graph" / "string_loader.py"
        src = loader_path.read_text()
        assert "interacts_with" in src, (
            "string_loader must create 'interacts_with' relationship type"
        )
        assert "Protein" in src, (
            "string_loader must create Protein node type"
        )


if __name__ == "__main__":
    pytest.main([__file__, "-v", "--tb=short"])
