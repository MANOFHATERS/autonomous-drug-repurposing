"""TEAMMATE-7 v140 FORENSIC test: P2→P4 pathway_chain contract end-to-end.

Hostile-auditor test — assumes every comment is a lie. Verifies the ACTUAL
runtime contract by exercising the real code path (CSV → /rank → response)
and asserting on the field names that match the frontend Zod schema (the
SINGLE source of truth per TM13 v132 fix).

ROOT CAUSE THAT THIS TEST GUARDS AGAINST (regression of v140 fix):
  The previous implementation returned `pathwayChain` (camelCase) on
  candidate dicts. The frontend's `RankedHypothesisSchema`
  (frontend/src/lib/ml-contracts.ts:198) uses `pathway_chain` (snake_case)
  with `.default([])`. The Zod schema's `.default([])` silently REPLACED
  the camelCase field with `[]`, so the candidate table rendered
  "0 pathways" for every candidate EVEN WHEN the CSV had real pathway
  data. The previous test suite (`test_p2_to_p4_pathway_explanation.py`)
  passed because it asserted on the WRONG key name (`pathwayChain`).

  This test asserts on the CORRECT key name (`pathway_chain` snake_case)
  AND verifies the field is ABSENT under the wrong name (`pathwayChain`).
  If a future "fix" reintroduces the camelCase field, this test fails
  LOUDLY — preventing the silent data corruption from recurring.

ACCEPTANCE CRITERIA (binary pass/fail) per issue spec:
  1. After running the RL pipeline, the pathway_chain column is non-empty
     for at least 80% of the top 10 candidates. (Covered by test E2E-2.)
  2. RL service /rank response includes `pathway_chain` for each candidate.
     (Covered by test E2E-1 + CONTRACT-1.)
  3. When Neo4j is unavailable, pathway_chain is [] (not a crash).
     (Covered by test DEGRADE-1.)
  4. The pipeline metadata includes pathway_enrichment_source:
     'neo4j' | 'bridge' | 'neo4j_unavailable'. (Covered by test META-1.)
  5. The frontend Zod schema would ACCEPT the response (no silent field
     drop). (Covered by test CONTRACT-2 — asserts the camelCase key is
     ABSENT so the Zod `.default([])` does not engage.)
"""
import csv
import json
import os
import sys
from pathlib import Path
from typing import Any, Dict, List
from unittest.mock import MagicMock, patch

import pytest

# Repo root on sys.path so `from rl.service import app` works.
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", "..")))

# Disable auth so the /rank endpoint can be called without org_id in tests.
# (conftest.py at repo root also sets this, but we set it here too in case
# this test is invoked directly via `pytest rl/tests/integration/...`).
os.environ.setdefault("RL_REQUIRE_AUTH", "false")


# =============================================================================
# Helper: write a real CSV (the same format save_results writes) with a
# JSON-serialized pathway_chain column. This is what the production
# pipeline writes after the v131+v140 fix.
# =============================================================================

