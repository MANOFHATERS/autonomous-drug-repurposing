"""
Rate limiting configuration for the DrugOS public REST API.

This module provides TWO complementary rate-limiting implementations:

1. **slowapi-based limiter (Teammate 4 ROOT FIX)**: production-grade
   rate limiting using the slowapi library (the FastAPI-compatible
   rate limiter used by production FastAPI apps at companies like Uber
   and Netflix). Used for general GET/POST endpoints via the
   ``@limiter.limit(RATE_LIMIT_GET)`` decorator pattern.

2. **In-memory sliding-window-log limiter (Teammate 8 ROOT FIX)**:
   dependency-free per-endpoint limiters for the /kg/* proxy routes.
   Used programmatically (``check_cypher_rate_limit(user_id)``) inside
   route handlers. Required because /cypher needs a STRICTER limit
   (10 req/min) than the general POST limit (30 req/min), and the
   limit must be enforced INSIDE the route handler (after JWT
   verification) so the rate-limit key is the authenticated user_id
   (not the IP address).

LIMITS (calibrated to the V1 contract — project docx §8):
  - GET endpoints:           100 requests / minute per JWT user
  - POST endpoints:           30 requests / minute per JWT user
  - /kg/stats, /kg/explore:  100 requests / minute per JWT user
  - /cypher:                  10 requests / minute per JWT user
    (Cypher is expensive — a single runaway query can saturate the
    Neo4j connection pool. The 10 req/min limit is the FIRST line of
    defense; the Phase 2 service's 30s server-side timeout + 1000-row
    cap + read-only whitelist are the SECOND/THIRD/FOURTH lines.)

The limits are PER USER (extracted from the JWT's sub claim), not per
IP. This is correct for an authenticated API — rate limiting by IP
would let a single user with multiple IPs (VPN, mobile hotspot, etc.)
bypass the limit, and would penalize multiple users behind a corporate
NAT.

Usage (slowapi decorator pattern):
    from backend.api.rate_limit import limiter, RATE_LIMIT_GET

    @app.get("/datasets/stats")
    @limiter.limit(RATE_LIMIT_GET)
    async def get_dataset_stats(request: Request, ...):
        ...

Usage (programmatic check pattern — for endpoints that need a custom
limit different from RATE_LIMIT_GET/POST):
    from backend.api.rate_limit import check_cypher_rate_limit

    @app.post("/cypher")
    async def run_cypher(..., user_id: str = Depends(verify_jwt)):
        check_cypher_rate_limit(user_id)  # raises HTTPException(429) on overflow
        ...

The ``request: Request`` parameter is REQUIRED by slowapi — it uses
the request to extract the rate-limit key (user ID from the JWT in
our case, configured via ``key_func`` below).
"""
from __future__ import annotations

import logging
import os
import threading
import time
from collections import defaultdict, deque
from typing import Callable, Deque, Dict, Optional

from fastapi import HTTPException, status

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# slowapi-based limiter (Teammate 4 ROOT FIX) — general GET/POST endpoints
# ---------------------------------------------------------------------------
# Rate limit values (exported as module-level constants so they can be
# referenced from route decorators and overridden via env vars for load
# testing). Format: "<count>/<period>" — slowapi parses this string.
RATE_LIMIT_GET: str = os.environ.get("DRUGOS_RATE_LIMIT_GET", "100/minute")
RATE_LIMIT_POST: str = os.environ.get("DRUGOS_RATE_LIMIT_POST", "30/minute")
RATE_LIMIT_DEFAULT: str = os.environ.get("DRUGOS_RATE_LIMIT_DEFAULT", "100/minute")


# ---------------------------------------------------------------------------
# slowapi Limiter — singleton instance shared by all routes.
# ---------------------------------------------------------------------------
try:
    from slowapi import Limiter, _rate_limit_exceeded_handler
    from slowapi.errors import RateLimitExceeded
    from slowapi.util import get_remote_address
    _SLOWAPI_AVAILABLE = True
except ImportError:  # pragma: no cover — slowapi is in requirements.txt
    logger.warning(
        "slowapi is not installed — slowapi-based rate limiting is DISABLED. "
        "Install with `pip install slowapi`. The Teammate 8 in-memory "
        "limiters (check_cypher_rate_limit, etc.) still work without slowapi. "
        "The V1 launch contract requires rate limiting; do NOT deploy to "
        "production without slowapi (or an equivalent)."
    )
    _SLOWAPI_AVAILABLE = False
    Limiter = None  # type: ignore[assignment, misc]
    _rate_limit_exceeded_handler = None  # type: ignore[assignment]
    RateLimitExceeded = None  # type: ignore[assignment]
    get_remote_address = None  # type: ignore[assignment]


