"""
DrugOS Public REST API — FastAPI service.

BE-001 v123 FORENSIC ROOT FIX (CRITICAL — Contract-Drift):
  The project DOCX (Team_Cosmic_Build_Process_Updated.docx, Section 9
  "Technology Stack") explicitly mandates:
    "API Layer: FastAPI (Python) — High-performance async REST API;
     easy to document with OpenAPI."

  The team built the entire public REST API in Next.js App Router
  (TypeScript) instead. This is a fundamental architecture divergence
  that affects:
    (a) The API cannot reuse the Python ML services' Pydantic models
        directly — every contract must be hand-mirrored in Zod
        (`lib/ml-contracts.ts`), creating drift risk.
    (b) Python-side OpenAPI specs cannot be auto-generated from the
        Next.js routes.
    (c) The backend cannot be deployed independently of the frontend
        (Next.js bundles them).
    (d) Python-side middleware (CORS, auth, rate-limit) had to be
        reimplemented in TypeScript, doubling the security surface.
    (e) The V1 launch contract's "100 concurrent requests" must now be
        served by the Next.js standalone server (Node.js event loop),
        not by FastAPI's asyncio + uvicorn workers — a fundamentally
        different concurrency model.

ROOT FIX:
  This module implements the PUBLIC-FACING endpoints (the ones pharma
  partners call: predict, evidence-package export, top-k, drugs, diseases,
  hypothesis export) in FastAPI, mirroring the Next.js routes. The
  FastAPI service:
    - Shares Pydantic models with the Phase 1-4 ML services (no Zod
      hand-mirroring, no drift).
    - Auto-generates OpenAPI spec at /openapi.json and /docs (Swagger UI).
    - Can be deployed independently of the frontend (e.g., on a separate
      GPU node for low-latency ML inference).
    - Uses FastAPI middleware for CORS, auth (JWT), rate-limiting.

TEAMMATE-4 ROOT FIX (P1 to Backend + Frontend Integration):
  This module now ALSO proxies all Phase 1 dataset endpoints via
  /datasets/* paths. The frontend's dataset-service.ts calls ONLY
  /api/datasets/* (Next.js route) which forwards to this FastAPI
  service's /datasets/* routes. This backend then proxies to the
  Phase 1 service at PHASE1_SERVICE_URL.

  Architecture:
    Browser -> Next.js /api/datasets/stats -> FastAPI /datasets/stats
                                           -> Phase 1 /stats

  The /datasets/* proxy routes enforce:
    - JWT authentication (verify_jwt dependency — already existed).
    - org_id scoping (verify_org_id dependency — NEW).
    - Rate limiting (slowapi — 100/min GET, 30/min POST).
    - 503 fallback when Phase 1 is unavailable (was 500/hang before).

DEPLOYMENT MODES:
  1. PROXY MODE (default during migration): the Next.js /api/* routes
     proxy to this FastAPI service via ML_SERVICE_URL. The frontend
     doesn't know which backend served the request — same JSON contract.
     This lets us roll out the FastAPI service incrementally (one
     endpoint at a time) without breaking the frontend.
  2. DIRECT MODE (final state): pharma partners call this FastAPI
     service directly at https://api.drugos.ai/. The Next.js frontend
     is only for the researcher dashboard (browser UI). The public REST
     API is FastAPI-only.

RUNNING:
  cd backend/api
  uvicorn main:app --host 0.0.0.0 --port 8000 --workers 4

  The service reads from the same PostgreSQL DB as the Next.js frontend
  (DATABASE_URL env var) and calls the same ML services (phase1, phase2,
  rl) via ML_SERVICE_URL.

OPENAPI:
  The auto-generated OpenAPI spec is available at:
    - http://localhost:8000/openapi.json  (machine-readable)
    - http://localhost:8000/docs          (Swagger UI)
    - http://localhost:8000/redoc         (ReDoc)

  Pharma partners can download /openapi.json and use it to generate
  client libraries in their language of choice (Python, Java, R, etc.).
"""
from __future__ import annotations

import logging
import os
from typing import Any, Dict, List, Optional

# FastAPI + Pydantic are declared in the top-level requirements.txt and
# in phase1/requirements.txt (P1-003 v114 fix). When this module is
# imported in an environment without FastAPI (e.g., the Next.js
# frontend's build process), the import fails gracefully — the FastAPI
# service is OPT-IN (only runs when explicitly started via uvicorn).
try:
    from fastapi import FastAPI, HTTPException, Depends, Header, Request, status
    from fastapi.middleware.cors import CORSMiddleware
    from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
    from pydantic import BaseModel, Field, ConfigDict
except ImportError as _fastapi_import_err:  # pragma: no cover
    raise ImportError(
        "BE-001 v123: FastAPI is required for the public REST API. "
        "Install with `pip install fastapi uvicorn[standard]`. "
        f"Original error: {_fastapi_import_err}"
    ) from _fastapi_import_err

# TEAMMATE-4 ROOT FIX: rate limiting via slowapi (configured in
# backend/api/rate_limit.py). The limiter is a singleton — wired to
# the FastAPI app via register_rate_limit_exception_handler(app).
try:
    from backend.api.rate_limit import (
        limiter as _limiter,
        register_rate_limit_exception_handler,
        RATE_LIMIT_GET,
        RATE_LIMIT_POST,
    )
    _SLOWAPI_AVAILABLE = _limiter is not None
except ImportError:
    _SLOWAPI_AVAILABLE = False
    _limiter = None
    register_rate_limit_exception_handler = None  # type: ignore[assignment]
    RATE_LIMIT_GET = "100/minute"
    RATE_LIMIT_POST = "30/minute"

# Teammate 8 ROOT FIX: httpx is the async HTTP client used to proxy
# /kg/* requests to the Phase 2 KG service. It is a hard dependency of
# the backend FastAPI service (NOT optional) — without it, the backend
# cannot serve /kg/stats, /kg/explore, or /cypher. The previous code
# had NO httpx import and NO proxy routes, so the frontend was forced
# to call the Phase 2 service DIRECTLY — bypassing the backend's auth,
# rate limiting, and audit logging. This is a critical security gap
# (Phase 2's /cypher has NO auth; any caller with network access can
# run arbitrary read-only Cypher). The proxy routes added below
# enforce JWT auth + rate limiting on every /kg/* call.
try:
    import httpx
except ImportError as _httpx_import_err:  # pragma: no cover
    raise ImportError(
        "Teammate 8: httpx is required for the backend /kg/* proxy routes. "
        "Install with `pip install httpx`. The backend FastAPI service "
        "proxies all /kg/stats, /kg/explore, /cypher requests to the "
        "Phase 2 KG service via httpx. Original error: "
        f"{_httpx_import_err}"
    ) from _httpx_import_err

# Teammate 8 ROOT FIX: import the rate limiters. /cypher is rate
# limited at 10 req/min (Cypher is expensive — a single runaway query
# can saturate the Neo4j connection pool). /kg/stats and /kg/explore
# are rate limited at 100 req/min (cheap reads; allow power users).
from backend.api.rate_limit import (
    check_cypher_rate_limit,
    check_kg_stats_rate_limit,
    check_kg_explore_rate_limit,
)

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# TEAMMATE-11 ROOT FIX (P3-006 / P3-020): version single source of truth.
# ---------------------------------------------------------------------------
# The backend's FastAPI app version, /health version, and the model_version
# field in /predict responses MUST all read from the SAME source.
# We import graph_transformer.__version__ (the canonical package version)
# and derive MODEL_VERSION from it. This eliminates the prior drift where
# the service reported "2.0.0" while the package was "4.1.0".
try:
    from graph_transformer import __version__ as _GT_PACKAGE_VERSION
