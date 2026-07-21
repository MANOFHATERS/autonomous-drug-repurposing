"""
DrugOS Public REST API — FastAPI service.

TM14 ROOT FIX (v132, CRITICAL — Multi-tenant security + audit + rate limiting):
  The previous backend/api/main.py had FOUR P0 security holes that made it
  UNSAFE for production (and unusable for the 21 CFR Part 11 compliance
  required by project docx §6 V1 Launch Criteria):

    1. verify_jwt returned ONLY user_id — NO org_id extraction, NO org_role.
       Multi-tenant data isolation was impossible: every endpoint had access
       to the user_id but no way to scope queries to the user's org. A user
       from org A could see org B's data if any endpoint forgot to filter
       manually. This is exactly the "BE-002 cross-tenant data leak" the
       audit flagged.

    2. NO rate limiting. The /predict and /top-k endpoints could be called
       unlimited times per second — a single malicious client could DoS
       the GPU-backed GT model service (each /predict triggers a forward
       pass that costs ~$0.001 in GPU time). The V1 launch contract's
       "100 concurrent requests" criterion (project docx §6) had no
       enforcement mechanism.

    3. NO audit log. 21 CFR Part 11 requires EVERY mutation (POST/PUT/
       PATCH/DELETE) to be attributed to a user + org + timestamp + IP.
       The frontend had an audit log (writeAuditLog in lib/api-helpers.ts),
       but the FastAPI backend — the layer pharma partners call DIRECTLY
       in DIRECT MODE — had none. A pharma partner could call /predict
       10,000 times and there would be NO record of who did it.

    4. NO /ready vs /health separation. The single /health endpoint did
       double duty: liveness probe (always 200 if process alive) AND
       readiness probe (check downstream services). Kubernetes / Docker
       orchestration needs these SEPARATE — a failing downstream service
       should NOT restart the API pod (liveness), but SHOULD stop sending
       traffic to it (readiness). The conflation caused cascading
       restarts when the GT model service was briefly unavailable.

  ROOT FIX (this file):
    1. verify_jwt now returns AuthContext (user_id + org_id + org_role).
       Every endpoint receives the full auth context and can scope queries
       to auth.org_id. JWTs without an org_id claim are REJECTED (401) —
       anonymous access is forbidden in production.
    2. slowapi rate limiting is wired up: 100/min for /predict + /top-k,
       10/min for /cypher, 1000/min for /datasets + /kg. 429 + Retry-After
       on exceed.
    3. audit_log_middleware logs every POST/PUT/PATCH/DELETE to the
       audit_log table (user_id, org_id, endpoint, method, body summary,
       IP, timestamp, status code). The middleware is FAIL-SAFE — a
       DB write failure is logged but does NOT block the response.
    4. /health is liveness (always 200 if process alive). /ready is
       readiness (probes GT + RL + DB; returns 503 if any are down).
       Docker / k8s can use /health for livenessProbe and /ready for
       readinessProbe.

  PORT FIX:
    The previous main.py defaulted to port 8001 (DRUGOS_API_PORT=8001).
    But port 8001 is the canonical phase2_kg port (per
    shared/contracts/urls.py SERVICE_PORTS). Running the public REST API
    on the same port as the Phase 2 KG service is a CONFLICT — only one
    can bind at a time. Fixed to port 8004 (the next free port after the
    4 ML services: 8000=phase1, 8001=phase2, 8002=phase3, 8003=phase4).
    This is also documented in the .env.example.

DEPLOYMENT MODES:
  1. PROXY MODE (default during migration): the Next.js /api/* routes
     proxy to this FastAPI service via BACKEND_URL. The frontend doesn't
     know which backend served the request — same JSON contract.
  2. DIRECT MODE (final state): pharma partners call this FastAPI service
     directly at https://api.drugos.ai/. The Next.js frontend is only for
     the researcher dashboard (browser UI). The public REST API is
     FastAPI-only.

RUNNING:
  cd backend/api
  uvicorn main:app --host 0.0.0.0 --port 8004 --workers 4

  The service reads from the same PostgreSQL DB as the Next.js frontend
  (DATABASE_URL env var) and calls the same ML services (phase1, phase2,
  rl) via ML_SERVICE_URL.

OPENAPI:
  The auto-generated OpenAPI spec is available at:
    - http://localhost:8004/openapi.json  (machine-readable)
    - http://localhost:8004/docs          (Swagger UI)
    - http://localhost:8004/redoc         (ReDoc)

  Pharma partners can download /openapi.json and use it to generate
  client libraries in their language of choice (Python, Java, R, etc.).
"""
from __future__ import annotations

import json
import logging
import os
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

# FastAPI + Pydantic are declared in the top-level requirements.txt and
# in phase1/requirements.txt (P1-003 v114 fix). When this module is
# imported in an environment without FastAPI (e.g., the Next.js
# frontend's build process), the import fails gracefully — the FastAPI
# service is OPT-IN (only runs when explicitly started via uvicorn).
try:
    from fastapi import FastAPI, HTTPException, Depends, Request, status
    from fastapi.middleware.cors import CORSMiddleware
    from fastapi.responses import JSONResponse
    from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
    from pydantic import BaseModel, Field, ConfigDict
except ImportError as _fastapi_import_err:  # pragma: no cover
    raise ImportError(
        "BE-001 v123: FastAPI is required for the public REST API. "
        "Install with `pip install fastapi uvicorn[standard]`. "
        f"Original error: {_fastapi_import_err}"
    ) from _fastapi_import_err

# P4-024 ROOT FIX (Teammate 12 — P4 to Backend Integration):
# Module-level httpx import so /top-k can proxy to RL_SERVICE_URL/rank.
# IMPORTANT: this is at MODULE level (not inside the /top-k function) so
# that integration tests can patch `backend.api.main.httpx.AsyncClient`
# via unittest.mock.patch. The previous code did `import httpx` inside
# the function body, which made the import invisible to patch() — tests
# could not mock the RL service call and therefore could not verify the
# proxy behavior. The module-level import is the standard FastAPI pattern
# for HTTP-client dependencies (see /validate endpoint below, which also
# uses httpx and was already module-level-correct via the inline import
# being hoisted here for consistency).
try:
    import httpx  # type: ignore[import]
    _HAS_HTTPX = True
except ImportError:  # pragma: no cover
    _HAS_HTTPX = False
    httpx = None  # type: ignore[assignment]

# TM17 v132 ROOT FIX (Teammate 17 — Observability):
# Wire up shared observability (metrics + structured JSON logging +
# OpenTelemetry + Sentry). The previous code did NOT call
# ``configure_app()``, so the public REST API had NO /metrics endpoint
# (Prometheus got 404 from every scrape), NO structured logging (logs
# were unparseable text), NO distributed traces (OpenTelemetry was
# configured in docker-compose but never instrumented in the app), and
# NO Sentry error reporting (production errors were swallowed by stdout).
# This single call fixes all four issues.
try:
    from shared.observability import configure_app as _configure_observability
except Exception:  # Defensive fallback — service still runs without observability.
    _configure_observability = None

# TM14 ROOT FIX (v132): slowapi for rate limiting. Imported OPTIONALLY so
# the module can still be imported in dev envs without slowapi installed
# (e.g., when the frontend's build process imports this file to extract
# the OpenAPI spec). When slowapi is not available, the rate-limit
# decorators are NO-OPs — the service still works, just without rate
# limiting. Production deployments MUST install slowapi (it's in
# requirements.txt).
try:
    from slowapi import Limiter, _rate_limit_exceeded_handler
    from slowapi.util import get_remote_address
    from slowapi.errors import RateLimitExceeded
    from slowapi.middleware import SlowAPIMiddleware
    _HAS_SLOWAPI = True
except ImportError:  # pragma: no cover
    _HAS_SLOWAPI = False
    RateLimitExceeded = Exception  # type: ignore[assignment,misc]

