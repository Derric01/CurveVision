"""Security headers and rate limiting."""

from __future__ import annotations

import time
from collections import defaultdict, deque
from collections.abc import Awaitable, Callable

from fastapi import FastAPI, Request, Response
from fastapi.responses import JSONResponse

from curvevision.core.config import Settings

#: Applied to every response. The API is JSON-only, so the CSP can be maximally strict --
#: the web frontend is served separately and ships its own policy.
SECURITY_HEADERS = {
    "X-Content-Type-Options": "nosniff",
    "X-Frame-Options": "DENY",
    "Referrer-Policy": "strict-origin-when-cross-origin",
    "Content-Security-Policy": "default-src 'none'; frame-ancestors 'none'",
    "Permissions-Policy": "geolocation=(), microphone=(), camera=()",
    "Cross-Origin-Resource-Policy": "same-site",
}

#: Endpoints where the docs UI needs to load its own assets.
_DOC_PATHS = ("/api/docs", "/api/redoc", "/api/openapi.json")


class SlidingWindowLimiter:
    """In-process sliding-window rate limiter.

    Deliberately per-process: it protects a single instance from a runaway client without
    requiring Redis, which keeps single-node self-hosting dependency-free. A multi-replica
    deployment that needs a global limit should enforce it at the ingress, and a
    Redis-backed limiter is *Planned* for deployments that want it in-app.
    """

    def __init__(self, limit_per_minute: int) -> None:
        self.limit = limit_per_minute
        self._hits: dict[str, deque[float]] = defaultdict(deque)

    def allow(self, key: str, now: float | None = None) -> bool:
        if self.limit <= 0:
            return True
        now = now if now is not None else time.monotonic()
        window = self._hits[key]
        cutoff = now - 60.0
        while window and window[0] < cutoff:
            window.popleft()
        if len(window) >= self.limit:
            return False
        window.append(now)
        return True

    def retry_after(self, key: str, now: float | None = None) -> int:
        window = self._hits.get(key)
        if not window:
            return 1
        now = now if now is not None else time.monotonic()
        return max(1, int(60.0 - (now - window[0])) + 1)


def install_security_middleware(app: FastAPI, settings: Settings) -> None:
    limiter = SlidingWindowLimiter(settings.rate_limit_per_minute)
    app.state.rate_limiter = limiter

    @app.middleware("http")
    async def security(
        request: Request, call_next: Callable[[Request], Awaitable[Response]]
    ) -> Response:
        # Rate-limit key: the API token or bearer prefix when present, else the peer
        # address. Never a client-supplied header alone, which would be trivially forged.
        authorization = request.headers.get("authorization", "")
        if authorization.lower().startswith("bearer "):
            key = f"token:{authorization[7:39]}"
        else:
            key = f"ip:{request.client.host if request.client else 'unknown'}"

        if not limiter.allow(key):
            return JSONResponse(
                status_code=429,
                content={
                    "type": "https://curvevision.dev/errors/rate_limited",
                    "title": "Too many requests",
                    "status": 429,
                    "detail": (f"Rate limit of {limiter.limit} requests per minute exceeded."),
                },
                media_type="application/problem+json",
                headers={"Retry-After": str(limiter.retry_after(key))},
            )

        response = await call_next(request)
        for header, value in SECURITY_HEADERS.items():
            if header == "Content-Security-Policy" and request.url.path.startswith(_DOC_PATHS):
                continue
            response.headers.setdefault(header, value)
        return response