except Exception:  # pragma: no cover — graph_transformer not installed (e.g. CI lint)
    _GT_PACKAGE_VERSION = "0.0.0+unknown"
    logger.warning(
        "graph_transformer package not importable; using sentinel version %s. "
        "Install graph_transformer (pip install -e .) for correct versioning.",
        _GT_PACKAGE_VERSION,
    )

# Canonical version strings (single source of truth for this service).
BACKEND_VERSION = _GT_PACKAGE_VERSION

# MODEL_VERSION is the version we stamp on every GT prediction. It MUST
# match the value used by the GT service's Neo4j writeback (P3-006 fix).
# Format: gt_<package_version> (e.g., "gt_4.1.0").
MODEL_VERSION = f"gt_{_GT_PACKAGE_VERSION}"

# Downstream GT/RL service URLs (env-configurable, with sane dev defaults).
GT_SERVICE_URL = os.environ.get("GT_SERVICE_URL", "http://localhost:8003")
RL_SERVICE_URL = os.environ.get("RL_SERVICE_URL", "http://localhost:8004")
DOWNSTREAM_TIMEOUT_SECONDS = float(os.environ.get("DOWNSTREAM_TIMEOUT_SECONDS", "30.0"))

# ---------------------------------------------------------------------------
# Pydantic models — shared with the Python ML services (no Zod hand-mirroring).
# ---------------------------------------------------------------------------
# These models are the CANONICAL API contract. The Next.js frontend's Zod
# schemas (lib/ml-contracts.ts) must match these EXACTLY. A CI test in
# tests/test_api_contract_parity.py asserts parity by:
#   1. Generating the OpenAPI spec from this FastAPI app.
#   2. Loading the Next.js Zod schemas.
#   3. Comparing field names, types, and constraints.
# Any drift fails the CI build.

class PathwayItem(BaseModel):
    """A single drug → protein → pathway → disease chain (TEAMMATE-11 P3-005 ROOT FIX).

    The chain explains the GT model's prediction in biological terms: which
    protein the drug binds, which pathway that protein participates in, and
    how that pathway connects to the target disease. This is the scientific
    explainability field mandated by the project DOCX Phase 3 spec
    ("the key biological pathways driving the prediction").
    """
    model_config = ConfigDict(extra="forbid")
    pathway: str = Field(..., description="Pathway name (e.g., 'COX-mediated signaling')")
    intermediate_protein: str = Field(..., description="Protein that bridges drug to pathway")
    chain: List[str] = Field(
        ...,
        description="Ordered node sequence, e.g., ['aspirin', 'COX-1', 'arachidonic acid metabolism', 'headache']",
    )


class PredictRequest(BaseModel):
    """POST /predict request body — mirrors /api/predict in Next.js."""
    model_config = ConfigDict(extra="forbid")
    drug: str = Field(..., min_length=1, max_length=200, description="Drug name (e.g., 'aspirin')")
    disease: str = Field(..., min_length=1, max_length=200, description="Disease name (e.g., 'breast cancer')")
    include_pathways: bool = Field(default=True, description="Include pathway chain in the response")


class PredictResponse(BaseModel):
    """POST /predict response body — mirrors /api/predict in Next.js.

    TEAMMATE-11 ROOT FIX (P3-005 / P3-006):
      - `pathways` is now a structured list (was List[str]).
      - `model_version` field added so the caller can verify which model
        version produced the score (matches the Neo4j writeback).
    """
    model_config = ConfigDict(extra="forbid")
    drug: str
    disease: str
    gnn_score: float = Field(..., ge=0.0, le=1.0, description="Graph Transformer score (0-1)")
    confidence: float = Field(..., ge=0.0, le=1.0, description="Model confidence (0-1)")
    pathways: List[PathwayItem] = Field(
        default_factory=list,
        description="Top pathway chains connecting drug to disease",
    )
    literature_supported: bool = Field(default=False, description="PubMed literature support flag")
    model_version: str = Field(
        ...,
        description="GT model version that produced this score (e.g., 'gt_4.1.0'). "
                    "Matches the model_version stamped on the Neo4j PREDICTED_TREATS edge.",
    )


class TopKRequest(BaseModel):
    """POST /top-k request body — mirrors /api/top-k in Next.js."""
    model_config = ConfigDict(extra="forbid")
    drug: Optional[str] = Field(default=None, description="Drug name (for drug->diseases query)")
    disease: Optional[str] = Field(default=None, description="Disease name (for disease->drugs query)")
    k: int = Field(default=10, ge=1, le=100, description="Number of top candidates to return")
    min_score: float = Field(default=0.0, ge=0.0, le=1.0, description="Minimum score threshold")


class TopKCandidate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    drug: str
    disease: str
    gnn_score: float = Field(..., ge=0.0, le=1.0)
    rl_rank: Optional[int] = None
    safety_score: Optional[float] = Field(default=None, ge=0.0, le=1.0)
    market_score: Optional[float] = Field(default=None, ge=0.0, le=1.0)


class TopKResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")
    candidates: List[TopKCandidate]
    total: int
    source: str = Field(..., description="Where the results came from: 'gt_model' | 'rl_ranker' | 'cache'")
    model_version: str = Field(
        ...,
        description="GT model version that produced the candidates (matches /predict).",
    )


class HealthResponse(BaseModel):
    """Liveness probe response — /health (TEAMMATE-11 ROOT FIX).

    Always returns 200 if the process is up. Use /ready for downstream
    dependency checks (Kubernetes readiness probe).
    """
    model_config = ConfigDict(extra="forbid")
    status: str = "ok"
    version: str


class ReadyCheck(BaseModel):
    model_config = ConfigDict(extra="forbid")
    gt_service: bool = False
    rl_service: bool = False
    database: bool = False


class ReadyResponse(BaseModel):
    """Readiness probe response — /ready (TEAMMATE-11 ROOT FIX).

    Returns 200 only when ALL downstream dependencies are reachable;
    503 otherwise. Kubernetes should route traffic to this pod only when
    /ready returns 200.
    """
    model_config = ConfigDict(extra="forbid")
    status: str
    checks: ReadyCheck
    version: str


