"""ASGI application factory."""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, Response
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from curvevision import __version__
from curvevision.api.errors import install_error_handlers
from curvevision.api.middleware import install_security_middleware
from curvevision.api.router import api_router
from curvevision.core.config import Settings, configure_settings, get_settings
from curvevision.core.db import configure_engine, dispose_engine
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
    # Bind the engine *and* the process-wide configuration to this app's settings, so an
    # app built with an explicit Settings object does not silently talk to whatever the
    # environment names -- neither in a request handler nor in a background job.
    configure_settings(settings)
    configure_engine(settings)
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
    configure_settings(None)
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

    if not install_web_app(app, settings):

        @app.get("/", include_in_schema=False)
        async def root() -> dict[str, str]:
            return {
                "name": "CurveVision",
                "version": __version__,
                "docs": "/api/docs",
                "api": settings.api_prefix,
            }

    return app


def install_web_app(app: FastAPI, settings: Settings) -> bool:
    """Serve a built copy of the web application from this process, if one is configured.

    The desktop build turns the whole product into a single executable this way, and it
    also makes the editor same-origin with the API, which removes CORS from the picture
    entirely. A container deployment leaves `web_root` unset and puts a web server in
    front, which is better at static files than we will ever be.

    Returns whether anything was mounted.
    """
    if not settings.web_root:
        return False

    root = Path(settings.web_root).resolve()
    index = root / "index.html"
    if not index.is_file():
        get_logger(__name__).warning(
            "web_root is set but has no index.html; serving the API only",
            extra={"web_root": str(root)},
        )
        return False

    # Vite emits hashed filenames here, so these can be cached hard; index.html cannot.
    assets = root / "assets"
    if assets.is_dir():
        app.mount("/assets", StaticFiles(directory=assets), name="assets")

    @app.get("/{path:path}", include_in_schema=False)
    async def spa(path: str) -> Response:
        """Serve a file if it exists, otherwise the app shell.

        The editor uses client-side routing, so a deep link like `/jobs/<id>` has no file
        behind it and must still load the application rather than 404.
        """
        candidate = (root / path).resolve()
        # `..` in the path resolves before this check, so it cannot escape the bundle.
        if path and candidate.is_relative_to(root) and candidate.is_file():
            return FileResponse(candidate)
        return FileResponse(index, headers={"Cache-Control": "no-cache"})

    return True


app = create_app()