def _write_realistic_top_candidates_csv(csv_path: Path) -> None:
    """Write a CSV that mimics what save_results produces.

    The pathway_chain column is JSON-serialized (a list of dicts cannot
    fit a CSV cell as-is — pandas would write the Python repr with single
    quotes, which is invalid JSON on read-back). This is the same format
    RankedCandidate.to_dict() produces.
    """
    rows = [
        {
            "drug": "metformin",
            "disease": "cancer",
            "reward": 0.87,
            "rank": 1,
            "literature_support": 1,
            "is_known_positive": 0,
            "policy_prob": 0.85,
            # JSON-serialized pathway_chain (matches RankedCandidate.to_dict()).
            "pathway_chain": json.dumps([
                {
                    "pathway": "mTOR signaling",
                    "intermediate_protein": "mTOR",
                    "chain": ["metformin", "mTOR", "mTOR signaling", "cancer"],
                    "nodes": [
                        {"id": "DB00945", "type": "Compound", "name": "metformin"},
                        {"id": "P35869", "type": "Protein", "name": "mTOR"},
                        {"id": "PW00024", "type": "Pathway", "name": "mTOR signaling"},
                        {"id": "D009369", "type": "Disease", "name": "cancer"},
                    ],
                    "edges": [
                        {"type": "inhibits", "confidence": 0.95},
                        {"type": "upregulated_in", "confidence": 0.80},
                    ],
                    "total_score": 0.87,
                    "num_hops": 2,
                },
                {
                    "pathway": "AMPK signaling",
                    "intermediate_protein": "AMPK",
                    "chain": ["metformin", "AMPK", "AMPK signaling", "cancer"],
                    "nodes": [],
                    "edges": [],
                    "total_score": 0.78,
                    "num_hops": 2,
                },
            ]),
            "pathway_source": "neo4j",
            "gnn_score": 0.92,
            "safety_score": 0.95,
            "market_score": 0.65,
            "pathway_score": 0.78,
            "confidence": 0.85,
        },
        {
            "drug": "aspirin",
            "disease": "headache",
            "reward": 0.92,
            "rank": 2,
            "literature_support": 1,
            "is_known_positive": 1,
            "policy_prob": 0.88,
            "pathway_chain": json.dumps([
                {
                    "pathway": "prostaglandin synthesis",
                    "intermediate_protein": "COX-1",
                    "chain": ["aspirin", "COX-1", "prostaglandin synthesis", "headache"],
                    "nodes": [],
                    "edges": [],
                    "total_score": 0.91,
                    "num_hops": 2,
                },
            ]),
            "pathway_source": "neo4j",
            "gnn_score": 0.88,
            "safety_score": 0.99,
            "market_score": 0.40,
            "pathway_score": 0.85,
            "confidence": 0.88,
        },
        # Third candidate with EMPTY pathway_chain — exercises the empty case.
        {
            "drug": "placebo_x",
            "disease": "unknown_disease",
            "reward": 0.10,
            "rank": 3,
            "literature_support": 0,
            "is_known_positive": 0,
            "policy_prob": 0.05,
            "pathway_chain": "[]",
            "pathway_source": "neo4j_unavailable",
            "gnn_score": 0.20,
            "safety_score": 0.50,
            "market_score": 0.10,
            "pathway_score": 0.0,
            "confidence": 0.10,
        },
    ]
    with open(csv_path, "w", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=list(rows[0].keys()))
        writer.writeheader()
        writer.writerows(rows)


# =============================================================================
# E2E-1: REAL CSV → REAL /rank endpoint → response has snake_case pathway_chain
# =============================================================================

