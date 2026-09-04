"""The ``curvevision`` command-line tool.

Built on the SDK, not beside it: every command is a thin shell over a client method, so
the CLI cannot drift from the library or the API.

```
curvevision login https://curvevision.example.com --username alice
curvevision projects
curvevision task create <project-id> --name "Batch 1" --upload ./images/*.jpg
curvevision export <project-id> --format yolo --output dataset.zip
```

Credentials live in ``~/.config/curvevision/config.json`` with 0600 permissions.
"""

from __future__ import annotations

import json
import os
import stat
import sys
import uuid
from pathlib import Path
from typing import Annotated, Any

import typer

from curvevision_sdk import CurveVision, CurveVisionError, __version__

app = typer.Typer(
    name="curvevision",
    help="CurveVision — open-source annotation and dataset infrastructure.",
    no_args_is_help=True,
    add_completion=False,
)
task_app = typer.Typer(help="Create and manage tasks.", no_args_is_help=True)
job_app = typer.Typer(help="Inspect and update annotation jobs.", no_args_is_help=True)
app.add_typer(task_app, name="task")
app.add_typer(job_app, name="job")


def config_path() -> Path:
    base = Path(os.environ.get("XDG_CONFIG_HOME", Path.home() / ".config"))
    return base / "curvevision" / "config.json"


def load_config() -> dict[str, Any]:
    path = config_path()
    if not path.exists():
        return {}
    try:
        return dict(json.loads(path.read_text()))
    except (OSError, ValueError):
        return {}


def save_config(config: dict[str, Any]) -> None:
    path = config_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(config, indent=2))
    # A token in a world-readable file is a credential leak on any shared machine.
    path.chmod(stat.S_IRUSR | stat.S_IWUSR)


def client() -> CurveVision:
    config = load_config()
    url = os.environ.get("CURVEVISION_URL") or config.get("url")
    token = os.environ.get("CURVEVISION_TOKEN") or config.get("token")
    if not url:
        typer.secho(
            "Not configured. Run `curvevision login <url>` or set CURVEVISION_URL.",
            fg=typer.colors.RED,
            err=True,
        )
        raise typer.Exit(2)
    return CurveVision(url, token=token)


def emit(value: Any, *, as_json: bool = False) -> None:
    if as_json or not isinstance(value, list | dict):
        typer.echo(json.dumps(value, indent=2, default=str))
        return
    typer.echo(json.dumps(value, indent=2, default=str))


def handle(function: Any) -> Any:
    """Turn an API problem document into a readable CLI error."""
    try:
        return function()
    except CurveVisionError as exc:
        typer.secho(f"Error: {exc.detail}", fg=typer.colors.RED, err=True)
        for error in exc.errors:
            location = ".".join(str(part) for part in error.get("location", []))
            typer.secho(f"  {location}: {error.get('message')}", fg=typer.colors.YELLOW, err=True)
        raise typer.Exit(1) from exc


@app.command()
def version() -> None:
    """Print the CLI version."""
    typer.echo(f"curvevision {__version__}")


@app.command()
def login(
    url: Annotated[str, typer.Argument(help="Base URL of the CurveVision server")],
    username: Annotated[str | None, typer.Option(help="Username or email address")] = None,
    password: Annotated[str | None, typer.Option(help="Password (prompted if omitted)")] = None,
    token: Annotated[str | None, typer.Option(help="Use an existing cv_ API token")] = None,
) -> None:
    """Authenticate and store credentials.

    With ``--token`` the token is stored as-is. Otherwise a password login mints a
    long-lived API token, so the stored credential can be revoked individually rather
    than being a session that silently expires mid-job.
    """
    if token:
        save_config({"url": url.rstrip("/"), "token": token})
        typer.secho(f"Stored API token for {url}", fg=typer.colors.GREEN)
        return

    username = username or typer.prompt("Username or email")
    password = password or typer.prompt("Password", hide_input=True)

    def _login() -> str:
        session = CurveVision.login(url, username or "", password or "")
        minted = session.create_token(f"curvevision-cli@{os.uname().nodename}")
        session.close()
        return minted

    minted = handle(_login)
    save_config({"url": url.rstrip("/"), "token": minted})
    typer.secho(f"Logged in to {url} and stored an API token.", fg=typer.colors.GREEN)


@app.command()
def whoami() -> None:
    """Show the authenticated account."""
    with client() as cv:
        emit(handle(cv.whoami))


@app.command()
def health() -> None:
    """Check server health."""
    with client() as cv:
        emit(handle(cv.health))


@app.command()
def formats() -> None:
    """List dataset formats and what each can represent."""
    with client() as cv:
        for fmt in handle(cv.formats):
            directions = "/".join(
                d
                for d, on in (
                    ("import", fmt["supports_import"]),
                    ("export", fmt["supports_export"]),
                )
                if on
            )
            typer.echo(f"{fmt['id']:<14} {fmt['name']:<22} [{directions}]")
            typer.echo(f"{'':<14} shapes: {', '.join(fmt['shape_types'])}")
            if fmt.get("notes"):
                typer.echo(f"{'':<14} note:   {fmt['notes']}")


@app.command(name="organizations")
def list_organizations() -> None:
    """List organizations you belong to."""
    with client() as cv:
        for org in handle(cv.organizations):
            typer.echo(f"{org.id}  {org.slug:<20} {org.name}  ({org.raw.get('role')})")


@app.command(name="projects")
def list_projects(
    organization_id: Annotated[str | None, typer.Option(help="Filter by organization")] = None,
) -> None:
    """List projects."""
    params = {"organization_id": organization_id} if organization_id else {}
    with client() as cv:
        for project in handle(lambda: cv.projects(**params)):
            typer.echo(f"{project.id}  {project.slug:<24} {project.name}")


