"""The rate limit, in one process and shared through Redis.

Until iteration 76 the limiter had no test at all, and only one backend: a sliding window
held in each process. A server run as several instances therefore let a client through as
many times the limit as there were instances. ``rate_limit_backend = "redis"`` keeps the
window in Redis, on Redis' clock, so every instance counts against one limit.

The Redis tests run against a real server when ``CURVEVISION_TEST_REDIS_URL`` is set (CI
sets it); without one they skip, as the PostgreSQL ones do.
"""

from __future__ import annotations

import os
import uuid
from typing import Any

import httpx
import pytest

from curvevision.core.config import Settings
from curvevision.main import create_app

REDIS = os.environ.get("CURVEVISION_TEST_REDIS_URL")
HEALTH = "/api/v1/health"


def limited(settings: Settings, limit: int, **changes: Any) -> Settings:
    return settings.model_copy(update={"rate_limit_per_minute": limit, **changes})


def client_for(settings: Settings) -> httpx.AsyncClient:
    app = create_app(settings)
    return httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://server")


def bearer(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


async def statuses(client: httpx.AsyncClient, count: int, token: str) -> list[int]:
    return [(await client.get(HEALTH, headers=bearer(token))).status_code for _ in range(count)]


class TestInOneProcess:
    async def test_the_request_past_the_limit_is_refused_and_told_when_to_retry(
        self, settings: Settings
    ) -> None:
        async with client_for(limited(settings, 3)) as client:
            assert await statuses(client, 3, "alice") == [200, 200, 200]
            refused = await client.get(HEALTH, headers=bearer("alice"))

        assert refused.status_code == 429
        assert refused.headers["content-type"] == "application/problem+json"
        assert refused.json()["title"] == "Too many requests"
        assert 1 <= int(refused.headers["Retry-After"]) <= 61

    async def test_each_token_has_its_own_limit(self, settings: Settings) -> None:
        async with client_for(limited(settings, 2)) as client:
            assert await statuses(client, 3, "alice") == [200, 200, 429]
            assert await statuses(client, 2, "bob") == [200, 200]

    async def test_two_instances_each_allow_the_whole_limit(self, settings: Settings) -> None:
        """Why the Redis backend exists: in memory, every process counts on its own."""
        config = limited(settings, 2)
        async with client_for(config) as first, client_for(config) as second:
            assert await statuses(first, 2, "alice") == [200, 200]
            assert await statuses(second, 2, "alice") == [200, 200]


@pytest.mark.skipif(not REDIS, reason="set CURVEVISION_TEST_REDIS_URL to run against Redis")
class TestSharedThroughRedis:
    def config(self, settings: Settings, limit: int) -> Settings:
        return limited(settings, limit, rate_limit_backend="redis", redis_url=REDIS)

    async def test_two_instances_enforce_one_limit_between_them(self, settings: Settings) -> None:
        token = uuid.uuid4().hex  # a key no earlier run has counted against
        config = self.config(settings, 3)
        async with client_for(config) as first, client_for(config) as second:
            assert await statuses(first, 2, token) == [200, 200]
            assert await statuses(second, 1, token) == [200]
            refused = await second.get(HEALTH, headers=bearer(token))
            also_refused = await first.get(HEALTH, headers=bearer(token))

        assert (refused.status_code, also_refused.status_code) == (429, 429)
        assert 1 <= int(refused.headers["Retry-After"]) <= 61

    async def test_each_token_has_its_own_limit(self, settings: Settings) -> None:
        alice, bob = uuid.uuid4().hex, uuid.uuid4().hex
        async with client_for(self.config(settings, 2)) as client:
            assert await statuses(client, 3, alice) == [200, 200, 429]
            assert await statuses(client, 2, bob) == [200, 200]


async def test_an_unreachable_redis_lets_requests_through(settings: Settings) -> None:
    """Fail open: a limiter outage must not take the API down with it. Nothing listens on
    port 1, so this needs no Redis at all."""
    config = limited(settings, 1, rate_limit_backend="redis", redis_url="redis://127.0.0.1:1/0")
    async with client_for(config) as client:
        assert await statuses(client, 3, "alice") == [200, 200, 200]
