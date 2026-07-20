"""TASK 4.1 contract test: verify chembl_loader reads every Phase 1 ChEMBL
CSV column produced by chembl_pipeline.

The task spec says:
    "Verify chembl_loader reads every column produced by chembl_pipeline.
     Specifically check: chembl_id, inchikey, smiles, activity_type,
     activity_value, activity_units, target_chembl_id, target_uniprot_id.
     Add a contract test that loads a 10-row sample and asserts no field
     is None."

This test is NOT a source-text-grep test. It WRITES a 10-row sample
DataFrame that mimics what Phase 1's chembl_pipeline ACTUALLY emits
(column name ``target_accession`` for the UniProt accession -- NOT
``uniprot_accession``), passes it through the loader, and asserts
every required field is populated on the output records.

Run:
    cd /path/to/repo
    PYTHONPATH=.:phase2 python -m pytest phase2/tests/test_chembl_loader_contract.py -v
"""
from __future__ import annotations

import sys
from pathlib import Path

import pandas as pd
import pytest

# Path bootstrap (same as other phase2 test files)
_HERE = Path(__file__).resolve().parent
_REPO_ROOT = _HERE.parents[1]
_PHASE2_ROOT = _HERE.parent
for _p in (str(_REPO_ROOT), str(_PHASE2_ROOT)):
    if _p not in sys.path:
        sys.path.insert(0, _p)


# ===========================================================================
# TASK 4.1: 10-row sample DataFrames mimicking Phase 1's chembl_pipeline
# output. The column names match what chembl_pipeline.py ACTUALLY emits:
#   - chembl_drugs.csv       -> chembl_id, inchikey, smiles, name, ...
#   - chembl_activities_clean.csv -> molecule_chembl_id, target_chembl_id,
#                                    target_accession (NOT uniprot_accession!),
#                                    activity_type, activity_value,
#                                    activity_units, pchembl_value,
#                                    standard_relation
# ===========================================================================

_SAMPLE_DRUGS_ROWS = [
    {
        "chembl_id": "CHEMBL25", "name": "Aspirin",
        "inchikey": "BSYNRYMUTXBXSQ-UHFFFAOYSA-N",
        "smiles": "CC(=O)OC1=CC=CC=C1C(=O)O",
        "molecular_weight": 180.16, "max_phase": 4, "is_fda_approved": True,
    },
    {
        "chembl_id": "CHEMBL112", "name": "Acetaminophen",
        "inchikey": "RZVAJINKPMORJF-UHFFFAOYSA-N",
        "smiles": "CC(=O)NC1=CC=C(O)C=C1",
        "molecular_weight": 151.16, "max_phase": 4, "is_fda_approved": True,
    },
    {
        "chembl_id": "CHEMBL744", "name": "Ibuprofen",
        "inchikey": "HEFNNWSXXWATIW-UHFFFAOYSA-N",
        "smiles": "CC(C)CC1=CC=C(C=C1)C(C)C(=O)O",
        "molecular_weight": 206.28, "max_phase": 4, "is_fda_approved": True,
    },
    {
        "chembl_id": "CHEMBL546", "name": "Warfarin",
        "inchikey": "PJVWKTKQMONHTF-UHFFFAOYSA-N",
        "smiles": "CC(=O)CC(C1=CC=CC=C1)C2=C(O)C3=CC=CC=C3OC2=O",
        "molecular_weight": 308.33, "max_phase": 4, "is_fda_approved": True,
    },
    {
        "chembl_id": "CHEMBL404", "name": "Diazepam",
        "inchikey": "AAUVKQBHMPKKQR-UHFFFAOYSA-N",
        "smiles": "ClC1=CC2=C(C=C1)C(=NCC(=O)N2C)C3=CC=CC=C3",
        "molecular_weight": 284.74, "max_phase": 4, "is_fda_approved": True,
    },
    {
        "chembl_id": "CHEMBL1383", "name": "Omeprazole",
        "inchikey": "SUBDBMMJDZJVPE-UHFFFAOYSA-N",
        "smiles": "COc1ccc2[nH]c(nc2c1)S(=O)Cc1nnc(C)o1",
        "molecular_weight": 345.42, "max_phase": 4, "is_fda_approved": True,
    },
    {
        "chembl_id": "CHEMBL658", "name": "Metformin",
        "inchikey": "XZWYZXLIPXDOLG-UHFFFAOYSA-N",
        "smiles": "CN(C)C(=N)N=C(N)N",
        "molecular_weight": 129.16, "max_phase": 4, "is_fda_approved": True,
    },
    {
        "chembl_id": "CHEMBL819", "name": "Atorvastatin",
        "inchikey": "XUKUURHRXDUEBC-CAQYLUSBKJ-N",
        "smiles": "CC(C)c1c(C(=O)NCc2ccccc2)c(c2ccc(F)cc2)c(c1C(C)C)C(=O)Nc1ccc(O)cc1",
        "molecular_weight": 558.66, "max_phase": 4, "is_fda_approved": True,
    },
    {
        "chembl_id": "CHEMBL148", "name": "Caffeine",
        "inchikey": "RYYVLZVUVIJVGH-UHFFFAOYSA-N",
        "smiles": "CN1C=NC2=C1C(=O)N(C(=O)N2C)C",
        "molecular_weight": 194.19, "max_phase": 4, "is_fda_approved": True,
    },
    {
        "chembl_id": "CHEMBL137", "name": "Simvastatin",
        "inchikey": "RYMZZMVNOQMWHX-GQBYOXTTSA-N",
        "smiles": "CC(C)C1C(C(C(=O)O1)C)OC(=O)CCc2c3ccc(cc3oc2C)C",
        "molecular_weight": 418.57, "max_phase": 4, "is_fda_approved": True,
    },
]

