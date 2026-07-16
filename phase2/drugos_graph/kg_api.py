#!/usr/bin/env python3
"""Phase 2 Knowledge Graph HTTP API (canonical FastAPI service).

Task 356 ROOT FIX: ``docker-compose.yml`` referenced
``phase2.drugos_graph.kg_api:app`` but the file did not exist, so the
``phase2-kg-builder`` container could not start. ``uvicorn`` would emit
``ModuleNotFoundError`` and crash on boot, leaving the frontend's
``/api/knowledge-graph`` route dead in production.

This module is the production entry point for the Phase 2 KG service
that docker-compose starts. It re-exports the ``app`` object from
``phase2.service`` (the actual FastAPI app — see ``phase2/service.py``
for the full endpoint documentation), then adds two safety nets required
for containerized deployment:

1. ``GET /healthz`` — Docker-native healthcheck endpoint (separate from
   the existing ``GET /health`` which returns rich metadata). Returns a
   tiny ``{"status":"ok"}`` body with HTTP 200 so the docker-compose
   healthcheck command ``curl -fsS http://localhost:8001/healthz`` works
   without parsing JSON.
2. Module-level path setup so ``uvicorn phase2.drugos_graph.kg_api:app``
   works whether invoked from the repo root, ``/opt/phase2``, or
   ``/opt/repo`` inside the container (the docker-compose service mounts
   both ``./phase2`` and ``./``).

Run
---
    uvicorn phase2.drugos_graph.kg_api:app --host 0.0.0.0 --port 8001

The service is stateless — it reads Neo4j credentials from env vars and
falls back to the in-memory RecordingGraphBuilder when Neo4j is not
configured (dev/CI mode).
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

# Make the repo root + phase2 importable regardless of CWD. The
# docker-compose service mounts ``./`` at ``/opt/repo`` and ``./phase2``
# at ``/opt/phase2``; either path must resolve ``phase2.service``.
_HERE = Path(__file__).resolve().parent  # phase2/drugos_graph/
_PHASE2_ROOT = _HERE.parent               # phase2/
_REPO_ROOT = _PHASE2_ROOT.parent          # repo root
for _p in (str(_REPO_ROOT), str(_PHASE2_ROOT)):
    if _p not in sys.path:
        sys.path.insert(0, _p)

# Re-export the FastAPI app from phase2.service. That module already
# implements /health, /kg/stats, /kg/explore, /query, /cypher with the
# Neo4j + in-memory fallback logic. Re-exporting avoids duplicating the
# endpoint code in two places (which is exactly the kind of drift that
# caused the original bug — see FORENSIC_AUDIT_FIX_SUMMARY_V29 §3.4).
from phase2.service import app, logger  # noqa: E402  (after sys.path setup)


@app.get("/healthz", tags=["health"])
def healthz() -> dict:
    """Container healthcheck endpoint.

    Returns a tiny 200-OK body. Used by docker-compose's healthcheck
    directive so the orchestrator can mark this service healthy without
    parsing the richer ``/health`` JSON.
    """
    return {"status": "ok", "service": "phase2-kg"}


if __name__ == "__main__":
    import uvicorn

    port = int(os.environ.get("KG_SERVICE_PORT", "8001"))
    uvicorn.run(
        "phase2.drugos_graph.kg_api:app",
        host="0.0.0.0",
        port=port,
        log_level=os.environ.get("KG_LOG_LEVEL", "info"),
    )
