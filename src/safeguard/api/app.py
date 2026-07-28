"""Application factory.

A factory rather than a module-level ``app`` object so that tests can build an
isolated instance with injected dependencies, and so nothing connects to a
broker as an import side effect.
"""

from __future__ import annotations

import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI

from safeguard import __version__
from safeguard.api.routes import router
from safeguard.config import Settings, get_settings
from safeguard.core.pipeline import Pipeline, build_pipeline
from safeguard.observability.telemetry import configure_logging, instrument_app

logger = logging.getLogger(__name__)

DESCRIPTION = """
Real-time content safety decisioning.

`POST /v1/moderate` runs a four-stage pipeline — feature enrichment,
deterministic rules, probabilistic classification, policy resolution — and
returns an auditable verdict of **allow**, **review**, or **block** together
with every signal that produced it.

See `/v1/policy` for the thresholds and detectors currently in force.
""".strip()


def create_app(
    settings: Settings | None = None,
    *,
    pipeline: Pipeline | None = None,
) -> FastAPI:
    """Build the ASGI application."""
    settings = settings or get_settings()
    configure_logging(settings)

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        app.state.settings = settings
        app.state.pipeline = pipeline or build_pipeline(settings)
        await app.state.pipeline.startup()
        logger.info(
            "safeguard %s ready (env=%s, policy=%s, shadow=%s)",
            __version__,
            settings.environment,
            settings.policy_version,
            settings.shadow_mode,
        )
        try:
            yield
        finally:
            await app.state.pipeline.shutdown()

    app = FastAPI(
        title="SafeGuard",
        description=DESCRIPTION,
        version=__version__,
        lifespan=lifespan,
        # Interactive docs are genuinely useful and genuinely a surface. Off in
        # production; enabled everywhere else.
        docs_url=None if settings.is_production else "/docs",
        redoc_url=None,
        openapi_url=None if settings.is_production else "/openapi.json",
    )

    app.include_router(router)
    instrument_app(app, settings)
    return app