_SAMPLE_ACTIVITIES_ROWS = [
    # 10 rows of activity data. The column ``target_accession`` is what
    # phase1/pipelines/chembl_pipeline.py ACTUALLY emits (NOT
    # ``uniprot_accession`` -- the previous loader was reading the wrong
    # column name and silently dropping every real UniProt AC).
    {
        "molecule_chembl_id": "CHEMBL25", "target_chembl_id": "CHEMBL218",
        "target_accession": "P23219",  # PTGS2 (COX-2)
        "activity_type": "IC50", "activity_value": 100.0, "activity_units": "nM",
        "pchembl_value": 7.0, "standard_relation": "=",
    },
    {
        "molecule_chembl_id": "CHEMBL25", "target_chembl_id": "CHEMBL221",
        "target_accession": "P35354",  # PTGS1 (COX-1)
        "activity_type": "IC50", "activity_value": 5.0, "activity_units": "nM",
        "pchembl_value": 8.3, "standard_relation": "=",
    },
    {
        "molecule_chembl_id": "CHEMBL112", "target_chembl_id": "CHEMBL218",
        "target_accession": "P23219",
        "activity_type": "IC50", "activity_value": 1000.0, "activity_units": "nM",
        "pchembl_value": 6.0, "standard_relation": ">",
    },
    {
        "molecule_chembl_id": "CHEMBL744", "target_chembl_id": "CHEMBL218",
        "target_accession": "P23219",
        "activity_type": "IC50", "activity_value": 2.6, "activity_units": "nM",
        "pchembl_value": 8.6, "standard_relation": "=",
    },
    {
        "molecule_chembl_id": "CHEMBL546", "target_chembl_id": "CHEMBL206",
        "target_accession": "P00735",  # F2 (prothrombin)
        "activity_type": "Ki", "activity_value": 1.5, "activity_units": "nM",
        "pchembl_value": 8.8, "standard_relation": "=",
    },
    {
        "molecule_chembl_id": "CHEMBL404", "target_chembl_id": "CHEMBL226",
        "target_accession": "P14867",  # GABRA1
        "activity_type": "Ki", "activity_value": 14.0, "activity_units": "nM",
        "pchembl_value": 7.85, "standard_relation": "=",
    },
    {
        "molecule_chembl_id": "CHEMBL1383", "target_chembl_id": "CHEMBL236",
        "target_accession": "P09172",  # ATP4A
        "activity_type": "IC50", "activity_value": 0.39, "activity_units": "uM",
        "pchembl_value": 6.4, "standard_relation": "=",
    },
    {
        "molecule_chembl_id": "CHEMBL658", "target_chembl_id": "CHEMBL218",
        "target_accession": "P23219",
        "activity_type": "IC50", "activity_value": 1000.0, "activity_units": "nM",
        "pchembl_value": 6.0, "standard_relation": ">",
    },
    {
        "molecule_chembl_id": "CHEMBL148", "target_chembl_id": "CHEMBL242",
        "target_accession": "P08172",  # ADA1A
        "activity_type": "Ki", "activity_value": 7.7, "activity_units": "nM",
        "pchembl_value": 8.1, "standard_relation": "=",
    },
    {
        "molecule_chembl_id": "CHEMBL137", "target_chembl_id": "CHEMBL218",
        "target_accession": "P23219",
        "activity_type": "IC50", "activity_value": 50.0, "activity_units": "nM",
        "pchembl_value": 7.3, "standard_relation": "=",
    },
]