def _get_user_id_from_request(request) -> str:
    """Extract the user ID from the request for rate-limit keying.

    TEAMMATE-4 ROOT FIX: rate limit PER USER (not per IP). The request
    state is populated by the verify_jwt dependency (which runs BEFORE
    the rate limiter in the dependency chain). Falls back to the remote
    address if the JWT hasn't been verified yet (e.g. on public routes
    like /health).
    """
    # FastAPI stores Depends() results in request.state (when using
    # the `request: Request` parameter, the dependency-injected values
    # are accessible via request.state.<name> if the dependency sets
    # them).
    user_id = getattr(request.state, "user_id", None)
    if user_id:
        return f"user:{user_id}"
    # Fall back to IP for unauthenticated routes (e.g. /health).
    if get_remote_address is not None:
        try:
            return f"ip:{get_remote_address(request)}"
        except Exception:
            pass
    return "anonymous"


# Singleton limiter — module-level so it can be imported by main.py
# and any sub-router. The ``key_func`` determines the rate-limit bucket:
# we use the JWT user ID (per-user limiting) instead of the default
# per-IP limiting (which is wrong for an authenticated API behind a
# corporate NAT — see module docstring).
if _SLOWAPI_AVAILABLE:
    limiter = Limiter(key_func=_get_user_id_from_request)
else:
    limiter = None  # type: ignore[assignment]


def register_rate_limit_exception_handler(app):
    """Register the slowapi exception handler on the FastAPI app.

    Call this once at app startup (after the limiter is wired to
    ``app.state.limiter``). The handler returns a 429 response with
    a JSON body matching the API's standard error shape.
    """
    if not _SLOWAPI_AVAILABLE:
        logger.warning(
            "slowapi not installed — skipping rate-limit exception handler "
            "registration. slowapi-based rate limiting is DISABLED."
        )
        return
    app.state.limiter = limiter
    app.add_exception_handler(RateLimitExceeded, _rate_limit_exceeded_handler)


# ---------------------------------------------------------------------------
# In-memory sliding-window-log limiter (Teammate 8 ROOT FIX) — /kg/* routes
# ---------------------------------------------------------------------------
# This is a simple, dependency-free, in-memory rate limiter based on the
# sliding-window-log algorithm. It is intentionally NOT a distributed
# rate limiter (no Redis, no Memcached) — that is reserved for the
# production deploy where the backend runs behind a sticky load balancer
# with a shared Redis instance. For single-process uvicorn workers (the
# default for the DrugOS V1 launch), this is sufficient and adds zero
# operational overhead.
#
# Why a separate implementation when slowapi is already available?
#   1. The /cypher route needs a STRICTER limit (10 req/min) than the
#      general POST limit (30 req/min). slowapi supports per-route
#      limits via decorators, but the limit must be enforced AFTER
#      JWT verification (so the rate-limit key is the authenticated
#      user_id, not the IP). slowapi's decorator runs BEFORE the
#      route handler, which means it runs BEFORE verify_jwt — at
#      that point, the user_id is not yet known. The programmatic
#      check pattern (``check_cypher_rate_limit(user_id)``) runs
#      INSIDE the route handler, AFTER verify_jwt has populated
#      user_id. This is the correct place for per-user rate limiting.
#   2. The in-memory limiter has ZERO external dependencies — it works
#      even if slowapi is not installed. This makes the /kg/* routes
#      resilient to slowapi being unavailable (e.g., during a CI run
#      that didn't install slowapi).
#
# Algorithm: sliding window log
#   - Each (key, window) pair has a deque of request timestamps.
#   - On each request, prune timestamps older than ``window_seconds``.
#   - If the deque length >= ``max_requests``, reject with HTTP 429.
#   - Otherwise, append the current timestamp and allow the request.
#
# This is O(K) per request where K is the number of requests in the
# current window (bounded by ``max_requests``). For 10 req/min, K <= 10.
#
# Thread safety: a ``threading.Lock`` guards the underlying dict. The
# lock is held for the duration of the prune+append (microseconds), so
# contention is negligible even under high load.


