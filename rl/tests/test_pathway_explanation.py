"""TEAMMATE-7 v131 verification: RL env pathway explanation wiring.

HOSTILE-AUDITOR REWRITE (v131):
  The previous version of this file (test_pathway_explanation.py) asserted
  on the WRONG contract — it expected
  ``result[0]["pathway_chain"]["available"]`` (a wrapper dict with an
  ``available`` key). That contract was NEVER what the frontend's
  PathwayChain.tsx consumed, NEVER what the issue-spec API contract
  specified, and NEVER what a pharma partner could use. The tests PASSED
  while the integration was UNUSABLE — the exact "comments are fakes"
  pattern the user warned about.

  This rewrite enforces the CORRECT contract:
    - ``candidate["pathway_chain"]`` is a LIST of pathway dicts.
    - Each pathway dict has ``pathway`` (str), ``intermediate_protein`` (str),
      ``chain`` (list of str), ``nodes`` (list of dicts),
      ``edges`` (list of dicts), ``total_score`` (float), ``num_hops`` (int).
    - ``candidate["pathway_source"]`` is one of
      ``"neo4j" | "bridge" | "neo4j_unavailable"``.

  The tests verify EXECUTABLE BEHAVIOR (real function calls with mock
  queries / mock bridge), NOT comments or docstrings.
"""
import os
import sys
import json
import pytest
from unittest.mock import MagicMock, patch
from dataclasses import dataclass, field
from typing import Dict, List, Any, Tuple

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..', '..')))

from rl.env import (
    get_pathway_explanation,
    enrich_candidates_with_pathways,
    is_neo4j_available,
    get_pathway_explanation_from_bridge,
    _mechanistic_path_to_frontend_dict,
    DrugRankingEnv,
)


class FakeMechanisticPath:
    """Mimic the phase2 MechanisticPath dataclass shape."""
    def __init__(self, nodes, edges, total_score, num_hops):
        self.nodes = nodes
        self.edges = edges
        self.total_score = total_score
        self.num_hops = num_hops


# =============================================================================
# SECTION 1: _mechanistic_path_to_frontend_dict converter
# =============================================================================

def test_converter_extracts_pathway_and_protein_by_node_type():
    """The converter MUST extract pathway name + protein name from the
    MechanisticPath.nodes list by matching node.type (case-insensitive)."""
    fake_path = FakeMechanisticPath(
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
    )
    result = _mechanistic_path_to_frontend_dict(fake_path)
    assert result["pathway"] == "mTOR signaling"
    assert result["intermediate_protein"] == "mTOR"
    assert result["chain"] == ["metformin", "mTOR", "mTOR signaling", "cancer"]
    assert len(result["nodes"]) == 4
    assert len(result["edges"]) == 2
    assert result["total_score"] == pytest.approx(0.87, abs=0.01)
    assert result["num_hops"] == 2


def test_converter_handles_missing_pathway_node():
    """When no Pathway node exists, pathway="" (not a crash)."""
    fake_path = FakeMechanisticPath(
        nodes=[
            {"id": "DB00945", "type": "Compound", "name": "aspirin"},
            {"id": "D013577", "type": "Disease", "name": "headache"},
        ],
        edges=[{"type": "treats", "confidence": 0.9}],
        total_score=0.9,
        num_hops=1,
    )
    result = _mechanistic_path_to_frontend_dict(fake_path)
    assert result["pathway"] == ""
    assert result["intermediate_protein"] == ""
    assert result["chain"] == ["aspirin", "headache"]


def test_converter_accepts_dict_input():
    """The converter MUST accept a dict (not just a dataclass) so it can
    be reused for the bridge fallback path."""
    result = _mechanistic_path_to_frontend_dict({
        "nodes": [
            {"id": "metformin", "type": "Compound", "name": "metformin"},
            {"id": "mTOR", "type": "Protein", "name": "mTOR"},
            {"id": "mTOR_sig", "type": "Pathway", "name": "mTOR signaling"},
            {"id": "cancer", "type": "Disease", "name": "cancer"},
        ],
        "edges": [],
        "total_score": 0.0,
        "num_hops": 3,
    })
    assert result["pathway"] == "mTOR signaling"
    assert result["intermediate_protein"] == "mTOR"


# =============================================================================
# SECTION 2: get_pathway_explanation — Neo4j path
# =============================================================================

def test_get_pathway_explanation_exists_and_callable():
    """get_pathway_explanation exists and is callable."""
    assert callable(get_pathway_explanation)


