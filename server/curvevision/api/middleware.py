"""Security headers and rate limiting."""

from __future__ import annotations

import time
from collections import defaultdict, deque
from collections.abc import Awaitable, Callable

from fastapi import FastAPI, Request, Response
from fastapi.responses import JSONResponse

from curvevision.core.config import Settings

#: Applied to every response. `default-src 'none'` is right for a JSON API: it can load
#: nothing, so it is allowed to load nothing.
SECURITY_HEADERS = {
    "X-Content-Type-Options": "nosniff",
    "X-Frame-Options": "DENY",
    "Referrer-Policy": "strict-origin-when-cross-origin",
    "Content-Security-Policy": "default-src 'none'; frame-ancestors 'none'",
    "Permissions-Policy": "geolocation=(), microphone=(), camera=()",
    "Cross-Origin-Resource-Policy": "same-site",
}

#: The policy for responses that are *the web application itself*, which the server sends
#: when `web_root` is configured (the desktop build, and any deployment that would rather
#: not run a separate web server).
#:
#: `default-src 'none'` above would block every script and stylesheet the page needs, so a
#: strict policy on these responses is not strict — it is a blank window. Each directive
#: below is the narrowest thing that still lets the editor run:
#:
#: * `script-src 'self'` — Vite emits external files; nothing is inlined.
#: * `style-src` needs `'unsafe-inline'` because React writes `style` attributes, which
#:   CSP treats as inline styles. It buys an attacker styling, not execution.
#: * `img-src` needs `blob:` and `data:` — the editor renders frames and thumbnails it has
#:   fetched, and the canvas produces blobs.
#: * `connect-src 'self'` — the API is same-origin by construction, which is the whole
#:   reason the desktop window loads from the server rather than an asset protocol.
#: * `frame-ancestors 'none'` and `object-src 'none'` stay: no clickjacking, no plugins.
WEB_APP_CSP = (
    "default-src 'self'; "
    "script-src 'self'; "
    "style-src 'self' 'unsafe-inline'; "
    "img-src 'self' data: blob:; "
    "font-src 'self' data:; "
    "connect-src 'self'; "
    "worker-src 'self' blob:; "
    "object-src 'none'; "
    "base-uri 'self'; "
    "form-action 'self'; "
    "frame-ancestors 'none'"
)

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

    # Only relax the policy when this process actually serves the application. A pure API
    # deployment, which is every containerised one, keeps `default-src 'none'`.
    serves_web_app = bool(settings.web_root)
    api_prefix = settings.api_prefix

    def policy_for(path: str) -> str | None:
        """The CSP for this response, or None to leave it unset."""
        if path.startswith(_DOC_PATHS):
            return None  # the docs UI loads its own assets from a CDN
        if serves_web_app and not path.startswith(api_prefix) and path != "/metrics":
            return WEB_APP_CSP
        return SECURITY_HEADERS["Content-Security-Policy"]

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
            if header == "Content-Security-Policy":
                if (policy := policy_for(request.url.path)) is not None:
                    response.headers.setdefault(header, policy)
                continue
            response.headers.setdefault(header, value)
        return response