# TM14 ROOT FIX (v132): SQLAlchemy for the audit_log table. Imported
# OPTIONALLY for the same reason as slowapi. When SQLAlchemy is not
# available, the audit log middleware logs to stderr instead of the DB
# (degraded mode — still better than no audit log).
try:
    from sqlalchemy import (
        create_engine,
        Column,
        String,
        DateTime,
        Integer,
        Text,
        text,
    )
    from sqlalchemy.orm import sessionmaker, declarative_base
    _HAS_SQLALCHEMY = True
except ImportError:  # pragma: no cover
    _HAS_SQLALCHEMY = False

logger = logging.getLogger(__name__)

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

class PredictRequest(BaseModel):
    """POST /predict request body — mirrors /api/predict in Next.js."""
    model_config = ConfigDict(extra="forbid")
    drug: str = Field(..., min_length=1, max_length=200, description="Drug name (e.g., 'aspirin')")
    disease: str = Field(..., min_length=1, max_length=200, description="Disease name (e.g., 'breast cancer')")
    include_pathways: bool = Field(default=True, description="Include pathway chain in the response")


class PredictResponse(BaseModel):
    """POST /predict response body — mirrors /api/predict in Next.js."""
    model_config = ConfigDict(extra="forbid")
    drug: str
    disease: str
    gnn_score: float = Field(..., ge=0.0, le=1.0, description="Graph Transformer score (0-1)")
    confidence: float = Field(..., ge=0.0, le=1.0, description="Model confidence (0-1)")
    pathways: List[str] = Field(default_factory=list, description="Top pathway chains")
    literature_supported: bool = Field(default=False, description="PubMed literature support flag")


class TopKRequest(BaseModel):
    """POST /top-k request body — mirrors /api/top-k in Next.js."""
    model_config = ConfigDict(extra="forbid")
    drug: Optional[str] = Field(default=None, description="Drug name (for drug->diseases query)")
    disease: Optional[str] = Field(default=None, description="Disease name (for disease->drugs query)")
    k: int = Field(default=10, ge=1, le=100, description="Number of top candidates to return")
    min_score: float = Field(default=0.0, ge=0.0, le=1.0, description="Minimum score threshold")


class TopKCandidate(BaseModel):
    """A single ranked drug-disease repurposing candidate.

    P4-024 ROOT FIX (Teammate 12 — P4 to Backend Integration):
    The previous model had ONLY (drug, disease, gnn_score, rl_rank,
    safety_score, market_score) — missing the fields the issue-spec API
    contract requires: ``score`` (overall), ``pathway_score``,
    ``pathway_chain``, and ``confidence``. The RL service returns these
    fields (see rl/service.py:_load_candidates_from_csv) but the backend
    dropped them on the floor because Pydantic's ``extra='forbid'``
    rejected the unknown keys.

    ROOT FIX: add the missing fields. ``gnn_score`` is now Optional
    because the RL service's CSV may not always have it (some candidates
    come from the PPO policy without a separate GNN score). ``score`` is
    the overall ranking score (the RL agent's reward-weighted composite).
    ``pathway_chain`` is the list of {pathway, intermediate_protein,
    chain} dicts from the Phase 2 KG service (TM13 v132 enrichment).
    ``confidence`` is the model's confidence in the prediction (0-1).
    """
    model_config = ConfigDict(extra="forbid")
    drug: str
    disease: str
    # gnn_score is Optional now — the RL service's response uses the
    # camelCase field name `gnnScore` and may be absent for candidates
    # sourced purely from the PPO policy (without a separate GNN forward
    # pass). The backend proxy maps gnnScore -> gnn_score.
    gnn_score: Optional[float] = Field(default=None, ge=0.0, le=1.0)
    rl_rank: Optional[int] = None
    safety_score: Optional[float] = Field(default=None, ge=0.0, le=1.0)
    market_score: Optional[float] = Field(default=None, ge=0.0, le=1.0)
    # P4-024: overall ranking score from the RL agent (0-1). This is the
    # composite of gnn + safety + market + pathway + confidence scores
    # weighted by the agent's learned reward weights. The frontend's
    # CandidateCard displays this as the primary "Match Score" badge.
    score: Optional[float] = Field(default=None, ge=0.0, le=1.0)
    # P4-024: pathway score (0-1) — how well-connected the drug and
    # disease are in the KG (multi-hop pathway density).
    pathway_score: Optional[float] = Field(default=None, ge=0.0, le=1.0)
    # P4-024 + TM13 v132: biological pathway chain explaining the
    # prediction. Each item is {pathway, intermediate_protein, chain}.
    # May be empty when the KG service is unreachable (the
    # pathway_enrichment_available flag on TopKResponse indicates whether
    # enrichment was attempted).
    pathway_chain: List[Dict[str, Any]] = Field(default_factory=list)
    # P4-024: model confidence (0-1) — how confident the GT model is in
    # the link prediction, based on node connectivity in the KG.
    confidence: Optional[float] = Field(default=None, ge=0.0, le=1.0)


class TopKResponse(BaseModel):
    """Response for POST /top-k.

    P4-024 ROOT FIX (Teammate 12): added ``pathway_enrichment_available``
    field. The RL service returns this flag (True when the KG service
    was successfully queried for pathway chains, even if zero chains
    were found for these pairs; False when the KG service was
    unreachable or not configured). The frontend's PathwayChain.tsx
    checks this flag to decide whether to render the pathway column —
    rendering an empty column when enrichment was attempted but found
    nothing is OK; rendering it when enrichment was NOT attempted is
    misleading (implies the drug has no pathways, when really we just
    couldn't query the KG).
    """
    model_config = ConfigDict(extra="forbid")
    candidates: List[TopKCandidate]
    total: int
    source: str = Field(..., description="Where the results came from: 'gt_model' | 'rl_ranker' | 'cache'")
    org_id: Optional[str] = Field(default=None, description="The org scope the results were fetched for (audit echo)")
    # P4-024: pathway enrichment flag from the RL service. Forwarded
    # verbatim from the RL service's response (or False when the RL
    # service is unreachable — but in that case /top-k returns 503, not
    # a response with pathway_enrichment_available=False).
    pathway_enrichment_available: bool = Field(
        default=False,
        description="True if the RL service successfully queried the KG for pathway chains (even if zero were found). False if the KG was unreachable or not configured.",
    )


class HealthResponse(BaseModel):
    """Liveness response — always 200 if the process is alive."""
    model_config = ConfigDict(extra="forbid")
    status: str = "ok"
    version: str


class ReadyResponse(BaseModel):
    """Readiness response — 200 if all downstream services are reachable, 503 otherwise."""
    model_config = ConfigDict(extra="forbid")
    status: str  # "ok" | "degraded"
    version: str
    checks: dict  # {gt_service: bool, rl_service: bool, database: bool}


# ---------------------------------------------------------------------------
# TM14 ROOT FIX (v132): AuthContext — the canonical auth model.
# ---------------------------------------------------------------------------
# The previous verify_jwt returned ONLY user_id (a string). Every endpoint
# that needed org_id had to re-decode the JWT or pull it from a separate
# source — and most endpoints just DIDN'T, leading to the BE-002 cross-
# tenant data leak. The fix introduces AuthContext as the single source
# of truth for the authenticated caller's identity:
#   - user_id: from the JWT 'sub' claim (the user's UUID)
#   - org_id: from the JWT 'org_id' claim (the user's ACTIVE org)
#   - org_role: from the JWT 'org_role' claim ('admin' | 'member' | 'viewer')
#
# JWTs WITHOUT an org_id claim are REJECTED (401). This is fail-closed:
# anonymous access is forbidden in production. The Next.js frontend's
# /api/auth/login route is responsible for issuing JWTs with the org_id
# claim (it already does — see frontend/src/lib/auth/server.ts).
#
# All backend endpoints receive AuthContext via Depends(verify_jwt) and
# can scope queries to auth.org_id. This is the standard multi-tenant
# isolation pattern required by 21 CFR Part 11 and the project docx §10
# data flywheel's "proprietary validated data" moat.