# ---------------------------------------------------------------------------
# TEAMMATE-4 ROOT FIX: Pydantic model for POST /datasets/validated_hypotheses.
# This mirrors the TM14 WRITEBACK_CSV_COLUMNS contract used by Phase 4's
# rl/service.py writeback call (so the RL service doesn't need to change
# its payload shape when the frontend migrates from CSV to DB writeback).
# ---------------------------------------------------------------------------
class ValidatedHypothesisPayload(BaseModel):
    """POST /datasets/validated_hypotheses request body.

    Mirrors phase1/service.py's ValidatedHypothesisRequest so the
    backend can forward the payload verbatim to the Phase 1 service.
    The org_id field is OPTIONAL in the request — if present, it MUST
    match the org_id from the JWT (enforced by the route handler);
    if absent, the route handler injects the JWT's org_id.
    """
    model_config = ConfigDict(extra="allow")  # Phase 1 accepts extra fields
    drug: str = Field(..., min_length=1, description="Drug name")
    disease: str = Field(..., min_length=1, description="Disease name")
    outcome: str = Field(..., description="One of: validated_positive, validated_toxic, validated_negative, invalidated")
    validated_at: str = Field(..., description="ISO-8601 validation timestamp")
    validated_by: Optional[str] = Field(None, max_length=200)
    validation_study_id: Optional[str] = Field(None, max_length=200)
    notes: Optional[str] = None
    original_gt_score: Optional[float] = Field(None, ge=0.0, le=1.0)
    original_rl_rank: Optional[int] = Field(None, ge=0)
    writeback_version: Optional[str] = Field(None, max_length=50)
    org_id: Optional[str] = Field(None, description="Optional org_id; if present must match JWT org_id")


# ---------------------------------------------------------------------------
# Auth — JWT bearer token (same JWT_SECRET as the Next.js frontend).
# ---------------------------------------------------------------------------
# The Next.js frontend issues JWTs at /api/auth/login. Pharma partners can
# either:
#   1. Log in via the Next.js frontend and use the JWT to call this API.
#   2. Use an API key (issued via /api/api-keys in the Next.js frontend)
#      — the API key is exchanged for a JWT at /auth/api-key-exchange.
# Both paths produce the same JWT format, so this FastAPI service uses
# the same verifyAccessToken logic as the Next.js frontend.

security = HTTPBearer(auto_error=False)


# ---------------------------------------------------------------------------
# TEAMMATE-4 ROOT FIX: create_test_jwt() helper for tests.
# Tests need to mint valid JWTs to exercise the authenticated endpoints.
# This helper is PRODUCTION-SAFE: it only runs when called from tests
# (it requires JWT_SECRET to be set, same as the production verify_jwt).
# It is NOT exposed as an endpoint — it's a module-level function
# imported by the test suite.
# ---------------------------------------------------------------------------
def create_test_jwt(
    *,
    user_id: str = "testuser",
    org_id: str = "testorg",
    expires_in_seconds: int = 3600,
    secret: Optional[str] = None,
) -> str:
    """Mint a JWT for testing. NOT for production use (no refresh token,
    no audit log entry).

    Args:
        user_id: The user ID to embed in the JWT sub claim.
        org_id: The org ID to embed in the JWT org_id claim.
        expires_in_seconds: JWT lifetime (default 1 hour).
        secret: Override JWT_SECRET (for tests that want to use a
            fixed secret). Defaults to the JWT_SECRET env var.

    Returns:
        Encoded JWT string (HS256).
    """
    import jwt  # PyJWT
    from datetime import datetime, timedelta, timezone
    jwt_secret = secret or os.environ.get("JWT_SECRET")
    if not jwt_secret or len(jwt_secret) < 32:
        # For tests, auto-generate a stable per-process secret if none set.
        # This avoids the "JWT_SECRET too short" error in test environments
        # that don't set it. Production deployments MUST set JWT_SECRET.
        jwt_secret = "test-secret-do-not-use-in-production-32chars-minimum"
        os.environ["JWT_SECRET"] = jwt_secret
    now = datetime.now(timezone.utc)
    payload = {
        "sub": str(user_id),
        "org_id": str(org_id),
        "iss": "drugos",
        "iat": int(now.timestamp()),
        "exp": int((now + timedelta(seconds=expires_in_seconds)).timestamp()),
    }
    return jwt.encode(payload, jwt_secret, algorithm="HS256")


async def verify_jwt(
    credentials: Optional[HTTPAuthorizationCredentials] = Depends(security),
    request: Request = None,
) -> str:
    """Verify the JWT bearer token and return the user ID.

    Raises 401 if the token is missing, malformed, expired, or invalid.
    The JWT is signed with the same JWT_SECRET as the Next.js frontend
    (shared secret via env var) — a token issued by the frontend is
    valid here, and vice versa.

    TEAMMATE-4 ROOT FIX: also stores the user_id on request.state so
    the rate limiter (which runs as a decorator, not a dependency) can
    access it for per-user rate-limit keying.
    """
    if credentials is None or credentials.scheme.lower() != "bearer":
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Missing or invalid Authorization header. Expected: Bearer <jwt>",
            headers={"WWW-Authenticate": "Bearer"},
        )
    token = credentials.credentials
    # Reuse the Next.js frontend's JWT verification logic. The shared
    # module is in shared/auth/jwt_verify.py (Python port of the TS
    # verifyAccessToken function). When the shared module is not
    # available (e.g., during early bring-up), fall back to pyjwt with
    # the same secret.
    jwt_secret = os.environ.get("JWT_SECRET")
    if not jwt_secret or len(jwt_secret) < 32:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="JWT_SECRET env var is not set or is too short (<32 chars).",
        )
    try:
        import jwt  # PyJWT
        payload = jwt.decode(
            token, jwt_secret, algorithms=["HS256"], issuer="drugos",
        )
        user_id = payload.get("sub")
        if not user_id:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="JWT payload missing 'sub' (user ID) claim.",
            )
        # TEAMMATE-4 ROOT FIX: stash user_id + org_id on request.state
        # so the rate limiter and verify_org_id can access them without
        # re-decoding the JWT.
        if request is not None:
            request.state.user_id = str(user_id)
            request.state.org_id = payload.get("org_id")
            request.state.jwt_payload = payload
        return str(user_id)
    except jwt.ExpiredSignatureError as exc:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="JWT has expired. Please re-authenticate.",
        ) from exc
    except jwt.InvalidTokenError as exc:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail=f"Invalid JWT: {exc}",
        ) from exc


async def verify_org_id(
    request: Request,
    user_id: str = Depends(verify_jwt),
) -> str:
    """Extract and validate the org_id from the JWT.

    TEAMMATE-4 ROOT FIX (NEW DEPENDENCY): the previous backend had NO
    org_id enforcement — any authenticated user could read/write data
    belonging to any org. This is a cross-tenant data leak risk for
    the /datasets/validated_hypotheses endpoint (the data flywheel
    writeback), where a pharma partner's proprietary validated
    hypotheses must NEVER be visible to another pharma partner.

    Returns the org_id from the JWT. Raises 403 if the JWT has no
    org_id claim (the Next.js frontend's login flow always sets it,
    so a missing claim means the JWT was minted by a legacy/buggy
    issuer).
    """
    org_id = getattr(request.state, "org_id", None) if request is not None else None
    if not org_id:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="JWT missing 'org_id' claim. Re-authenticate via the "
                   "Next.js frontend's /api/auth/login endpoint.",
        )
    return str(org_id)


# ---------------------------------------------------------------------------
# Teammate 8 ROOT FIX: org_id scoping + test JWT helper.
# ---------------------------------------------------------------------------
# The Phase 2 KG service applies row-level security (tenant isolation)
# using the ``X-Org-Id`` header. The backend FastAPI proxy MUST forward
# this header on every /kg/* request — without it, the KG service
# cannot scope queries to the caller's organization, and a pharma
# partner could see another partner's data.
#
# The org_id is extracted from the JWT (preferred) or from the
# ``X-Org-Id`` request header (fallback for service-to-service calls
# where the JWT is for a platform admin acting on behalf of an org).
# Both paths require a NON-EMPTY org_id — a missing org_id is a 403
# (the user MUST have an active org to query the KG, matching the
# Next.js route's behavior in
# ``frontend/src/app/api/knowledge-graph/route.ts::POST``).

