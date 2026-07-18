#!/usr/bin/env python3
"""v121 REAL CODE integration test — exercise actual production code paths.

This is NOT a smoke test. It exercises the REAL production code:
1. rl/service.py /rank endpoint (returns RankedCandidate shape)
2. rl/service.py /validate endpoint (returns ValidateResponse shape)
3. phase4/writeback.py write_validated_hypothesis (writes the CSV + trigger JSON)
4. shared.contracts.writeback (canonical schema)
5. frontend/contracts/api_contracts.ts (static TS contract)

Verifies the v121 ROOT FIX: all three layers (static TS, runtime Zod, Python)
agree on the same shape.
"""
from __future__ import annotations

import csv
import json
import os
import re
import sys
import tempfile
from pathlib import Path

# Make repo importable.
_REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_REPO_ROOT))

# Set dev env so _dev_samples can be imported.
os.environ.setdefault("DRUGOS_ENVIRONMENT", "development")


def test_real_rank_endpoint_returns_canonical_shape():
    """REAL CODE: hit the /rank endpoint with a real CSV and verify
    the response shape matches the static TS contract."""
    from rl.service import app
    from fastapi.testclient import TestClient

    # Create a real top_candidates_*.csv in a temp dir.
    with tempfile.TemporaryDirectory() as tmpdir:
        csv_path = Path(tmpdir) / "top_candidates_20260718.csv"
        with open(csv_path, "w", encoding="utf-8") as f:
            w = csv.writer(f)
            w.writerow([
                "drug", "disease", "rank", "gnn_score", "safety_score",
                "market_score", "reward", "policy_prob", "literature_support",
                "is_known_positive", "confidence", "pathway_score",
                "unmet_need_score", "efficacy_score", "adme_score",
            ])
            w.writerow([
                "aspirin", "pain", 1, 0.92, 0.85, 0.70, 0.88, 0.75,
                1, "true", 0.65, 0.80, 0.60, 0.55, 0.90,
            ])
            w.writerow([
                "metformin", "type 2 diabetes", 2, 0.89, 0.92, 0.65, 0.85,
                0.72, 1, "false", 0.62, 0.75, 0.85, 0.70, 0.85,
            ])

        # Point the RL service at the temp CSV.
        os.environ["RL_OUTPUT_DIR"] = tmpdir
        try:
            client = TestClient(app, raise_server_exceptions=False)
            r = client.get("/rank?limit=10")
            assert r.status_code == 200, f"/rank failed: {r.status_code} {r.text}"
            data = r.json()
            print(f"  /rank response keys: {list(data.keys())}")
            # Verify the canonical RankResponse shape (matches static TS contract).
            assert "candidates" in data, "missing 'candidates'"
            assert "total" in data, "missing 'total'"
            assert "source" in data, "missing 'source'"
            assert "generatedAt" in data, "missing 'generatedAt' (canonical camelCase)"
            assert "page" in data, "missing 'page'"
            assert "pageSize" in data, "missing 'pageSize'"
            assert "count" in data, "missing 'count'"
            # The v120 fictional fields must NOT be present.
            assert "ranked_at" not in data, "must NOT have 'ranked_at' (v120 fictional)"
            assert "next_cursor" not in data, "must NOT have 'next_cursor' (v120 fictional)"

            # Verify the RankedCandidate shape.
            c = data["candidates"][0]
            print(f"  First candidate keys: {list(c.keys())}")
            assert c["drug"] == "aspirin"
            assert c["disease"] == "pain"
            assert c["rank"] == 1
            # Canonical camelCase flat fields (matches static TS contract).
            assert "gnnScore" in c, "missing 'gnnScore'"
            assert "safetyScore" in c, "missing 'safetyScore'"
            assert "marketScore" in c, "missing 'marketScore'"
            assert "literatureSupport" in c, "missing 'literatureSupport'"
            assert "isKnownPositive" in c, "missing 'isKnownPositive'"
            # The v120 fictional nested reward_breakdown must NOT be present.
            assert "reward_breakdown" not in c, (
                "must NOT have 'reward_breakdown' (v120 fictional shape)"
            )
            # The v120 fictional id fields must NOT be present.
            assert "drug_id" not in c, "must NOT have 'drug_id'"
            assert "disease_id" not in c, "must NOT have 'disease_id'"
            assert "drug_name" not in c, "must NOT have 'drug_name'"
            assert "disease_name" not in c, "must NOT have 'disease_name'"

            print("  ✅ /rank endpoint returns canonical shape (matches static TS contract)")
        finally:
            del os.environ["RL_OUTPUT_DIR"]


def test_real_validate_endpoint_rejects_invalid_outcome():
    """REAL CODE: hit /validate with an invalid outcome and verify 400."""
    from rl.service import app
    from fastapi.testclient import TestClient

    client = TestClient(app, raise_server_exceptions=False)
    r = client.post("/validate", json={
        "drug": "aspirin",
        "disease": "pain",
        "outcome": "INVALID_OUTCOME",
        "validated_by": "wet_lab:test",
    })
    assert r.status_code == 400, f"expected 400, got {r.status_code}"
    detail = r.json().get("detail", "")
    assert "outcome must be one of" in detail or "validated_positive" in detail
    print("  ✅ /validate rejects invalid outcome with 400")


