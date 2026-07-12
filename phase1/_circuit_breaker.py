"""Shared circuit breaker implementation for the Drug Repurposing ETL platform.

This module provides :class:`_CircuitBreaker`, a thread-safe circuit breaker
with closed / open / half-open state transitions and a single-probe gate
for the half-open state.

All Phase 1 modules that need a circuit breaker should import from this
single location instead of defining their own copy.

States
------
- **CLOSED** (normal): all requests are allowed.
- **OPEN** (failing): all requests are refused until ``reset_timeout``
  elapses, at which point the breaker transitions to HALF_OPEN.
- **HALF_OPEN** (probing): exactly **one** probe request is allowed.
  Subsequent requests are refused until the probe completes (success ->
  CLOSED, failure -> OPEN).  This single-probe gate prevents a thundering
  herd when the protected service is still recovering.

API
---
The class exposes three equivalent check interfaces so callers can pick
the one that matches their codebase convention:

- ``allow_request() -> bool`` -- returns True if the request should proceed.
- ``is_open() -> bool`` -- returns True if the breaker is open (call should
  be refused).  This is the logical inverse of ``allow_request()`` for
  OPEN/CLOSED states; in HALF_OPEN the semantics differ subtly
  (``is_open`` returns False for the probe, ``allow_request`` returns True).
- ``state`` property -- returns the current state string.

History
-------
Consolidated from five duplicate implementations across the codebase:
  - ``database/connection.py``  (dataclass, UPPERCASE states, ``allow_request``)
  - ``pipelines/base_pipeline.py``  (plain class, lowercase states, ``is_open``)
  - ``pipelines/disgenet_pipeline.py``  (plain class, no probe gate)
  - ``pipelines/_chembl_http_client.py``  (wrapper around base_pipeline's)
  - ``cleaning/__init__.py``  (plain class, ``name`` param, no lock)

The canonical version merges all features: thread-safety via a lock,
``_half_open_probe_in_flight`` single-probe gate (v40 ROOT FIX / P1-A8),
``time.monotonic()`` for monotonic elapsed-time measurement, optional
``name`` attribute for logging, and all three check APIs.
"""

from __future__ import annotations

import logging
import threading
import time

logger = logging.getLogger(__name__)


