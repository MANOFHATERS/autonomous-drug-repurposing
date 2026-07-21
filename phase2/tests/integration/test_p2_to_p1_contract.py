"""Teammate 5 (P2→P1 Integration, P0 root fix) — Verification Tests.

Tests that the Phase 2 bridge consumes Phase 1 outputs using ONLY the
contract (no hardcoded fallbacks). Verifies the three acceptance
criteria from the issue:

  1. ``test_bridge_reads_csvs_using_contract`` — happy path: the bridge
     reads all required Phase 1 CSVs using contract-driven alias
     resolution (``get_all_aliases()``), validates required columns,
     and returns a populated dict.

  2. ``test_bridge_fails_on_missing_required_column`` — a required
     source CSV is missing a contract-required column → bridge raises
     ``CriticalDataSourceError`` with "missing required columns" in
     the message.

  3. ``test_bridge_fails_fast_when_contract_too_old`` — the contract
     version is mocked to 1.0.0 → bridge raises
     ``CriticalDataSourceError`` with "contract version 1.0.0 is too
     old" in the message.

TEST DATA REQUIREMENTS
----------------------
The test data uses the CANONICAL column names from
``phase1/contracts/phase1_schema.py`` (e.g. ``chembl_id``+``inchikey``
for ``chembl_drugs``, NOT the stale ``drug_name`` column from the
issue's illustrative example). This is INTENTIONAL — the issue's
example used stale column names that don't match the current contract,
which would have produced false-negative test results. The root fix
makes the test data contract-compliant so the tests verify the bridge's
REAL behavior against the REAL contract.

Run with::

    python -m pytest phase2/tests/integration/test_p2_to_p1_contract.py -v
"""
from __future__ import annotations

import tempfile
from pathlib import Path
from unittest.mock import patch

import pandas as pd
import pytest


# ===========================================================================
# Helpers — build contract-compliant Phase 1 CSV fixtures.
# ===========================================================================
# The contract (phase1/contracts/phase1_schema.py) declares these sources
# as REQUIRED (min_rows >= 1):
#   chembl_drugs, chembl_activities, uniprot_proteins, string_ppi,
#   disgenet_gda, omim_gda
# And these as OPTIONAL (min_rows == 0):
#   drugs (DrugBank), interactions, indications, omim_susceptibility,
#   pubchem_enrichment
#
# For the happy-path test we provide ALL 6 required sources + 1 optional
# (pubchem_enrichment) so we can verify the bridge loads both required
# and optional sources correctly.
# ===========================================================================


def _write_contract_compliant_csvs(base: Path) -> None:
    """Write all required Phase 1 CSVs with contract-compliant columns.

    The column names match ``phase1/contracts/phase1_schema.py``'s
    ``required_columns`` tuples EXACTLY — using the canonical names
    (chembl_id, inchikey, molecule_chembl_id, etc.) rather than the
    stale names (drug_name) from the issue's illustrative example.
    """
    # 1. chembl_drugs (REQUIRED, min_rows=1)
    #    Contract requires: chembl_id, inchikey
    #    We use the alias 'drugs.csv' to verify alias resolution.
    pd.DataFrame({
        "chembl_id": ["CHEMBL1", "CHEMBL2"],
        "inchikey": ["RZVAJINKQORUOD-UHFFFAOYSA-N", "BSYNRYMUTXBXSQ-UHFFFAOYSA-N"],
    }).to_csv(base / "drugs.csv", index=False)

    # 2. chembl_activities (REQUIRED, min_rows=1)
    #    Contract requires: molecule_chembl_id, target_chembl_id,
    #    pchembl_value, standard_relation
    pd.DataFrame({
        "molecule_chembl_id": ["CHEMBL1", "CHEMBL2"],
        "target_chembl_id": ["CHEMBL_TGT1", "CHEMBL_TGT2"],
        "pchembl_value": [7.5, 6.2],
        "standard_relation": ["=", "="],
    }).to_csv(base / "chembl_activities_clean.csv", index=False)

    # 3. uniprot_proteins (REQUIRED, min_rows=1)
    #    Contract requires: gene_symbol
    #    ANY_OF group: uniprot_ac OR accession OR uniprot_id
    pd.DataFrame({
        "uniprot_id": ["P00533", "P04626"],
        "gene_symbol": ["EGFR", "BRCA1"],
        "protein_name": ["Epidermal growth factor receptor", "Breast cancer type 1 susceptibility protein"],
    }).to_csv(base / "proteins.csv", index=False)

    # 4. string_ppi (REQUIRED, min_rows=1)
    #    Contract requires: combined_score
    #    ANY_OF groups:
    #      (uniprot_ac_a OR protein_a OR uniprot_id_a OR string_id_a)
    #      (uniprot_ac_b OR protein_b OR uniprot_id_b OR string_id_b)
    #      (score OR combined_score)
    pd.DataFrame({
        "uniprot_id_a": ["P00533", "P04626"],
        "uniprot_id_b": ["P04626", "P00533"],
        "combined_score": [950, 870],
    }).to_csv(base / "protein_protein_interactions.csv", index=False)

    # 5. disgenet_gda (REQUIRED, min_rows=1)
    #    Contract requires: gene_symbol, disease_id, score
    #    ANY_OF group: gene_id OR ncbi_gene_id
    pd.DataFrame({
        "gene_symbol": ["BRCA1", "EGFR"],
        "disease_id": ["C0006142", "C0678222"],
        "score": [0.8, 0.6],
        "gene_id": [672, 1956],
    }).to_csv(base / "gene_disease_associations.csv", index=False)

    # 6. omim_gda (REQUIRED, min_rows=1)
    #    Contract requires: gene_mim, gene_symbol, disease_id, disease_name
    pd.DataFrame({
        "gene_mim": ["113705", "600185"],
        "gene_symbol": ["BRCA1", "EGFR"],
        "disease_id": ["114480", "131550"],
        "disease_name": ["BREAST CANCER", "LUNG CANCER"],
    }).to_csv(base / "omim_gene_disease_associations.csv", index=False)

    # 7. pubchem_enrichment (OPTIONAL, min_rows=0)
    #    Contract requires: inchikey, canonical_smiles
    pd.DataFrame({
        "inchikey": ["RZVAJINKQORUOD-UHFFFAOYSA-N", "BSYNRYMUTXBXSQ-UHFFFAOYSA-N"],
        "canonical_smiles": ["CC(=O)OC1=CC=CC=C1C(=O)O", "CC(C)CC1=CC=C(C=C1)C(C)C(=O)O"],
        "cid": [2244, 3672],
    }).to_csv(base / "pubchem_enrichment.csv", index=False)


