# BE-001 v123 — Integration Note: FastAPI Public REST API

## Status: PARTIAL MIGRATION (Phase 1 of 3)

This document explains the architectural decision behind the partial
migration of the public REST API from Next.js App Router to FastAPI,
per the DOCX spec (Team_Cosmic_Build_Process_Updated.docx, Section 9
"Technology Stack") which mandates:

> **API Layer: FastAPI (Python)** — High-performance async REST API;
> easy to document with OpenAPI.

## The Divergence

The original team built the entire public REST API in Next.js App
Router (TypeScript) — see `frontend/src/app/api/**/route.ts`. This
was a fundamental architecture divergence from the spec, with five
concrete consequences documented in the BE-001 audit finding:

1. **No Pydantic ↔ Python ML services reuse** — every contract must be
   hand-mirrored in Zod (`lib/ml-contracts.ts`), creating drift risk.
2. **No auto-generated OpenAPI spec** — pharma partners cannot
   programmatically consume the API without hand-written docs.
3. **No independent backend deployment** — Next.js bundles the
   frontend and backend; the backend cannot scale independently.
4. **Duplicated middleware** — CORS, auth, rate-limit had to be
   reimplemented in TypeScript, doubling the security surface.
5. **Different concurrency model** — V1's "100 concurrent requests"
   SLO must be served by Node.js's event loop instead of FastAPI's
   asyncio + uvicorn workers.

## The Root Fix

We're migrating the PUBLIC-FACING endpoints (the ones pharma partners
call) to FastAPI in `backend/api/main.py`. The migration is in 3 phases:

### Phase 1 (this commit, v123) — Skeleton + OpenAPI
- Created `backend/api/main.py` with the FastAPI app and Pydantic
  models for the public endpoints (`/predict`, `/top-k`, `/health`).
- Auto-generated OpenAPI spec at `/openapi.json` and Swagger UI at
  `/docs` — pharma partners can download the spec and generate client
  libraries.
- CORS middleware configured to allow the Next.js frontend origin.
- JWT auth (shared secret with the Next.js frontend — same `JWT_SECRET`
  env var) so tokens issued by either backend are valid on both.
- The endpoints return placeholder responses for now — the actual GT
  model and RL ranker calls will be wired up in Phase 2.

### Phase 2 (next commit) — Wire to ML services
- Implement the actual GT model call in `/predict` (proxy to the GT
  service via `ML_SERVICE_URL`).
- Implement the actual RL ranker call in `/top-k` (proxy to the RL
  service via `RL_SERVICE_URL`).
- Implement `/evidence-package`, `/hypothesis/export`, `/drugs/search`,
  `/diseases/search` endpoints with the same logic as the Next.js
  routes.
- Add a CI test (`tests/test_api_contract_parity.py`) that asserts
  parity between the FastAPI Pydantic models and the Next.js Zod
  schemas — fails the build if they drift.

### Phase 3 (final migration) — Direct mode
- Pharma partners call the FastAPI service directly at
  `https://api.drugos.ai/` (no Next.js proxy).
- The Next.js frontend is only for the researcher dashboard (browser
  UI) — its `/api/*` routes are removed (or kept as a thin proxy for
  backward compat with existing client integrations).
- The FastAPI service is deployed on a separate GPU node for low-
  latency ML inference (no Node.js event-loop bottleneck).

## Running the FastAPI Service

```bash
# From the repo root:
pip install fastapi uvicorn[standard] pyjwt
export JWT_SECRET="<32+ char secret, same as frontend>"
export ML_SERVICE_URL="http://localhost:8000"  # GT model service
export RL_SERVICE_URL="http://localhost:8002"  # RL ranker service
export FRONTEND_URL="http://localhost:3000"    # Next.js frontend (CORS)

python -m backend.api.main
# OR:
uvicorn backend.api.main:app --host 0.0.0.0 --port 8001 --workers 4
```

The OpenAPI spec is then available at:
- `http://localhost:8001/openapi.json` (machine-readable)
- `http://localhost:8001/docs` (Swagger UI)
- `http://localhost:8001/redoc` (ReDoc)

## Decision Rationale

The full migration (ripping out all Next.js `/api/*` routes in one
commit) would break the existing frontend and any pharma partner
integrations that point at the Next.js URLs. The 3-phase approach
lets us:

1. **Phase 1** — Stand up the FastAPI service in parallel with the
   Next.js routes. No existing integration breaks. Pharma partners
   can opt-in to the FastAPI endpoint (with the OpenAPI spec) for new
   integrations.
2. **Phase 2** — Wire up the actual ML service calls. The FastAPI
   service is now functionally equivalent to the Next.js routes. A CI
   test asserts contract parity.
3. **Phase 3** — Cut over pharma partners to the FastAPI service
   directly. The Next.js `/api/*` routes are deprecated and removed
   in a subsequent release.

This is the standard "parallel-run, then cutover" migration pattern
recommended for production systems (see Google SRE Book, "Making
Changes Safely").