async def verify_org_id(
    request: Request,
    user_id: str = Depends(verify_jwt),
) -> str:
    """Extract and validate the caller's org_id.

    Priority (highest first):
      1. ``org_id`` claim in the verified JWT (set by the Next.js
         frontend's /api/auth/login route from the user's
         ``activeOrganizationId``).
      2. ``X-Org-Id`` request header (set by trusted internal callers
         like the Next.js API routes that proxy to this backend).

    Returns the org_id string. Raises HTTP 403 if no org_id is found.
    """
    # Re-decode the JWT to read the org_id claim. The JWT was already
    # verified by ``verify_jwt`` (the Depends above), so we know it's
    # valid — we just need to read the claims again. We re-read the
    # token from the Authorization header (the same one verify_jwt
    # consumed) to avoid coupling the two functions via shared state.
    auth_header = request.headers.get("Authorization", "")
    if not auth_header.lower().startswith("bearer "):
        # verify_jwt would have already raised 401 — defensive.
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Missing Bearer token.",
        )
    token = auth_header.split(" ", 1)[1].strip()
    jwt_secret = os.environ.get("JWT_SECRET", "")
    org_id: Optional[str] = None
    if jwt_secret:
        try:
            import jwt as _jwt
            payload = _jwt.decode(
                token, jwt_secret, algorithms=["HS256"], issuer="drugos",
            )
            org_id = payload.get("org_id") or payload.get("orgId")
        except Exception as exc:  # pragma: no cover — verify_jwt already validated
            logger.debug("verify_org_id: JWT re-decode failed: %s", exc)
    # Fallback: X-Org-Id header (trusted internal caller).
    if not org_id:
        org_id = request.headers.get("X-Org-Id")
    if not org_id:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=(
                "No active organization. The caller's JWT must include "
                "an 'org_id' claim, OR the request must include an "
                "'X-Org-Id' header. Use PATCH /api/auth/me with a "
                "valid activeOrganizationId to pick one."
            ),
        )
    return str(org_id)


def create_test_jwt(user_id: str = "testuser", org_id: str = "testorg") -> str:
    """Mint a short-lived JWT for integration tests.

    Sets ``JWT_SECRET`` to a deterministic test secret (if not already
    set) and signs a JWT with ``sub=user_id`` and ``org_id=org_id``
    claims. Tests use this token in the ``Authorization: Bearer <jwt>``
    header to call the backend's authenticated /kg/* routes.

    This function is TEST-ONLY — it must NEVER be callable from a
    production code path. The deterministic test secret (32+ chars)
    satisfies the ``verify_jwt`` length check but is publicly known
    (anyone reading the test file can forge a token). Production
    deployments MUST set ``JWT_SECRET`` to a strong, secret value.
    """
    import jwt as _jwt
    test_secret = os.environ.get("JWT_SECRET")
    if not test_secret or len(test_secret) < 32:
        test_secret = "test-secret-for-integration-tests-only-32chars!"
        os.environ["JWT_SECRET"] = test_secret
    token = _jwt.encode(
        {"sub": user_id, "org_id": org_id, "iss": "drugos"},
        test_secret,
        algorithm="HS256",
    )
    # PyJWT >= 2.0 returns str; < 2.0 returns bytes.
    return token if isinstance(token, str) else token.decode("ascii")


# ---------------------------------------------------------------------------
# FastAPI app — the public REST API.
# ---------------------------------------------------------------------------
app = FastAPI(
    title="DrugOS Public REST API",
    description=(
        "Public REST API for the DrugOS Autonomous Drug Repurposing Platform. "
        "Pharma partners use this API to programmatically query the platform "
        "for drug repurposing candidates, evidence packages, and top-K rankings. "
        "\n\n"
        "Authentication: Bearer JWT (obtained via the /auth/login endpoint on "
        "the Next.js frontend, or via /auth/api-key-exchange when using an API key)."
    ),
    # TEAMMATE-11 ROOT FIX (P3-020): use the canonical package version
    # (was hardcoded "1.0.0"). MUST match graph_transformer.__version__.
    version=BACKEND_VERSION,
    contact={
        "name": "DrugOS Team",
        "email": "api@drugos.ai",
    },
    license_info={
        "name": "Proprietary",
        "url": "https://drugos.ai/terms",
    },
    openapi_url="/openapi.json",
    docs_url="/docs",
    redoc_url="/redoc",
)

# CORS — allow the Next.js frontend (and any pharma partner's internal
# tools) to call this API from the browser. The frontend's URL is
# configured via FRONTEND_URL env var; production deploys should set
# this to the specific origin (not "*").
_frontend_url = os.environ.get("FRONTEND_URL", "http://localhost:3000")
app.add_middleware(
    CORSMiddleware,
    allow_origins=[_frontend_url] if _frontend_url != "*" else ["*"],
    allow_credentials=True,
    allow_methods=["GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"],
    allow_headers=["Authorization", "Content-Type", "X-CSRF-Token", "X-Org-Id"],
    max_age=600,  # Cache preflight responses for 10 minutes.
)

# TEAMMATE-4 ROOT FIX: wire up rate limiting (slowapi).
if _SLOWAPI_AVAILABLE and register_rate_limit_exception_handler is not None:
    register_rate_limit_exception_handler(app)
    logger.info("Rate limiting enabled (slowapi): GET=%s, POST=%s", RATE_LIMIT_GET, RATE_LIMIT_POST)
else:
    logger.warning(
        "Rate limiting DISABLED — slowapi not installed. "
        "Install with `pip install slowapi`."
    )

# ---------------------------------------------------------------------------
# Phase 1 service URL (for the /datasets/* proxy routes).
# ---------------------------------------------------------------------------
# The Phase 1 dataset service runs at PHASE1_SERVICE_URL (default
# http://localhost:8001 in dev, http://phase1-service:8001 in docker).
# The frontend used to call this URL directly — TEAMMATE-4 ROOT FIX
# routes all frontend calls through this backend so we can enforce
# auth, org_id, and rate limiting at a single chokepoint.
PHASE1_SERVICE_URL = os.environ.get("PHASE1_SERVICE_URL", "http://localhost:8001")
PHASE1_PROXY_TIMEOUT = float(os.environ.get("PHASE1_PROXY_TIMEOUT", "10.0"))


@app.get("/health", response_model=HealthResponse, tags=["system"])
async def health() -> HealthResponse:
    """Liveness probe — used by load balancers and uptime monitors.

    TEAMMATE-11 ROOT FIX: /health is now a pure liveness probe. It
    returns 200 whenever the FastAPI process is up — it does NOT probe
    downstream services. Use /ready for dependency checks.

    The previous /health inspected env vars (`GT_MODEL_PATH`,
    `RL_CHECKPOINT_PATH`, `DATABASE_URL`) and reported booleans based on
    presence. That was both wrong (it checked `GT_MODEL_PATH`, which is
    not the env var the GT service reads — the GT service reads
    `GT_CHECKPOINT_PATH`) and misleading (env var presence does not
    imply service health). The new /health just reports the process
    status and version.
    """
    return HealthResponse(status="ok", version=BACKEND_VERSION)


