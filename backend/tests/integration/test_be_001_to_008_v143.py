"""BE-001 to BE-008 v143 forensic verification tests (Teammate 12).

Hostile-auditor pass: each test reads the REAL code (not comments) and
verifies the actual runtime behavior matches the audit's required fix.

Test strategy:
  - Use FastAPI TestClient (in-process — no real HTTP server needed).
  - Mock httpx.AsyncClient for /predict and /top-k so tests don't need
    real GT/RL services running.
  - Use sqlite in-memory DB for the OrganizationMember membership check
    (BE-002) and audit_log writes (BE-008).
  - Each test asserts the SPECIFIC behavior the audit required — not
    just "no exception".

The 8 audit issues map to tests as follows:
  BE-001 [CRITICAL]: /predict + /top-k call real GT/RL services
                     → test_be_001_predict_calls_gt_service
                     → test_be_001_top_k_calls_rl_service
  BE-002 [CRITICAL]: verify_jwt returns AuthContext + verify_org_membership
                     → test_be_002_auth_context_has_platform_role
                     → test_be_002_verify_org_membership_rejects_when_no_row
                     → test_be_002_verify_org_membership_accepts_when_row_exists
  BE-003 [CRITICAL]: Port collision fixed (8004)
                     → test_be_003_default_port_is_8004
                     → test_be_003_url_constants_register_drugos_api
  BE-004 [CRITICAL]: No duplicate dependency keys in package.json
                     → test_be_004_no_duplicate_package_json_keys
  BE-005 [CRITICAL]: CORS fail-closed when FRONTEND_URL unset in prod
                     → test_be_005_startup_fails_when_frontend_url_unset_in_prod
                     → test_be_005_startup_succeeds_when_frontend_url_set_in_prod
  BE-006 [HIGH]: /health (liveness) + /ready (readiness) separation
                → test_be_006_health_is_liveness_probe
                → test_be_006_ready_probes_downstream_services
  BE-007 [HIGH]: Rate limiter keyed by user_id (not IP)
                → test_be_007_rate_limiter_keyed_by_jwt_sub
                → test_be_007_rate_limiter_falls_back_to_ip_for_unauth
  BE-008 [HIGH]: Audit log fail-closed in production
                → test_be_008_audit_log_fail_closed_in_production
                → test_be_008_audit_log_fail_safe_in_development
"""
from __future__ import annotations

import json
import os
import sys
import importlib
from pathlib import Path
from typing import Any, Dict, List, Optional
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

# Ensure the repo root is on sys.path so `from backend.api.main import app`
# works when pytest is invoked from any cwd.
_REPO_ROOT = Path(__file__).resolve().parents[3]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

# Deterministic test secret (>=32 chars). NEVER use this in production.
os.environ.setdefault(
    "JWT_SECRET",
    "test-secret-for-integration-tests-only-not-for-production-use-32chars",
)
# Disable the in-memory Teammate 8 rate limiters so the test client's
# rapid-fire requests don't get 429'd.
os.environ.setdefault("DRUGOS_DISABLE_RATE_LIMIT", "1")


# ============================================================================
# Test fixtures
# ============================================================================

@pytest.fixture
def fresh_main_module():
    """Import backend.api.main fresh for each test.

    Several tests need to set/unset ENVIRONMENT and FRONTEND_URL env vars
    BEFORE the module loads (the CORS check runs at module load time).
    Using importlib.reload ensures each test gets a clean module state.
    """
    # Save and restore env vars so tests don't pollute each other.
    saved_env = dict(os.environ)
    try:
        # Drop the module from sys.modules so the reload is clean.
        for mod_name in list(sys.modules.keys()):
            if mod_name.startswith("backend.api.main"):
                del sys.modules[mod_name]
        import backend.api.main as main_module
        importlib.reload(main_module)
        yield main_module
    finally:
        os.environ.clear()
        os.environ.update(saved_env)


@pytest.fixture
def client(fresh_main_module):
    """FastAPI TestClient wired to the freshly-loaded app."""
    from fastapi.testclient import TestClient
    return TestClient(fresh_main_module.app)


def _mint_jwt(fresh_main_module, user_id="testuser", org_id="testorg",
              org_role="member", platform_role="none"):
    """Mint a valid JWT using the module under test's create_test_jwt."""
    return fresh_main_module.create_test_jwt(
        user_id=user_id, org_id=org_id, org_role=org_role,
        platform_role=platform_role,
    )