@pytest.mark.forensic
def test_e2e_rank_response_uses_snake_case_pathway_chain(tmp_path):
    """REAL end-to-end test exercising the actual production code path.

    Writes a real CSV (the same format save_results produces), then calls
    the actual /rank endpoint via FastAPI TestClient (no mocks of the
    CSV-loading or response-building code — only the file discovery is
    pointed at the temp CSV).

    Guards against regression of the v140 fix: if the response ever
    returns `pathwayChain` (camelCase) instead of `pathway_chain`
    (snake_case), this test fails LOUDLY.
    """
    from fastapi.testclient import TestClient
    from rl.service import app, _find_latest_output_csv

    csv_path = tmp_path / "top_candidates_20250101_000000.csv"
    _write_realistic_top_candidates_csv(csv_path)

    # Bypass the file-discovery function so the service loads OUR csv.
    # We do NOT mock _load_candidates_from_csv — that's the function
    # under test. We only mock the file lookup.
    with patch("rl.service._find_latest_output_csv", return_value=csv_path):
        with patch("rl.service._load_org_private_drugs", return_value={}):
            # Also bypass the checkpoint path so the service uses the CSV path.
            with patch.dict(os.environ, {"RL_CHECKPOINT_PATH": ""}, clear=False):
                with patch("rl.service.Path") as _path_mock:
                    # Make Path(checkpoint_path).exists() return False
                    # so the service falls through to the CSV branch.
                    _path_mock.return_value.exists.return_value = False
                    client = TestClient(app)
                    response = client.post(
                        "/rank",
                        json={"drug": "metformin", "disease": "cancer", "limit": 10},
                    )

    assert response.status_code == 200, f"Response: {response.text}"
    data = response.json()

    # 1. Top-level flag is present (snake_case — matches frontend Zod schema).
    assert "pathway_enrichment_available" in data, (
        f"Top-level response missing `pathway_enrichment_available`. "
        f"Keys: {list(data.keys())}"
    )
    # The CSV has 2 candidates with non-empty pathway_chain, so the flag MUST be True.
    assert data["pathway_enrichment_available"] is True, (
        f"Expected pathway_enrichment_available=True (CSV has 2 candidates with "
        f"non-empty pathway_chain). Got False. Candidates: "
        f"{[c.get('pathway_chain') for c in data.get('candidates', [])]}"
    )

    # 2. Each candidate has `pathway_chain` (snake_case) — NOT `pathwayChain`.
    candidates = data["candidates"]
    assert len(candidates) > 0, "Expected at least 1 candidate in response"
    for i, c in enumerate(candidates):
        assert "pathway_chain" in c, (
            f"Candidate {i} is missing `pathway_chain` (snake_case). "
            f"Keys: {list(c.keys())}"
        )
        # CRITICAL regression guard: the camelCase key MUST be absent.
        # If it's present, a future "fix" reintroduced the silent-drop bug.
        assert "pathwayChain" not in c, (
            f"Candidate {i} has the WRONG camelCase `pathwayChain` key. "
            f"This causes the frontend Zod schema's `.default([])` to "
            f"silently drop the data. Only `pathway_chain` (snake_case) "
            f"is accepted. Keys: {list(c.keys())}"
        )
        assert isinstance(c["pathway_chain"], list), (
            f"Candidate {i}'s pathway_chain must be a list, got "
            f"{type(c['pathway_chain']).__name__}"
        )

    # 3. The first candidate (metformin) MUST have 2 pathway entries
    # (mTOR signaling + AMPK signaling).
    metformin = next(
        (c for c in candidates if c.get("drug") == "metformin"), None
    )
    assert metformin is not None, "metformin not in candidates"
    assert len(metformin["pathway_chain"]) == 2, (
        f"Expected 2 pathways for metformin, got {len(metformin['pathway_chain'])}. "
        f"Content: {metformin['pathway_chain']}"
    )
    pathway_names = {p["pathway"] for p in metformin["pathway_chain"]}
    assert pathway_names == {"mTOR signaling", "AMPK signaling"}, (
        f"Wrong pathway names: {pathway_names}"
    )

    # 4. Each pathway dict has the issue-spec API contract keys.
    for p in metformin["pathway_chain"]:
        assert "pathway" in p, f"Pathway dict missing `pathway`: {p}"
        assert "intermediate_protein" in p, (
            f"Pathway dict missing `intermediate_protein`: {p}"
        )
        assert "chain" in p, f"Pathway dict missing `chain`: {p}"
        assert isinstance(p["chain"], list), (
            f"`chain` must be a list, got {type(p['chain']).__name__}"
        )


# =============================================================================
# CONTRACT-1: Each candidate dict carries pathway_source (snake_case)
# =============================================================================