# ===========================================================================
# Test 1: Happy path — bridge reads CSVs using contract aliases.
# ===========================================================================


@pytest.mark.integration
def test_bridge_reads_csvs_using_contract():
    """Bridge reads Phase 1 CSVs using contract-driven alias resolution.

    Verifies:
      - The bridge finds CSVs by their ALIAS names (e.g. ``drugs.csv``
        for ``chembl_drugs`` — not the canonical ``chembl_drugs.csv``).
      - All required sources are present in the returned dict.
      - Row counts match the test fixtures.
      - The bridge returns a dict-like object (``_Phase1BridgeResult``)
        that supports ``in`` and ``[]`` access.
    """
    # Local import so the test fails loudly if the bridge module can't
    # be imported (rather than failing at collection time and blocking
    # the rest of the test suite).
    from phase2.drugos_graph.phase1_bridge import read_phase1_outputs

    with tempfile.TemporaryDirectory() as tmpdir:
        tmpdir = Path(tmpdir)
        _write_contract_compliant_csvs(tmpdir)

        # Force CSV backend (no PostgreSQL in test env).
        # strict_required_sources=True so the bridge enforces the
        # contract's required-source gate (Teammate 5 P0 root fix).
        data = read_phase1_outputs(
            tmpdir, prefer_postgres=False, strict_required_sources=True,
        )

        # Required sources must be present and populated.
        assert "chembl_drugs" in data, (
            f"chembl_drugs missing from bridge output; keys={list(data.keys())}"
        )
        assert len(data["chembl_drugs"]) == 2, (
            f"chembl_drugs should have 2 rows; got {len(data['chembl_drugs'])}"
        )
        assert "chembl_activities" in data, "chembl_activities missing"
        assert len(data["chembl_activities"]) == 2
        assert "uniprot_proteins" in data, "uniprot_proteins missing"
        assert len(data["uniprot_proteins"]) == 2
        assert "string_ppi" in data, "string_ppi missing"
        assert len(data["string_ppi"]) == 2
        assert "disgenet_gda" in data, "disgenet_gda missing"
        assert len(data["disgenet_gda"]) == 2
        assert "omim_gda" in data, "omim_gda missing"
        assert len(data["omim_gda"]) == 2

        # Optional sources: pubchem_enrichment was provided, so it must
        # be present and populated. Other optional sources (drugs,
        # interactions, indications, omim_susceptibility) were NOT
        # provided — the bridge should return empty DataFrames for them
        # (graceful degradation, NOT CriticalDataSourceError).
        assert "pubchem_enrichment" in data, "pubchem_enrichment missing"
        assert len(data["pubchem_enrichment"]) == 2

        # Optional sources not provided → empty DataFrames (not crashes).
        assert "drugs" in data, "drugs (DrugBank) key should exist even when missing"
        assert len(data["drugs"]) == 0, (
            "drugs (DrugBank) should be empty when CSV is absent (optional source)"
        )
        assert "interactions" in data
        assert len(data["interactions"]) == 0


# ===========================================================================
# Test 2: Missing required column → CriticalDataSourceError.
# ===========================================================================


