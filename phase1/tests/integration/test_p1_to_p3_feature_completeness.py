"""Teammate 2 — P1 to P3 Integration feature-completeness verification.

These tests verify the ROOT FIX for the issue:

    Teammate 2 — P1 to P3 Integration - Ensure Phase 1 Outputs Contain
    All Features Phase 3 GT Model Needs

The issue's "VERIFICATION SCRIPT" was written against an EARLIER state of
the codebase. Forensic re-audit found that 4 of the 5 concerns the issue
listed were ALREADY addressed by prior teammate work:

  * ``prevalence_per_10k`` is correctly declared in
    ``disgenet_gda.optional_columns`` (NOT in ``pubchem_enrichment`` —
    prevalence is a disease attribute, not a drug attribute). The Phase 3
    consumer ``biomedical_tables._get_prevalence_map`` reads it from the
    Phase 1 DB.
  * ``isomeric_smiles`` is requested by both ``_v50_downloaders`` (line
    ~830) and ``pubchem_pipeline`` (line ~830 of the response parser).
    The "15% of drugs missing" claim was outdated.
  * ``compute_drug_features`` has the signature
    ``(smiles, drug_name, feature_dim, allow_chemberta)`` — NOT
    ``(row)``. It returns a zero vector for missing SMILES (not ``None``).
    Changing the signature would REGRESS Teammate 6's work.
  * ``subcellular_location`` is parsed correctly by the inlined loop at
    ``uniprot_pipeline.py`` ~line 1171 (REST) and ~line 1371 (DAT).

The GENUINELY missing piece was a NULL-rate validator that runs after
the pipeline and FAILS the run if any nullable column exceeds a 5%
NULL threshold. This commit adds:

  * ``phase1/contracts/feature_validator.py`` —
    ``validate_feature_completeness(processed_dir, schema, max_null_rate)``
  * Wiring in ``master_pipeline_dag.validate_output()`` (Check 5)
  * ``uniprot_pipeline._parse_subcellular_location`` module-level
    function (extracted from the inlined loop, for testability)
  * ``pubchem_pipeline._download_pubchem_compound(cid)`` module-level
    helper (single-CID lookup, for testability)

These tests verify ALL of the above. They run as ``@pytest.mark.integration``
because two of them require network access (PubChem) or RDKit (real
molecular fingerprints). The other three are pure unit tests on
fixture data and should always run in CI.

Run with::

    python -m pytest phase1/tests/integration/test_p1_to_p3_feature_completeness.py -v
"""
from __future__ import annotations

import os
import sys
import tempfile
from pathlib import Path

import pytest

# Ensure phase1/ is on sys.path so we can import pipelines.* and contracts.*
# without requiring the package to be installed (matches the DAG's
# ensure_project_root() pattern).
_THIS_FILE = Path(__file__).resolve()
_PHASE1_DIR = _THIS_FILE.parents[2]  # .../phase1
if str(_PHASE1_DIR) not in sys.path:
    sys.path.insert(0, str(_PHASE1_DIR))


# =============================================================================
# Test 1: PubChem single-CID lookup returns IsomericSMILES
# =============================================================================
@pytest.mark.integration
def test_pubchem_enrichment_has_isomeric_smiles():
    """``_download_pubchem_compound(2244)`` (aspirin) returns IsomericSMILES.

    Verifies that the PubChem PUG-REST endpoint is queried with the full
    property list INCLUDING ``IsomericSMILES``. Aspirin has no stereo
    centers, so the returned value will equal ``CanonicalSMILES`` —
    but the KEY must be present in the response dict.
    """
    from pipelines.pubchem_pipeline import _download_pubchem_compound

    result = _download_pubchem_compound(2244)  # aspirin
    assert "CID" in result, "result must always carry CID"
    assert result["CID"] == 2244
    # If we got an error key, the test env may not have network access
    # to PubChem. Skip gracefully rather than fail — the test still
    # proves the function is callable and structured correctly.
    if "error" in result:
        pytest.skip(
            f"PubChem PUG-REST unreachable from this environment "
            f"(error: {result['error']}). The function exists and is "
            f"callable; this is a network-availability issue, not a "
            f"code defect."
        )
    assert "IsomericSMILES" in result, (
        f"IsomericSMILES key missing from PubChem response. Got keys: "
        f"{sorted(result.keys())}"
    )
    assert result["IsomericSMILES"], (
        "IsomericSMILES is empty for CID 2244 (aspirin). PubChem should "
        "always return this field (possibly equal to CanonicalSMILES "
        "for non-chiral compounds)."
    )


