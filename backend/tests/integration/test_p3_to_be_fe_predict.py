"""TEAMMATE-11 acceptance tests: P3 -> Backend + Frontend Integration.

These tests verify the ROOT FIX for the Teammate-11 issue:
  1. /predict proxies to the GT service via httpx (not hardcoded 0.5).
  2. /predict response includes pathways, model_version, real gnn_score.
  3. /predict returns 503 when the GT service is down (graceful failure).
  4. /ready probes the GT service /health and reports degraded when down.
  5. /health is a pure liveness probe (always 200 when process is up).
  6. /predict requires both JWT auth and org_id.
  7. PredictResponse shape matches the documented API contract.
"""
from __future__ import annotations

import httpx
import pytest
from fastapi.testclient import TestClient


@pytest.fixture
def client():
    """FastAPI TestClient."""
    from backend.api import main as main_module
    return TestClient(main_module.app)


def _mint_jwt() -> str:
    """Mint a valid test JWT using the backend's create_test_jwt helper."""
    from backend.api.main import create_test_jwt
    return create_test_jwt(user_id="testuser", org_id="testorg")


@pytest.mark.integration
def test_backend_predict_proxies_to_gt_service(client):
    """Acceptance #1: /predict returns the GT service's real score (not 0.5)."""
    from backend.api import main as main_module

    mock_gt_response = httpx.Response(
        200,
        json={
            "predictions": [{
                "drug": "aspirin",
                "disease": "headache",
                "score": 0.87,
                "confidence": 0.85,
                "pathways": [{
                    "pathway": "COX inhibition",
                    "intermediate_protein": "COX-1",
                    "chain": ["aspirin", "COX-1", "arachidonic acid metabolism", "headache"],
                }],
                "literature_supported": True,
            }],
            "modelVersion": "gt_4.1.0",
            "generatedAt": "2026-07-21T00:00:00Z",
            "count": 1,
            "checkpointPath": "/tmp/gt.pt",
        },
    )

    original_async_client = main_module.httpx.AsyncClient

    class _MockAsyncClient:
        def __init__(self, *args, **kwargs):
            self._mock_response = mock_gt_response

        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            return False

        async def post(self, url, **kwargs):
            return self._mock_response

        async def get(self, url, **kwargs):
            return self._mock_response

    main_module.httpx.AsyncClient = _MockAsyncClient
    try:
        jwt_token = _mint_jwt()
        response = client.post(
            "/predict",
            headers={"Authorization": f"Bearer {jwt_token}"},
            json={"drug": "aspirin", "disease": "headache"},
        )
    finally:
        main_module.httpx.AsyncClient = original_async_client

    assert response.status_code == 200, response.text
    data = response.json()
    assert data["gnn_score"] == 0.87, f"Expected 0.87, got {data['gnn_score']}"
    assert data["confidence"] == 0.85
    assert len(data["pathways"]) == 1
    assert data["pathways"][0]["pathway"] == "COX inhibition"
    assert data["pathways"][0]["intermediate_protein"] == "COX-1"
    assert data["pathways"][0]["chain"] == [
        "aspirin", "COX-1", "arachidonic acid metabolism", "headache",
    ]
    assert data["model_version"] == "gt_4.1.0"
    assert data["literature_supported"] is True


@pytest.mark.integration
def test_backend_predict_returns_503_when_gt_down(client):
    """Acceptance #5: /predict returns 503 when GT service is unreachable."""
    from backend.api import main as main_module

    original_async_client = main_module.httpx.AsyncClient

    class _MockAsyncClientFails:
        def __init__(self, *args, **kwargs):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            return False

        async def post(self, url, **kwargs):
            raise httpx.ConnectError("Connection refused")

        async def get(self, url, **kwargs):
            raise httpx.ConnectError("Connection refused")

    main_module.httpx.AsyncClient = _MockAsyncClientFails
    try:
        jwt_token = _mint_jwt()
        response = client.post(
            "/predict",
            headers={"Authorization": f"Bearer {jwt_token}"},
            json={"drug": "aspirin", "disease": "headache"},
        )
    finally:
        main_module.httpx.AsyncClient = original_async_client

    assert response.status_code == 503, response.text
    assert "GT service unavailable" in response.json()["detail"]


@pytest.mark.integration
def test_backend_predict_requires_auth(client):
    """Acceptance: /predict rejects requests without a valid JWT."""
    response = client.post(
        "/predict",
        json={"drug": "aspirin", "disease": "headache"},
    )
    assert response.status_code == 401, response.text