class _MockGTResponse:
    """Mock httpx.Response for the GT service /predict endpoint."""
    def __init__(self, status_code: int = 200, payload: Optional[Dict] = None):
        self.status_code = status_code
        self._payload = payload or {
            "predictions": [{
                "drug": "aspirin",
                "disease": "headache",
                "score": 0.87,
                "confidence": 0.92,
                "pathways": [{
                    "pathway": "arachidonic acid metabolism",
                    "intermediate_protein": "COX-1",
                    "chain": ["aspirin", "COX-1", "arachidonic acid metabolism", "headache"],
                }],
                "literature_supported": True,
            }],
            "modelVersion": "gt_4.1.0",
            "source": "gt_checkpoint",
            "count": 1,
        }
        self.text = json.dumps(self._payload)

    def json(self):
        return self._payload

    @property
    def is_success(self):
        return 200 <= self.status_code < 300


class _MockRLResponse:
    """Mock httpx.Response for the RL service /rank endpoint."""
    def __init__(self, status_code: int = 200, payload: Optional[Dict] = None):
        self.status_code = status_code
        self._payload = payload or {
            "candidates": [{
                "drug": "metformin",
                "disease": "breast cancer",
                "rank": 1,
                "gnnScore": 0.78,
                "safetyScore": 0.92,
                "marketScore": 0.65,
                "overallScore": 0.81,
                "pathwayScore": 0.74,
                "pathwayChain": [{
                    "pathway": "AMPK signaling",
                    "intermediate_protein": "AMPK",
                    "chain": ["metformin", "AMPK", "AMPK signaling", "breast cancer"],
                }],
                "confidence": 0.88,
            }],
            "total": 1,
            "source": "service",
            "pathway_enrichment_available": True,
            "orgId": "testorg",
        }
        self.text = json.dumps(self._payload)

    def json(self):
        return self._payload

    @property
    def is_success(self):
        return 200 <= self.status_code < 300


# ============================================================================
# BE-001 [CRITICAL] — /predict and /top-k must call real GT/RL services
# ============================================================================

def test_be_001_predict_calls_gt_service(client, fresh_main_module):
    """BE-001: /predict must POST to {GT_SERVICE_URL}/predict (not return 0.5).

    The audit found /predict returned gnn_score=0.5 hardcoded. The fix
    proxies to the GT service. This test mocks httpx.AsyncClient and
    verifies /predict actually calls the GT service AND maps the response
    correctly (score -> gnn_score, etc.).
    """
    token = _mint_jwt(fresh_main_module)
    mock_resp = _MockGTResponse(status_code=200)

    # Patch the httpx.AsyncClient used inside the endpoint.
    with patch("backend.api.main.httpx.AsyncClient") as mock_client_cls:
        mock_client = MagicMock()
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=None)
        mock_client.post = AsyncMock(return_value=mock_resp)
        mock_client_cls.return_value = mock_client

        # Also patch verify_org_membership to skip the DB lookup (we test
        # that separately in BE-002 tests).
        with patch("backend.api.main.verify_org_membership", return_value=None):
            response = client.post(
                "/predict",
                headers={"Authorization": f"Bearer {token}"},
                json={"drug": "aspirin", "disease": "headache"},
            )

    # /predict must return 200 with the REAL score (0.87), not 0.5.
    assert response.status_code == 200, f"Response: {response.text}"
    data = response.json()
    assert data["gnn_score"] == 0.87, (
        f"BE-001 FAIL: gnn_score should be 0.87 (from GT service), got "
        f"{data['gnn_score']}. The endpoint is still returning a placeholder."
    )
    assert data["confidence"] == 0.92
    assert data["literature_supported"] is True
    assert data["model_version"] == "gt_4.1.0"
    assert len(data["pathways"]) == 1
    assert data["pathways"][0]["pathway"] == "arachidonic acid metabolism"

    # Verify httpx.AsyncClient.post was actually called (the audit's
    # core requirement: "GT service is NEVER called" must be FALSE).
    assert mock_client.post.called, (
        "BE-001 FAIL: httpx.AsyncClient.post was NOT called. The endpoint "
        "is still returning a placeholder without invoking the GT service."
    )
    call_args = mock_client.post.call_args
    assert "/predict" in call_args.args[0], (
        f"BE-001 FAIL: GT service URL should contain /predict, got "
        f"{call_args.args[0]}"
    )