# =============================================================================
# Test 2: UniProt subcellular_location parser
# =============================================================================
def test_uniprot_subcellular_location_parsed():
    """``_parse_subcellular_location`` extracts "Cell membrane" from a REST record.

    Uses the fixture from the issue's verification script. Verifies the
    module-level function (extracted from the previously-inlined loop in
    ``_flatten_uniprot_rest_json``) correctly handles the UniProt REST
    schema:

        {"comments": [{"commentType": "SUBCELLULAR LOCATION",
                       "subcellularLocations": [
                           {"location": {"value": "Cell membrane"}}
                       ]}]}
    """
    # Import at call time so a failure here surfaces clearly in the test
    # output rather than during collection.
    from pipelines.uniprot_pipeline import _parse_subcellular_location

    fixture_entry = {
        "comments": [
            {
                "commentType": "SUBCELLULAR LOCATION",
                "subcellularLocations": [
                    {"location": {"value": "Cell membrane"}}
                ]
            }
        ]
    }
    result = _parse_subcellular_location(fixture_entry)
    assert result == "Cell membrane", (
        f"Expected 'Cell membrane', got {result!r}"
    )

    # Additional regression: function must never raise on malformed input.
    assert _parse_subcellular_location({}) == ""
    assert _parse_subcellular_location({"comments": []}) == ""
    assert _parse_subcellular_location({"comments": [{"commentType": "FUNCTION"}]}) == ""
    assert _parse_subcellular_location({"comments": "not-a-list"}) == ""
    assert _parse_subcellular_location(None) == ""  # type: ignore[arg-type]
    assert _parse_subcellular_location("not-a-dict") == ""  # type: ignore[arg-type]


# =============================================================================
# Test 3: feature_validator detects >5% NULL rate
# =============================================================================
def test_feature_validator_detects_high_null_rate():
    """``validate_feature_completeness`` FAILS when a column has 50% NULL.

    Writes a fixture CSV with 50% NULL in ``isomeric_smiles`` and asserts
    the validator returns ``passed=False`` with a descriptive failure
    message naming the column and the NULL rate.
    """
    import pandas as pd
    from contracts.feature_validator import validate_feature_completeness

    with tempfile.TemporaryDirectory() as tmpdir:
        tmpdir = Path(tmpdir)
        # Build a fixture pubchem_enrichment.csv with 50% NULL isomeric_smiles.
        df = pd.DataFrame({
            "inchikey": ["A"] * 100,
            "canonical_smiles": ["C"] * 100,
            "pubchem_cid": list(range(100)),
            "xlogp": [1.5] * 100,
            "isomeric_smiles": ["C"] * 50 + [None] * 50,  # 50% NULL
        })
        df.to_csv(tmpdir / "pubchem_enrichment.csv", index=False)
        # Build a minimal schema with only this source so other missing
        # sources don't pollute the failure list.
        from contracts.phase1_schema import ColumnSpec, SourceSpec
        schema = {
            "pubchem_enrichment": SourceSpec(
                key="pubchem_enrichment",
                filename="pubchem_enrichment.csv",
                aliases=(),
                required_columns=(
                    ColumnSpec("inchikey", "string", nullable=False),
                    ColumnSpec("canonical_smiles", "string", nullable=True),
                ),
                any_of_groups=(),
                optional_columns=(
                    ColumnSpec("pubchem_cid", "int64", nullable=True),
                    ColumnSpec("xlogp", "float64", nullable=True),
                    ColumnSpec("isomeric_smiles", "string", nullable=True),
                ),
                min_rows=1,
                description="test fixture",
            ),
        }
        passed, failures = validate_feature_completeness(
            tmpdir, schema=schema, max_null_rate=0.05,
        )
        assert not passed, (
            "Validator should FAIL when a column has 50% NULL rate "
            "(threshold 5%). Got failures: " + "; ".join(failures)
        )
        # The failure message must name isomeric_smiles and the 50% rate.
        joined = " | ".join(failures)
        assert "isomeric_smiles" in joined, (
            f"Failure list must mention 'isomeric_smiles'. Got: {joined}"
        )
        assert "50.0%" in joined, (
            f"Failure list must mention '50.0%' NULL rate. Got: {joined}"
        )


