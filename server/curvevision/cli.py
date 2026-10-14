"""Server-side management commands.

Deliberately argparse rather than a CLI framework: this runs inside the container as an
operator tool, and it should not depend on anything the API does not already need. The
*user-facing* CLI lives in ``sdk/python`` and does use Typer.
"""

from __future__ import annotations

import argparse
import asyncio
import sys
from typing import Any

from curvevision import __version__
from curvevision.core.config import get_settings
from curvevision.core.db import get_sessionmaker
from curvevision.core.logging import configure_logging


async def _create_superuser(email: str, username: str, password: str) -> int:
    from curvevision.services import auth as auth_service

    settings = get_settings()
    factory = get_sessionmaker()
    async with factory() as session:
        user = await auth_service.register_user(
            session,
            settings,
            email=email,
            username=username,
            password=password,
            is_superuser=True,
        )
        await session.commit()
        print(f"Created superuser {user.username} <{user.email}>")
    return 0


async def _list_formats() -> int:
    from curvevision.formats import all_formats

    for fmt in all_formats():
        directions = []
        if fmt.capabilities.supports_import:
            directions.append("import")
        if fmt.capabilities.supports_export:
            directions.append("export")
        shapes = ", ".join(shape.value for shape in fmt.capabilities.shape_types)
        print(f"{fmt.id:<14} {fmt.name:<22} [{'/'.join(directions)}]  {shapes}")
    return 0


def _settings_for(desktop: str | None) -> Any:
    """The installation a backup or restore acts on: the server's environment, or — with
    `--desktop` — the desktop application's data directory (its default when no path)."""
    if desktop is None:
        return get_settings()
    from pathlib import Path

    from curvevision import desktop as desktop_module

    return desktop_module.local_settings(Path(desktop) if desktop else None)


async def _backup(destination: str, desktop: str | None) -> int:
    from pathlib import Path

    from curvevision.core.db import create_engine
    from curvevision.services import backup
    from curvevision.storage import get_storage

    settings = _settings_for(desktop)
    engine = create_engine(settings)
    try:
        report = await backup.write_backup(engine, get_storage(settings), Path(destination))
    finally:
        await engine.dispose()
    rows = sum(report.tables.values())
    print(
        f"Backed up {rows} rows in {len(report.tables)} tables and {report.objects} stored "
        f"objects ({report.object_bytes / 1e6:.1f} MB) to {destination}"
    )
    if report.in_place_files:
        print(
            f"Not copied: {report.in_place_files} media file(s) annotated in place, which stay "
            "where they are on this computer. Back those folders up yourself."
        )
    if report.missing:
        print(f"Warning: {len(report.missing)} object(s) the database names were not in storage:")
        for key in report.missing[:20]:
            print(f"  {key}")
    return 0


async def _restore(source: str, desktop: str | None) -> int:
    from pathlib import Path

    from curvevision import desktop as desktop_module
    from curvevision.core.db import create_engine
    from curvevision.services import backup
    from curvevision.storage import get_storage

    settings = _settings_for(desktop)
    try:
        manifest = backup.read_manifest(Path(source))
    except backup.BackupError as exc:
        print(f"Cannot restore: {exc}", file=sys.stderr)
        return 1
    # The schema first, as the server does on start: a restore needs an empty installation
    # at the backup's revision, and a new one has no schema at all yet.
    await asyncio.to_thread(desktop_module.migrate, settings)
    engine = create_engine(settings)
    try:
        report = await backup.restore_backup(engine, get_storage(settings), Path(source))
    except backup.BackupError as exc:
        print(f"Cannot restore: {exc}", file=sys.stderr)
        return 1
    finally:
        await engine.dispose()
    rows = sum(report.tables.values())
    print(
        f"Restored {rows} rows and {report.objects} stored objects from a backup made "
        f"{manifest.get('created_at')} by CurveVision {manifest.get('curvevision_version')}."
    )
    if report.in_place_files:
        print(
            f"{report.in_place_files} media file(s) were annotated in place on the machine the "
            "backup came from; they open here only if they are at the same paths."
        )
    return 0


