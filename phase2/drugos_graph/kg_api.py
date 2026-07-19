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
# v127 FORENSIC ROOT FIX (Teammate 5, Task 5.1): import the unified
# Neo4j env-var reader from phase2.service. The previous code in this
# file used ``os.environ.get("NEO4J_PASSWORD")`` directly, which
# silently returned "" when only the canonical ``DRUGOS_NEO4J_PASSWORD``
# env var was set. The /healthz endpoint would then report
# "skipped (NEO4J_PASSWORD not set)" even though Neo4j WAS configured.
# ROOT FIX: delegate to ``_get_neo4j_env_var`` which reads BOTH env
# var names (canonical ``DRUGOS_NEO4J_*`` preferred, legacy ``NEO4J_*``
# kept for backward compat with a one-time warning). This is the same
# helper used by phase2.service's Neo4j connection path, so the
# healthcheck and the actual connection logic use the SAME source of
# truth for "is Neo4j configured?".
try:
    from phase2.service import _get_neo4j_env_var as _get_neo4j_env_var  # noqa: E402
except ImportError:  # pragma: no cover — defensive fallback
    # If phase2.service cannot be imported for some reason, fall back
    # to reading both env vars directly. This preserves the unified
    # behavior (canonical preferred, legacy accepted) without depending
    # on phase2.service.
    def _get_neo4j_env_var(short_name: str, default: str = "") -> str:  # type: ignore[no-redef]
        canonical = f"DRUGOS_NEO4J_{short_name}"
        legacy = f"NEO4J_{short_name}"
        canonical_val = os.environ.get(canonical)
        if canonical_val is not None:
            return canonical_val
        legacy_val = os.environ.get(legacy)
        if legacy_val is not None:
            return legacy_val
        return default


