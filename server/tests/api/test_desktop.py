"""Desktop (local) mode.

These tests cover the path an installed application actually takes: migrate a fresh
database, provision the one local account, mint a token, and be usable with no sign-in
step. They run the real Alembic migration against a real SQLite file in `tmp_path`,
because the whole point of local mode is that a desktop user's database survives an
upgrade, and `create_all` would not prove that.
"""

from __future__ import annotations

import asyncio
import json
import sys
from dataclasses import asdict
from pathlib import Path
from typing import Any

import httpx
import pytest
from sqlalchemy import select

from curvevision import desktop
from curvevision.core import db as db_module
from curvevision.core.config import Settings, configure_settings
from curvevision.domain.identity import ApiToken, Organization, OrganizationMembership, User


@pytest.fixture
async def installation(tmp_path: Path) -> Any:
    """A provisioned local installation, with process globals restored afterwards.

    `bootstrap` is a synchronous entry point -- it drives Alembic, which owns its own event
    loop -- so it runs in a worker thread here, exactly as it runs before uvicorn starts in
    the packaged sidecar.
    """
    settings, token = await asyncio.to_thread(desktop.bootstrap, tmp_path / "CurveVision")
    yield settings, token
    await db_module.dispose_engine()
    configure_settings(None)


async def test_bootstrap_creates_a_self_contained_installation(installation: Any) -> None:
    settings, token = installation
    data_dir = Path(settings.app_data_dir or "")

    assert settings.local_mode is True
    assert settings.is_sqlite
    # Everything a person's work depends on lives under one directory they can copy.
    assert Path(settings.storage_local_root).is_relative_to(data_dir)
    assert (data_dir / "curvevision.db").exists()
    assert (data_dir / "secret.key").exists()
    assert token.startswith("cv_")

    # No broker, no object store, no registration endpoint worth attacking.
    assert settings.job_queue_backend == "inline"
    assert settings.storage_backend == "local"
    assert settings.allow_registration is False


@pytest.mark.skipif(sys.platform == "win32", reason="POSIX permission bits")
async def test_the_signing_key_is_not_world_readable(installation: Any) -> None:
    settings, _ = installation
    key_file = Path(settings.app_data_dir or "") / "secret.key"
    assert key_file.stat().st_mode & 0o077 == 0


async def test_the_local_account_and_workspace_are_provisioned(installation: Any) -> None:
    settings, _ = installation
    db_module.configure_engine(settings)

    async with db_module.get_sessionmaker()() as session:
        users = (await session.execute(select(User))).scalars().all()
        assert [user.username for user in users] == [desktop.LOCAL_USERNAME]
        # There is exactly one person here, and the app is theirs.
        assert users[0].is_superuser is True

        workspaces = (await session.execute(select(Organization))).scalars().all()
        assert [workspace.slug for workspace in workspaces] == [desktop.LOCAL_WORKSPACE_SLUG]

        memberships = (await session.execute(select(OrganizationMembership))).scalars().all()
        assert len(memberships) == 1


async def test_relaunching_reuses_the_database_and_rotates_the_token(tmp_path: Path) -> None:
    data_dir = tmp_path / "CurveVision"
    first_settings, first_token = await asyncio.to_thread(desktop.bootstrap, data_dir)
    await db_module.dispose_engine()
    second_settings, second_token = await asyncio.to_thread(desktop.bootstrap, data_dir)

    try:
        # The installation is one directory, migrated in place, not recreated.
        assert second_settings.secret_key == first_settings.secret_key
        assert second_settings.database_url == first_settings.database_url
        assert second_token != first_token

        db_module.configure_engine(second_settings)
        async with db_module.get_sessionmaker()() as session:
            assert len((await session.execute(select(User))).scalars().all()) == 1
            assert len((await session.execute(select(Organization))).scalars().all()) == 1

            tokens = (
                (await session.execute(select(ApiToken).where(ApiToken.name == desktop.TOKEN_NAME)))
                .scalars()
                .all()
            )
            live = [token for token in tokens if token.revoked_at is None]
            # A token that leaked into a log or a crash report stops working on restart.
            assert len(tokens) == 2
            assert len(live) == 1
    finally:
        await db_module.dispose_engine()
        configure_settings(None)


async def test_the_app_is_usable_with_the_handshake_token_and_no_sign_in(
    installation: Any,
) -> None:
    """The shell holds a token, never a password. Everything must work from that alone."""
    from curvevision.main import create_app

    settings, token = installation
    app = create_app(settings)
    headers = {"Authorization": f"Bearer {token}"}

    transport = httpx.ASGITransport(app=app)
    async with (
        httpx.AsyncClient(transport=transport, base_url="http://desktop") as client,
        app.router.lifespan_context(app),
    ):
        me = await client.get("/api/v1/auth/me", headers=headers)
        assert me.status_code == 200
        assert me.json()["username"] == desktop.LOCAL_USERNAME

        workspaces = await client.get("/api/v1/organizations", headers=headers)
        assert workspaces.status_code == 200
        assert [item["slug"] for item in workspaces.json()] == [desktop.LOCAL_WORKSPACE_SLUG]

        # Loopback is the boundary, not anonymity: an unauthenticated caller is still
        # rejected, so a stray page in a browser on this machine gets nothing.
        assert (await client.get("/api/v1/auth/me")).status_code == 401


def test_the_handshake_is_one_parseable_line() -> None:
    """The shell reads stdout line by line and matches on the prefix."""
    handshake = desktop.Handshake(
        url="http://127.0.0.1:1234", token="cv_x", data_dir="/tmp/x", version="0.0.0"
    )
    line = desktop.HANDSHAKE_PREFIX + json.dumps(asdict(handshake))

    assert "\n" not in line
    assert line.startswith(desktop.HANDSHAKE_PREFIX)
    assert json.loads(line[len(desktop.HANDSHAKE_PREFIX) :])["url"] == "http://127.0.0.1:1234"


def test_the_data_directory_follows_the_platform_convention() -> None:
    path = desktop.default_app_data_dir()
    assert path.name == "CurveVision"
    if sys.platform == "darwin":
        assert "Application Support" in path.parts
    elif sys.platform == "win32":
        assert "AppData" in path.parts or path.is_absolute()
    else:
        assert ".local" in path.parts or "XDG" not in str(path)


def test_the_server_is_offered_a_bindable_loopback_port() -> None:
    import socket

    port = desktop._free_port()
    assert 1024 < port < 65536
    # The point is that the OS chose it and it is actually free -- a hard-coded port would
    # collide with a second instance, or with whatever else the user is running.
    with socket.socket() as server:
        server.bind(("127.0.0.1", port))


async def test_request_handlers_read_the_app_s_settings_not_the_environment(
    settings: Settings,
) -> None:
    """Regression: `create_app(settings)` used to bind its engine to one configuration
    while every request handler resolved another from the environment. In a desktop
    install that silently writes a person's media to the wrong directory."""
    from curvevision.main import create_app

    app = create_app(settings)
    transport = httpx.ASGITransport(app=app)
    async with (
        httpx.AsyncClient(transport=transport, base_url="http://testserver") as client,
        app.router.lifespan_context(app),
    ):
        body = (await client.get("/api/v1/health")).json()

    assert body["environment"] == settings.environment == "test"
    await db_module.dispose_engine()
    configure_settings(None)