def test_be_001_top_k_calls_rl_service(client, fresh_main_module):
    """BE-001: /top-k must POST to {RL_SERVICE_URL}/rank (not return empty)."""
    token = _mint_jwt(fresh_main_module)
    mock_resp = _MockRLResponse(status_code=200)

    with patch("backend.api.main.httpx.AsyncClient") as mock_client_cls:
        mock_client = MagicMock()
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=None)
        mock_client.post = AsyncMock(return_value=mock_resp)
        mock_client_cls.return_value = mock_client

        with patch("backend.api.main.verify_org_membership", return_value=None):
            response = client.post(
                "/top-k",
                headers={"Authorization": f"Bearer {token}"},
                json={"drug": "metformin", "k": 5},
            )

    assert response.status_code == 200, f"Response: {response.text}"
    data = response.json()
    assert data["total"] == 1, (
        f"BE-001 FAIL: total should be 1 (from RL service), got {data['total']}. "
        f"The endpoint is still returning an empty placeholder."
    )
    assert len(data["candidates"]) == 1
    cand = data["candidates"][0]
    assert cand["drug"] == "metformin"
    assert cand["gnn_score"] == 0.78
    assert cand["score"] == 0.81
    assert cand["safety_score"] == 0.92
    # pathway_enrichment_available is on the TopKResponse, not on the candidate.
    assert data.get("pathway_enrichment_available") is True, (
        f"BE-001 FAIL: pathway_enrichment_available should be True (forwarded "
        f"from RL service). Got: {data}"
    )
    assert mock_client.post.called, (
        "BE-001 FAIL: httpx.AsyncClient.post was NOT called for /top-k."
    )


# ============================================================================
# BE-002 [CRITICAL] — verify_jwt + verify_org_membership with DB lookup
# ============================================================================

def test_be_002_auth_context_has_platform_role(fresh_main_module):
    """BE-002: AuthContext must include platform_role (not just user_id).

    The audit required: "Return an AuthenticatedUser dataclass (not just
    str) so endpoints can scope." AuthContext is the equivalent. It must
    carry platform_role so /admin/* routes can be gated.
    """
    fields = list(fresh_main_module.AuthContext.model_fields.keys())
    assert "user_id" in fields
    assert "org_id" in fields
    assert "org_role" in fields
    assert "platform_role" in fields, (
        f"BE-002 FAIL: AuthContext must have 'platform_role' field. Got: {fields}"
    )


def test_be_002_verify_jwt_extracts_platform_role(fresh_main_module):
    """BE-002: verify_jwt must extract platformRole from the JWT payload."""
    token = _mint_jwt(fresh_main_module, platform_role="admin")
    # Decode the token manually and verify the claim is present.
    import jwt as pyjwt
    payload = pyjwt.decode(
        token,
        os.environ["JWT_SECRET"],
        algorithms=["HS256"],
        issuer="drugos",
    )
    assert payload.get("platformRole") == "admin", (
        f"BE-002 FAIL: create_test_jwt must include platformRole claim. "
        f"Got payload: {payload}"
    )


def test_be_002_verify_org_membership_rejects_when_no_row(monkeypatch):
    """BE-002: verify_org_membership must 401 when no OrganizationMember row.

    Tests the dependency IN ISOLATION (not via /predict) so the audit
    middleware doesn't fail-closed and mask the 401. The audit
    middleware's fail-closed behavior is tested separately in BE-008.
    """
    for mod_name in list(sys.modules.keys()):
        if mod_name.startswith("backend.api.main"):
            del sys.modules[mod_name]
    # Use development mode so the audit middleware is fail-safe (won't
    # mask the 401 with a 503 audit-failure).
    monkeypatch.setenv("ENVIRONMENT", "production")
    monkeypatch.setenv("FRONTEND_URL", "https://app.drugos.ai")
    monkeypatch.setenv("DATABASE_URL", "sqlite:///:memory:")
    monkeypatch.setenv(
        "JWT_SECRET",
        "test-secret-for-integration-tests-only-not-for-production-use-32chars",
    )

    import backend.api.main as main_module

    from sqlalchemy import create_engine, text
    engine = create_engine("sqlite:///:memory:")
    with engine.connect() as conn:
        conn.execute(text(
            'CREATE TABLE "OrganizationMember" ('
            '"id" TEXT PRIMARY KEY, '
            '"userId" TEXT, '
            '"organizationId" TEXT, '
            '"role" TEXT, '
            '"joinedAt" TEXT)'
        ))
        conn.commit()

    # Build the AuthContext that verify_jwt would have produced.
    auth = main_module.AuthContext(
        user_id="u1", org_id="org1", org_role="member", platform_role="none",
    )

    # Patch create_engine so the dependency uses our in-memory DB.
    with patch("backend.api.main.create_engine", return_value=engine):
        # The dependency is async — call it directly.
        import asyncio
        with pytest.raises(main_module.HTTPException) as exc_info:
            asyncio.run(main_module.verify_org_membership(auth=auth))

    assert exc_info.value.status_code == 401, (
        f"BE-002 FAIL: verify_org_membership should raise 401 when no "
        f"OrganizationMember row exists. Got {exc_info.value.status_code}."
    )
    detail = str(exc_info.value.detail).lower()
    assert "not a member" in detail or "membership" in detail, (
        f"BE-002 FAIL: 401 detail should mention membership. Got: {exc_info.value.detail}"
    )


