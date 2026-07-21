"""
Phase 2 /kg/stats canonicalNodeCount tests (Teammate 8 ROOT FIX).

Verifies that the Phase 2 service's /kg/stats endpoint returns the
``canonicalNodeCount`` field — the count of CANONICAL-type nodes only
(Compound, Protein, Pathway, Disease, ClinicalOutcome). This is the
number the frontend's Knowledge Graph Explorer displays as
"canonical nodes" per the project docx Phase 2 contract.

The previous /kg/stats response had only ``nodeCount`` (total, includes
non-canonical types like Gene/MedDRA_Term/Anatomy) — the dashboard
could not tell researchers how many of the total nodes were
scientifically meaningful entities. This fix adds ``canonicalNodeCount``
so the dashboard can show BOTH numbers.

Run with:
    python -m pytest phase2/tests/integration/test_kg_stats_canonical_count.py -v
"""
from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import patch, MagicMock

import pytest

# Make the repo root importable.
_REPO_ROOT = Path(__file__).resolve().parents[3]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))


# ---------------------------------------------------------------------------
# Test 1: _compute_canonical_node_count returns the sum of canonical types
# ---------------------------------------------------------------------------
def test_compute_canonical_node_count_sums_canonical_types_only():
    """_compute_canonical_node_count should sum ONLY the canonical types
    (Compound, Protein, Pathway, Disease, ClinicalOutcome). Non-canonical
    types (Gene, MedDRA_Term, Anatomy, etc.) should be EXCLUDED.
    """
    from phase2.service import _compute_canonical_node_count, CANONICAL_NODE_TYPES

    # Verify the canonical type set uses the SINGULAR form
    # "ClinicalOutcome" (NOT the plural "ClinicalOutcomes").
    assert "ClinicalOutcome" in CANONICAL_NODE_TYPES, (
        "CANONICAL_NODE_TYPES must include 'ClinicalOutcome' (SINGULAR) "
        "to match the Phase 2 KG label vocabulary. The previous plural "
        "form 'ClinicalOutcomes' silently dropped all ClinicalOutcome "
        "nodes from the canonical count."
    )
    assert "ClinicalOutcomes" not in CANONICAL_NODE_TYPES, (
        "CANONICAL_NODE_TYPES must NOT include 'ClinicalOutcomes' (PLURAL). "
        "The Phase 2 KG label vocabulary uses the singular form."
    )

    node_types = {
        "Compound": 10000,        # canonical
        "Protein": 20000,         # canonical
        "Pathway": 5000,          # canonical
        "Disease": 8000,          # canonical
        "ClinicalOutcome": 2000,  # canonical (SINGULAR form)
        "Gene": 25000,            # NON-canonical
        "MedDRA_Term": 2000,      # NON-canonical
        "Anatomy": 3000,          # NON-canonical
    }
    # Total = 75000, canonical = 10000 + 20000 + 5000 + 8000 + 2000 = 45000
    result = _compute_canonical_node_count(node_types)
    assert result == 45000, (
        f"Expected canonical count 45000 (Compound+Protein+Pathway+Disease"
        f"+ClinicalOutcome only), got {result}"
    )


# ---------------------------------------------------------------------------
# Test 2: _compute_canonical_node_count returns 0 for empty input
# ---------------------------------------------------------------------------
def test_compute_canonical_node_count_handles_empty_input():
    """_compute_canonical_node_count should return 0 for empty or
    non-canonical-only input.
    """
    from phase2.service import _compute_canonical_node_count

    assert _compute_canonical_node_count({}) == 0
    assert _compute_canonical_node_count({"Gene": 25000, "Anatomy": 3000}) == 0


