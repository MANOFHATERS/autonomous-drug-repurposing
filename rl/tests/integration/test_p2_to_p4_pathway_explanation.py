"""TEAMMATE-7 v131 P2→P4 Integration test: pathway explanation end-to-end.

Verifies the FULL wiring chain mandated by the issue spec:
  1. ``enrich_candidates_with_pathways`` attaches a non-empty
     ``pathway_chain`` (LIST of pathway dicts) when Neo4j is available.
  2. ``enrich_candidates_with_pathways`` degrades gracefully when Neo4j
     is unavailable (pathway_chain=[], pathway_source='neo4j_unavailable').
  3. ``RankedCandidate.to_dict()`` includes ``pathway_chain`` so
     ``save_results`` writes it to the CSV.
  4. The RL service ``/rank`` response includes ``pathway_chain`` for
     each candidate AND a top-level ``pathway_enrichment_available`` flag.
  5. The bridge fallback (``get_pathway_explanation_from_bridge``) builds
     a pathway_chain from the Phase 1 staged edge lists when Neo4j is
     unavailable.

This file is the issue-spec's acceptance test. ALL tests MUST pass for
the P2→P4 integration to be considered "100% connected".
"""
import os
import sys
import json
import pytest
from unittest.mock import MagicMock, patch
from dataclasses import dataclass, field
from typing import Dict, List, Any, Tuple

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..', '..', '..')))

# Disable auth so the /rank endpoint can be called without org_id in tests.
os.environ.setdefault("RL_REQUIRE_AUTH", "false")


class FakeMechanisticPath:
    """Mimic the phase2 MechanisticPath dataclass shape."""
    def __init__(self, nodes, edges, total_score, num_hops):
        self.nodes = nodes
        self.edges = edges
        self.total_score = total_score
        self.num_hops = num_hops


@dataclass
class FakeBridge:
    """Mimic the Phase1StagedData .edges dict shape."""
    edges: Dict[Tuple[str, str, str], List[Dict[str, Any]]] = field(default_factory=dict)


def _make_fake_bridge_with_metformin_cancer_pathway():
    """Build a fake bridge with a metformin -> mTOR -> mTOR signaling -> cancer chain."""
    return FakeBridge(edges={
        ("Compound", "targets", "Protein"): [
            {"source": "metformin", "target": "mTOR"},
            {"source": "metformin", "target": "AMPK"},
        ],
        ("Protein", "part_of", "Pathway"): [
            {"source": "mTOR", "target": "mTOR signaling"},
            {"source": "AMPK", "target": "AMPK signaling"},
        ],
        ("Pathway", "disrupted_in", "Disease"): [
            {"source": "mTOR signaling", "target": "cancer"},
            {"source": "AMPK signaling", "target": "cancer"},
        ],
    })


def _make_mock_neo4j_queries():
    """Build a mock DrugOSGraphQueries that returns a metformin -> mTOR pathway."""
    mock_queries = MagicMock()
    mock_queries.get_mechanistic_pathway.return_value = [
        FakeMechanisticPath(
            nodes=[
                {"id": "DB00945", "type": "Compound", "name": "metformin"},
                {"id": "P35869", "type": "Protein", "name": "mTOR"},
                {"id": "PW00024", "type": "Pathway", "name": "mTOR signaling"},
                {"id": "D009369", "type": "Disease", "name": "cancer"},
            ],
            edges=[
                {"type": "inhibits", "confidence": 0.95},
                {"type": "upregulated_in", "confidence": 0.80},
            ],
            total_score=0.87,
            num_hops=2,
        ),
    ]
    return mock_queries


# =============================================================================
# ACCEPTANCE TEST 1: enrich_candidates_with_pathways adds pathway_chain
# =============================================================================

@pytest.mark.integration
def test_enrich_candidates_with_pathways_adds_pathway_chain():
    """Acceptance criterion #1: enrich_candidates_with_pathways attaches a
    non-empty pathway_chain (LIST of pathway dicts) when Neo4j is available."""
    from rl.env import enrich_candidates_with_pathways

    candidates = [
        {"drug": "metformin", "disease": "cancer", "score": 0.87},
        {"drug": "aspirin", "disease": "headache", "score": 0.92},
    ]
    mock_queries = _make_mock_neo4j_queries()
    result = enrich_candidates_with_pathways(candidates, queries=mock_queries)
    assert len(result) == 2
    # CONTRACT: pathway_chain is a LIST (not a wrapper dict).
    assert isinstance(result[0]["pathway_chain"], list)
    assert len(result[0]["pathway_chain"]) > 0
    assert result[0]["pathway_chain"][0]["pathway"] == "mTOR signaling"
    # CONTRACT: pathway_source is set.
    assert result[0]["pathway_source"] == "neo4j"


# =============================================================================
# ACCEPTANCE TEST 2: graceful degradation without Neo4j
# =============================================================================