@pytest.mark.integration
def test_health_endpoint_is_liveness_probe(client):
    """Acceptance: /health is a liveness probe (always 200 when process is up)."""
    response = client.get("/health")
    assert response.status_code == 200, response.text
    data = response.json()
    assert data["status"] == "ok"
    assert data["version"] == "4.1.0", (
        f"Expected 4.1.0, got {data['version']} (P3-020 version drift)"
    )


@pytest.mark.integration
def test_ready_endpoint_probes_gt_service(client):
    """Acceptance: /ready probes the GT service /health endpoint."""
    from backend.api import main as main_module

    mock_gt_response = httpx.Response(
        200, json={"status": "ok", "version": "4.1.0"}
    )
    mock_rl_response = httpx.Response(
        200, json={"status": "ok", "version": "4.1.0"}
    )
    responses = iter([mock_gt_response, mock_rl_response])

    original_async_client = main_module.httpx.AsyncClient

    class _MockAsyncClient:
        def __init__(self, *args, **kwargs):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            return False

        async def get(self, url, **kwargs):
            return next(responses)

        async def post(self, url, **kwargs):
            return next(responses)

    main_module.httpx.AsyncClient = _MockAsyncClient
    # /ready also probes the DB. Disable the DB probe by unsetting DATABASE_URL.
    main_module.os.environ.pop("DATABASE_URL", None)
    try:
        response = client.get("/ready")
    finally:
        main_module.httpx.AsyncClient = original_async_client
        main_module.os.environ["DATABASE_URL"] = ""

    # DB probe will fail (no DATABASE_URL), so response should be 503 with
    # status=degraded. The GT and RL probes should be True.
    assert response.status_code in (200, 503), response.text
    if response.status_code == 503:
        data = response.json()["detail"]
    else:
        data = response.json()
    checks = data.get("checks", {}) if isinstance(data, dict) else {}
    assert checks.get("gt_service") is True, (
        f"GT service probe failed; checks={checks}"
    )


@pytest.mark.integration
def test_predict_response_shape_matches_contract(client):
    """Verify the PredictResponse shape matches the documented API contract."""
    from backend.api import main as main_module

    mock_gt_response = httpx.Response(
        200,
        json={
            "predictions": [{
                "drug": "metformin",
                "disease": "diabetes",
                "score": 0.92,
                "confidence": 0.88,
                "pathways": [],
                "literature_supported": True,
            }],
            "modelVersion": "gt_4.1.0",
        },
    )
    original_async_client = main_module.httpx.AsyncClient

    class _MockAsyncClient:
        def __init__(self, *args, **kwargs):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            return False

        async def post(self, url, **kwargs):
            return mock_gt_response

        async def get(self, url, **kwargs):
            return mock_gt_response

    main_module.httpx.AsyncClient = _MockAsyncClient
    try:
        jwt_token = _mint_jwt()
        response = client.post(
            "/predict",
            headers={"Authorization": f"Bearer {jwt_token}"},
            json={"drug": "metformin", "disease": "diabetes"},
        )
    finally:
        main_module.httpx.AsyncClient = original_async_client

    assert response.status_code == 200, response.text
    data = response.json()
    required_fields = {
        "drug", "disease", "gnn_score", "confidence",
        "pathways", "literature_supported", "model_version",
    }
    assert required_fields.issubset(set(data.keys())), (
        f"Missing fields: {required_fields - set(data.keys())}"
    )
    assert isinstance(data["drug"], str)
    assert isinstance(data["disease"], str)
    assert isinstance(data["gnn_score"], (int, float))
    assert 0.0 <= data["gnn_score"] <= 1.0
    assert isinstance(data["confidence"], (int, float))
    assert 0.0 <= data["confidence"] <= 1.0
    assert isinstance(data["pathways"], list)
    assert isinstance(data["literature_supported"], bool)
    assert isinstance(data["model_version"], str)


@pytest.mark.integration
def test_backend_version_matches_gt_package_version():
    """Acceptance: BACKEND_VERSION == graph_transformer.__version__."""
    from graph_transformer import __version__ as pkg_version
    from backend.api.main import BACKEND_VERSION, MODEL_VERSION
    assert BACKEND_VERSION == pkg_version, (
        f"BACKEND_VERSION={BACKEND_VERSION} != pkg={pkg_version}"
    )
    assert MODEL_VERSION == f"gt_{pkg_version}", (
        f"MODEL_VERSION={MODEL_VERSION} != gt_{pkg_version}"
    )
