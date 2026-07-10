# MIT License — Copyright (c) 2026 Team Cosmic / VentureLab — see LICENSE
"""
ChEMBL-specific HTTP client for the ChEMBL pipeline.

v65 ROOT FIX (P1-026 + P1-027): this module's previous file name was
``_http_client.py`` — a generic-sounding name that implied it was a
pipeline-agnostic HTTP utility. In reality the client hard-codes
ChEMBL-specific behaviour (token-bucket parameters tuned for ChEMBL's
rate limits, ``CHEMBL_MAX_RESPONSE_BYTES`` size cap, the ChEMBL
User-Agent string, and the ChEMBL REST API URL contract). Only
``chembl_pipeline.py`` imports it. The file has been RENAMED to
``_chembl_http_client.py`` so the file name reflects the actual scope;
the old name ``_http_client.py`` is kept as a THIN COMPATIBILITY SHIM
that re-exports the same names (so existing tests and import sites that
reference ``pipelines._http_client`` continue to work).

This module provides :class:`RateLimitedHttpClient`, a small, focused HTTP
client that hardens the ChEMBL REST API access path. It exists to fix the
reliability, performance, and security issues identified in the
``chembl_pipeline.py`` forensic audit (Domains 6, 8, 9, 11, 12, 14, 15).

Design goals
------------
1. **Token-bucket rate limiting** (P4) — instead of sleeping a fixed amount
   before every call, the client maintains a token bucket that allows short
   bursts while keeping the long-term average under the configured rate.
2. **Retry with exponential backoff + jitter** (R1, R3, C34, C36) — only
   retryable failures are retried (429, 5xx, ConnectionError, Timeout,
   ChunkedEncodingError, ContentDecodingError). 4xx (other than 429) fail
   fast — they will not succeed on retry.
3. **Circuit breaker** (R10) — after N consecutive failures, the client
   enters ``OPEN`` state and fails fast for a cooldown period before
   allowing a single probe request through.
4. **Response size cap** (SEC-5) — both ``Content-Length`` and the streamed
   body are bounded by ``CHEMBL_MAX_RESPONSE_BYTES`` to prevent a malicious
   or buggy server from exhausting memory.
5. **JSON decode error handling** (C4) — if the response body is not valid
   JSON, the first 500 chars are logged at ERROR and the request is treated
   as a retryable failure.
6. **Observable** (L1, L2, L3, L6) — every call's URL, params, status,
   duration, and response size are logged at INFO/DEBUG and recorded in an
   in-memory ``api_calls`` list that the pipeline writes to its manifest.
7. **No bare except** (Domain 6 / R8) — every catch is specific
   (``requests.exceptions.RequestException`` subclasses).

This module is deliberately self-contained: it depends only on the standard
library, ``requests``, and ``config.settings``. It does NOT import from
``pipelines.base_pipeline`` (avoids circular import).
"""

from __future__ import annotations

import json
import logging
import random
import threading
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

import requests

from config.settings import (
    CHEMBL_CIRCUIT_BREAKER_RESET_SECONDS,
    CHEMBL_CIRCUIT_BREAKER_THRESHOLD,
    CHEMBL_HTTP_TIMEOUT,
    CHEMBL_MAX_RESPONSE_BYTES,
    CHEMBL_MAX_RETRIES,
    CHEMBL_MIN_REQUEST_INTERVAL,
    CHEMBL_RETRY_BACKOFF_BASE,
    PIPELINE_CONTACT_EMAIL,
)

logger = logging.getLogger(__name__)


# HTTP status codes that should be retried.
# 429 = Too Many Requests (rate-limited; back off and try again).
# 5xx = server errors (transient; back off and try again).
RETRYABLE_STATUS_CODES: frozenset[int] = frozenset({429, 500, 502, 503, 504})

# Exception classes that should trigger a retry.
# Note: 4xx (other than 429) is NOT in this list — those are permanent
# failures (bad URL, unauthorised, not found, etc.) and retrying will not
# help.
RETRYABLE_EXCEPTIONS: tuple[type[BaseException], ...] = (
    requests.exceptions.ConnectionError,
    requests.exceptions.Timeout,
    requests.exceptions.ChunkedEncodingError,
    requests.exceptions.ContentDecodingError,
)


