"""TASK 4.2 contract test: verify drugbank_loader consumes is_withdrawn
flag (and the withdrawn_reason/country/year forward-compat fields) from
Phase 1's drugbank_drugs.csv with the P2-050 criticality-inversion FIXED.

The task spec says:
    "drugbank_loader must read is_withdrawn, withdrawn_reason,
     withdrawn_country, withdrawn_year from drugbank_drugs.csv and
     create Drug nodes with these properties. Verify the loader sets
     confidence correctly: withdrawn drugs should have HIGHER
     criticality (lower safety) not lower (P2-050 inverted)."

Verification: ``python -m pytest phase2/tests/test_drugbank_withdrawn.py -v``
"""
from __future__ import annotations

import sys
from pathlib import Path

import pandas as pd
import pytest

# Path bootstrap
_HERE = Path(__file__).resolve().parent
_REPO_ROOT = _HERE.parents[1]
_PHASE2_ROOT = _HERE.parent
for _p in (str(_REPO_ROOT), str(_PHASE2_ROOT)):
    if _p not in sys.path:
        sys.path.insert(0, _p)


# ===========================================================================
# 10-row sample DataFrame mimicking Phase 1's drugbank_drugs.csv with
# the four patient-safety columns populated.
# ===========================================================================

_SAMPLE_DRUGBANK_ROWS = [
    {
        "name": "Aspirin", "drugbank_id": "DB00945", "chembl_id": "CHEMBL25",
        "inchikey": "BSYNRYMUTXBXSQ-UHFFFAOYSA-N",
        "smiles": "CC(=O)OC1=CC=CC=C1C(=O)O",
        "is_withdrawn": False,
    },
    {
        "name": "Acetaminophen", "drugbank_id": "DB00316", "chembl_id": "CHEMBL112",
        "inchikey": "RZVAJINKPMORJF-UHFFFAOYSA-N",
        "is_withdrawn": False,
    },
    {
        "name": "Ibuprofen", "drugbank_id": "DB01050", "chembl_id": "CHEMBL744",
        "inchikey": "HEFNNWSXXWATIW-UHFFFAOYSA-N",
        "is_withdrawn": False,
    },
    # Withdrawn drugs -- patient-safety critical.
    {
        "name": "Valdecoxib", "drugbank_id": "DB00533", "chembl_id": "CHEMBL1098",
        "inchikey": "LGPQKFXOTQZKCE-UHFFFAOYSA-N",
        "is_withdrawn": True,
        "withdrawn_reason": "severe skin reactions (Stevens-Johnson syndrome)",
        "withdrawn_country": "global",
        "withdrawn_year": 2005,
    },
    {
        "name": "Rofecoxib", "drugbank_id": "DB00795", "chembl_id": "CHEMBL122",
        "inchikey": "RJIAFGOPVQFVHE-UHFFFAOYSA-N",
        "is_withdrawn": True,
        "withdrawn_reason": "cardiotoxicity (increased risk of heart attack)",
        "withdrawn_country": "global",
        "withdrawn_year": 2004,
    },
    {
        "name": "Terfenadine", "drugbank_id": "DB00642", "chembl_id": "CHEMBL1240",
        "inchikey": "GUGOEWJFRGUGOF-UHFFFAOYSA-N",
        "is_withdrawn": True,
        "withdrawn_reason": "cardiac arrhythmia (QT prolongation)",
        "withdrawn_country": "US",
        "withdrawn_year": 1998,
    },
    {
        "name": "Cisapride", "drugbank_id": "DB00604", "chembl_id": "CHEMBL1226",
        "inchikey": "DCSUBABJRXZOMT-UHFFFAOYSA-N",
        "is_withdrawn": True,
        "withdrawn_reason": "cardiotoxicity (QT prolongation, fatal arrhythmias)",
        "withdrawn_country": "US",
        "withdrawn_year": 2000,
    },
    {
        "name": "Phenylpropanolamine", "drugbank_id": "DB00397", "chembl_id": "CHEMBL771",
        "inchikey": "DCYULCBWUIYOWT-UHFFFAOYSA-N",
        "is_withdrawn": True,
        "withdrawn_reason": "increased risk of hemorrhagic stroke",
        "withdrawn_country": "US",
        "withdrawn_year": 2000,
    },
    {
        "name": "Sibutramine", "drugbank_id": "DB01195", "chembl_id": "CHEMBL779",
        "inchikey": "YULUGBSKBIAHHK-UHFFFAOYSA-N",
        "is_withdrawn": True,
        "withdrawn_reason": "cardiovascular events (severe)",
        "withdrawn_country": "global",
        "withdrawn_year": 2010,
    },
    {
        "name": "Tegaserod", "drugbank_id": "DB00820", "chembl_id": "CHEMBL1167",
        "inchikey": "RRGUKUMVNBBFGQ-UHFFFAOYSA-N",
        "is_withdrawn": True,
        "withdrawn_reason": "cardiovascular risk (severe)",
        "withdrawn_country": "US",
        "withdrawn_year": 2007,
    },
]


