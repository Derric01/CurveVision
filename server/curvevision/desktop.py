"""Desktop (local) mode.

This is how the installed application runs. The Tauri shell spawns this module as a
sidecar process; it picks a free port on the loopback interface, brings the local database
up to date, provisions a single local account on first launch, and prints one line of JSON
so the shell knows where to point the webview and which token to use.

Three properties matter:

* **Zero configuration.** No secret key to generate, no database to install, no bucket to
  create. A person who double-clicks an installer should be annotating within seconds.
* **Loopback only.** The server binds `127.0.0.1`, on the port it used last time if that is
  still free. Nothing is reachable from the network, which is what makes the
  auto-provisioned account safe.
* **A real upgrade path.** The schema is migrated with Alembic, not `create_all`, because
  a desktop user will open v2 with a database written by v1 and must not lose it.
"""

from __future__ import annotations

import asyncio
import json
import os
import secrets
import socket
import sys
import threading
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

from curvevision.core.config import Settings, configure_settings
from curvevision.core.logging import configure_logging, get_logger

logger = get_logger(__name__)

#: The line the shell waits for on stdout. Prefixed so it cannot be confused with a log
#: line, and printed exactly once.
HANDSHAKE_PREFIX = "CURVEVISION_READY "

LOCAL_USERNAME = "local"
LOCAL_EMAIL = "local@curvevision.app"
LOCAL_WORKSPACE_SLUG = "local"
LOCAL_WORKSPACE_NAME = "My Workspace"
#: Marks the tokens this module mints, so a relaunch can tell its own from one the user
#: created deliberately for a script.
TOKEN_NAME = "desktop"


@dataclass(frozen=True, slots=True)
class Handshake:
    """What the desktop shell needs in order to talk to the sidecar."""

    url: str
    token: str
    data_dir: str
    version: str


def default_app_data_dir() -> Path:
    """The conventional per-user application directory for this platform.

    Using the platform convention rather than a dotfile in `$HOME` means the OS backs it
    up, cleans it up on uninstall, and does not surprise anyone looking for their data.
    """
    if sys.platform == "darwin":
        return Path.home() / "Library" / "Application Support" / "CurveVision"
    if sys.platform == "win32":
        base = os.environ.get("LOCALAPPDATA") or os.environ.get("APPDATA")
        root = Path(base) if base else Path.home() / "AppData" / "Local"
        return root / "CurveVision"
    # Linux and everything else: XDG.
    base = os.environ.get("XDG_DATA_HOME")
    return (Path(base) if base else Path.home() / ".local" / "share") / "CurveVision"


def _free_port() -> int:
    """Ask the OS for an unused loopback port.

    A fixed port would collide with a second instance, and with whatever else the user
    happens to be running.
    """
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        return int(probe.getsockname()[1])


def _port_is_free(port: int) -> bool:
    with socket.socket() as probe:
        # As uvicorn binds: without this, the connections the previous run closed a moment
        # ago hold the port in TIME_WAIT, and a relaunch straight after a crash -- the case
        # this exists for -- would find it taken. Windows needs no such help, and there the
        # option would let this bind over a port somebody else is listening on.
        if os.name != "nt":
            probe.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        try:
            probe.bind(("127.0.0.1", port))
        except OSError:
            return False
    return True


def port_for(app_data_dir: Path) -> int:
    """The port this installation used last time if it is still free, or a free one.

    The window loads the application from `http://127.0.0.1:<port>`, and a browser keeps what
    a page stores -- the editor's copy of work not yet saved among it -- per origin, and the
    port is part of the origin. A new port on every launch was a new, empty origin on every
    launch, so whatever the editor kept for after a crash was somewhere no later launch could
    look. Remembering the port keeps the origin; falling back to a free one means a second
    instance, or anything else holding the port, cannot stop this one starting. A port that
    is only busy is not forgotten: the instance holding it is usually the first one, whose
    origin is the one worth keeping.
    """
    remembered = app_data_dir / "port"
    try:
        preferred = int(remembered.read_text().strip())
    except (OSError, ValueError):
        preferred = 0
    if 1024 <= preferred <= 65535:
        return preferred if _port_is_free(preferred) else _free_port()
    chosen = _free_port()
    try:
        remembered.write_text(str(chosen))
    except OSError:  # pragma: no cover - a read-only data directory still runs
        logger.debug("could not remember the port", extra={"port": chosen})
    return chosen


def _persistent_secret(app_data_dir: Path) -> str:
    """Read or create the signing key for this installation.

    Persisted rather than regenerated per launch so that a token stays valid across
    restarts; `0600` because it signs credentials.
    """
    key_file = app_data_dir / "secret.key"
    if key_file.exists():
        existing = key_file.read_text().strip()
        if existing:
            return existing

    generated = secrets.token_urlsafe(48)
    key_file.write_text(generated)
    try:
        key_file.chmod(0o600)
    except OSError:  # pragma: no cover - Windows filesystems may refuse this
        logger.debug("could not restrict permissions on the secret key file")
    return generated