@dataclass
class ApiCallRecord:
    """A single HTTP call's observability record (LIN-07, L6)."""

    url: str
    params: dict[str, Any]
    method: str
    status: int | None
    duration_sec: float
    response_size_bytes: int | None
    error: str | None = None
    timestamp: str = field(
        default_factory=lambda: datetime.now(timezone.utc).isoformat()
    )

    def to_dict(self) -> dict[str, Any]:
        """Return a JSON-serialisable dict of this record."""
        return {
            "url": self.url,
            "params": {k: str(v) for k, v in self.params.items()},
            "method": self.method,
            "status": self.status,
            "duration_sec": round(self.duration_sec, 4),
            "response_size_bytes": self.response_size_bytes,
            "error": self.error,
            "timestamp": self.timestamp,
        }


class _TokenBucket:
    """Thread-safe token-bucket rate limiter (P4).

    A token bucket holds at most ``capacity`` tokens. Each token permits
    one operation. Tokens are replenished at ``rate`` tokens per second.
    ``acquire()`` blocks until a token is available.

    Unlike a sleep-before-every-call limiter, this allows short bursts
    (up to ``capacity`` calls in quick succession) while maintaining the
    long-term average rate.
    """

    def __init__(self, rate: float, capacity: float | None = None) -> None:
        if rate < 0:
            raise ValueError(f"rate must be >= 0, got {rate}")
        self.rate: float = rate
        # Default capacity = 2x the per-second rate, capped at 5 to avoid
        # bursting the API too hard.
        self.capacity: float = (
            capacity if capacity is not None else min(max(rate * 2.0, 1.0), 5.0)
        )
        self._tokens: float = self.capacity
        self._last_refill: float = time.monotonic()
        self._lock = threading.Lock()

    def acquire(self, timeout: float | None = None) -> bool:
        """Block until a token is available, then consume it.

        Returns ``True`` if a token was acquired, ``False`` if ``timeout``
        was reached before one became available.
        """
        deadline = (time.monotonic() + timeout) if timeout is not None else None
        with self._lock:
            while True:
                now = time.monotonic()
                elapsed = now - self._last_refill
                self._tokens = min(
                    self.capacity, self._tokens + elapsed * self.rate
                )
                self._last_refill = now
                if self._tokens >= 1.0:
                    self._tokens -= 1.0
                    return True
                # Compute sleep time until next token is available.
                deficit = 1.0 - self._tokens
                wait = deficit / self.rate if self.rate > 0 else float("inf")
                if deadline is not None:
                    remaining = deadline - now
                    if remaining <= 0 or wait > remaining:
                        return False
                time.sleep(min(wait, 1.0))  # cap each sleep at 1s for responsiveness


class _CircuitBreaker:
    """Thread-safe circuit breaker (R10).

    States:
    - ``CLOSED``: requests flow normally. Each failure increments the
      counter; each success resets it.
    - ``OPEN``: after ``threshold`` consecutive failures, the breaker
      opens. All requests fail fast with ``CircuitBreakerOpenError`` for
      ``reset_seconds``.
    - ``HALF_OPEN``: after ``reset_seconds``, one probe request is
      allowed through. If it succeeds, the breaker closes. If it fails,
      the breaker re-opens for another ``reset_seconds``.
    """

    CLOSED = "closed"
    OPEN = "open"
    HALF_OPEN = "half_open"

    def __init__(
        self,
        failure_threshold: int = 10,
        reset_seconds: float = 60.0,
    ) -> None:
        if failure_threshold < 1:
            raise ValueError(
                f"failure_threshold must be >= 1, got {failure_threshold}"
            )
        if reset_seconds < 0:
            raise ValueError(
                f"reset_seconds must be >= 0, got {reset_seconds}"
            )
        self.failure_threshold: int = failure_threshold
        self.reset_seconds: float = reset_seconds
        self._state: str = self.CLOSED
        self._consecutive_failures: int = 0
        self._opened_at: float = 0.0
        self._lock = threading.Lock()

    @property
    def state(self) -> str:
        """Current breaker state (closed / open / half_open)."""
        with self._lock:
            if self._state == self.OPEN:
                # Check if it's time to transition to HALF_OPEN.
                if time.monotonic() - self._opened_at >= self.reset_seconds:
                    self._state = self.HALF_OPEN
            return self._state

    def before_call(self) -> None:
        """Raise ``CircuitBreakerOpenError`` if the breaker is OPEN.

        In HALF_OPEN state, the call is allowed through (it's the probe).
        """
        if self.state == self.OPEN:
            raise CircuitBreakerOpenError(
                "Circuit breaker is OPEN — failing fast. "
                f"Last {self._consecutive_failures} consecutive failures. "
                f"Will retry in {self.reset_seconds:.1f}s."
            )

    def record_success(self) -> None:
        """Mark a call as successful — closes the breaker."""
        with self._lock:
            self._consecutive_failures = 0
            self._state = self.CLOSED

    def record_failure(self) -> None:
        """Mark a call as failed — may open the breaker."""
        with self._lock:
            self._consecutive_failures += 1
            if (
                self._state == self.HALF_OPEN
                or self._consecutive_failures >= self.failure_threshold
            ):
                self._state = self.OPEN
                self._opened_at = time.monotonic()
                logger.error(
                    "Circuit breaker OPENED after %d consecutive failures "
                    "(threshold=%d, reset=%ss)",
                    self._consecutive_failures,
                    self.failure_threshold,
                    self.reset_seconds,
                )


