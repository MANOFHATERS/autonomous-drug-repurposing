"""Task 7.5 — Neo4j writeback of GT predictions as PREDICTED_TREATS edges.

HOSTILE-AUDITOR TEST: verifies service.py ACTUALLY writes predictions
back to Neo4j (not just claims to in comments). The previous code only
returned predictions in the HTTP response -- no Neo4j MERGE.

This test:
  1. Reads the source code (hostile-auditor pattern).
  2. Exercises the runtime with Neo4j NOT configured (no-op path).
  3. Exercises the runtime with a MOCK Neo4j driver (verifies the
     Cypher MERGE query is actually executed).
"""
from __future__ import annotations

import inspect
import os
import sys
from unittest.mock import MagicMock, patch

_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
if _REPO_ROOT not in sys.path:
    sys.path.insert(0, _REPO_ROOT)


def test_service_imports_writeback_function():
    """Test 7.5.1: write_predictions_to_neo4j is importable from service."""
    os.environ.pop("GT_NEO4J_PASSWORD", None)
    os.environ.pop("DRUGOS_NEO4J_PASSWORD", None)
    os.environ.pop("NEO4J_PASSWORD", None)
    os.environ["GT_CHECKPOINT_PATH"] = ""
    from graph_transformer.service import write_predictions_to_neo4j
    assert callable(write_predictions_to_neo4j)


def test_service_has_predicted_treats_edge_type_constant():
    """Test 7.5.2: PREDICTED_TREATS_EDGE_TYPE constant exists."""
    from graph_transformer.service import PREDICTED_TREATS_EDGE_TYPE
    assert PREDICTED_TREATS_EDGE_TYPE == "PREDICTED_TREATS"


def test_service_has_predicted_treats_edge_properties():
    """Test 7.5.3: PREDICTED_TREATS_EDGE_PROPERTIES tuple exists with all required fields."""
    from graph_transformer.service import PREDICTED_TREATS_EDGE_PROPERTIES
    expected = {"score", "confidence", "model_version", "generated_at", "checkpoint_path"}
    assert set(PREDICTED_TREATS_EDGE_PROPERTIES) == expected, (
        f"PREDICTED_TREATS_EDGE_PROPERTIES mismatch. "
        f"Got: {set(PREDICTED_TREATS_EDGE_PROPERTIES)}, expected: {expected}"
    )


def test_service_source_contains_cypher_merge():
    """Test 7.5.4 (hostile-auditor): service.py source contains the MERGE Cypher query."""
    from graph_transformer.service import write_predictions_to_neo4j
    src = inspect.getsource(write_predictions_to_neo4j)
    # The MERGE pattern must be present (not just CREATE).
    assert "MERGE (d)-[r:PREDICTED_TREATS]->(dis)" in src, (
        "write_predictions_to_neo4j source does not contain the MERGE "
        "Cypher query for PREDICTED_TREATS edges."
    )
    # The edge properties must be set in the Cypher.
    assert "r.score = row.score" in src, "Cypher missing r.score = row.score"
    assert "r.confidence = row.confidence" in src, "Cypher missing r.confidence"
    assert "r.model_version = row.model_version" in src, "Cypher missing r.model_version"
    assert "r.generated_at = row.generated_at" in src, "Cypher missing r.generated_at"
    assert "r.checkpoint_path = row.checkpoint_path" in src, "Cypher missing r.checkpoint_path"
    # The query must use UNWIND for batch efficiency.
    assert "UNWIND $batch AS row" in src, (
        "Cypher missing UNWIND -- predictions should be batched for efficiency."
    )
    # The MERGE on Drug and Disease nodes (so writeback works even if
    # the node doesn't exist yet).
    assert "MERGE (d:Drug {name: row.drug})" in src, "Cypher missing MERGE on Drug node"
    assert "MERGE (dis:Disease {name: row.disease})" in src, "Cypher missing MERGE on Disease node"


def test_service_has_get_predictions_endpoint():
    """Test 7.5.5: GET /predictions endpoint exists for retrieving predictions from Neo4j."""
    from graph_transformer.service import app, get_predictions
    routes = [r.path for r in app.routes]
    assert "/predictions" in routes, (
        f"/predictions endpoint not found. Routes: {routes}"
    )
    assert callable(get_predictions)


