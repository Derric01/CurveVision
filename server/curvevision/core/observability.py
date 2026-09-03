"""Request instrumentation: request ids, access logs, Prometheus metrics.

OpenTelemetry tracing is *Planned*; this module is where it will attach.
"""

from __future__ import annotations

import time
import uuid
from collections.abc import Awaitable, Callable

from fastapi import FastAPI, Request, Response
from prometheus_client import CONTENT_TYPE_LATEST, Counter, Histogram, generate_latest

from curvevision.core.logging import get_logger, principal_id_var, request_id_var

logger = get_logger("curvevision.access")

REQUEST_COUNT = Counter(
    "curvevision_http_requests_total",
    "HTTP requests handled",
    ["method", "route", "status"],
)
REQUEST_LATENCY = Histogram(
    "curvevision_http_request_duration_seconds",
    "HTTP request latency",
    ["method", "route"],
    buckets=(0.005, 0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1.0, 2.5, 5.0, 10.0),
)
ANNOTATION_WRITES = Counter(
    "curvevision_annotation_writes_total",
    "Annotation objects written",
    ["kind", "operation"],
)
BACKGROUND_TASKS = Counter(
    "curvevision_background_tasks_total",
    "Background tasks by terminal state",
    ["kind", "state"],
)


def _route_template(request: Request) -> str:
    """Use the route pattern, not the concrete path.

    Labelling metrics with raw paths would create one time series per UUID -- a classic way
    to melt a Prometheus instance.
    """
    route = request.scope.get("route")
    return getattr(route, "path", request.url.path)


def install_middleware(app: FastAPI) -> None:
    @app.middleware("http")
    async def observe(
        request: Request, call_next: Callable[[Request], Awaitable[Response]]
    ) -> Response:
        request_id = request.headers.get("x-request-id") or uuid.uuid4().hex
        token = request_id_var.set(request_id)
        principal_token = principal_id_var.set(None)
        started = time.perf_counter()
        status_code = 500
        try:
            response = await call_next(request)
            status_code = response.status_code
            response.headers["X-Request-ID"] = request_id
            return response
        finally:
            elapsed = time.perf_counter() - started
            route = _route_template(request)
            REQUEST_COUNT.labels(request.method, route, str(status_code)).inc()
            REQUEST_LATENCY.labels(request.method, route).observe(elapsed)
            if route != "/metrics":
                logger.info(
                    "request",
                    extra={
                        "method": request.method,
                        "route": route,
                        "path": request.url.path,
                        "status": status_code,
                        "duration_ms": round(elapsed * 1000, 2),
                    },
                )
            request_id_var.reset(token)
            principal_id_var.reset(principal_token)


def metrics_response() -> Response:
    return Response(content=generate_latest(), media_type=CONTENT_TYPE_LATEST)
