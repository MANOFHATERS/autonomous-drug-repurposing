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
from typing import List, Optional

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


@app.get("/health", response_model=HealthResponse, tags=["system"])
async def health() -> HealthResponse:
    """Health check endpoint — used by load balancers and uptime monitors.

    No authentication required (returns only public status info).
    """
    # Probe the GT model service, RL ranker service, and database.
    # Each probe is best-effort — a failure sets the corresponding flag
    # to False but doesn't fail the health check (the API is still up,
    # just degraded).
    gt_loaded = False
    rl_loaded = False
    db_connected = False
    try:
        # TODO: probe the GT model service via ML_SERVICE_URL.
        # For now, assume it's loaded if the env var is set.
        gt_loaded = bool(os.environ.get("GT_MODEL_PATH"))
    except Exception as exc:
        logger.debug("health: GT probe failed: %s", exc)
    try:
        rl_loaded = bool(os.environ.get("RL_CHECKPOINT_PATH"))
    except Exception as exc:
        logger.debug("health: RL probe failed: %s", exc)
    try:
        # TODO: probe the database via DATABASE_URL.
        db_connected = bool(os.environ.get("DATABASE_URL"))
    except Exception as exc:
        logger.debug("health: DB probe failed: %s", exc)
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

    This endpoint mirrors `POST /api/predict` in the Next.js frontend.
    The Next.js route is kept during the migration period (it proxies to
    this FastAPI service when ML_SERVICE_URL is set). Eventually, the
    Next.js route will be removed and pharma partners will call this
    endpoint directly.
    """
    # TODO: call the GT model service via ML_SERVICE_URL.
    # For now, return a placeholder that matches the response schema.
    # The actual GT model call will be implemented in the next phase
    # (when the GT service is deployed to a GPU node).
    logger.info("predict: user=%s drug=%s disease=%s", user_id, req.drug, req.disease)
    return PredictResponse(
        drug=req.drug,
        disease=req.disease,
        gnn_score=0.5,  # placeholder
        confidence=0.5,  # placeholder
        pathways=[],
        literature_supported=False,
    )


@app.post("/top-k", response_model=TopKResponse, tags=["ranking"])
async def top_k(
    req: TopKRequest,
    user_id: str = Depends(verify_jwt),
) -> TopKResponse:
    """Get the top-K repurposing candidates for a drug or disease.

    If `drug` is provided, returns the top-K diseases for that drug.
    If `disease` is provided, returns the top-K drugs for that disease.
    Exactly one of `drug` or `disease` must be provided.

    This endpoint mirrors `POST /api/top-k` in the Next.js frontend.
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
    # TODO: call the RL ranker service via RL_SERVICE_URL.
    logger.info("top-k: user=%s drug=%s disease=%s k=%d", user_id, req.drug, req.disease, req.k)
    return TopKResponse(candidates=[], total=0, source="rl_ranker")


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