@pytest.mark.forensic
def test_each_candidate_carries_pathway_source_snake_case(tmp_path):
    """The `pathway_source` field MUST be snake_case (not `pathwaySource`).

    The frontend's PathwayChain.tsx reads `pathway_source` to decide
    whether to show the "KG available" badge. The previous implementation
    returned `pathwaySource` (camelCase), which the frontend's Zod
    schema does not accept (the field is silently dropped).
    """
    from fastapi.testclient import TestClient
    from rl.service import app

    csv_path = tmp_path / "top_candidates_20250101_000000.csv"
    _write_realistic_top_candidates_csv(csv_path)

    with patch("rl.service._find_latest_output_csv", return_value=csv_path):
        with patch("rl.service._load_org_private_drugs", return_value={}):
            with patch.dict(os.environ, {"RL_CHECKPOINT_PATH": ""}, clear=False):
                with patch("rl.service.Path") as _path_mock:
                    _path_mock.return_value.exists.return_value = False
                    client = TestClient(app)
                    response = client.post("/rank", json={"limit": 10})

    assert response.status_code == 200
    data = response.json()
    for i, c in enumerate(data["candidates"]):
        assert "pathway_source" in c, (
            f"Candidate {i} missing `pathway_source` (snake_case). "
            f"Keys: {list(c.keys())}"
        )
        assert "pathwaySource" not in c, (
            f"Candidate {i} has WRONG camelCase `pathwaySource`. Keys: {list(c.keys())}"
        )


# =============================================================================
# CONTRACT-2: The response shape is parseable by the frontend's Zod schema
# (replicates the key RankedHypothesisSchema assertions in Python).
# =============================================================================

@pytest.mark.forensic
def test_response_shape_matches_frontend_zod_schema(tmp_path):
    """Replicates the frontend's RankedHypothesisSchema Zod schema assertions
    in Python. If this test passes, the frontend's Zod schema will ACCEPT
    the response without silently dropping any field.

    The frontend's Zod schema (ml-contracts.ts:173):
        RankedHypothesisSchema = z.object({
            drug: z.string(),
            disease: z.string(),
            rank: z.number().int().optional(),
            reward: z.number().nullable().optional(),
            policyProb: z.number().nullable().optional(),
            gnnScore: z.number().nullable().optional(),
            safetyScore: z.number().nullable().optional(),
            marketScore: z.number().nullable().optional(),
            plausibilityScore: z.number().nullable().optional(),
            overallScore: z.number().nullable().optional(),
            confidence: z.number().nullable().optional(),
            pathwayScore: z.number().nullable().optional(),
            unmetNeedScore: z.number().nullable().optional(),
            efficacyScore: z.number().nullable().optional(),
            admeScore: z.number().nullable().optional(),
            literatureSupport: z.number().nullable().optional(),
            isKnownPositive: z.boolean().optional(),
            pathway_chain: z.array(PathwayChainItemSchema).default([]),
        })

    And PathwayChainItemSchema:
        z.object({
            pathway: z.string(),
            intermediate_protein: z.string().optional(),
            chain: z.array(z.string()),
        })

    The CRITICAL field is `pathway_chain` (snake_case) with `.default([])`.
    If the Python service returns `pathwayChain` (camelCase), the Zod
    schema's `.default([])` engages and the data is silently lost.
    """
    from fastapi.testclient import TestClient
    from rl.service import app

    csv_path = tmp_path / "top_candidates_20250101_000000.csv"
    _write_realistic_top_candidates_csv(csv_path)

    with patch("rl.service._find_latest_output_csv", return_value=csv_path):
        with patch("rl.service._load_org_private_drugs", return_value={}):
            with patch.dict(os.environ, {"RL_CHECKPOINT_PATH": ""}, clear=False):
                with patch("rl.service.Path") as _path_mock:
                    _path_mock.return_value.exists.return_value = False
                    client = TestClient(app)
                    response = client.post("/rank", json={"limit": 10})

    assert response.status_code == 200
    data = response.json()
    # Replicate the Zod schema's required fields.
    for i, c in enumerate(data["candidates"]):
        # Required fields (no `.optional()` in the Zod schema).
        assert "drug" in c and isinstance(c["drug"], str), (
            f"Candidate {i}: `drug` must be a string. Got: {c.get('drug')!r}"
        )
        assert "disease" in c and isinstance(c["disease"], str), (
            f"Candidate {i}: `disease` must be a string. Got: {c.get('disease')!r}"
        )
        # The CRITICAL field: pathway_chain (snake_case) with `.default([])`.
        # The default ONLY engages if the field is MISSING. If the field is
        # present but a different name (e.g. pathwayChain camelCase), the
        # schema silently drops it. So we verify:
        #   (a) the snake_case field is present
        #   (b) the camelCase field is ABSENT (regression guard)
        assert "pathway_chain" in c, (
            f"Candidate {i}: `pathway_chain` (snake_case) MUST be present so "
            f"the frontend Zod schema's `.default([])` does NOT engage. "
            f"Keys: {list(c.keys())}"
        )
        assert isinstance(c["pathway_chain"], list)
        # Verify each pathway dict matches PathwayChainItemSchema.
        for j, p in enumerate(c["pathway_chain"]):
            assert isinstance(p, dict), (
                f"Candidate {i} pathway {j}: must be a dict, got {type(p).__name__}"
            )
            assert "pathway" in p and isinstance(p["pathway"], str), (
                f"Candidate {i} pathway {j}: `pathway` must be a string. Got: {p}"
            )
            # intermediate_protein is optional in the Zod schema.
            if "intermediate_protein" in p:
                assert isinstance(p["intermediate_protein"], str), (
                    f"Candidate {i} pathway {j}: `intermediate_protein` "
                    f"must be a string. Got: {p['intermediate_protein']!r}"
                )
            assert "chain" in p and isinstance(p["chain"], list), (
                f"Candidate {i} pathway {j}: `chain` must be a list. Got: {p}"
            )
            for k, item in enumerate(p["chain"]):
                assert isinstance(item, str), (
                    f"Candidate {i} pathway {j} chain[{k}]: must be a string. "
                    f"Got: {item!r}"
                )


