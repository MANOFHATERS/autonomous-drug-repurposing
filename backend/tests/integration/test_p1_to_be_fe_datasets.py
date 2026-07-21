"""
Integration tests for the backend FastAPI /datasets/* proxy routes.

TEAMMATE-4 ROOT FIX: these tests verify that the backend correctly
proxies to Phase 1 and enforces auth + org_id + 503 fallback.

Run with:
    cd <repo_root>
    python -m pytest backend/tests/integration/test_p1_to_be_fe_datasets.py -v
"""
from __future__ import annotations

import os
import sys
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest
from fastapi.testclient import TestClient


# Ensure the repo root is importable so `from backend.api.main import app`
# works regardless of the current working directory.
_REPO_ROOT = Path(__file__).resolve().parents[3]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

# Ensure JWT_SECRET is set BEFORE importing the app (the verify_jwt
# dependency checks it at import time).
os.environ.setdefault(
    "JWT_SECRET",
    "test-secret-do-not-use-in-production-32chars-minimum",
)
# Point PHASE1_SERVICE_URL at a dummy URL — the tests mock httpx.AsyncClient
# so no real request is made.
os.environ.setdefault("PHASE1_SERVICE_URL", "http://phase1-test:8001")


def _make_mock_response(status_code: int, json_data: dict) -> httpx.Response:
    """Build an httpx.Response with a request attached (needed for raise_for_status).

    The mock httpx.AsyncClient returns this response. The route handlers
    call response.raise_for_status() which requires response.request to
    be set (otherwise it raises RuntimeError, not the expected behavior).
    """
    request = httpx.Request("GET", "http://phase1-test:8001/stats")
    response = httpx.Response(
        status_code=status_code,
        json=json_data,
        request=request,
    )
    return response


def _make_mock_post_response(status_code: int, json_data: dict) -> httpx.Response:
    """Build an httpx.Response for a POST request."""
    request = httpx.Request("POST", "http://phase1-test:8001/datasets/validated_hypotheses")
    return httpx.Response(
        status_code=status_code,
        json=json_data,
        request=request,
    )


@pytest.fixture(scope="module")
def client():
    """TestClient for the FastAPI app, loaded once per module."""
    from backend.api.main import app
    with TestClient(app) as c:
        yield c


@pytest.fixture
def test_jwt():
    """Mint a valid JWT for testing."""
    from backend.api.main import create_test_jwt
    return create_test_jwt(user_id="testuser", org_id="testorg")


@pytest.fixture
def auth_headers(test_jwt):
    """Authorization headers with a valid test JWT."""
    return {"Authorization": f"Bearer {test_jwt}"}


# ===========================================================================
# TEST 1: /datasets/stats proxies to Phase 1 and returns the real response.
# ===========================================================================
@pytest.mark.integration
def test_backend_datasets_stats_proxies_to_phase1(client, auth_headers):
    """GET /datasets/stats proxies to Phase 1 /stats and returns the JSON."""
    mock_response = _make_mock_response(
        200,
        {
            "sources": [{"name": "drugbank", "loaded": True, "rowsLoaded": 1450}],
            "total_drugs": 1450,
            "total_proteins": 20340,
            "total_ppi": 8900,
            "nodesLoaded": 21790,
            "edgesLoaded": 8900,
            "compoundNodesLoaded": 1450,
            "proteinNodesLoaded": 20340,
            "edgeTypesPresent": [
                "Compound->Protein",
                "Compound->Disease",
                "Protein->Protein",
                "Gene->Disease",
            ],
            "schemaVersion": "20",
            "bridgeVersion": "1.0.0",
            "lastUpdated": "2026-07-21T09:15:32Z",
            "backend": "phase1_service",
            "warnings": [],
            "errors": [],
            "generatedAt": "2026-07-21T09:15:32Z",
        },
    )

    with patch("backend.api.main.httpx.AsyncClient") as mock_client_cls:
        mock_client = AsyncMock()
        mock_client.__aenter__.return_value = mock_client
        mock_client.get.return_value = mock_response
        mock_client_cls.return_value = mock_client

        response = client.get("/datasets/stats", headers=auth_headers)

    assert response.status_code == 200, response.text
    data = response.json()
    assert data["total_drugs"] == 1450
    assert data["total_proteins"] == 20340
    assert data["schemaVersion"] == "20"
    assert "Compound->Disease" in data["edgeTypesPresent"]
    assert data["compoundNodesLoaded"] == 1450
    assert data["proteinNodesLoaded"] == 20340
    assert data["lastUpdated"] == "2026-07-21T09:15:32Z"


# ===========================================================================
# TEST 2: /datasets/stats returns 503 when Phase 1 is down.
# ===========================================================================
@pytest.mark.integration
def test_backend_datasets_stats_returns_503_when_phase1_down(client, auth_headers):
    """When Phase 1 is unreachable, the backend returns 503 (not 500/hang)."""
    with patch("backend.api.main.httpx.AsyncClient") as mock_client_cls:
        mock_client = AsyncMock()
        mock_client.__aenter__.return_value = mock_client
        mock_client.get.side_effect = httpx.RequestError("Connection refused")
        mock_client_cls.return_value = mock_client

        response = client.get("/datasets/stats", headers=auth_headers)

    assert response.status_code == 503, response.text
    detail = response.json()["detail"]
    assert "Phase 1 service unavailable" in detail


# ===========================================================================
# TEST 3: /datasets/stats returns 401 without a JWT.
# ===========================================================================
@pytest.mark.integration
def test_backend_datasets_stats_returns_401_without_jwt(client):
    """No Authorization header -> 401."""
    response = client.get("/datasets/stats")
    assert response.status_code == 401


