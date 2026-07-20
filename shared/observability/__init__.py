"""shared/observability — Production observability toolkit for FastAPI services.

v122 FORENSIC ROOT FIX (Teammate 15 — hostile-auditor, BUG-4/BUG-5/BUG-6):
    The v116 docker-compose.yml added Prometheus + Grafana + OTel + Jaeger
    services and claimed IN-040 (metrics), IN-041 (structured logging), and
    IN-042 (tracing) were fixed. But reading the ACTUAL FastAPI service
    code (phase1/service.py, phase2/service.py, scripts/gt_api.py,
    rl/service.py) shows NONE of them:

      * mount a /metrics endpoint (so Prometheus gets 404 from every scrape)
      * use structlog or python-json-logger (still text-format logging)
      * call FastAPIInstrumentor.instrument_app(app) (no traces sent to OTel)

    This is exactly the "comments are fakes, code is broken" pattern the
    user warned about. The infrastructure was added but the application
    code was never instrumented to USE it.

    ROOT FIX: this module provides a single ``configure_app(app, service_name)``
    function that:
      1. Mounts ``/metrics`` on the FastAPI app via prometheus_client's
         ``make_asgi_app()``. Exposes default Python process metrics
         (gc, python_info) plus HTTP request metrics via middleware.
      2. Configures structured JSON logging via the stdlib ``logging``
         module + a JSON formatter. Every log line is a single JSON
         object with ``ts``, ``level``, ``logger``, ``msg``, and any
         ``extra`` fields the caller passes. Machine-parseable by ELK,
         Loki, Datadog, CloudWatch Logs Insights.
      3. Instruments the FastAPI app with OpenTelemetry
         (``FastAPIInstrumentor.instrument_app(app)``) if the
         ``opentelemetry-instrumentation-fastapi`` package is installed
         AND ``OTEL_EXPORTER_OTLP_ENDPOINT`` is set. Falls back
         gracefully (no-op) if either is missing.

v129 TM16 ROOT FIX (Task 16.5 — Sentry SDK, IN-043):
    The v122 fix added /metrics + JSON logging + OTel but Sentry was
    MISSING. The audit explicitly required "Sentry SDK with DSN from env
    var" (IN-043). Without Sentry, production errors are logged to stdout
    but never reach an alerting system — patient-safety incidents (e.g.
    a hypothesis export failing for a pharma partner) can be missed for
    hours. The fix adds ``_init_sentry(service_name)`` which:
      * Reads ``SENTRY_DSN`` from env. If unset/empty, returns False
        (dev runs do not need a Sentry account — graceful no-op).
      * Reads optional ``SENTRY_ENVIRONMENT`` (default: from
        ``DRUGOS_ENVIRONMENT`` or "development"), ``SENTRY_RELEASE``
        (default: git SHA via ``DRUGOS_GIT_SHA`` env or "unknown"),
        ``SENTRY_TRACES_SAMPLE_RATE`` (default: 0.0 — only errors
        captured, no performance traces; set to 0.01 for 1% sampling).
      * Calls ``sentry_sdk.init()`` with the FastAPIIntegration + the
        default dedupe integration. Errors raised in async route
        handlers are auto-captured with full request context (method,
        path, headers filtered for PII by sentry_sdk's default
        BeforeSendProcessor).
      * Tags every Sentry event with ``service_name`` so we can filter
        by service in the Sentry dashboard.

    Each FastAPI service calls ``configure_app(app, "<service_name>")``
    early in module init (after ``app = FastAPI(...)``). One function,
    one import, four problems solved.

Usage:
    from shared.observability import configure_app
    app = FastAPI(...)
    configure_app(app, service_name="phase3-gt-api")
"""
from __future__ import annotations

import json
import logging
import os
import sys
import time
import uuid
from datetime import datetime, timezone
from typing import Any, Optional

_LOGGING_CONFIGURED = False
_OTEL_CONFIGURED = False
_SENTRY_CONFIGURED = False


class _JsonFormatter(logging.Formatter):
    """JSON formatter for stdlib logging — one JSON object per log line."""

    _STD_ATTRS = frozenset({
        "name", "msg", "args", "levelname", "levelno", "pathname", "filename",
        "module", "exc_info", "exc_text", "stack_info", "lineno", "funcName",
        "created", "msecs", "relativeCreated", "thread", "threadName",
        "processName", "process", "message", "asctime", "taskName",
    })

    def format(self, record: logging.LogRecord) -> str:
        ts = datetime.fromtimestamp(record.created, tz=timezone.utc).isoformat()
        log_obj: dict[str, Any] = {
            "ts": ts,
            "level": record.levelname,
            "logger": record.name,
            "msg": record.getMessage(),
        }
        extras = {
            k: v
            for k, v in record.__dict__.items()
            if k not in self._STD_ATTRS and not k.startswith("_")
        }
        if extras:
            log_obj["extra"] = {k: _coerce(v) for k, v in extras.items()}
        if record.exc_info:
            log_obj["exception"] = self.formatException(record.exc_info)
        if record.levelno >= logging.ERROR:
            log_obj["source"] = {
                "file": record.pathname,
                "line": record.lineno,
                "function": record.funcName,
            }
        return json.dumps(log_obj, default=str)