def test_get_pathway_explanation_returns_pathway_chain_as_list():
    """CRITICAL: get_pathway_explanation MUST return pathway_chain as a
    LIST of pathway dicts (NOT a wrapper dict). This is the contract
    the issue spec + frontend PathwayChain.tsx require."""
    fake_queries = MagicMock()
    fake_queries.get_mechanistic_pathway.return_value = [
        FakeMechanisticPath(
            nodes=[
                {"id": "DB00945", "type": "Compound", "name": "aspirin"},
                {"id": "P23219", "type": "Protein", "name": "COX-1"},
                {"id": "PW00099", "type": "Pathway", "name": "Inflammation"},
                {"id": "D013577", "type": "Disease", "name": "syndrome X"},
            ],
            edges=[
                {"type": "inhibits", "confidence": 0.95},
                {"type": "disrupted_in", "confidence": 0.80},
            ],
            total_score=0.87,
            num_hops=2,
        ),
    ]
    result = get_pathway_explanation(
        drug_id="DB00945", disease_id="D013577",
        max_depth=4, queries=fake_queries,
    )
    fake_queries.get_mechanistic_pathway.assert_called_once_with(
        drug_id="DB00945", disease_id="D013577", max_depth=4,
    )
    assert result["available"] is True
    assert result["source"] == "neo4j"
    # CONTRACT: pathway_chain is a LIST (not a wrapper dict).
    assert isinstance(result["pathway_chain"], list), (
        f"pathway_chain must be a list, got {type(result['pathway_chain']).__name__}"
    )
    assert len(result["pathway_chain"]) == 1
    pathway = result["pathway_chain"][0]
    assert pathway["pathway"] == "Inflammation"
    assert pathway["intermediate_protein"] == "COX-1"
    assert pathway["chain"] == ["aspirin", "COX-1", "Inflammation", "syndrome X"]
    assert len(pathway["nodes"]) == 4
    assert len(pathway["edges"]) == 2


def test_get_pathway_explanation_degrades_when_neo4j_unavailable():
    """When Neo4j is unavailable, returns available=False with empty pathway_chain."""
    with patch("rl.env.is_neo4j_available", return_value=False):
        result = get_pathway_explanation(
            drug_id="DB00945", disease_id="D013577",
        )
    assert result["available"] is False
    assert result["source"] == "neo4j_unavailable"
    assert result["pathway_chain"] == []
    assert result["pathways"] == []
    assert "error" in result and result["error"]


def test_get_pathway_explanation_validates_inputs():
    """Empty drug_id or disease_id returns available=False with clear error."""
    result = get_pathway_explanation(drug_id="", disease_id="D013577")
    assert result["available"] is False
    assert "drug_id" in result["error"]

    result = get_pathway_explanation(drug_id="DB00945", disease_id="")
    assert result["available"] is False
    assert "disease_id" in result["error"]


# =============================================================================
# SECTION 3: enrich_candidates_with_pathways — Neo4j path
# =============================================================================

def test_enrich_candidates_attaches_pathway_chain_as_list():
    """CRITICAL: enrich_candidates_with_pathways MUST attach pathway_chain
    as a LIST of pathway dicts (NOT a wrapper dict). The previous version
    attached a wrapper dict — the tests passed but the contract was unusable."""
    fake_queries = MagicMock()
    fake_queries.get_mechanistic_pathway.return_value = [
        FakeMechanisticPath(
            nodes=[
                {"id": "DB00945", "type": "Compound", "name": "DB00945"},
                {"id": "D013577", "type": "Disease", "name": "D013577"},
            ],
            edges=[{"type": "treats", "confidence": 0.9}],
            total_score=0.9,
            num_hops=1,
        ),
    ]
    candidates = [
        {"drug": "DB00945", "disease": "D013577", "rank": 1},
        {"drug": "DB00946", "disease": "D013578", "rank": 2},
    ]
    with patch("rl.env.is_neo4j_available", return_value=True):
        result = enrich_candidates_with_pathways(
            candidates, max_depth=4, queries=fake_queries,
        )
    assert len(result) == 2
    # CONTRACT: pathway_chain is a LIST (not a wrapper dict).
    assert isinstance(result[0]["pathway_chain"], list), (
        f"pathway_chain must be a list, got {type(result[0]['pathway_chain']).__name__}"
    )
    assert len(result[0]["pathway_chain"]) == 1
    # CONTRACT: pathway_source is a string, one of the allowed values.
    assert result[0]["pathway_source"] == "neo4j"
    assert result[1]["pathway_source"] == "neo4j"