# ===========================================================================
# TEST 4: /datasets/stats returns 403 when JWT has no org_id claim.
# ===========================================================================
@pytest.mark.integration
def test_backend_datasets_stats_returns_403_when_jwt_missing_org_id(client):
    """JWT without org_id claim -> 403 (verify_org_id rejects it)."""
    # Mint a JWT with no org_id by encoding manually.
    import jwt as _jwt
    secret = os.environ["JWT_SECRET"]
    token = _jwt.encode(
        {"sub": "testuser", "iss": "drugos", "exp": 9999999999},
        secret,
        algorithm="HS256",
    )
    response = client.get(
        "/datasets/stats",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert response.status_code == 403
    assert "org_id" in response.json()["detail"]


# ===========================================================================
# TEST 5: POST /datasets/validated_hypotheses enforces org_id scoping.
# ===========================================================================
@pytest.mark.integration
def test_post_validated_hypothesis_enforces_org_id_scoping(client, auth_headers):
    """User in org A cannot write a hypothesis for org B (403)."""
    response = client.post(
        "/datasets/validated_hypotheses",
        headers=auth_headers,
        json={
            "drug": "aspirin",
            "disease": "headache",
            "outcome": "validated_positive",
            "validated_at": "2026-07-21T09:15:32Z",
            "org_id": "DIFFERENT_ORG",  # Mismatch with JWT's org_id="testorg"
        },
    )
    assert response.status_code == 403
    assert "Cross-org validation forbidden" in response.json()["detail"]


# ===========================================================================
# TEST 6: POST /datasets/validated_hypotheses forwards to Phase 1 with the
# JWT's org_id (not the payload's org_id).
# ===========================================================================
@pytest.mark.integration
def test_post_validated_hypothesis_forwards_with_jwt_org_id(client, auth_headers):
    """When org_id is omitted from the payload, the backend injects the
    JWT's org_id and forwards to Phase 1."""
    mock_response = _make_mock_post_response(
        201,
        {
            "status": "ok",
            "id": 42,
            "validated_hypothesis": {
                "id": 42,
                "drug_name": "aspirin",
                "disease_name": "headache",
                "outcome": "validated_positive",
            },
            "flywheel_status": "persisted_to_postgresql",
        },
    )

    with patch("backend.api.main.httpx.AsyncClient") as mock_client_cls:
        mock_client = AsyncMock()
        mock_client.__aenter__.return_value = mock_client
        mock_client.post.return_value = mock_response
        mock_client_cls.return_value = mock_client

        response = client.post(
            "/datasets/validated_hypotheses",
            headers=auth_headers,
            json={
                "drug": "aspirin",
                "disease": "headache",
                "outcome": "validated_positive",
                "validated_at": "2026-07-21T09:15:32Z",
                # No org_id in payload — backend should inject "testorg".
            },
        )

    assert response.status_code == 201, response.text
    # Verify the forwarded payload had the JWT's org_id injected.
    mock_client.post.assert_awaited_once()
    call_args = mock_client.post.await_args
    forwarded_json = call_args.kwargs.get("json") or call_args[1].get("json")
    assert forwarded_json["org_id"] == "testorg", (
        f"Expected forwarded payload org_id='testorg', got {forwarded_json.get('org_id')}"
    )
    # Verify the X-Org-Id header was set.
    forwarded_headers = call_args.kwargs.get("headers") or call_args[1].get("headers")
    assert forwarded_headers["X-Org-Id"] == "testorg"


# ===========================================================================
# TEST 7: POST /datasets/validated_hypotheses returns 503 when Phase 1 down.
# ===========================================================================
@pytest.mark.integration
def test_post_validated_hypothesis_returns_503_when_phase1_down(client, auth_headers):
    """When Phase 1 is unreachable, POST returns 503 (not 500)."""
    with patch("backend.api.main.httpx.AsyncClient") as mock_client_cls:
        mock_client = AsyncMock()
        mock_client.__aenter__.return_value = mock_client
        mock_client.post.side_effect = httpx.RequestError("Connection refused")
        mock_client_cls.return_value = mock_client

        response = client.post(
            "/datasets/validated_hypotheses",
            headers=auth_headers,
            json={
                "drug": "aspirin",
                "disease": "headache",
                "outcome": "validated_positive",
                "validated_at": "2026-07-21T09:15:32Z",
            },
        )

    assert response.status_code == 503


# ===========================================================================
# TEST 8: GET /datasets/{drug}/mechanism proxies to Phase 1.
# ===========================================================================
@pytest.mark.integration
def test_get_drug_mechanism_proxies_to_phase1(client, auth_headers):
    """GET /datasets/{drug}/mechanism proxies to Phase 1."""
    request = httpx.Request("GET", "http://phase1-test:8001/datasets/aspirin/mechanism")
    mock_response = httpx.Response(
        200,
        json={
            "drug": "aspirin",
            "drugbank_id": "DB00945",
            "targets": [
                {"protein": "PTGS1", "uniprot_id": "P23219", "action": "inhibitor", "evidence": "drugbank"}
            ],
            "indications": ["pain", "fever", "inflammation"],
            "source": "phase1_drugbank_csv",
        },
        request=request,
    )

    with patch("backend.api.main.httpx.AsyncClient") as mock_client_cls:
        mock_client = AsyncMock()
        mock_client.__aenter__.return_value = mock_client
        mock_client.get.return_value = mock_response
        mock_client_cls.return_value = mock_client

        response = client.get(
            "/datasets/aspirin/mechanism",
            headers=auth_headers,
        )

    assert response.status_code == 200, response.text
    data = response.json()
    assert data["drug"] == "aspirin"
    assert data["drugbank_id"] == "DB00945"
    assert len(data["targets"]) == 1
    assert data["targets"][0]["protein"] == "PTGS1"