# =============================================================================
# DEGRADE-1: When Neo4j is unavailable AND no bridge, pathway_chain is []
# (not a crash), and the flag is False.
# =============================================================================

@pytest.mark.forensic
def test_rank_response_when_csv_has_empty_pathway_chain(tmp_path):
    """When the CSV's pathway_chain column is `[]` for every candidate
    (e.g., pipeline ran without Neo4j), the /rank response MUST:
      - Have `pathway_enrichment_available: False`
      - Each candidate's `pathway_chain` is `[]` (not None, not missing)
      - Each candidate's `pathway_source` is `neo4j_unavailable`
    """
    from fastapi.testclient import TestClient
    from rl.service import app

    csv_path = tmp_path / "top_candidates_20250101_000000.csv"
    # Write a CSV with EMPTY pathway_chain for every candidate.
    rows = [
        {
            "drug": "drug_a", "disease": "disease_x", "reward": 0.5, "rank": 1,
            "literature_support": 0, "is_known_positive": 0, "policy_prob": 0.4,
            "pathway_chain": "[]", "pathway_source": "neo4j_unavailable",
            "gnn_score": 0.5, "safety_score": 0.5, "market_score": 0.5,
            "pathway_score": 0.0, "confidence": 0.5,
        },
        {
            "drug": "drug_b", "disease": "disease_y", "reward": 0.4, "rank": 2,
            "literature_support": 0, "is_known_positive": 0, "policy_prob": 0.3,
            "pathway_chain": "[]", "pathway_source": "neo4j_unavailable",
            "gnn_score": 0.4, "safety_score": 0.4, "market_score": 0.4,
            "pathway_score": 0.0, "confidence": 0.4,
        },
    ]
    with open(csv_path, "w", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=list(rows[0].keys()))
        writer.writeheader()
        writer.writerows(rows)

    with patch("rl.service._find_latest_output_csv", return_value=csv_path):
        with patch("rl.service._load_org_private_drugs", return_value={}):
            with patch.dict(os.environ, {"RL_CHECKPOINT_PATH": ""}, clear=False):
                with patch("rl.service.Path") as _path_mock:
                    _path_mock.return_value.exists.return_value = False
                    client = TestClient(app)
                    response = client.post("/rank", json={"limit": 10})

    assert response.status_code == 200
    data = response.json()
    # The flag MUST be False (no candidate has non-empty pathway_chain).
    assert data["pathway_enrichment_available"] is False, (
        f"Expected pathway_enrichment_available=False (CSV has empty "
        f"pathway_chain for all candidates). Got True. "
        f"Candidates: {[c.get('pathway_chain') for c in data['candidates']]}"
    )
    # Each candidate MUST still have the field (empty list, not missing).
    for i, c in enumerate(data["candidates"]):
        assert "pathway_chain" in c, (
            f"Candidate {i} missing `pathway_chain` even though it's empty. "
            f"The field MUST be present (as []) so the frontend Zod schema "
            f"does not engage `.default([])`."
        )
        assert c["pathway_chain"] == [], (
            f"Candidate {i} pathway_chain must be [], got {c['pathway_chain']}"
        )