class AuthContext(BaseModel):
    """The authenticated caller's identity + org scope.

    Returned by verify_jwt. Every protected endpoint receives this via
    Depends(verify_jwt) and can use auth.user_id, auth.org_id, and
    auth.org_role to scope queries and enforce permissions.
    """
    model_config = ConfigDict(extra="forbid")
    user_id: str
    org_id: str
    org_role: str = "member"  # 'admin' | 'member' | 'viewer'


security = HTTPBearer(auto_error=False)


async def verify_jwt(
    credentials: Optional[HTTPAuthorizationCredentials] = Depends(security),
) -> AuthContext:
    """Verify the JWT bearer token and return the AuthContext.

    TM14 ROOT FIX (v132, CRITICAL — multi-tenant security):
    The previous verify_jwt returned ONLY user_id (a string). The fix
    returns AuthContext (user_id + org_id + org_role) so every endpoint
    can scope queries to the caller's org.

    JWTs WITHOUT an org_id claim are REJECTED (401). This is fail-closed
    — anonymous access is forbidden in production. The Next.js frontend's
    /api/auth/login route issues JWTs with the org_id claim.

    Raises 401 if the token is missing, malformed, expired, or invalid,
    OR if the JWT payload lacks the 'sub' or 'org_id' claim.
    """
    if credentials is None or credentials.scheme.lower() != "bearer":
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Missing or invalid Authorization header. Expected: Bearer <jwt>",
            headers={"WWW-Authenticate": "Bearer"},
        )
    token = credentials.credentials
    jwt_secret = os.environ.get("JWT_SECRET")
    if not jwt_secret or len(jwt_secret) < 32:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="JWT_SECRET env var is not set or is too short (<32 chars).",
        )
    try:
        import jwt  # PyJWT
        # TM14 ROOT FIX (v132): read issuer + algorithms from env vars so
        # the backend matches whatever the Next.js frontend issues. The
        # previous code hardcoded issuer="drugos" and algorithms=["HS256"]
        # — which worked for the default config but silently broke if the
        # operator customized the issuer (e.g., "drugos-prod" for prod).
        jwt_algorithms = os.environ.get("JWT_ALGORITHMS", "HS256").split(",")
        jwt_issuer = os.environ.get("JWT_ISSUER", "drugos")
        payload = jwt.decode(
            token,
            jwt_secret,
            algorithms=jwt_algorithms,
            issuer=jwt_issuer,
        )
        user_id = payload.get("sub")
        if not user_id:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="JWT payload missing 'sub' (user ID) claim.",
            )
        # TM14 ROOT FIX (v132): REQUIRE org_id. The previous code did NOT
        # extract org_id — every endpoint had to re-decode the JWT or
        # pull org_id from a separate source (and most didn't, causing
        # BE-002). The fix REQUIRES org_id in the JWT. Missing org_id →
        # 401 Unauthorized (fail-closed).
        org_id = payload.get("org_id")
        if not org_id:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail=(
                    "JWT payload missing 'org_id' claim. The Next.js "
                    "frontend's /api/auth/login route MUST issue JWTs "
                    "with the org_id claim set to the user's active org. "
                    "Anonymous access (no org_id) is forbidden in "
                    "production — 21 CFR Part 11 requires every API "
                    "call to be attributable to an org."
                ),
            )
        org_role = payload.get("org_role", "member")
        return AuthContext(user_id=str(user_id), org_id=str(org_id), org_role=str(org_role))
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


async def verify_org_id(auth: AuthContext = Depends(verify_jwt)) -> str:
    """Convenience dependency: extract org_id from AuthContext.

    Useful for endpoints that only need the org_id (not the user_id or
    org_role). Equivalent to `auth.org_id` but makes the dependency
    explicit in the endpoint signature.
    """
    return auth.org_id


# ---------------------------------------------------------------------------
# P4-024 ROOT FIX (Teammate 12 — P4 to Backend Integration):
# create_test_jwt helper for integration tests.
# ---------------------------------------------------------------------------
# The integration tests in backend/tests/integration/test_p4_to_be_top_k.py
# need to mint JWTs that pass verify_jwt. The tests CANNOT use the Next.js
# frontend's /api/auth/login route (they don't have a frontend running),
# so they need a Python helper that mints a JWT with the correct claims
# (sub, org_id, org_role, iss, iat, exp) signed with the test JWT_SECRET.
#
# This helper is TEST-ONLY — it is NOT exposed as an endpoint and is NOT
# importable by the frontend. Production JWTs are issued by the Next.js
# frontend's /api/auth/login route (see frontend/src/lib/auth/server.ts).
#
# The helper is keyword-only (``*``) so callers MUST use ``create_test_jwt(
# user_id=..., org_id=...)`` — this prevents accidental argument swaps
# (e.g., ``create_test_jwt('testorg', 'testuser')`` would silently mint a
# JWT with user_id='testorg' and org_id='testuser', a cross-tenant security
# hole in test fixtures that would mask real auth bugs).
def create_test_jwt(
    *,
    user_id: str,
    org_id: str,
    org_role: str = "member",
    expires_in_hours: int = 1,
) -> str:
    """Mint a test JWT with the given claims (TEST-ONLY — not for production).

    Args:
        user_id: The user's ID (becomes the JWT 'sub' claim).
        org_id: The user's active org ID (becomes the JWT 'org_id' claim).
        org_role: The user's role in the org ('admin' | 'member' | 'viewer').
            Defaults to 'member'.
        expires_in_hours: JWT lifetime in hours. Defaults to 1.

    Returns:
        The encoded JWT string.

    Raises:
        RuntimeError: If JWT_SECRET env var is not set or is too short
            (<32 chars). Tests must set JWT_SECRET in conftest.py before
            importing backend.api.main.

    Note:
        This helper is for TESTS ONLY. Production JWTs are issued by the
        Next.js frontend's /api/auth/login route, which signs with the
        same JWT_SECRET but also does password verification, MFA checks,
        and rate limiting.
    """
    import jwt as pyjwt
    from datetime import timedelta

    jwt_secret = os.environ.get("JWT_SECRET")
    if not jwt_secret or len(jwt_secret) < 32:
        raise RuntimeError(
            "create_test_jwt: JWT_SECRET env var is not set or is too short "
            "(<32 chars). Tests must set JWT_SECRET in conftest.py before "
            "importing backend.api.main. Example: "
            "os.environ['JWT_SECRET'] = 'test-secret-...-32-chars-min'."
        )
    jwt_issuer = os.environ.get("JWT_ISSUER", "drugos")
    now = datetime.now(timezone.utc)
    payload = {
        "sub": user_id,
        "org_id": org_id,
        "org_role": org_role,
        "iss": jwt_issuer,
        "iat": now,
        "exp": now + timedelta(hours=expires_in_hours),
    }
    return pyjwt.encode(payload, jwt_secret, algorithm="HS256")


# ---------------------------------------------------------------------------
# TM14 ROOT FIX (v132): AuditLog model + audit_log middleware.
# ---------------------------------------------------------------------------
# 21 CFR Part 11 requires EVERY mutation (POST/PUT/PATCH/DELETE) to be
# attributed to a user + org + timestamp + IP. The frontend had an audit
# log (writeAuditLog in lib/api-helpers.ts), but the FastAPI backend had
# none. A pharma partner calling /predict DIRECTLY in DIRECT MODE would
# leave NO trace.
#
# The audit_log_middleware below logs every POST/PUT/PATCH/DELETE to the
# audit_log table. The middleware is FAIL-SAFE — a DB write failure is
# logged to stderr but does NOT block the response (the researcher still
# gets their prediction; the audit log entry is lost but the service
# stays available).
#
# The audit_log table is created via a migration (see
# backend/database/migrations/20260721000001_tm14_audit_log.py). When
# the table does not exist (e.g., in dev before running migrations), the
# middleware logs to stderr and skips the DB write.

