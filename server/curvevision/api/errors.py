"""Error handlers: everything leaves as RFC 9457 ``application/problem+json``."""

from __future__ import annotations

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

from curvevision.core.errors import CurveVisionError
from curvevision.core.logging import get_logger

logger = get_logger(__name__)

PROBLEM_JSON = "application/problem+json"


def install_error_handlers(app: FastAPI) -> None:
    @app.exception_handler(CurveVisionError)
    async def handle_domain_error(request: Request, exc: CurveVisionError) -> JSONResponse:
        return JSONResponse(
            status_code=exc.status_code,
            content=exc.to_problem(instance=str(request.url.path)),
            media_type=PROBLEM_JSON,
        )

    @app.exception_handler(RequestValidationError)
    async def handle_validation_error(
        request: Request, exc: RequestValidationError
    ) -> JSONResponse:
        return JSONResponse(
            status_code=422,
            content={
                "type": "https://curvevision.dev/errors/validation_error",
                "title": "Request validation failed",
                "status": 422,
                "detail": "One or more fields are invalid",
                "instance": str(request.url.path),
                # jsonable via FastAPI's own encoder: errors may carry non-JSON values.
                "errors": [
                    {
                        "location": list(error.get("loc", [])),
                        "message": error.get("msg", ""),
                        "type": error.get("type", ""),
                    }
                    for error in exc.errors()
                ],
            },
            media_type=PROBLEM_JSON,
        )

    @app.exception_handler(StarletteHTTPException)
    async def handle_http_error(request: Request, exc: StarletteHTTPException) -> JSONResponse:
        return JSONResponse(
            status_code=exc.status_code,
            content={
                "type": "https://curvevision.dev/errors/http_error",
                "title": str(exc.detail),
                "status": exc.status_code,
                "detail": str(exc.detail),
                "instance": str(request.url.path),
            },
            media_type=PROBLEM_JSON,
            headers=getattr(exc, "headers", None),
        )

    @app.exception_handler(Exception)
    async def handle_unexpected(request: Request, exc: Exception) -> JSONResponse:
        # The detail is deliberately generic: internal messages leak implementation detail
        # to unauthenticated callers. The real error goes to the structured log.
        logger.exception(
            "unhandled exception", extra={"path": request.url.path, "method": request.method}
        )
        return JSONResponse(
            status_code=500,
            content={
                "type": "https://curvevision.dev/errors/internal_error",
                "title": "Internal server error",
                "status": 500,
                "detail": "An unexpected error occurred. The incident has been logged.",
                "instance": str(request.url.path),
            },
            media_type=PROBLEM_JSON,
        )