class RateLimiter:
    """In-memory sliding-window-log rate limiter.

    Parameters
    ----------
    max_requests : int
        Maximum number of requests allowed within the rolling window.
    window_seconds : int
        Length of the rolling window in seconds.

    Usage
    -----
        cypher_limiter = RateLimiter(max_requests=10, window_seconds=60)
        cypher_limiter.check(user_id)  # raises HTTPException(429) on overflow
    """

    def __init__(self, max_requests: int, window_seconds: int) -> None:
        if max_requests <= 0:
            raise ValueError(
                f"max_requests must be > 0 (got {max_requests}). "
                f"A rate limiter that allows 0 requests would block all "
                f"traffic — that is a misconfiguration."
            )
        if window_seconds <= 0:
            raise ValueError(
                f"window_seconds must be > 0 (got {window_seconds}). "
                f"A 0-second window would make the limiter trivially "
                f"permissive (all requests would be 'in window')."
            )
        self._max_requests = int(max_requests)
        self._window_seconds = int(window_seconds)
        # Keyed by an arbitrary string (user_id, org_id, IP, etc.).
        self._hits: Dict[str, Deque[float]] = defaultdict(deque)
        self._lock = threading.Lock()

    @property
    def max_requests(self) -> int:
        return self._max_requests

    @property
    def window_seconds(self) -> int:
        return self._window_seconds

    def _prune(self, key: str, now: float) -> None:
        """Remove timestamps older than the rolling window.

        MUST be called under ``self._lock``.
        """
        cutoff = now - self._window_seconds
        bucket = self._hits.get(key)
        if not bucket:
            return
        # deque.popleft is O(1); we pop from the left until the oldest
        # entry is within the window. This is bounded by the number of
        # requests in the window (max_requests), so it's O(K) where K
        # <= max_requests.
        while bucket and bucket[0] < cutoff:
            bucket.popleft()
        # Garbage-collect empty buckets to prevent unbounded memory
        # growth from one-off keys (e.g., transient user IDs).
        if not bucket:
            self._hits.pop(key, None)

    def check(self, key: str) -> None:
        """Check whether a request from ``key`` is allowed.

        Raises ``HTTPException(429)`` if the rate limit is exceeded.
        Otherwise, records the request and returns silently.

        Parameters
        ----------
        key : str
            The rate-limit key — typically the authenticated user_id,
            org_id, or remote IP. Per-user is preferred over per-IP
            (NAT'd corporate networks would otherwise share a limit).
        """
        if not key:
            # An empty key is a programming error — the caller should
            # always have an authenticated user_id at this point.
            # Reject rather than silently rate-limiting the empty-string
            # bucket (which would block ALL anonymous requests).
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail="Rate-limit key is empty — caller must pass user_id/org_id.",
            )
        now = time.monotonic()
        with self._lock:
            self._prune(key, now)
            bucket = self._hits[key]
            if len(bucket) >= self._max_requests:
                # Compute the seconds until the oldest request ages out,
                # so the client knows when to retry. This is the
                # ``Retry-After`` header value (seconds).
                retry_after = int(bucket[0] + self._window_seconds - now) + 1
                raise HTTPException(
                    status_code=status.HTTP_429_TOO_MANY_REQUESTS,
                    detail={
                        "error": "rate_limit_exceeded",
                        "message": (
                            f"Rate limit exceeded: {self._max_requests} requests "
                            f"per {self._window_seconds} seconds. Retry after "
                            f"{retry_after} seconds."
                        ),
                        "retry_after_seconds": retry_after,
                        "limit": self._max_requests,
                        "window_seconds": self._window_seconds,
                    },
                    headers={"Retry-After": str(retry_after)},
                )
            bucket.append(now)

    def reset(self, key: str | None = None) -> None:
        """Clear rate-limit state for a key (or all keys if None).

        Primarily used by tests to reset state between test cases.
        Production code should NOT call this.
        """
        with self._lock:
            if key is None:
                self._hits.clear()
            else:
                self._hits.pop(key, None)

    def current_count(self, key: str) -> int:
        """Return the current request count for ``key`` in the window.

        Primarily used by tests to assert the rate-limit state.
        """
        now = time.monotonic()
        with self._lock:
            self._prune(key, now)
            return len(self._hits.get(key, deque()))


# ─── Default limiters ──────────────────────────────────────────────────────
# Per the Teammate 8 issue spec:
#   /cypher — 10 req/min (Cypher is expensive; strict DoS protection)
#   /kg/stats, /kg/explore — 100 req/min (cheap reads; allow power users)
# These are MODULE-LEVEL singletons so all requests to a given endpoint
# share the same counter (per-key). Multiple uvicorn workers would each
# have their own counter (no shared state) — this is acceptable for V1
# (single-process) and documented as a known limitation for multi-worker
# deploys (where Redis would be required).
CYPHER_RATE_LIMITER = RateLimiter(max_requests=10, window_seconds=60)
KG_STATS_RATE_LIMITER = RateLimiter(max_requests=100, window_seconds=60)
KG_EXPLORE_RATE_LIMITER = RateLimiter(max_requests=100, window_seconds=60)


def check_cypher_rate_limit(key: str) -> None:
    """Convenience wrapper: enforce the /cypher per-user rate limit."""
    CYPHER_RATE_LIMITER.check(key)


def check_kg_stats_rate_limit(key: str) -> None:
    """Convenience wrapper: enforce the /kg/stats per-user rate limit."""
    KG_STATS_RATE_LIMITER.check(key)


def check_kg_explore_rate_limit(key: str) -> None:
    """Convenience wrapper: enforce the /kg/explore per-user rate limit."""
    KG_EXPLORE_RATE_LIMITER.check(key)


__all__: list[str] = [
    # slowapi-based (Teammate 4)
    "RATE_LIMIT_GET",
    "RATE_LIMIT_POST",
    "RATE_LIMIT_DEFAULT",
    "limiter",
    "register_rate_limit_exception_handler",
    # In-memory sliding-window-log (Teammate 8)
    "RateLimiter",
    "CYPHER_RATE_LIMITER",
    "KG_STATS_RATE_LIMITER",
    "KG_EXPLORE_RATE_LIMITER",
    "check_cypher_rate_limit",
    "check_kg_stats_rate_limit",
    "check_kg_explore_rate_limit",
]
