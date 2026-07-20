"""TASK 4.3 contract test: verify entity_resolver builds an InChIKey-canonical
crosswalk from Phase 1 sources (ChEMBL + DrugBank + PubChem).

The task spec says:
    "Entity resolver must use InChIKey (14+2+1 char canonical) as the
     canonical drug identifier per spec. Currently only 30-entry
     crosswalk (P2-020). Fix: (1) build crosswalk from all Phase 1
     sources (ChEMBL, DrugBank, PubChem) keyed by InChIKey; (2) verify
     each drug appears exactly once; (3) flag duplicates for review."

Verification: ``python -m pytest phase2/tests/test_entity_resolver_inchikey.py -v``
"""
from __future__ import annotations

import csv
import os
import sys
from pathlib import Path

import pytest

# Path bootstrap
_HERE = Path(__file__).resolve().parent
_REPO_ROOT = _HERE.parents[1]
_PHASE2_ROOT = _HERE.parent
for _p in (str(_REPO_ROOT), str(_PHASE2_ROOT)):
    if _p not in sys.path:
        sys.path.insert(0, _p)


# ===========================================================================
# 10-row sample Phase 1 CSVs for the crosswalk builder.
# ===========================================================================

_CHEMBL_ROWS = [
    {"chembl_id": "CHEMBL25",    "name": "Aspirin",       "inchikey": "BSYNRYMUTXBXSQ-UHFFFAOYSA-N", "smiles": "CC(=O)OC1=CC=CC=C1C(=O)O"},
    {"chembl_id": "CHEMBL112",   "name": "Acetaminophen", "inchikey": "RZVAJINKPMORJF-UHFFFAOYSA-N",  "smiles": "CC(=O)NC1=CC=C(O)C=C1"},
    {"chembl_id": "CHEMBL744",   "name": "Ibuprofen",     "inchikey": "HEFNNWSXXWATIW-UHFFFAOYSA-N",  "smiles": "CC(C)CC1=CC=C(C=C1)C(C)C(=O)O"},
    {"chembl_id": "CHEMBL546",   "name": "Warfarin",      "inchikey": "PJVWKTKQMONHTF-UHFFFAOYSA-N",  "smiles": "X"},
    {"chembl_id": "CHEMBL404",   "name": "Diazepam",      "inchikey": "AAUVKQBHMPKKQR-UHFFFAOYSA-N",  "smiles": "X"},
    {"chembl_id": "CHEMBL1383",  "name": "Omeprazole",    "inchikey": "SUBDBMMJDZJVPE-UHFFFAOYSA-N",  "smiles": "X"},
    {"chembl_id": "CHEMBL658",   "name": "Metformin",     "inchikey": "XZWYZXLIPXDOLG-UHFFFAOYSA-N",  "smiles": "X"},
    {"chembl_id": "CHEMBL819",   "name": "Atorvastatin",  "inchikey": "XUKUURHRXDUEBC-CAQYLUSBKJ-N",  "smiles": "X"},
    {"chembl_id": "CHEMBL148",   "name": "Caffeine",      "inchikey": "RYYVLZVUVIJVGH-UHFFFAOYSA-N",  "smiles": "X"},
    {"chembl_id": "CHEMBL137",   "name": "Simvastatin",   "inchikey": "RYMZZMVNOQMWHX-GQBYOXTTSA-N",  "smiles": "X"},
]

_DRUGBANK_ROWS = [
    # 5 of the same drugs (cross-source merge expected) + 1 drug only in DrugBank.
    {"drugbank_id": "DB00945", "chembl_id": "CHEMBL25",  "name": "Aspirin",       "inchikey": "BSYNRYMUTXBXSQ-UHFFFAOYSA-N"},
    {"drugbank_id": "DB00316", "chembl_id": "CHEMBL112", "name": "Acetaminophen", "inchikey": "RZVAJINKPMORJF-UHFFFAOYSA-N"},
    {"drugbank_id": "DB01050", "chembl_id": "CHEMBL744", "name": "Ibuprofen",     "inchikey": "HEFNNWSXXWATIW-UHFFFAOYSA-N"},
    {"drugbank_id": "DB00682", "chembl_id": "CHEMBL546", "name": "Warfarin",      "inchikey": "PJVWKTKQMONHTF-UHFFFAOYSA-N"},
    {"drugbank_id": "DB00619", "chembl_id": "CHEMBL404", "name": "Diazepam",      "inchikey": "AAUVKQBHMPKKQR-UHFFFAOYSA-N"},
    {"drugbank_id": "DB01175", "chembl_id": "",          "name": "Escitalopram",  "inchikey": "WSEQKYVQPPVMAW-UHFFFAOYSA-N"},
]