@pytest.mark.integration
def test_enrich_candidates_degrades_gracefully_without_neo4j():
    """Acceptance criterion #4: when Neo4j is unavailable, pathway_chain is
    [] (not a crash) and pathway_source is 'neo4j_unavailable'."""
    from rl.env import enrich_candidates_with_pathways

    candidates = [{"drug": "metformin", "disease": "cancer", "score": 0.87}]
    with patch("rl.env.is_neo4j_available", return_value=False):
        result = enrich_candidates_with_pathways(candidates)
    assert len(result) == 1
    assert isinstance(result[0]["pathway_chain"], list)
    assert result[0]["pathway_chain"] == []
    assert result[0]["pathway_source"] == "neo4j_unavailable"


# =============================================================================
# ACCEPTANCE TEST 3: RankedCandidate.to_dict includes pathway_chain
# =============================================================================

@pytest.mark.integration
def test_ranked_candidate_includes_pathway_chain_in_dict():
    """Acceptance criterion #3 (CSV write side): RankedCandidate.to_dict()
    includes pathway_chain (JSON-serialized) so save_results writes it to
    the CSV."""
    from rl.rl_drug_ranker import RankedCandidate

    candidate = RankedCandidate(
        drug="metformin", disease="cancer", reward=0.87,
        pathway_chain=[
            {"pathway": "mTOR signaling", "intermediate_protein": "mTOR",
             "chain": ["metformin", "mTOR", "mTOR signaling", "cancer"]},
        ],
        pathway_source="neo4j",
    )
    d = candidate.to_dict()
    assert "pathway_chain" in d
    assert "pathway_source" in d
    parsed = json.loads(d["pathway_chain"])
    assert len(parsed) == 1
    assert parsed[0]["pathway"] == "mTOR signaling"


# =============================================================================
# ACCEPTANCE TEST 4: /rank response includes pathway_chain + flag
# =============================================================================

@pytest.mark.integration
def test_rl_service_rank_response_includes_pathway_chain():
    """Acceptance criterion #3 (API side): the RL service /rank response
    includes pathway_chain for each candidate AND a top-level
    pathway_enrichment_available flag."""
    from fastapi.testclient import TestClient
    from rl.service import app
    from rl.rl_drug_ranker import RankedCandidate

    client = TestClient(app)
    mock_candidate_dict = {
        "drug": "metformin", "disease": "cancer", "rank": 1, "reward": 0.87,
        "policyProb": 0.85, "gnnScore": 0.92, "safetyScore": 0.95,
        "marketScore": 0.65, "plausibilityScore": 0.92, "overallScore": 0.85,
        "confidence": 0.85, "pathwayScore": 0.78, "unmetNeedScore": 0.6,
        "efficacyScore": 0.7, "admeScore": 0.8, "literatureSupport": 1,
        "isKnownPositive": False,
        # TEAMMATE-7 v140 ROOT FIX: snake_case to match the frontend Zod
        # schema (frontend/src/lib/ml-contracts.ts:198). The previous
        # test used camelCase `pathwayChain` which matched the BUGGY
        # implementation but did NOT match the frontend's contract --
        # the frontend Zod schema's `.default([])` silently dropped the
        # camelCase field, so the candidate table rendered "0 pathways"
        # even when the CSV had real pathway data. This test now asserts
        # on the SPEC field name, not the buggy implementation's field name.
        "pathway_chain": [
            {"pathway": "mTOR signaling", "intermediate_protein": "mTOR",
             "chain": ["metformin", "mTOR", "mTOR signaling", "cancer"]},
        ],
        "pathway_source": "neo4j",
    }
    mock_loaded = {"candidates": [mock_candidate_dict], "total": 1}
    fake_csv_path = MagicMock()
    fake_csv_path.exists.return_value = True
    fake_csv_path.with_suffix.return_value = MagicMock(exists=lambda: False)
    fake_csv_path.parent = MagicMock(iterdir=lambda: [])

    with patch("rl.service._load_candidates_from_csv", return_value=mock_loaded):
        with patch("rl.service._find_latest_output_csv", return_value=fake_csv_path):
            with patch("rl.service._load_org_private_drugs", return_value={}):
                response = client.post("/rank", json={"drug": "metformin", "disease": "cancer", "limit": 5})
    assert response.status_code == 200
    data = response.json()
    assert "pathway_enrichment_available" in data
    assert data["pathway_enrichment_available"] is True
    # TEAMMATE-7 v140: assert on snake_case `pathway_chain` (matches frontend Zod schema).
    assert "pathway_chain" in data["candidates"][0]
    assert len(data["candidates"][0]["pathway_chain"]) == 1
    assert data["candidates"][0]["pathway_chain"][0]["pathway"] == "mTOR signaling"
    assert data["candidates"][0]["pathway_source"] == "neo4j"


