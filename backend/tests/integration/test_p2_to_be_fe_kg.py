"""
Teammate 8 — P2 to Backend + Frontend Integration tests.

Verifies the three pillars of the Teammate 8 ROOT FIX:
  1. The backend FastAPI service proxies /kg/stats, /kg/explore, /cypher
     to the Phase 2 KG service via httpx.
  2. The backend enforces JWT auth + org_id scoping on every /kg/* call.
  3. The backend enforces a 10 req/min per-user rate limit on /cypher
     (returns 429 on the 11th call within a 60s window).
  4. The backend returns 503 when the Phase 2 service is unreachable.

These tests use FastAPI's TestClient + unittest.mock to mock the
httpx.AsyncClient — they do NOT require a real Phase 2 service to be
running. This makes them suitable for CI (no external dependencies).

Run with:
    python -m pytest backend/tests/integration/test_p2_to_be_fe_kg.py -v
"""
from __future__ import annotations

import os
import sys
from pathlib import Path
from unittest.mock import AsyncMock, patch

import httpx
import pytest

# Make the repo root importable so ``from backend.api.main import app``
# works regardless of the test runner's CWD.
_REPO_ROOT = Path(__file__).resolve().parents[3]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))


@pytest.fixture
def fastapi_client():
    """Build a FastAPI TestClient for the backend app."""
    from fastapi.testclient import TestClient
    from backend.api.main import app
    # Reset all rate limiters between tests so the per-user counters
    # don't bleed across test cases.
    from backend.api.rate_limit import (
        CYPHER_RATE_LIMITER,
        KG_STATS_RATE_LIMITER,
        KG_EXPLORE_RATE_LIMITER,
    )
    CYPHER_RATE_LIMITER.reset()
    KG_STATS_RATE_LIMITER.reset()
    KG_EXPLORE_RATE_LIMITER.reset()
    with TestClient(app) as client:
        yield client
    # Cleanup after the test.
    CYPHER_RATE_LIMITER.reset()
    KG_STATS_RATE_LIMITER.reset()
    KG_EXPLORE_RATE_LIMITER.reset()


@pytest.fixture
def test_jwt():
    """Mint a test JWT for the authenticated test user."""
    from backend.api.main import create_test_jwt
    return create_test_jwt(user_id="testuser", org_id="testorg")


@pytest.fixture
def auth_headers(test_jwt):
    """Build the Authorization + X-Org-Id headers for authenticated requests."""
    return {
        "Authorization": f"Bearer {test_jwt}",
        "X-Org-Id": "testorg",
        "Content-Type": "application/json",
    }


@pytest.fixture
def mock_kg_stats_response():
    """Build a mock Phase 2 /kg/stats response (with canonicalNodeCount)."""
    return httpx.Response(
        200,
        json={
            "nodeCount": 105000,
            "canonicalNodeCount": 95000,
            "edgeCount": 2500000,
            "nodeTypes": {
                "Compound": 10000,
                "Protein": 20000,
                "Pathway": 5000,
                "Disease": 8000,
                "ClinicalOutcome": 2000,
                "Gene": 25000,
                "MedDRA_Term": 2000,
                "Anatomy": 3000,
            },
            "sources": [{"name": "chembl", "available": True}],
            "lastUpdated": "2026-07-21T09:15:32Z",
            "source": "neo4j",
        },
    )


