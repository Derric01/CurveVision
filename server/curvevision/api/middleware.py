"""Security headers and rate limiting."""

from __future__ import annotations

import time
import uuid
from collections import defaultdict, deque
from collections.abc import Awaitable, Callable
from typing import Any, Protocol

from fastapi import FastAPI, Request, Response
from fastapi.responses import JSONResponse

from curvevision.core.config import Settings
from curvevision.core.logging import get_logger

logger = get_logger(__name__)

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


#: The window every limiter counts requests over.
WINDOW_SECONDS = 60.0


class RateLimiter(Protocol):
    limit: int

    async def check(self, key: str) -> tuple[bool, int]:
        """Count a request against `key`: whether it is allowed, and if not, how many
        seconds until it would be."""
        ...


class SlidingWindowLimiter:
    """In-process sliding-window rate limiter.

    Per-process: it protects a single instance from a runaway client without requiring
    Redis, which keeps single-node self-hosting and the desktop build dependency-free. A
    server run as several instances counts in Redis instead (`RedisSlidingWindowLimiter`,
    ``rate_limit_backend = "redis"``): with this one, each process allows the full limit.
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

    async def check(self, key: str) -> tuple[bool, int]:
        if self.allow(key):
            return True, 0
        return False, self.retry_after(key)


#: One request against one key, atomically, on Redis' clock — so instances whose clocks
#: disagree still share one window. The window is a sorted set of request times; the
#: member carries a random suffix so two requests in the same microsecond both count.
#: Returns ``{1, 0}`` when allowed, ``{0, seconds until the oldest request ages out}``
#: when not.
_SLIDING_WINDOW = """
local now = redis.call('TIME')
local t = tonumber(now[1]) + tonumber(now[2]) / 1000000
local window = tonumber(ARGV[2])
redis.call('ZREMRANGEBYSCORE', KEYS[1], '-inf', t - window)
if redis.call('ZCARD', KEYS[1]) >= tonumber(ARGV[1]) then
  local oldest = redis.call('ZRANGE', KEYS[1], 0, 0, 'WITHSCORES')
  return {0, math.ceil(tonumber(oldest[2]) + window - t)}
end
redis.call('ZADD', KEYS[1], t, t .. ':' .. ARGV[3])
redis.call('PEXPIRE', KEYS[1], math.ceil(window * 1000))
return {1, 0}
"""


class RedisSlidingWindowLimiter:
    """The same sliding window, kept in Redis and shared by every instance.

    **Fails open**: when Redis cannot be reached, requests are allowed and the failure is
    logged — once per outage, not per request. A limiter whose outage took the API down
    with it would turn a cache failing into the whole service failing; the limit exists to
    stop a runaway client, which a short window without it does not make dangerous.
    """

    def __init__(self, limit_per_minute: int, redis_url: str, *, prefix: str = "ratelimit:"):
        try:
            from redis import asyncio as redis_asyncio
        except ImportError as exc:  # pragma: no cover - the extra is a deployment choice
            raise RuntimeError(
                'rate_limit_backend = "redis" needs the redis package: '
                'pip install "curvevision-server[redis]"'
            ) from exc
        self.limit = limit_per_minute
        self.prefix = prefix
        self._client: Any = redis_asyncio.from_url(redis_url)
        self._script: Any = self._client.register_script(_SLIDING_WINDOW)
        self._failing = False

    async def check(self, key: str) -> tuple[bool, int]:
        if self.limit <= 0:
            return True, 0
        try:
            allowed, retry = await self._script(
                keys=[f"{self.prefix}{key}"],
                args=[self.limit, WINDOW_SECONDS, uuid.uuid4().hex],
            )
        except Exception as exc:  # any Redis failure means the same thing here
            if not self._failing:
                logger.warning("rate limiting is off: Redis is unreachable (%s)", exc)
                self._failing = True
            return True, 0
        if self._failing:
            logger.info("rate limiting is back: Redis is reachable again")
            self._failing = False
        return bool(allowed), max(1, int(retry)) if not allowed else 0

    async def aclose(self) -> None:
        await self._client.aclose()


def make_limiter(settings: Settings) -> RateLimiter:
    if settings.rate_limit_backend == "redis":
        return RedisSlidingWindowLimiter(settings.rate_limit_per_minute, settings.redis_url)
    return SlidingWindowLimiter(settings.rate_limit_per_minute)


def install_security_middleware(app: FastAPI, settings: Settings) -> None:
    limiter = make_limiter(settings)
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

        allowed, retry_after = await limiter.check(key)
        if not allowed:
            return JSONResponse(
                status_code=429,
                content={
                    "type": "https://curvevision.dev/errors/rate_limited",
                    "title": "Too many requests",
                    "status": 429,
                    "detail": (f"Rate limit of {limiter.limit} requests per minute exceeded."),
                },
                media_type="application/problem+json",
                headers={"Retry-After": str(retry_after)},
            )

        response = await call_next(request)
        for header, value in SECURITY_HEADERS.items():
            if header == "Content-Security-Policy":
                if (policy := policy_for(request.url.path)) is not None:
                    response.headers.setdefault(header, policy)
                continue
            response.headers.setdefault(header, value)
        return response