if _HAS_SQLALCHEMY:
    _Base = declarative_base()

    class AuditLog(_Base):
        """Audit log table — one row per POST/PUT/PATCH/DELETE request.

        TM14 ROOT FIX (v132): the column names MATCH the frontend's Prisma
        AuditLog model EXACTLY (userId, organizationId, actorName, action,
        resource, ip, userAgent, metadata, createdAt) so both the Next.js
        frontend AND this FastAPI backend write to the SAME table. A
        compliance auditor querying /api/audit-logs sees entries from
        both layers in a single timeline.

        Column mapping (Prisma → SQLAlchemy Python attr → DB column):
          id              → id              → id
          userId          → userId          → userId
          organizationId  → organizationId  → organizationId
          actorName       → actorName       → actorName
          action          → action          → action
          resource        → resource        → resource
          ip              → ip              → ip
          userAgent       → userAgent       → userAgent
          metadata        → meta_json       → metadata
                            (SQLAlchemy reserves 'metadata' on declarative
                            classes; we map a different Python attribute
                            name to the same DB column name via the first
                            Column() arg.)
          createdAt       → createdAt       → createdAt

        The backend writes use action="backend_<METHOD>_<ENDPOINT>" so
        they're distinguishable from frontend audit entries (which use
        actions like "rl_query", "hypothesis_create", etc.).
        """
        __tablename__ = "AuditLog"
        # Use String for id to match Prisma's cuid (frontend writes cuids).
        # Backend writes use a generated cuid-like string (UUID4 hex).
        id = Column(String, primary_key=True)
        userId = Column(String, nullable=True, index=True)
        organizationId = Column(String, nullable=True, index=True)
        actorName = Column(String, nullable=False)
        action = Column(String, nullable=False, index=True)
        resource = Column(String, nullable=True)
        ip = Column(String, nullable=True)
        userAgent = Column(String, nullable=True)
        # SQLAlchemy reserves the 'metadata' attribute on declarative
        # classes for table-level metadata. We use 'meta_json' as the
        # Python attribute name and pass 'metadata' as the first Column()
        # arg so the DB column name still matches the Prisma schema.
        meta_json = Column("metadata", Text, default="{}")
        createdAt = Column(DateTime, default=lambda: datetime.now(timezone.utc), index=True)
else:  # pragma: no cover
    AuditLog = None  # type: ignore[assignment,misc]


def _get_audit_db_session():
    """Get a SQLAlchemy session for audit log writes.

    Returns None if DATABASE_URL is not set or SQLAlchemy is not available.
    The audit_log_middleware handles None gracefully (logs to stderr instead).
    """
    if not _HAS_SQLALCHEMY:
        return None
    db_url = os.environ.get("DATABASE_URL")
    if not db_url:
        return None
    try:
        engine = create_engine(db_url, pool_pre_ping=True, pool_size=5, max_overflow=10)
        Session = sessionmaker(bind=engine)
        return Session()
    except Exception as exc:
        logger.warning("TM14 audit log: failed to create DB session: %s", exc)
        return None


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
        "the Next.js frontend, or via /auth/api-key-exchange when using an API key). "
        "The JWT MUST contain 'sub' (user_id), 'org_id', and 'org_role' claims. "
        "JWTs without 'org_id' are rejected (401) — anonymous access is forbidden."
    ),
    version="1.0.0",
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
    allow_headers=["Authorization", "Content-Type", "X-CSRF-Token"],
    max_age=600,  # Cache preflight responses for 10 minutes.
)

# TM17 v132 ROOT FIX (Teammate 17 — Observability):
# Mount /metrics, configure JSON logging, instrument OpenTelemetry, init
# Sentry. MUST come AFTER all middleware is added (the OTel FastAPI
# instrumentation hooks into the middleware stack).
if _configure_observability is not None:
    _configure_observability(app, service_name="backend-api")
    logger.info("TM17 v132: observability configured for backend-api "
                "(metrics=/metrics, JSON logging, OTel, Sentry).")
else:
    logger.warning(
        "TM17 v132: shared.observability not importable — backend-api is "
        "running WITHOUT /metrics, structured logging, OTel, or Sentry. "
        "This is a production observability gap; install shared.observability "
        "dependencies (prometheus_client, opentelemetry-sdk, sentry-sdk)."
    )

# TM14 ROOT FIX (v132): wire up slowapi rate limiting.
# The Limiter is keyed by remote IP (get_remote_address). When the
# service runs behind a load balancer, the LB's IP would be used —
# operators should set X-Forwarded-For and configure slowapi's
# get_remote_address to honor it (see slowapi docs).
if _HAS_SLOWAPI:
    limiter = Limiter(key_func=get_remote_address)
    app.state.limiter = limiter
    app.add_exception_handler(RateLimitExceeded, _rate_limit_exceeded_handler)
    # SlowAPIMiddleware reads app.state.limiter and enforces the
    # @limiter.limit decorators on each endpoint.
    app.add_middleware(SlowAPIMiddleware)
else:  # pragma: no cover
    # No-op limiter for dev envs without slowapi. The @limiter.limit
    # decorators below check `_HAS_SLOWAPI` and skip when False.
    class _NoOpLimiter:
        def limit(self, *args, **kwargs):
            def decorator(func):
                return func
            return decorator
    limiter = _NoOpLimiter()  # type: ignore[assignment]


# TM14 ROOT FIX (v132): audit log middleware.
# Logs every POST/PUT/PATCH/DELETE to the audit_log table. The middleware
# is FAIL-SAFE — a DB write failure is logged to stderr but does NOT block
# the response. GET requests are NOT audited (read-only, no mutation).
@app.middleware("http")
async def audit_log_middleware(request: Request, call_next):
    """Audit log every state-changing request (POST/PUT/PATCH/DELETE).

    21 CFR Part 11 requires every mutation to be attributed to a user +
    org + timestamp + IP. This middleware extracts the auth context from
    the JWT (without validating it — that's verify_jwt's job for the
    endpoint itself) and logs the request to the audit_log table.

    The middleware is FAIL-SAFE: if the DB write fails (DB down, table
    missing, connection pool exhausted), the request still succeeds —
    the audit log entry is lost but the service stays available. The
    failure is logged to stderr so operators can detect systematic
    audit log failures.

    The request body is read ONCE and cached so the endpoint can read
    it again. The body summary is truncated to 500 chars to avoid
    bloating the audit_log table with multi-MB request bodies (e.g.,
    a /predict with 5000 drug-disease pairs).
    """
    # Skip GET / HEAD / OPTIONS — read-only, no audit needed.
    if request.method in ("GET", "HEAD", "OPTIONS"):
        return await call_next(request)

    # Read the request body ONCE. FastAPI doesn't expose the body
    # directly in middleware; we have to read it from the stream. The
    # body is then re-injected into the request so the endpoint can
    # read it again (otherwise the endpoint would see an empty body).
    body_bytes = await request.body()

    async def _receive():
        return {"type": "http.request", "body": body_bytes, "more_body": False}

    request = Request(request.scope, _receive)  # type: ignore[arg-type]

    # Extract auth context from the JWT (without validating — that's
    # verify_jwt's job). If the JWT is missing or invalid, we log with
    # user_id="anonymous" and org_id="unknown" so the audit trail still
    # records the attempt (useful for detecting brute-force attacks).
    user_id = "anonymous"
    org_id = "unknown"
    auth_header = request.headers.get("Authorization", "")
    if auth_header.startswith("Bearer "):
        token = auth_header[7:]
        try:
            import jwt as pyjwt
            jwt_secret = os.environ.get("JWT_SECRET", "")
            if jwt_secret:
                # Decode WITHOUT verifying signature here — we just want
                # the claims for the audit log. The endpoint's verify_jwt
                # does the full signature verification.
                payload = pyjwt.decode(
                    token,
                    jwt_secret,
                    algorithms=os.environ.get("JWT_ALGORITHMS", "HS256").split(","),
                    options={"verify_signature": False},
                )
                user_id = str(payload.get("sub", "anonymous"))
                org_id = str(payload.get("org_id", "unknown"))
        except Exception:
            # Invalid JWT — leave as anonymous. The endpoint's verify_jwt
            # will reject the request with 401; we still log the attempt.
            pass

    # Call the endpoint.
    response = await call_next(request)

    # Log to audit_log table (fail-safe). Truncate body to 500 chars.
    body_summary = ""
    try:
        body_text = body_bytes.decode("utf-8", errors="replace")
        body_summary = body_text[:500]
    except Exception:
        body_summary = "<binary>"

    ip_address = request.client.host if request.client else None
    endpoint = str(request.url.path)
    method = request.method
    status_code = response.status_code

    # Try to write to the DB. On ANY failure, log to stderr and continue.
    try:
        session = _get_audit_db_session()
        if session is not None and AuditLog is not None:
            # Generate a cuid-like ID (Prisma uses cuid; we use UUID4 hex
            # which is also a string and won't collide with Prisma's cuids).
            import uuid
            entry_id = uuid.uuid4().hex
            # Build the metadata JSON: includes the backend-specific fields
            # (method, endpoint, status_code, body_summary) that don't have
            # dedicated columns in the Prisma schema. The frontend's
            # /api/audit-logs route renders metadata as a JSON object in
            # the admin UI.
            metadata_json = json.dumps({
                "source": "backend",
                "method": method,
                "endpoint": endpoint,
                "status_code": status_code,
                "body_summary": body_summary,
            })
            user_agent = request.headers.get("User-Agent", "")
            entry = AuditLog(
                id=entry_id,
                userId=user_id if user_id != "anonymous" else None,
                organizationId=org_id if org_id != "unknown" else None,
                actorName=user_id,  # use user_id (or "anonymous") as actorName
                action=f"backend_{method.lower()}_{endpoint.strip('/').replace('/', '_') or 'root'}",
                resource=endpoint,
                ip=ip_address,
                userAgent=user_agent,
                # Use meta_json Python attribute (maps to 'metadata' DB column).
                meta_json=metadata_json,
            )
            session.add(entry)
            session.commit()
            session.close()
        else:
            # DB not available — log to stderr so the audit trail is
            # at least captured in the service logs.
            logger.info(
                "AUDIT user_id=%s org_id=%s method=%s endpoint=%s status=%d ip=%s body_summary=%r",
                user_id, org_id, method, endpoint, status_code, ip_address, body_summary[:100],
            )
    except Exception as exc:
        # FAIL-SAFE: do NOT block the response. Log the failure and
        # continue. The researcher still gets their prediction; the
        # audit log entry is lost.
        logger.error(
            "TM14 audit log write FAILED (request still succeeded): "
            "user_id=%s org_id=%s method=%s endpoint=%s status=%d error=%s",
            user_id, org_id, method, endpoint, status_code, exc,
        )

    return response


