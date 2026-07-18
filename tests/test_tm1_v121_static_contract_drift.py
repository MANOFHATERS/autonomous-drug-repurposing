#!/usr/bin/env python3
"""v121 FORENSIC ROOT FIX regression tests — Teammate 1.

This test file prevents the SPECIFIC class of bug that v121 fixes:
the static TypeScript contract (frontend/contracts/api_contracts.ts)
drifting from the runtime Zod schema (frontend/src/lib/ml-contracts.ts)
AND from the Python service response shape.

BACKGROUND:
    The v120 "ROOT FIX" claimed to fix SH-005 and SH-024 by updating the
    static TS contract (api_contracts.ts) to a "canonical" shape with
    nested reward_breakdown + snake_case bools + success/csv_path fields.
    However, NEITHER the runtime Zod schema NOR the Python service was
    updated to match. The static contract became FICTION — it described
    a shape that nobody actually serves or consumes.

    The user (Manoj) caught this: "every session every ai tells its
    100 percent integrated but see the reality the report filet there
    are issues" — the comments claimed fixes, but manual verification
    showed the code was still broken.

v121 ROOT FIX:
    Reverted the static TS contract to EXACTLY match the runtime Zod
    schema (which matches Python). This way all three layers agree:
      1. Static TS contract (api_contracts.ts) — describes the shape
      2. Runtime Zod schema (ml-contracts.ts) — validates the shape
      3. Python service (rl/service.py) — serves the shape

THIS TEST FILE:
    Asserts that all three layers agree. If a future agent changes one
    layer without updating the others, this test fails — preventing a
    repeat of the v120 drift bug.

VERIFICATION APPROACH:
    The tests read the actual source files as TEXT and check for the
    presence of specific field names. This is intentionally brittle —
    a single character change in any layer will break the test, forcing
    the developer to update all three layers together.
"""
from __future__ import annotations

import csv
import os
import re
import sys
import tempfile
from pathlib import Path

import pytest

_REPO_ROOT = Path(__file__).resolve().parents[1]
_STATIC_CONTRACT = _REPO_ROOT / "frontend" / "contracts" / "api_contracts.ts"
_RUNTIME_ZOD = _REPO_ROOT / "frontend" / "src" / "lib" / "ml-contracts.ts"
_RL_SERVICE = _REPO_ROOT / "rl" / "service.py"