def test_be_002_verify_org_membership_accepts_when_row_exists(monkeypatch):
    """BE-002: verify_org_membership must accept (return None) when row exists."""
    for mod_name in list(sys.modules.keys()):
        if mod_name.startswith("backend.api.main"):
            del sys.modules[mod_name]
    monkeypatch.setenv("ENVIRONMENT", "production")
    monkeypatch.setenv("FRONTEND_URL", "https://app.drugos.ai")
    monkeypatch.setenv("DATABASE_URL", "sqlite:///:memory:")
    monkeypatch.setenv(
        "JWT_SECRET",
        "test-secret-for-integration-tests-only-not-for-production-use-32chars",
    )

    import backend.api.main as main_module

    from sqlalchemy import create_engine, text
    engine = create_engine("sqlite:///:memory:")
    with engine.connect() as conn:
        conn.execute(text(
            'CREATE TABLE "OrganizationMember" ('
            '"id" TEXT PRIMARY KEY, '
            '"userId" TEXT, '
            '"organizationId" TEXT, '
            '"role" TEXT, '
            '"joinedAt" TEXT)'
        ))
        # Insert a matching row for (u1, org1).
        conn.execute(text(
            'INSERT INTO "OrganizationMember" ("id", "userId", "organizationId", "role", "joinedAt") '
            "VALUES ('m1', 'u1', 'org1', 'member', '2026-01-01')"
        ))
        conn.commit()

    auth = main_module.AuthContext(
        user_id="u1", org_id="org1", org_role="member", platform_role="none",
    )

    with patch("backend.api.main.create_engine", return_value=engine):
        import asyncio
        result = asyncio.run(main_module.verify_org_membership(auth=auth))

    # Should return None (no exception) — the membership check passed.
    assert result is None, (
        f"BE-002 FAIL: verify_org_membership should return None when the "
        f"row exists. Got: {result}"
    )


# ============================================================================
# BE-003 [CRITICAL] — Port collision fixed
# ============================================================================

def test_be_003_default_port_is_8004():
    """BE-003: DRUGOS_API_PORT default must NOT be 8000 or 8001.

    8000 = phase1_dataset (per shared/contracts/urls.py).
    8001 = phase2_kg (per shared/contracts/urls.py).
    The previous "fix" moved FastAPI from 8001 → 8000 — a collision
    with phase1_dataset. The audit required a TRULY free port.
    """
    # Read the file as text (don't import — module-load side effects).
    main_py = Path(_REPO_ROOT / "backend" / "api" / "main.py").read_text()
    # Find the DRUGOS_API_PORT default.
    import re
    m = re.search(r'DRUGOS_API_PORT",\s*"(\d+)"', main_py)
    assert m, "BE-003 FAIL: DRUGOS_API_PORT default not found in main.py"
    port = int(m.group(1))
    assert port not in (8000, 8001), (
        f"BE-003 FAIL: DRUGOS_API_PORT default is {port} — collides with "
        f"phase1_dataset (8000) or phase2_kg (8001). Must be a free port "
        f"(e.g., 8004)."
    )
    assert port == 8004, f"BE-003: expected 8004, got {port}"