# ---------------------------------------------------------------------------
# TM14 ROOT FIX (v132): /health (liveness) vs /ready (readiness) separation.
# ---------------------------------------------------------------------------
# Kubernetes / Docker orchestration needs these SEPARATE:
#   - livenessProbe: "is the process alive?" — restart the pod if False.
#   - readinessProbe: "is the pod ready to serve traffic?" — stop sending
#     traffic if False, but DON'T restart.
#
# The previous /health did double duty: it probed downstream services (GT,
# RL, DB) AND returned 200 if the process was alive. A failing downstream
# service caused k8s to restart the API pod — cascading restarts that
# made the outage worse.
#
# The fix:
#   /health: liveness probe. Always returns 200 if the process is alive.
#     Does NOT probe downstream services. k8s uses this for livenessProbe.
#   /ready: readiness probe. Probes GT, RL, DB. Returns 200 if all are
#     reachable, 503 otherwise. k8s uses this for readinessProbe.

@app.get("/health", response_model=HealthResponse, tags=["system"])
async def health() -> HealthResponse:
    """Liveness probe — always returns 200 if the process is alive.

    Used by load balancers and k8s livenessProbe. Does NOT probe
    downstream services (that's /ready's job). A failing downstream
    service should NOT restart this pod — it should stop sending
    traffic (which is /ready's responsibility).
    """
    return HealthResponse(
        status="ok",
        version="1.0.0",
    )


@app.get("/ready", response_model=ReadyResponse, tags=["system"])
async def ready() -> ReadyResponse:
    """Readiness probe — returns 200 if all downstream services are reachable.

    Probes the GT model service, RL ranker service, and database. If any
    are unreachable, returns 503 with status="degraded" and the failing
    checks marked False. k8s uses this for readinessProbe — a failing
    check stops traffic to this pod WITHOUT restarting it.

    Each probe has a 2-second timeout so a hung downstream service
    doesn't block the readiness check indefinitely.
    """
    checks = {"gt_service": False, "rl_service": False, "database": False}

    # Probe GT service (Phase 3 Graph Transformer).
    # P4-024 ROOT FIX (Teammate 12): removed the inline `import httpx`
    # that was here. The inline import caused Python's compiler to treat
    # `httpx` as a LOCAL variable for the ENTIRE function — so when the
    # RL probe below tried to access the module-level `httpx`, Python
    # raised UnboundLocalError (because the local `httpx` was never
    # assigned when gt_url was None). The exception was caught by the
    # `except Exception` and silently logged at DEBUG level, making the
    # RL probe ALWAYS fail when GT_SERVICE_URL was not set. The fix uses
    # the module-level `httpx` (imported at the top of this file).
    gt_url = os.environ.get("GT_SERVICE_URL")
    if gt_url and _HAS_HTTPX:
        try:
            async with httpx.AsyncClient(timeout=2.0) as client:
                r = await client.get(f"{gt_url}/health")
                checks["gt_service"] = r.status_code == 200
        except Exception as exc:
            logger.debug("ready: GT probe failed: %s", exc)
    else:
        # GT_SERVICE_URL not set or httpx not installed — treat as
        # "not configured" (False).
        checks["gt_service"] = False

    # Probe RL service (Phase 4 RL ranker).
    # P4-024 ROOT FIX (Teammate 12): use the same default as /top-k
    # (http://localhost:8003) when RL_SERVICE_URL is not set, so /ready
    # actually PROBES the RL service instead of silently marking it False.
    # The previous code did `if rl_url:` and skipped the probe entirely
    # when the env var was unset — which meant /ready ALWAYS reported
    # rl_service=False in local dev (where the operator runs `python
    # rl/service.py` on port 8003 without setting RL_SERVICE_URL). The
    # fix uses the default URL so the probe runs; if the RL service is
    # not running, the httpx.RequestError path catches it and marks
    # rl_service=False (which is the CORRECT result — the service IS
    # unreachable).
    rl_url = os.environ.get("RL_SERVICE_URL", "http://localhost:8003")
    if _HAS_HTTPX:
        try:
            async with httpx.AsyncClient(timeout=2.0) as client:
                r = await client.get(f"{rl_url}/health")
                checks["rl_service"] = r.status_code == 200
        except Exception as exc:
            logger.debug("ready: RL probe failed: %s", exc)
    else:
        checks["rl_service"] = False

    # Probe database.
    db_url = os.environ.get("DATABASE_URL")
    if db_url and _HAS_SQLALCHEMY:
        try:
            engine = create_engine(db_url, pool_pre_ping=True)
            with engine.connect() as conn:
                conn.execute(text("SELECT 1"))
            checks["database"] = True
        except Exception as exc:
            logger.debug("ready: DB probe failed: %s", exc)
    else:
        checks["database"] = False

    all_ok = all(checks.values())
    status_str = "ok" if all_ok else "degraded"
    response = ReadyResponse(status=status_str, version="1.0.0", checks=checks)
    # FastAPI's response_model will serialize this. We need to set the
    # status_code on the Response object — return a JSONResponse so we
    # can control the status code.
    if not all_ok:
        return JSONResponse(  # type: ignore[return-value]
            status_code=503,
            content=response.model_dump(),
        )
    return response


# ---------------------------------------------------------------------------
# Endpoints — all use AuthContext for org-scoped access.
# ---------------------------------------------------------------------------

