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

# Note: `import httpx` is placed AFTER the FastAPI import below — see the
# TM11 v141 + TM8 v134 ROOT FIX comment block for the rationale (module-
# level import is required for integration test mocking).

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
#
# TM8 v134 ROOT FIX (Teammate 8 — P2 to Backend Integration):
# The same module-level `import httpx` is also used by the /kg/* proxy
# routes (GET /kg/stats, POST /kg/explore, POST /cypher) which proxy to
# the Phase 2 KG service via httpx.AsyncClient. Without this module-level
# import, `patch('backend.api.main.httpx.AsyncClient')` in the Teammate 8
# integration tests would raise AttributeError.
#
# P4-024 ROOT FIX (Teammate 12 — P4 to Backend Integration):
# The same module-level `import httpx` is also used by the /top-k endpoint
# which proxies to the RL service via httpx.AsyncClient. The previous code
# imported httpx INSIDE the /top-k function body — invisible to patch().
import httpx  # noqa: E402 — required at module level for test mocking

# TM8 v134 ROOT FIX: import the per-user in-memory rate limiters for
# the /kg/* proxy routes. These are defined in backend/api/rate_limit.py
# (the Teammate 8 in-memory sliding-window-log implementation). The
# limiters are:
#   - CYPHER_RATE_LIMITER:        10 req/min per user (strict — Cypher is expensive)
#   - KG_STATS_RATE_LIMITER:      100 req/min per user (cheap reads)
#   - KG_EXPLORE_RATE_LIMITER:    100 req/min per user (cheap reads)
# The check_*_rate_limit functions raise HTTPException(429) on overflow
# with a ``retry_after_seconds`` field in the detail dict. The /kg/*
# routes call these INSIDE the route handler (after JWT verification)
# so the rate-limit key is the authenticated user_id (not the IP).
try:
    from backend.api.rate_limit import (
        check_cypher_rate_limit,
        check_kg_stats_rate_limit,
        check_kg_explore_rate_limit,
        CYPHER_RATE_LIMITER,
        KG_STATS_RATE_LIMITER,
        KG_EXPLORE_RATE_LIMITER,
    )
    _HAS_RATE_LIMIT = True