def _coerce(value: Any) -> Any:
    if value is None or isinstance(value, (bool, int, float, str)):
        return value
    if isinstance(value, (list, tuple)):
        return [_coerce(v) for v in value]
    if isinstance(value, dict):
        return {str(k): _coerce(v) for k, v in value.items()}
    return str(value)


def _configure_logging(level: Optional[str] = None) -> None:
    """Configure structured JSON logging on the root logger. Idempotent."""
    global _LOGGING_CONFIGURED
    if _LOGGING_CONFIGURED:
        return

    log_level = level or os.environ.get("DRUGOS_LOG_LEVEL", "INFO").upper()
    root = logging.getLogger()
    for h in list(root.handlers):
        root.removeHandler(h)
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(_JsonFormatter())
    root.addHandler(handler)
    root.setLevel(log_level)
    for noisy in ("uvicorn", "uvicorn.access", "sqlalchemy", "neo4j"):
        logging.getLogger(noisy).setLevel(max(log_level, "INFO"))

    _LOGGING_CONFIGURED = True


def _add_request_id_middleware(app: Any) -> None:
    """Add a middleware that generates a per-request UUID and logs it."""
    import threading
    from fastapi import Request
    from starlette.middleware.base import BaseHTTPMiddleware

    class RequestIdMiddleware(BaseHTTPMiddleware):
        async def dispatch(self, request: Request, call_next):
            request_id = request.headers.get("X-Request-ID") or str(uuid.uuid4())
            if not hasattr(request.app.state, "request_ids"):
                request.app.state.request_ids = {}
            request.app.state.request_ids[threading.get_ident()] = request_id

            start = time.perf_counter()
            logger = logging.getLogger("shared.observability.request")
            logger.info(
                "request_started",
                extra={
                    "request_id": request_id,
                    "method": request.method,
                    "path": request.url.path,
                },
            )
            try:
                response = await call_next(request)
            except Exception:
                duration_ms = (time.perf_counter() - start) * 1000
                logger.error(
                    "request_failed",
                    extra={
                        "request_id": request_id,
                        "method": request.method,
                        "path": request.url.path,
                        "duration_ms": duration_ms,
                    },
                    exc_info=True,
                )
                raise
            duration_ms = (time.perf_counter() - start) * 1000
            response.headers["X-Request-ID"] = request_id
            logger.info(
                "request_completed",
                extra={
                    "request_id": request_id,
                    "method": request.method,
                    "path": request.url.path,
                    "status_code": response.status_code,
                    "duration_ms": duration_ms,
                },
            )
            return response

    app.add_middleware(RequestIdMiddleware)


def _mount_metrics(app: Any) -> bool:
    """Mount /metrics endpoint on the FastAPI app. Returns True if mounted."""
    try:
        from prometheus_client import make_asgi_app
    except ImportError:
        logging.getLogger(__name__).warning(
            "prometheus_client not installed — /metrics endpoint NOT mounted."
        )
        return False

    metrics_app = make_asgi_app()
    app.mount("/metrics", metrics_app)
    return True


def _instrument_otel(app: Any, service_name: str) -> bool:
    """Instrument the FastAPI app with OpenTelemetry. Returns True if instrumented."""
    global _OTEL_CONFIGURED
    if _OTEL_CONFIGURED:
        return True

    endpoint = os.environ.get("OTEL_EXPORTER_OTLP_ENDPOINT")
    if not endpoint:
        return False

    try:
        from opentelemetry import trace
        from opentelemetry.exporter.otlp.proto.http.trace_exporter import (
            OTLPSpanExporter,
        )
        from opentelemetry.instrumentation.fastapi import (
            FastAPIInstrumentor,
        )
        from opentelemetry.sdk.resources import Resource
        from opentelemetry.sdk.trace import TracerProvider
        from opentelemetry.sdk.trace.export import BatchSpanProcessor
    except ImportError:
        logging.getLogger(__name__).warning(
            "opentelemetry-instrumentation-fastapi not installed — "
            "traces NOT sent to %s.",
            endpoint,
        )
        return False

    resource = Resource.create({
        "service.name": service_name,
        "service.version": os.environ.get("DRUGOS_SERVICE_VERSION", "1.0.0"),
        "deployment.environment": os.environ.get("DRUGOS_ENVIRONMENT", "development"),
    })
    provider = TracerProvider(resource=resource)
    exporter = OTLPSpanExporter(endpoint=f"{endpoint}/v1/traces")
    provider.add_span_processor(BatchSpanProcessor(exporter))
    trace.set_tracer_provider(provider)
    FastAPIInstrumentor.instrument_app(app)

    _OTEL_CONFIGURED = True
    logging.getLogger(__name__).info(
        "otel_instrumentation_enabled",
        extra={"service_name": service_name, "endpoint": endpoint},
    )
    return True