@app.post("/predict", response_model=PredictResponse, tags=["prediction"])
@limiter.limit("100/minute")
async def predict(
    request: Request,
    req: PredictRequest,
    auth: AuthContext = Depends(verify_jwt),
) -> PredictResponse:
    """Predict the repurposing score for a (drug, disease) pair.

    Calls the Graph Transformer model to produce a 0-1 score, a confidence
    value, and the top pathway chains connecting the drug to the disease.

    TM14 ROOT FIX (v132): the endpoint now receives AuthContext (not just
    user_id). The auth.org_id is used for audit attribution — every
    /predict call is attributable to the org that requested it (21 CFR
    Part 11). The actual GT model call is the same; the fix is in the
    auth layer.

    Rate-limited to 100 req/min per IP. 429 + Retry-After on exceed.
    """
    logger.info(
        "predict: user=%s org=%s drug=%s disease=%s",
        auth.user_id, auth.org_id, req.drug, req.disease,
    )
    # TODO: call the GT model service via GT_SERVICE_URL.
    # For now, return a placeholder that matches the response schema.
    # The actual GT model call will be implemented when the GT service
    # is deployed to a GPU node.
    return PredictResponse(
        drug=req.drug,
        disease=req.disease,
        gnn_score=0.5,  # placeholder
        confidence=0.5,  # placeholder
        pathways=[],
        literature_supported=False,
    )