# ---------------------------------------------------------------------------
# Test 1: backend /kg/stats proxies to Phase 2 and returns canonicalNodeCount
# ---------------------------------------------------------------------------
@pytest.mark.integration
def test_backend_kg_stats_proxies_to_phase2(
    fastapi_client, auth_headers, mock_kg_stats_response,
):
    """The backend /kg/stats route proxies to Phase 2 and returns the
    canonicalNodeCount field unchanged.
    """
    with patch("backend.api.main.httpx.AsyncClient") as mock_client_cls:
        mock_client = AsyncMock()
        mock_client.__aenter__.return_value = mock_client
        mock_client.get.return_value = mock_kg_stats_response
        mock_client_cls.return_value = mock_client

        response = fastapi_client.get("/kg/stats", headers=auth_headers)

    assert response.status_code == 200, (
        f"Expected 200, got {response.status_code}: {response.text}"
    )
    data = response.json()
    assert data["nodeCount"] == 105000, f"nodeCount mismatch: {data}"
    assert data["canonicalNodeCount"] == 95000, (
        f"canonicalNodeCount mismatch: {data}"
    )
    # Verify the backend actually called the Phase 2 service via httpx.
    mock_client.get.assert_awaited_once()
    # The first positional arg to client.get() should be the KG URL.
    call_args = mock_client.get.call_args
    url = call_args.args[0] if call_args.args else call_args.kwargs.get("url", "")
    assert "/kg/stats" in str(url), (
        f"Backend should call /kg/stats, got URL: {url}"
    )


# ---------------------------------------------------------------------------
# Test 2: backend /kg/stats returns 503 when Phase 2 is down
# ---------------------------------------------------------------------------
@pytest.mark.integration
def test_backend_kg_stats_returns_503_when_phase2_down(
    fastapi_client, auth_headers,
):
    """When the Phase 2 KG service is unreachable, the backend /kg/stats
    route returns HTTP 503 with a clear error message.
    """
    with patch("backend.api.main.httpx.AsyncClient") as mock_client_cls:
        mock_client = AsyncMock()
        mock_client.__aenter__.return_value = mock_client
        mock_client.get.side_effect = httpx.RequestError("Connection refused")
        mock_client_cls.return_value = mock_client

        response = fastapi_client.get("/kg/stats", headers=auth_headers)

    assert response.status_code == 503, (
        f"Expected 503, got {response.status_code}: {response.text}"
    )
    data = response.json()
    # FastAPI HTTPException detail is wrapped in {"detail": ...}.
    detail = data.get("detail", data)
    assert isinstance(detail, dict), f"Expected dict detail, got: {detail}"
    assert detail.get("error") == "kg_service_unavailable", (
        f"Expected error=kg_service_unavailable, got: {detail}"
    )


# ---------------------------------------------------------------------------
# Test 3: backend /kg/stats returns 401 without a JWT
# ---------------------------------------------------------------------------
@pytest.mark.integration
def test_backend_kg_stats_returns_401_without_jwt(fastapi_client):
    """The backend /kg/stats route requires JWT auth — a request
    without an Authorization header returns 401.
    """
    response = fastapi_client.get("/kg/stats")
    assert response.status_code == 401, (
        f"Expected 401, got {response.status_code}: {response.text}"
    )


# ---------------------------------------------------------------------------
# Test 4: backend /kg/stats returns 403 without an org_id
# ---------------------------------------------------------------------------
@pytest.mark.integration
def test_backend_kg_stats_returns_403_without_org_id(
    fastapi_client, test_jwt,
):
    """The backend /kg/stats route requires an org_id — a request with
    a valid JWT but no org_id claim and no X-Org-Id header returns 403.
    """
    # Mint a JWT without an org_id claim.
    from backend.api.main import create_test_jwt
    jwt_no_org = create_test_jwt(user_id="testuser", org_id="")
    # Manually strip the org_id from the JWT (create_test_jwt always sets it).
    import jwt as _jwt
    secret = os.environ.get("JWT_SECRET", "")
    payload = _jwt.decode(jwt_no_org, secret, algorithms=["HS256"], issuer="drugos")
    payload.pop("org_id", None)
    payload.pop("orgId", None)
    jwt_no_org = _jwt.encode(payload, secret, algorithm="HS256")
    if isinstance(jwt_no_org, bytes):
        jwt_no_org = jwt_no_org.decode("ascii")

    response = fastapi_client.get(
        "/kg/stats",
        headers={"Authorization": f"Bearer {jwt_no_org}"},
    )
    assert response.status_code == 403, (
        f"Expected 403, got {response.status_code}: {response.text}"
    )