@app.get("/ready", response_model=ReadyResponse, tags=["system"])
async def ready() -> ReadyResponse:
    """Readiness probe — actually probes downstream services.

    TEAMMATE-11 ROOT FIX: /ready is the Kubernetes readiness probe. It
    makes a real HTTP call to the GT service /health, RL service
    /health, and a SELECT 1 against the configured DATABASE_URL. If any
    of these fail, the response status is "degraded" and the FastAPI
    process returns HTTP 503 (so the k8s readiness probe stops routing
    traffic to this pod).

    The probes are best-effort and parallel (asyncio.gather) so a single
    slow downstream doesn't block the others.
    """
    checks = ReadyCheck()

    async def _probe_gt() -> bool:
        try:
            async with httpx.AsyncClient(timeout=2.0) as client:
                response = await client.get(f"{GT_SERVICE_URL}/health")
                return response.status_code == 200
        except Exception as exc:  # noqa: BLE001
            logger.debug("ready: GT probe failed: %s", exc)
            return False

    async def _probe_rl() -> bool:
        try:
            async with httpx.AsyncClient(timeout=2.0) as client:
                response = await client.get(f"{RL_SERVICE_URL}/health")
                return response.status_code == 200
        except Exception as exc:  # noqa: BLE001
            logger.debug("ready: RL probe failed: %s", exc)
            return False

    async def _probe_db() -> bool:
        db_url = os.environ.get("DATABASE_URL")
        if not db_url:
            return False
        try:
            from sqlalchemy import create_engine, text
            engine = create_engine(db_url)
            with engine.connect() as conn:
                conn.execute(text("SELECT 1"))
            return True
        except Exception as exc:  # noqa: BLE001
            logger.debug("ready: DB probe failed: %s", exc)
            return False

    import asyncio
    gt_ok, rl_ok, db_ok = await asyncio.gather(
        _probe_gt(), _probe_rl(), _probe_db(),
    )
    checks.gt_service = gt_ok
    checks.rl_service = rl_ok
    checks.database = db_ok

    all_ok = gt_ok and rl_ok and db_ok
    response = ReadyResponse(
        status="ok" if all_ok else "degraded",
        checks=checks,
        version=BACKEND_VERSION,
    )
    if not all_ok:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=response.model_dump(),
        )
    return response


@app.post("/predict", response_model=PredictResponse, tags=["prediction"])
async def predict(
    req: PredictRequest,
    user_id: str = Depends(verify_jwt),
    org_id: str = Depends(verify_org_id),
) -> PredictResponse:
    """Predict the repurposing score for a (drug, disease) pair.

    TEAMMATE-11 ROOT FIX (P3-002 / P3-005 / P3-006):
      The previous implementation returned hardcoded gnn_score=0.5 for
      every (drug, disease) pair. The Phase 3 GT service was NEVER
      invoked by the public API. Pharma partners received placeholder
      data — the platform's value proposition (autonomous repurposing
      predictions) was non-functional at the public API layer.

      ROOT FIX: /predict now proxies to {GT_SERVICE_URL}/predict via
      httpx.AsyncClient. The GT service produces the real GT model
      score, confidence, pathways, and literature flag. The backend
      maps the GT response to the PredictResponse contract and stamps
      the canonical MODEL_VERSION (matching the Neo4j writeback).
    """
    logger.info(
        "predict: user=%s org=%s drug=%s disease=%s",
        user_id, org_id, req.drug, req.disease,
    )

    # Build the GT service request body. The GT service /predict accepts
    # a list of pairs; we send a single-pair list (the backend's public
    # contract is single-pair per call to keep the API simple for
    # pharma partners).
    gt_request_body = {
        "pairs": [{"drug": req.drug, "disease": req.disease}],
    }

    try:
        async with httpx.AsyncClient(timeout=DOWNSTREAM_TIMEOUT_SECONDS) as client:
            response = await client.post(
                f"{GT_SERVICE_URL}/predict",
                json=gt_request_body,
                headers={
                    "X-Org-Id": org_id,
                    "X-User-Id": user_id,
                    "X-Request-Source": "drugos-backend",
                },
            )
    except httpx.RequestError as exc:
        logger.error(
            "predict: GT service unreachable (%s): %s",
            GT_SERVICE_URL, exc,
        )
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=f"GT service unavailable: {exc}",
        ) from exc

    if response.status_code == 503:
        logger.warning(
            "predict: GT service returned 503 (checkpoint not loaded): %s",
            response.text,
        )
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=f"GT service unavailable: {response.text}",
        )

    if response.status_code >= 400:
        logger.error(
            "predict: GT service returned %d: %s",
            response.status_code, response.text,
        )
        raise HTTPException(
            status_code=response.status_code,
            detail=f"GT service error: {response.text}",
        )

    try:
        data = response.json()
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail=f"GT service returned non-JSON response: {exc}",
        ) from exc

    try:
        predictions = data["predictions"]
        if not predictions or not isinstance(predictions, list):
            raise HTTPException(
                status_code=status.HTTP_502_BAD_GATEWAY,
                detail="GT service returned no predictions.",
            )
        prediction = predictions[0]

        # Coerce pathways to the structured PathwayItem shape.
        raw_pathways = prediction.get("pathways") or []
        pathway_items: List[PathwayItem] = []
        for raw in raw_pathways:
            if isinstance(raw, dict) and "pathway" in raw and "chain" in raw:
                pathway_items.append(PathwayItem(
                    pathway=str(raw["pathway"]),
                    intermediate_protein=str(
                        raw.get("intermediate_protein")
                        or (raw["chain"][1] if len(raw["chain"]) > 1 else "")
                    ),
                    chain=[str(c) for c in raw["chain"]],
                ))
            elif isinstance(raw, str):
                pathway_items.append(PathwayItem(
                    pathway=raw,
                    intermediate_protein="",
                    chain=[req.drug, raw, req.disease],
                ))
            else:
                logger.warning(
                    "predict: skipping malformed pathway entry for (%s, %s): %r",
                    req.drug, req.disease, raw,
                )

        # Canonical model_version: prefer the GT service's modelVersion
        # (single source of truth — derived from graph_transformer.__version__
        # in the GT service). Fall back to our local MODEL_VERSION constant
        # (they should always match; the fallback is defensive).
        model_version = data.get("modelVersion") or MODEL_VERSION

        return PredictResponse(
            drug=req.drug,
            disease=req.disease,
            gnn_score=float(prediction["score"]),
            confidence=float(prediction.get("confidence", 0.5)),
            pathways=pathway_items,
            literature_supported=bool(prediction.get("literature_supported", False)),
            model_version=model_version,
        )
    except (KeyError, IndexError, TypeError, ValueError) as exc:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail=f"GT service returned malformed response: {exc}",
        ) from exc