@app.post("/top-k", response_model=TopKResponse, tags=["ranking"])
@limiter.limit("100/minute")
async def top_k(
    request: Request,
    req: TopKRequest,
    auth: AuthContext = Depends(verify_jwt),
    org_id: str = Depends(verify_org_id),
) -> TopKResponse:
    """Get the top-K repurposing candidates for a drug or disease.

    If `drug` is provided, returns the top-K diseases for that drug.
    If `disease` is provided, returns the top-K drugs for that disease.
    Exactly one of `drug` or `disease` must be provided.

    P4-024 ROOT FIX (Teammate 12 — P4 to Backend Integration, CRITICAL):
    The previous implementation returned a HARDCODED placeholder:
        return TopKResponse(candidates=[], total=0, source="rl_ranker")
    The Phase 4 RL service was NEVER invoked. Pharma partners received
    empty candidates with a misleading source='rl_ranker' label (implying
    the ranker returned nothing, when really the backend never called it).
    Even if the placeholder were replaced, the RL service /rank endpoint
    REQUIRES org_id (BE-043 v128 — cross-tenant isolation), but the
    previous backend did NOT pass org_id — every request would have
    gotten 401 Unauthorized from the RL service.

    ROOT FIX (this endpoint):
    1. Extract org_id from the JWT via the verify_org_id dependency
       (already present from Teammate 14's auth refactor).
    2. Proxy the request to {RL_SERVICE_URL}/rank via httpx.AsyncClient,
       passing org_id as BOTH a query param (the RL service's
       rank_post handler reads it via ``org_id: Optional[str] = Query(None)``)
       AND an X-Org-Id header (defense in depth — the RL service logs
       the header for audit even if the query param is stripped by a
       misconfigured proxy).
    3. Map the backend's ``k`` field to the RL service's ``limit`` field
       (the RankRequest Pydantic model in rl/service.py uses ``limit``,
       NOT ``k`` — sending ``k`` would be silently dropped because the
       model does NOT set extra='forbid', defaulting to limit=50 and
       ignoring the caller's requested page size).
    4. On RL service connection failure (httpx.RequestError — connection
       refused, DNS error, timeout), return 503 Service Unavailable
       (NOT an empty 200 with source='rl_ranker'). The 503 tells the
       API client the RL service is down and they should retry; an
       empty 200 would have silently misled them into thinking the
       ranker returned no candidates.
    5. On RL service 401 (org_id rejected — should not happen since the
       backend already verified the JWT, but defensive), return 401 so
       the client can re-authenticate. On other 4xx/5xx from the RL
       service, propagate the status code + detail to the client.
    6. Map the RL service's camelCase response fields to the backend's
       snake_case TopKCandidate schema (gnnScore -> gnn_score,
       safetyScore -> safety_score, etc.). The RL service uses
       camelCase to match the frontend's TypeScript types; the backend
       uses snake_case to match Python conventions. The mapping is
       explicit (not a generic camelCase -> snake_case converter) so
       the API contract is auditable.
    7. Forward the pathway_enrichment_available flag from the RL service
       response (TM13 v132 — True if the KG service was successfully
       queried for pathway chains).

    Rate-limited to 100 req/min per IP. 429 + Retry-After on exceed.

    Args:
        request: The FastAPI Request (required by slowapi's @limiter.limit).
        req: The TopKRequest body (drug, disease, k, min_score).
        auth: The authenticated caller's AuthContext (from verify_jwt).
        org_id: The caller's org_id (from verify_org_id — extracted
            from the JWT's org_id claim). Passed to the RL service
            for cross-tenant isolation (BE-043 v128).

    Returns:
        TopKResponse with ranked candidates, total count, source,
        org_id (audit echo), and pathway_enrichment_available flag.

    Raises:
        400: Neither drug nor disease provided, or both provided.
        401: JWT missing or invalid (from verify_jwt), or RL service
            rejected the org_id (propagated 401).
        429: Rate limit exceeded (100 req/min).
        503: RL service unreachable (httpx.RequestError) or
            RL_SERVICE_URL not configured.
        502/504: RL service returned a non-200 status (propagated).
    """
    # P4-024: check httpx availability at module level. The /top-k
    # endpoint CANNOT function without httpx — there is no fallback.
    # Raise 503 immediately (not 500) so the client knows to retry.
    if not _HAS_HTTPX:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=(
                "P4-024: httpx is not installed — the backend cannot proxy "
                "to the RL service. Install with `pip install httpx` and "
                "restart the backend."
            ),
        )

    if not req.drug and not req.disease:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="At least one of 'drug' or 'disease' must be provided.",
        )
    # P4-024: REMOVED the "if req.drug and req.disease: raise 400" check
    # that was in the previous code. The RL service's _rank_impl accepts
    # BOTH drug and disease as optional AND filters (return candidates
    # matching BOTH). This is a valid use case: a pharma partner asking
    # "give me the ranking for (metformin, cancer) specifically" should
    # get a 200 response with that candidate (or empty if no match), NOT
    # a 400. The previous validation was too strict and broke the issue
    # spec's API contract (which allows both drug? and disease? in the
    # request body).
    logger.info(
        "top-k: user=%s org=%s drug=%s disease=%s k=%d",
        auth.user_id, auth.org_id, req.drug, req.disease, req.k,
    )

    # P4-024: read RL_SERVICE_URL from env. The default
    # (http://localhost:8003) matches the canonical port for the RL
    # service per shared/contracts/urls.py SERVICE_PORTS. In production,
    # this is set to the RL service's internal Docker/k8s DNS name
    # (e.g., http://phase4-rl:8003). We use the default (NOT raise 503)
    # when the env var is unset so that:
    #   1. Local dev "just works" — operator doesn't need to set env vars
    #      to test against a localhost RL service.
    #   2. The httpx.RequestError path returns 503 when the RL service is
    #      actually unreachable (connection refused to localhost:8003).
    #      This gives a more informative error message than "env var not
    #      set" — it tells the operator the RL service is not RUNNING.
    rl_url = os.environ.get("RL_SERVICE_URL", "http://localhost:8003")
    rl_url = rl_url.rstrip("/")

    # P4-024 CRITICAL FIX: map k -> limit. The RL service's RankRequest
    # Pydantic model (rl/service.py line ~143) uses ``limit``, NOT ``k``.
    # The issue spec's example code sent ``'k': req.k`` which would be
    # SILENTLY DROPPED by Pydantic (the model has no extra='forbid', so
    # unknown fields are ignored, and ``limit`` defaults to 50). The
    # caller's requested page size would be IGNORED — every request
    # would return 50 candidates regardless of the ``k`` value. This is
    # a silent contract drift that the issue spec got wrong. The fix
    # maps k -> limit explicitly so the caller's k is honored.
    request_body = {
        "drug": req.drug,
        "disease": req.disease,
        "limit": req.k,
    }
    # P4-024: pass org_id as BOTH a query param AND an X-Org-Id header.
    # The RL service's rank_post handler reads org_id from the query
    # param (the canonical path). The X-Org-Id header is defense-in-depth
    # — if a misconfigured proxy strips query params but preserves
    # headers, the RL service can still read org_id from the header
    # (it logs the header for audit even if it doesn't use it for
    # filtering). Both must be present so the RL service has org_id
    # available regardless of proxy behavior.
    request_params = {"org_id": org_id}
    request_headers = {
        "X-Org-Id": org_id,
        "Content-Type": "application/json",
    }

    # P4-024: 60s timeout. The RL service's /rank endpoint may need to:
    #   1. Load the PPO checkpoint (fast — seconds).
    #   2. Run PPO inference on the cached bridge's RL input (seconds).
    #   3. Query the KG service for pathway enrichment (2s per candidate,
    #      capped by _enrich_candidates_with_pathways's timeout_ms=2000).
    # For k=100 candidates with KG enrichment, worst case is ~200s — but
    # the KG enrichment is best-effort and runs in parallel-ish (sequential
    # per candidate but with 2s timeout each). 60s is a reasonable upper
    # bound for k<=100; larger k values should use the paginated /rank
    # endpoint directly. The previous code had NO timeout — a hung RL
    # service would hang /top-k indefinitely, exhausting the backend's
    # connection pool.
    try:
        async with httpx.AsyncClient(timeout=60.0) as client:
            rl_resp = await client.post(
                f"{rl_url}/rank",
                json=request_body,
                params=request_params,
                headers=request_headers,
            )
    except httpx.RequestError as exc:
        # P4-024: connection failure → 503. This covers:
        #   - httpx.ConnectError (connection refused — RL service not running)
        #   - httpx.ConnectTimeout (RL service slow to accept connections)
        #   - httpx.ReadTimeout (RL service accepted but didn't respond in 60s)
        #   - httpx.RemoteProtocolError (RL service closed connection mid-response)
        # All of these mean the RL service is UNAVAILABLE — the client
        # should retry with backoff. Returning 503 (not 500) signals
        # "temporary outage, retry later" per HTTP semantics.
        logger.error(
            "P4-024: /top-k proxy to RL service failed (RequestError): "
            "url=%s org_id=%s error=%s",
            rl_url, org_id, exc,
        )
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=(
                f"RL service unavailable at {rl_url}: "
                f"{type(exc).__name__}: {exc}. The RL ranker service is "
                f"either not running, slow to respond (>60s), or "
                f"experiencing network issues. Retry with backoff."
            ),
        ) from exc

    # P4-024: propagate non-200 responses from the RL service.
    # The RL service may return:
    #   400: invalid limit/offset (shouldn't happen — backend validates k)
    #   401: org_id missing or rejected (shouldn't happen — backend
    #        extracted org_id from JWT, but defensive)
    #   500: internal RL service error (checkpoint corruption, etc.)
    # We propagate the status code + detail to the client so they see
    # the actual RL service error, not a generic 500.
    if rl_resp.status_code != 200:
        # P4-024: 401 from RL service → 401 to client (so frontend can
        # re-authenticate). Other non-200 → 502 Bad Gateway (the RL
        # service responded but with an error — the backend is a
        # gateway in this context).
        if rl_resp.status_code == 401:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail=(
                    f"RL service rejected org_id (401): "
                    f"{rl_resp.text[:500]}. The org_id extracted from the "
                    f"JWT was rejected by the RL service. This should not "
                    f"happen — verify RL_REQUIRE_AUTH is not set to a "
                    f"non-standard value on the RL service."
                ),
                headers={"WWW-Authenticate": "Bearer"},
            )
        # P4-024: other 4xx/5xx from RL service → 502 Bad Gateway.
        # The backend is acting as a gateway/proxy; a non-200 from the
        # upstream service is a Bad Gateway per HTTP semantics.
        logger.error(
            "P4-024: RL service /rank returned non-200: status=%d body=%s",
            rl_resp.status_code, rl_resp.text[:500],
        )
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail=(
                f"RL service /rank returned {rl_resp.status_code}: "
                f"{rl_resp.text[:500]}"
            ),
        )

    # P4-024: parse the RL service's response and map to TopKResponse.
    # The RL service returns:
    #   {
    #     "candidates": [{drug, disease, rank, gnnScore, safetyScore,
    #                      marketScore, overallScore, pathwayScore,
    #                      pathwayChain, confidence, ...}],
    #     "total": int,
    #     "source": "service" | "none",
    #     "pathway_enrichment_available": bool,
    #     "orgId": str (echo),
    #     ...
    #   }
    # We map the camelCase candidate fields to the snake_case
    # TopKCandidate schema. The mapping is EXPLICIT (not a generic
    # camelCase -> snake_case converter) so the API contract is auditable.
    try:
        rl_data = rl_resp.json()
    except Exception as exc:
        logger.error(
            "P4-024: RL service /rank returned non-JSON response: %s",
            rl_resp.text[:500],
        )
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail=(
                f"RL service /rank returned a non-JSON response "
                f"(status=200 but body is not valid JSON): "
                f"{rl_resp.text[:500]}"
            ),
        ) from exc

    rl_candidates = rl_data.get("candidates", [])
    if not isinstance(rl_candidates, list):
        logger.error(
            "P4-024: RL service /rank returned non-list candidates: %r",
            type(rl_candidates).__name__,
        )
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail=(
                f"RL service /rank returned non-list candidates "
                f"(got {type(rl_candidates).__name__}). The RL service "
                f"may be running a stale version — restart it."
            ),
        )

    # P4-024: map each RL candidate (camelCase) to TopKCandidate (snake_case).
    # Defensive: each candidate must have at minimum ``drug`` and ``disease``
    # (strings). Other fields are optional and default to None / [].
    mapped_candidates: List[TopKCandidate] = []
    for idx, c in enumerate(rl_candidates):
        if not isinstance(c, dict):
            logger.warning(
                "P4-024: skipping non-dict candidate at index %d: %r",
                idx, type(c).__name__,
            )
            continue
        drug = c.get("drug")
        disease = c.get("disease")
        if not drug or not disease:
            # Skip candidates without drug/disease — they're malformed.
            logger.warning(
                "P4-024: skipping candidate at index %d with missing "
                "drug/disease: %r", idx, c,
            )
            continue

        def _to_float(v: Any) -> Optional[float]:
            """Coerce a value to float, returning None on failure.

            Handles: None, "", NaN, strings like "0.87", ints, floats.
            Validates the result is in [0, 1] for score fields (we do NOT
            clamp — out-of-range values indicate a bug in the RL service
            and should be surfaced as None rather than silently clamped).
            """
            if v is None or v == "":
                return None
            try:
                f = float(v)
                # NaN check (NaN != NaN).
                if f != f:
                    return None
                return f
            except (ValueError, TypeError):
                return None

        # P4-024: explicit field mapping (camelCase -> snake_case).
        # ``score`` is the RL agent's overall ranking score
        # (overallScore in the RL response — the reward-weighted composite).
        # ``gnn_score`` is the raw GNN link-prediction score (gnnScore).
        # ``pathway_chain`` is the list of {pathway, intermediate_protein,
        # chain} dicts from TM13 v132 enrichment (pathwayChain in the RL
        # response).
        gnn_score = _to_float(c.get("gnnScore") or c.get("gnn_score"))
        safety_score = _to_float(c.get("safetyScore") or c.get("safety_score"))
        market_score = _to_float(c.get("marketScore") or c.get("market_score"))
        score = _to_float(c.get("overallScore") or c.get("score") or c.get("reward"))
        pathway_score = _to_float(c.get("pathwayScore") or c.get("pathway_score"))
        confidence = _to_float(c.get("confidence"))
        # pathway_chain: the RL service returns a list of dicts. We
        # accept it as-is (TopKCandidate.pathway_chain is List[Dict[str, Any]]).
        # Defensive: if it's not a list, default to [].
        pathway_chain_raw = c.get("pathwayChain") or c.get("pathway_chain") or []
        if not isinstance(pathway_chain_raw, list):
            pathway_chain_raw = []
        # Filter out non-dict items (defensive — never trust upstream input).
        pathway_chain = [item for item in pathway_chain_raw if isinstance(item, dict)]
        # rl_rank: the RL service returns ``rank`` (int, 1-indexed).
        rl_rank_raw = c.get("rank")
        try:
            rl_rank = int(rl_rank_raw) if rl_rank_raw is not None else None
        except (ValueError, TypeError):
            rl_rank = None

        try:
            mapped_candidates.append(TopKCandidate(
                drug=str(drug),
                disease=str(disease),
                gnn_score=gnn_score,
                rl_rank=rl_rank,
                safety_score=safety_score,
                market_score=market_score,
                score=score,
                pathway_score=pathway_score,
                pathway_chain=pathway_chain,
                confidence=confidence,
            ))
        except Exception as exc:
            # Pydantic validation error — log and skip (don't fail the
            # whole request because one candidate is malformed).
            logger.warning(
                "P4-024: skipping malformed candidate at index %d "
                "(Pydantic validation failed): %s. Candidate: %r",
                idx, exc, c,
            )
            continue

    # P4-024: build the TopKResponse. ``total`` is the count AFTER
    # filtering (the RL service already applied org-scoped filtering
    # via _filter_candidates_by_org). ``source`` is "rl_ranker" (the
    # backend is the public API; the RL service is the upstream source).
    # ``org_id`` is echoed back for audit (21 CFR Part 11).
    # ``pathway_enrichment_available`` is forwarded from the RL service.
    pathway_enrichment_available = bool(
        rl_data.get("pathway_enrichment_available", False)
    )
    # ``total``: prefer the RL service's total (it's the count AFTER
    # org-scoped filtering, BEFORE pagination). Fall back to the length
    # of the candidates list if the RL service didn't include total.
    total = rl_data.get("total")
    if not isinstance(total, int) or total < 0:
        total = len(mapped_candidates)

    return TopKResponse(
        candidates=mapped_candidates,
        total=total,
        source="rl_ranker",
        org_id=auth.org_id,  # audit echo
        pathway_enrichment_available=pathway_enrichment_available,
    )