# ---------------------------------------------------------------------------
# Test 5: 11th /cypher request within 1 minute returns 429
# ---------------------------------------------------------------------------
@pytest.mark.integration
def test_cypher_rate_limited_after_10_per_min(
    fastapi_client, auth_headers,
):
    """The backend /cypher route enforces a 10 req/min per-user rate
    limit. The first 10 requests succeed; the 11th returns 429.
    """
    mock_response = httpx.Response(
        200,
        json={
            "records": [],
            "row_count": 0,
            "truncated": False,
            "max_rows": 1000,
            "backend": "neo4j",
            "timeout_seconds": 30,
        },
    )
    with patch("backend.api.main.httpx.AsyncClient") as mock_client_cls:
        mock_client = AsyncMock()
        mock_client.__aenter__.return_value = mock_client
        mock_client.post.return_value = mock_response
        mock_client_cls.return_value = mock_client

        # First 10 requests should succeed.
        for i in range(10):
            response = fastapi_client.post(
                "/cypher",
                headers=auth_headers,
                json={"query": "RETURN 1", "params": {}},
            )
            assert response.status_code == 200, (
                f"Request {i + 1}/10 should succeed, got "
                f"{response.status_code}: {response.text}"
            )

        # 11th request should be rate-limited (429).
        response = fastapi_client.post(
            "/cypher",
            headers=auth_headers,
            json={"query": "RETURN 1", "params": {}},
        )
        assert response.status_code == 429, (
            f"11th request should be rate-limited (429), got "
            f"{response.status_code}: {response.text}"
        )
        # Verify the rate-limit detail includes retry_after_seconds.
        data = response.json()
        detail = data.get("detail", data)
        if isinstance(detail, dict):
            assert "retry_after_seconds" in detail, (
                f"429 response should include retry_after_seconds: {detail}"
            )


# ---------------------------------------------------------------------------
# Test 6: backend /kg/explore proxies POST to Phase 2
# ---------------------------------------------------------------------------
@pytest.mark.integration
def test_backend_kg_explore_proxies_to_phase2(
    fastapi_client, auth_headers,
):
    """The backend /kg/explore POST route proxies to Phase 2."""
    mock_response = httpx.Response(
        200,
        json={
            "nodes": [{"id": "CHEMBL123", "label": "Aspirin", "type": "Compound"}],
            "edges": [],
            "truncated": False,
        },
    )
    with patch("backend.api.main.httpx.AsyncClient") as mock_client_cls:
        mock_client = AsyncMock()
        mock_client.__aenter__.return_value = mock_client
        mock_client.post.return_value = mock_response
        mock_client_cls.return_value = mock_client

        response = fastapi_client.post(
            "/kg/explore",
            headers=auth_headers,
            json={"drug": "aspirin", "depth": 2},
        )

    assert response.status_code == 200, (
        f"Expected 200, got {response.status_code}: {response.text}"
    )
    data = response.json()
    assert len(data["nodes"]) == 1, f"Expected 1 node, got: {data}"
    assert data["nodes"][0]["label"] == "Aspirin", (
        f"Expected Aspirin, got: {data['nodes'][0]['label']}"
    )
    # Verify the backend forwarded the body to Phase 2.
    mock_client.post.assert_awaited_once()
    call_kwargs = mock_client.post.call_args.kwargs
    forwarded_body = call_kwargs.get("json", {})
    assert forwarded_body.get("drug") == "aspirin", (
        f"Backend should forward drug=aspirin, got: {forwarded_body}"
    )