# =============================================================================
# SH-005: ValidateResponse shape — static TS, runtime Zod, Python all agree
# =============================================================================
class TestSH005StaticContractMatchesRuntimeZod:
    """SH-005 v121: the static TS ValidateResponse interface MUST match
    the runtime Zod RlValidateResponseSchema (which matches Python)."""

    def test_static_contract_uses_ok_not_success(self):
        """The static interface must use `ok` (not `success`) — matches Python."""
        src = _STATIC_CONTRACT.read_text()
        # Extract the ValidateResponse interface body.
        m = re.search(
            r"export interface ValidateResponse \{(.*?)\}",
            src, re.DOTALL,
        )
        assert m, "ValidateResponse interface not found in api_contracts.ts"
        body = m.group(1)
        assert "ok: boolean" in body, (
            "SH-005 v121 FAIL: static ValidateResponse must use `ok: boolean` "
            "(matches Python rl/service.py and runtime Zod schema). The "
            "previous v120 'fix' used `success: boolean` which NEITHER the "
            "Python service NOR the runtime Zod schema uses — that was the "
            "drift bug."
        )
        assert "success:" not in body, (
            "SH-005 v121 FAIL: static ValidateResponse must NOT have `success` "
            "field. Python returns `ok`, not `success`."
        )

    def test_static_contract_uses_writeback_nested_object(self):
        """The static interface must use `writeback: { phase1_csv_path, ... }`
        (not flat `csv_path`/`csv_row_count`) — matches Python."""
        src = _STATIC_CONTRACT.read_text()
        m = re.search(
            r"export interface ValidateResponse \{(.*?)\}",
            src, re.DOTALL,
        )
        assert m, "ValidateResponse interface not found"
        body = m.group(1)
        assert "writeback:" in body, (
            "SH-005 v121 FAIL: static ValidateResponse must have `writeback` "
            "nested object (matches Python + runtime Zod)."
        )
        assert "phase1_csv_path" in body, (
            "SH-005 v121 FAIL: writeback.phase1_csv_path missing."
        )
        assert "phase2_neo4j_written" in body, (
            "SH-005 v121 FAIL: writeback.phase2_neo4j_written missing."
        )
        assert "phase3_trigger_path" in body, (
            "SH-005 v121 FAIL: writeback.phase3_trigger_path missing."
        )
        assert "writeback_version" in body, (
            "SH-005 v121 FAIL: writeback.writeback_version missing."
        )
        # The v120 fictional fields must NOT be present at the TOP level.
        # We strip the nested `writeback: { ... }` block first, then check
        # the remaining top-level fields.
        top_level_body = re.sub(
            r"writeback:\s*\{[^{}]*\}", "", body, count=1, flags=re.DOTALL,
        )
        # Use word-boundary regex to avoid matching `phase1_csv_path` as `csv_path`.
        assert not re.search(r"(?<![\w])csv_path\s*[:?]", top_level_body), (
            "SH-005 v121 FAIL: static ValidateResponse must NOT have top-level "
            "`csv_path` — that was the v120 fictional shape. Python returns "
            "`writeback.phase1_csv_path` (nested)."
        )
        assert not re.search(r"(?<![\w])csv_row_count\s*[:?]", top_level_body), (
            "SH-005 v121 FAIL: static ValidateResponse must NOT have "
            "`csv_row_count` — Python does not return this field."
        )
        assert not re.search(r"(?<![\w])validated_at\s*[:?]", top_level_body), (
            "SH-005 v121 FAIL: static ValidateResponse must NOT have top-level "
            "`validated_at` — Python does not return this field at the top "
            "level (it's inside writeback.validated_hypothesis.validated_at)."
        )

    def test_runtime_zod_schema_matches_static_contract(self):
        """The runtime Zod RlValidateResponseSchema must have the SAME fields
        as the static ValidateResponse interface."""
        src = _RUNTIME_ZOD.read_text()
        m = re.search(
            r"export const RlValidateResponseSchema = z\.object\(\{(.*?)\}\)",
            src, re.DOTALL,
        )
        assert m, "RlValidateResponseSchema not found in ml-contracts.ts"
        body = m.group(1)
        assert "ok: z.boolean()" in body
        assert "writeback: z.object" in body
        assert "phase1_csv_path" in body
        assert "phase2_neo4j_written" in body
        assert "phase3_trigger_path" in body
        assert "writeback_version" in body

    def test_python_validate_endpoint_returns_ok_not_success(self):
        """Python rl/service.py /validate must return `ok` (not `success`)."""
        src = _RL_SERVICE.read_text()
        # Find the validate() function's return statement.
        m = re.search(
            r'def validate\(req: ValidateRequest\).*?return \{(.*?)\}',
            src, re.DOTALL,
        )
        assert m, "validate() function return statement not found"
        ret_body = m.group(1)
        assert '"ok": True' in ret_body or '"ok":' in ret_body, (
            "SH-005 v121 FAIL: Python /validate must return `ok: True` "
            "(not `success: True`)."
        )
        assert '"success":' not in ret_body, (
            "SH-005 v121 FAIL: Python /validate must NOT return `success`."
        )
        assert '"writeback":' in ret_body, (
            "SH-005 v121 FAIL: Python /validate must return `writeback` "
            "nested object."
        )