def local_settings(app_data_dir: Path | None = None, **overrides: object) -> Settings:
    """Build the Settings a desktop installation runs with."""
    data_dir = Path(app_data_dir) if app_data_dir else default_app_data_dir()
    data_dir.mkdir(parents=True, exist_ok=True)
    bundled_web = web_root()

    return Settings(
        environment="production",
        local_mode=True,
        app_data_dir=str(data_dir),
        # Same-origin with the API, so there is no CORS policy to get wrong here.
        web_root=str(bundled_web) if bundled_web else None,
        secret_key=_persistent_secret(data_dir),
        # A SQLite file in the app directory: no service to install, and the whole of a
        # user's work is one file they can copy to another machine.
        database_url=f"sqlite+aiosqlite:///{(data_dir / 'curvevision.db').as_posix()}",
        storage_backend="local",
        storage_local_root=str(data_dir / "media"),
        # Every background job runs in-process. There is no broker on a laptop, and the
        # work (export, import, thumbnails) is short enough not to need one.
        job_queue_backend="inline",
        # Nothing is reachable off this machine, so registration and rate limiting are
        # not the controls that matter here.
        allow_registration=False,
        rate_limit_per_minute=0,
        cors_origins=(),
        log_format="console",
        metrics_enabled=False,
        **overrides,  # type: ignore[arg-type]
    )


def web_root() -> Path | None:
    """The built web application to serve, if this build carries one.

    Serving the editor from the same process is what makes the desktop app a single
    executable and what makes the editor same-origin with the API -- no CORS, no second
    port, and `curvevision-local` on its own opens a complete working CurveVision in a
    browser. A source checkout has it only after `npm run build`.
    """
    bundled = getattr(sys, "_MEIPASS", None)
    candidates = (
        [Path(bundled) / "web"]
        if bundled is not None
        else [Path(__file__).resolve().parents[2] / "web" / "dist"]
    )
    return next((path for path in candidates if (path / "index.html").is_file()), None)


def migrations_dir() -> Path:
    """Where Alembic's revision scripts live.

    Alembic reads `env.py` and each revision from disk, so in a packaged build they are
    bundled as data rather than compiled into the archive, and `__file__` no longer points
    anywhere useful -- the entry script is unpacked at the root of the bundle while the
    data keeps its package path.
    """
    bundled = getattr(sys, "_MEIPASS", None)
    if bundled is not None:
        return Path(bundled) / "curvevision" / "migrations"
    return Path(__file__).resolve().parent / "migrations"


def alembic_config(settings: Settings) -> Any:
    """The Alembic configuration this installation's migrations run under.

    Separate from `migrate` so a test can drive the same configuration *downwards*: a
    migration nobody has ever run backwards is a migration whose `downgrade` is a guess.
    """
    import logging

    from alembic.config import Config

    migrations = migrations_dir()
    if not (migrations / "env.py").is_file():  # pragma: no cover - packaging mistake
        raise RuntimeError(
            f"This build is missing its database migrations (looked in {migrations}). "
            "It cannot safely open a database."
        )
    # Alembic narrates every step at INFO. On a server that is useful; on a desktop launch
    # it is seven lines of noise before the window opens.
    logging.getLogger("alembic").setLevel(logging.WARNING)

    config = Config()
    config.set_main_option("script_location", str(migrations))
    config.set_main_option("sqlalchemy.url", settings.database_url)
    # env.py prefers this over the environment, so an in-process migration cannot drift
    # from the settings the server is about to run with.
    config.attributes["settings"] = settings
    return config


def migrate(settings: Settings) -> None:
    """Bring the local database up to the current schema.

    Alembic rather than `create_all`: a desktop user *will* open a new version of the app
    against a database written by an older one, and that has to be a migration, not a
    surprise.
    """
    from alembic import command

    command.upgrade(alembic_config(settings), "head")