# ---------------------------------------------------------------------------
# Test 7: backend port defaults to 8004 (no collision with phase1=8000 or phase2=8001)
# ---------------------------------------------------------------------------
@pytest.mark.integration
def test_backend_default_port_is_8004():
    """The backend FastAPI service defaults to port 8004 (NOT 8000 or 8001).

    BE-003 v143 ROOT FIX (Teammate 12 — hostile-auditor pass):
      The previous "fix" moved the port from 8001 → 8000 to avoid colliding
      with phase2_kg. But 8000 is the canonical phase1_dataset port (per
      shared/contracts/urls.py SERVICE_PORTS) — the "fix" just moved the
      collision. The TRULY free port is 8004 (after the 4 ML services
      8000-8003). This test asserts the default is 8004.
    """
    # Import the module to read the port default.
    import importlib
    import backend.api.main as main_module
    importlib.reload(main_module)  # ensure env vars from prior tests don't leak

    # Save + restore the env var so this test doesn't affect others.
    saved_port = os.environ.pop("DRUGOS_API_PORT", None)
    try:
        # Re-read the default by reading the source and asserting the
        # default value is "8004" (NOT "8000" — that collides with
        # phase1_dataset per shared/contracts/urls.py).
        main_file = Path(main_module.__file__)
        source = main_file.read_text()
        assert 'os.environ.get("DRUGOS_API_PORT", "8004")' in source, (
            "backend/api/main.py should default DRUGOS_API_PORT to '8004' "
            "(NOT '8000' — 8000 collides with phase1_dataset per "
            "shared/contracts/urls.py SERVICE_PORTS; NOT '8001' — 8001 "
            "collides with phase2_kg). BE-003 v143."
        )
    finally:
        if saved_port is not None:
            os.environ["DRUGOS_API_PORT"] = saved_port


# ---------------------------------------------------------------------------
# Test 8: verify_org_id reads org_id from X-Org-Id header (fallback path)
# ---------------------------------------------------------------------------
@pytest.mark.integration
def test_verify_org_id_reads_x_org_id_header(
    fastapi_client, mock_kg_stats_response,
):
    """verify_org_id should accept the X-Org-Id header as a FALLBACK
    when the JWT doesn't include an org_id claim.

    This tests the trusted-internal-caller path: a service-to-service
    call where the caller passes an X-Org-Id header instead of minting
    a full JWT. The backend's verify_org_id reads the JWT first (priority
    1), then falls back to the X-Org-Id header (priority 2).
    """
    # Mint a JWT WITHOUT an org_id claim (simulating a service-to-service
    # caller that hasn't populated the org_id claim).
    import jwt as _jwt
    # Use a fresh test secret (don't pollute the env var permanently).
    test_secret = os.environ.get("JWT_SECRET") or "test-secret-for-integration-tests-only-32chars!"
    os.environ["JWT_SECRET"] = test_secret
    jwt_no_org = _jwt.encode(
        {"sub": "service-account", "iss": "drugos"},
        test_secret,
        algorithm="HS256",
    )
    if isinstance(jwt_no_org, bytes):
        jwt_no_org = jwt_no_org.decode("ascii")

    with patch("backend.api.main.httpx.AsyncClient") as mock_client_cls:
        mock_client = AsyncMock()
        mock_client.__aenter__.return_value = mock_client
        mock_client.get.return_value = mock_kg_stats_response
        mock_client_cls.return_value = mock_client

        response = fastapi_client.get(
            "/kg/stats",
            headers={
                "Authorization": f"Bearer {jwt_no_org}",
                "X-Org-Id": "custom-org-id-from-header",
            },
        )

    assert response.status_code == 200, (
        f"Expected 200, got {response.status_code}: {response.text}"
    )
    # Verify the backend forwarded the X-Org-Id header to Phase 2.
    mock_client.get.assert_awaited_once()
    call_kwargs = mock_client.get.call_args.kwargs
    forwarded_headers = call_kwargs.get("headers", {})
    assert forwarded_headers.get("X-Org-Id") == "custom-org-id-from-header", (
        f"Backend should forward X-Org-Id header when JWT lacks org_id, "
        f"got: {forwarded_headers}"
    )


if __name__ == "__main__":
    # Allow running this test file directly:
    #   python -m pytest backend/tests/integration/test_p2_to_be_fe_kg.py -v
    pytest.main([__file__, "-v", "--tb=short"])
