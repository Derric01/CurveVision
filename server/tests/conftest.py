"""Test fixtures.

The whole suite runs with **no external services**: SQLite via aiosqlite, local-filesystem
storage in a temp directory, and the inline job queue. That is a deliberate design
constraint -- a contributor should be able to clone the repo and run `pytest`.
"""

from __future__ import annotations

import uuid
from collections.abc import AsyncIterator, Iterator
from typing import Any

import httpx
import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from curvevision.core import db as db_module
from curvevision.core.config import Settings, configure_settings
from curvevision.core.db import Base, session_scope
from curvevision.domain.enums import Role
from curvevision.jobs import InlineJobQueue, set_job_queue
from curvevision.main import create_app
from curvevision.storage import reset_storage_cache


@pytest.fixture
def settings(tmp_path: Any) -> Iterator[Settings]:
    values = Settings(
        environment="test",
        secret_key="test-secret-key-not-used-anywhere-real",
        database_url="sqlite+aiosqlite:///:memory:",
        storage_backend="local",
        storage_local_root=str(tmp_path / "storage"),
        job_queue_backend="inline",
        log_level="WARNING",
        log_format="console",
        # Off in tests: a shared in-process limiter would make tests order-dependent.
        rate_limit_per_minute=0,
        password_min_length=8,
    )
    reset_storage_cache()
    yield values
    reset_storage_cache()


@pytest.fixture
async def engine(settings: Settings) -> AsyncIterator[Any]:
    # A single shared connection: an in-memory SQLite database exists only for as long as
    # its connection does, so every session in a test must use the same one.
    created = create_async_engine(
        settings.database_url,
        connect_args={"check_same_thread": False},
        poolclass=None,
    )
    async with created.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    yield created
    await created.dispose()


@pytest.fixture
async def sessionmaker_(engine: Any) -> async_sessionmaker[AsyncSession]:
    return async_sessionmaker(engine, expire_on_commit=False, autoflush=False)


@pytest.fixture
async def session(sessionmaker_: async_sessionmaker[AsyncSession]) -> AsyncIterator[AsyncSession]:
    async with sessionmaker_() as active:
        yield active


@pytest.fixture
async def app(
    settings: Settings, sessionmaker_: async_sessionmaker[AsyncSession]
) -> AsyncIterator[Any]:
    application = create_app(settings)

    async def override_session() -> AsyncIterator[AsyncSession]:
        async with sessionmaker_() as active:
            try:
                yield active
            except Exception:
                await active.rollback()
                raise

    application.dependency_overrides[session_scope] = override_session

    # Request handlers read `app.state.settings`, which `create_app` already set. Job
    # handlers and the storage factory reach for `get_settings()`, so bind the process
    # too -- otherwise a job would write to whatever the environment names.
    configure_settings(settings)
    # The runner opens its own sessions, so it needs the test engine too.
    db_module._sessionmaker = sessionmaker_
    queue = InlineJobQueue(wait=True)
    set_job_queue(queue)

    yield application

    set_job_queue(None)
    db_module._sessionmaker = None
    configure_settings(None)
    application.dependency_overrides.clear()


@pytest.fixture
async def client(app: Any) -> AsyncIterator[httpx.AsyncClient]:
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as http_client:
        # Exercise lifespan-registered registries without running uvicorn.
        import curvevision.formats
        import curvevision.jobs.tasks
        import curvevision.ml  # noqa: F401

        yield http_client


# ------------------------------------------------------------------------- helpers


class ApiActor:
    """A registered user plus an authenticated client for them."""

    def __init__(self, client: httpx.AsyncClient, user: dict[str, Any], token: str) -> None:
        self.client = client
        self.user = user
        self.token = token
        self.headers = {"Authorization": f"Bearer {token}"}

    @property
    def id(self) -> str:
        return str(self.user["id"])

    async def get(self, url: str, **kwargs: Any) -> httpx.Response:
        return await self.client.get(url, headers=self.headers, **kwargs)

    async def post(self, url: str, **kwargs: Any) -> httpx.Response:
        return await self.client.post(url, headers=self.headers, **kwargs)

    async def patch(self, url: str, **kwargs: Any) -> httpx.Response:
        return await self.client.patch(url, headers=self.headers, **kwargs)

    async def put(self, url: str, **kwargs: Any) -> httpx.Response:
        return await self.client.put(url, headers=self.headers, **kwargs)

    async def delete(self, url: str, **kwargs: Any) -> httpx.Response:
        return await self.client.delete(url, headers=self.headers, **kwargs)


async def register(
    client: httpx.AsyncClient, username: str | None = None, password: str = "correct-horse-42"
) -> ApiActor:
    username = username or f"user{uuid.uuid4().hex[:8]}"
    response = await client.post(
        "/api/v1/auth/register",
        json={
            "email": f"{username}@example.com",
            "username": username,
            "password": password,
            "full_name": username.title(),
        },
    )
    assert response.status_code == 201, response.text
    user = response.json()

    login = await client.post(
        "/api/v1/auth/login", json={"identifier": username, "password": password}
    )
    assert login.status_code == 200, login.text
    return ApiActor(client, user, login.json()["access_token"])


@pytest.fixture
async def owner(client: httpx.AsyncClient) -> ApiActor:
    """The first registered account, which becomes the instance superuser."""
    return await register(client, "founder")


@pytest.fixture
async def organization(owner: ApiActor) -> dict[str, Any]:
    response = await owner.post(
        "/api/v1/organizations",
        json={"slug": "acme-vision", "name": "Acme Vision"},
    )
    assert response.status_code == 201, response.text
    return dict(response.json())


@pytest.fixture
async def project(owner: ApiActor, organization: dict[str, Any]) -> dict[str, Any]:
    response = await owner.post(
        "/api/v1/projects",
        json={
            "organization_id": organization["id"],
            "slug": "street-scenes",
            "name": "Street Scenes",
            "labels": [
                {
                    "name": "car",
                    "color": "#ef4444",
                    "attributes": [
                        {
                            "name": "colour",
                            "attribute_type": "select",
                            "values": ["red", "blue", "black"],
                        },
                        {"name": "parked", "attribute_type": "checkbox", "mutable": True},
                    ],
                },
                {"name": "pedestrian", "color": "#22c55e"},
            ],
        },
    )
    assert response.status_code == 201, response.text
    return dict(response.json())


async def add_member(owner: ApiActor, organization_id: str, actor: ApiActor, role: Role) -> None:
    response = await owner.post(
        f"/api/v1/organizations/{organization_id}/members",
        json={"user_id": actor.id, "role": role.value},
    )
    assert response.status_code == 201, response.text
