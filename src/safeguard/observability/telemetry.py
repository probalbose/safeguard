"""Observability.

Logs are structured as JSON in staging and production, and human-readable in
development. The reason is not aesthetics: a moderation decision is an audit
record, and audit records that require a regex to parse are audit records that
will not be queried when it matters.

Tracing is optional and wired only when an OTLP endpoint is configured, so the
dependency is not forced on anyone running the platform locally.
"""

from __future__ import annotations

import json
import logging
import sys
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any

from safeguard.config import Settings

if TYPE_CHECKING:  # pragma: no cover
    from fastapi import FastAPI

logger = logging.getLogger(__name__)

#: Attributes present on every LogRecord; anything else was added by the caller
#: and belongs in the structured payload.
_STANDARD_ATTRS = frozenset(logging.LogRecord("", 0, "", 0, "", (), None).__dict__) | {
    "message",
    "asctime",
    "taskName",
}


class JsonFormatter(logging.Formatter):
    """Renders records as one JSON object per line."""

    def format(self, record: logging.LogRecord) -> str:
        payload: dict[str, Any] = {
            "ts": datetime.fromtimestamp(record.created, UTC).isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
        }
        if record.exc_info:
            payload["exception"] = self.formatException(record.exc_info)

        extras = {k: v for k, v in record.__dict__.items() if k not in _STANDARD_ATTRS}
        if extras:
            payload["context"] = extras

        return json.dumps(payload, default=str)


def configure_logging(settings: Settings) -> None:
    """Install the root log handler. Idempotent."""
    root = logging.getLogger()
    root.setLevel(settings.log_level.upper())

    for existing in list(root.handlers):
        root.removeHandler(existing)

    handler = logging.StreamHandler(sys.stdout)
    if settings.environment == "development":
        handler.setFormatter(logging.Formatter("%(levelname)-8s %(name)s: %(message)s"))
    else:
        handler.setFormatter(JsonFormatter())
    root.addHandler(handler)


def instrument_app(app: FastAPI, settings: Settings) -> None:
    """Attach OpenTelemetry tracing when an OTLP endpoint is configured.

    A no-op otherwise, and a logged warning rather than a failure if the otel
    extra is missing. Missing telemetry should never prevent a service from
    starting; it should be visible that it is missing.
    """
    if not settings.otlp_endpoint:
        return

    try:
        from opentelemetry import trace
        from opentelemetry.exporter.otlp.proto.grpc.trace_exporter import OTLPSpanExporter
        from opentelemetry.instrumentation.fastapi import FastAPIInstrumentor
        from opentelemetry.sdk.resources import Resource
        from opentelemetry.sdk.trace import TracerProvider
        from opentelemetry.sdk.trace.export import BatchSpanProcessor
    except ImportError:
        logger.warning("otel extra not installed; tracing disabled")
        return

    provider = TracerProvider(resource=Resource.create({"service.name": settings.service_name}))
    provider.add_span_processor(
        BatchSpanProcessor(OTLPSpanExporter(endpoint=settings.otlp_endpoint))
    )
    trace.set_tracer_provider(provider)
    FastAPIInstrumentor.instrument_app(app)
    logger.info("tracing enabled, exporting to %s", settings.otlp_endpoint)
