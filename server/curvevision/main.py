"""ASGI application factory."""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI, Response
from fastapi.middleware.cors import CORSMiddleware

from curvevision import __version__
from curvevision.api.errors import install_error_handlers
from curvevision.api.middleware import install_security_middleware
from curvevision.api.router import api_router
from curvevision.core.config import Settings, get_settings
from curvevision.core.db import dispose_engine
from curvevision.core.logging import configure_logging, get_logger
from curvevision.core.observability import install_middleware, metrics_response

DESCRIPTION = """
CurveVision is open-source annotation and dataset infrastructure for computer vision.

**Annotate manually. Accelerate with AI. Build better datasets. Keep control of your data.**

Authenticate with either a session token (`POST /api/v1/auth/login`) or an API token
(`POST /api/v1/auth/tokens`), passed as `Authorization: Bearer <token>`.
"""


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    settings: Settings = app.state.settings
    configure_logging(settings.log_level, settings.log_format)
    logger = get_logger(__name__)
    logger.info(
        "CurveVision starting",
        extra={
            "version": __version__,
            "environment": settings.environment,
            "storage": settings.storage_backend,
            "job_queue": settings.job_queue_backend,
        },
    )
    # Importing these registers the built-in dataset formats, model providers and
    # background job handlers. Registration at import time keeps the registries a
    # single source of truth rather than something each entrypoint wires up.
    import curvevision.formats
    import curvevision.jobs.tasks
    import curvevision.ml  # noqa: F401

    yield
    await dispose_engine()
    logger.info("CurveVision stopped")


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or get_settings()
    app = FastAPI(
        title="CurveVision",
        version=__version__,
        description=DESCRIPTION,
        lifespan=lifespan,
        docs_url="/api/docs",
        redoc_url="/api/redoc",
        openapi_url=f"{settings.api_prefix}/openapi.json",
        # OpenAPI 3.1 so the schema matches what the SDK and TypeScript client generate from.
        openapi_version="3.1.0",
    )
    app.state.settings = settings

    app.add_middleware(
        CORSMiddleware,
        # An explicit allow-list, never "*": the API accepts credentials.
        allow_origins=list(settings.cors_origins),
        allow_credentials=True,
        allow_methods=["GET", "POST", "PATCH", "PUT", "DELETE", "OPTIONS"],
        allow_headers=["Authorization", "Content-Type", "X-Request-ID", "Idempotency-Key"],
        expose_headers=["X-Request-ID", "X-CurveVision-Warnings"],
    )
    install_security_middleware(app, settings)
    install_middleware(app)
    install_error_handlers(app)
    app.include_router(api_router, prefix=settings.api_prefix)

    if settings.metrics_enabled:

        @app.get("/metrics", include_in_schema=False)
        async def metrics() -> Response:
            return metrics_response()

    @app.get("/", include_in_schema=False)
    async def root() -> dict[str, str]:
        return {
            "name": "CurveVision",
            "version": __version__,
            "docs": "/api/docs",
            "api": settings.api_prefix,
        }

    return app


app = create_app()