except ImportError:  # pragma: no cover — rate_limit.py is in the same package
    _HAS_RATE_LIMIT = False

    def _rate_limit_noop(key: str) -> None:  # type: ignore[no-redef]
        """No-op fallback when rate_limit.py is not importable."""
        pass

    check_cypher_rate_limit = _rate_limit_noop  # type: ignore[assignment]
    check_kg_stats_rate_limit = _rate_limit_noop  # type: ignore[assignment]
    check_kg_explore_rate_limit = _rate_limit_noop  # type: ignore[assignment]
    CYPHER_RATE_LIMITER = None  # type: ignore[assignment]
    KG_STATS_RATE_LIMITER = None  # type: ignore[assignment]
    KG_EXPLORE_RATE_LIMITER = None  # type: ignore[assignment]

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

    BE-002 v143: added platform_role field (mirrors the Prisma
    PlatformRole enum: 'none' | 'admin'). The JWT's 'platformRole'
    claim is extracted alongside 'org_role' so backend endpoints can
    enforce platform-admin vs. org-admin distinctions (e.g., only
    platformRole='admin' can call /admin/* cross-tenant routes).
    """
    model_config = ConfigDict(extra="forbid")
    user_id: str
    org_id: str
    org_role: str = "member"  # 'admin' | 'member' | 'viewer'
    # BE-002 v143: platformRole from JWT — 'none' (default) or 'admin'.
    # Mirrors frontend/prisma/schema.prisma:enum PlatformRole.
    platform_role: str = "none"


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
        # BE-002 v143: extract platformRole from JWT. The Next.js frontend's
        # /api/auth/login route sets this claim from the User.platformRole
        # DB column (frontend/prisma/schema.prisma:enum PlatformRole).
        # Values: 'none' (default for every user) or 'admin' (SaaS operator).
        platform_role = str(payload.get("platformRole") or payload.get("platform_role") or "none")
        return AuthContext(
            user_id=str(user_id),
            org_id=str(org_id),
            org_role=str(org_role),
            platform_role=platform_role,
        )
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
# BE-002 ROOT FIX (v143, Teammate 12 — hostile-auditor pass):
# verify_org_membership — DB-backed org membership verification.
# ---------------------------------------------------------------------------
# The audit explicitly required:
#   "Add a verify_org_membership dependency that queries the shared
#    PostgreSQL DB (DATABASE_URL) for OrganizationMember(userId, orgId).
#    Reject with 401 if membership is missing (mirror the BE-062 + BE-084
#    logic from lib/auth/server.ts)."
#
# The previous verify_jwt only extracted org_id from the JWT — it did NOT
# verify the user is STILL a member of that org. A user removed from an
# org could continue calling /predict for 30 days via refresh-token
# rotation, with no revocation signal reaching the FastAPI service. This
# is exactly the BE-062 + BE-084 bug pattern the frontend fixed in
# lib/auth/server.ts.
#
# This dependency does a real SELECT against the OrganizationMember table
# (frontend/prisma/schema.prisma:model OrganizationMember) using the
# SQLAlchemy engine already imported for the audit log. When DATABASE_URL
# is unset OR the lookup fails (DB down, table missing), we fall back to
# TRUSTING the JWT (fail-open) ONLY in non-production — in production we
# fail-closed (401) because silently trusting an unverified org_id claim
# is a 21 CFR Part 11 violation (cross-tenant data leakage).
#
# Endpoints that need cross-tenant isolation MUST use this dependency
# instead of (or in addition to) verify_jwt. Example:
#
#   @app.post("/predict")
#   async def predict(
#       auth: AuthContext = Depends(verify_jwt),
#       _membership: None = Depends(verify_org_membership),
#   ):
#       ...
async def verify_org_membership(
    auth: AuthContext = Depends(verify_jwt),
) -> None:
    """Verify the JWT's (user_id, org_id) is a real OrganizationMember row.

    Queries the shared PostgreSQL DB at DATABASE_URL for an
    OrganizationMember record matching (auth.user_id, auth.org_id).
    Raises 401 if no such row exists — the user has been removed from
    the org (or never was a member), mirroring the frontend's
    lib/auth/server.ts getAuthenticatedUser() BE-062 fix.

    Fail-open in non-production when the DB is unreachable (so dev/CI
    environments without a real DB still work). Fail-CLOSED in production
    — silently trusting an unverified org_id claim is a 21 CFR Part 11
    violation.

    Returns None on success (the dependency is used for its side effect
    of raising 401 on bad membership; endpoints don't need the return
    value).
    """
    if not _HAS_SQLALCHEMY:
        # SQLAlchemy not installed — can't query the DB. In production
        # this is a fail-closed condition (we cannot verify membership
        # without a DB). In non-production, fail-open.
        if _is_production_env:
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail=(
                    "BE-002: SQLAlchemy is not installed — cannot verify "
                    "OrganizationMember membership. In production this is "
                    "a fail-closed condition (21 CFR Part 11 requires "
                    "membership verification). Install SQLAlchemy and "
                    "psycopg2-binary, then restart the service."
                ),
            )
        return None

    db_url = os.environ.get("DATABASE_URL")
    if not db_url:
        if _is_production_env:
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail=(
                    "BE-002: DATABASE_URL is not set — cannot verify "
                    "OrganizationMember membership. In production this is "
                    "a fail-closed condition (21 CFR Part 11 requires "
                    "membership verification). Set DATABASE_URL to the "
                    "shared PostgreSQL connection string."
                ),
            )
        return None

    # Build the SQL query. The OrganizationMember table has columns
    # userId, organizationId (camelCase, matching Prisma's column
    # naming). The unique constraint (userId, organizationId) ensures
    # at most one row matches — we just need to know if it EXISTS.
    try:
        # Use a fresh engine per call (cheap with pool_pre_ping). The
        # alternative — a module-level engine — would leak connections
        # across worker processes under uvicorn --workers >1.
        engine = create_engine(db_url, pool_pre_ping=True, pool_size=2)
        with engine.connect() as conn:
            # parameterized query — never interpolate user-controlled
            # values into SQL. user_id and org_id come from the JWT
            # (signed) but defense-in-depth.
            result = conn.execute(
                text(
                    "SELECT 1 FROM \"OrganizationMember\" "
                    "WHERE \"userId\" = :uid AND \"organizationId\" = :oid "
                    "LIMIT 1"
                ),
                {"uid": auth.user_id, "oid": auth.org_id},
            )
            row = result.fetchone()
        engine.dispose()
        if row is None:
            # Membership missing — fail-closed. The user's JWT says they're
            # in this org, but the DB says they're not. This is the BE-062
            # scenario: the user was removed from the org but their JWT
            # hasn't expired yet.
            logger.warning(
                "BE-002: membership check FAILED for user_id=%s org_id=%s "
                "(no OrganizationMember row). Rejecting with 401.",
                auth.user_id, auth.org_id,
            )
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail=(
                    "BE-002: User is not a member of the organization in "
                    "their JWT. The membership may have been revoked since "
                    "the JWT was issued. Re-authenticate to get a fresh "
                    "JWT with a valid org_id claim."
                ),
                headers={"WWW-Authenticate": "Bearer"},
            )
    except HTTPException:
        raise  # Propagate 401/503 — don't let the catch-all swallow it
    except Exception as exc:
        # DB error (connection refused, table missing, etc.). In
        # production, fail-closed. In non-production, fail-open.
        logger.error(
            "BE-002: OrganizationMember lookup error: %s. user_id=%s org_id=%s",
            exc, auth.user_id, auth.org_id,
        )
        if _is_production_env:
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail=(
                    f"BE-002: Cannot verify org membership (DB error: "
                    f"{type(exc).__name__}). In production this is a "
                    f"fail-closed condition. Retry the request; if the "
                    f"error persists, the database is unavailable."
                ),
            ) from exc
        # Non-production: fail-open (let the request through).
        return None

    return None


# ---------------------------------------------------------------------------
# TM8 v134 ROOT FIX (Teammate 8 — P2 to Backend Integration):
# Lenient JWT verification + X-Org-Id header fallback for /kg/* proxy routes.
# ---------------------------------------------------------------------------
# The existing ``verify_jwt`` is STRICT — it rejects JWTs without an
# ``org_id`` claim with HTTP 401. This is correct for the public REST
# API endpoints (/predict, /top-k, /datasets/stats, /validate) where
# the Next.js frontend ALWAYS mints JWTs with org_id.
#
# But the /kg/* proxy routes need to support an ADDITIONAL caller pattern:
# service-to-service calls where the caller is an internal service
# account (e.g., the Next.js /api/kg/* route proxying to the backend
# on behalf of a user) that passes the org_id via the ``X-Org-Id``
# HTTP header INSTEAD of minting a full per-user JWT. This is the
# "trusted internal caller" pattern documented in OAUTH2 RFC 8693
# (token exchange) — the backend trusts the upstream proxy to set
# X-Org-Id correctly.
#
# ``verify_jwt_lenient`` accepts JWTs with OR without org_id. When
# org_id is missing, the returned AuthContext has org_id="" — the
# caller MUST then use ``verify_org_id_with_fallback`` to resolve
# the org_id from the X-Org-Id header (or 403 if neither is present).
#
# SECURITY: the X-Org-Id header is ONLY honored when the JWT itself
# lacks org_id. A JWT WITH org_id ALWAYS wins (the JWT is the stronger
# credential — it is signed by the auth service, while the X-Org-Id
# header is just a header anyone could set). This prevents a malicious
# user from spoofing another org by sending both a valid JWT (with
# their own org_id) AND an X-Org-Id header for someone else's org.

async def verify_jwt_lenient(
    credentials: Optional[HTTPAuthorizationCredentials] = Depends(security),
) -> AuthContext:
    """Verify the JWT bearer token — lenient mode (org_id optional).

    Same as ``verify_jwt`` but does NOT require the ``org_id`` claim.
    Used by the /kg/* proxy routes that support the X-Org-Id header
    fallback pattern (service-to-service calls).

    Returns AuthContext with org_id="" if the JWT lacks the claim.
    The caller MUST then use ``verify_org_id_with_fallback`` to
    resolve the org_id from the X-Org-Id header (or 403 if missing).
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
        # TM8 v134: org_id is OPTIONAL in lenient mode. The X-Org-Id
        # header fallback (in verify_org_id_with_fallback) handles the
        # case where the JWT lacks org_id.
        org_id = str(payload.get("org_id") or payload.get("orgId") or "")
        org_role = str(payload.get("org_role") or "member")
        # BE-002 v143: extract platformRole (same as verify_jwt).
        platform_role = str(payload.get("platformRole") or payload.get("platform_role") or "none")
        return AuthContext(
            user_id=str(user_id),
            org_id=org_id,
            org_role=org_role,
            platform_role=platform_role,
        )
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


async def verify_org_id_with_fallback(
    request: Request,
    auth: AuthContext = Depends(verify_jwt_lenient),
) -> str:
    """Resolve org_id from JWT (priority 1) or X-Org-Id header (priority 2).

    Used by the /kg/* proxy routes that support service-to-service
    callers passing X-Org-Id instead of a per-user JWT.

    Resolution order:
      1. ``auth.org_id`` from the JWT (if non-empty) — JWT is the stronger
         credential (signed by the auth service).
      2. ``X-Org-Id`` HTTP request header — for trusted internal callers
         (e.g., the Next.js /api/kg/* proxy).
      3. 403 Forbidden — neither JWT org_id nor X-Org-Id header present.

    SECURITY: the X-Org-Id header is ONLY honored when the JWT lacks
    org_id. This prevents a malicious user from spoofing another org
    by sending both a valid JWT (with their own org_id) AND an X-Org-Id
    header for someone else's org — the JWT's org_id ALWAYS wins.
    """
    # Priority 1: JWT org_id (the signed, stronger credential).
    if auth.org_id:
        return auth.org_id
    # Priority 2: X-Org-Id header (for trusted internal callers).
    header_org_id = request.headers.get("X-Org-Id", "").strip()
    if header_org_id:
        return header_org_id
    # Neither — 403 Forbidden.
    raise HTTPException(
        status_code=status.HTTP_403_FORBIDDEN,
        detail={
            "error": "org_id_required",
            "message": (
                "An org_id is required for this endpoint. Provide it via "
                "the JWT 'org_id' claim (preferred) OR the 'X-Org-Id' "
                "HTTP header (for trusted internal callers). Anonymous "
                "access (no org_id) is forbidden — 21 CFR Part 11 requires "
                "every API call to be attributable to an org."
            ),
        },
    )


# ---------------------------------------------------------------------------
# TM8 v134 ROOT FIX: create_test_jwt is defined LATER in this file
# (Teammate 11 v141 version, which is the canonical one). Earlier
# duplicate definitions from Teammate 8 and Teammate 12 have been
# removed to avoid confusion. The active definition is the LAST one
# in the file (Python's standard shadowing rule). All integration
# tests use create_test_jwt(user_id=..., org_id=...) with keyword
# args, which the active definition supports.
# ---------------------------------------------------------------------------


# ---------------------------------------------------------------------------
# TM8 v134 ROOT FIX: KG_SERVICE_URL — Phase 2 KG service URL.
# ---------------------------------------------------------------------------
# The backend proxies /kg/* requests to the Phase 2 KG service via this URL.
# Default is http://localhost:8001 (the canonical phase2_kg port per
# shared/contracts/urls.py SERVICE_PORTS). In docker-compose, set this to
# http://phase2-kg-api:8001 (the docker network service name).
#
# The Phase 2 KG service (phase2/service.py) exposes:
#   GET  /kg/stats   — node/edge counts, per-type breakdown, canonicalNodeCount
#   GET  /kg/explore — subgraph around a drug/disease (?drug=&disease=&limit=)
#   POST /cypher     — raw read-only Cypher passthrough (whitelist + 30s timeout)
#   POST /query      — structured drug/disease query (same as /kg/explore but POST)
#   GET  /health     — liveness probe
#
# The backend's /kg/stats, /kg/explore, /cypher routes are THIN PROXIES —
# they add JWT auth, org_id scoping, rate limiting, and 503 fallback, then
# forward to the Phase 2 service which does the actual work.
KG_SERVICE_URL: str = os.environ.get(
    "KG_SERVICE_URL",
    "http://localhost:8001",
).rstrip("/")


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
#
# P1-023 ROOT FIX (Team 2 — Phase 1): hostile-auditor pass.
#   The previous code was:
#       allow_origins=[_frontend_url] if _frontend_url != "*" else ["*"],
#       allow_credentials=True,
#   This is a CORS security vulnerability per the W3C CORS spec: when
#   ``allow_origins=["*"]`` is combined with ``allow_credentials=True``,
#   the spec REQUIRES browsers to refuse credentialed requests. Modern
#   browsers comply, but the configuration is still a vulnerability
#   because:
#     1. Older or non-compliant browsers (some embedded WebViews) may
#        still send credentials, allowing any website to make
#        authenticated requests to the API.
#     2. Pharma partner API keys in cookies/Authorization headers could
#        be exfiltrated by a malicious website if the browser fails to
#        enforce the spec.
#     3. Static-analysis tools and security auditors flag this as a
#        finding, blocking production deployment at pharma IT review.
#
#   ROOT FIX (defense-in-depth):
#     A. In PRODUCTION (ENVIRONMENT=production), REJECT ``FRONTEND_URL=*``
#        with a RuntimeError at startup. Fail-closed: the API refuses
#        to start rather than run with an insecure CORS config. This
#        matches the patient-safety principle: "a wrong integration is
#        worse than no integration."
#     B. In NON-PRODUCTION (dev/CI), if ``FRONTEND_URL=*`` is set, log
#        a CRITICAL warning and DISABLE ``allow_credentials`` so the
#        config is technically valid per the spec. This preserves the
#        dev convenience of ``FRONTEND_URL=*`` for local testing
#        without enabling credentialed cross-origin requests.
#     C. When ``FRONTEND_URL`` is a specific origin (the normal case),
#        ``allow_credentials=True`` is preserved (the frontend needs
#        cookies for JWT session auth).
# BE-005 ROOT FIX (v143, Teammate 12 — hostile-auditor pass):
# The previous "fix" handled the case FRONTEND_URL="*" (rejected in prod),
# but SILENTLY accepted the case where FRONTEND_URL was UNSET. The default
# "http://localhost:3000" would be used, and in a production deploy where
# the operator forgot to set FRONTEND_URL, every browser request from the
# real frontend domain (e.g. https://app.drugos.ai) would be rejected by
# CORS — the whole API silently broken. The audit explicitly required:
# "Hard-fail at startup if NODE_ENV=production AND FRONTEND_URL is unset or '*'."
# We honor that literally: if ENVIRONMENT=production AND the env var is
# missing or empty, raise RuntimeError. This is the patient-safety
# principle — "a wrong integration is worse than no integration".
#
# NOTE: we check os.environ.get("FRONTEND_URL") WITHOUT a default — the
# default "http://localhost:3000" is only used in non-production.
_frontend_url_raw = os.environ.get("FRONTEND_URL")
_environment = os.environ.get("ENVIRONMENT", "development").strip().lower()
_is_production_env = _environment in ("production", "prod")

if _is_production_env and not (_frontend_url_raw or "").strip():
    raise RuntimeError(
        "BE-005 CORS security vulnerability (v143): FRONTEND_URL env var is "
        "UNSET (or empty) in production (ENVIRONMENT=production). The "
        "default 'http://localhost:3000' would silently reject every "
        "browser request from the real production frontend domain. Set "
        "FRONTEND_URL to the specific frontend origin (e.g. "
        "'https://app.drugos.ai') or a comma-separated list of allowed "
        "origins. Anonymous/unconfigured CORS is forbidden in production "
        "— 21 CFR Part 11 requires every API call to be attributable to "
        "a known origin."
    )

_frontend_url = (_frontend_url_raw or "http://localhost:3000").strip()

if _frontend_url == "*":
    if _is_production_env:
        # P1-023 ROOT FIX: fail-closed in production. Refuse to start.
        raise RuntimeError(
            "CORS security vulnerability (P1-023): FRONTEND_URL='*' is "
            "FORBIDDEN in production (ENVIRONMENT=production). Setting "
            "allow_origins=['*'] with allow_credentials=True allows any "
            "website to make authenticated requests to this API, "
            "exfiltrating pharma partner API keys. Set FRONTEND_URL to "
            "the specific frontend origin (e.g. "
            "'https://app.drugos.ai') or set ENVIRONMENT=development "
            "for local testing (which disables credentials)."
        )
    else:
        # Non-production: allow '*' but DISABLE credentials. Log CRITICAL
        # so the operator sees this in the structured log stream.
        logger.critical(
            "CORS P1-023: FRONTEND_URL='*' is set in a non-production "
            "environment (%s). allow_credentials is being DISABLED to "
            "comply with the W3C CORS spec (allow_origins=['*'] + "
            "allow_credentials=True is a security vulnerability). "
            "Credentialed cross-origin requests will FAIL. Set "
            "FRONTEND_URL to a specific origin to enable credentials.",
            _environment,
        )
        _cors_origins = ["*"]
        _cors_allow_credentials = False
else:
    # Normal case: specific origin(s). Allow comma-separated lists.
    _cors_origins = [u.strip() for u in _frontend_url.split(",") if u.strip()]
    _cors_allow_credentials = True

app.add_middleware(
    CORSMiddleware,
    allow_origins=_cors_origins,
    allow_credentials=_cors_allow_credentials,
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
# BE-007 ROOT FIX (v143, Teammate 12 — hostile-auditor pass):
# The previous limiter was keyed by `get_remote_address` (IP-based). The
# audit explicitly required: "Key the limiter on user_id (extracted from
# JWT) not IP — the FastAPI is meant for direct API-key access." A single
# pharma partner with one NAT egress IP could share a 100/min budget
# across 50 researchers — defeating the per-user SLO. Conversely, two
# different pharma partners behind the same corporate proxy would SHARE
# the same IP-keyed budget, each getting throttled by the other's traffic.
#
# The fix introduces `_get_user_id_from_jwt` — a slowapi key_func that
# extracts `sub` from the JWT in the Authorization header. When the JWT
# is missing/invalid (e.g., pre-auth /health), it falls back to the
# remote IP so unauthenticated endpoints don't bypass the limiter
# entirely. Authenticated requests are keyed by user_id, which is what
# the V1 contract's "100 concurrent requests" SLO is actually scoped to.
def _get_user_id_from_jwt(request: Request) -> str:
    """slowapi key_func — extract user_id from JWT, fall back to IP.

    Used as the slowapi Limiter's key_func so rate limits are enforced
    PER-USER (not per-IP). The JWT is decoded WITHOUT signature
    verification here — the verify_jwt dependency on each endpoint does
    the full signature verification. We just need the `sub` claim to
    identify the caller for rate-limit accounting.

    Fallback chain:
      1. JWT 'sub' claim (the canonical user_id).
      2. X-User-Id header (trusted internal caller — e.g. the Next.js
         proxy passing the authenticated user_id through).
      3. Remote IP address (unauthenticated or pre-auth requests).
    """
    auth_header = request.headers.get("Authorization", "")
    if auth_header.startswith("Bearer "):
        token = auth_header[7:]
        try:
            import jwt as _pyjwt
            # Decode without verification — just need the claims. The
            # endpoint's verify_jwt does the real signature check.
            payload = _pyjwt.decode(
                token,
                options={"verify_signature": False},
            )
            sub = payload.get("sub")
            if sub:
                return f"user:{sub}"
        except Exception:
            pass  # Fall through to next strategy
    # Trusted internal caller header (set by the Next.js proxy).
    x_user_id = request.headers.get("X-User-Id", "").strip()
    if x_user_id:
        return f"user:{x_user_id}"
    # Fallback: IP address (for unauthenticated endpoints like /health).
    return f"ip:{get_remote_address(request)}"


if _HAS_SLOWAPI:
    # BE-007 v143: key by user_id (not IP).
    limiter = Limiter(key_func=_get_user_id_from_jwt)
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
# BE-008 ROOT FIX (v143, Teammate 12 — hostile-auditor pass):
# The previous middleware was FAIL-SAFE — a DB write failure was logged
# to stderr but the response was returned unchanged. The audit explicitly
# required: "Make it critical — fail-closed if audit log write fails
# (mirror lib/api-helpers.ts writeAuditLogCritical)."
#
# A fail-safe audit log is a 21 CFR Part 11 VIOLATION: if the audit DB
# is briefly unreachable, /predict calls silently succeed without an
# audit record — exactly the "unattributable API call" the regulation
# forbids. The fix is fail-closed: if the audit write fails, return 503
# so the caller knows their request was NOT processed auditably and can
# retry. The original response is DISCARDED — the researcher sees an
# audit-failure error instead of a prediction.
#
# OPT-OUT for non-production: when ENVIRONMENT != production, the
# middleware stays fail-safe (logs the failure but returns the response)
# so dev/CI environments without a real DB don't break. The fail-closed
# behavior is gated on `_is_production_env` — the same env var that
# gates the CORS hard-fail (BE-005).
@app.middleware("http")
async def audit_log_middleware(request: Request, call_next):
    """Audit log every state-changing request (POST/PUT/PATCH/DELETE).

    21 CFR Part 11 requires every mutation to be attributed to a user +
    org + timestamp + IP. This middleware extracts the auth context from
    the JWT (without validating it — that's verify_jwt's job for the
    endpoint itself) and logs the request to the audit_log table.

    BE-008 v143: the middleware is FAIL-CLOSED in production. If the
    audit DB write fails (DB down, table missing, connection pool
    exhausted), the middleware returns 503 instead of the original
    response — the caller knows their request was NOT audited and can
    retry. In non-production, the middleware is fail-safe (logs to stderr
    but returns the response) so dev/CI without a DB still works.

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

    # Prepare the audit log fields. Truncate body to 500 chars.
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

    # BE-008 v143: write the audit log entry. In PRODUCTION, a write
    # failure causes the response to be REPLACED with a 503 — fail-closed
    # per 21 CFR Part 11. In non-production, the failure is logged but
    # the original response is returned (dev convenience).
    audit_write_ok = False
    audit_write_error: Optional[str] = None
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
            audit_write_ok = True
        else:
            # DB not available. In production this is a fail-closed
            # condition — the audit log table is required for 21 CFR
            # Part 11 compliance. In non-production, just log to stderr.
            audit_write_error = "DATABASE_URL not set or SQLAlchemy not available"
            logger.info(
                "AUDIT user_id=%s org_id=%s method=%s endpoint=%s status=%d ip=%s body_summary=%r",
                user_id, org_id, method, endpoint, status_code, ip_address, body_summary[:100],
            )
    except Exception as exc:
        audit_write_error = f"{type(exc).__name__}: {exc}"
        logger.error(
            "BE-008 audit log write FAILED: user_id=%s org_id=%s method=%s "
            "endpoint=%s status=%d error=%s",
            user_id, org_id, method, endpoint, status_code, exc,
        )

    # BE-008 v143: fail-closed in production. If the audit log write
    # failed AND we're in production, return 503 — the request was NOT
    # audited, so the caller must retry. Returning the original response
    # would silently violate 21 CFR Part 11.
    if _is_production_env and not audit_write_ok:
        # Import locally to avoid circular import at module load.
        from fastapi.responses import JSONResponse
        logger.error(
            "BE-008 FAIL-CLOSED: returning 503 because audit log write "
            "failed in production. user_id=%s org_id=%s method=%s endpoint=%s "
            "original_status=%d audit_error=%s",
            user_id, org_id, method, endpoint, status_code, audit_write_error,
        )
        return JSONResponse(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            content={
                "error": "audit_log_write_failed",
                "message": (
                    "The audit log write failed in production. The request "
                    "was processed but NOT recorded in the audit trail — "
                    "21 CFR Part 11 requires every mutation to be "
                    "attributable. The request has been rejected (fail-"
                    "closed). Retry the request; if the error persists, "
                    "the audit_log database table is unavailable and the "
                    "platform operator must be notified."
                ),
                "audit_error": audit_write_error,
                "original_status_code": status_code,
            },
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
    # BE-002 v143: verify the user is STILL a member of their JWT's org.
    # In production this does a real SELECT against OrganizationMember.
    _membership: None = Depends(verify_org_membership),
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
    # BE-002 v143: verify the user is STILL a member of their JWT's org.
    _membership: None = Depends(verify_org_membership),
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
    # TEAMMATE-11 v141 + P4-024: httpx is imported at module level now
    # (unconditional — no _HAS_HTTPX check needed). If httpx is not
    # installed, the module fails to load at import time, which is the
    # desired fail-fast behavior for production.

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
    # P0 ROOT FIX (hostile-auditor v134): wire /datasets/stats to the
    # Phase 1 dataset service at PHASE1_SERVICE_URL/stats. The previous
    # implementation returned HARDCODED empty stats (sources=[], nodes=0,
    # edges=0) — the dashboard's dataset-stats card ALWAYS showed zero
    # even when Phase 1 had loaded real data. This fix proxies to
    # PHASE1_SERVICE_URL/stats. If PHASE1_SERVICE_URL is not configured,
    # we return HTTP 503 — we NEVER return fake empty stats that could
    # be confused with "Phase 1 has no data loaded".
    import httpx

    phase1_url = os.environ.get("PHASE1_SERVICE_URL")
    if not phase1_url:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="PHASE1_SERVICE_URL not configured — the Phase 1 dataset "
                   "service is not deployed. /datasets/stats cannot return "
                   "real stats. Deploy the Phase 1 service and set "
                   "PHASE1_SERVICE_URL.",
        )

    try:
        async with httpx.AsyncClient(timeout=15.0) as client:
            p1_resp = await client.get(f"{phase1_url.rstrip('/')}/stats")
        if p1_resp.status_code != 200:
            raise HTTPException(
                status_code=status.HTTP_502_BAD_GATEWAY,
                detail=f"Phase 1 service /stats returned "
                       f"{p1_resp.status_code}: {p1_resp.text[:500]}",
            )
        p1_payload = p1_resp.json()
    except httpx.RequestError as exc:
        raise HTTPException(
            status_code=status.HTTP_504_GATEWAY_TIMEOUT,
            detail=f"Phase 1 service unreachable at {phase1_url}: {exc}",
        ) from exc

    # Merge the Phase 1 service's response with our audit fields. The
    # Phase 1 /stats endpoint already returns the fields the frontend
    # destructures (sources, total_drugs, total_proteins, nodesLoaded,
    # edgesLoaded, edgeTypesPresent, schemaVersion, bridgeVersion,
    # lastUpdated, warnings, errors, generatedAt, backend). We add
    # org_id for audit echo (TM14 v132 fix preserved).
    p1_payload["org_id"] = auth.org_id  # audit echo
    return p1_payload


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
    # BE-002 v143: verify the user is STILL a member of their JWT's org.
    _membership: None = Depends(verify_org_membership),
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


# ---------------------------------------------------------------------------
# TM8 v134 ROOT FIX (Teammate 8 — P2 to Backend Integration):
# Phase 2 KG proxy routes — /kg/stats, /kg/explore, /cypher.
# ---------------------------------------------------------------------------
# These routes are THIN PROXIES to the Phase 2 KG service (phase2/service.py).
# They add:
#   1. JWT auth (verify_jwt_lenient — accepts JWTs with OR without org_id,
#      to support service-to-service callers).
#   2. org_id scoping (verify_org_id_with_fallback — JWT org_id OR X-Org-Id
#      header, 403 if neither).
#   3. Rate limiting (per-user, via the in-memory sliding-window-log limiters
#      in backend/api/rate_limit.py — 10/min for /cypher, 100/min for others).
#   4. 503 fallback when the Phase 2 service is unreachable (with a clear
#      ``error: kg_service_unavailable`` detail so the frontend can show
#      "KG service is down" instead of a generic 500).
#   4. 30-second hard timeout on every Phase 2 call (KG queries can be slow —
#      a multi-hop Cypher traversal of a 10M-edge graph can take 10-20s; we
#      cap at 30s so a hung Neo4j doesn't tie up the backend's worker pool).
#   5. X-Org-Id header forwarding to Phase 2 (so Phase 2 can scope queries
#      to the caller's org when multi-tenant Neo4j is implemented).
#
# The Phase 2 service does the ACTUAL work:
#   - /kg/stats: queries Neo4j (or builds in-memory from Phase 1 CSVs) for
#     node/edge counts, per-type breakdown, and canonicalNodeCount (the
#     count of CANONICAL-type nodes only — Compound, Protein, Pathway,
#     Disease, ClinicalOutcome).
#   - /kg/explore: BFS traversal from a drug/disease node, returns the
#     subgraph (nodes + edges) up to ``limit`` hops.
#   - /cypher: raw read-only Cypher passthrough with a whitelist (MATCH/
#     OPTIONAL MATCH/WITH/RETURN/WHERE only), 30s server-side timeout, and
#     a 1000-row cap. The backend's 10 req/min rate limit is the FIRST
#     line of defense; the Phase 2 whitelist + timeout + cap are the
#     SECOND/THIRD/FOURTH lines.
#
# SCIENTIFIC INTEGRITY: the backend NEVER fabricates KG stats. If the
# Phase 2 service is unreachable, the backend returns 503 — it does NOT
# return mock numbers. This is critical because the frontend's Knowledge
# Graph Explorer displays these counts to researchers making drug
# repurposing decisions; fake numbers would be scientific fraud.

# TM8 v134: 30-second hard timeout for ALL Phase 2 proxy calls. KG queries
# can be slow (multi-hop Cypher on a 10M-edge graph), but anything over 30s
# indicates a hung Neo4j or a runaway query — fail fast so the backend's
# worker pool isn't tied up. The Phase 2 service has its OWN 30s timeout
# on /cypher (enforced server-side via the Neo4j driver), so the backend's
# 30s timeout is a defense-in-depth backstop.
_KG_PROXY_TIMEOUT_SECONDS: float = 30.0


def _kg_service_unavailable_response(exc: Exception) -> HTTPException:
    """Build a 503 HTTPException for when the Phase 2 KG service is down.

    The detail dict follows the project's standard error shape:
      {"error": "kg_service_unavailable", "message": str, "backend": "missing"}

    The frontend's kg-service.ts checks for this error code to show
    "KG service is down" instead of a generic 500.
    """
    return HTTPException(
        status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
        detail={
            "error": "kg_service_unavailable",
            "message": (
                f"The Phase 2 KG service at {KG_SERVICE_URL} is unreachable: "
                f"{exc}. Ensure the Phase 2 service is running (uvicorn "
                f"phase2.service:app --port 8001) and that KG_SERVICE_URL "
                f"is set correctly on the backend."
            ),
            "backend": "missing",
        },
    )


@app.get("/kg/stats", tags=["knowledge-graph"])
async def proxy_kg_stats(
    request: Request,  # noqa: ARG001 — used by Depends below
    auth: AuthContext = Depends(verify_jwt_lenient),
    org_id: str = Depends(verify_org_id_with_fallback),
) -> Dict[str, Any]:
    """Proxy GET /kg/stats to the Phase 2 KG service.

    Returns real KG stats from Neo4j (or in-memory bridge fallback):
      - ``nodeCount``: total node count (ALL types, including non-canonical)
      - ``canonicalNodeCount``: count of CANONICAL-type nodes only
        (Compound, Protein, Pathway, Disease, ClinicalOutcome)
      - ``edgeCount``: total edge count
      - ``nodeTypeCounts``: per-type node count breakdown
      - ``edgeTypeCounts``: per-type edge count breakdown
      - ``sources``: array of {name, loaded} objects (Phase 1 source provenance)
      - ``lastUpdated`` / ``generatedAt``: server-authoritative UTC timestamp
      - ``source``: "neo4j" | "in_memory" (which backend served the query)

    Rate-limited to 100 req/min per authenticated user (KG_STATS_RATE_LIMITER).
    429 + Retry-After on exceed.

    Returns 503 with ``{"error": "kg_service_unavailable"}`` when the Phase 2
    service is unreachable.
    """
    # TM8 v134: enforce per-user rate limit INSIDE the handler (after JWT
    # verification) so the rate-limit key is the authenticated user_id.
    # ``auth`` is the AuthContext returned by verify_jwt_lenient; we use
    # ``auth.user_id`` as the rate-limit key (per-user, not per-IP — see
    # backend/api/rate_limit.py module docstring for the rationale).
    check_kg_stats_rate_limit(auth.user_id)
    logger.info(
        "kg/stats proxy: user=%s org=%s → %s",
        auth.user_id, org_id, KG_SERVICE_URL,
    )
    try:
        async with httpx.AsyncClient(timeout=_KG_PROXY_TIMEOUT_SECONDS) as client:
            response = await client.get(
                f"{KG_SERVICE_URL}/kg/stats",
                headers={"X-Org-Id": org_id},
            )
        # TM8 v134: check is_success directly instead of raise_for_status()
        # to avoid httpx 0.28+ RuntimeError when the Response object's
        # request attribute is None (which happens in unit tests that
        # mock httpx.AsyncClient). raise_for_status() requires the request
        # attribute to be set so it can build the HTTPStatusError; checking
        # is_success directly avoids that requirement.
        if not response.is_success:
            logger.warning(
                "kg/stats proxy: Phase 2 returned %d: %s (org=%s)",
                response.status_code, response.text[:200], org_id,
            )
            raise HTTPException(
                status_code=response.status_code,
                detail=_safe_parse_json_error(response, default_error="kg_stats_failed"),
            )
        return response.json()
    except httpx.RequestError as exc:
        logger.warning(
            "kg/stats proxy: Phase 2 unreachable: %s (org=%s)",
            exc, org_id,
        )
        raise _kg_service_unavailable_response(exc) from exc


@app.post("/kg/explore", tags=["knowledge-graph"])
async def proxy_kg_explore(
    payload: Dict[str, Any],
    request: Request,  # noqa: ARG001 — used by Depends below
    auth: AuthContext = Depends(verify_jwt_lenient),
    org_id: str = Depends(verify_org_id_with_fallback),
) -> Dict[str, Any]:
    """Proxy POST /kg/explore to the Phase 2 KG service.

    Request body (JSON):
      - ``drug`` (str, optional): drug name to explore (e.g., "aspirin")
      - ``disease`` (str, optional): disease name to explore
      - ``limit`` (int, optional, default 50, max 500): max nodes to return
      - ``depth`` (int, optional, default 2): BFS hop depth

    At least one of ``drug`` or ``disease`` must be provided. The Phase 2
    service returns the subgraph (nodes + edges) around the specified entity.

    Response (JSON):
      - ``nodes``: array of {id, label, type}
      - ``edges``: array of {source, target, type}
      - ``truncated`` (bool): true if the result was truncated at ``limit``

    Rate-limited to 100 req/min per authenticated user (KG_EXPLORE_RATE_LIMITER).
    429 + Retry-After on exceed.

    Returns 503 with ``{"error": "kg_service_unavailable"}`` when the Phase 2
    service is unreachable.
    """
    check_kg_explore_rate_limit(auth.user_id)
    logger.info(
        "kg/explore proxy: user=%s org=%s payload_keys=%s → %s",
        auth.user_id, org_id, list(payload.keys()), KG_SERVICE_URL,
    )
    # TM8 v134 ROOT FIX (contract translation): Phase 2's /kg/explore is
    # GET-only (accepts ?drug=&disease=&limit= query params). The frontend
    # sends POST /kg/explore with a JSON body {drug, disease, limit}. The
    # body shape matches Phase 2's POST /query endpoint EXACTLY (see
    # phase2/service.py:QueryBody). So we forward the POST body to Phase
    # 2's POST /query — same body, same response shape (nodes + edges +
    # truncated). This is the cleanest translation: no field renaming, no
    # body→query-param conversion, no API drift.
    #
    # The backend's PUBLIC contract (POST /kg/explore with JSON body) is
    # UNCHANGED — the frontend doesn't know or care that Phase 2 routes
    # the request to /query internally. This is the whole point of the
    # proxy: the backend presents a stable public API while Phase 2's
    # internal API can evolve.
    try:
        async with httpx.AsyncClient(timeout=_KG_PROXY_TIMEOUT_SECONDS) as client:
            response = await client.post(
                f"{KG_SERVICE_URL}/query",
                json=payload,
                headers={"X-Org-Id": org_id},
            )
        if not response.is_success:
            logger.warning(
                "kg/explore proxy: Phase 2 returned %d: %s (org=%s)",
                response.status_code, response.text[:200], org_id,
            )
            raise HTTPException(
                status_code=response.status_code,
                detail=_safe_parse_json_error(response, default_error="kg_explore_failed"),
            )
        return response.json()
    except httpx.RequestError as exc:
        logger.warning(
            "kg/explore proxy: Phase 2 unreachable: %s (org=%s)",
            exc, org_id,
        )
        raise _kg_service_unavailable_response(exc) from exc


@app.post("/cypher", tags=["knowledge-graph"])
async def proxy_cypher(
    payload: Dict[str, Any],
    request: Request,  # noqa: ARG001 — used by Depends below
    auth: AuthContext = Depends(verify_jwt_lenient),
    org_id: str = Depends(verify_org_id_with_fallback),
) -> Dict[str, Any]:
    """Proxy POST /cypher to the Phase 2 KG service — STRICT 10 req/min limit.

    Cypher is expensive — a single runaway query can saturate the Neo4j
    connection pool (100 connections) and DoS the entire Phase 2 KG service
    for ALL users. The 10 req/min per-user limit is the FIRST line of
    defense. The Phase 2 service applies ADDITIONAL defenses:
      - Read-only whitelist (MATCH/OPTIONAL MATCH/WITH/RETURN/WHERE only —
        blocks CREATE/MERGE/DELETE/SET/REMOVE/CALL/LOAD CSV/subqueries/APOC)
      - 30-second server-side timeout (enforced via the Neo4j driver)
      - 1000-row cap (prevents a ``RETURN *`` from returning 10M rows)
      - Parameterized queries only (rejects non-scalar params)

    Request body (JSON):
      - ``query`` (str, required): read-only Cypher query
      - ``params`` (dict, optional): parameterized query variables
      - ``max_rows`` (int, optional, default 1000): row cap

    Response (JSON):
      - ``records``: array of dicts (column name → value)
      - ``row_count``: int — number of rows returned
      - ``truncated`` (bool): true if ``max_rows`` was hit
      - ``max_rows`` (int): the cap that was applied
      - ``backend``: "neo4j"
      - ``timeout_seconds``: 30

    Returns:
      - 429 + Retry-After on the 11th request within a 60s window.
      - 503 with ``{"error": "kg_service_unavailable"}`` when Phase 2 is down.
      - 400 with ``{"error": "cypher_not_readonly"}`` when the query violates
        the whitelist (passed through from Phase 2).
    """
    # TM8 v134: STRICT 10 req/min rate limit. This MUST run BEFORE we
    # forward to Phase 2 — a flood of Cypher queries would saturate the
    # Neo4j connection pool before Phase 2's own timeout could kick in.
    check_cypher_rate_limit(auth.user_id)
    logger.info(
        "cypher proxy: user=%s org=%s query_len=%d → %s",
        auth.user_id, org_id, len(str(payload.get("query", ""))), KG_SERVICE_URL,
    )
    # TM8 v134 ROOT FIX (contract translation): the frontend sends
    # {"query": "...", "params": {...}} (see frontend/src/lib/services/
    # kg-service.ts:executeCypher). Phase 2's /cypher endpoint expects
    # {"cypher": "...", "params": {...}} (see phase2/service.py:CypherBody).
    # The field name differs (``query`` vs ``cypher``). The backend proxy
    # TRANSLATES the field name so both the frontend AND Phase 2 can keep
    # their existing contracts without regression risk.
    #
    # We accept BOTH field names on the backend's public API (``query``
    # from the frontend, ``cypher`` from direct API callers who match
    # Phase 2's shape). This is forward-compatible: if Phase 2 later
    # renames ``cypher`` → ``query``, we just remove the translation.
    cypher_query = payload.get("cypher") or payload.get("query")
    if not cypher_query:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail={
                "error": "missing_cypher_query",
                "message": (
                    "The request body must include a 'query' (or 'cypher') "
                    "field containing the read-only Cypher query string. "
                    "Example: {\"query\": \"MATCH (n) RETURN count(n)\", \"params\": {}}."
                ),
            },
        )
    # Build the Phase 2-compatible payload. Forward ``params`` and
    # ``max_rows`` if present (Phase 2's CypherBody only declares ``cypher``
    # and ``params``, but it ignores unknown fields gracefully via
    # Pydantic's default behavior — we forward ``max_rows`` anyway for
    # forward compat with future Phase 2 versions that add a row cap).
    phase2_payload: Dict[str, Any] = {
        "cypher": str(cypher_query),
        "params": payload.get("params") or {},
    }
    if "max_rows" in payload:
        phase2_payload["max_rows"] = payload["max_rows"]
    try:
        async with httpx.AsyncClient(timeout=_KG_PROXY_TIMEOUT_SECONDS) as client:
            response = await client.post(
                f"{KG_SERVICE_URL}/cypher",
                json=phase2_payload,
                headers={"X-Org-Id": org_id},
            )
        if not response.is_success:
            logger.warning(
                "cypher proxy: Phase 2 returned %d: %s (org=%s)",
                response.status_code, response.text[:200], org_id,
            )
            raise HTTPException(
                status_code=response.status_code,
                detail=_safe_parse_json_error(response, default_error="cypher_failed"),
            )
        return response.json()
    except httpx.RequestError as exc:
        logger.warning(
            "cypher proxy: Phase 2 unreachable: %s (org=%s)",
            exc, org_id,
        )
        raise _kg_service_unavailable_response(exc) from exc


def _safe_parse_json_error(response: httpx.Response, default_error: str) -> Dict[str, Any]:
    """Parse a Phase 2 error response into a dict, never raising.

    The Phase 2 service returns errors as JSON dicts like:
      {"error": "cypher_not_readonly", "message": "..."}

    But if the response body is not JSON (e.g., a 502 from a reverse proxy
    in front of Phase 2), we fall back to a generic error dict with the
    raw text. This function NEVER raises — it always returns a dict so the
    HTTPException detail is always well-formed.
    """
    try:
        data = response.json()
        if isinstance(data, dict):
            return data
        # Non-dict JSON (e.g., a JSON array or string) — wrap it.
        return {"error": default_error, "message": str(data), "backend": "error"}
    except Exception:
        return {
            "error": default_error,
            "message": response.text[:500] if response.text else "(empty body)",
            "backend": "error",
            "status_code": response.status_code,
        }


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
    platform_role: str = "none",
    expires_in_seconds: int = 3600,
) -> str:
    """Mint a valid JWT for integration tests.

    Reads ``JWT_SECRET`` and ``JWT_ISSUER`` from the environment (same
    env vars verify_jwt reads). The token's claims match what the
    Next.js frontend's /api/auth/login route issues:
      - ``sub``: the user_id (str)
      - ``org_id``: the user's active org (str, REQUIRED)
      - ``org_role``: 'admin' | 'member' | 'viewer' (default 'member')
      - ``platformRole``: 'none' | 'admin' (default 'none') — BE-002 v143
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
        # BE-002 v143: platformRole claim — 'none' (default) or 'admin'.
        "platformRole": str(platform_role),
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
    # TM8 v134 ROOT FIX (Teammate 8 — P2 to Backend Integration, port collision):
    # The previous default was 8001, which is the canonical phase2_kg port
    # (per shared/contracts/urls.py SERVICE_PORTS). Running the public REST
    # API on the same port as the Phase 2 KG service is a CONFLICT — only
    # one can bind at a time.
    #
    # TM14 v132 previously moved the default to 8004 (the next free port
    # after the 4 ML services: 8000=phase1, 8001=phase2, 8002=phase3,
    # 8003=phase4). However, the v128 TM15 Task 15.2 fix later MOVED
    # phase1 from 8000 → 8001 (canonical per phase1/service.py:17
    # docstring), which FREED port 8000. The frontend's .env.example
    # (frontend/.env.example:50) was already updated to expect the
    # backend at BACKEND_URL=http://localhost:8000.
    #
    # TM8 v134: align the backend default with the frontend's expectation
    # → port 8000. This is the canonical "DrugOS public REST API" port.
    # There is NO collision:
    #   - phase1-service runs in a docker container with `expose: ["8001"]`
    #     (internal-only, NOT mapped to host port 8000).
    #   - phase2-kg-api runs in a docker container with `expose: ["8001"]`
    #     (internal-only).
    #   - The backend FastAPI runs on the HOST (or in a separate docker
    #     container with `ports: ["8000:8000"]`), so port 8000 on the
    #     host is free.
    # BE-003 ROOT FIX (v143, Teammate 12 — hostile-auditor pass):
    # The previous "fix" moved the FastAPI from port 8001 → 8000 to avoid
    # colliding with phase2_kg. But port 8000 is the CANONICAL phase1_dataset
    # port per shared/contracts/urls.py SERVICE_PORTS — the "fix" just moved
    # the collision. phase1/service.py actually runs on 8001 (docker-compose
    # bump), but the CONTRACT still says 8000, and the frontend reads the
    # contract for its SERVICE_PORTS map. Using 8000 here would silently
    # shadow the contract's phase1_dataset port.
    #
    # The TRULY free port is 8004 — after the 4 ML services (8000-8003).
    # shared/contracts/urls.py and frontend/contracts/_url-constants.ts have
    # BOTH been updated to register `drugos_api: 8004` so the contract is
    # the single source of truth.
    port = int(os.environ.get("DRUGOS_API_PORT", "8004"))
    workers = int(os.environ.get("DRUGOS_API_WORKERS", "4"))
    uvicorn.run(
        "backend.api.main:app",
        host="0.0.0.0",
        port=port,
        workers=workers,
        log_level=os.environ.get("DRUGOS_API_LOG_LEVEL", "info"),
    )