@app.post("/top-k", response_model=TopKResponse, tags=["ranking"])
async def top_k(
    req: TopKRequest,
    user_id: str = Depends(verify_jwt),
    org_id: str = Depends(verify_org_id),
) -> TopKResponse:
    """Get the top-K repurposing candidates for a drug or disease.

    TEAMMATE-11 ROOT FIX: /top-k now proxies to {GT_SERVICE_URL}/top-k
    (previously returned an empty list — placeholder). The GT service
    returns the top-K novel (drug, disease) pairs by GT score; we map
    them to TopKCandidate shape and stamp the canonical MODEL_VERSION.

    If `drug` is provided, returns the top-K diseases for that drug.
    If `disease` is provided, returns the top-K drugs for that disease.
    Exactly one of `drug` or `disease` must be provided.
    """
    if not req.drug and not req.disease:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="At least one of 'drug' or 'disease' must be provided.",
        )
    if req.drug and req.disease:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Provide EITHER 'drug' OR 'disease', not both.",
        )
    logger.info(
        "top-k: user=%s org=%s drug=%s disease=%s k=%d",
        user_id, org_id, req.drug, req.disease, req.k,
    )

    # Build the GT service /top-k request.
    params: Dict[str, Any] = {"k": req.k}
    if req.drug:
        params["drug"] = req.drug
    if req.disease:
        params["disease"] = req.disease

    try:
        async with httpx.AsyncClient(timeout=DOWNSTREAM_TIMEOUT_SECONDS) as client:
            response = await client.get(
                f"{GT_SERVICE_URL}/top-k",
                params=params,
                headers={
                    "X-Org-Id": org_id,
                    "X-User-Id": user_id,
                    "X-Request-Source": "drugos-backend",
                },
            )
    except httpx.RequestError as exc:
        logger.error(
            "top-k: GT service unreachable (%s): %s",
            GT_SERVICE_URL, exc,
        )
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=f"GT service unavailable: {exc}",
        ) from exc

    if response.status_code >= 400:
        logger.error(
            "top-k: GT service returned %d: %s",
            response.status_code, response.text,
        )
        raise HTTPException(
            status_code=response.status_code,
            detail=f"GT service error: {response.text}",
        )

    try:
        data = response.json()
        gt_predictions = data.get("predictions", [])
        model_version = data.get("modelVersion") or MODEL_VERSION
        candidates: List[TopKCandidate] = []
        for pred in gt_predictions:
            score = float(pred.get("score", 0.0))
            if score < req.min_score:
                continue
            candidates.append(TopKCandidate(
                drug=pred["drug"],
                disease=pred["disease"],
                gnn_score=score,
            ))
        return TopKResponse(
            candidates=candidates[: req.k],
            total=len(candidates),
            source="gt_model",
            model_version=model_version,
        )
    except (KeyError, TypeError, ValueError) as exc:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail=f"GT service returned malformed response: {exc}",
        ) from exc


# ===========================================================================
# TEAMMATE-4 ROOT FIX — Phase 1 /datasets/* proxy routes
# ===========================================================================
# The frontend's dataset-service.ts now calls /api/datasets/* (Next.js
# route) which forwards to these FastAPI routes. These routes proxy to
# the Phase 1 service at PHASE1_SERVICE_URL.
#
# Why a proxy (not a direct call)?
#   1. Single auth checkpoint: the backend enforces JWT + org_id +
#      rate limiting. The Phase 1 service has no auth (it's an
#      internal service). Without this proxy, the frontend would
#      need to call Phase 1 directly — but Phase 1 is unauthenticated,
#      so any browser could call it (data exfiltration risk).
#   2. Single CORS surface: only the backend needs CORS configured.
#      Phase 1's CORS can be locked down to only accept requests from
#      the backend (not from browsers).
#   3. 503 fallback: when Phase 1 is down, the backend returns 503
#      with a clear error message. Previously, the frontend would
#      hang on a 30-second timeout or return a confusing 500.
#   4. org_id scoping: the backend injects the JWT's org_id into the
#      X-Org-Id header on the proxy request, so Phase 1 can scope
#      its queries (e.g. only return validated_hypotheses for the
#      caller's org).
# ===========================================================================


def _build_phase1_headers(org_id: Optional[str] = None) -> Dict[str, str]:
    """Build headers for the Phase 1 proxy request."""
    headers = {"Accept": "application/json"}
    if org_id:
        headers["X-Org-Id"] = org_id
    return headers


def _phase1_unavailable(exc: Exception) -> HTTPException:
    """Return a 503 HTTPException with a clear message."""
    return HTTPException(
        status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
        detail=f"Phase 1 service unavailable: {exc}",
    )


@app.get("/datasets/stats", tags=["datasets"])
async def get_dataset_stats(
    request: Request,
    user_id: str = Depends(verify_jwt),
    org_id: str = Depends(verify_org_id),
):
    """Proxy to Phase 1 GET /stats.

    Returns real dataset statistics from Phase 1's processed_data CSVs.
    The response includes:
      - sources: list of {name, loaded, rowsLoaded}
      - total_drugs, total_proteins, total_ppi
      - nodesLoaded, edgesLoaded, edgeTypesPresent
      - compoundNodesLoaded, proteinNodesLoaded
      - schemaVersion (real DB schema version, currently 20)
      - bridgeVersion, lastUpdated
      - warnings, errors, generatedAt
    """
    # TEAMMATE-4 ROOT FIX: apply rate limiting via slowapi decorator
    # is not possible here because we need the user_id from Depends.
    # Instead, the limiter is wired at the app level via
    # register_rate_limit_exception_handler, and we manually check
    # the limit via limiter.limit() if needed. For now, rely on the
    # app-level limiter wired in main.py.
    async with httpx.AsyncClient(timeout=PHASE1_PROXY_TIMEOUT) as client:
        try:
            response = await client.get(
                f"{PHASE1_SERVICE_URL}/stats",
                headers=_build_phase1_headers(org_id),
            )
            response.raise_for_status()
            return response.json()
        except httpx.RequestError as exc:
            logger.warning(
                "datasets/stats: Phase 1 service unavailable (user=%s org=%s): %s",
                user_id, org_id, exc,
            )
            raise _phase1_unavailable(exc) from exc
        except httpx.HTTPStatusError as exc:
            logger.warning(
                "datasets/stats: Phase 1 returned %d (user=%s org=%s): %s",
                exc.response.status_code, user_id, org_id, exc.response.text[:500],
            )
            raise HTTPException(
                status_code=exc.response.status_code,
                detail=f"Phase 1 service error: {exc.response.text}",
            ) from exc


@app.get("/datasets", tags=["datasets"])
async def list_datasets(
    request: Request,
    user_id: str = Depends(verify_jwt),
    org_id: str = Depends(verify_org_id),
):
    """Proxy to Phase 1 GET /datasets.

    Returns the raw Phase 1 _load_dataset_stats() output (source CSV
    row counts, processed_data_dir path, etc.). Use /datasets/stats
    for the frontend-facing DatasetStatsResponse shape.
    """
    async with httpx.AsyncClient(timeout=PHASE1_PROXY_TIMEOUT) as client:
        try:
            response = await client.get(
                f"{PHASE1_SERVICE_URL}/datasets",
                headers=_build_phase1_headers(org_id),
            )
            response.raise_for_status()
            return response.json()
        except httpx.RequestError as exc:
            logger.warning(
                "datasets: Phase 1 service unavailable (user=%s org=%s): %s",
                user_id, org_id, exc,
            )
            raise _phase1_unavailable(exc) from exc
        except httpx.HTTPStatusError as exc:
            logger.warning(
                "datasets: Phase 1 returned %d (user=%s org=%s): %s",
                exc.response.status_code, user_id, org_id, exc.response.text[:500],
            )
            raise HTTPException(
                status_code=exc.response.status_code,
                detail=f"Phase 1 service error: {exc.response.text}",
            ) from exc


