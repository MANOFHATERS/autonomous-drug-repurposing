"""
P4-024 ROOT FIX (Teammate 12 — P4 to Backend Integration):
Integration tests for the backend /top-k → RL service /rank proxy.

THE BUG (forensic root cause):
  The previous backend /top-k endpoint returned a HARDCODED placeholder:
      return TopKResponse(candidates=[], total=0, source="rl_ranker")
  The Phase 4 RL service was NEVER invoked. Pharma partners received
  empty candidates with a misleading source='rl_ranker'. Even if the
  placeholder were replaced, the RL service /rank REQUIRES org_id
  (BE-043 v128) but the backend did NOT extract org_id from the JWT —
  every request would have gotten 401.

ROOT FIX:
  1. Extract org_id from the JWT (verify_org_id dependency — already
     present in main from Teammate 4/8).
  2. Proxy /top-k to {RL_SERVICE_URL}/rank via httpx, passing org_id
     as a query param + X-Org-Id header.
  3. On RL service connection failure, return 503 (NOT empty 200).
  4. On RL service 401, return 401 (so frontend can re-authenticate).

VERIFICATION:
  These tests mock httpx.AsyncClient so they don't require the RL
  service to be running. They verify:
    - /top-k proxies to RL_SERVICE_URL/rank with org_id as query param.
    - /top-k returns the RL service's candidates (not empty placeholder).
    - /top-k returns 503 when the RL service is unreachable.
    - /top-k passes the correct org_id (extracted from the JWT) to the
      RL service — NOT a hardcoded org_id.
"""
from __future__ import annotations

import os
import sys
from pathlib import Path
from unittest.mock import patch, AsyncMock

import httpx
import pytest

# Ensure repo root is on sys.path so `import backend.api.main` works.
_REPO_ROOT = Path(__file__).resolve().parents[3]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

# P4-024: tests MUST set JWT_SECRET (>=32 chars) before importing
# backend.api.main, because verify_jwt checks it at request time.
os.environ.setdefault(
    "JWT_SECRET",
    "test-secret-key-for-testing-only-not-for-production-use-32-chars-min",
)
# Disable RL_REQUIRE_AUTH so the RL service (if imported) doesn't reject
# test requests during module import.
os.environ.setdefault("RL_REQUIRE_AUTH", "false")


@pytest.fixture
def jwt_token():
    """Mint a test JWT with sub + org_id claims.

    Uses main's create_test_jwt(*, user_id=, org_id=) helper (keyword-only).
    """
    from backend.api.main import create_test_jwt
    return create_test_jwt(user_id="testuser", org_id="testorg")


@pytest.fixture
def jwt_token_specific_org():
    """Mint a test JWT with a specific org_id to verify org_id pass-through."""
    from backend.api.main import create_test_jwt
    return create_test_jwt(user_id="testuser", org_id="specific_org_123")


@pytest.mark.integration
def test_backend_top_k_proxies_to_rl_service(jwt_token):
    """P4-024 acceptance criterion 1: /top-k proxies to RL service /rank.

    Verifies that:
      - /top-k returns 200 with the RL service's response (not empty).
      - Each candidate's fields (score, pathway_chain) are forwarded.
      - pathway_enrichment_available is True when candidates have pathway_chain.
      - org_id is passed as a query param to RL_SERVICE_URL/rank.
    """
    from fastapi.testclient import TestClient
    from backend.api.main import app

    client = TestClient(app)

    # Mock the RL service's response — a single candidate with a pathway_chain.
    # NOTE: httpx.Response requires a `request` attribute for raise_for_status()
    # to work. We construct the response with an explicit request so the
    # backend's `response.raise_for_status()` call doesn't raise RuntimeError.
    mock_response = httpx.Response(
        200,
        json={
            "candidates": [
                {
                    "drug": "metformin",
                    "disease": "cancer",
                    "score": 0.87,
                    "pathway_chain": [
                        {"pathway": "mTOR", "chain": ["metformin", "mTOR", "cancer"]},
                    ],
                },
            ],
            "total": 1,
            "source": "service",
            "pathway_enrichment_available": True,
        },
        request=httpx.Request("POST", "http://test-rl-service/rank"),
    )

    # Patch httpx.AsyncClient so the /top-k proxy doesn't actually call
    # the RL service.
    with patch("backend.api.main.httpx.AsyncClient") as mock_client_cls:
        mock_client = AsyncMock()
        mock_client.__aenter__.return_value = mock_client
        mock_client.post.return_value = mock_response
        mock_client_cls.return_value = mock_client

        response = client.post(
            "/top-k",
            headers={"Authorization": f"Bearer {jwt_token}"},
            json={"drug": "metformin", "disease": "cancer", "k": 10},
        )

    assert response.status_code == 200, f"Expected 200, got {response.status_code}: {response.text}"
    data = response.json()
    assert len(data["candidates"]) == 1, f"Expected 1 candidate, got {len(data['candidates'])}"
    assert data["candidates"][0]["score"] == 0.87
    assert data["candidates"][0]["pathway_chain"][0]["pathway"] == "mTOR"
    assert data["pathway_enrichment_available"] is True

    # Verify the proxy called the RL service with org_id as a query param.
    mock_client.post.assert_called_once()
    call_args = mock_client.post.call_args
    assert call_args[0][0] == "http://localhost:8004/rank" or call_args[0][0].endswith("/rank")
    assert call_args[1]["params"]["org_id"] == "testorg", (
        f"Expected org_id='testorg' in query params, got: {call_args[1]['params']}"
    )