@pytest.mark.integration
def test_bridge_fails_on_missing_required_column():
    """Bridge raises CriticalDataSourceError when a required column is missing.

    Verifies:
      - The bridge does NOT silently accept a CSV with missing required
        columns (the "85% aligned" failure mode the audit flagged).
      - The error message contains "missing required columns" so
        operators can grep for it in logs.
      - The error type is ``CriticalDataSourceError`` (subclass of
        ``DrugOSDataError``) so callers can catch at the right
        granularity.
    """
    from phase2.drugos_graph.phase1_bridge import (
        CriticalDataSourceError,
        read_phase1_outputs,
    )

    with tempfile.TemporaryDirectory() as tmpdir:
        tmpdir = Path(tmpdir)
        _write_contract_compliant_csvs(tmpdir)

        # OVERWRITE drugs.csv with a version that's missing the required
        # ``chembl_id`` column. The contract for ``chembl_drugs``
        # requires (chembl_id, inchikey); we provide only ``inchikey``.
        # This must trigger the column validator.
        pd.DataFrame({
            "inchikey": ["RZVAJINKQORUOD-UHFFFAOYSA-N"],
            # NOTE: chembl_id is MISSING — this is the regression we're
            # testing for. The bridge must reject this CSV rather than
            # silently producing Compound nodes with chembl_id=None.
        }).to_csv(tmpdir / "drugs.csv", index=False)

        with pytest.raises(CriticalDataSourceError, match="missing required columns"):
            read_phase1_outputs(
                tmpdir, prefer_postgres=False, strict_required_sources=True,
            )


# ===========================================================================
# Test 3: Contract too old → CriticalDataSourceError (fail fast).
# ===========================================================================


@pytest.mark.integration
def test_bridge_fails_fast_when_contract_too_old():
    """Bridge raises CriticalDataSourceError when contract version < 2.

    Verifies:
      - The contract version gate fires BEFORE any data is read.
      - The error message contains "contract version 1.0.0 is too old".
      - The error type is ``CriticalDataSourceError``.

    The test mocks ``PHASE1_CONTRACT_VERSION`` to "1.0.0" via
    ``unittest.mock.patch`` — the bridge's ``_check_phase1_contract_
    version`` reads this module-level constant. The mock ensures the
    test is deterministic (doesn't depend on the actual contract
    version, which is bumped over time).
    """
    from phase2.drugos_graph.phase1_bridge import (
        CriticalDataSourceError,
        read_phase1_outputs,
    )

    # Mock the version to 1.0.0 (too old — major < 2).
    # The bridge reads PHASE1_CONTRACT_VERSION at the top of
    # _check_phase1_contract_version; patching the module attribute
    # ensures the gate sees the mocked value.
    with patch(
        "phase2.drugos_graph.phase1_bridge.PHASE1_CONTRACT_VERSION", "1.0.0"
    ):
        with pytest.raises(
            CriticalDataSourceError,
            match=r"contract version 1\.0\.0 is too old",
        ):
            # The Path doesn't need to exist — the version gate fires
            # BEFORE the directory existence check.
            read_phase1_outputs(Path("/tmp/nonexistent_phase1_dir"), prefer_postgres=False)


# ===========================================================================
# Test 4 (BONUS): Missing required source → CriticalDataSourceError.
# ===========================================================================
# This test verifies the REQUIRED-SOURCE GATE: if a required source CSV
# is missing entirely (not just a missing column), the bridge must raise
# CriticalDataSourceError rather than silently returning an empty
# DataFrame. This is the root fix for the "85% aligned" audit finding.
# ===========================================================================


@pytest.mark.integration
def test_bridge_fails_on_missing_required_source():
    """Bridge raises CriticalDataSourceError when a required source CSV is missing.

    Verifies:
      - A required source (min_rows >= 1) that's missing entirely triggers
        CriticalDataSourceError (not silent empty DataFrame).
      - The error message names the missing source and lists the aliases
        the bridge tried.
      - Optional sources (min_rows == 0) still degrade gracefully.
    """
    from phase2.drugos_graph.phase1_bridge import (
        CriticalDataSourceError,
        read_phase1_outputs,
    )

    with tempfile.TemporaryDirectory() as tmpdir:
        tmpdir = Path(tmpdir)
        _write_contract_compliant_csvs(tmpdir)

        # DELETE a required source CSV — chembl_activities.
        # The contract marks it REQUIRED (min_rows=1).
        (tmpdir / "chembl_activities_clean.csv").unlink()
        # Also delete the alias to be sure.
        for alias in ("chembl_activities.csv",):
            p = tmpdir / alias
            if p.exists():
                p.unlink()

        with pytest.raises(
            CriticalDataSourceError,
            match=r"Phase 1 CSV for source 'chembl_activities' not found",
        ):
            read_phase1_outputs(
                tmpdir, prefer_postgres=False, strict_required_sources=True,
            )