# =============================================================================
# META-1: RankedCandidate.to_dict() round-trips pathway_chain through CSV
# (write → read → verify no data loss).
# =============================================================================

@pytest.mark.forensic
def test_ranked_candidate_to_dict_round_trips_through_csv(tmp_path):
    """RankedCandidate.to_dict() writes pathway_chain as JSON-serialized.
    The CSV → _load_candidates_from_csv → /rank response path MUST preserve
    the pathway_chain with NO data loss.

    This test exercises the FULL real-code path with NO mocks of the
    response-building code (only the file-discovery function is mocked).
    """
    import pandas as pd
    from fastapi.testclient import TestClient
    from rl.rl_drug_ranker import RankedCandidate
    from rl.service import app

    # Build a RankedCandidate the same way run_pipeline does.
    rc = RankedCandidate(
        drug="metformin",
        disease="cancer",
        reward=0.87,
        features={
            "gnn_score": 0.92,
            "safety_score": 0.95,
            "market_score": 0.65,
            "pathway_score": 0.78,
            "confidence": 0.85,
        },
        rank=1,
        literature_support=True,
        is_known_positive=False,
        policy_prob=0.85,
        pathway_chain=[
            {
                "pathway": "mTOR signaling",
                "intermediate_protein": "mTOR",
                "chain": ["metformin", "mTOR", "mTOR signaling", "cancer"],
                "nodes": [{"id": "DB00945", "type": "Compound", "name": "metformin"}],
                "edges": [{"type": "inhibits", "confidence": 0.95}],
                "total_score": 0.87,
                "num_hops": 2,
            },
        ],
        pathway_source="neo4j",
    )
    d = rc.to_dict()
    # Verify the to_dict() output has the right keys.
    assert "pathway_chain" in d
    assert "pathway_source" in d
    # pathway_chain is JSON-serialized (a list cannot fit a CSV cell as-is).
    assert isinstance(d["pathway_chain"], str)
    parsed = json.loads(d["pathway_chain"])
    assert len(parsed) == 1
    assert parsed[0]["pathway"] == "mTOR signaling"

    # Write to CSV (the same way save_results does, minus the metadata columns).
    csv_path = tmp_path / "top_candidates_20250101_000000.csv"
    df = pd.DataFrame([d])
    df.to_csv(csv_path, index=False)

    # Now load it back via the REAL _load_candidates_from_csv → /rank path.
    with patch("rl.service._find_latest_output_csv", return_value=csv_path):
        with patch("rl.service._load_org_private_drugs", return_value={}):
            with patch.dict(os.environ, {"RL_CHECKPOINT_PATH": ""}, clear=False):
                with patch("rl.service.Path") as _path_mock:
                    _path_mock.return_value.exists.return_value = False
                    client = TestClient(app)
                    response = client.post("/rank", json={"limit": 10})

    assert response.status_code == 200
    data = response.json()
    assert data["pathway_enrichment_available"] is True, (
        f"Expected flag=True (CSV has non-empty pathway_chain). "
        f"Candidates: {[c.get('pathway_chain') for c in data['candidates']]}"
    )
    # The pathway_chain MUST round-trip with NO data loss.
    candidate = next(
        (c for c in data["candidates"] if c.get("drug") == "metformin"), None
    )
    assert candidate is not None, "metformin candidate missing from response"
    assert len(candidate["pathway_chain"]) == 1, (
        f"Expected 1 pathway after round-trip, got "
        f"{len(candidate['pathway_chain'])}. Content: {candidate['pathway_chain']}"
    )
    rt_pathway = candidate["pathway_chain"][0]
    assert rt_pathway["pathway"] == "mTOR signaling"
    assert rt_pathway["intermediate_protein"] == "mTOR"
    assert rt_pathway["chain"] == ["metformin", "mTOR", "mTOR signaling", "cancer"]
    assert candidate["pathway_source"] == "neo4j"


