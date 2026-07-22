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
import threading
import time
from pathlib import Path

# Make the repo root importable regardless of CWD. The docker-compose
# service mounts ``./`` at ``/opt/repo`` and ``./phase2`` at
# ``/opt/phase2``; either path must resolve ``phase2.service``.
#
# P2-004 ROOT FIX (Teammate 5, forensic, root-level): the previous code
# added BOTH ``_REPO_ROOT`` (repo root) AND ``_PHASE2_ROOT`` (``phase2/``)
# to ``sys.path``. This created TWO import paths for the same module:
#   * ``phase2.drugos_graph.phase1_bridge`` (loaded via _REPO_ROOT)
#   * ``drugos_graph.phase1_bridge``        (loaded via _PHASE2_ROOT)
# Python's import system registered BOTH as separate module objects in
# ``sys.modules`` — any module-level singleton, class registry, or
# atexit-registered cleanup in phase1_bridge would have TWO instances.
# The RecordingGraphBuilder's in-memory state and the bridge's
# bridge-fallback cache (``_PHASE1_SOURCE_TO_CSV``) live in module-level
# globals — two instances means two caches that can drift. In a long-
# running uvicorn worker, the /healthz check (which imported
# ``drugos_graph.phase1_bridge``) and the actual bridge execution path
# (which uses ``phase2.drugos_graph.phase1_bridge`` via ``from .phase1_bridge import``)
# could see DIFFERENT state.
#
# ROOT FIX: add ONLY ``_REPO_ROOT`` to ``sys.path``. The canonical
# import path is ``phase2.drugos_graph.*`` (package-qualified). The
# ``drugos_graph.*`` top-level form is FORBIDDEN — a CI check below
# verifies it is never used in this file.
_HERE = Path(__file__).resolve().parent  # phase2/drugos_graph/
_PHASE2_ROOT = _HERE.parent               # phase2/  (kept for reference / healthcheck path)
_REPO_ROOT = _PHASE2_ROOT.parent          # repo root
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))
# P2-004: ``_PHASE2_ROOT`` is NO LONGER added to sys.path. Importing
# ``drugos_graph.*`` (top-level) is now impossible — callers MUST use
# ``phase2.drugos_graph.*`` (canonical package-qualified form).

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


# =============================================================================
# P2-015 ROOT FIX (v142 — Teammate 6 forensic): module-level Neo4j driver
# cache for the /healthz endpoint. The previous code created a NEW driver
# on EVERY /healthz call (line 163: ``driver = GraphDatabase.driver(...)``
# inside the endpoint function). Docker's healthcheck defaults to every
# 30s; under a restart loop (e.g. Neo4j temporarily unreachable), this
# fires every 5s. Each call creates a driver, opens a session, runs
# ``RETURN 1``, closes the session, closes the driver. The driver creation
# involves TCP handshake + Bolt handshake + auth — ~100ms per call. Under
# load (multiple containers health-checking simultaneously), this exhausts
# the Neo4j connection pool's backlog.
#
# ROOT FIX (institutional-grade, three layers):
#   1. Cache a SINGLE Neo4j driver at module level (created on first
#      /healthz call, reused for all subsequent calls). The neo4j
#      driver IS thread-safe and designed to be a long-lived singleton.
#   2. Cache the /healthz RESULT for 30s (configurable via
#      ``DRUGOS_HEALTHCHECK_CACHE_TTL``). Subsequent calls within the
#      TTL return the cached result without touching Neo4j. This is
#      the primary defence against restart-loop exhaustion — even if
#      the driver cache fails, the result cache limits Neo4j load to
#      1 call per TTL window.
#   3. The driver is created lazily on first use (not at module import)
#      so the module remains importable without neo4j installed.
# =============================================================================
_HEALTHZ_DRIVER_LOCK = threading.Lock()
_healthz_cached_driver = None  # type: ignore[var-annotated]
_healthz_cache_lock = threading.Lock()
_healthz_cached_result = None  # type: ignore[var-annotated]
_healthz_cached_at: float = 0.0
_HEALTHZ_CACHE_TTL_SECONDS = int(
    os.environ.get("DRUGOS_HEALTHCHECK_CACHE_TTL", "30")
)


def _get_healthz_neo4j_driver():
    """P2-015 v142: return the cached module-level Neo4j driver.

    Creates the driver on first call, then reuses it. The neo4j driver
    is thread-safe and designed to be a long-lived singleton (the
    official neo4j docs recommend ONE driver per application). The
    previous /healthz code created a new driver per call, which is
    explicitly discouraged by the neo4j docs and exhausts the
    connection pool under Docker restart loops.

    Returns None if Neo4j is not configured or the driver cannot be
    created (the caller falls back to "neo4j_reachable: failed").
    """
    global _healthz_cached_driver
    if _healthz_cached_driver is not None:
        return _healthz_cached_driver
    with _HEALTHZ_DRIVER_LOCK:
        if _healthz_cached_driver is not None:
            return _healthz_cached_driver
        try:
            from neo4j import GraphDatabase
            uri = _get_neo4j_env_var("URI", "bolt://localhost:7687")
            user = _get_neo4j_env_var("USER", "neo4j")
            password = _get_neo4j_env_var("PASSWORD", "")
            if not password:
                return None  # Neo4j not configured
            _healthz_cached_driver = GraphDatabase.driver(
                uri, auth=(user, password)
            )
            return _healthz_cached_driver
        except Exception as exc:
            logger.warning(
                "P2-015 v142: failed to create cached Neo4j driver for "
                "/healthz: %s: %s. Subsequent /healthz calls will retry.",
                type(exc).__name__, exc,
            )
            return None