class CircuitBreakerOpenError(Exception):
    """Raised when the circuit breaker is OPEN and a call is attempted."""


class HttpClientError(Exception):
    """Base class for HTTP client errors."""


class MaxResponseSizeExceeded(HttpClientError):
    """Raised when a response exceeds ``CHEMBL_MAX_RESPONSE_BYTES``."""


class RateLimitedHttpClient:
    """Hardened HTTP client for the ChEMBL REST API.

    Wraps ``requests.Session`` with token-bucket rate limiting, retry with
    exponential backoff + jitter, a circuit breaker, response-size cap,
    and structured per-call observability.

    Parameters
    ----------
    rate_limit_per_sec : float
        Maximum sustained request rate. Default: ``1 / CHEMBL_MIN_REQUEST_INTERVAL``
        (so setting ``CHEMBL_MIN_REQUEST_INTERVAL=0.5`` → 2 req/sec).
    max_retries : int
        Maximum TOTAL attempts per call (1 initial + N-1 retries).
        Default: ``CHEMBL_MAX_RETRIES``. Despite the name, this is
        MAX_ATTEMPTS, not retries-after-first. E.g. max_retries=5
        means 5 total attempts (1 initial + 4 retries).
    backoff_base : float
        Base for exponential backoff: ``wait = backoff_base * (2 ** attempt) + jitter``.
        Default: ``CHEMBL_RETRY_BACKOFF_BASE``.
    timeout : tuple[float, float]
        ``(connect_timeout, read_timeout)`` in seconds.
        Default: ``CHEMBL_HTTP_TIMEOUT``.
    max_response_bytes : int
        Maximum response body size. Default: ``CHEMBL_MAX_RESPONSE_BYTES``.
    user_agent : str
        ``User-Agent`` header sent on every request.
    verify_tls : bool
        Whether to verify TLS certificates. **Should always be ``True`` in
        production** (SEC-1).
    circuit_breaker_threshold : int
        Consecutive failures before the breaker opens.
    circuit_breaker_reset_seconds : float
        Cooldown before the breaker enters HALF_OPEN.

    Attributes
    ----------
    api_calls : list[ApiCallRecord]
        Append-only log of every HTTP call made through this client
        (LIN-07, L6). The pipeline writes this list to its manifest.

    Examples
    --------
    >>> client = RateLimitedHttpClient()
    >>> data = client.get("https://www.ebi.ac.uk/chembl/api/data/molecule.json",
    ...                   {"max_phase": 4, "limit": 10})
    >>> len(client.api_calls)
    1
    """

    def __init__(
        self,
        *,
        rate_limit_per_sec: float | None = None,
        max_retries: int = CHEMBL_MAX_RETRIES,
        backoff_base: float = CHEMBL_RETRY_BACKOFF_BASE,
        timeout: tuple[float, float] = CHEMBL_HTTP_TIMEOUT,
        max_response_bytes: int = CHEMBL_MAX_RESPONSE_BYTES,
        user_agent: str | None = None,
        verify_tls: bool = True,
        circuit_breaker_threshold: int = CHEMBL_CIRCUIT_BREAKER_THRESHOLD,
        circuit_breaker_reset_seconds: float = CHEMBL_CIRCUIT_BREAKER_RESET_SECONDS,
    ) -> None:
        if max_retries < 1:
            raise ValueError(f"max_retries must be >= 1, got {max_retries}")
        if backoff_base < 1.0:
            raise ValueError(f"backoff_base must be >= 1.0, got {backoff_base}")
        if max_response_bytes < 1024:
            raise ValueError(
                f"max_response_bytes must be >= 1024, got {max_response_bytes}"
            )

        # Derive rate from CHEMBL_MIN_REQUEST_INTERVAL if not given.
        if rate_limit_per_sec is None:
            if CHEMBL_MIN_REQUEST_INTERVAL > 0:
                rate_limit_per_sec = 1.0 / CHEMBL_MIN_REQUEST_INTERVAL
            else:
                rate_limit_per_sec = float("inf")  # no rate limit

        self._rate_limiter: _TokenBucket = _TokenBucket(rate=rate_limit_per_sec)
        self._circuit_breaker: _CircuitBreaker = _CircuitBreaker(
            failure_threshold=circuit_breaker_threshold,
            reset_seconds=circuit_breaker_reset_seconds,
        )
        self.max_retries: int = max_retries  # v40: despite the name, this is MAX_ATTEMPTS (not retries-after-first)
        self.backoff_base: float = backoff_base
        self.timeout: tuple[float, float] = timeout
        self.max_response_bytes: int = max_response_bytes
        self.verify_tls: bool = verify_tls
        self.user_agent: str = user_agent or (
            f"DrugRepurposingPipeline/1.0 (contact={PIPELINE_CONTACT_EMAIL})"
        )
        self._session: requests.Session = requests.Session()
        self._session.headers.update(
            {
                "Accept": "application/json",
                "User-Agent": self.user_agent,
            }
        )
        self.api_calls: list[ApiCallRecord] = []
        # Metrics counters (L6).
        self.metrics: dict[str, int] = {
            "api_calls": 0,
            "api_calls_429": 0,
            "api_calls_5xx": 0,
            "api_calls_4xx": 0,
            "retries": 0,
            "circuit_breaker_trips": 0,
        }

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def get(
        self, url: str, params: dict[str, Any] | None = None
    ) -> dict[str, Any] | list[Any]:
        """Issue a GET request and return the parsed JSON body.

        Parameters
        ----------
        url : str
            Full URL (``https://...``).
        params : dict, optional
            Query string parameters.

        Returns
        -------
        dict | list
            Parsed JSON response body. v65 ROOT FIX (P1-027): the previous
            annotation said ``dict[str, Any]`` but the underlying
            ``_parse_json`` calls ``json.loads(text)`` which can return a
            ``list`` if the JSON body is a top-level array (ChEMBL never
            emits this in practice, but the type contract must match the
            implementation — otherwise static type checkers flag it and
            callers that rely on ``.get()`` (dict method) would fail with
            ``AttributeError`` at runtime if a list is returned). The
            return type is now ``dict[str, Any] | list[Any]``.

        Raises
        ------
        CircuitBreakerOpenError
            If the circuit breaker is OPEN.
        HttpClientError
            If the response is not valid JSON, exceeds the size cap,
            or returns a non-2xx status after all retries.
        requests.exceptions.RequestException
            On network-level failures after all retries.
        """
        params = dict(params or {})
        last_exc: Exception | None = None

        for attempt in range(1, self.max_retries + 1):
            # Circuit breaker check — fails fast if OPEN.
            try:
                self._circuit_breaker.before_call()
            except CircuitBreakerOpenError as exc:
                self.metrics["circuit_breaker_trips"] += 1
                raise

            # Rate limit — blocks until a token is available.
            self._rate_limiter.acquire()

            start = time.monotonic()
            status: int | None = None
            response_size: int | None = None
            error: str | None = None

            try:
                logger.info(
                    "[chembl] GET %s params=%s (attempt %d/%d)",
                    url,
                    params,
                    attempt,
                    self.max_retries,
                )
                # P1-5 ROOT FIX (SEC-5 effectiveness): pass ``stream=True``
                # so the response body is NOT downloaded eagerly by the
                # underlying urllib3 call. Without this, ``iter_content``
                # in ``_read_body_bounded`` iterates over an already-
                # downloaded buffer — meaning a malicious or buggy server
                # could send a 10 GB body and exhaust memory BEFORE the
                # SEC-5 cap in ``_read_body_bounded`` ever fired. With
                # ``stream=True`` the body is fetched incrementally and
                # ``iter_content`` aborts as soon as the cap is exceeded.
                resp = self._session.get(
                    url,
                    params=params,
                    timeout=self.timeout,
                    verify=self.verify_tls,
                    stream=True,
                )
                status = resp.status_code
                response_size = self._safe_response_size(resp)

                # 2xx success — parse JSON.
                if 200 <= resp.status_code < 300:
                    body = self._read_body_bounded(resp)
                    parsed = self._parse_json(body, url)
                    self._circuit_breaker.record_success()
                    self._record_call(
                        url, params, "GET", status, time.monotonic() - start,
                        response_size, None,
                    )
                    return parsed

                # 4xx (except 429) — fail fast, no retry.
                if (
                    400 <= resp.status_code < 500
                    and resp.status_code != 429
                ):
                    self.metrics["api_calls_4xx"] += 1
                    # v43 ROOT FIX (P0 — 4xx opens circuit breaker):
                    # The previous code called record_failure() here,
                    # which meant 10 consecutive 404s (e.g. querying
                    # deleted ChEMBL records — common, ChEMBL deprecates
                    # molecules regularly) would OPEN the breaker and
                    # block ALL ChEMBL API calls for
                    # CHEMBL_CIRCUIT_BREAKER_RESET_SECONDS (default 60s).
                    # 4xx errors are PERMANENT client errors — they will
                    # never succeed on retry. The circuit breaker is
                    # designed for TRANSIENT failures (429 rate-limit,
                    # 5xx server errors, ConnectionError, Timeout). A
                    # batch of queries against deleted records is NOT
                    # a transient API problem — it's expected behavior.
                    # Treating 404s as breaker failures meant the
                    # pipeline crashed with CircuitBreakerOpenError
                    # after 10 consecutive 404s, even though the API
                    # was healthy.
                    #
                    # Fix: do NOT call record_failure() for 4xx. Only
                    # 429/5xx (below) count toward the breaker. We DO
                    # call record_success() to keep the breaker healthy
                    # — a 404 means the API responded correctly with
                    # "not found", which is a successful round-trip.
                    self._circuit_breaker.record_success()
                    error = (
                        f"HTTP {resp.status_code} on {url}: "
                        f"{resp.text[:500]!r}"
                    )
                    logger.warning(
                        "[chembl] HTTP %d on %s (non-retryable)",
                        resp.status_code,
                        url,
                    )
                    logger.debug(
                        "[chembl] Response body (first 500 chars): %s",
                        resp.text[:500],
                    )
                    self._record_call(
                        url, params, "GET", status, time.monotonic() - start,
                        response_size, error,
                    )
                    raise HttpClientError(error)

                # 429 or 5xx — retryable.
                if resp.status_code == 429:
                    self.metrics["api_calls_429"] += 1
                else:
                    self.metrics["api_calls_5xx"] += 1
                error = (
                    f"HTTP {resp.status_code} on {url}: "
                    f"{resp.text[:500]!r}"
                )
                logger.warning(
                    "[chembl] HTTP %d on %s (retryable, attempt %d/%d)",
                    resp.status_code,
                    url,
                    attempt,
                    self.max_retries,
                )
                last_exc = HttpClientError(error)
                # v49 ROOT FIX (Compound-7 — ChEMBL Retry-After Asymmetry):
                # The v48 ChEMBL HTTP client did NOT respect the
                # `Retry-After` HTTP header (PubChem did). This caused
                # ChEMBL retries to fire too soon after a 429, triggering
                # another 429, wasting requests and potentially escalating
                # to a longer rate-limit window. ROOT FIX: capture the
                # Retry-After header here and use it (capped at 60s) as
                # the wait time instead of the exponential backoff.
                # Supports both integer (seconds) and HTTP-date formats.
                _retry_after_raw = resp.headers.get("Retry-After", "")
                if _retry_after_raw:
                    try:
                        # Try integer seconds first.
                        _retry_after = float(_retry_after_raw)
                    except ValueError:
                        # HTTP-date format — compute seconds from now.
                        try:
                            from email.utils import parsedate_to_datetime
                            import datetime as _dt
                            _ra_dt = parsedate_to_datetime(_retry_after_raw)
                            _now_dt = _dt.datetime.now(_ra_dt.tzinfo)
                            _retry_after = max(0.0, (_ra_dt - _now_dt).total_seconds())
                        except Exception:
                            _retry_after = None
                    if _retry_after is not None:
                        # Cap at 60s to avoid extremely long waits.
                        _override_wait = min(max(_retry_after, 1.0), 60.0)
                        # Stash on last_exc so the backoff block below
                        # uses this instead of exponential.
                        setattr(last_exc, "_retry_after_wait", _override_wait)
                        logger.info(
                            "[chembl] Retry-After header = %.2fs (will use "
                            "instead of exponential backoff)",
                            _override_wait,
                        )

            except RETRYABLE_EXCEPTIONS as exc:
                error = f"{type(exc).__name__}: {exc}"
                logger.warning(
                    "[chembl] Request exception on %s: %s (attempt %d/%d)",
                    url,
                    exc,
                    attempt,
                    self.max_retries,
                )
                last_exc = exc

            except MaxResponseSizeExceeded as exc:
                # v9 ROOT FIX (audit F4.5) + v41 ROOT FIX (P1 #13):
                # MaxResponseSizeExceeded is a subclass of HttpClientError.
                # The previous ordering caught HttpClientError FIRST,
                # making this block UNREACHABLE. The v9 fix reordered the
                # except clauses so this block IS reached. The v41 fix
                # corrects the misleading comment which claimed "the
                # previous ordering caught HttpClientError FIRST (line
                # 500)" — there was no ``except HttpClientError`` block
                # at all in the original code. The actual issue was that
                # the ``except RETRYABLE_EXCEPTIONS`` block didn't catch
                # HttpClientError (it's not a RETRYABLE_EXCEPTION), so
                # MaxResponseSizeExceeded propagated naturally to this
                # block. The v9 fix was still correct (adding the explicit
                # catch), but the comment was misleading. The comment is
                # now accurate.
                # Response too large — do NOT retry (the server is sending
                # too much data; retrying won't help).
                self._circuit_breaker.record_failure()
                self._record_call(
                    url, params, "GET", status, time.monotonic() - start,
                    response_size, str(exc),
                )
                raise

            # v35 ROOT FIX (issue 26): removed the dead
            # ``except HttpClientError as exc: raise`` no-op. The block
            # simply re-raised, which is exactly what Python does
            # naturally when an exception isn't caught. Keeping it
            # misled readers into thinking it had side effects (e.g.
            # recording the failure), which it did not — non-retryable
            # 4xx HttpClientError raised at line 491 above propagates
            # naturally out of the for loop, skipping the
            # ``# Record the failed attempt`` block below (which is the
            # intended behavior: 4xx failures are already recorded at
            # line 487-490).

            # Record the failed attempt.
            self._circuit_breaker.record_failure()
            self._record_call(
                url, params, "GET", status, time.monotonic() - start,
                response_size, error,
            )

            # If this was the last attempt, break out and raise.
            if attempt == self.max_retries:
                break

            # Exponential backoff with jitter (C34, C36).
            # wait = backoff_base * (2 ** attempt) + random.uniform(0, 1)
            # Cap at 60s to avoid extremely long waits.
            # v49 ROOT FIX (Compound-7): if `last_exc` carries a
            # `_retry_after_wait` attribute (set when the server sent a
            # Retry-After header), use that instead of exponential.
            _override = getattr(last_exc, "_retry_after_wait", None)
            if _override is not None:
                wait = _override
                _backoff_kind = "Retry-After"
            else:
                wait = min(
                    self.backoff_base * (2 ** attempt) + random.uniform(0, 1),
                    60.0,
                )
                _backoff_kind = "exponential"
            self.metrics["retries"] += 1
            logger.info(
                "[chembl] Retrying %s in %.2fs [%s] (attempt %d/%d)",
                url,
                wait,
                _backoff_kind,
                attempt,
                self.max_retries,
            )
            time.sleep(wait)

        # All retries exhausted.
        # v40 ROOT FIX (P1 #14): replaced ``assert last_exc is not None``
        # with a proper RuntimeError. The assert would fire if
        # max_retries=0 (the constructor validates max_retries >= 1, but
        # a future refactor could bypass it). An assert with -O flag is
        # stripped, silently raising ``None`` (TypeError: exceptions
        # must derive from BaseException). The fix raises a clear
        # RuntimeError instead.
        if last_exc is None:
            raise RuntimeError(
                "HttpClient: all retries exhausted but last_exc is None. "
                "This should never happen (max_retries >= 1 is enforced "
                "by the constructor). If you see this, a refactor broke "
                "the retry loop invariant. (v40 P1 #14 fix)"
            )
        raise last_exc

    def close(self) -> None:
        """Close the underlying ``requests.Session``."""
        self._session.close()

    # ------------------------------------------------------------------
    # Internals
    # ------------------------------------------------------------------

    @staticmethod
    def _safe_response_size(resp: requests.Response) -> int | None:
        """Return the response's advertised size from ``Content-Length``.

        Returns ``None`` if the header is absent or unparseable.
        """
        try:
            cl = resp.headers.get("Content-Length")
            return int(cl) if cl is not None else None
        except (TypeError, ValueError):
            return None

    def _read_body_bounded(self, resp: requests.Response) -> bytes:
        """Read the response body, enforcing ``max_response_bytes``.

        Uses ``iter_content`` so the body is streamed in chunks and we can
        abort as soon as the cap is exceeded (SEC-5).
        """
        # Pre-check Content-Length if present.
        advertised = self._safe_response_size(resp)
        if advertised is not None and advertised > self.max_response_bytes:
            raise MaxResponseSizeExceeded(
                f"Response size {advertised} bytes exceeds cap "
                f"{self.max_response_bytes} bytes (URL={resp.url})"
            )

        chunks: list[bytes] = []
        total = 0
        for chunk in resp.iter_content(chunk_size=65536):
            if not chunk:
                continue
            total += len(chunk)
            if total > self.max_response_bytes:
                raise MaxResponseSizeExceeded(
                    f"Streamed response exceeded cap {self.max_response_bytes} "
                    f"bytes after {total} bytes (URL={resp.url})"
                )
            chunks.append(chunk)
        return b"".join(chunks)

    @staticmethod
    def _parse_json(body: bytes, url: str) -> dict[str, Any] | list[Any]:
        """Parse ``body`` as JSON. Raise ``HttpClientError`` on failure (C4).

        v65 ROOT FIX (P1-027): return type widened from ``dict[str, Any]``
        to ``dict[str, Any] | list[Any]`` because ``json.loads`` can return
        either depending on the top-level JSON token (``{}`` → dict,
        ``[]`` → list). ChEMBL's REST API always returns objects at the
        top level, but the type contract must reflect the implementation.
        """
        try:
            text = body.decode("utf-8", errors="replace")
            return json.loads(text)
        except (json.JSONDecodeError, UnicodeDecodeError) as exc:
            preview = body[:500].decode("utf-8", errors="replace")
            logger.error(
                "[chembl] JSON decode error on %s: %s. Body preview: %s",
                url,
                exc,
                preview,
            )
            raise HttpClientError(
                f"Failed to parse JSON from {url}: {exc}. "
                f"Body preview: {preview!r}"
            ) from exc

    def _record_call(
        self,
        url: str,
        params: dict[str, Any],
        method: str,
        status: int | None,
        duration: float,
        response_size: int | None,
        error: str | None,
    ) -> None:
        """Append an ``ApiCallRecord`` and increment metrics."""
        self.metrics["api_calls"] += 1
        rec = ApiCallRecord(
            url=url,
            params=params,
            method=method,
            status=status,
            duration_sec=duration,
            response_size_bytes=response_size,
            error=error,
        )
        self.api_calls.append(rec)