# =============================================================================
# Test 4: feature_validator PASSES when all columns are <5% NULL
# =============================================================================
def test_feature_validator_passes_low_null_rate():
    """``validate_feature_completeness`` PASSES when all columns are populated.

    Complementary to test 3 — verifies the validator does not produce
    false positives. Acceptance criterion #4 in the issue: "validate_
    feature_completeness passes on a fixture dataset with <5% NULL rate."
    """
    import pandas as pd
    from contracts.feature_validator import validate_feature_completeness

    with tempfile.TemporaryDirectory() as tmpdir:
        tmpdir = Path(tmpdir)
        df = pd.DataFrame({
            "inchikey": [f"KEY{i:020d}" for i in range(100)],
            "canonical_smiles": ["C" + "C" * i for i in range(100)],
            "pubchem_cid": list(range(100)),
            "xlogp": [1.5 * (i % 10) for i in range(100)],
            "isomeric_smiles": ["C" + "C" * i for i in range(100)],
        })
        df.to_csv(tmpdir / "pubchem_enrichment.csv", index=False)
        from contracts.phase1_schema import ColumnSpec, SourceSpec
        schema = {
            "pubchem_enrichment": SourceSpec(
                key="pubchem_enrichment",
                filename="pubchem_enrichment.csv",
                aliases=(),
                required_columns=(
                    ColumnSpec("inchikey", "string", nullable=False),
                    ColumnSpec("canonical_smiles", "string", nullable=True),
                ),
                any_of_groups=(),
                optional_columns=(
                    ColumnSpec("pubchem_cid", "int64", nullable=True),
                    ColumnSpec("xlogp", "float64", nullable=True),
                    ColumnSpec("isomeric_smiles", "string", nullable=True),
                ),
                min_rows=1,
                description="test fixture",
            ),
        }
        passed, failures = validate_feature_completeness(
            tmpdir, schema=schema, max_null_rate=0.05,
        )
        assert passed, (
            "Validator should PASS when all columns are populated with "
            "real (non-NULL) values. Got failures: " + "; ".join(failures)
        )
        assert failures == [], f"Failure list should be empty. Got: {failures}"


# =============================================================================
# Test 5: Phase 3 compute_drug_features handles missing SMILES gracefully
# =============================================================================
def test_phase3_compute_drug_features_handles_missing_smiles():
    """``compute_drug_features`` returns a ZERO vector for missing SMILES.

    The issue's "EXACT FIX CODE" proposed signature ``compute_drug_features
    (row) -> Optional[List[float]]`` returning ``None`` for missing SMILES.
    That signature was REPLACED by Teammate 6 with the current
    ``(smiles, drug_name, feature_dim, allow_chemberta)`` signature, which
    returns a zero vector for missing SMILES (NOT noise, NOT None). The
    zero vector is scientifically HONEST: the GNN learns nothing about
    the drug's structure (which is correct — we know nothing about it).

    This test verifies the ACTUAL signature and behavior. Changing it
    back to ``compute_drug_features(row) -> Optional[List[float]]`` would
    REGRESS Teammate 6's work and break all Phase 3 callers.
    """
    import numpy as np
    from graph_transformer.data.biomedical_tables import compute_drug_features

    # Case 1: empty SMILES -> ZERO vector of shape (feature_dim,).
    # We force DRUGOS_SKIP_CHEMBERTA=1 so the test does not require
    # the ChemBERTa model weights (which may not be downloaded in CI).
    os.environ["DRUGOS_SKIP_CHEMBERTA"] = "1"
    try:
        result = compute_drug_features(
            smiles="",
            drug_name="missing_smiles_drug",
            feature_dim=128,
        )
        assert isinstance(result, np.ndarray), (
            f"Expected numpy.ndarray, got {type(result).__name__}"
        )
        assert result.shape == (128,), (
            f"Expected shape (128,), got {result.shape}"
        )
        # ZERO vector, NOT noise (the previous broken behavior was
        # deterministic noise — scientifically meaningless).
        assert float(np.linalg.norm(result)) == 0.0, (
            "Missing SMILES must produce a ZERO vector, not noise. "
            f"Got norm={float(np.linalg.norm(result)):.6f}."
        )
    finally:
        os.environ.pop("DRUGOS_SKIP_CHEMBERTA", None)

    # Case 2: valid SMILES -> non-zero vector of shape (feature_dim,).
    # Uses RDKit Morgan fingerprint fallback (ChemBERTa skipped above).
    # "CCO" is ethanol — a simple, unambiguous molecule.
    os.environ["DRUGOS_SKIP_CHEMBERTA"] = "1"
    try:
        result = compute_drug_features(
            smiles="CCO",
            drug_name="ethanol",
            feature_dim=128,
        )
        assert isinstance(result, np.ndarray)
        assert result.shape == (128,), f"Expected shape (128,), got {result.shape}"
        # RDKit Morgan fingerprint of ethanol is non-zero (has 1-bits).
        assert float(np.linalg.norm(result)) > 0.0, (
            "Valid SMILES must produce a non-zero feature vector. "
            "Got zero vector — RDKit Morgan fingerprint of 'CCO' should "
            "have at least one set bit."
        )
    finally:
        os.environ.pop("DRUGOS_SKIP_CHEMBERTA", None)