async def _storage_init(wait: float) -> int:
    """Make sure the configured object store is ready for a first upload.

    Waits up to `wait` seconds for the store to answer, because this runs as a one-shot
    container started alongside it, and an S3 server that is still starting refuses
    connections for a moment.
    """
    import time

    from curvevision.storage import get_storage

    settings = get_settings()
    storage = get_storage(settings)
    ensure = getattr(storage, "ensure_bucket", None)
    if ensure is None:
        # A folder, which local storage creates as it writes: there is nothing to set up.
        print(f"Local storage at {settings.storage_local_root}: nothing to create.")
        return 0

    deadline = time.monotonic() + wait
    while True:
        try:
            created = await ensure()
            break
        except Exception as exc:
            if time.monotonic() >= deadline:
                print(
                    f"Object storage at {settings.s3_endpoint_url or 'AWS S3'} is not usable: "
                    f"{exc}",
                    file=sys.stderr,
                )
                return 1
            await asyncio.sleep(1)
    bucket = storage.bucket  # type: ignore[attr-defined]
    print(f"Created bucket {bucket}." if created else f"Bucket {bucket} already exists.")
    return 0


def _run_worker(queues: list[str]) -> int:
    """Start a Dramatiq worker.

    Exec'ing the dramatiq CLI rather than reimplementing its process supervision: it
    already handles graceful shutdown, prefetch and the worker/consumer split correctly.
    """
    import os
    import shutil

    executable = shutil.which("dramatiq")
    if executable is None:
        print(
            "dramatiq is not installed. Install the worker extra:\n"
            "  pip install 'curvevision-server[worker]'",
            file=sys.stderr,
        )
        return 1
    argv = [executable, "curvevision.jobs.dramatiq_app", "--queues", *queues]
    os.execv(executable, argv)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="curvevision-server", description="CurveVision server")
    parser.add_argument("--version", action="version", version=f"CurveVision {__version__}")
    sub = parser.add_subparsers(dest="command", required=True)

    superuser = sub.add_parser("createsuperuser", help="Create an administrator account")
    superuser.add_argument("--email", required=True)
    superuser.add_argument("--username", required=True)
    superuser.add_argument("--password", required=True)

    sub.add_parser("formats", help="List available dataset formats")

    desktop_help = (
        "act on the desktop application's data instead of the server's environment "
        "(its default directory, or DIR)"
    )
    backup = sub.add_parser("backup", help="Write the whole installation to one archive")
    backup.add_argument("archive", help="where to write it, e.g. curvevision-2026-10-13.tar")
    backup.add_argument("--desktop", nargs="?", const="", metavar="DIR", help=desktop_help)
    restore = sub.add_parser(
        "restore", help="Restore an archive into an empty installation (migrating it first)"
    )
    restore.add_argument("archive")
    restore.add_argument("--desktop", nargs="?", const="", metavar="DIR", help=desktop_help)

    storage_init = sub.add_parser(
        "storage-init",
        help="Create the object storage bucket if it does not exist (safe to run again)",
    )
    storage_init.add_argument(
        "--wait",
        type=float,
        default=60.0,
        metavar="SECONDS",
        help="how long to wait for the object store to start answering (default: 60)",
    )

    worker = sub.add_parser("worker", help="Run a background worker")
    worker.add_argument(
        "--queues",
        nargs="+",
        default=["default", "media", "import", "export", "inference", "webhooks", "quality"],
    )

    args = parser.parse_args(argv)
    settings = get_settings()
    configure_logging(settings.log_level, settings.log_format)

    handlers: dict[str, Any] = {
        "createsuperuser": lambda: asyncio.run(
            _create_superuser(args.email, args.username, args.password)
        ),
        "formats": lambda: asyncio.run(_list_formats()),
        "backup": lambda: asyncio.run(_backup(args.archive, args.desktop)),
        "restore": lambda: asyncio.run(_restore(args.archive, args.desktop)),
        "storage-init": lambda: asyncio.run(_storage_init(args.wait)),
        "worker": lambda: _run_worker(args.queues),
    }
    return int(handlers[args.command]())


if __name__ == "__main__":
    raise SystemExit(main())
