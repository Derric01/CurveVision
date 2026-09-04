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
        "worker": lambda: _run_worker(args.queues),
    }
    return int(handlers[args.command]())


if __name__ == "__main__":
    raise SystemExit(main())