# ---------------------------------------------------------------------------
# Test 3: /kg/stats endpoint returns canonicalNodeCount in the response
# ---------------------------------------------------------------------------
def test_kg_stats_returns_canonical_node_count_via_test_client():
    """The /kg/stats endpoint should include ``canonicalNodeCount`` in
    the response — the count of CANONICAL-type nodes only.
    """
    from fastapi.testclient import TestClient
    from phase2.service import app, _compute_canonical_node_count

    # We need to mock the Neo4j + bridge paths so the endpoint returns
    # a deterministic response without requiring a real Neo4j or Phase 1
    # data. Mock _get_kg_stats_from_neo4j to return None (forces the
    # bridge path) and _get_kg_stats_from_builder to return a known
    # response shape with the canonicalNodeCount field.
    test_node_types = {
        "Compound": 10000,
        "Protein": 20000,
        "Gene": 25000,  # NON-canonical — must be excluded
    }
    expected_total = 55000  # 10000 + 20000 + 25000
    expected_canonical = 30000  # 10000 + 20000 only

    # Sanity check: verify _compute_canonical_node_count agrees.
    assert _compute_canonical_node_count(test_node_types) == expected_canonical

    # Build a mock bridge response that mirrors what
    # _get_kg_stats_from_builder would produce.
    from datetime import datetime, timezone
    _last_updated = datetime.now(timezone.utc).isoformat()
    mock_bridge_response = {
        "node_count": expected_total,
        "edge_count": 100000,
        "node_types": test_node_types,
        "edge_types": {"treats": 50000},
        "backend": "in_memory_bridge",
        "sources_read": ["chembl"],
        "node_type_counts": test_node_types,
        "edge_type_counts": {"treats": 50000},
        "last_updated": _last_updated,
        "source": "in_memory",
        "nodeCount": expected_total,
        "canonicalNodeCount": expected_canonical,
        "edgeCount": 100000,
        "nodeTypeCounts": test_node_types,
        "edgeTypeCounts": {"treats": 50000},
        "generatedAt": _last_updated,
        "sources": [{"name": "chembl", "loaded": True}],
    }

    with patch("phase2.service._get_kg_stats_from_neo4j", return_value=None), \
         patch(
             "phase2.service._get_kg_stats_from_builder",
             return_value=mock_bridge_response,
         ):
        with TestClient(app) as client:
            response = client.get("/kg/stats")

    assert response.status_code == 200, (
        f"Expected 200, got {response.status_code}: {response.text}"
    )
    data = response.json()
    assert data["nodeCount"] == expected_total, (
        f"nodeCount should be {expected_total} (total), got: {data.get('nodeCount')}"
    )
    assert data["canonicalNodeCount"] == expected_canonical, (
        f"canonicalNodeCount should be {expected_canonical} (canonical only), "
        f"got: {data.get('canonicalNodeCount')}. The /kg/stats response "
        f"MUST include canonicalNodeCount — the frontend's Knowledge "
        f"Graph Explorer displays this as 'canonical nodes'."
    )


# ---------------------------------------------------------------------------
# Test 4: /kg/stats canonicalNodeCount excludes Gene/MedDRA_Term/Anatomy
# ---------------------------------------------------------------------------
def test_kg_stats_canonical_count_excludes_non_canonical_types():
    """The canonicalNodeCount field should EXCLUDE non-canonical types
    like Gene, MedDRA_Term, and Anatomy. Only Compound, Protein,
    Pathway, Disease, and ClinicalOutcome (SINGULAR) count.
    """
    from phase2.service import _compute_canonical_node_count, CANONICAL_NODE_TYPES

    # Verify the canonical set is exactly the 5 project docx types.
    expected = {"Compound", "Protein", "Pathway", "Disease", "ClinicalOutcome"}
    assert CANONICAL_NODE_TYPES == expected, (
        f"CANONICAL_NODE_TYPES should be exactly {expected}, got: "
        f"{CANONICAL_NODE_TYPES}"
    )

    # A graph with ONLY non-canonical types should have canonical count = 0.
    non_canonical_only = {"Gene": 100, "MedDRA_Term": 200, "Anatomy": 300}
    assert _compute_canonical_node_count(non_canonical_only) == 0

    # A graph with ONLY canonical types should have canonical count = total.
    canonical_only = {"Compound": 100, "Protein": 200, "Pathway": 300,
                      "Disease": 400, "ClinicalOutcome": 500}
    total = sum(canonical_only.values())
    assert _compute_canonical_node_count(canonical_only) == total

    # A mixed graph should have canonical count = sum of canonical only.
    mixed = {**canonical_only, **non_canonical_only}
    assert _compute_canonical_node_count(mixed) == total


# ---------------------------------------------------------------------------
# Test 5: CANONICAL_NODE_TYPES module-level constant is exported
# ---------------------------------------------------------------------------
def test_canonical_node_types_is_module_level_constant():
    """CANONICAL_NODE_TYPES should be a module-level constant importable
    from phase2.service. This lets the backend FastAPI proxy and the
    frontend's ml-contracts.ts share the same canonical type set
    (single source of truth).
    """
    from phase2.service import CANONICAL_NODE_TYPES
    assert isinstance(CANONICAL_NODE_TYPES, set)
    assert len(CANONICAL_NODE_TYPES) == 5
    # The set must include the SINGULAR form (not "ClinicalOutcomes").
    assert "ClinicalOutcome" in CANONICAL_NODE_TYPES
    assert "ClinicalOutcomes" not in CANONICAL_NODE_TYPES


if __name__ == "__main__":
    pytest.main([__file__, "-v", "--tb=short"])