# =============================================================================
# ACCEPTANCE TEST 5: bridge fallback when Neo4j is unavailable
# =============================================================================

@pytest.mark.integration
def test_bridge_fallback_produces_pathway_chain_when_neo4j_down():
    """Acceptance criterion #5: when Neo4j is unavailable, the bridge
    fallback builds a pathway_chain from the Phase 1 staged edge lists
    (drug -> protein -> pathway -> disease). source='bridge'."""
    from rl.env import get_pathway_explanation_from_bridge, enrich_candidates_with_pathways

    bridge = _make_fake_bridge_with_metformin_cancer_pathway()
    # Direct call
    result = get_pathway_explanation_from_bridge("metformin", "cancer", bridge)
    assert result["available"] is True
    assert result["source"] == "bridge"
    assert len(result["pathway_chain"]) == 2  # mTOR + AMPK both connect to cancer

    # Via enrich_candidates_with_pathways
    candidates = [{"drug": "metformin", "disease": "cancer", "rank": 1}]
    with patch("rl.env.is_neo4j_available", return_value=False):
        enriched = enrich_candidates_with_pathways(candidates, bridge=bridge)
    assert enriched[0]["pathway_source"] == "bridge"
    assert len(enriched[0]["pathway_chain"]) == 2


# =============================================================================
# ACCEPTANCE TEST 6: full CSV round-trip — write with pathway_chain, read back
# =============================================================================

@pytest.mark.integration
def test_csv_round_trip_preserves_pathway_chain(tmp_path):
    """End-to-end: RankedCandidate.to_dict() → CSV → _load_candidates_from_csv
    MUST preserve the pathway_chain (no data loss across the serialization
    boundary)."""
    import pandas as pd
    from pathlib import Path
    from rl.rl_drug_ranker import RankedCandidate
    from rl.service import _load_candidates_from_csv

    rc = RankedCandidate(
        drug="metformin", disease="cancer", reward=0.87,
        features={"gnn_score": 0.92, "safety_score": 0.95},
        rank=1,
        pathway_chain=[
            {"pathway": "mTOR signaling", "intermediate_protein": "mTOR",
             "chain": ["metformin", "mTOR", "mTOR signaling", "cancer"]},
            {"pathway": "AMPK signaling", "intermediate_protein": "AMPK",
             "chain": ["metformin", "AMPK", "AMPK signaling", "cancer"]},
        ],
        pathway_source="neo4j",
    )
    d = rc.to_dict()
    csv_path = tmp_path / "top_candidates_test.csv"
    df = pd.DataFrame([d])
    df.to_csv(csv_path, index=False)

    loaded = _load_candidates_from_csv(Path(csv_path), None, None, limit=0)
    assert loaded["total"] == 1
    candidate = loaded["candidates"][0]
    assert candidate["drug"] == "metformin"
    # TEAMMATE-7 v140 ROOT FIX: assert on snake_case `pathway_chain` (matches
    # the frontend Zod schema). The previous test asserted on camelCase
    # `pathwayChain` which matched the BUGGY implementation.
    assert isinstance(candidate["pathway_chain"], list)
    assert len(candidate["pathway_chain"]) == 2
    assert candidate["pathway_chain"][0]["pathway"] == "mTOR signaling"
    assert candidate["pathway_chain"][1]["pathway"] == "AMPK signaling"
    assert candidate["pathway_source"] == "neo4j"


# =============================================================================
# ACCEPTANCE TEST 7: pathway_source metadata is recorded
# =============================================================================

@pytest.mark.integration
def test_enrich_records_pathway_source_per_candidate():
    """Each candidate MUST have a pathway_source field (one of
    'neo4j' | 'bridge' | 'neo4j_unavailable') so the pipeline metadata
    can record which source was used."""
    from rl.env import enrich_candidates_with_pathways

    # Neo4j path
    mock_queries = _make_mock_neo4j_queries()
    candidates = [{"drug": "metformin", "disease": "cancer", "rank": 1}]
    result = enrich_candidates_with_pathways(candidates, queries=mock_queries)
    assert result[0]["pathway_source"] == "neo4j"

    # Bridge fallback path
    bridge = _make_fake_bridge_with_metformin_cancer_pathway()
    candidates2 = [{"drug": "metformin", "disease": "cancer", "rank": 1}]
    with patch("rl.env.is_neo4j_available", return_value=False):
        result2 = enrich_candidates_with_pathways(candidates2, bridge=bridge)
    assert result2[0]["pathway_source"] == "bridge"

    # Neo4j unavailable, no bridge
    candidates3 = [{"drug": "metformin", "disease": "cancer", "rank": 1}]
    with patch("rl.env.is_neo4j_available", return_value=False):
        result3 = enrich_candidates_with_pathways(candidates3)
    assert result3[0]["pathway_source"] == "neo4j_unavailable"