@pytest.fixture
def sample_drugbank_df() -> pd.DataFrame:
    return pd.DataFrame(_SAMPLE_DRUGBANK_ROWS)


# ===========================================================================
# TASK 4.2 contract: drugbank_loader reads every required column AND
# sets criticality correctly (P2-050 inversion fixed).
# ===========================================================================

class TestDrugbankLoaderWithdrawnTask42:
    """TASK 4.2: verify drugbank_loader consumes is_withdrawn + family."""

    def test_loader_file_exists(self):
        """The drugbank_loader.py file must exist (it didn't before this fix)."""
        from drugos_graph import drugbank_loader
        assert hasattr(drugbank_loader, "drugbank_to_node_records_from_phase1")
        assert hasattr(drugbank_loader, "compute_criticality")
        assert hasattr(drugbank_loader, "compute_safety_score")
        assert hasattr(drugbank_loader, "DrugBankLoader")

    def test_node_records_contain_withdrawn_fields(self, sample_drugbank_df):
        """Every Compound node must have is_withdrawn + the 3 forward-compat fields."""
        from drugos_graph.drugbank_loader import drugbank_to_node_records_from_phase1

        nodes = drugbank_to_node_records_from_phase1(sample_drugbank_df)
        assert len(nodes) == 10, f"Expected 10 nodes, got {len(nodes)}"

        for i, node in enumerate(nodes):
            # is_withdrawn must NEVER be null (patient-safety contract).
            assert "is_withdrawn" in node, f"Row {i}: is_withdrawn key missing"
            assert node["is_withdrawn"] is not None, (
                f"Row {i}: is_withdrawn is None -- patient-safety contract "
                f"violated (must be bool, never null)."
            )
            assert isinstance(node["is_withdrawn"], bool), (
                f"Row {i}: is_withdrawn is {type(node['is_withdrawn'])}, "
                f"expected bool"
            )
            # Forward-compat fields (may be None when Phase 1 doesn't emit).
            assert "withdrawn_reason" in node, f"Row {i}: withdrawn_reason key missing"
            assert "withdrawn_country" in node, f"Row {i}: withdrawn_country key missing"
            assert "withdrawn_year" in node, f"Row {i}: withdrawn_year key missing"
            # safety_score must be a real float in [0, 1].
            assert "safety_score" in node, f"Row {i}: safety_score key missing"
            assert isinstance(node["safety_score"], float), (
                f"Row {i}: safety_score is {type(node['safety_score'])}, expected float"
            )
            assert 0.0 <= node["safety_score"] <= 1.0, (
                f"Row {i}: safety_score {node['safety_score']} out of [0,1]"
            )
            # criticality must be a real float in [0, 1].
            assert "criticality" in node, f"Row {i}: criticality key missing"
            assert isinstance(node["criticality"], float)
            assert 0.0 <= node["criticality"] <= 1.0

    def test_p2_050_inversion_fixed(self, sample_drugbank_df):
        """P2-050 ROOT FIX: withdrawn drugs MUST have HIGHER criticality
        (lower safety) than non-withdrawn drugs. The previous implementation
        INVERTED this -- surfacing withdrawn drugs as top repurposing candidates.
        """
        from drugos_graph.drugbank_loader import drugbank_to_node_records_from_phase1

        nodes = drugbank_to_node_records_from_phase1(sample_drugbank_df)
        withdrawn = [n for n in nodes if n["is_withdrawn"]]
        non_withdrawn = [n for n in nodes if not n["is_withdrawn"]]

        assert len(withdrawn) == 7, f"Expected 7 withdrawn drugs, got {len(withdrawn)}"
        assert len(non_withdrawn) == 3, f"Expected 3 non-withdrawn, got {len(non_withdrawn)}"

        # The KEY assertion: withdrawn drugs have HIGHER criticality.
        avg_withdrawn_crit = sum(n["criticality"] for n in withdrawn) / len(withdrawn)
        avg_non_withdrawn_crit = sum(n["criticality"] for n in non_withdrawn) / len(non_withdrawn)
        assert avg_withdrawn_crit > avg_non_withdrawn_crit, (
            f"P2-050 INVERSION NOT FIXED: withdrawn drugs have avg criticality "
            f"{avg_withdrawn_crit:.4f} which is NOT higher than non-withdrawn "
            f"{avg_non_withdrawn_crit:.4f}."
        )

        # And: withdrawn drugs have LOWER safety_score.
        avg_withdrawn_safety = sum(n["safety_score"] for n in withdrawn) / len(withdrawn)
        avg_non_withdrawn_safety = sum(n["safety_score"] for n in non_withdrawn) / len(non_withdrawn)
        assert avg_withdrawn_safety < avg_non_withdrawn_safety, (
            f"P2-050 INVERSION: withdrawn drugs have avg safety_score "
            f"{avg_withdrawn_safety:.4f} which is NOT lower than non-withdrawn "
            f"{avg_non_withdrawn_safety:.4f}."
        )

        # Spot-check Valdecoxib (withdrawn globally for severe skin reactions).
        valdecoxib = next(n for n in nodes if n["name"] == "Valdecoxib")
        assert valdecoxib["is_withdrawn"] is True
        assert valdecoxib["withdrawn_reason"] == "severe skin reactions (Stevens-Johnson syndrome)"
        assert valdecoxib["withdrawn_country"] == "global"
        assert valdecoxib["withdrawn_year"] == 2005
        # Valdecoxib should have high criticality (low safety).
        assert valdecoxib["criticality"] >= 0.5, (
            f"Valdecoxib criticality {valdecoxib['criticality']:.4f} should be >= 0.5"
        )
        assert valdecoxib["safety_score"] <= 0.5, (
            f"Valdecoxib safety_score {valdecoxib['safety_score']:.4f} should be <= 0.5"
        )

        # Spot-check Aspirin (not withdrawn -- clean record).
        aspirin = next(n for n in nodes if n["name"] == "Aspirin")
        assert aspirin["is_withdrawn"] is False
        assert aspirin["criticality"] == 0.0, (
            f"Aspirin criticality {aspirin['criticality']:.4f} should be 0.0"
        )
        assert aspirin["safety_score"] == 0.85, (
            f"Aspirin safety_score {aspirin['safety_score']:.4f} should be 0.85"
        )

    def test_criticality_function_direction(self):
        """Direct unit test of compute_criticality -- the P2-050 ROOT FIX."""
        from drugos_graph.drugbank_loader import compute_criticality

        # Withdrawn drug with low safety (0.10) -> HIGH criticality (0.90).
        assert compute_criticality(True, 0.10) == 0.90, (
            f"Withdrawn drug with safety=0.10 should have criticality=0.90, "
            f"got {compute_criticality(True, 0.10)}"
        )
        # Withdrawn drug with very low safety (0.05) -> VERY HIGH criticality (0.95).
        assert compute_criticality(True, 0.05) == 0.95
        # Non-withdrawn drug -- criticality is 0.0 (no extra review urgency).
        assert compute_criticality(False, 0.85) == 0.0
        assert compute_criticality(False, 0.10) == 0.0
        # Unknown withdrawal status -- conservative 0.5 (surface for review).
        assert compute_criticality(None, 0.50) == 0.5

    def test_safety_score_real_signal(self):
        """Verify compute_safety_score returns REAL values, not random."""
        from drugos_graph.drugbank_loader import compute_safety_score

        # Clean drug -> 0.85.
        assert compute_safety_score({"is_withdrawn": False}) == 0.85
        # Withdrawn globally + severe + recent -> 0.05 (very unsafe).
        s = compute_safety_score({
            "is_withdrawn": True,
            "withdrawn_reason": "cardiotoxicity (fatal arrhythmias)",
            "withdrawn_country": "global",
            "withdrawn_year": 2015,
        })
        assert s == 0.05, f"Expected 0.05, got {s}"
        # Withdrawn globally + older + severe -> still 0.05 (severe penalty dominates).
        # The compute_safety_score applies the severe penalty BEFORE the year
        # check, so any severe-reason global withdrawal gets 0.05 regardless
        # of year. This is intentional -- a severe AE is severe regardless
        # of when it was withdrawn.
        s = compute_safety_score({
            "is_withdrawn": True,
            "withdrawn_reason": "hepatotoxicity",
            "withdrawn_country": "global",
            "withdrawn_year": 1990,
        })
        assert s == 0.05, f"Expected 0.05 (severe global), got {s}"
        # Withdrawn globally + NON-severe + older -> 0.20.
        s = compute_safety_score({
            "is_withdrawn": True,
            "withdrawn_reason": "lack of efficacy",
            "withdrawn_country": "global",
            "withdrawn_year": 1990,
        })
        assert s == 0.20, f"Expected 0.20 (non-severe global old), got {s}"
        # Withdrawn globally + NON-severe + recent -> 0.10.
        s = compute_safety_score({
            "is_withdrawn": True,
            "withdrawn_reason": "lack of efficacy",
            "withdrawn_country": "global",
            "withdrawn_year": 2015,
        })
        assert s == 0.10, f"Expected 0.10 (non-severe global recent), got {s}"
        # Unknown status -> 0.50 (conservative).
        assert compute_safety_score({"is_withdrawn": None}) == 0.50

    def test_loader_protocol_satisfied(self):
        """DrugBankLoader must satisfy the Loader Protocol (D1-002)."""
        from drugos_graph.drugbank_loader import DrugBankLoader
        from drugos_graph._loader_protocol import Loader

        loader = DrugBankLoader()
        # Structural typing -- has the 3 required methods + name attribute.
        assert hasattr(loader, "name")
        assert hasattr(loader, "download")
        assert hasattr(loader, "parse")
        assert hasattr(loader, "to_graph")
        assert loader.name == "drugbank"

    def test_loader_handles_missing_withdrawn_columns(self):
        """Verify the loader doesn't crash when Phase 1 only emits is_withdrawn
        (no withdrawn_reason/country/year -- the current Phase 1 state)."""
        from drugos_graph.drugbank_loader import drugbank_to_node_records_from_phase1

        df = pd.DataFrame([
            {
                "name": "Aspirin", "drugbank_id": "DB00945",
                "inchikey": "BSYNRYMUTXBXSQ-UHFFFAOYSA-N",
                "is_withdrawn": False,
                # NOTE: no withdrawn_reason, withdrawn_country, withdrawn_year
            },
        ])
        nodes = drugbank_to_node_records_from_phase1(df)
        assert len(nodes) == 1
        n = nodes[0]
        assert n["is_withdrawn"] is False
        # Forward-compat fields default to None / empty when Phase 1 doesn't emit.
        assert n["withdrawn_reason"] is None
        assert n["withdrawn_country"] is None
        assert n["withdrawn_year"] is None
        # Safety score still computed from is_withdrawn=False -> 0.85.
        assert n["safety_score"] == 0.85