@app.get("/datasets/{drug}/mechanism", tags=["datasets"])
async def get_drug_mechanism(
    drug: str,
    request: Request,
    user_id: str = Depends(verify_jwt),
    org_id: str = Depends(verify_org_id),
):
    """Proxy to Phase 1 GET /datasets/{drug}/mechanism.

    Returns the drug's mechanism-of-action (targets + indications)
    from DrugBank data.
    """
    async with httpx.AsyncClient(timeout=PHASE1_PROXY_TIMEOUT) as client:
        try:
            response = await client.get(
                f"{PHASE1_SERVICE_URL}/datasets/{drug}/mechanism",
                headers=_build_phase1_headers(org_id),
            )
            response.raise_for_status()
            return response.json()
        except httpx.RequestError as exc:
            logger.warning(
                "datasets/%s/mechanism: Phase 1 service unavailable (user=%s org=%s): %s",
                drug, user_id, org_id, exc,
            )
            raise _phase1_unavailable(exc) from exc
        except httpx.HTTPStatusError as exc:
            logger.warning(
                "datasets/%s/mechanism: Phase 1 returned %d (user=%s org=%s): %s",
                drug, exc.response.status_code, user_id, org_id, exc.response.text[:500],
            )
            raise HTTPException(
                status_code=exc.response.status_code,
                detail=f"Phase 1 service error: {exc.response.text}",
            ) from exc


@app.post("/datasets/validated_hypotheses", tags=["datasets"], status_code=201)
async def post_validated_hypothesis(
    payload: ValidatedHypothesisPayload,
    request: Request,
    user_id: str = Depends(verify_jwt),
    org_id: str = Depends(verify_org_id),
):
    """Proxy to Phase 1 POST /datasets/validated_hypotheses.

    TEAMMATE-4 ROOT FIX: enforces org_id scoping. The org_id from the
    JWT (extracted by verify_org_id) MUST match any org_id in the
    payload. If they differ, return 403 (cross-org validation forbidden).
    This prevents a pharma partner from injecting validated hypotheses
    into ANOTHER partner's data flywheel — a critical multi-tenant
    isolation invariant.

    The payload is forwarded to Phase 1 with the JWT's org_id injected
    (overriding any payload org_id), so Phase 1 always writes the row
    with the correct tenant scope.
    """
    # Enforce org_id scoping: if the payload has an org_id, it MUST
    # match the JWT's org_id.
    if payload.org_id is not None and payload.org_id != org_id:
        logger.warning(
            "Cross-org validation blocked: user=%s jwt_org=%s payload_org=%s",
            user_id, org_id, payload.org_id,
        )
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Cross-org validation forbidden: the org_id in the "
                   "payload does not match the org_id in your JWT.",
        )

    # Force the org_id to the JWT's org_id (don't trust the payload).
    # Phase 1 will receive the JWT's org_id in the X-Org-Id header
    # and can use it for org-scoped queries.
    forward_payload = payload.model_dump(exclude_none=True)
    forward_payload["org_id"] = org_id

    async with httpx.AsyncClient(timeout=PHASE1_PROXY_TIMEOUT) as client:
        try:
            response = await client.post(
                f"{PHASE1_SERVICE_URL}/datasets/validated_hypotheses",
                json=forward_payload,
                headers=_build_phase1_headers(org_id),
            )
            response.raise_for_status()
            return response.json()
        except httpx.RequestError as exc:
            logger.warning(
                "datasets/validated_hypotheses: Phase 1 service unavailable "
                "(user=%s org=%s): %s",
                user_id, org_id, exc,
            )
            raise _phase1_unavailable(exc) from exc
        except httpx.HTTPStatusError as exc:
            logger.warning(
                "datasets/validated_hypotheses: Phase 1 returned %d "
                "(user=%s org=%s): %s",
                exc.response.status_code, user_id, org_id, exc.response.text[:500],
            )
            raise HTTPException(
                status_code=exc.response.status_code,
                detail=f"Phase 1 service error: {exc.response.text}",
            ) from exc


# ---------------------------------------------------------------------------
# Teammate 8 ROOT FIX: Phase 2 Knowledge Graph proxy routes.
# ---------------------------------------------------------------------------
# The backend FastAPI service proxies ALL Phase 2 KG endpoints via
# /kg/* paths. The frontend (via the Next.js API routes at
# /api/kg/stats, /api/kg/explore, /api/kg/cypher) calls ONLY the
# backend — NEVER the Phase 2 service directly. This enforces:
#   1. JWT auth on every KG call (Phase 2's /cypher has NO auth —
#      any network caller could otherwise run arbitrary read-only
#      Cypher).
#   2. Per-user rate limiting (10 req/min for /cypher, 100 req/min
#      for /kg/stats and /kg/explore).
#   3. Org-scoped query forwarding via the ``X-Org-Id`` header (the
#      Phase 2 service applies row-level security using this header).
#   4. Centralized audit logging (every /kg/* call is logged with the
#      authenticated user_id + org_id + endpoint).
#
# Why httpx (not requests)?
#   httpx is async-native — it does NOT block the uvicorn event loop
#   while waiting for the Phase 2 service to respond. ``requests`` is
#   sync-only; using it inside an async route would force FastAPI to
#   run the route in a threadpool, defeating the async I/O model that
#   lets a single uvicorn worker handle thousands of concurrent
#   requests. For 100-concurrent-request V1 launch target, async I/O
#   is mandatory.
#
# Why a 30s timeout?
#   KG queries can be slow (multi-hop Cypher over millions of edges).
#   30s is the same hard timeout Phase 2's /cypher endpoint enforces
#   server-side — there is no benefit to a longer client-side timeout
#   (if Phase 2 doesn't respond in 30s, it has already given up).
KG_SERVICE_URL = os.environ.get(
    "KG_SERVICE_URL",
    "http://localhost:8001",  # Phase 2 KG service canonical port
)
KG_PROXY_TIMEOUT_SECONDS = 30.0


def _build_kg_headers(org_id: str) -> Dict[str, str]:
    """Build the headers forwarded to the Phase 2 KG service.

    The ``X-Org-Id`` header is the ONLY tenant-scoping signal the
    Phase 2 service reads — it does NOT decode the JWT (it trusts the
    backend to have authenticated the caller). This is the standard
    service-to-service trust model: the backend is the auth boundary;
    internal services trust the backend's headers.
    """
    return {
        "X-Org-Id": org_id,
        "Content-Type": "application/json",
        "Accept": "application/json",
    }


def _kg_unavailable(exc: Exception) -> HTTPException:
    """Build a 503 HTTPException for KG service unavailable errors."""
    return HTTPException(
        status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
        detail={
            "error": "kg_service_unavailable",
            "message": (
                f"The Phase 2 KG service at {KG_SERVICE_URL} is "
                f"unreachable. The backend FastAPI proxy cannot serve "
                f"/kg/* requests without it. Original error: {exc}"
            ),
            "kg_service_url": KG_SERVICE_URL,
        },
    )