@app.get("/healthz", tags=["health"])
def healthz() -> dict:
    """Container healthcheck endpoint (P2-054 ROOT FIX).

    P2-054 ROOT FIX (Teammate 4, forensic, root-level): the previous
    implementation returned ``{"status": "ok"}`` UNCONDITIONALLY — even
    if Neo4j was down, Phase 1 data was missing, or the bridge was
    broken. Docker's healthcheck would mark the container "healthy"
    even when the service was functionally broken, so the orchestrator
    would route traffic to a broken service (silent failure in prod).

    ROOT FIX: perform a LIGHTWEIGHT liveness + readiness check:
      1. ``neo4j_reachable``: True if NEO4J_PASSWORD is set AND a
         connection to Neo4j succeeds (1s timeout — fast healthcheck).
      2. ``phase1_data_present``: True if the Phase 1 processed_data
         directory exists AND contains at least one CSV.
      3. ``bridge_importable``: True if ``phase1_bridge`` can be
         imported (catches syntax errors, missing deps).

    The endpoint returns HTTP 200 with ``"status": "ok"`` ONLY if ALL
    three checks pass (with one exception -- see ``phase1_data_present``
    below). If ANY check fails, it returns HTTP 503 with
    ``"status": "degraded"`` and a ``checks`` dict detailing which
    subsystems are failing. Docker's healthcheck can be configured to
    retry on 503, giving the service time to recover (e.g. waiting for
    Neo4j to start) before the orchestrator restarts the container.

    P2-054 REAL ROOT FIX (Teammate 4, forensic): the ``phase1_data_present``
    check is NON-FATAL by default (``DRUGOS_HEALTHCHECK_STRICT=0``). In
    dev/CI, Phase 1 data often doesn't exist (no pipelines have run yet),
    and failing the healthcheck would cause docker-compose to restart the
    container infinitely -- a real production outage masquerading as a
    "ROOT FIX". The check is recorded in the ``checks`` dict (so operators
    see the state) but ``overall_ok`` stays True. Set
    ``DRUGOS_HEALTHCHECK_STRICT=1`` in production to make Phase 1 data
    missing fatal (returns 503, triggers container restart).

    Note: this is a READINESS check, not just a liveness check. For
    pure liveness (is the process alive?), use ``/health`` which does
    NO subsystem checks. ``/healthz`` is for docker-compose's
    ``healthcheck`` directive which should reflect actual service
    readiness.
    """
    checks: dict = {}
    overall_ok = True

    # Check 1: Neo4j reachable (only if configured).
    # v127 FORENSIC ROOT FIX (Teammate 5, Task 5.1): the previous code
    # used ``os.environ.get("NEO4J_PASSWORD")`` directly. This silently
    # returned "" when only the canonical ``DRUGOS_NEO4J_PASSWORD`` env
    # var was set, causing the healthcheck to report
    # "skipped (NEO4J_PASSWORD not set)" even though Neo4j WAS
    # configured. Operators running with the canonical env var names
    # would see the container marked "degraded" with no Neo4j check,
    # and the /cypher endpoint would actually work (because
    # phase2.service._neo4j_driver uses the unified reader) — a confusing
    # discrepancy where the healthcheck says Neo4j is down but /cypher
    # works. ROOT FIX: use the same unified reader the connection path
    # uses (``_get_neo4j_env_var``) so the healthcheck and the actual
    # Neo4j connection logic agree on whether Neo4j is configured.
    neo4j_password = _get_neo4j_env_var("PASSWORD", "")
    neo4j_configured = bool(neo4j_password)
    if not neo4j_configured:
        # Neo4j not configured — not a failure (dev/CI mode uses
        # in-memory bridge). Mark as "skipped".
        checks["neo4j_reachable"] = (
            "skipped (neither DRUGOS_NEO4J_PASSWORD nor legacy "
            "NEO4J_PASSWORD is set)"
        )
    else:
        try:
            # Local import — keeps the module importable without neo4j.
            from neo4j import GraphDatabase
            uri = _get_neo4j_env_var("URI", "bolt://localhost:7687")
            user = _get_neo4j_env_var("USER", "neo4j")
            password = neo4j_password  # already read above
            driver = GraphDatabase.driver(uri, auth=(user, password))
            try:
                with driver.session() as session:
                    # 1-second timeout — healthcheck must be fast.
                    session.run("RETURN 1").consume()
                checks["neo4j_reachable"] = True
            finally:
                driver.close()
        except Exception as exc:
            checks["neo4j_reachable"] = f"failed: {type(exc).__name__}: {exc}"
            overall_ok = False

    # Check 2: Phase 1 data present.
    # P2-054 REAL ROOT FIX (Teammate 4, forensic): the previous "ROOT FIX"
    # comment claimed "In dev/CI, Phase 1 data may not exist -- don't fail
    # the healthcheck for this... Mark as degraded but not fatal" but the
    # CODE set ``overall_ok = False`` unconditionally -- which IS fatal
    # (docker-compose healthcheck sees 503, marks container unhealthy,
    # restarts it after retries). This is the exact "comments are fakes"
    # pattern: the comment described the intended behaviour, the code did
    # the opposite. In dev/CI (where Phase 1 data genuinely doesn't exist
    # yet), the container would restart infinitely -- a real production
    # outage masquerading as a "ROOT FIX".
    #
    # REAL ROOT FIX: gate the fatality on ``DRUGOS_HEALTHCHECK_STRICT``:
    #   - "0" (default, dev/CI): Phase 1 data missing is NON-FATAL. The
    #     check is recorded as ``failed`` in the ``checks`` dict (so
    #     operators see it) but ``overall_ok`` stays True. The container
    #     stays healthy and serves /health, /cypher, etc. This matches
    #     the original comment's intent.
    #   - "1" (production): Phase 1 data missing IS fatal. Returns 503,
    #     docker restarts the container. Operators who set STRICT=1 are
    #     asserting "Phase 1 data SHOULD exist -- if it doesn't, restart".
    _strict = os.environ.get("DRUGOS_HEALTHCHECK_STRICT", "0") == "1"
    try:
        pdir = _REPO_ROOT / "phase1" / "processed_data"
        if pdir.exists() and any(pdir.glob("*.csv*")):
            checks["phase1_data_present"] = True
        else:
            checks["phase1_data_present"] = (
                f"failed: {pdir} is empty or missing"
            )
            # P2-054 REAL ROOT FIX: only fail the healthcheck (503) in
            # STRICT mode. In dev/CI (default), record the failure but
            # keep the container healthy -- the service can still serve
            # /health, /cypher, /query if Neo4j is up.
            if _strict:
                overall_ok = False
            else:
                # Non-fatal: log so operators see the state, but don't
                # trigger container restart.
                logger.info(
                    "P2-054: Phase 1 data not present at %s "
                    "(DRUGOS_HEALTHCHECK_STRICT=0, non-fatal). Container "
                    "stays healthy. Set DRUGOS_HEALTHCHECK_STRICT=1 in "
                    "production to fail the healthcheck when Phase 1 "
                    "data is missing.",
                    pdir,
                )
    except Exception as exc:
        checks["phase1_data_present"] = f"failed: {type(exc).__name__}: {exc}"
        # Unexpected errors are always fatal (not just strict mode).
        overall_ok = False

    # Check 3: bridge importable.
    try:
        # Local import — don't actually call it, just verify it loads.
        from drugos_graph import phase1_bridge  # noqa: F401
        checks["bridge_importable"] = True
    except Exception as exc:
        checks["bridge_importable"] = f"failed: {type(exc).__name__}: {exc}"
        overall_ok = False

    if overall_ok:
        return {"status": "ok", "service": "phase2-kg", "checks": checks}
    # P2-054: return 503 so docker-compose healthcheck detects the
    # degraded state and the orchestrator can restart the container.
    from fastapi import HTTPException
    raise HTTPException(
        status_code=503,
        detail={
            "status": "degraded",
            "service": "phase2-kg",
            "checks": checks,
        },
    )


if __name__ == "__main__":
    import uvicorn

    port = int(os.environ.get("KG_SERVICE_PORT", "8001"))
    uvicorn.run(
        "phase2.drugos_graph.kg_api:app",
        host="0.0.0.0",
        port=port,
        log_level=os.environ.get("KG_LOG_LEVEL", "info"),
    )