_PUBCHEM_ROWS = [
    # 3 of the same drugs (cross-source merge expected).
    {"pubchem_cid": "2244", "inchikey": "BSYNRYMUTXBXSQ-UHFFFAOYSA-N", "canonical_smiles": "X"},
    {"pubchem_cid": "1983", "inchikey": "RZVAJINKPMORJF-UHFFFAOYSA-N",  "canonical_smiles": "X"},
    {"pubchem_cid": "3672", "inchikey": "HEFNNWSXXWATIW-UHFFFAOYSA-N",  "canonical_smiles": "X"},
]


@pytest.fixture
def sample_processed_dir(tmp_path):
    """Write the 3 sample CSVs to a temp directory mimicking Phase 1's processed_data."""
    for name, rows in (
        ("chembl_drugs.csv", _CHEMBL_ROWS),
        ("drugbank_drugs.csv", _DRUGBANK_ROWS),
        ("pubchem_enrichment.csv", _PUBCHEM_ROWS),
    ):
        path = tmp_path / name
        with open(path, "w", newline="", encoding="utf-8") as fh:
            writer = csv.DictWriter(fh, fieldnames=rows[0].keys())
            writer.writeheader()
            writer.writerows(rows)
    return tmp_path


# ===========================================================================
# TASK 4.3 contract: InChIKey-canonical crosswalk built from 3 sources.
# ===========================================================================

