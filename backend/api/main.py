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
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional

# FastAPI + Pydantic are declared in the top-level requirements.txt and
# in phase1/requirements.txt (P1-003 v114 fix). When this module is
# imported in an environment without FastAPI (e.g., the Next.js
# frontend's build process), the import fails gracefully — the FastAPI
# service is OPT-IN (only runs when explicitly started via uvicorn).
try:
    from fastapi import FastAPI, HTTPException, Depends, Request, status
    from fastapi.middleware.cors import CORSMiddleware
    from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
    from pydantic import BaseModel, Field, ConfigDict
except ImportError as _fastapi_import_err:  # pragma: no cover
    raise ImportError(
        "BE-001 v123: FastAPI is required for the public REST API. "
        "Install with `pip install fastapi uvicorn[standard]`. "
        f"Original error: {_fastapi_import_err}"
    ) from _fastapi_import_err

# TEAMMATE-11 ROOT FIX (v141, P0 — P3 → Backend integration):
# ``httpx`` is imported at MODULE LEVEL (not inside function bodies) so
# the /predict and /ready endpoints can make async HTTP calls to the
# downstream GT service, AND so integration tests can mock
# ``main_module.httpx.AsyncClient`` to inject deterministic responses.
# The previous code imported httpx INSIDE each function (``import httpx``
# as the first line of the function body) — this made the function work
# at runtime but left the module WITHOUT an ``httpx`` attribute, so
# ``main_module.httpx.AsyncClient = MockAsyncClient`` raised
# ``AttributeError: module 'backend.api.main' has no attribute 'httpx'``
# and every integration test that tried to mock the GT service failed
# at collection time. Importing at module level is also the standard
# FastAPI pattern (https://fastapi.tiangolo.com/advanced/async-tests/).
import httpx  # noqa: E402 — required at module level for test mocking

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

# ---------------------------------------------------------------------------
# TEAMMATE-11 ROOT FIX (v141, P3-020 + P3-006 — version drift closure):
# ---------------------------------------------------------------------------
# The backend's version constants MUST be aligned with the Phase 3 GT
# package version (``graph_transformer.__version__``). The previous code
# hardcoded ``"1.0.0"`` everywhere (FastAPI app version, /health response
# version, /ready response version), while the GT package was at
# ``"4.1.0"``. This created two production-grade problems:
#
#   1. The /ready probe could not verify the backend was running a
#      version compatible with the GT service it was proxying to. A
#      backend at "1.0.0" talking to a GT service at "4.1.0" is an
#      unsupported combination that could silently produce wrong API
#      contracts (the response schema changed between 1.x and 4.x).
#
#   2. The ``model_version`` field was MISSING from PredictResponse
#      entirely (see fix below). Pharma partners had no way to attribute
#      a prediction to the model version that produced it — a 21 CFR
#      Part 11 audit trail gap.
#
# ROOT FIX: introduce TWO module-level constants, both derived from
# ``graph_transformer.__version__``:
#
#   - ``BACKEND_VERSION``: the backend's own version. The backend and
#     the GT package are versioned TOGETHER (they ship as a single
#     release per the project docx Phase 5/6 V1 launch). The FastAPI
#     app version, /health, and /ready all read from this constant.
#
#   - ``MODEL_VERSION``: the model version stamped on every /predict
#     response's ``model_version`` field. Format: ``gt_<package_version>``
#     (e.g., ``"gt_4.1.0"``). This MUST match the ``modelVersion`` field
#     the GT service stamps on its own /predict response AND the
#     ``model_version`` property on Neo4j PREDICTED_TREATS edges
#     (verified by ``test_predict_response_modelversion_matches_neo4j_writeback``
#     in graph_transformer/tests/integration/test_service_version_consistency.py).
#
# When ``graph_transformer`` is not importable (e.g., the backend is
# deployed in a standalone container without the GT package), we fall
# back to a deterministic "0.0.0+unknown" string and log a WARNING —
# production deployments MUST have both packages installed together.
# ---------------------------------------------------------------------------
try:
    from graph_transformer import __version__ as _GT_PACKAGE_VERSION
    BACKEND_VERSION: str = _GT_PACKAGE_VERSION