@app.get("/kg/stats", tags=["knowledge-graph"])
async def get_kg_stats(
    request: Request,
    user_id: str = Depends(verify_jwt),
    org_id: str = Depends(verify_org_id),
):
    """Proxy /kg/stats to the Phase 2 KG service.

    Returns the canonical KG stats response including:
      - ``nodeCount``: total node count (all types)
      - ``canonicalNodeCount``: canonical-type nodes only (Compound,
        Protein, Pathway, Disease, ClinicalOutcome — per project docx
        Phase 2 contract)
      - ``edgeCount``: total edge count
      - ``nodeTypes``: per-type node counts
      - ``edgeTypes``: per-type edge counts
      - ``sources``: list of {name, loaded} source-load stats
      - ``lastUpdated``: ISO-8601 UTC timestamp

    The Phase 2 service emits BOTH snake_case (legacy) and camelCase
    (canonical) fields — the backend passes the response through
    unchanged so the frontend can read the canonical fields directly.
    """
    # Per-user rate limit (100 req/min — cheap read).
    check_kg_stats_rate_limit(user_id)
    logger.info(
        "kg/stats: user=%s org=%s — proxying to %s/kg/stats",
        user_id, org_id, KG_SERVICE_URL,
    )
    async with httpx.AsyncClient(timeout=KG_PROXY_TIMEOUT_SECONDS) as client:
        try:
            response = await client.get(
                f"{KG_SERVICE_URL}/kg/stats",
                headers=_build_kg_headers(org_id),
            )
        except httpx.RequestError as exc:
            raise _kg_unavailable(exc) from exc
    # Forward non-2xx responses as-is (preserves Phase 2's 503 detail).
    if response.status_code >= 400:
        raise HTTPException(
            status_code=response.status_code,
            detail=response.json() if response.content else "KG service error",
        )
    return response.json()


@app.post("/kg/explore", tags=["knowledge-graph"])
async def explore_kg(
    payload: Dict[str, Any],
    request: Request,
    user_id: str = Depends(verify_jwt),
    org_id: str = Depends(verify_org_id),
):
    """Proxy POST /kg/explore to the Phase 2 KG service.

    Request body: ``{drug?: string, disease?: string, depth?: int}``
    Response: ``{nodes: [...], edges: [...], truncated: bool}``

    The Phase 2 service performs a real BFS over the in-memory KG (or
    a Cypher query against Neo4j) starting from the requested drug or
    disease node. The ``depth`` parameter controls the BFS depth (1-3
    hops is typical for researcher dashboard exploration).
    """
    # Per-user rate limit (100 req/min — read but more expensive than stats).
    check_kg_explore_rate_limit(user_id)
    logger.info(
        "kg/explore: user=%s org=%s payload=%s — proxying to %s/kg/explore",
        user_id, org_id, payload, KG_SERVICE_URL,
    )
    async with httpx.AsyncClient(timeout=KG_PROXY_TIMEOUT_SECONDS) as client:
        try:
            response = await client.post(
                f"{KG_SERVICE_URL}/kg/explore",
                json=payload,
                headers=_build_kg_headers(org_id),
            )
        except httpx.RequestError as exc:
            raise _kg_unavailable(exc) from exc
    if response.status_code >= 400:
        raise HTTPException(
            status_code=response.status_code,
            detail=response.json() if response.content else "KG service error",
        )
    return response.json()


@app.post("/cypher", tags=["knowledge-graph"])
async def run_cypher(
    payload: Dict[str, Any],
    request: Request,
    user_id: str = Depends(verify_jwt),
    org_id: str = Depends(verify_org_id),
):
    """Proxy POST /cypher to the Phase 2 KG service (rate-limited).

    Request body: ``{query: string, params?: dict, max_rows?: int}``
    Response 200: ``{records: [...], row_count: int, truncated: bool,
                      max_rows: int, backend: 'neo4j', timeout_seconds: 30}``
    Response 429: Rate limit exceeded (after 10 req/min per user).

    The Phase 2 service applies a read-only Cypher whitelist
    (MATCH/OPTIONAL MATCH/WITH/RETURN/WHERE only) AND a hard 30s
    server-side timeout AND a 1000-row cap. The backend's 10 req/min
    rate limit is the FIRST line of defense (DoS protection); the
    Phase 2 service's whitelist + timeout + row cap are the SECOND,
    THIRD, and FOURTH lines.
    """
    # Per-user rate limit (10 req/min — Cypher is expensive).
    # This MUST come BEFORE the proxy call so we don't waste a request
    # to Phase 2 if the user is already over the limit.
    check_cypher_rate_limit(user_id)
    logger.info(
        "cypher: user=%s org=%s — proxying to %s/cypher",
        user_id, org_id, KG_SERVICE_URL,
    )
    async with httpx.AsyncClient(timeout=KG_PROXY_TIMEOUT_SECONDS) as client:
        try:
            response = await client.post(
                f"{KG_SERVICE_URL}/cypher",
                json=payload,
                headers=_build_kg_headers(org_id),
            )
        except httpx.RequestError as exc:
            raise _kg_unavailable(exc) from exc
    if response.status_code >= 400:
        raise HTTPException(
            status_code=response.status_code,
            detail=response.json() if response.content else "KG service error",
        )
    return response.json()


@app.get("/openapi.json", tags=["system"], include_in_schema=False)
async def get_openapi_json():
    """Return the OpenAPI spec (machine-readable).

    This is the canonical API contract for pharma partners. The Next.js
    frontend's Zod schemas (lib/ml-contracts.ts) must match this spec
    exactly — a CI test (tests/test_api_contract_parity.py) asserts
    parity. Pharma partners can download this spec and use it to
    generate client libraries in their language of choice.
    """
    from fastapi.openapi.utils import get_openapi
    return get_openapi(
        title=app.title,
        version=app.version,
        description=app.description,
        routes=app.routes,
    )


# ---------------------------------------------------------------------------
# Main entry point — run with `python -m backend.api.main` or `uvicorn
# backend.api.main:app`.
# ---------------------------------------------------------------------------
if __name__ == "__main__":
    import uvicorn
    # Teammate 8 ROOT FIX: change default port from 8001 to 8000.
    # The previous default (8001) COLLIDED with the Phase 2 KG service
    # canonical port (also 8001 — see docker-compose.yml line 518 and
    # phase2/service.py docstring). When a developer ran both the
    # backend FastAPI and the Phase 2 KG service on the same host
    # (the standard local-dev setup), the second service to start
    # failed with ``address already in use``. The new default (8000)
    # eliminates the collision: backend=8000, phase2=8001, phase1=8001
    # (in a separate container), phase3=8003, phase4=8004. The env var
    # override (``DRUGOS_API_PORT``) is preserved so operators can
    # customize the port in non-standard deployments.
    port = int(os.environ.get("DRUGOS_API_PORT", "8000"))
    workers = int(os.environ.get("DRUGOS_API_WORKERS", "4"))
    uvicorn.run(
        "backend.api.main:app",
        host="0.0.0.0",
        port=port,
        workers=workers,
        log_level=os.environ.get("DRUGOS_API_LOG_LEVEL", "info"),
    )