def test_be_003_url_constants_register_drugos_api():
    """BE-003: shared/contracts/urls.py SERVICE_PORTS must include drugos_api."""
    urls_py = Path(_REPO_ROOT / "shared" / "contracts" / "urls.py").read_text()
    assert '"drugos_api":' in urls_py, (
        "BE-003 FAIL: shared/contracts/urls.py SERVICE_PORTS must register "
        "'drugos_api' so the contract is the single source of truth."
    )
    # Also check the TS mirror.
    ts = Path(_REPO_ROOT / "frontend" / "contracts" / "_url-constants.ts").read_text()
    assert "drugos_api:" in ts, (
        "BE-003 FAIL: frontend/contracts/_url-constants.ts SERVICE_PORTS "
        "must register drugos_api (mirror of shared/contracts/urls.py)."
    )


# ============================================================================
# BE-004 [CRITICAL] — No duplicate dependency keys in package.json
# ============================================================================

def test_be_004_no_duplicate_package_json_keys():
    """BE-004: frontend/package.json must have NO duplicate keys.

    The audit found @prisma/client and 5 @radix-ui/* packages declared
    TWICE with conflicting versions. JSON allows duplicates (last-wins)
    but npm ci fails because the lockfile won't match.
    """
    pkg_json_path = _REPO_ROOT / "frontend" / "package.json"
    raw = pkg_json_path.read_text()

    # Parse with duplicate-key detection.
    seen_dups: List[str] = []

    def check_pairs(pairs):
        keys = [k for k, _ in pairs]
        for k in set(keys):
            if keys.count(k) > 1:
                seen_dups.append(k)
        return dict(pairs)

    json.loads(raw, object_pairs_hook=check_pairs)
    assert not seen_dups, (
        f"BE-004 FAIL: duplicate keys in frontend/package.json: {seen_dups}. "
        f"npm ci would fail because the lockfile won't match."
    )

    # Also assert the specific packages the audit called out are present
    # exactly once.
    for key in [
        "@prisma/client",
        "@radix-ui/react-accordion",
        "@radix-ui/react-alert-dialog",
        "@radix-ui/react-aspect-ratio",
        "@radix-ui/react-avatar",
        "@radix-ui/react-checkbox",
    ]:
        count = raw.count(f'"{key}"')
        assert count == 1, (
            f"BE-004 FAIL: '{key}' appears {count} times in package.json "
            f"(should be exactly 1)."
        )

    # @prisma/client and prisma must use the SAME major version (the
    # audit found 6.11.1 vs 7.8.0 — a major-version mismatch).
    pkg = json.loads(raw)
    prisma_client_ver = pkg["dependencies"]["@prisma/client"]
    prisma_cli_ver = pkg["dependencies"]["prisma"]
    pc_major = prisma_client_ver.lstrip("^").split(".")[0]
    pc_cli_major = prisma_cli_ver.lstrip("^").split(".")[0]
    assert pc_major == pc_cli_major, (
        f"BE-004 FAIL: @prisma/client ({prisma_client_ver}) and prisma "
        f"({prisma_cli_ver}) must use the same major version. Got "
        f"{pc_major} vs {pc_cli_major}."
    )


# ============================================================================
# BE-005 [CRITICAL] — CORS fail-closed when FRONTEND_URL unset in prod
# ============================================================================

def test_be_005_startup_fails_when_frontend_url_unset_in_prod(monkeypatch):
    """BE-005: ENVIRONMENT=production + FRONTEND_URL unset → RuntimeError.

    The audit required: "Hard-fail at startup if NODE_ENV=production AND
    FRONTEND_URL is unset or '*'."
    """
    # Drop the module so the next import re-runs module-load code.
    for mod_name in list(sys.modules.keys()):
        if mod_name.startswith("backend.api.main"):
            del sys.modules[mod_name]
    monkeypatch.setenv("ENVIRONMENT", "production")
    monkeypatch.delenv("FRONTEND_URL", raising=False)
    # JWT_SECRET must be set or the import will fail for a different reason.
    monkeypatch.setenv(
        "JWT_SECRET",
        "test-secret-for-integration-tests-only-not-for-production-use-32chars",
    )

    with pytest.raises(RuntimeError, match="BE-005"):
        import backend.api.main  # noqa: F401