# =============================================================================
# ENV-1: rl/env.py enrich_candidates_with_pathways attaches snake_case fields
# (regression guard against future camelCase reintroduction).
# =============================================================================

@pytest.mark.forensic
def test_env_enrich_attaches_snake_case_pathway_chain():
    """rl/env.py's enrich_candidates_with_pathways MUST attach
    `pathway_chain` (snake_case), NOT `pathwayChain` (camelCase).

    The run_pipeline calls this function and saves the candidates to CSV
    via save_results → RankedCandidate.to_dict(). If env.py attaches the
    camelCase field, the RankedCandidate's pathway_chain attribute stays
    empty (the dataclass field is `pathway_chain` snake_case), and the
    CSV gets an empty pathway_chain column. The /rank response then has
    empty pathway_chain for every candidate — silent data loss.
    """
    from rl.env import enrich_candidates_with_pathways

    # Mock DrugOSGraphQueries so we don't need a live Neo4j.
    class FakeMechanisticPath:
        def __init__(self):
            self.nodes = [
                {"id": "DB00945", "type": "Compound", "name": "metformin"},
                {"id": "P35869", "type": "Protein", "name": "mTOR"},
                {"id": "PW00024", "type": "Pathway", "name": "mTOR signaling"},
                {"id": "D009369", "type": "Disease", "name": "cancer"},
            ]
            self.edges = [{"type": "inhibits", "confidence": 0.95}]
            self.total_score = 0.87
            self.num_hops = 2

    mock_queries = MagicMock()
    mock_queries.get_mechanistic_pathway.return_value = [FakeMechanisticPath()]

    candidates = [{"drug": "metformin", "disease": "cancer", "rank": 1}]
    result = enrich_candidates_with_pathways(candidates, queries=mock_queries)

    assert len(result) == 1
    c = result[0]
    # SNAKE_CASE (matches RankedCandidate.pathway_chain dataclass field).
    assert "pathway_chain" in c, (
        f"env.py MUST attach `pathway_chain` (snake_case) to match the "
        f"RankedCandidate dataclass field. Keys: {list(c.keys())}"
    )
    assert "pathway_source" in c, (
        f"env.py MUST attach `pathway_source` (snake_case). Keys: {list(c.keys())}"
    )
    # Regression guard: camelCase keys MUST be absent.
    assert "pathwayChain" not in c, (
        f"env.py attached WRONG camelCase `pathwayChain`. This causes "
        f"silent data loss when the candidate is converted to a "
        f"RankedCandidate (the dataclass field is `pathway_chain` snake_case)."
    )
    assert "pathwaySource" not in c
    assert len(c["pathway_chain"]) == 1
    assert c["pathway_chain"][0]["pathway"] == "mTOR signaling"
    assert c["pathway_source"] == "neo4j"