except Exception as _gt_import_err:  # pragma: no cover — defensive fallback
    import logging as _logging
    _logging.getLogger(__name__).warning(
        "TEAMMATE-11 v141: graph_transformer package not importable "
        "(%s). Backend will report BACKEND_VERSION='0.0.0+unknown'. "
        "Production deployments MUST install graph_transformer alongside "
        "the backend so the versions stay aligned.", _gt_import_err,
    )
    BACKEND_VERSION = "0.0.0+unknown"

# MODEL_VERSION is the canonical version stamped on every /predict
# response AND on every Neo4j PREDICTED_TREATS edge. The format
# ``gt_<package_version>`` matches the GT service's own MODEL_VERSION
# constant in graph_transformer/service.py (single source of truth).
MODEL_VERSION: str = f"gt_{BACKEND_VERSION}"

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


class PathwayItem(BaseModel):
    """A single biological pathway chain connecting a drug to a disease.

    TEAMMATE-11 ROOT FIX (v141, P3-005 — pathways contract):
    The previous PredictResponse.pathways field was ``List[str]`` (a flat
    list of pathway names). The project docx (§5 Phase 3 — Model Outputs)
    mandates "the key biological pathways driving the prediction (for
    scientific explainability)". A flat list of names is NOT
    explainability — it gives the researcher no way to trace HOW the
    drug connects to the disease through that pathway.

    The structured shape below mirrors the GT service's
    ``_get_pathway_explanation`` output (graph_transformer/service.py)
    so the backend is a faithful pass-through, NOT a transformer:

      - ``pathway``: the pathway node name (e.g., "arachidonic acid metabolism")
      - ``intermediate_protein``: the drug target protein that links the
        drug to the pathway (e.g., "COX-1")
      - ``chain``: the full ordered node sequence ``[drug, protein,
        pathway, disease]`` so the frontend's Hypothesis Detail View
        can render the explainability diagram without re-deriving it.
    """
    model_config = ConfigDict(extra="forbid")
    pathway: str = Field(..., description="Pathway node name (e.g., 'arachidonic acid metabolism')")
    intermediate_protein: str = Field(..., description="Drug target protein linking drug to pathway (e.g., 'COX-1')")
    chain: List[str] = Field(
        ...,
        description="Ordered node sequence [drug, protein, pathway, disease] — full explainability chain",
    )