def test_real_validate_endpoint_accepts_all_4_canonical_outcomes():
    """REAL CODE: hit /validate with each of the 4 canonical outcomes and
    verify none are rejected as invalid (proving SH-002 + SH-004 are fixed)."""
    from rl.service import app
    from fastapi.testclient import TestClient

    client = TestClient(app, raise_server_exceptions=False)
    for outcome in ("validated_positive", "validated_toxic",
                    "validated_negative", "invalidated"):
        # Use a unique drug/disease pair per outcome so we don't write duplicates.
        drug = f"test_drug_{outcome[:4]}"
        disease = f"test_disease_{outcome[:4]}"
        r = client.post("/validate", json={
            "drug": drug,
            "disease": disease,
            "outcome": outcome,
            "validated_by": "wet_lab:test",
            "notes": f"v121 real code test for outcome={outcome}",
        })
        # The endpoint may return 200 (success) or 500 (if Neo4j/CSV write fails
        # in this env) — but it MUST NOT return 400 (invalid outcome).
        assert r.status_code != 400, (
            f"outcome={outcome} was rejected as invalid (400). "
            f"SH-002/SH-004 regression. Response: {r.text}"
        )
        print(f"  ✅ outcome={outcome} accepted (status={r.status_code})")


def test_real_shared_contract_imports():
    """REAL CODE: import the shared contract and verify the canonical values."""
    from shared.contracts.writeback import (
        VALID_OUTCOMES,
        WRITEBACK_VERSION,
        WRITEBACK_CSV_COLUMNS,
        DRUG_COL,
        DISEASE_COL,
        OUTCOME_COL,
    )
    # SH-002: 4 outcomes (not 3).
    assert len(VALID_OUTCOMES) == 4, (
        f"SH-002 FAIL: expected 4 outcomes, got {len(VALID_OUTCOMES)}"
    )
    assert "validated_positive" in VALID_OUTCOMES
    assert "validated_toxic" in VALID_OUTCOMES
    assert "validated_negative" in VALID_OUTCOMES
    assert "invalidated" in VALID_OUTCOMES
    # SH-012: WRITEBACK_VERSION is the shared value (not "1.0.0-rt010").
    assert WRITEBACK_VERSION == "2.0.0-shared-contract", (
        f"SH-012 FAIL: WRITEBACK_VERSION is {WRITEBACK_VERSION!r}, "
        f"expected '2.0.0-shared-contract'"
    )
    # SH-003: columns match (drug, disease, outcome, etc.).
    assert DRUG_COL == "drug"
    assert DISEASE_COL == "disease"
    assert OUTCOME_COL == "outcome"
    assert "drug_id" not in WRITEBACK_CSV_COLUMNS
    assert "disease_id" not in WRITEBACK_CSV_COLUMNS
    print(f"  ✅ shared.contracts.writeback: 4 outcomes, version={WRITEBACK_VERSION}")


def test_real_phase4_writeback_writes_canonical_csv():
    """REAL CODE: call write_validated_hypothesis and verify the CSV row
    matches the canonical schema."""
    from phase4.writeback import write_validated_hypothesis
    from shared.contracts.writeback import (
        CANONICAL_VALIDATED_CSV,
        WRITEBACK_CSV_COLUMNS,
        WRITEBACK_VERSION,
    )

    # Use a temp dir for the CSV so we don't pollute the repo.
    with tempfile.TemporaryDirectory() as tmpdir:
        csv_path = Path(tmpdir) / "validated_hypotheses.csv"
        os.environ["VALIDATED_HYPOTHESES_CSV"] = str(csv_path)
        os.environ["PHASE1_VALIDATED_CSV"] = str(csv_path)
        try:
            result = write_validated_hypothesis(
                drug="aspirin",
                disease="pain",
                outcome="validated_positive",
                validated_by="wet_lab:v121_test",
                validation_study_id="NCT-V121-TEST",
                notes="v121 real code integration test",
            )
            # Verify the result shape (matches static TS contract).
            assert "phase1_csv_path" in result
            assert "phase2_neo4j_written" in result
            assert "phase3_trigger_path" in result
            assert "validated_hypothesis" in result
            assert "writeback_version" in result
            assert result["writeback_version"] == WRITEBACK_VERSION

            # Verify the CSV was written with the canonical schema.
            assert csv_path.exists(), f"CSV not written at {csv_path}"
            with open(csv_path) as f:
                reader = csv.DictReader(f)
                rows = list(reader)
            assert len(rows) == 1
            row = rows[0]
            # SH-003: columns match the canonical WRITEBACK_CSV_COLUMNS.
            for col in WRITEBACK_CSV_COLUMNS:
                assert col in row, f"CSV missing canonical column: {col}"
            assert row["drug"] == "aspirin"
            assert row["disease"] == "pain"
            assert row["outcome"] == "validated_positive"
            assert row["validated_by"] == "wet_lab:v121_test"
            assert row["writeback_version"] == WRITEBACK_VERSION
            # SH-003: NO non-canonical columns.
            assert "drug_id" not in row
            assert "disease_id" not in row
            assert "drug_name" not in row
            assert "disease_name" not in row
            assert "score" not in row
            print(f"  ✅ write_validated_hypothesis wrote canonical CSV row: {dict(row)}")
        finally:
            del os.environ["VALIDATED_HYPOTHESES_CSV"]
            del os.environ["PHASE1_VALIDATED_CSV"]