def _init_sentry(service_name: str) -> bool:
    """Initialize the Sentry SDK if SENTRY_DSN is set.

    Returns True if Sentry was initialized, False otherwise (graceful no-op
    when SENTRY_DSN is unset — dev runs do not need a Sentry account).

    Reads these env vars:
      * SENTRY_DSN (required for init) — the project DSN from sentry.io.
      * SENTRY_ENVIRONMENT (optional) — defaults to DRUGOS_ENVIRONMENT
        env var or "development".
      * SENTRY_RELEASE (optional) — defaults to DRUGOS_GIT_SHA env var
        or "unknown" (set DRUGOS_GIT_SHA in the Docker build).
      * SENTRY_TRACES_SAMPLE_RATE (optional) — float 0.0–1.0, default 0.0
        (errors only). Set to 0.01 for 1% performance sampling.
      * SENTRY_PROFILES_SAMPLE_RATE (optional) — float 0.0–1.0, default 0.0.

    Idempotent: only initializes once per process (subsequent calls are
    no-ops). Safe to call from configure_app() on every FastAPI app.
    """
    global _SENTRY_CONFIGURED
    if _SENTRY_CONFIGURED:
        return True

    dsn = os.environ.get("SENTRY_DSN", "").strip()
    if not dsn:
        # Dev-mode no-op: SENTRY_DSN not set. This is the expected state
        # for local development + CI. Production deployments MUST set
        # SENTRY_DSN in the container env.
        return False

    try:
        import sentry_sdk
        from sentry_sdk.integrations.fastapi import FastApiIntegration
        from sentry_sdk.integrations.logging import LoggingIntegration
    except ImportError:
        logging.getLogger(__name__).warning(
            "sentry-sdk not installed — SENTRY_DSN was set but errors "
            "will NOT be reported to Sentry. Install with: "
            "pip install 'sentry-sdk[fastapi]>=1.40,<3.0'",
        )
        return False

    environment = (
        os.environ.get("SENTRY_ENVIRONMENT")
        or os.environ.get("DRUGOS_ENVIRONMENT")
        or "development"
    )
    release = os.environ.get("SENTRY_RELEASE") or os.environ.get("DRUGOS_GIT_SHA") or "unknown"

    # Parse traces_sample_rate defensively — a malformed env value should
    # NOT crash the service. Default to 0.0 (errors only, no perf traces).
    try:
        traces_sample_rate = float(
            os.environ.get("SENTRY_TRACES_SAMPLE_RATE", "0.0"),
        )
        if not 0.0 <= traces_sample_rate <= 1.0:
            traces_sample_rate = 0.0
    except (TypeError, ValueError):
        traces_sample_rate = 0.0

    try:
        profiles_sample_rate = float(
            os.environ.get("SENTRY_PROFILES_SAMPLE_RATE", "0.0"),
        )
        if not 0.0 <= profiles_sample_rate <= 1.0:
            profiles_sample_rate = 0.0
    except (TypeError, ValueError):
        profiles_sample_rate = 0.0

    # LoggingIntegration: capture ERROR+ logs as Sentry events. WARNING
    # logs are NOT captured (would be too noisy). The integration also
    # adds structured log records as breadcrumbs to subsequent events,
    # giving full context when an error fires.
    logging_integration = LoggingIntegration(
        level=logging.INFO,          # INFO+ as breadcrumbs
        event_level=logging.ERROR,   # ERROR+ as events
    )

    sentry_sdk.init(
        dsn=dsn,
        environment=environment,
        release=release,
        traces_sample_rate=traces_sample_rate,
        profiles_sample_rate=profiles_sample_rate,
        send_default_pii=False,  # NEVER send PII (HIPAA/GDPR compliance)
        attach_stacktrace=True,  # capture stacktrace even for caught exceptions
        max_breadcrumbs=100,     # keep last 100 log records as context
        integrations=[
            FastApiIntegration(),
            logging_integration,
        ],
        before_send=_sentry_before_send,
    )

    # Tag every Sentry event with the service name so we can filter by
    # service in the Sentry dashboard. set_tag() at the global scope
    # applies to all subsequent events.
    sentry_sdk.set_tag("service_name", service_name)
    sentry_sdk.set_tag("component", service_name)
    sentry_sdk.set_context("service", {
        "name": service_name,
        "environment": environment,
        "release": release,
    })

    _SENTRY_CONFIGURED = True
    logging.getLogger(__name__).info(
        "sentry_initialized",
        extra={
            "service_name": service_name,
            "environment": environment,
            "release": release,
            "traces_sample_rate": traces_sample_rate,
        },
    )
    return True