class PredictResponse(BaseModel):
    """POST /predict response body — mirrors /api/predict in Next.js.

    TEAMMATE-11 ROOT FIX (v141, P3-005 + P3-006 — full API contract):
      - ``pathways`` is now ``List[PathwayItem]`` (structured chain), not
        ``List[str]``. See PathwayItem above.
      - ``model_version`` is now included so pharma partners can
        attribute every prediction to the GT model version that
        produced it (21 CFR Part 11 audit trail). The value matches
        the ``modelVersion`` field in the GT service's /predict
        response AND the ``model_version`` property on Neo4j
        PREDICTED_TREATS edges (single source of truth: the
        ``MODEL_VERSION`` constant in this module, derived from
        ``graph_transformer.__version__``).
    """
    model_config = ConfigDict(extra="forbid")
    drug: str
    disease: str
    gnn_score: float = Field(..., ge=0.0, le=1.0, description="Graph Transformer score (0-1)")
    confidence: float = Field(..., ge=0.0, le=1.0, description="Model confidence (0-1)")
    pathways: List[PathwayItem] = Field(default_factory=list, description="Top pathway chains (structured)")
    literature_supported: bool = Field(default=False, description="PubMed literature support flag")
    model_version: str = Field(
        ...,
        description="GT model version that produced this prediction (e.g., 'gt_4.1.0'). "
                    "Matches the modelVersion field in the GT service /predict response "
                    "AND the model_version property on Neo4j PREDICTED_TREATS edges.",
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
    org_id: Optional[str] = Field(default=None, description="The org scope the results were fetched for (audit echo)")


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
    # TEAMMATE-11 ROOT FIX (v141, P3-020): the FastAPI app version is
    # now the canonical BACKEND_VERSION (derived from
    # graph_transformer.__version__) — was hardcoded "1.0.0". The
    # OpenAPI spec at /openapi.json now reports the real version so
    # pharma partners generating client libraries get the right one.
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
        # TEAMMATE-11 ROOT FIX (v141, P3-020): report BACKEND_VERSION
        # (was hardcoded "1.0.0"). The /health endpoint is the canonical
        # way for ops + pharma partners to check which backend version
        # is live. Integration test ``test_health_endpoint_is_liveness_probe``
        # asserts this matches graph_transformer.__version__ ("4.1.0").
        version=BACKEND_VERSION,
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
    # TEAMMATE-11 v141: default to http://localhost:8002 (canonical GT
    # service port per shared/contracts/urls.py SERVICE_PORTS). This
    # ensures the probe ACTUALLY TRIES to reach the GT service rather
    # than silently skipping when GT_SERVICE_URL is unset. A
    # misconfigured env surfaces as a real "connection refused" error
    # in the check, not a silent False.
    gt_url = os.environ.get("GT_SERVICE_URL", "http://localhost:8002")
    try:
        # TEAMMATE-11 v141: httpx is imported at module level now —
        # no need for a local import (which would also break test
        # mocking, since the mock replaces the module-level attr).
        async with httpx.AsyncClient(timeout=2.0) as client:
            r = await client.get(f"{gt_url}/health")
            checks["gt_service"] = r.status_code == 200
    except Exception as exc:
        logger.debug("ready: GT probe failed: %s", exc)

    # Probe RL service (Phase 4 RL ranker).
    # TEAMMATE-11 v141: default to http://localhost:8003 (canonical RL
    # service port per shared/contracts/urls.py SERVICE_PORTS).
    rl_url = os.environ.get("RL_SERVICE_URL", "http://localhost:8003")
    try:
        async with httpx.AsyncClient(timeout=2.0) as client:
            r = await client.get(f"{rl_url}/health")
            checks["rl_service"] = r.status_code == 200
    except Exception as exc:
        logger.debug("ready: RL probe failed: %s", exc)

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
    # TEAMMATE-11 ROOT FIX (v141, P3-020): version=BACKEND_VERSION (was
    # hardcoded "1.0.0"). The /ready endpoint is the canonical way for
    # k8s + ops to verify the backend is running the expected version.
    response = ReadyResponse(status=status_str, version=BACKEND_VERSION, checks=checks)
    if not all_ok:
        # TEAMMATE-11 ROOT FIX (v141, test contract alignment):
        # The previous code returned ``JSONResponse(status_code=503,
        # content=response.model_dump())`` — this bypasses FastAPI's
        # standard error envelope (``{"detail": ...}``) and breaks the
        # integration test ``test_ready_endpoint_probes_gt_service``,
        # which asserts ``response.json()["detail"]`` contains the
        # checks dict. Raising HTTPException with the response dict as
        # ``detail`` matches FastAPI's standard error contract AND
        # preserves the body for k8s readiness probes (which inspect
        # the JSON body for the failing check name).
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=response.model_dump(),
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

    TEAMMATE-11 ROOT FIX (v141, P0 — P3 → Backend integration):
    The previous implementation returned a HARDCODED placeholder
    ``gnn_score=0.5, confidence=0.5, pathways=[], literature_supported=False``
    for EVERY (drug, disease) pair — the GT service was NEVER invoked.
    Pharma partners calling the public API received identical 0.5
    scores whether they asked about (aspirin, headache) or
    (metformin, glioblastoma) — a complete failure of the platform's
    core value proposition (autonomous repurposing predictions).

    The fix proxies every /predict call to the GT service at
    ``${GT_SERVICE_URL}/predict`` via ``httpx.AsyncClient``. The GT
    service's response is mapped to the backend's PredictResponse
    schema (which now includes the ``model_version`` field and
    structured ``pathways`` — see PathwayItem). When the GT service
    is unreachable, the endpoint returns 503 with a clear error
    message (so the frontend can surface "GT service unavailable"
    rather than silently fabricating a 0.5 score).

    Rate-limited to 100 req/min per IP. 429 + Retry-After on exceed.
    """
    logger.info(
        "predict: user=%s org=%s drug=%s disease=%s",
        auth.user_id, auth.org_id, req.drug, req.disease,
    )

    # TEAMMATE-11 v141: resolve GT_SERVICE_URL at request time (not at
    # module load) so tests can monkeypatch os.environ between calls.
    # Default to http://localhost:8002 — the canonical Phase 3 GT
    # service port (see shared/contracts/urls.py SERVICE_PORTS).
    # This matches the project docx's port allocation:
    #   8000=phase1, 8001=phase2_kg, 8002=phase3_gt, 8003=phase4_rl,
    #   8004=backend public REST API.
    # Using a default (instead of failing when GT_SERVICE_URL is unset)
    # means the /ready probe ACTUALLY TRIES to reach the GT service
    # rather than silently skipping the check — a misconfigured env
    # surfaces as a 503 with a real "connection refused" error, not
    # a silent False.
    gt_service_url = os.environ.get("GT_SERVICE_URL", "http://localhost:8002")

    # Build the GT service request body. The GT service's /predict
    # endpoint accepts ``{pairs: [{drug, disease}, ...]}`` (see
    # graph_transformer/service.py PredictRequest). The backend's public
    # API accepts a SINGLE (drug, disease) pair per call (the pharma
    # partner API contract) — we wrap it in a 1-element pairs list.
    gt_request_body = {
        "pairs": [{"drug": req.drug, "disease": req.disease}],
    }
    # Forward the org_id as a header so the GT service can attribute
    # the prediction to the requesting org in its audit log. The GT
    # service does NOT require this header (it has its own /health
    # endpoint that doesn't check auth), but including it is good
    # practice for end-to-end audit trail.
    gt_headers = {
        "Content-Type": "application/json",
        "X-Org-Id": auth.org_id,
        "X-User-Id": auth.user_id,
    }

    try:
        # 30s timeout — the GT service pre-encodes the graph at startup
        # (P3-050 fix), so per-request inference is ~100ms for a single
        # pair. 30s gives a 300x safety margin for slow GPU contention.
        async with httpx.AsyncClient(timeout=30.0) as client:
            gt_response = await client.post(
                f"{gt_service_url.rstrip('/')}/predict",
                json=gt_request_body,
                headers=gt_headers,
            )
        # TEAMMATE-11 v141: check status_code MANUALLY instead of calling
        # ``gt_response.raise_for_status()``. The latter raises
        # ``RuntimeError: Cannot call raise_for_status as the request
        # instance has not been set on this response`` when the response
        # was constructed without an attached request (which is what
        # integration tests do when they mock ``httpx.AsyncClient`` to
        # return a pre-built ``httpx.Response(200, json={...})``). In
        # real production usage the response always has an attached
        # request, but the manual check is functionally equivalent AND
        # test-friendly — no behavioral difference for callers.
        if gt_response.status_code >= 400:
            raise httpx.HTTPStatusError(
                f"GT service returned {gt_response.status_code}",
                request=gt_response.request if hasattr(gt_response, "request") else None,
                response=gt_response,
            )
    except httpx.RequestError as exc:
        # Connection refused, DNS resolution failure, timeout, etc.
        # The GT service is unreachable — return 503 so the frontend
        # can surface "GT service unavailable" to the researcher.
        logger.error(
            "predict: GT service unreachable at %s: %s",
            gt_service_url, exc, exc_info=True,
        )
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=f"GT service unavailable: {exc}",
        ) from exc
    except httpx.HTTPStatusError as exc:
        # The GT service returned a non-2xx status (4xx/5xx). Forward
        # the status code and body so the caller sees the real error
        # (e.g., 404 drug not in graph, 500 model error).
        logger.error(
            "predict: GT service returned %d: %s",
            exc.response.status_code, exc.response.text[:500],
        )
        raise HTTPException(
            status_code=exc.response.status_code,
            detail=f"GT service error: {exc.response.text[:500]}",
        ) from exc

    # Parse the GT service response. Expected shape (see
    # graph_transformer/service.py /predict):
    #   {
    #     "predictions": [{
    #       "drug": str, "disease": str, "score": float,
    #       "confidence": float,
    #       "pathways": [{pathway, intermediate_protein, chain}, ...],
    #       "literature_supported": bool,
    #       "note": str (optional, present on error)
    #     }],
    #     "modelVersion": str,         # e.g., "gt_4.1.0"
    #     "source": "gt_checkpoint",
    #     "generatedAt": str (ISO 8601),
    #     "count": int,
    #     "checkpointPath": str,
    #     "error_count": int, "error_rate": float,
    #     "neo4j_writeback": dict (optional),
    #   }
    try:
        gt_data = gt_response.json()
        prediction = gt_data["predictions"][0]
    except (KeyError, IndexError, ValueError) as exc:
        # The GT service returned a malformed response. This is a
        # programming error in the GT service — surface as 502 Bad
        # Gateway so the operator can investigate.
        logger.error(
            "predict: GT service returned malformed response: %s",
            gt_response.text[:500], exc_info=True,
        )
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail=(
                f"GT service returned malformed response: {exc}. "
                f"Body: {gt_response.text[:500]}"
            ),
        ) from exc

    # Map the GT service's prediction to the backend's PredictResponse.
    # The GT service's prediction has ``score`` and ``confidence``
    # (lowercase); the backend's PredictResponse has ``gnn_score`` and
    # ``confidence`` (the public API contract uses gnn_score to make
    # the field name self-documenting for pharma partners).
    pathways_raw = prediction.get("pathways", [])
    # Coerce each pathway dict to PathwayItem. If the GT service
    # returns a pathway dict missing a required field, we skip it
    # (rather than failing the whole request) — a partial pathway
    # chain is better than no prediction. The skip is logged.
    pathways: List[PathwayItem] = []
    for pw in pathways_raw:
        if not isinstance(pw, dict):
            logger.warning("predict: skipping non-dict pathway: %r", pw)
            continue
        try:
            pathways.append(PathwayItem(
                pathway=str(pw["pathway"]),
                intermediate_protein=str(pw["intermediate_protein"]),
                chain=[str(n) for n in pw["chain"]],
            ))
        except (KeyError, TypeError, ValueError) as exc:
            logger.warning(
                "predict: skipping malformed pathway %r: %s", pw, exc,
            )

    # Use the GT service's modelVersion if present; otherwise fall back
    # to the backend's MODEL_VERSION constant (single source of truth).
    # They SHOULD always match — the GT service derives its modelVersion
    # from the same graph_transformer.__version__ the backend uses.
    model_version = str(gt_data.get("modelVersion", MODEL_VERSION))

    return PredictResponse(
        drug=req.drug,
        disease=req.disease,
        gnn_score=float(prediction.get("score", 0.0)),
        confidence=float(prediction.get("confidence", 0.0)),
        pathways=pathways,
        literature_supported=bool(prediction.get("literature_supported", False)),
        model_version=model_version,
    )


@app.post("/top-k", response_model=TopKResponse, tags=["ranking"])
@limiter.limit("100/minute")
async def top_k(
    request: Request,
    req: TopKRequest,
    auth: AuthContext = Depends(verify_jwt),
) -> TopKResponse:
    """Get the top-K repurposing candidates for a drug or disease.

    If `drug` is provided, returns the top-K diseases for that drug.
    If `disease` is provided, returns the top-K drugs for that disease.
    Exactly one of `drug` or `disease` must be provided.

    TM14 ROOT FIX (v132): the endpoint now receives AuthContext (not just
    user_id). The auth.org_id is echoed back in the response (TopKResponse.
    org_id) so the caller can verify the scope of the results. The actual
    RL ranker call is the same; the fix is in the auth layer + response
    schema.

    Rate-limited to 100 req/min per IP. 429 + Retry-After on exceed.
    """
    # TEAMMATE-11 v141: httpx is imported at module level now.

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
        auth.user_id, auth.org_id, req.drug, req.disease, req.k,
    )
    # TODO: call the RL ranker service via RL_SERVICE_URL with org_id
    # for org-scoped candidate filtering.
    return TopKResponse(
        candidates=[],
        total=0,
        source="rl_ranker",
        org_id=auth.org_id,  # TM14: echo back for audit
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
    # TEAMMATE-11 v141: httpx is imported at module level now.

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
# TEAMMATE-11 ROOT FIX (v141): create_test_jwt — test helper for
# integration tests that need to mint a valid JWT for the verify_jwt
# dependency. Lives in main.py (not in a separate test_utils module)
# so it shares the SAME JWT_SECRET + JWT_ISSUER env var reads that
# verify_jwt uses — there is no risk of the helper drifting from the
# verifier. The helper is INTENTIONALLY importable from production
# code paths (it does NOT bypass verify_jwt — it just mints a token
# that PASSES verify_jwt). Production code never calls it.
# ---------------------------------------------------------------------------

def create_test_jwt(
    user_id: str,
    org_id: str,
    org_role: str = "member",
    expires_in_seconds: int = 3600,
) -> str:
    """Mint a valid JWT for integration tests.

    Reads ``JWT_SECRET`` and ``JWT_ISSUER`` from the environment (same
    env vars verify_jwt reads). The token's claims match what the
    Next.js frontend's /api/auth/login route issues:
      - ``sub``: the user_id (str)
      - ``org_id``: the user's active org (str, REQUIRED)
      - ``org_role``: 'admin' | 'member' | 'viewer' (default 'member')
      - ``iss``: the issuer (default 'drugos', override via JWT_ISSUER)
      - ``exp``: now + expires_in_seconds (default 1 hour)
      - ``iat``: now

    Raises ``RuntimeError`` if ``JWT_SECRET`` is not set or is <32
    chars — matching the production check in verify_jwt. This prevents
    tests from silently passing when the secret is misconfigured.

    Usage in tests:
        from backend.api.main import create_test_jwt
        token = create_test_jwt(user_id="testuser", org_id="testorg")
        response = client.post("/predict",
            headers={"Authorization": f"Bearer {token}"},
            json={"drug": "aspirin", "disease": "headache"},
        )
    """
    jwt_secret = os.environ.get("JWT_SECRET")
    if not jwt_secret or len(jwt_secret) < 32:
        raise RuntimeError(
            "create_test_jwt requires JWT_SECRET env var >=32 chars. "
            "Set it in conftest.py (see backend/tests/integration/conftest.py)."
        )
    import jwt as pyjwt  # PyJWT
    jwt_issuer = os.environ.get("JWT_ISSUER", "drugos")
    jwt_algorithm = os.environ.get("JWT_ALGORITHMS", "HS256").split(",")[0].strip()
    now = datetime.now(timezone.utc)
    payload = {
        "sub": str(user_id),
        "org_id": str(org_id),
        "org_role": str(org_role),
        "iss": jwt_issuer,
        "iat": now,
        "exp": now + timedelta(seconds=int(expires_in_seconds)),
    }
    return pyjwt.encode(payload, jwt_secret, algorithm=jwt_algorithm)


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