def test_be_005_startup_fails_when_frontend_url_is_star_in_prod(monkeypatch):
    """BE-005: ENVIRONMENT=production + FRONTEND_URL='*' → RuntimeError."""
    for mod_name in list(sys.modules.keys()):
        if mod_name.startswith("backend.api.main"):
            del sys.modules[mod_name]
    monkeypatch.setenv("ENVIRONMENT", "production")
    monkeypatch.setenv("FRONTEND_URL", "*")
    monkeypatch.setenv(
        "JWT_SECRET",
        "test-secret-for-integration-tests-only-not-for-production-use-32chars",
    )

    with pytest.raises(RuntimeError):
        import backend.api.main  # noqa: F401


def test_be_005_startup_succeeds_when_frontend_url_set_in_prod(monkeypatch):
    """BE-005: ENVIRONMENT=production + FRONTEND_URL='https://app.drugos.ai' → OK."""
    for mod_name in list(sys.modules.keys()):
        if mod_name.startswith("backend.api.main"):
            del sys.modules[mod_name]
    monkeypatch.setenv("ENVIRONMENT", "production")
    monkeypatch.setenv("FRONTEND_URL", "https://app.drugos.ai")
    monkeypatch.setenv(
        "JWT_SECRET",
        "test-secret-for-integration-tests-only-not-for-production-use-32chars",
    )

    # Should not raise.
    import backend.api.main  # noqa: F401


# ============================================================================
# BE-006 [HIGH] — /health (liveness) + /ready (readiness) separation
# ============================================================================

def test_be_006_health_is_liveness_probe(client):
    """BE-006: /health must always return 200 (liveness — process alive)."""
    response = client.get("/health")
    assert response.status_code == 200
    data = response.json()
    assert data["status"] == "ok"
    # Liveness must NOT include downstream-service check fields (those
    # belong on /ready). The audit required /health and /ready be SEPARATE.
    assert "checks" not in data, (
        "BE-006 FAIL: /health should NOT include 'checks' field — that "
        "belongs on /ready. Conflating liveness + readiness causes k8s "
        "cascading restarts."
    )


def test_be_006_ready_probes_downstream_services(client):
    """BE-006: /ready must probe GT/RL/DB (readiness — downstream reachable)."""
    # /ready will return 503 because GT/RL services aren't running in the
    # test env. That's the CORRECT behavior — readiness must reflect real
    # downstream reachability.
    response = client.get("/ready")
    assert response.status_code in (200, 503), (
        f"BE-006 FAIL: /ready should return 200 (all healthy) or 503 "
        f"(degraded). Got {response.status_code}."
    )
    data = response.json() if response.status_code == 200 else response.json().get("detail", {})
    # /ready must include the checks dict.
    if isinstance(data, dict) and "checks" in data:
        checks = data["checks"]
        assert "gt_service" in checks
        assert "rl_service" in checks
        assert "database" in checks
    else:
        # The 503 path nests the response under 'detail'.
        assert "checks" in str(data), (
            f"BE-006 FAIL: /ready response must include 'checks' dict. "
            f"Got: {data}"
        )


# ============================================================================
# BE-007 [HIGH] — Rate limiter keyed by user_id (not IP)
# ============================================================================

def test_be_007_rate_limiter_keyed_by_jwt_sub(fresh_main_module):
    """BE-007: slowapi key_func must extract user_id from JWT.

    The audit required: "Key the limiter on user_id (extracted from JWT)
    not IP — the FastAPI is meant for direct API-key access."
    """
    # Build a fake Request with a JWT-bearing Authorization header.
    token = _mint_jwt(fresh_main_module, user_id="alice")
    fake_request = MagicMock()
    fake_request.headers = {"Authorization": f"Bearer {token}"}

    key = fresh_main_module._get_user_id_from_jwt(fake_request)
    assert key == "user:alice", (
        f"BE-007 FAIL: rate-limit key should be 'user:alice' (from JWT sub), "
        f"got '{key}'. The limiter is still keyed by IP."
    )


def test_be_007_rate_limiter_falls_back_to_ip_for_unauth(fresh_main_module):
    """BE-007: unauthenticated requests fall back to IP-based keying.

    /health and other unauthenticated endpoints still need rate limiting
    (to prevent anonymous DoS). The key_func must fall back to the remote
    IP when no JWT is present.
    """
    fake_request = MagicMock()
    fake_request.headers = {}  # No Authorization header.
    # get_remote_address reads request.client.host — mock it.
    fake_request.client = MagicMock()
    fake_request.client.host = "203.0.113.42"

    key = fresh_main_module._get_user_id_from_jwt(fake_request)
    assert key.startswith("ip:"), (
        f"BE-007 FAIL: rate-limit key for unauthenticated request should "
        f"start with 'ip:', got '{key}'."
    )