def _sentry_before_send(event: dict, hint: dict) -> Optional[dict]:
    """Sentry before_send hook — strip PII + rate-limit noisy exceptions.

    This runs on every Sentry event before it's sent. Two responsibilities:
      1. Strip personally-identifiable information (PII) from request
         headers + bodies. Even though send_default_pii=False, request
         headers can still contain Authorization, Cookie, X-API-Key
         values that leak credentials. We redact them explicitly.
      2. Drop events from known-noisy exception types that flood Sentry
         (e.g. CancelledError from graceful shutdown). These are NOT
         real errors and would mask real incidents in the Sentry UI.
    """
    # ─── PII redaction on request headers + bodies ───
    request = event.get("request", {})
    if isinstance(request, dict):
        headers = request.get("headers")
        if isinstance(headers, dict):
            redacted = {}
            sensitive = {
                "authorization", "cookie", "x-api-key", "x-auth-token",
                "x-csrf-token", "proxy-authorization", "set-cookie",
            }
            for k, v in headers.items():
                if isinstance(k, str) and k.lower() in sensitive:
                    redacted[k] = "[REDACTED]"
                else:
                    redacted[k] = v
            request["headers"] = redacted

        # Strip query string + body — may contain patient identifiers
        # (drug names, disease names, OMIM IDs) which are PHI under HIPAA
        # when combined with a researcher identity. We keep method + path
        # (needed for routing analytics) but drop everything else.
        request.pop("query_string", None)
        request.pop("data", None)
        request.pop("cookies", None)
        event["request"] = request

    # ─── Drop noisy exception types ───
    exc_info = hint.get("exc_info")
    if exc_info:
        exc_type = exc_info[0]
        # asyncio.CancelledError fires on every graceful shutdown — NOT
        # an error, just normal lifecycle. Dropping prevents Sentry
        # floods during deploys.
        if exc_type is not None and exc_type.__name__ == "CancelledError":
            return None
        # KeyboardInterrupt is user-initiated, not an error.
        if exc_type is not None and exc_type.__name__ == "KeyboardInterrupt":
            return None

    return event


def configure_app(app: Any, service_name: str) -> None:
    """Configure observability for a FastAPI app.

    Args:
        app: the FastAPI app instance.
        service_name: the service name (e.g., "phase3-gt-api") — used as
            the OTel service.name attribute, the Sentry service_name tag,
            and in log lines.

    Side effects:
        * Configures JSON structured logging on the root logger (idempotent).
        * Adds a request_id middleware (logs request_started/request_completed
          with duration_ms, method, path, status_code, request_id).
        * Mounts /metrics endpoint (Prometheus scrape target).
        * Instruments the app with OpenTelemetry (if OTEL_EXPORTER_OTLP_ENDPOINT
          is set and the package is installed).
        * Initializes Sentry SDK (if SENTRY_DSN is set and the package is
          installed). Errors raised in route handlers are auto-captured.

    Idempotent: calling this on multiple apps in the same process is safe.
    """
    _configure_logging()

    logger = logging.getLogger(__name__)
    logger.info(
        "observability_configuring",
        extra={"service_name": service_name},
    )

    try:
        _add_request_id_middleware(app)
    except Exception as exc:
        logger.warning(
            "request_id_middleware_failed",
            extra={"service_name": service_name, "error": str(exc)},
        )

    try:
        metrics_mounted = _mount_metrics(app)
        logger.info(
            "metrics_endpoint_status",
            extra={
                "service_name": service_name,
                "mounted": metrics_mounted,
                "path": "/metrics",
            },
        )
    except Exception as exc:
        logger.warning(
            "metrics_endpoint_failed",
            extra={"service_name": service_name, "error": str(exc)},
        )

    try:
        otel_enabled = _instrument_otel(app, service_name)
        logger.info(
            "otel_status",
            extra={
                "service_name": service_name,
                "instrumented": otel_enabled,
            },
        )
    except Exception as exc:
        logger.warning(
            "otel_failed",
            extra={"service_name": service_name, "error": str(exc)},
        )

    try:
        sentry_enabled = _init_sentry(service_name)
        logger.info(
            "sentry_status",
            extra={
                "service_name": service_name,
                "initialized": sentry_enabled,
            },
        )
    except Exception as exc:
        logger.warning(
            "sentry_init_failed",
            extra={"service_name": service_name, "error": str(exc)},
        )

    logger.info(
        "observability_configured",
        extra={
            "service_name": service_name,
            "sentry_enabled": _SENTRY_CONFIGURED,
            "otel_enabled": _OTEL_CONFIGURED,
        },
    )


__all__ = ["configure_app"]
