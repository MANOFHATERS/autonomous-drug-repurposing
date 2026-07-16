"""shared.contracts.urls — canonical URL paths for all Python services.

TASK 327 ROOT FIX (forensic, root-level):
  Previously, each Python service (Phase 2 service.py, Phase 3 service.py,
  Phase 4 service.py) defined its own URL paths INLINE —
  ``@app.get("/kg/stats")``, ``@app.post("/predict")``, etc. The frontend's
  API proxy routes (Next.js ``src/app/api/*/route.ts``) had to
  reverse-engineer the paths from each service's source code. When a
  service changed a path (e.g. ``/predict`` -> ``/gt/predict``), the
  frontend silently broke until someone noticed a 404 in production.

  This module extracts the canonical URL paths into a CONTRACT that both
  the Python services (writers) and the frontend (reader) import. Any
  change to a path is now a compile-time error on both sides — the
  contract consistency test (Task 330) verifies each service actually
  registers the paths declared here.

Canonical paths
---------------
Service       | Path            | Method | Description
--------------+-----------------+--------+-----------------------------------
Phase 2 (KG)  | /kg/stats       | GET    | Graph stats (node/edge counts)
Phase 2 (KG)  | /kg/explore     | GET    | Explore a node's neighborhood
Phase 3 (GT)  | /predict        | POST   | Predict drug-disease score
Phase 3 (GT)  | /top-k          | GET    | Top-k novel predictions
Phase 4 (RL)  | /rank           | GET    | Ranked candidates (composite score)
Phase 4 (RL)  | /rank/{drug}    | GET    | Ranked candidates filtered by drug
Phase 4 (RL)  | /rank           | POST   | Same as GET /rank with body filters
All services  | /health         | GET    | Health check (liveness probe)
Validation    | /validate       | POST   | Validate a hypothesis (writeback)
"""
from __future__ import annotations

from typing import Dict, Tuple


# =============================================================================
# Canonical URL paths — single source of truth
# =============================================================================
# Each path is the EXACT string the Python service registers via
# ``@app.get(path)`` or ``@app.post(path)``. The frontend MUST import
# these constants instead of hardcoding the path strings.

# Phase 2 (Knowledge Graph service)
URL_KG_STATS: str = "/kg/stats"
URL_KG_EXPLORE: str = "/kg/explore"

# Phase 3 (Graph Transformer service)
URL_PREDICT: str = "/predict"
URL_TOP_K: str = "/top-k"

# Phase 4 (RL Ranker service)
URL_RANK: str = "/rank"
URL_RANK_BY_DRUG: str = "/rank/{drug}"  # path parameter — frontend uses /rank/<drug>

# Hypothesis validation (writable endpoint — initiates writeback)
URL_VALIDATE: str = "/validate"

# Health check (all services)
URL_HEALTH: str = "/health"


# =============================================================================
# All service URLs (for the contract consistency test)
# =============================================================================
ALL_SERVICE_URLS: Tuple[str, ...] = (
    URL_KG_STATS,
    URL_KG_EXPLORE,
    URL_PREDICT,
    URL_TOP_K,
    URL_RANK,
    URL_RANK_BY_DRUG,
    URL_VALIDATE,
    URL_HEALTH,
)


# =============================================================================
# Default service ports (used by docker-compose and the frontend's env vars)
# =============================================================================
# These are NOT a contract — they're defaults. Operators may override via
# env vars (KG_SERVICE_URL, GT_SERVICE_URL, RL_SERVICE_URL).
SERVICE_PORTS: Dict[str, int] = {
    "phase2_kg": 8002,
    "phase3_gt": 8003,
    "phase4_rl": 8004,
    "validation": 8005,
}


# =============================================================================
# URL -> service mapping (for the contract consistency test)
# =============================================================================
# Maps each URL path to the service that owns it. The contract test
# verifies that the named service actually registers the path.
URL_TO_SERVICE: Dict[str, str] = {
    URL_KG_STATS: "phase2",
    URL_KG_EXPLORE: "phase2",
    URL_PREDICT: "phase3",
    URL_TOP_K: "phase3",
    URL_RANK: "phase4",
    URL_RANK_BY_DRUG: "phase4",
    URL_VALIDATE: "phase4",
    URL_HEALTH: "all",
}
