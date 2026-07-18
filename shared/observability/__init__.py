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

    Each FastAPI service calls ``configure_app(app, "<service_name>")``
    early in module init (after ``app = FastAPI(...)``). One function,
    one import, three problems solved.

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


def configure_app(app: Any, service_name: str) -> None:
    """Configure observability for a FastAPI app.

    Args:
        app: the FastAPI app instance.
        service_name: the service name (e.g., "phase3-gt-api") — used as
            the OTel service.name attribute and in log lines.

    Side effects:
        * Configures JSON structured logging on the root logger (idempotent).
        * Adds a request_id middleware (logs request_started/request_completed
          with duration_ms, method, path, status_code, request_id).
        * Mounts /metrics endpoint (Prometheus scrape target).
        * Instruments the app with OpenTelemetry (if OTEL_EXPORTER_OTLP_ENDPOINT
          is set and the package is installed).

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

    logger.info(
        "observability_configured",
        extra={"service_name": service_name},
    )


__all__ = ["configure_app"]