def test_real_static_ts_contract_matches_runtime_zod():
    """REAL CODE: read the actual frontend files and verify the static TS
    contract matches the runtime Zod schema (preventing v120-style drift)."""
    static_src = (_REPO_ROOT / "frontend" / "contracts" /
                  "api_contracts.ts").read_text()
    runtime_src = (_REPO_ROOT / "frontend" / "src" / "lib" /
                   "ml-contracts.ts").read_text()

    # ValidateResponse: both must use `ok` (not `success`).
    static_vr = re.search(
        r"export interface ValidateResponse \{(.*?)\}",
        static_src, re.DOTALL,
    ).group(1)
    runtime_vr = re.search(
        r"export const RlValidateResponseSchema = z\.object\(\{(.*?)\}\)",
        runtime_src, re.DOTALL,
    ).group(1)
    assert "ok:" in static_vr, "static ValidateResponse missing `ok`"
    assert "ok:" in runtime_vr, "runtime RlValidateResponseSchema missing `ok`"
    assert "writeback:" in static_vr
    assert "writeback:" in runtime_vr

    # RankedCandidate: both must use flat camelCase (not nested reward_breakdown).
    static_rc = re.search(
        r"export interface RankedCandidate \{(.*?)\}",
        static_src, re.DOTALL,
    ).group(1)
    runtime_rc = re.search(
        r"export const RankedHypothesisSchema = z\.object\(\{(.*?)\}\)",
        runtime_src, re.DOTALL,
    ).group(1)
    for field in ("gnnScore", "safetyScore", "marketScore",
                  "literatureSupport", "isKnownPositive"):
        assert field in static_rc, f"static RankedCandidate missing `{field}`"
        assert field in runtime_rc, f"runtime RankedHypothesisSchema missing `{field}`"
    assert "reward_breakdown" not in static_rc, (
        "static RankedCandidate must NOT have `reward_breakdown` (v120 drift)"
    )
    assert "reward_breakdown" not in runtime_rc, (
        "runtime Zod must NOT have `reward_breakdown` (v120 drift)"
    )
    print("  ✅ Static TS contract matches runtime Zod schema (no drift)")


def test_real_dev_samples_can_be_imported_in_dev():
    """REAL CODE: import _dev_samples in dev mode and verify the embedded
    data is correct (P1-016, P1-034, P1-048 fixes)."""
    from phase1.pipelines._dev_samples import (
        embedded_chembl_molecules,
        embedded_drugbank_drugs,
        embedded_drugbank_interactions,
        _PRODUCTION_GUARD_FAILED,
    )
    # P1-034: import must NOT raise in dev mode.
    # P1-016: all 10 drugs must have is_fda_approved=None.
    chembl_df = embedded_chembl_molecules()
    assert len(chembl_df) == 10
    assert chembl_df["is_fda_approved"].isna().all(), (
        "P1-016 FAIL: not all is_fda_approved are None"
    )
    drugbank_df = embedded_drugbank_drugs()
    assert len(drugbank_df) == 10
    assert drugbank_df["is_fda_approved"].isna().all()
    # P1-048: interactions must have 2-3 targets per drug (polypharmacology).
    interactions_df = embedded_drugbank_interactions()
    targets_per_drug = interactions_df.groupby("drugbank_id").size()
    assert targets_per_drug.min() >= 2, (
        f"P1-048 FAIL: some drugs have only 1 target. "
        f"Min targets per drug: {targets_per_drug.min()}"
    )
    print(f"  ✅ _dev_samples: 10 drugs, all is_fda_approved=None, "
          f"avg {targets_per_drug.mean():.1f} targets/drug")


def main():
    print("=" * 70)
    print("v121 REAL CODE integration test — exercising production paths")
    print("=" * 70)
    tests = [
        test_real_shared_contract_imports,
        test_real_static_ts_contract_matches_runtime_zod,
        test_real_rank_endpoint_returns_canonical_shape,
        test_real_validate_endpoint_rejects_invalid_outcome,
        test_real_validate_endpoint_accepts_all_4_canonical_outcomes,
        test_real_phase4_writeback_writes_canonical_csv,
        test_real_dev_samples_can_be_imported_in_dev,
    ]
    passed = 0
    failed = 0
    for t in tests:
        print(f"\n[{t.__name__}]")
        try:
            t()
            passed += 1
        except Exception as e:
            print(f"  ❌ FAILED: {type(e).__name__}: {e}")
            failed += 1
    print(f"\n{'=' * 70}")
    print(f"REAL CODE integration test: {passed} passed, {failed} failed")
    print(f"{'=' * 70}")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