class TestEntityResolverInchikeyTask43:
    """TASK 4.3: verify InChIKey-canonical crosswalk from Phase 1 sources."""

    def test_build_function_exists(self):
        """``build_inchikey_crosswalk_from_phase1`` must exist (it didn't before)."""
        from drugos_graph.entity_resolver import (
            build_inchikey_crosswalk_from_phase1,
            register_phase1_inchikey_crosswalk,
        )
        assert callable(build_inchikey_crosswalk_from_phase1)
        assert callable(register_phase1_inchikey_crosswalk)

    def test_each_drug_appears_exactly_once(self, sample_processed_dir):
        """Every drug (InChIKey) must appear EXACTLY ONCE in the crosswalk."""
        from drugos_graph.entity_resolver import build_inchikey_crosswalk_from_phase1

        result = build_inchikey_crosswalk_from_phase1(
            chembl_csv=sample_processed_dir / "chembl_drugs.csv",
            drugbank_csv=sample_processed_dir / "drugbank_drugs.csv",
            pubchem_csv=sample_processed_dir / "pubchem_enrichment.csv",
        )
        crosswalk = result["crosswalk"]
        # Each inchikey is a unique dict key by definition -- but verify.
        inchikeys = list(crosswalk.keys())
        assert len(inchikeys) == len(set(inchikeys)), "InChIKeys not unique!"
        # 10 ChEMBL drugs + 1 DrugBank-only drug (Escitalopram) = 11 unique.
        assert len(crosswalk) == 11, (
            f"Expected 11 unique InChIKeys, got {len(crosswalk)}. "
            f"Keys: {sorted(inchikeys)}"
        )

    def test_cross_source_merge_works(self, sample_processed_dir):
        """Aspirin should appear in all 3 sources under one InChIKey."""
        from drugos_graph.entity_resolver import build_inchikey_crosswalk_from_phase1

        result = build_inchikey_crosswalk_from_phase1(
            chembl_csv=sample_processed_dir / "chembl_drugs.csv",
            drugbank_csv=sample_processed_dir / "drugbank_drugs.csv",
            pubchem_csv=sample_processed_dir / "pubchem_enrichment.csv",
        )
        aspirin = result["crosswalk"]["BSYNRYMUTXBXSQ-UHFFFAOYSA-N"]
        assert aspirin["chembl_id"] == "CHEMBL25"
        assert aspirin["drugbank_id"] == "DB00945"
        assert aspirin["pubchem_cid"] == "2244"
        assert set(aspirin["sources"]) == {"chembl", "drugbank", "pubchem"}
        assert aspirin["name"] == "Aspirin"

    def test_drugbank_only_drug_appears(self, sample_processed_dir):
        """Escitalopram (only in DrugBank, no ChEMBL/PubChem) should appear."""
        from drugos_graph.entity_resolver import build_inchikey_crosswalk_from_phase1

        result = build_inchikey_crosswalk_from_phase1(
            chembl_csv=sample_processed_dir / "chembl_drugs.csv",
            drugbank_csv=sample_processed_dir / "drugbank_drugs.csv",
            pubchem_csv=sample_processed_dir / "pubchem_enrichment.csv",
        )
        escitalopram = result["crosswalk"]["WSEQKYVQPPVMAW-UHFFFAOYSA-N"]
        assert escitalopram["drugbank_id"] == "DB01175"
        assert escitalopram["chembl_id"] == ""
        assert escitalopram["pubchem_cid"] == ""
        assert escitalopram["sources"] == ["drugbank"]
        assert escitalopram["name"] == "Escitalopram"

    def test_inchikey_is_27_char_canonical(self, sample_processed_dir):
        """Every InChIKey in the crosswalk must be the 14-10-1 canonical form."""
        from drugos_graph.entity_resolver import build_inchikey_crosswalk_from_phase1
        import re
        _IK_RE = re.compile(r"^[A-Z]{14}-[A-Z]{10}-[A-Z]$")

        result = build_inchikey_crosswalk_from_phase1(
            chembl_csv=sample_processed_dir / "chembl_drugs.csv",
            drugbank_csv=sample_processed_dir / "drugbank_drugs.csv",
            pubchem_csv=sample_processed_dir / "pubchem_enrichment.csv",
        )
        for ik in result["crosswalk"]:
            assert _IK_RE.match(ik), (
                f"InChIKey {ik!r} doesn't match 14-10-1 canonical pattern"
            )

    def test_stats_are_populated(self, sample_processed_dir):
        """Stats must show 10 ChEMBL rows, 6 DrugBank rows, 3 PubChem rows, 11 unique."""
        from drugos_graph.entity_resolver import build_inchikey_crosswalk_from_phase1

        result = build_inchikey_crosswalk_from_phase1(
            chembl_csv=sample_processed_dir / "chembl_drugs.csv",
            drugbank_csv=sample_processed_dir / "drugbank_drugs.csv",
            pubchem_csv=sample_processed_dir / "pubchem_enrichment.csv",
        )
        stats = result["stats"]
        assert stats["chembl_rows_read"] == 10
        assert stats["drugbank_rows_read"] == 6
        assert stats["pubchem_rows_read"] == 3
        assert stats["unique_inchikeys"] == 11
        # 5 drugs appear in 2+ sources (Aspirin, Acetaminophen, Ibuprofen,
        # Warfarin, Diazepam -- all in ChEMBL + DrugBank; 3 of those also in PubChem).
        assert stats["drugs_in_multiple_sources"] == 5
        # All 3 files present.
        assert stats["files_present"] == {"chembl": True, "drugbank": True, "pubchem": True}

    def test_within_source_duplicates_flagged(self, sample_processed_dir, tmp_path):
        """Two ChEMBL IDs mapping to the same InChIKey must be flagged."""
        from drugos_graph.entity_resolver import build_inchikey_crosswalk_from_phase1

        # Add a duplicate ChEMBL row (same InChIKey as Aspirin, different chembl_id).
        dup_chembl = sample_processed_dir / "chembl_drugs_dup.csv"
        with open(dup_chembl, "w", newline="", encoding="utf-8") as fh:
            writer = csv.DictWriter(fh, fieldnames=_CHEMBL_ROWS[0].keys())
            writer.writeheader()
            writer.writerows(_CHEMBL_ROWS)
            # Duplicate Aspirin under a different chembl_id.
            writer.writerow({
                "chembl_id": "CHEMBL999999",
                "name": "Aspirin (alt)",
                "inchikey": "BSYNRYMUTXBXSQ-UHFFFAOYSA-N",
                "smiles": "X",
            })

        result = build_inchikey_crosswalk_from_phase1(
            chembl_csv=dup_chembl,
            drugbank_csv=sample_processed_dir / "drugbank_drugs.csv",
            pubchem_csv=sample_processed_dir / "pubchem_enrichment.csv",
        )
        dups = result["duplicate_inchikeys_within_source"]
        # Should have at least one within-source duplicate group for "chembl".
        assert "chembl" in dups, "Duplicate ChEMBL IDs for Aspirin not flagged"
        aspirin_dups = [d for d in dups["chembl"] if d[0] == "BSYNRYMUTXBXSQ-UHFFFAOYSA-N"]
        assert aspirin_dups, (
            f"Aspirin InChIKey not in chembl duplicates: {dups.get('chembl')}"
        )
        # The duplicate group should contain both CHEMBL25 and CHEMBL999999.
        ik, ids = aspirin_dups[0]
        assert "CHEMBL25" in ids, f"CHEMBL25 not in dup ids: {ids}"
        assert "CHEMBL999999" in ids, f"CHEMBL999999 not in dup ids: {ids}"

    def test_register_phase1_inchikey_crosswalk_pushes_to_idcrosswalk(self, sample_processed_dir):
        """The register function should push mappings into the global IDCrosswalk."""
        from drugos_graph.entity_resolver import (
            build_inchikey_crosswalk_from_phase1,
            register_phase1_inchikey_crosswalk,
        )
        from drugos_graph.id_crosswalk import (
            IDCrosswalk,
            reset_default_crosswalk,
        )

        # Reset the singleton to ensure clean state.
        reset_default_crosswalk()
        result = build_inchikey_crosswalk_from_phase1(
            chembl_csv=sample_processed_dir / "chembl_drugs.csv",
            drugbank_csv=sample_processed_dir / "drugbank_drugs.csv",
            pubchem_csv=sample_processed_dir / "pubchem_enrichment.csv",
        )
        n = register_phase1_inchikey_crosswalk(result)
        # 11 self-mappings + 10 chembl + 6 drugbank + 3 pubchem = 30
        # (chembl_id for Aspirin is registered twice -- once from chembl_drugs,
        # once from drugbank_drugs.chembl_id alias -- register_compound_inchikey
        # is idempotent so the second call returns 0).
        assert n >= 25, f"Expected >= 25 mappings registered, got {n}"

        # Now verify the crosswalk can translate aliases.
        from drugos_graph.id_crosswalk import get_default_crosswalk
        cw = get_default_crosswalk()
        # InChIKey -> itself.
        assert cw.compound_id_to_inchikey("BSYNRYMUTXBXSQ-UHFFFAOYSA-N") == "BSYNRYMUTXBXSQ-UHFFFAOYSA-N"
        # ChEMBL ID -> InChIKey.
        assert cw.compound_id_to_inchikey("CHEMBL25") == "BSYNRYMUTXBXSQ-UHFFFAOYSA-N"
        # DrugBank ID -> InChIKey.
        assert cw.compound_id_to_inchikey("DB00945") == "BSYNRYMUTXBXSQ-UHFFFAOYSA-N"
        # PubChem CID -> InChIKey.
        assert cw.compound_id_to_inchikey("2244") == "BSYNRYMUTXBXSQ-UHFFFAOYSA-N"

        # Cleanup.
        reset_default_crosswalk()

    def test_works_with_chembl_only_deployment(self, tmp_path):
        """Verify the function works when DrugBank is absent (ChEMBL-only)."""
        from drugos_graph.entity_resolver import build_inchikey_crosswalk_from_phase1

        # Only write chembl_drugs.csv -- no drugbank or pubchem.
        chembl_path = tmp_path / "chembl_drugs.csv"
        with open(chembl_path, "w", newline="", encoding="utf-8") as fh:
            writer = csv.DictWriter(fh, fieldnames=_CHEMBL_ROWS[0].keys())
            writer.writeheader()
            writer.writerows(_CHEMBL_ROWS)

        result = build_inchikey_crosswalk_from_phase1(
            chembl_csv=chembl_path,
            drugbank_csv=tmp_path / "nonexistent_drugbank.csv",
            pubchem_csv=tmp_path / "nonexistent_pubchem.csv",
        )
        assert result["stats"]["unique_inchikeys"] == 10
        assert result["stats"]["files_present"] == {
            "chembl": True, "drugbank": False, "pubchem": False,
        }