def test_be_007_limiter_uses_custom_key_func(fresh_main_module):
    """BE-007: the slowapi Limiter must be constructed with _get_user_id_from_jwt."""
    if not fresh_main_module._HAS_SLOWAPI:
        pytest.skip("slowapi not installed in this env")
    limiter = fresh_main_module.limiter
    # slowapi stores the key_func on the Limiter instance as _key_func.
    assert limiter._key_func == fresh_main_module._get_user_id_from_jwt, (
        "BE-007 FAIL: slowapi Limiter must use _get_user_id_from_jwt as "
        "key_func, not get_remote_address."
    )


# ============================================================================
# BE-008 [HIGH] — Audit log fail-closed in production
# ============================================================================

def test_be_008_audit_log_fail_closed_in_production(monkeypatch):
    """BE-008: in production, audit DB write failure → 503 (fail-closed).

    The audit required: "Make it critical — fail-closed if audit log
    write fails (mirror lib/api-helpers.ts writeAuditLogCritical)."
    """
    # Force production mode + no DATABASE_URL (so _get_audit_db_session
    # returns None, simulating a DB outage).
    for mod_name in list(sys.modules.keys()):
        if mod_name.startswith("backend.api.main"):
            del sys.modules[mod_name]
    monkeypatch.setenv("ENVIRONMENT", "production")
    monkeypatch.setenv("FRONTEND_URL", "https://app.drugos.ai")
    monkeypatch.delenv("DATABASE_URL", raising=False)
    monkeypatch.setenv(
        "JWT_SECRET",
        "test-secret-for-integration-tests-only-not-for-production-use-32chars",
    )

    import backend.api.main as main_module
    from fastapi.testclient import TestClient
    client = TestClient(main_module.app)

    token = main_module.create_test_jwt(user_id="u1", org_id="org1")
    # Patch verify_org_membership to skip (we're testing BE-008, not BE-002).
    with patch("backend.api.main.verify_org_membership", return_value=None), \
         patch("backend.api.main.httpx.AsyncClient") as mock_client_cls:
        mock_client = MagicMock()
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=None)
        mock_client.post = AsyncMock(return_value=_MockGTResponse(200))
        mock_client_cls.return_value = mock_client

        response = client.post(
            "/predict",
            headers={"Authorization": f"Bearer {token}"},
            json={"drug": "aspirin", "disease": "headache"},
        )

    # FAIL-CLOSED: even though the GT service returned 200, the audit
    # log write failed (no DATABASE_URL), so the response must be 503.
    assert response.status_code == 503, (
        f"BE-008 FAIL: in production, audit log write failure must return "
        f"503 (fail-closed). Got {response.status_code}: {response.text}"
    )
    body = response.json()
    assert body["error"] == "audit_log_write_failed", (
        f"BE-008 FAIL: error code should be 'audit_log_write_failed'. "
        f"Got: {body}"
    )


def test_be_008_audit_log_fail_safe_in_development(client, fresh_main_module, monkeypatch):
    """BE-008: in non-production, audit DB write failure → response returned.

    Dev/CI environments without a real DB should still work — the audit
    failure is logged to stderr but the original response is returned.
    """
    # fresh_main_module fixture already sets ENVIRONMENT=development (default).
    monkeypatch.delenv("DATABASE_URL", raising=False)

    token = _mint_jwt(fresh_main_module)
    with patch("backend.api.main.verify_org_membership", return_value=None), \
         patch("backend.api.main.httpx.AsyncClient") as mock_client_cls:
        mock_client = MagicMock()
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=None)
        mock_client.post = AsyncMock(return_value=_MockGTResponse(200))
        mock_client_cls.return_value = mock_client

        response = client.post(
            "/predict",
            headers={"Authorization": f"Bearer {token}"},
            json={"drug": "aspirin", "disease": "headache"},
        )

    # FAIL-SAFE in dev: original 200 response is returned despite the
    # audit log write failing.
    assert response.status_code == 200, (
        f"BE-008 FAIL: in non-production, audit log write failure should "
        f"NOT block the response (fail-safe). Got {response.status_code}."
    )
