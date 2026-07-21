"""
Rate limiting configuration for the DrugOS public REST API.

TEAMMATE-4 ROOT FIX (NEW FILE): the issue audit found that the FastAPI
backend had NO rate limiting. The V1 launch contract (project docx §8)
mandates "API handles 100 concurrent requests without timeout" — but
without rate limiting, a single misbehaving client (or a malicious
actor with a stolen JWT) can saturate the backend's connection pool
and starve legitimate pharma partners.

This module configures slowapi (the FastAPI-compatible rate limiter
used by production FastAPI apps at companies like Uber and Netflix).

LIMITS (calibrated to the V1 contract):
  - GET endpoints: 100 requests / minute per JWT user
    (the V1 contract's "100 concurrent requests" target — but sustained
    over 1 minute. A researcher running a dashboard refresh every 0.6s
    is the ceiling.)
  - POST endpoints: 30 requests / minute per JWT user
    (writes are more expensive — DB inserts, ML inference, etc. The
    /datasets/validated_hypotheses endpoint writes a row to PostgreSQL;
    30/min is plenty for a human-curated workflow but blocks script
    abuse.)

The limits are PER USER (extracted from the JWT's sub claim), not per
IP. This is correct for an authenticated API — rate limiting by IP
would let a single user with multiple IPs (VPN, mobile hotspot, etc.)
bypass the limit, and would penalize multiple users behind a corporate
NAT.

Usage:
    from backend.api.rate_limit import limiter, RATE_LIMIT_GET, RATE_LIMIT_POST

    @app.get("/datasets/stats")
    @limiter.limit(RATE_LIMIT_GET)
    async def get_dataset_stats(request: Request, ...):
        ...

The ``request: Request`` parameter is REQUIRED by slowapi — it uses
the request to extract the rate-limit key (user ID from the JWT in
our case, configured via ``key_func`` below).
"""
from __future__ import annotations

import logging
import os
from typing import Callable, Optional

logger = logging.getLogger(__name__)

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
        "slowapi is not installed — rate limiting is DISABLED. "
        "Install with `pip install slowapi`. The V1 launch contract "
        "requires rate limiting; do NOT deploy to production without it."
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
            "registration. Rate limiting is DISABLED."
        )
        return
    app.state.limiter = limiter
    app.add_exception_handler(RateLimitExceeded, _rate_limit_exceeded_handler)


__all__: list[str] = [
    "RATE_LIMIT_GET",
    "RATE_LIMIT_POST",
    "RATE_LIMIT_DEFAULT",
    "limiter",
    "register_rate_limit_exception_handler",
]