# =============================================================================
# SH-024: RankedCandidate shape — static TS, runtime Zod, Python all agree
# =============================================================================
class TestSH024StaticContractMatchesRuntimeZod:
    """SH-024 v121: the static TS RankedCandidate interface MUST match
    the runtime Zod RankedHypothesisSchema (which matches Python)."""

    def test_static_contract_uses_flat_camelCase_not_nested_reward_breakdown(self):
        """The static interface must use FLAT camelCase fields (gnnScore,
        safetyScore, etc.) — NOT a nested reward_breakdown object."""
        src = _STATIC_CONTRACT.read_text()
        m = re.search(
            r"export interface RankedCandidate \{(.*?)\}",
            src, re.DOTALL,
        )
        assert m, "RankedCandidate interface not found in api_contracts.ts"
        body = m.group(1)
        # Must have flat camelCase fields.
        assert "gnnScore" in body, (
            "SH-024 v121 FAIL: static RankedCandidate must have `gnnScore` "
            "(camelCase, flat) — matches Python + runtime Zod."
        )
        assert "safetyScore" in body
        assert "marketScore" in body
        assert "literatureSupport" in body
        assert "isKnownPositive" in body
        # Must NOT have the v120 fictional nested reward_breakdown.
        assert "reward_breakdown" not in body, (
            "SH-024 v121 FAIL: static RankedCandidate must NOT have "
            "`reward_breakdown` nested object — that was the v120 fictional "
            "shape. Python returns FLAT camelCase fields (gnnScore, etc.)."
        )
        # Must NOT have snake_case versions (would be a new drift).
        assert "gnn_score:" not in body, (
            "SH-024 v121 FAIL: static RankedCandidate must NOT have "
            "`gnn_score` (snake_case) — Python returns `gnnScore` (camelCase)."
        )
        assert "literature_support:" not in body
        assert "is_known_positive:" not in body

    def test_static_contract_uses_drug_disease_not_drug_id(self):
        """The static interface must use `drug`/`disease` (not
        `drug_id`/`drug_name`/`disease_id`/`disease_name`)."""
        src = _STATIC_CONTRACT.read_text()
        m = re.search(
            r"export interface RankedCandidate \{(.*?)\}",
            src, re.DOTALL,
        )
        assert m, "RankedCandidate interface not found"
        body = m.group(1)
        assert "drug: string" in body
        assert "disease: string" in body
        assert "drug_id" not in body, (
            "SH-024 v121 FAIL: static RankedCandidate must NOT have `drug_id`."
        )
        assert "disease_id" not in body
        assert "drug_name" not in body
        assert "disease_name" not in body

    def test_runtime_zod_schema_matches_static_contract(self):
        """The runtime Zod RankedHypothesisSchema must have the SAME fields
        as the static RankedCandidate interface."""
        src = _RUNTIME_ZOD.read_text()
        m = re.search(
            r"export const RankedHypothesisSchema = z\.object\(\{(.*?)\}\)",
            src, re.DOTALL,
        )
        assert m, "RankedHypothesisSchema not found in ml-contracts.ts"
        body = m.group(1)
        assert "drug: z.string()" in body
        assert "disease: z.string()" in body
        assert "gnnScore" in body
        assert "safetyScore" in body
        assert "marketScore" in body
        assert "literatureSupport" in body
        assert "isKnownPositive" in body
        # Must NOT have nested reward_breakdown.
        assert "reward_breakdown" not in body, (
            "SH-024 v121 FAIL: runtime Zod RankedHypothesisSchema must NOT "
            "have `reward_breakdown` — Python doesn't return it."
        )

    def test_python_load_candidates_returns_camelCase_flat_fields(self):
        """Python rl/service.py _load_candidates_from_csv must return
        camelCase flat fields (gnnScore, safetyScore, etc.) — NOT a
        nested reward_breakdown object."""
        src = _RL_SERVICE.read_text()
        # Find the out.append({...}) block in _load_candidates_from_csv.
        m = re.search(
            r"out\.append\(\{(.*?)\}\)",
            src, re.DOTALL,
        )
        assert m, "out.append({...}) block not found in _load_candidates_from_csv"
        body = m.group(1)
        assert '"gnnScore"' in body, (
            "SH-024 v121 FAIL: Python must return `gnnScore` (camelCase)."
        )
        assert '"safetyScore"' in body
        assert '"marketScore"' in body
        assert '"literatureSupport"' in body
        assert '"isKnownPositive"' in body
        # Must NOT have nested reward_breakdown.
        assert '"reward_breakdown"' not in body, (
            "SH-024 v121 FAIL: Python must NOT return `reward_breakdown` "
            "nested object — that would break the runtime Zod schema."
        )

    def test_python_response_uses_drug_disease_not_drug_id(self):
        """Python _load_candidates_from_csv must return `drug`/`disease`
        (not `drug_id`/`drug_name`)."""
        from rl.service import _load_candidates_from_csv
        with tempfile.NamedTemporaryFile(mode="w", suffix=".csv",
                                          delete=False, newline="") as tf:
            w = csv.writer(tf)
            w.writerow(["drug", "disease", "rank", "gnn_score",
                        "safety_score", "market_score"])
            w.writerow(["aspirin", "pain", 1, 0.9, 0.8, 0.7])
            tf_path = tf.name
        try:
            result = _load_candidates_from_csv(
                Path(tf_path), drug=None, disease=None, limit=10
            )
            assert "candidates" in result
            c = result["candidates"][0]
            assert "drug" in c
            assert "disease" in c
            assert "drug_id" not in c
            assert "disease_id" not in c
            assert "drug_name" not in c
            assert "disease_name" not in c
            # SH-024 v121: camelCase flat fields must be present.
            assert "gnnScore" in c
            assert "safetyScore" in c
            assert "marketScore" in c
            # SH-024 v121: nested reward_breakdown must NOT be present.
            assert "reward_breakdown" not in c, (
                "SH-024 v121 FAIL: Python must NOT return reward_breakdown "
                "nested object — that's the v120 fictional shape."
            )
        finally:
            os.unlink(tf_path)