@app.command(name="stats")
def project_stats(project_id: str) -> None:
    """Show a project's annotation statistics and class distribution."""
    with client() as cv:
        emit(handle(lambda: cv.project_statistics(uuid.UUID(project_id))))


@task_app.command("create")
def task_create(
    project_id: str,
    name: Annotated[str, typer.Option(help="Task name")],
    segment_size: Annotated[int, typer.Option(help="Frames per job; 0 for one job")] = 0,
    upload: Annotated[
        list[Path] | None, typer.Option(help="Media files to upload immediately")
    ] = None,
) -> None:
    """Create a task and optionally upload its media."""
    with client() as cv:
        task = handle(
            lambda: cv.create_task(uuid.UUID(project_id), name=name, segment_size=segment_size)
        )
        typer.secho(f"Created task {task.id}", fg=typer.colors.GREEN)
        if upload:
            assets = handle(lambda: cv.upload(task.id, list(upload)))
            typer.echo(f"Uploaded {len(assets)} file(s)")
            refreshed = handle(lambda: cv.task(task.id))
            typer.echo(f"Task now has {refreshed.frame_count} frame(s)")


@task_app.command("list")
def task_list(
    project_id: Annotated[str | None, typer.Option(help="Filter by project")] = None,
) -> None:
    """List tasks."""
    params = {"project_id": project_id} if project_id else {}
    with client() as cv:
        for task in handle(lambda: cv.tasks(**params)):
            typer.echo(f"{task.id}  {task.status:<12} {task.frame_count:>7} frames  {task.name}")


@task_app.command("upload")
def task_upload(task_id: str, paths: list[Path]) -> None:
    """Upload media into an existing task."""
    with client() as cv:
        assets = handle(lambda: cv.upload(uuid.UUID(task_id), list(paths)))
        typer.secho(f"Uploaded {len(assets)} file(s)", fg=typer.colors.GREEN)


@task_app.command("progress")
def task_progress(task_id: str) -> None:
    """Show annotation progress for a task."""
    with client() as cv:
        progress = handle(lambda: cv.task_progress(uuid.UUID(task_id)))
        percent = progress["completion"] * 100
        typer.echo(
            f"{percent:5.1f}%  {progress['completed_frames']}/{progress['total_frames']} frames"
        )
        for state, count in progress["jobs_by_state"].items():
            if count:
                typer.echo(f"  {state:<14} {count}")


@job_app.command("list")
def job_list(
    task_id: Annotated[str | None, typer.Option(help="Jobs in this task")] = None,
    mine: Annotated[bool, typer.Option(help="Only jobs assigned to you")] = False,
) -> None:
    """List annotation jobs."""
    with client() as cv:
        jobs = handle(lambda: cv.jobs(uuid.UUID(task_id)) if task_id else cv.jobs(mine=mine))
        for job in jobs:
            typer.echo(
                f"{job.id}  #{job.index:<4} {job.state:<12} "
                f"frames {job.start_frame}-{job.stop_frame}"
            )


@job_app.command("annotations")
def job_annotations(job_id: str) -> None:
    """Print a job's annotations as JSON."""
    with client() as cv:
        emit(handle(lambda: cv.annotations(uuid.UUID(job_id))), as_json=True)


@job_app.command("review")
def job_review(
    job_id: str,
    accept: Annotated[bool, typer.Option("--accept/--reject")] = True,
    comment: Annotated[str | None, typer.Option(help="Explain a rejection")] = None,
) -> None:
    """Accept or reject a submitted job."""
    with client() as cv:
        job = handle(lambda: cv.review_job(uuid.UUID(job_id), accepted=accept, comment=comment))
        typer.secho(f"Job is now {job.state}", fg=typer.colors.GREEN)


@app.command()
def export(
    project_id: str,
    format: Annotated[str, typer.Option(help="coco | yolo | voc | curvevision")] = "coco",
    output: Annotated[Path, typer.Option(help="Where to write the archive")] = Path("dataset.zip"),
    only_accepted: Annotated[
        bool, typer.Option(help="Export only jobs that passed review")
    ] = False,
    include_images: Annotated[bool, typer.Option(help="Include media files")] = False,
) -> None:
    """Export a project's annotations."""
    with client() as cv:
        data = handle(
            lambda: cv.export(
                uuid.UUID(project_id),
                format=format,
                destination=output,
                only_accepted=only_accepted,
                include_images=include_images,
            )
        )
        typer.secho(f"Wrote {output} ({len(data):,} bytes)", fg=typer.colors.GREEN)


@app.command(name="import")
def import_annotations(
    task_id: str,
    archive: Path,
    format: Annotated[str, typer.Option(help="coco | yolo | voc | curvevision")] = "coco",
    replace: Annotated[bool, typer.Option(help="Discard existing annotations first")] = False,
) -> None:
    """Import annotations into a task."""
    with client() as cv:
        result = handle(
            lambda: cv.import_annotations(
                uuid.UUID(task_id),
                archive,
                format=format,
                conflict_policy="replace" if replace else "append",
            )
        )
        typer.secho(
            f"Imported {result['shapes_imported']} shape(s) across "
            f"{result['frames_matched']} frame(s)",
            fg=typer.colors.GREEN,
        )
        for warning in result.get("warnings", []):
            typer.secho(f"  warning: {warning}", fg=typer.colors.YELLOW)


def main() -> None:
    try:
        app()
    except KeyboardInterrupt:  # pragma: no cover - interactive only
        typer.secho("Interrupted", fg=typer.colors.YELLOW, err=True)
        sys.exit(130)


if __name__ == "__main__":
    main()