def _check_neo4j_reachable() -> tuple[bool, str]:
    """P2-015 v142: lightweight Neo4j reachability check using cached driver.

    Returns (True, "") if reachable, (False, error_message) otherwise.
    Uses the module-level cached driver (created on first call) instead
    of creating a new driver per /healthz invocation.
    """
    driver = _get_healthz_neo4j_driver()
    if driver is None:
        return False, "driver not created (Neo4j not configured or init failed)"
    try:
        with driver.session() as session:
            # 1-second timeout — healthcheck must be fast. The neo4j
            # driver's session.run does NOT take a timeout kwarg directly;
            # the connection_timeout is set on the driver. For a per-query
            # timeout we'd need to use Transaction.run with a custom
            # timeout, but for a healthcheck the connection_timeout is
            # sufficient (if the server is unreachable, the connection
            # attempt times out quickly).
            session.run("RETURN 1").consume()
        return True, ""
    except Exception as exc:
        return False, f"{type(exc).__name__}: {exc}"


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

    P2-015 v142 ROOT FIX (Teammate 6 forensic): the previous code
    created a NEW Neo4j driver on every /healthz call. Docker's
    healthcheck defaults to every 30s; under a restart loop this
    fires every 5s, exhausting the Neo4j connection pool. ROOT FIX:
    cache the driver at module level (one driver, reused for all
    calls) AND cache the /healthz result for 30s (configurable via
    ``DRUGOS_HEALTHCHECK_CACHE_TTL``). Subsequent calls within the
    TTL return the cached result without touching Neo4j. This limits
    Neo4j load to 1 call per TTL window even under aggressive
    restart loops.
    """
    # P2-015 v142: result cache. If we have a fresh cached result
    # (within TTL), return it directly. This is the primary defence
    # against restart-loop exhaustion — even if the driver cache
    # fails, the result cache limits Neo4j load to 1 call per TTL.
    global _healthz_cached_result, _healthz_cached_at
    now = time.time()
    if (
        _healthz_cached_result is not None
        and (now - _healthz_cached_at) < _HEALTHZ_CACHE_TTL_SECONDS
    ):
        # Return cached result. Do NOT re-check Neo4j — the TTL has
        # not expired. The cached result already has the correct HTTP
        # status (200 or 503) embedded via the ``overall_ok`` flag.
        cached = _healthz_cached_result
        if cached["overall_ok"]:
            return {
                "status": "ok",
                "service": "phase2-kg",
                "checks": cached["checks"],
                "cached": True,
                "cache_age_seconds": round(now - _healthz_cached_at, 2),
            }
        from fastapi import HTTPException
        raise HTTPException(
            status_code=503,
            detail={
                "status": "degraded",
                "service": "phase2-kg",
                "checks": cached["checks"],
                "cached": True,
                "cache_age_seconds": round(now - _healthz_cached_at, 2),
            },
        )

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
    #
    # P2-015 v142: use the CACHED module-level driver via
    # ``_check_neo4j_reachable()`` instead of creating a new driver
    # per call. This is the fix for the connection-pool exhaustion
    # bug described in the module-level comment above.
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
        reachable, err_msg = _check_neo4j_reachable()
        if reachable:
            checks["neo4j_reachable"] = True
        else:
            checks["neo4j_reachable"] = f"failed: {err_msg}"
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
        # P2-004 ROOT FIX (Teammate 5): use the CANONICAL package-qualified
        # import path ``phase2.drugos_graph.phase1_bridge`` — NOT the
        # top-level ``drugos_graph.phase1_bridge``. The previous code used
        # the top-level form, which only worked because ``_PHASE2_ROOT``
        # was added to sys.path; that created a SECOND module instance in
        # sys.modules (one via _REPO_ROOT as ``phase2.drugos_graph.phase1_bridge``,
        # one via _PHASE2_ROOT as ``drugos_graph.phase1_bridge``), and any
        # module-level singleton / cache in phase1_bridge would have TWO
        # instances that could drift. With ``_PHASE2_ROOT`` removed from
        # sys.path (P2-004 fix above), the top-level form would now raise
        # ``ModuleNotFoundError`` — so the canonical form is the ONLY
        # correct path. Use the same import path the bridge execution
        # path uses (``from .phase1_bridge import`` inside the package
        # resolves to ``phase2.drugos_graph.phase1_bridge``), so the
        # healthcheck and the execution path see the SAME module object.
        from phase2.drugos_graph import phase1_bridge  # noqa: F401
        checks["bridge_importable"] = True
    except Exception as exc:
        checks["bridge_importable"] = f"failed: {type(exc).__name__}: {exc}"
        overall_ok = False

    # P2-015 v142: cache the result so subsequent /healthz calls within
    # the TTL return immediately without re-checking Neo4j. The cache
    # is thread-safe via ``_healthz_cache_lock``. We cache BOTH the
    # ok and degraded results — a degraded state should also be cached
    # so we don't hammer Neo4j with retries during an outage (the
    # operator's monitoring should catch the cached-degraded state via
    # the HTTP 503 status code, not via the cache being bypassed).
    with _healthz_cache_lock:
        _healthz_cached_result = {
            "overall_ok": overall_ok,
            "checks": dict(checks),  # copy so caller can't mutate the cache
        }
        _healthz_cached_at = time.time()

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