@pytest.mark.integration
def test_backend_top_k_returns_503_when_rl_down(jwt_token):
    """P4-024 acceptance criterion 4: /top-k returns 503 (NOT empty 200) when RL is down.

    The previous code returned an empty 200 with source='rl_ranker' even
    when the RL service was unreachable — misleading the API client into
    thinking the ranker returned nothing. The fix returns 503 so the
    client knows the RL service is unavailable and can retry.
    """
    from fastapi.testclient import TestClient
    from backend.api.main import app

    client = TestClient(app)

    with patch("backend.api.main.httpx.AsyncClient") as mock_client_cls:
        mock_client = AsyncMock()
        mock_client.__aenter__.return_value = mock_client
        # Simulate a connection failure (RL service down).
        mock_client.post.side_effect = httpx.ConnectError("Connection refused")
        mock_client_cls.return_value = mock_client

        response = client.post(
            "/top-k",
            headers={"Authorization": f"Bearer {jwt_token}"},
            json={"drug": "metformin", "disease": "cancer", "k": 10},
        )

    assert response.status_code == 503, (
        f"Expected 503 when RL service is down, got {response.status_code}: {response.text}"
    )
    detail = response.json()["detail"]
    assert "RL service unavailable" in detail, (
        f"Expected 'RL service unavailable' in detail, got: {detail}"
    )


@pytest.mark.integration
def test_backend_top_k_passes_org_id_to_rl_service(jwt_token_specific_org):
    """P4-024 acceptance criterion 5: org_id is passed as a query param to RL /rank.

    Verifies that the org_id extracted from the JWT (NOT a hardcoded
    value) is forwarded to the RL service. This is critical for
    cross-tenant isolation (BE-043 v128): the RL service filters
    candidates based on org_id, so passing the WRONG org_id (or none)
    would either leak another org's private drugs or 401.
    """
    from fastapi.testclient import TestClient
    from backend.api.main import app

    client = TestClient(app)

    mock_response = httpx.Response(
        200,
        json={"candidates": [], "total": 0, "source": "service"},
        request=httpx.Request("POST", "http://test-rl-service/rank"),
    )

    with patch("backend.api.main.httpx.AsyncClient") as mock_client_cls:
        mock_client = AsyncMock()
        mock_client.__aenter__.return_value = mock_client
        mock_client.post.return_value = mock_response
        mock_client_cls.return_value = mock_client

        client.post(
            "/top-k",
            headers={"Authorization": f"Bearer {jwt_token_specific_org}"},
            json={"drug": "aspirin", "disease": "headache", "k": 5},
        )

        call_args = mock_client.post.call_args
        assert call_args[1]["params"]["org_id"] == "specific_org_123", (
            f"Expected org_id='specific_org_123' (from JWT) in query params, "
            f"got: {call_args[1]['params']}"
        )
        # Also verify org_id is in the X-Org-Id header (defense in depth).
        assert call_args[1]["headers"]["X-Org-Id"] == "specific_org_123", (
            f"Expected X-Org-Id='specific_org_123' header, got: {call_args[1]['headers']}"
        )