def test_get_predictions_source_contains_cypher_match():
    """Test 7.5.6 (hostile-auditor): get_predictions source contains MATCH Cypher."""
    from graph_transformer.service import get_predictions
    src = inspect.getsource(get_predictions)
    assert "MATCH (d:Drug)-[r:PREDICTED_TREATS]->(dis:Disease)" in src, (
        "get_predictions source does not contain MATCH Cypher for PREDICTED_TREATS."
    )
    # Parameterized inputs (Cypher injection prevention).
    assert "$drug" in src, "Cypher missing $drug parameter"
    assert "$disease" in src, "Cypher missing $disease parameter"
    assert "$min_score" in src, "Cypher missing $min_score parameter"
    assert "$limit" in src, "Cypher missing $limit parameter"
    # toLower for case-insensitive matching.
    assert "toLower(d.name) = toLower($drug)" in src or "toLower(d.name) = toLower(" in src, (
        "Cypher missing toLower() for case-insensitive drug matching."
    )


def test_writeback_noop_when_neo4j_not_configured():
    """Test 7.5.7: writeback is a no-op when Neo4j is not configured
    (no NEO4J_PASSWORD env var). Returns neo4j_configured=False."""
    os.environ.pop("GT_NEO4J_PASSWORD", None)
    os.environ.pop("DRUGOS_NEO4J_PASSWORD", None)
    os.environ.pop("NEO4J_PASSWORD", None)
    from graph_transformer.service import write_predictions_to_neo4j
    result = write_predictions_to_neo4j([
        {"drug": "aspirin", "disease": "pain", "score": 0.9, "confidence": 0.8},
    ])
    assert result["neo4j_configured"] == False, (
        f"Expected neo4j_configured=False when no password set, got {result}"
    )
    assert result["written"] == 0
    assert result["failed"] == 0


def test_writeback_skips_error_entries():
    """Test 7.5.8: writeback skips predictions with 'note' field
    (error entries where drug/disease was not in the graph)."""
    os.environ.pop("GT_NEO4J_PASSWORD", None)
    os.environ.pop("DRUGOS_NEO4J_PASSWORD", None)
    os.environ.pop("NEO4J_PASSWORD", None)
    from graph_transformer.service import write_predictions_to_neo4j
    result = write_predictions_to_neo4j([
        {"drug": "aspirin", "disease": "pain", "score": 0.9, "confidence": 0.8},
        {"drug": "unknown", "disease": "unknown_d", "score": 0.0, "confidence": 0.0, "note": "not in graph"},
    ])
    assert result["skipped"] == 1, (
        f"Expected 1 skipped error entry, got {result['skipped']}"
    )


def test_writeback_empty_predictions():
    """Test 7.5.9: writeback handles empty prediction list gracefully."""
    from graph_transformer.service import write_predictions_to_neo4j
    result = write_predictions_to_neo4j([])
    assert result["written"] == 0
    assert result["skipped"] == 0
    assert result["failed"] == 0


def test_writeback_calls_neo4j_when_configured():
    """Test 7.5.10 (CRITICAL RUNTIME TEST): when Neo4j IS configured,
    the writeback actually calls driver.session().run() with the
    correct Cypher query.

    Uses a mock driver to verify the actual Cypher is executed.
    """
    # Set the env var so _get_neo4j_driver returns a driver.
    os.environ["GT_NEO4J_PASSWORD"] = "fake_password"
    try:
        # Mock GraphDatabase.driver to return a MagicMock.
        mock_driver = MagicMock()
        mock_session = MagicMock()
        mock_result = MagicMock()
        mock_driver.session.return_value.__enter__.return_value = mock_session
        mock_driver.session.return_value.__exit__.return_value = False
        mock_session.run.return_value = mock_result

        with patch("graph_transformer.service._get_neo4j_driver", return_value=mock_driver):
            from graph_transformer.service import write_predictions_to_neo4j
            result = write_predictions_to_neo4j([
                {"drug": "aspirin", "disease": "pain", "score": 0.9, "confidence": 0.8},
                {"drug": "ibuprofen", "disease": "inflammation", "score": 0.85, "confidence": 0.7},
            ], checkpoint_path="/tmp/test.pt", model_version="gt_v127")
        # Verify the writeback reported success.
        assert result["neo4j_configured"] == True, (
            f"Expected neo4j_configured=True, got {result}"
        )
        assert result["written"] == 2, f"Expected 2 written, got {result['written']}"
        assert result["failed"] == 0, f"Expected 0 failed, got {result['failed']}"
        # Verify session.run was called with the MERGE Cypher query.
        assert mock_session.run.called, "session.run was not called"
        call_args = mock_session.run.call_args
        cypher_query = call_args[0][0] if call_args[0] else ""
        assert "MERGE (d)-[r:PREDICTED_TREATS]->(dis)" in cypher_query, (
            f"Cypher query does not contain PREDICTED_TREATS MERGE. Query: {cypher_query[:200]}"
        )
        # Verify result.consume() was called (forces lazy evaluation).
        assert mock_result.consume.called, (
            "result.consume() was not called -- lazy evaluation could hide errors."
        )
        # Verify driver.close() was called (resource cleanup).
        assert mock_driver.close.called, "driver.close() was not called"
    finally:
        os.environ.pop("GT_NEO4J_PASSWORD", None)


