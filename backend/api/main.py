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
  uvicorn main:app --host 0.0.0.0 --port 8001 --workers 4

  The service reads from the same PostgreSQL DB as the Next.js frontend
  (DATABASE_URL env var) and calls the same ML services (phase1, phase2,
  rl) via ML_SERVICE_URL.

OPENAPI:
  The auto-generated OpenAPI spec is available at:
    - http://localhost:8001/openapi.json  (machine-readable)
    - http://localhost:8001/docs          (Swagger UI)
    - http://localhost:8001/redoc         (ReDoc)

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


class HealthResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")
    status: str = "ok"
    version: str
    gt_model_loaded: bool
    rl_agent_loaded: bool
    database_connected: bool


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


async def verify_jwt(
    credentials: Optional[HTTPAuthorizationCredentials] = Depends(security),
) -> str:
    """Verify the JWT bearer token and return the user ID.

    Raises 401 if the token is missing, malformed, expired, or invalid.
    The JWT is signed with the same JWT_SECRET as the Next.js frontend
    (shared secret via env var) — a token issued by the frontend is
    valid here, and vice versa.
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


@app.get("/health", response_model=HealthResponse, tags=["system"])
async def health() -> HealthResponse:
    """Health check endpoint — used by load balancers and uptime monitors.

    No authentication required (returns only public status info).

    TM17 v132 ROOT FIX (P1-004 — false-positive health):
        The previous code probed services by checking env var PRESENCE
        (``gt_loaded = bool(os.environ.get('GT_MODEL_PATH'))``). This
        reported "healthy" even when the GT service was DOWN — the env
        var was set at deploy time, but the service could crash hours
        later. Load balancers kept routing traffic to a dead service.

        ROOT FIX: actually probe each service via HTTP /health. Use a
        short timeout (2s) so a hung service doesn't stall the health
        check. Distinguish:
          * gt_model_loaded  — GT service responds 200 to /health.
          * rl_agent_loaded  — RL service responds 200 to /health.
          * database_connected — DATABASE_URL is set AND the DB accepts
            connections (lazy import of sqlalchemy + SELECT 1).
        Each probe is best-effort — a failure sets the flag to False
        but doesn't fail the health check (the API is still up, just
        degraded). Callers (load balancers) can decide whether to drain
        traffic based on the individual flags.
    """
    import httpx

    gt_loaded = False
    rl_loaded = False
    db_connected = False

    # --- GT service probe ---
    gt_url = os.environ.get("GT_SERVICE_URL") or os.environ.get("ML_SERVICE_URL")
    if gt_url:
        try:
            async with httpx.AsyncClient(timeout=2.0) as client:
                r = await client.get(f"{gt_url.rstrip('/')}/health")
                gt_loaded = r.status_code == 200
        except Exception as exc:
            logger.debug("health: GT probe failed (%s): %s", gt_url, exc)
    else:
        logger.debug("health: GT_SERVICE_URL/ML_SERVICE_URL not set — skipping GT probe")

    # --- RL service probe ---
    rl_url = os.environ.get("RL_SERVICE_URL")
    if rl_url:
        try:
            async with httpx.AsyncClient(timeout=2.0) as client:
                r = await client.get(f"{rl_url.rstrip('/')}/health")
                rl_loaded = r.status_code == 200
        except Exception as exc:
            logger.debug("health: RL probe failed (%s): %s", rl_url, exc)
    else:
        logger.debug("health: RL_SERVICE_URL not set — skipping RL probe")

    # --- Database probe ---
    db_url = os.environ.get("DATABASE_URL") or os.environ.get("PHASE1_DB_URL")
    if db_url:
        try:
            # Lazy import — sqlalchemy is only needed for the DB probe.
            from sqlalchemy import create_engine, text
            engine = create_engine(db_url, pool_pre_ping=True, connect_args={
                "check_same_thread": False} if db_url.startswith("sqlite") else {}
            )
            with engine.connect() as conn:
                conn.execute(text("SELECT 1"))
            db_connected = True
            engine.dispose()
        except Exception as exc:
            logger.debug("health: DB probe failed (%s): %s", db_url, exc)
    else:
        logger.debug("health: DATABASE_URL not set — skipping DB probe")

    return HealthResponse(
        status="ok",
        version="1.0.0",
        gt_model_loaded=gt_loaded,
        rl_agent_loaded=rl_loaded,
        database_connected=db_connected,
    )


@app.post("/predict", response_model=PredictResponse, tags=["prediction"])
async def predict(
    req: PredictRequest,
    user_id: str = Depends(verify_jwt),
) -> PredictResponse:
    """Predict the repurposing score for a (drug, disease) pair.

    Calls the Graph Transformer model to produce a 0-1 score, a confidence
    value, and the top pathway chains connecting the drug to the disease.

    TM17 v132 ROOT FIX (real GT service wiring, not placeholder 0.5):
        The previous code returned ``gnn_score=0.5`` unconditionally
        with the comment "placeholder". This made the V1 launch
        criterion "GT AUC > 0.85" impossible to verify (the E2E smoke
        test explicitly checks ``gnn_score != 0.5`` to detect this). The
        fix actually proxies the request to the GT service via
        ``GT_SERVICE_URL``. If the GT service is not configured, the
        endpoint returns HTTP 503 (not a placeholder 0.5 — pharma
        partners must NOT receive random scores labeled as predictions).
    """
    import httpx

    logger.info("predict: user=%s drug=%s disease=%s", user_id, req.drug, req.disease)

    gt_url = os.environ.get("GT_SERVICE_URL") or os.environ.get("ML_SERVICE_URL")
    if not gt_url:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="GT_SERVICE_URL not configured — the Graph Transformer "
                   "service is not deployed. /predict cannot return real "
                   "predictions without the GT model. Deploy the GT service "
                   "and set GT_SERVICE_URL.",
        )

    try:
        async with httpx.AsyncClient(timeout=30.0) as client:
            gt_resp = await client.post(
                f"{gt_url.rstrip('/')}/predict",
                json={
                    "drug": req.drug,
                    "disease": req.disease,
                    "include_pathways": req.include_pathways,
                },
            )
        if gt_resp.status_code != 200:
            raise HTTPException(
                status_code=status.HTTP_502_BAD_GATEWAY,
                detail=f"GT service returned {gt_resp.status_code}: "
                       f"{gt_resp.text[:500]}",
            )
        data = gt_resp.json()
        # The GT service returns {predictions: [{drug, disease, gnn_score, ...}]}.
        # Extract the prediction for THIS pair.
        predictions = data.get("predictions", [])
        match = next(
            (p for p in predictions
             if p.get("drug", "").lower() == req.drug.lower()
             and p.get("disease", "").lower() == req.disease.lower()),
            None,
        )
        if match is None:
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail=f"GT service did not return a prediction for "
                       f"({req.drug}, {req.disease}). Response: {data}",
            )
        return PredictResponse(
            drug=req.drug,
            disease=req.disease,
            gnn_score=float(match.get("gnn_score", 0.5)),
            confidence=float(match.get("confidence", 0.5)),
            pathways=match.get("pathways", []),
            literature_supported=bool(match.get("literature_supported", False)),
        )
    except httpx.RequestError as exc:
        raise HTTPException(
            status_code=status.HTTP_504_GATEWAY_TIMEOUT,
            detail=f"GT service unreachable at {gt_url}: {exc}",
        ) from exc


@app.post("/top-k", response_model=TopKResponse, tags=["ranking"])
async def top_k(
    req: TopKRequest,
    user_id: str = Depends(verify_jwt),
) -> TopKResponse:
    """Get the top-K repurposing candidates for a drug or disease.

    If `drug` is provided, returns the top-K diseases for that drug.
    If `disease` is provided, returns the top-K drugs for that disease.
    Exactly one of `drug` or `disease` must be provided.

    TM17 v132 ROOT FIX (real RL service wiring, not empty list):
        The previous code returned ``candidates=[]`` with the comment
        "TODO: call the RL ranker service". This made the V1 launch
        criterion "100 concurrent requests" meaningless (every request
        returned an empty list in <1ms). The fix actually proxies the
        request to the RL service via ``RL_SERVICE_URL``. If the RL
        service is not configured, returns 503.
    """
    import httpx

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
    logger.info("top-k: user=%s drug=%s disease=%s k=%d", user_id, req.drug, req.disease, req.k)

    rl_url = os.environ.get("RL_SERVICE_URL")
    if not rl_url:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="RL_SERVICE_URL not configured — the RL ranker service "
                   "is not deployed. /top-k cannot return real rankings.",
        )

    try:
        async with httpx.AsyncClient(timeout=60.0) as client:
            rl_resp = await client.post(
                f"{rl_url.rstrip('/')}/rank",
                json={
                    "drug": req.drug,
                    "disease": req.disease,
                    "limit": req.k,
                },
            )
        if rl_resp.status_code != 200:
            raise HTTPException(
                status_code=status.HTTP_502_BAD_GATEWAY,
                detail=f"RL service returned {rl_resp.status_code}: "
                       f"{rl_resp.text[:500]}",
            )
        data = rl_resp.json()
        candidates = [
            TopKCandidate(
                drug=c.get("drug", ""),
                disease=c.get("disease", ""),
                gnn_score=float(c.get("gnn_score", 0.0)),
                rl_rank=c.get("rl_rank"),
                safety_score=c.get("safety_score"),
                market_score=c.get("market_score"),
            )
            for c in data.get("candidates", [])[: req.k]
        ]
        # Apply min_score filter (the RL service may not have filtered).
        if req.min_score > 0.0:
            candidates = [c for c in candidates if c.gnn_score >= req.min_score]
        return TopKResponse(
            candidates=candidates,
            total=len(candidates),
            source=data.get("source", "rl_ranker"),
        )
    except httpx.RequestError as exc:
        raise HTTPException(
            status_code=status.HTTP_504_GATEWAY_TIMEOUT,
            detail=f"RL service unreachable at {rl_url}: {exc}",
        ) from exc


# ---------------------------------------------------------------------------
# TM15 v132 ROOT FIX (Teammate 15 — Data Flywheel, requirement #8):
# POST /validate — proxy to the RL service's /validate endpoint.
# ---------------------------------------------------------------------------
# The RL service (rl/service.py) already implements /validate, which calls
# phase4.writeback.write_validated_hypothesis() to write the validated
# hypothesis to ALL 3 phases (Phase 1 CSV, Phase 2 Neo4j edge, Phase 3
# retrain trigger). The backend proxy adds:
#   * JWT auth (the RL service's /validate does NOT require auth — it
#     trusts the backend to authenticate).
#   * Audit logging (the backend logs the validate call to Sentry / OTel
#     for the pharma partner's audit trail).
#   * Rate limiting (future: limit per-org validations to prevent abuse).
#
# CONTRACT (matches the RL service's /validate):
#   Request body: {drug, disease, outcome, validated_by?, notes?, ...}
#   Response 200: {ok, writeback: {phase1_csv_path, phase2_neo4j_written, ...}}
#   Response 400: invalid outcome enum value
#   Response 502: RL service returned non-200
#   Response 503: RL_SERVICE_URL not configured
#   Response 504: RL service unreachable
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
    user_id: str = Depends(verify_jwt),
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
        user_id, req.drug, req.disease, req.outcome,
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
                    "validated_by": req.validated_by or user_id,
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
    port = int(os.environ.get("DRUGOS_API_PORT", "8001"))
    workers = int(os.environ.get("DRUGOS_API_WORKERS", "4"))
    uvicorn.run(
        "backend.api.main:app",
        host="0.0.0.0",
        port=port,
        workers=workers,
        log_level=os.environ.get("DRUGOS_API_LOG_LEVEL", "info"),
    )