def test_enrich_candidates_degrades_when_neo4j_down():
    """When Neo4j is down AND no bridge is provided, candidates get
    pathway_chain=[] and pathway_source='neo4j_unavailable' (not a crash)."""
    candidates = [
        {"drug": "DB00945", "disease": "D013577", "rank": 1},
    ]
    with patch("rl.env.is_neo4j_available", return_value=False):
        result = enrich_candidates_with_pathways(candidates)
    assert len(result) == 1
    assert isinstance(result[0]["pathway_chain"], list)
    assert result[0]["pathway_chain"] == []
    assert result[0]["pathway_source"] == "neo4j_unavailable"


def test_enrich_candidates_handles_empty_list():
    """Empty candidate list returns empty list (no crash)."""
    result = enrich_candidates_with_pathways([])
    assert result == []


def test_enrich_candidates_handles_ranked_candidate_objects():
    """RankedCandidate objects (not dicts) get pathway_chain + pathway_source
    attributes (not dict keys)."""
    from rl.rl_drug_ranker import RankedCandidate
    rc = RankedCandidate(
        drug="aspirin", disease="fever", reward=0.5, policy_prob=0.8,
        rank=1, is_known_positive=False, features={},
    )
    fake_queries = MagicMock()
    fake_queries.get_mechanistic_pathway.return_value = [
        FakeMechanisticPath(
            nodes=[
                {"id": "DB00945", "type": "Compound", "name": "aspirin"},
                {"id": "P23219", "type": "Protein", "name": "COX-1"},
                {"id": "D013577", "type": "Disease", "name": "fever"},
            ],
            edges=[{"type": "treats", "confidence": 0.9}],
            total_score=0.9, num_hops=1,
        ),
    ]
    with patch("rl.env.is_neo4j_available", return_value=True):
        result = enrich_candidates_with_pathways(
            [rc], max_depth=4, queries=fake_queries,
        )
    assert len(result) == 1
    assert hasattr(result[0], "pathway_chain")
    # CONTRACT: pathway_chain is a LIST (not a wrapper dict).
    assert isinstance(result[0].pathway_chain, list)
    assert len(result[0].pathway_chain) == 1
    assert result[0].pathway_chain[0]["intermediate_protein"] == "COX-1"
    assert result[0].pathway_source == "neo4j"


def test_is_neo4j_available_returns_bool():
    """is_neo4j_available returns a bool (not raises)."""
    result = is_neo4j_available()
    assert isinstance(result, bool)


# =============================================================================
# SECTION 4: get_pathway_explanation_from_bridge — bridge fallback path
# =============================================================================

@dataclass
class FakeBridge:
    """Mimic the Phase1StagedData .edges dict shape."""
    edges: Dict[Tuple[str, str, str], List[Dict[str, Any]]] = field(default_factory=dict)


def _make_fake_bridge():
    return FakeBridge(edges={
        ("Compound", "targets", "Protein"): [
            {"source": "metformin", "target": "mTOR"},
            {"source": "metformin", "target": "AMPK"},
            {"source": "aspirin", "target": "COX-1"},
        ],
        ("Protein", "part_of", "Pathway"): [
            {"source": "mTOR", "target": "mTOR signaling"},
            {"source": "AMPK", "target": "AMPK signaling"},
            {"source": "COX-1", "target": "Inflammation"},
        ],
        ("Pathway", "disrupted_in", "Disease"): [
            {"source": "mTOR signaling", "target": "cancer"},
            {"source": "AMPK signaling", "target": "cancer"},
            {"source": "Inflammation", "target": "headache"},
        ],
    })


def test_bridge_fallback_finds_pathway_chain():
    """When Neo4j is unavailable, the bridge fallback builds a pathway_chain
    from the staged edge lists (drug -> protein -> pathway -> disease)."""
    bridge = _make_fake_bridge()
    result = get_pathway_explanation_from_bridge("metformin", "cancer", bridge)
    assert result["available"] is True
    assert result["source"] == "bridge"
    assert isinstance(result["pathway_chain"], list)
    assert len(result["pathway_chain"]) == 2  # mTOR signaling AND AMPK signaling
    pathways = {p["pathway"] for p in result["pathway_chain"]}
    assert pathways == {"mTOR signaling", "AMPK signaling"}
    # Verify chain shape
    for p in result["pathway_chain"]:
        assert p["chain"] == ["metformin", p["intermediate_protein"], p["pathway"], "cancer"]
        assert len(p["nodes"]) == 4


def test_bridge_fallback_respects_max_pathways():
    """max_pathways cap is respected (default 5 per issue spec)."""
    bridge = _make_fake_bridge()
    result = get_pathway_explanation_from_bridge("metformin", "cancer", bridge, max_pathways=1)
    assert len(result["pathway_chain"]) == 1


