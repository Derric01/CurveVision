"""SDK test fixtures.

The SDK is synchronous, and `httpx` has no synchronous ASGI transport, so these tests run
a **real uvicorn server** on an ephemeral port against a temporary SQLite database. That
is slower than an in-process transport and worth it: it exercises the actual HTTP path the
SDK will use in production, including status codes, headers and content negotiation.
"""

from __future__ import annotations

import asyncio
import contextlib
import socket
import threading
import time
from collections.abc import Iterator
from typing import Any

import pytest

pytest.importorskip("curvevision", reason="SDK tests run against the CurveVision server")
pytest.importorskip("uvicorn")


def _free_port() -> int:
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        return int(probe.getsockname()[1])


@pytest.fixture(scope="session")
def server(tmp_path_factory: pytest.TempPathFactory) -> Iterator[str]:
    import uvicorn
    from curvevision.core.config import Settings
    from curvevision.core.db import Base, create_engine
    from curvevision.main import create_app

    workspace = tmp_path_factory.mktemp("curvevision-sdk")
    port = _free_port()
    settings = Settings(
        environment="test",
        secret_key="sdk-test-secret-key-not-used-anywhere-real",
        database_url=f"sqlite+aiosqlite:///{workspace / 'sdk.db'}",
        storage_backend="local",
        storage_local_root=str(workspace / "storage"),
        job_queue_backend="inline",
        log_level="WARNING",
        log_format="console",
        rate_limit_per_minute=0,
        password_min_length=8,
        cors_origins=("http://localhost",),
    )

    async def prepare() -> None:
        engine = create_engine(settings)
        async with engine.begin() as connection:
            await connection.run_sync(Base.metadata.create_all)
        await engine.dispose()

    asyncio.run(prepare())

    app = create_app(settings)
    config = uvicorn.Config(app, host="127.0.0.1", port=port, log_level="error")
    uvicorn_server = uvicorn.Server(config)
    thread = threading.Thread(target=uvicorn_server.run, daemon=True)
    thread.start()

    deadline = time.monotonic() + 30.0
    while not uvicorn_server.started:
        if time.monotonic() > deadline:
            raise RuntimeError("CurveVision test server did not start within 30s")
        time.sleep(0.05)

    yield f"http://127.0.0.1:{port}"

    uvicorn_server.should_exit = True
    thread.join(timeout=10)


@pytest.fixture
def client(server: str) -> Iterator[Any]:
    """An authenticated SDK client with its own account and organization."""
    import uuid

    import httpx

    from curvevision_sdk import CurveVision

    username = f"sdk{uuid.uuid4().hex[:8]}"
    httpx.post(
        f"{server}/api/v1/auth/register",
        json={
            "email": f"{username}@example.com",
            "username": username,
            "password": "correct-horse-42",
            "full_name": "SDK Tester",
        },
        timeout=30.0,
    ).raise_for_status()

    session = CurveVision.login(server, username, "correct-horse-42")
    token = session.create_token("sdk-tests")
    session.close()

    with contextlib.closing(CurveVision(server, token=token)) as authenticated:
        yield authenticated


@pytest.fixture
def organization(client: Any) -> Any:
    import uuid

    return client.create_organization(f"org-{uuid.uuid4().hex[:8]}", "SDK Org")