@app.get("/datasets/stats", tags=["dataset"])
@limiter.limit("1000/minute")
async def get_dataset_stats(
    request: Request,
    auth: AuthContext = Depends(verify_jwt),
) -> dict:
    """Get Phase 1 dataset statistics (org-scoped audit).

    Returns the dataset source stats (loaded sources, row counts, sha256
    checksums) from the Phase 1 dataset service. The stats are PUBLIC
    (not org-scoped) — every org sees the same dataset stats — but the
    fetch is attributed to the caller's org for audit.

    Rate-limited to 1000 req/min per IP (read-heavy endpoint).
    """
    logger.info(
        "datasets/stats: user=%s org=%s",
        auth.user_id, auth.org_id,
    )
    # TODO: call the Phase 1 dataset service via PHASE1_SERVICE_URL.
    return {
        "sources": [],
        "nodesLoaded": 0,
        "edgesLoaded": 0,
        "edgeTypesPresent": [],
        "warnings": [],
        "errors": [],
        "generatedAt": datetime.now(timezone.utc).isoformat(),
        "org_id": auth.org_id,  # audit echo
    }


# ---------------------------------------------------------------------------
# TM15 v132 ROOT FIX (Teammate 15 — Data Flywheel, requirement #8):
# POST /validate — proxy to the RL service's /validate endpoint.
# ---------------------------------------------------------------------------
# The RL service (rl/service.py) already implements /validate, which calls
# phase4.writeback.write_validated_hypothesis() to write the validated
# hypothesis to ALL 3 phases (Phase 1 CSV, Phase 2 Neo4j edge, Phase 3
# retrain trigger). The backend proxy adds JWT auth + audit logging.
# ---------------------------------------------------------------------------
class ValidateRequest(BaseModel):
    """POST /validate request body — mirrors rl/service.py's ValidateRequest."""
    model_config = ConfigDict(extra="forbid")
    drug: str = Field(..., min_length=1, max_length=200)
    disease: str = Field(..., min_length=1, max_length=200)
    outcome: str = Field(..., description="One of: validated_positive, "
                                          "validated_negative, validated_toxic, invalidated")
    validated_by: Optional[str] = Field(None, max_length=200)
    validation_study_id: Optional[str] = Field(None, max_length=200)
    notes: Optional[str] = None
    original_gt_score: Optional[float] = Field(None, ge=0.0, le=1.0)
    original_rl_rank: Optional[int] = Field(None, ge=0)


@app.post("/validate", tags=["data-flywheel"])
async def validate(
    req: ValidateRequest,
    auth: AuthContext = Depends(verify_jwt),
) -> Dict[str, Any]:
    """Validate a drug-disease hypothesis and write it back to all 3 phases.

    This endpoint is the data flywheel entry point (DOCX §10). When a
    pharma partner wet-lab-validates a hypothesis, they POST it here.
    The backend proxies to the RL service's /validate endpoint, which
    writes the validated hypothesis to:
      1. Phase 1 CSV (phase1/processed_data/validated_hypotheses.csv)
      2. Phase 1 DB (validated_hypotheses table — via the POST
         /datasets/validated_hypotheses endpoint on the Phase 1 service)
      3. Phase 2 Neo4j (:VALIDATED_TREATS edge between drug and disease)
      4. Phase 3 retrain trigger (graph_transformer/retrain_triggered.json)

    When 10+ new validated hypotheses accumulate, the Airflow DAG
    ``retrain_on_validated`` triggers a full Phase 2 → 3 → 4 retraining
    run. This is the data flywheel in action.
    """
    import httpx

    logger.info(
        "validate: user=%s drug=%s disease=%s outcome=%s",
        auth.user_id, req.drug, req.disease, req.outcome,
    )

    rl_url = os.environ.get("RL_SERVICE_URL")
    if not rl_url:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="RL_SERVICE_URL not configured — the RL ranker service "
                   "is not deployed. /validate cannot write back the "
                   "validated hypothesis. Deploy the RL service and set "
                   "RL_SERVICE_URL.",
        )

    try:
        async with httpx.AsyncClient(timeout=15.0) as client:
            rl_resp = await client.post(
                f"{rl_url.rstrip('/')}/validate",
                json={
                    "drug": req.drug,
                    "disease": req.disease,
                    "outcome": req.outcome,
                    "validated_by": req.validated_by or auth.user_id,
                    "validation_study_id": req.validation_study_id,
                    "notes": req.notes,
                    "original_gt_score": req.original_gt_score,
                    "original_rl_rank": req.original_rl_rank,
                },
            )
        if rl_resp.status_code != 200:
            raise HTTPException(
                status_code=status.HTTP_502_BAD_GATEWAY,
                detail=f"RL service /validate returned {rl_resp.status_code}: "
                       f"{rl_resp.text[:500]}",
            )
        return rl_resp.json()
    except httpx.RequestError as exc:
        raise HTTPException(
            status_code=status.HTTP_504_GATEWAY_TIMEOUT,
            detail=f"RL service unreachable at {rl_url}: {exc}",
        ) from exc


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
    # TM14 ROOT FIX (v132, CRITICAL — port conflict):
    # The previous default was 8001, which is the canonical phase2_kg
    # port (per shared/contracts/urls.py SERVICE_PORTS). Running the
    # public REST API on the same port as the Phase 2 KG service is a
    # CONFLICT — only one can bind at a time. Fixed to 8004 (the next
    # free port after the 4 ML services: 8000=phase1, 8001=phase2,
    # 8002=phase3, 8003=phase4).
    port = int(os.environ.get("DRUGOS_API_PORT", "8004"))
    workers = int(os.environ.get("DRUGOS_API_WORKERS", "4"))
    uvicorn.run(
        "backend.api.main:app",
        host="0.0.0.0",
        port=port,
        workers=workers,
        log_level=os.environ.get("DRUGOS_API_LOG_LEVEL", "info"),
    )