class _CircuitBreaker:
    """Thread-safe circuit breaker with closed / open / half-open states.

    After ``failure_threshold`` consecutive failures, the breaker opens
    and refuses further calls for ``reset_timeout`` seconds.  After the
    timeout, it enters half-open state: one call is allowed (the probe);
    if it succeeds, the breaker closes; if it fails, the breaker re-opens.

    The half-open single-probe gate (``_half_open_probe_in_flight``)
    ensures that exactly ONE call is allowed in half-open state.
    Subsequent calls are refused until the probe completes via
    ``record_success()`` or ``record_failure()``.  This prevents a
    thundering herd when the protected service is recovering.

    Parameters
    ----------
    failure_threshold : int
        Consecutive failures required to trip the breaker open.
        Must be >= 1.  Default: 5.
    reset_timeout : float
        Seconds the breaker stays open before transitioning to half-open.
        Must be >= 0.  Default: 30.0.
    name : str or None
        Optional human-readable name included in log messages.
        Default: None.
    """

    def __init__(
        self,
        failure_threshold: int = 5,
        reset_timeout: float = 30.0,
        *,
        name: str | None = None,
    ) -> None:
        if failure_threshold < 1:
            raise ValueError(
                f"failure_threshold must be >= 1, got {failure_threshold}"
            )
        if reset_timeout < 0:
            raise ValueError(
                f"reset_timeout must be >= 0, got {reset_timeout}"
            )
        self._failure_threshold: int = int(failure_threshold)
        self._reset_timeout: float = float(reset_timeout)
        self.name: str | None = name
        self._failure_count: int = 0
        self._last_failure_time: float = 0.0
        self._state: str = "closed"  # closed | open | half_open
        self._lock = threading.Lock()
        # Half-open single-probe gate: only ONE call is allowed in
        # half_open state.  Subsequent calls are refused until the
        # probe completes (record_success or record_failure).
        self._half_open_probe_in_flight: bool = False

    # -- Convenience properties ----------------------------------------

    @property
    def failure_threshold(self) -> int:
        """Consecutive failure count that trips the breaker open."""
        return self._failure_threshold

    @failure_threshold.setter
    def failure_threshold(self, value: int) -> None:
        self._failure_threshold = max(1, int(value))

    @property
    def reset_timeout(self) -> float:
        """Seconds the breaker stays open before half-open transition."""
        return self._reset_timeout

    @reset_timeout.setter
    def reset_timeout(self, value: float) -> None:
        self._reset_timeout = max(0.0, float(value))

    @property
    def failure_count(self) -> int:
        """Current consecutive failure count."""
        return self._failure_count

    @failure_count.setter
    def failure_count(self, value: int) -> None:
        self._failure_count = int(value)

    @property
    def last_failure_time(self) -> float:
        """Monotonic timestamp of the most recent failure."""
        return self._last_failure_time

    @last_failure_time.setter
    def last_failure_time(self, value: float) -> None:
        self._last_failure_time = float(value)

    @property
    def state(self) -> str:
        """Current breaker state (``'closed'``, ``'open'``, or ``'half_open'``).

        v89 FORENSIC ROOT FIX (BUG #12 P1 -- pure observation):
          The previous implementation triggered an open -> half_open
          transition when the reset timeout had elapsed. This was a
          SIDE EFFECT on a read-only property -- the same class of bug
          as ``is_open()``. Monitoring code that read ``breaker.state``
          for dashboards inadvertently transitioned the breaker, and
          because the transition did NOT set
          ``_half_open_probe_in_flight`` consistently with
          ``allow_request()``, it could leave the breaker in a
          half-reserved state.
          ROOT FIX: this property is now PURE OBSERVATION. It returns
          the current state string without any transition. The open ->
          half_open transition is performed EXCLUSIVELY by
          ``allow_request()``.
        """
        with self._lock:
            return self._state

    @state.setter
    def state(self, value: str) -> None:
        """Set the breaker state directly (use with caution).

        If setting to ``'open'``, also sets ``_last_failure_time`` to the
        current monotonic clock so that the recovery timeout is measured
        from this moment.  This preserves the behaviour expected by tests
        that manually set the state and then check recovery transitions.
        """
        with self._lock:
            self._state = value
            if value == "open" and self._last_failure_time == 0.0:
                self._last_failure_time = time.monotonic()

    # Backward-compat UPPERCASE aliases consumed by connection.py tests.
    @property
    def recovery_timeout(self) -> float:
        """Alias for ``reset_timeout`` (backward compat with connection.py)."""
        return self._reset_timeout

    @recovery_timeout.setter
    def recovery_timeout(self, value: float) -> None:
        self._reset_timeout = max(0.0, float(value))

    # -- Core state transitions ----------------------------------------

    def record_success(self) -> None:
        """Record a successful operation -- closes the breaker."""
        with self._lock:
            self._failure_count = 0
            self._state = "closed"
            self._half_open_probe_in_flight = False

    def record_failure(self) -> None:
        """Record a failed operation -- may open the breaker.

        v89 FORENSIC ROOT FIX (BUG #13 P1 -- half-open probe flag not cleared
          on threshold-path re-open):
          The previous code checked ``if self._state == "half_open"`` AFTER
          the ``if self._failure_count >= self._failure_threshold`` block.
          When a half-open probe failed, ``_failure_count`` (which was NOT
          reset on entering half_open) was already >= threshold, so the
          threshold block set ``self._state = "open"`` FIRST. The subsequent
          ``if self._state == "half_open"`` check then evaluated False
          (state was now "open"), so ``_half_open_probe_in_flight`` was
          NOT cleared. The flag stayed True, blocking all future probes
          (combined with Bug #12's ``is_open()`` mutation, this could
          stick the breaker open forever).
          ROOT FIX: check for half_open FIRST and handle it exclusively
          (clear the flag, set state to open, log, and return). The
          threshold check only fires when NOT in half_open. This guarantees
          the flag is ALWAYS cleared when leaving half_open, regardless of
          which path triggered the transition.
        """
        with self._lock:
            self._failure_count += 1
            self._last_failure_time = time.monotonic()
            if self._state == "half_open":
                # A failed probe in half_open trips the breaker back to
                # open. ALWAYS clear the probe-in-flight flag when leaving
                # half_open (v89 BUG #13 -- was not cleared on the
                # threshold-path re-open, leaving the breaker stuck).
                self._state = "open"
                self._half_open_probe_in_flight = False
                label = self.name or "circuit_breaker"
                logger.warning(
                    "[%s] Circuit breaker re-OPENED after failed half-open "
                    "probe (failure_count=%d, threshold=%d, reset_timeout=%.1fs)",
                    label,
                    self._failure_count,
                    self._failure_threshold,
                    self._reset_timeout,
                )
                return
            if self._failure_count >= self._failure_threshold:
                if self._state != "open":
                    label = self.name or "circuit_breaker"
                    logger.warning(
                        "[%s] Circuit breaker OPENED after %d consecutive "
                        "failures (threshold=%d, reset_timeout=%.1fs)",
                        label,
                        self._failure_count,
                        self._failure_threshold,
                        self._reset_timeout,
                    )
                self._state = "open"

    # -- Check APIs ----------------------------------------------------

    def allow_request(self) -> bool:
        """Check if a request should be allowed through.

        Returns
        -------
        bool
            True if the request should proceed, False if blocked.

        In half_open state, exactly ONE probe request is allowed.
        Subsequent requests are refused until the probe completes
        (record_success -> closed, record_failure -> open).
        """
        with self._lock:
            current_state = self._state
            if current_state == "open":
                # Check if recovery timeout has elapsed.
                if time.monotonic() - self._last_failure_time > self._reset_timeout:
                    self._state = "half_open"
                    self._half_open_probe_in_flight = False
                    current_state = "half_open"
                else:
                    return False
            if current_state == "closed":
                return True
            # half_open: allow exactly ONE probe.
            if self._half_open_probe_in_flight:
                return False
            self._half_open_probe_in_flight = True
            return True

    def is_open(self) -> bool:
        """Return True if the breaker is open and calls should be refused.

        v89 FORENSIC ROOT FIX (BUG #12 P1 -- is_open() mutated state):
          The previous implementation MUTATED state when called from the
          "open" state with an elapsed reset timeout: it transitioned to
          "half_open" AND set ``_half_open_probe_in_flight = True``. This
          RESERVED the probe slot, so a subsequent ``allow_request()``
          call (which sets the flag to False before the probe, then True
          to reserve) saw the flag already True and returned False --
          refusing the actual probe. The breaker appeared stuck open even
          after the reset timeout. Any monitoring/dashboard code that
          called ``is_open()`` inadvertently broke the subsequent
          ``allow_request()`` call.
          ROOT FIX: make ``is_open()`` a PURE OBSERVATION method. It does
          NOT transition state, does NOT reserve probe slots. The
          open -> half_open transition is performed EXCLUSIVELY by
          ``allow_request()``. Callers who want to actually acquire a
          probe slot MUST call ``allow_request()``.

        Semantics
        ---------
        - state == "closed": return False (not open, calls allowed).
        - state == "open": return True (open, calls refused). Note: even
          if the reset timeout has elapsed, we return True because the
          state has not yet transitioned. ``allow_request()`` will
          transition to half_open on the next call.
        - state == "half_open": return ``_half_open_probe_in_flight``
          (True if a probe is already in flight and additional calls
          should be refused; False if no probe is in flight and the next
          ``allow_request()`` call will acquire the probe).
        """
        with self._lock:
            if self._state == "open":
                # Pure observation -- do NOT transition to half_open here.
                # allow_request() performs the transition when a caller
                # actually wants to acquire a probe slot.
                return True
            if self._state == "half_open":
                # In half_open, "open" means "refuse additional calls" =
                # a probe is already in flight.
                return self._half_open_probe_in_flight
            # closed
            return False