def test_bridge_fallback_returns_empty_when_no_path():
    """When no drug -> protein -> pathway -> disease chain exists, returns
    available=False with empty pathway_chain (not a crash)."""
    bridge = _make_fake_bridge()
    result = get_pathway_explanation_from_bridge("unknown_drug", "cancer", bridge)
    assert result["available"] is False
    assert result["pathway_chain"] == []


def test_bridge_fallback_handles_empty_bridge():
    """A bridge with no edges returns available=False (not a crash)."""
    bridge = FakeBridge(edges={})
    result = get_pathway_explanation_from_bridge("metformin", "cancer", bridge)
    assert result["available"] is False
    assert result["pathway_chain"] == []


def test_enrich_candidates_uses_bridge_fallback_when_neo4j_down():
    """When Neo4j is down AND a bridge is provided, the enrich function
    uses the bridge fallback (source='bridge', not 'neo4j_unavailable')."""
    bridge = _make_fake_bridge()
    candidates = [
        {"drug": "metformin", "disease": "cancer", "rank": 1},
        {"drug": "aspirin", "disease": "headache", "rank": 2},
    ]
    with patch("rl.env.is_neo4j_available", return_value=False):
        result = enrich_candidates_with_pathways(candidates, bridge=bridge)
    assert len(result) == 2
    assert result[0]["pathway_source"] == "bridge"
    assert len(result[0]["pathway_chain"]) == 2  # mTOR + AMPK
    assert result[1]["pathway_source"] == "bridge"
    assert len(result[1]["pathway_chain"]) == 1  # COX-1 -> Inflammation -> headache
    assert result[1]["pathway_chain"][0]["pathway"] == "Inflammation"


# =============================================================================
# SECTION 5: RankedCandidate integration
# =============================================================================

def test_ranked_candidate_has_pathway_chain_field():
    """RankedCandidate dataclass MUST have pathway_chain + pathway_source
    fields (default empty list / empty string)."""
    from rl.rl_drug_ranker import RankedCandidate
    rc = RankedCandidate(drug="m", disease="c", reward=0.5)
    assert hasattr(rc, "pathway_chain")
    assert rc.pathway_chain == []
    assert hasattr(rc, "pathway_source")
    assert rc.pathway_source == ""


def test_ranked_candidate_to_dict_includes_pathway_chain():
    """RankedCandidate.to_dict() MUST include pathway_chain (JSON-serialized)
    and pathway_source so save_results writes them to the CSV."""
    from rl.rl_drug_ranker import RankedCandidate
    rc = RankedCandidate(
        drug="metformin", disease="cancer", reward=0.87,
        pathway_chain=[
            {"pathway": "mTOR signaling", "intermediate_protein": "mTOR",
             "chain": ["metformin", "mTOR", "mTOR signaling", "cancer"]},
        ],
        pathway_source="neo4j",
    )
    d = rc.to_dict()
    assert "pathway_chain" in d
    assert "pathway_source" in d
    assert d["pathway_source"] == "neo4j"
    # pathway_chain is JSON-serialized (a list cannot fit a CSV cell as-is).
    parsed = json.loads(d["pathway_chain"])
    assert isinstance(parsed, list)
    assert len(parsed) == 1
    assert parsed[0]["pathway"] == "mTOR signaling"


def test_ranked_candidate_to_dict_pathway_chain_round_trips_as_json():
    """The JSON-serialized pathway_chain MUST round-trip back to the
    original list shape (no data loss)."""
    from rl.rl_drug_ranker import RankedCandidate
    original_chain = [
        {"pathway": "mTOR signaling", "intermediate_protein": "mTOR",
         "chain": ["metformin", "mTOR", "mTOR signaling", "cancer"],
         "nodes": [{"id": "m", "type": "Compound", "name": "metformin"}],
         "edges": [{"type": "inhibits", "confidence": 0.95}],
         "total_score": 0.87, "num_hops": 2},
        {"pathway": "AMPK signaling", "intermediate_protein": "AMPK",
         "chain": ["metformin", "AMPK", "AMPK signaling", "cancer"],
         "nodes": [], "edges": [], "total_score": 0.0, "num_hops": 0},
    ]
    rc = RankedCandidate(
        drug="metformin", disease="cancer", reward=0.87,
        pathway_chain=original_chain, pathway_source="neo4j",
    )
    d = rc.to_dict()
    parsed = json.loads(d["pathway_chain"])
    assert parsed == original_chain