@pytest.mark.integration
def test_backend_top_k_returns_403_without_org_id_claim():
    """P4-024: /top-k returns 403 when the JWT lacks the org_id claim.

    The RL service REQUIRES org_id (BE-043 v128). If the JWT doesn't
    have it, the backend's verify_org_id dependency returns 403
    (Teammate 8's implementation: "No active organization. The caller's
    JWT must include an 'org_id' claim...").
    """
    from fastapi.testclient import TestClient
    from backend.api.main import app
    import jwt as pyjwt
    from datetime import datetime, timedelta, timezone

    # Mint a JWT WITHOUT the org_id claim (simulate a legacy token).
    secret = os.environ["JWT_SECRET"]
    now = datetime.now(timezone.utc)
    token_no_org = pyjwt.encode(
        {
            "sub": "legacy_user",
            # NOTE: no org_id claim — simulates a pre-P4-024 token.
            "iss": "drugos",
            "iat": now,
            "exp": now + timedelta(hours=1),
        },
        secret,
        algorithm="HS256",
    )

    client = TestClient(app)
    response = client.post(
        "/top-k",
        headers={"Authorization": f"Bearer {token_no_org}"},
        json={"drug": "aspirin", "disease": "headache", "k": 5},
    )
    # Teammate 8's verify_org_id returns 403 for missing org_id.
    assert response.status_code in (401, 403), (
        f"Expected 401 or 403 for JWT without org_id claim, got {response.status_code}: {response.text}"
    )
    detail = response.json()["detail"].lower()
    assert "org_id" in detail or "organization" in detail, (
        f"Expected 'org_id' or 'organization' in detail, got: {response.json()['detail']}"
    )


@pytest.mark.integration
def test_backend_ready_endpoint_probes_rl_service():
    """P4-024: /ready probes the RL service health endpoint.

    The /ready check (implemented by Teammate 11) returns 200 ONLY when
    ALL downstream dependencies are reachable; 503 when any fail. In the
    test env, DATABASE_URL is not set, so the database check fails and
    /ready returns 503 with status='degraded'. This test verifies the
    RL service probe itself works (rl_service=True in the checks) —
    it does NOT require the overall status to be 200.
    """
    from fastapi.testclient import TestClient
    from backend.api.main import app

    client = TestClient(app)

    # Mock the RL service /health as reachable.
    rl_health_resp = httpx.Response(
        200,
        json={"status": "ok"},
        request=httpx.Request("GET", "http://test-rl-service/health"),
    )
    with patch("backend.api.main.httpx.AsyncClient") as mock_client_cls:
        mock_client = AsyncMock()
        mock_client.__aenter__.return_value = mock_client
        mock_client.get.return_value = rl_health_resp
        mock_client_cls.return_value = mock_client

        response = client.get("/ready")

    # /ready returns 200 when ALL checks pass, 503 when any fail.
    # In the test env, DATABASE_URL is not set, so the DB check fails
    # and /ready returns 503. This is CORRECT behavior (Teammate 11's
    # implementation). We verify the RL service probe itself worked.
    assert response.status_code in (200, 503), (
        f"Expected 200 (all checks pass) or 503 (degraded), got {response.status_code}: {response.text}"
    )
    # The response body may be wrapped in {"detail": {...}} if Teammate 11's
    # implementation raises HTTPException for the 503 case. Handle both.
    body = response.json()
    if "detail" in body and isinstance(body["detail"], dict):
        body = body["detail"]
    assert "checks" in body, f"Expected 'checks' in response body, got: {body}"
    assert "rl_service" in body["checks"], f"Expected 'rl_service' in checks, got: {body['checks']}"
    # RL service probe should succeed (we mocked it as 200).
    assert body["checks"]["rl_service"] is True, (
        f"Expected rl_service=True (mocked 200), got: {body['checks']}"
    )


@pytest.mark.integration
def test_backend_top_k_returns_400_without_drug_or_disease(jwt_token):
    """P4-024: /top-k returns 400 when neither drug nor disease is provided.

    This is the existing input-validation behavior (preserved by the fix).
    The fix does NOT change this — it only replaces the placeholder
    return with a real RL service proxy.
    """
    from fastapi.testclient import TestClient
    from backend.api.main import app

    client = TestClient(app)
    response = client.post(
        "/top-k",
        headers={"Authorization": f"Bearer {jwt_token}"},
        json={"k": 10},  # no drug, no disease
    )
    assert response.status_code == 400
    assert "drug" in response.json()["detail"].lower() or "disease" in response.json()["detail"].lower()
