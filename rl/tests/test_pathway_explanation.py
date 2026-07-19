"""TASK 8.3 verification: RL env can query Neo4j for pathway explanations.

The task spec requires:
  (1) call graph_queries.get_pathway_explanation(drug_id, disease_id)
  (2) include pathway chain in the ranking output
  (3) verify the pathway is non-empty for top-K candidates

Since Neo4j is not running in the test environment, we verify:
  - The function `get_pathway_explanation` exists and is callable
  - It calls `DrugOSGraphQueries.get_mechanistic_pathway` when Neo4j IS
    available (verified by dependency-injecting a mock queries object)
  - It degrades gracefully (returns empty pathway, available=False)
    when Neo4j is unavailable
  - `enrich_candidates_with_pathways` attaches a `pathway_chain` field
    to each candidate
  - When Neo4j IS available (mocked), the pathway_chain is non-empty
"""
import os
import sys
import pytest
from unittest.mock import MagicMock, patch

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..', '..')))

from rl.env import (
    get_pathway_explanation,
    enrich_candidates_with_pathways,
    is_neo4j_available,
    DrugRankingEnv,
)


class FakeMechanisticPath:
    """Mimic the phase2 MechanisticPath dataclass shape."""
    def __init__(self, nodes, edges, total_score, num_hops):
        self.nodes = nodes
        self.edges = edges
        self.total_score = total_score
        self.num_hops = num_hops


def test_get_pathway_explanation_exists_and_callable():
    """(1) get_pathway_explanation exists and is callable."""
    assert callable(get_pathway_explanation), "get_pathway_explanation is not callable"


def test_get_pathway_explanation_calls_graph_queries_method():
    """(1) get_pathway_explanation calls DrugOSGraphQueries.get_mechanistic_pathway."""
    fake_queries = MagicMock()
    fake_queries.get_mechanistic_pathway.return_value = [
        FakeMechanisticPath(
            nodes=[{"id": "DB00945", "type": "Compound", "name": "aspirin"},
                   {"id": "P23219", "type": "Protein", "name": "COX-1"},
                   {"id": "D013577", "type": "Disease", "name": "syndrome X"}],
            edges=[{"type": "inhibits", "confidence": 0.95},
                   {"type": "disrupted_in", "confidence": 0.80}],
            total_score=0.87,
            num_hops=2,
        ),
    ]
    result = get_pathway_explanation(
        drug_id="DB00945",
        disease_id="D013577",
        max_depth=4,
        queries=fake_queries,
    )
    # The mock must have been called once.
    fake_queries.get_mechanistic_pathway.assert_called_once_with(
        drug_id="DB00945", disease_id="D013577", max_depth=4,
    )
    # Result must contain the pathway chain.
    assert result["available"] is True
    assert len(result["pathways"]) == 1
    pathway = result["pathways"][0]
    assert len(pathway["nodes"]) == 3
    assert len(pathway["edges"]) == 2
    assert pathway["total_score"] == pytest.approx(0.87, abs=0.01)
    assert pathway["num_hops"] == 2


def test_get_pathway_explanation_degrades_when_neo4j_unavailable():
    """(2) When Neo4j is unavailable, returns available=False with empty pathways."""
    # We don't have Neo4j running in CI. Patch is_neo4j_available + the
    # DrugOSGraphQueries constructor to raise.
    with patch("rl.env.is_neo4j_available", return_value=False):
        result = get_pathway_explanation(
            drug_id="DB00945", disease_id="D013577",
        )
    assert result["available"] is False
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


def test_enrich_candidates_with_pathways_attaches_pathway_chain():
    """(2) enrich_candidates_with_pathways attaches pathway_chain to each candidate."""
    fake_queries = MagicMock()
    fake_queries.get_mechanistic_pathway.return_value = [
        FakeMechanisticPath(
            nodes=[{"id": "DB00945", "type": "Compound"},
                   {"id": "D013577", "type": "Disease"}],
            edges=[{"type": "treats", "confidence": 0.9}],
            total_score=0.9,
            num_hops=1,
        ),
    ]
    # Use plain dicts as candidates (the function supports both dicts and
    # RankedCandidate objects).
    candidates = [
        {"drug": "DB00945", "disease": "D013577", "rank": 1},
        {"drug": "DB00946", "disease": "D013578", "rank": 2},
    ]
    # Patch is_neo4j_available to skip the pre-check (so the function
    # uses our injected fake_queries).
    with patch("rl.env.is_neo4j_available", return_value=True):
        result = enrich_candidates_with_pathways(
            candidates, max_depth=4, queries=fake_queries,
        )
    assert len(result) == 2
    # Both candidates must have a pathway_chain field.
    assert "pathway_chain" in result[0]
    assert "pathway_chain" in result[1]
    # The pathway_chain must be non-empty (the mock returned a path).
    assert result[0]["pathway_chain"]["available"] is True
    assert len(result[0]["pathway_chain"]["pathways"]) == 1
    assert len(result[0]["pathway_chain"]["pathways"][0]["nodes"]) == 2


def test_enrich_candidates_with_pathways_degrades_when_neo4j_down():
    """When Neo4j is down, candidates still get a pathway_chain (empty)."""
    candidates = [
        {"drug": "DB00945", "disease": "D013577", "rank": 1},
    ]
    with patch("rl.env.is_neo4j_available", return_value=False):
        result = enrich_candidates_with_pathways(candidates)
    assert len(result) == 1
    assert "pathway_chain" in result[0]
    assert result[0]["pathway_chain"]["available"] is False
    assert result[0]["pathway_chain"]["pathways"] == []


def test_enrich_candidates_with_pathways_handles_empty_list():
    """Empty candidate list returns empty list (no crash)."""
    result = enrich_candidates_with_pathways([])
    assert result == []


def test_enrich_candidates_with_pathways_handles_ranked_candidate_objects():
    """RankedCandidate objects (not dicts) get a pathway_chain attribute."""
    # Build a real RankedCandidate via the env.
    from rl.rl_drug_ranker import (
        RankedCandidate, generate_fake_data, PipelineConfig,
    )
    cfg = PipelineConfig()
    train_data = generate_fake_data(n_pairs=20, seed=42)
    env = DrugRankingEnv(data=train_data, config=cfg)
    # Reset env to get a fresh observation.
    obs, info = env.reset()
    # Build a fake RankedCandidate manually (avoids running the policy).
    rc = RankedCandidate(
        drug="aspirin", disease="fever", reward=0.5, policy_prob=0.8,
        rank=1, is_known_positive=False, features={},
    )
    fake_queries = MagicMock()
    fake_queries.get_mechanistic_pathway.return_value = [
        FakeMechanisticPath(
            nodes=[{"id": "DB00945"}, {"id": "D013577"}],
            edges=[{"type": "treats", "confidence": 0.9}],
            total_score=0.9, num_hops=1,
        ),
    ]
    with patch("rl.env.is_neo4j_available", return_value=True):
        result = enrich_candidates_with_pathways(
            [rc], max_depth=4, queries=fake_queries,
        )
    assert len(result) == 1
    # The RankedCandidate object should now have a pathway_chain attribute.
    assert hasattr(result[0], "pathway_chain")
    assert result[0].pathway_chain["available"] is True
    assert len(result[0].pathway_chain["pathways"]) == 1


def test_is_neo4j_available_returns_bool():
    """is_neo4j_available returns a bool (not raises)."""
    result = is_neo4j_available()
    assert isinstance(result, bool)