# =============================================================================
# RankResponse shape — static TS, runtime Zod, Python all agree
# =============================================================================
class TestRankResponseShape:
    """The static TS RankResponse interface MUST match the runtime Zod
    RlRankResponseSchema (which matches Python _rank_impl)."""

    def test_static_contract_uses_generatedAt_not_ranked_at(self):
        """The static interface must use `generatedAt` (not `ranked_at`)
        — matches Python + runtime Zod."""
        src = _STATIC_CONTRACT.read_text()
        m = re.search(
            r"export interface RankResponse \{(.*?)\}",
            src, re.DOTALL,
        )
        assert m, "RankResponse interface not found"
        body = m.group(1)
        assert "generatedAt" in body, (
            "RankResponse must have `generatedAt` (matches Python + Zod)."
        )
        # The v120 fictional fields must NOT be present.
        assert "ranked_at" not in body, (
            "RankResponse must NOT have `ranked_at` — Python returns `generatedAt`."
        )
        assert "next_cursor" not in body, (
            "RankResponse must NOT have `next_cursor` — Python returns "
            "`page` + `pageSize` + `count` for pagination."
        )

    def test_static_contract_has_pagination_fields(self):
        """The static interface must have `page`, `pageSize`, `count`, `total`
        — matches Python + runtime Zod."""
        src = _STATIC_CONTRACT.read_text()
        m = re.search(
            r"export interface RankResponse \{(.*?)\}",
            src, re.DOTALL,
        )
        assert m, "RankResponse interface not found"
        body = m.group(1)
        assert "page:" in body
        assert "pageSize:" in body
        assert "count:" in body
        assert "total:" in body


# =============================================================================
# Cross-layer drift prevention — meta-test
# =============================================================================
class TestCrossLayerDriftPrevention:
    """Meta-test: verify the static TS contract, runtime Zod schema, and
    Python service all agree on the SAME field names. This is the test
    that would have caught the v120 drift bug."""

    def test_no_drift_between_static_and_runtime_zod(self):
        """If a field exists in BOTH the static TS contract AND the runtime
        Zod schema, they must use the SAME name. Catches drift like
        `success` (static) vs `ok` (runtime)."""
        static_src = _STATIC_CONTRACT.read_text()
        runtime_src = _RUNTIME_ZOD.read_text()

        # ValidateResponse: static must have `ok`, runtime must have `ok`.
        static_vr = re.search(
            r"export interface ValidateResponse \{(.*?)\}",
            static_src, re.DOTALL,
        ).group(1)
        runtime_vr = re.search(
            r"export const RlValidateResponseSchema = z\.object\(\{(.*?)\}\)",
            runtime_src, re.DOTALL,
        ).group(1)
        # Both must agree on `ok` (not `success`).
        assert ("ok:" in static_vr) == ("ok:" in runtime_vr), (
            "DRIFT: static ValidateResponse and runtime RlValidateResponseSchema "
            "disagree on `ok` field."
        )
        assert ("success:" in static_vr) == ("success:" in runtime_vr), (
            "DRIFT: static and runtime disagree on `success` field."
        )
        # Both must agree on `writeback` nested object.
        assert ("writeback:" in static_vr) == ("writeback:" in runtime_vr)

    def test_no_drift_between_static_and_python(self):
        """The static TS contract field names must match what Python returns."""
        static_src = _STATIC_CONTRACT.read_text()
        py_src = _RL_SERVICE.read_text()

        # RankedCandidate: static must have `gnnScore`, Python must return `gnnScore`.
        static_rc = re.search(
            r"export interface RankedCandidate \{(.*?)\}",
            static_src, re.DOTALL,
        ).group(1)
        py_append = re.search(
            r"out\.append\(\{(.*?)\}\)",
            py_src, re.DOTALL,
        ).group(1)
        # Both must agree on camelCase flat fields.
        for field in ("gnnScore", "safetyScore", "marketScore",
                      "literatureSupport", "isKnownPositive"):
            static_has = field in static_rc
            py_has = f'"{field}"' in py_append
            assert static_has == py_has, (
                f"DRIFT: static RankedCandidate and Python _load_candidates_from_csv "
                f"disagree on `{field}` field. static={static_has}, python={py_has}."
            )
        # Both must NOT have nested reward_breakdown.
        assert "reward_breakdown" not in static_rc, (
            "DRIFT: static RankedCandidate has `reward_breakdown` — Python "
            "doesn't return it."
        )
        assert '"reward_breakdown"' not in py_append, (
            "DRIFT: Python returns `reward_breakdown` — static contract "
            "doesn't declare it."
        )


if __name__ == "__main__":
    # Allow running this test file directly: python tests/test_tm1_v121_static_contract_drift.py
    sys.exit(pytest.main([__file__, "-v"]))