async def provision(settings: Settings) -> str:
    """Ensure the local account exists and return a fresh API token for this launch.

    A new token per launch, with the previous one revoked, means a token that leaks into a
    log or a crash report stops working when the app restarts.
    """
    from sqlalchemy import select

    from curvevision.core.db import configure_engine, dispose_engine, get_sessionmaker, utcnow
    from curvevision.domain.enums import Role
    from curvevision.domain.identity import ApiToken, Organization, OrganizationMembership, User
    from curvevision.services import auth as auth_service
    from curvevision.services import organizations as org_service

    configure_engine(settings)
    factory = get_sessionmaker()

    async with factory() as session:
        user = (
            await session.execute(select(User).where(User.username == LOCAL_USERNAME))
        ).scalar_one_or_none()

        if user is None:
            user = await auth_service.register_user(
                session,
                settings,
                email=LOCAL_EMAIL,
                username=LOCAL_USERNAME,
                # Never shown and never used to sign in; the shell authenticates with the
                # API token below. It exists because the column is not nullable.
                password=secrets.token_urlsafe(32),
                full_name="Local user",
                is_superuser=True,
            )
            await session.flush()

        workspace = (
            await session.execute(
                select(Organization).where(Organization.slug == LOCAL_WORKSPACE_SLUG)
            )
        ).scalar_one_or_none()

        if workspace is None:
            workspace = await org_service.create_organization(
                session,
                slug=LOCAL_WORKSPACE_SLUG,
                name=LOCAL_WORKSPACE_NAME,
                description="Everything on this computer.",
                creator=user,
            )
        else:
            # A database restored from another machine may carry the workspace without a
            # membership for this user; without one, every project would 404.
            membership = (
                await session.execute(
                    select(OrganizationMembership).where(
                        OrganizationMembership.organization_id == workspace.id,
                        OrganizationMembership.user_id == user.id,
                    )
                )
            ).scalar_one_or_none()
            if membership is None:
                session.add(
                    OrganizationMembership(
                        organization_id=workspace.id, user_id=user.id, role=Role.OWNER
                    )
                )

        stale = (
            (
                await session.execute(
                    select(ApiToken).where(
                        ApiToken.user_id == user.id,
                        ApiToken.name == TOKEN_NAME,
                        ApiToken.revoked_at.is_(None),
                    )
                )
            )
            .scalars()
            .all()
        )
        for token in stale:
            token.revoked_at = utcnow()

        _, minted = await auth_service.create_api_token(session, user, name=TOKEN_NAME)
        await session.commit()
        plaintext = minted.plaintext

    # This ran on a loop that is about to close; the server opens its own. Leaving the
    # engine bound would hand the app connections created on a dead loop.
    await dispose_engine()
    return plaintext


def bootstrap(app_data_dir: Path | None = None) -> tuple[Settings, str]:
    """Prepare a local installation: migrate, provision, return settings and a token."""
    settings = local_settings(app_data_dir)
    configure_logging(settings.log_level, settings.log_format)
    configure_settings(settings)
    migrate(settings)
    token = asyncio.run(provision(settings))
    return settings, token


def serve(
    app_data_dir: Path | None = None,
    port: int | None = None,
    *,
    exit_with_parent: bool = False,
) -> None:
    """Run the local server and announce itself on stdout.

    Blocks until the process is terminated, which is what the shell expects of a sidecar.
    """
    import uvicorn

    from curvevision import __version__
    from curvevision.main import create_app

    settings, token = bootstrap(app_data_dir)
    chosen = port or port_for(Path(settings.app_data_dir or "."))

    handshake = Handshake(
        url=f"http://127.0.0.1:{chosen}",
        token=token,
        data_dir=settings.app_data_dir or "",
        version=__version__,
    )
    # One line, flushed immediately: the shell blocks on this before showing a window.
    print(HANDSHAKE_PREFIX + json.dumps(asdict(handshake)), flush=True)

    server = uvicorn.Server(
        uvicorn.Config(
            create_app(settings),
            host="127.0.0.1",  # loopback only; never reachable from the network
            port=chosen,
            log_level="warning",
            access_log=False,
        )
    )
    if exit_with_parent:
        _exit_when_parent_does(server)
    server.run()


def _exit_when_parent_does(server: Any) -> None:
    """Shut down when whoever launched us goes away.

    A desktop application that leaves an HTTP server running after its window closes is a
    bug people discover much later and never quite explain. The shell's own exit handler
    covers the ordinary case; this covers the ones it cannot -- a crash, a force quit, a
    kill -9 -- by watching the pipe the parent holds open. When the parent dies the OS
    closes its end and the read returns end-of-file.

    Requested explicitly with `--exit-with-parent`, never assumed: run from a terminal,
    consuming standard input would eat the user's keystrokes.
    """

    def watch() -> None:
        try:
            while sys.stdin.readline():
                pass
        except (OSError, ValueError):  # pragma: no cover - the pipe was torn down
            pass
        # Asking uvicorn to stop, rather than killing the process, so an in-flight write
        # to the database finishes.
        server.should_exit = True

    threading.Thread(target=watch, name="parent-watch", daemon=True).start()


def main(argv: list[str] | None = None) -> int:
    """Entry point for the packaged sidecar binary."""
    import argparse

    parser = argparse.ArgumentParser(
        prog="curvevision-local",
        description="Run CurveVision's local server for the desktop application.",
    )
    parser.add_argument(
        "--data-dir",
        type=Path,
        default=None,
        help="Override the application data directory.",
    )
    parser.add_argument(
        "--port",
        type=int,
        default=None,
        help="Bind this loopback port instead of the one used last time, or a free one.",
    )
    parser.add_argument(
        "--print-data-dir",
        action="store_true",
        help="Print where this installation keeps its data, then exit.",
    )
    parser.add_argument(
        "--exit-with-parent",
        action="store_true",
        help=(
            "Shut down when the process that launched this one goes away, detected by "
            "end-of-file on standard input. The desktop shell passes this so a crash "
            "cannot leave a server running."
        ),
    )
    args = parser.parse_args(argv)

    if args.print_data_dir:
        print(args.data_dir or default_app_data_dir())
        return 0

    serve(args.data_dir, args.port, exit_with_parent=args.exit_with_parent)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