def test_get_predictions_returns_no_neo4j_when_not_configured():
    """Test 7.5.11: GET /predictions returns source='no_neo4j' when
    Neo4j is not configured."""
    os.environ.pop("GT_NEO4J_PASSWORD", None)
    os.environ.pop("DRUGOS_NEO4J_PASSWORD", None)
    os.environ.pop("NEO4J_PASSWORD", None)
    from graph_transformer.service import get_predictions
    result = get_predictions(drug="aspirin", limit=10)
    assert result["source"] == "no_neo4j", (
        f"Expected source='no_neo4j', got {result['source']}"
    )
    assert result["neo4j_configured"] == False
    assert result["predictions"] == []


def test_get_predictions_validates_inputs():
    """Test 7.5.12: GET /predictions validates limit and min_score."""
    os.environ.pop("GT_NEO4J_PASSWORD", None)
    os.environ.pop("DRUGOS_NEO4J_PASSWORD", None)
    os.environ.pop("NEO4J_PASSWORD", None)
    from graph_transformer.service import get_predictions
    from fastapi import HTTPException
    # limit=0 should raise.
    try:
        get_predictions(limit=0)
        assert False, "Expected HTTPException for limit=0"
    except HTTPException as e:
        assert e.status_code == 400
    # limit=1000 should raise (max is 500).
    try:
        get_predictions(limit=1000)
        assert False, "Expected HTTPException for limit=1000"
    except HTTPException as e:
        assert e.status_code == 400
    # min_score=-0.1 should raise.
    try:
        get_predictions(min_score=-0.1)
        assert False, "Expected HTTPException for min_score=-0.1"
    except HTTPException as e:
        assert e.status_code == 400


def test_predict_endpoint_includes_neo4j_writeback_in_response():
    """Test 7.5.13 (hostile-auditor): the /predict endpoint's source
    code actually calls write_predictions_to_neo4j and includes the
    result in the response."""
    from graph_transformer.service import _predict_inner
    src = inspect.getsource(_predict_inner)
    assert "write_predictions_to_neo4j" in src, (
        "_predict_inner source does not call write_predictions_to_neo4j. "
        "The /predict endpoint does NOT write back to Neo4j -- the audit "
        "task 7.5 is NOT actually wired in."
    )
    assert "neo4j_writeback" in src, (
        "_predict_inner source does not include 'neo4j_writeback' in the "
        "response. The writeback result is not surfaced to the caller."
    )


if __name__ == "__main__":
    test_service_imports_writeback_function()
    print("Test 7.5.1 PASSED: write_predictions_to_neo4j importable")
    test_service_has_predicted_treats_edge_type_constant()
    print("Test 7.5.2 PASSED: PREDICTED_TREATS_EDGE_TYPE exists")
    test_service_has_predicted_treats_edge_properties()
    print("Test 7.5.3 PASSED: PREDICTED_TREATS_EDGE_PROPERTIES exists")
    test_service_source_contains_cypher_merge()
    print("Test 7.5.4 PASSED: source contains MERGE Cypher")
    test_service_has_get_predictions_endpoint()
    print("Test 7.5.5 PASSED: /predictions endpoint exists")
    test_get_predictions_source_contains_cypher_match()
    print("Test 7.5.6 PASSED: get_predictions contains MATCH Cypher")
    test_writeback_noop_when_neo4j_not_configured()
    print("Test 7.5.7 PASSED: no-op when Neo4j not configured")
    test_writeback_skips_error_entries()
    print("Test 7.5.8 PASSED: skips error entries")
    test_writeback_empty_predictions()
    print("Test 7.5.9 PASSED: handles empty predictions")
    test_writeback_calls_neo4j_when_configured()
    print("Test 7.5.10 PASSED: actually calls Neo4j when configured")
    test_get_predictions_returns_no_neo4j_when_not_configured()
    print("Test 7.5.11 PASSED: /predictions returns no_neo4j source")
    test_get_predictions_validates_inputs()
    print("Test 7.5.12 PASSED: /predictions validates inputs")
    test_predict_endpoint_includes_neo4j_writeback_in_response()
    print("Test 7.5.13 PASSED: /predict endpoint includes writeback in response")
    print("---ALL TASK 7.5 TESTS PASSED---")