@pytest.fixture
def sample_drugs_df() -> pd.DataFrame:
    return pd.DataFrame(_SAMPLE_DRUGS_ROWS)


@pytest.fixture
def sample_activities_df() -> pd.DataFrame:
    return pd.DataFrame(_SAMPLE_ACTIVITIES_ROWS)


# ===========================================================================
# TASK 4.1 contract: every required column is read by the loader.
# ===========================================================================

class TestChemblLoaderContractTask41:
    """TASK 4.1: verify chembl_loader reads every Phase 1 ChEMBL column."""

    def test_node_records_contain_every_required_field(self, sample_drugs_df):
        """Every Compound node must have chembl_id, inchikey, smiles populated."""
        from drugos_graph.chembl_loader import chembl_to_node_records_from_phase1

        nodes = chembl_to_node_records_from_phase1(sample_drugs_df)
        assert len(nodes) == 10, f"Expected 10 nodes, got {len(nodes)}"

        for i, node in enumerate(nodes):
            # chembl_id
            assert node.get("chembl_id"), f"Row {i}: chembl_id missing"
            assert node["chembl_id"].startswith("CHEMBL"), (
                f"Row {i}: chembl_id {node['chembl_id']!r} doesn't look like a ChEMBL ID"
            )
            # inchikey (27-char canonical: 14 + "-" + 10 + "-" + 1)
            assert node.get("inchikey"), f"Row {i}: inchikey missing"
            assert len(node["inchikey"]) == 27, (
                f"Row {i}: inchikey {node['inchikey']!r} is not 27 chars"
            )
            assert node["inchikey"][14] == "-" and node["inchikey"][25] == "-", (
                f"Row {i}: inchikey {node['inchikey']!r} doesn't match 14-10-1 pattern"
            )
            # smiles
            assert node.get("smiles"), f"Row {i}: smiles missing"

    def test_edge_records_contain_every_required_field(self, sample_activities_df):
        """Every edge must have target_chembl_id + target_uniprot_id populated.

        The TASK 4.1 spec requires the loader to read these columns:
          - chembl_id (via molecule_chembl_id)        ✓
          - inchikey (used for src_id normalization)  ✓
          - smiles                                    (node-side)
          - activity_type                             ✓
          - activity_value                            ✓
          - activity_units                            ✓
          - target_chembl_id                          ✓
          - target_uniprot_id                         ✓ (KEY FIX)

        The KEY FIX is target_uniprot_id: the previous loader read the
        column name ``uniprot_accession``, but Phase 1's chembl_pipeline
        ACTUALLY emits it as ``target_accession``. Every row with a real
        UniProt AC was silently dropped and replaced with a synthetic
        ``CHEMBL_TGT_<digits>`` placeholder. This test verifies the fix.
        """
        from drugos_graph.chembl_loader import chembl_to_edge_records_from_phase1

        edges = chembl_to_edge_records_from_phase1(sample_activities_df)
        assert len(edges) == 10, f"Expected 10 edges, got {len(edges)}"

        for i, edge in enumerate(edges):
            props = edge.get("props", {})
            # target_chembl_id
            tci = props.get("target_chembl_id")
            assert tci, f"Edge {i}: target_chembl_id missing"
            assert tci.startswith("CHEMBL"), (
                f"Edge {i}: target_chembl_id {tci!r} doesn't look like a ChEMBL target ID"
            )
            # target_uniprot_id (KEY FIX -- was being silently dropped)
            tupi = props.get("target_uniprot_id")
            assert tupi, (
                f"Edge {i}: target_uniprot_id missing -- the loader is NOT "
                f"reading the Phase 1 'target_accession' column. TASK 4.1 ROOT "
                f"FIX REGRESSION."
            )
            # UniProt accessions are 6-10 chars, alphanumeric, start with
            # [OPQ] or [A-NR-Z]. We accept either real ACs (P23219) or
            # the synthetic CHEMBL_TGT_<digits> fallback (which means no
            # real AC was available -- acceptable when target_accession
            # is genuinely missing, but NOT acceptable here since the
            # sample data has real ACs on every row).
            assert not tupi.startswith("CHEMBL_TGT_"), (
                f"Edge {i}: target_uniprot_id is the synthetic fallback "
                f"{tupi!r} -- the loader did NOT read the real UniProt AC "
                f"from the 'target_accession' column. TASK 4.1 ROOT FIX "
                f"REGRESSION."
            )
            # activity_type
            at = props.get("activity_type")
            assert at, f"Edge {i}: activity_type missing"
            assert at in ("IC50", "Ki", "Kd", "EC50"), (
                f"Edge {i}: activity_type {at!r} is not a standard type"
            )
            # activity_value
            av = props.get("activity_value")
            assert av is not None, f"Edge {i}: activity_value missing"
            assert av > 0, f"Edge {i}: activity_value {av!r} should be > 0"
            # activity_units
            au = props.get("activity_units")
            assert au, f"Edge {i}: activity_units missing"

    def test_loader_reads_target_accession_alias(self):
        """Specifically verify the ``target_accession`` alias is read.

        Phase 1's chembl_pipeline.py emits the UniProt AC under the
        column name ``target_accession`` (see
        phase1/pipelines/chembl_pipeline.py:_resolve_target_accessions).
        The previous loader read only ``uniprot_accession`` -- a
        different name -- so every real AC was silently dropped.
        """
        from drugos_graph.chembl_loader import chembl_to_edge_records_from_phase1

        df = pd.DataFrame([
            {
                "molecule_chembl_id": "CHEMBL25",
                "target_chembl_id": "CHEMBL218",
                "target_accession": "P23219",  # what pipeline emits
                "activity_type": "IC50", "activity_value": 100.0,
                "activity_units": "nM", "pchembl_value": 7.0,
                "standard_relation": "=",
            },
        ])
        edges = chembl_to_edge_records_from_phase1(df)
        assert len(edges) == 1
        assert edges[0]["props"]["target_uniprot_id"] == "P23219"
        # dst_id should also be the real UniProt AC (not CHEMBL_TGT_xxx)
        assert edges[0]["dst_id"] == "P23219"

    def test_loader_reads_other_uniprot_aliases(self):
        """Verify the loader also reads the legacy/spec alias names.

        The loader should accept ANY of these column names for the
        UniProt AC (in priority order):
          1. target_accession     (what phase1_pipeline emits)
          2. uniprot_accession    (legacy chembl_loader)
          3. target_uniprot_id    (task 4.1 spec)
          4. uniprot_id           (phase1_schema)
          5. accession            (bare alias)
        """
        from drugos_graph.chembl_loader import chembl_to_edge_records_from_phase1

        for alias in (
            "target_accession",
            "uniprot_accession",
            "target_uniprot_id",
            "uniprot_id",
            "accession",
        ):
            df = pd.DataFrame([
                {
                    "molecule_chembl_id": "CHEMBL25",
                    "target_chembl_id": "CHEMBL218",
                    alias: "P23219",
                    "activity_type": "IC50", "activity_value": 100.0,
                    "activity_units": "nM", "pchembl_value": 7.0,
                    "standard_relation": "=",
                },
            ])
            edges = chembl_to_edge_records_from_phase1(df)
            assert len(edges) == 1, f"Alias {alias!r}: no edge emitted"
            assert edges[0]["props"]["target_uniprot_id"] == "P23219", (
                f"Alias {alias!r}: target_uniprot_id not populated correctly"
            )
            assert edges[0]["dst_id"] == "P23219", (
                f"Alias {alias!r}: dst_id not the real UniProt AC"
            )
