"""Teammate 2 — P1 to P3 Integration feature-completeness verification.

This file is the GROUND-TRUTH verification script from the issue as written
by the user. It MUST pass for the P1-to-P3 integration to be declared
"100% connected".

The previous version of this file had been WATERED DOWN to match the
existing (broken) code:
  * Test 3 used ``SourceSpec`` instead of the bare dict schema the issue
    specifies.
  * Test 5 used ``compute_drug_features(smiles, drug_name, feature_dim)``
    instead of the ``compute_drug_features(row) -> Optional[List[float]]``
    contract the issue specifies.

That watering-down is exactly the "comments and tests are fakes" failure
mode the user explicitly called out. This file is the user's exact
contract, restored verbatim from the issue's VERIFICATION SCRIPT.

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
_REPO_ROOT = _THIS_FILE.parents[3]   # .../autonomous-drug-repurposing
for _p in (_PHASE1_DIR, _REPO_ROOT):
    if str(_p) not in sys.path:
        sys.path.insert(0, str(_p))


# =============================================================================
# Test 1: PubChem single-CID lookup returns IsomericSMILES
# =============================================================================
@pytest.mark.integration
def test_pubchem_enrichment_has_isomeric_smiles():
    """Issue contract: ``_download_pubchem_compound(2244)`` must return a dict
    containing a non-empty ``IsomericSMILES`` value.

    If PubChem is unreachable from the test environment, the test is SKIPPED
    (not failed) — the function exists and is callable; the failure is a
    network-availability issue, not a code defect.
    """
    from phase1.pipelines.pubchem_pipeline import _download_pubchem_compound

    result = _download_pubchem_compound(2244)  # aspirin
    assert "CID" in result, "result must always carry CID"
    assert result["CID"] == 2244
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
    """Issue contract: ``_parse_subcellular_location`` must extract
    "Cell membrane" from a UniProt REST record with the canonical
    ``subcellularLocations[0].location.value`` shape.
    """
    from phase1.pipelines.uniprot_pipeline import _parse_subcellular_location

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


# =============================================================================
# Test 3: feature_validator detects >5% NULL rate (BARE-DICT SCHEMA)
# =============================================================================
def test_feature_validator_detects_high_null_rate():
    """Issue contract: ``validate_feature_completeness`` must FAIL when a
    column has 50% NULL rate, using a BARE-DICT schema (NOT SourceSpec).

    The issue's verification script passes a schema shaped like::

        {"pubchem_enrichment": {
            "filename": "pubchem_enrichment.csv",
            "required_columns": ["inchikey", "cid", "xlogp", "isomeric_smiles"],
            "required": True,
        }}

    This is the user's GROUND-TRUTH contract. The validator MUST accept
    this shape (in addition to the richer ``SourceSpec`` shape used by the
    production DAG). A validator that only accepts ``SourceSpec`` would
    fail this test — and that is exactly the bug being fixed.
    """
    import pandas as pd
    from phase1.contracts.feature_validator import validate_feature_completeness

    with tempfile.TemporaryDirectory() as tmpdir:
        tmpdir = Path(tmpdir)
        df = pd.DataFrame({
            "inchikey": ["A"] * 100,
            "cid": list(range(100)),
            "xlogp": [1.5] * 100,
            "isomeric_smiles": ["C"] * 50 + [None] * 50,  # 50% NULL
        })
        df.to_csv(tmpdir / "pubchem_enrichment.csv", index=False)
        schema = {
            "pubchem_enrichment": {
                "filename": "pubchem_enrichment.csv",
                "required_columns": ["inchikey", "cid", "xlogp", "isomeric_smiles"],
                "required": True,
            }
        }
        passed, failures = validate_feature_completeness(
            tmpdir, schema, max_null_rate=0.05,
        )
        assert not passed, (
            "Validator should fail with 50% NULL rate. Got failures: "
            + "; ".join(failures)
        )
        assert any("isomeric_smiles" in f and "50.0%" in f for f in failures), (
            f"Failure list must mention 'isomeric_smiles' and '50.0%'. "
            f"Got: {failures}"
        )


# =============================================================================
# Test 4: feature_validator PASSES when all columns are <5% NULL
# =============================================================================
def test_feature_validator_passes_low_null_rate():
    """Issue acceptance criterion #4: validator PASSES on a fixture with
    <5% NULL rate. Uses the same bare-dict schema shape as test 3.
    """
    import pandas as pd
    from phase1.contracts.feature_validator import validate_feature_completeness

    with tempfile.TemporaryDirectory() as tmpdir:
        tmpdir = Path(tmpdir)
        df = pd.DataFrame({
            "inchikey": [f"KEY{i:020d}" for i in range(100)],
            "cid": list(range(100)),
            "xlogp": [1.5 * (i % 10) for i in range(100)],
            "isomeric_smiles": ["C" + "C" * i for i in range(100)],
        })
        df.to_csv(tmpdir / "pubchem_enrichment.csv", index=False)
        schema = {
            "pubchem_enrichment": {
                "filename": "pubchem_enrichment.csv",
                "required_columns": ["inchikey", "cid", "xlogp", "isomeric_smiles"],
                "required": True,
            }
        }
        passed, failures = validate_feature_completeness(
            tmpdir, schema, max_null_rate=0.05,
        )
        assert passed, (
            "Validator should pass with <5% NULL rate. Got failures: "
            + "; ".join(failures)
        )
        assert failures == [], f"Failure list should be empty. Got: {failures}"


# =============================================================================
# Test 5: Phase 3 compute_drug_features handles missing SMILES gracefully
# =============================================================================
def test_phase3_compute_drug_features_handles_missing_smiles():
    """Issue contract: ``compute_drug_features(row)`` must:

    * Return ``None`` when ``row["isomeric_smiles"]`` is empty/missing.
    * Return a 2-element list ``[xlogp, prevalence]`` otherwise.

    This is the row-based contract the issue specifies. The existing
    ChemBERTa/RDKit path (``compute_drug_features(smiles, drug_name,
    feature_dim, allow_chemberta)``) is PRESERVED via polymorphic
    dispatch — when the first argument is a STRING, the existing
    behavior is used (no regression to Teammate 6's work). When the
    first argument is a DICT (the issue's contract), the new
    row-based behavior is used.
    """
    from graph_transformer.data.biomedical_tables import compute_drug_features

    # Case 1: empty SMILES -> None
    row = {"inchikey": "ABC", "isomeric_smiles": "", "xlogp": 1.5}
    result = compute_drug_features(row)
    assert result is None, (
        f"Should return None for missing SMILES. Got: {result!r}"
    )

    # Case 2: valid SMILES -> [xlogp, prevalence] (2 floats)
    row = {"inchikey": "ABC", "isomeric_smiles": "CCO", "xlogp": 1.5}
    result = compute_drug_features(row)
    assert result is not None, (
        "Should return a feature list for valid SMILES."
    )
    assert len(result) == 2, (
        f"Should return 2 features [xlogp, prevalence]. Got len={len(result)}"
    )

    # Case 3: missing prevalence defaults to 0.0
    row = {"inchikey": "ABC", "isomeric_smiles": "CCO", "xlogp": 1.5}
    result = compute_drug_features(row)
    assert result == [1.5, 0.0], (
        f"Expected [1.5, 0.0] when prevalence missing. Got: {result!r}"
    )

    # Case 4: missing xlogp defaults to 0.0
    row = {"inchikey": "ABC", "isomeric_smiles": "CCO"}
    result = compute_drug_features(row)
    assert result == [0.0, 0.0], (
        f"Expected [0.0, 0.0] when both xlogp and prevalence missing. Got: {result!r}"
    )
