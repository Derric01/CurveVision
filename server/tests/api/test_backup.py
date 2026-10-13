"""A backup of a real installation, restored into another, read back through the API.

`curvevision-server backup` / `restore` (`services/backup.py`), iteration 79. The source is
a migrated desktop installation with one of most things in it — a skeleton label whose
joints refer to it, images, a shape, a track, a tagged frame, a released dataset version
with its frozen copy — and the check is what a person would check: that the restored
installation serves the same projects, the same annotations, the same image bytes, and
exports the released version.

The target is SQLite, and PostgreSQL too when `CURVEVISION_TEST_POSTGRES_URL` names one —
a backup written from SQLite restoring there is the desktop-to-team-server move.
"""

from __future__ import annotations

import asyncio
import io
import json
import os
import tarfile
from pathlib import Path
from typing import Any

import httpx
import pytest
import sqlalchemy as sa

from curvevision import desktop
from curvevision.core import db as db_module
from curvevision.core.config import Settings, configure_settings
from curvevision.services import backup
from curvevision.storage import get_storage, reset_storage_cache

pytest.importorskip("PIL", reason="frames need real images")

POSTGRES = os.environ.get("CURVEVISION_TEST_POSTGRES_URL")


def png(colour: str) -> bytes:
    from PIL import Image

    buffer = io.BytesIO()
    Image.new("RGB", (64, 48), colour).save(buffer, format="PNG")
    return buffer.getvalue()


async def with_api(settings: Settings, token: str, work: Any) -> Any:
    from curvevision.main import create_app

    app = create_app(settings)
    async with (
        httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app),
            base_url="http://desktop",
            headers={"Authorization": f"Bearer {token}"},
        ) as client,
        app.router.lifespan_context(app),
    ):
        result = await work(client)
    await db_module.dispose_engine()
    reset_storage_cache()
    return result


async def seed(client: httpx.AsyncClient) -> dict[str, Any]:
    workspace = (await client.get("/api/v1/organizations")).json()[0]
    project = (
        await client.post(
            "/api/v1/projects",
            json={
                "organization_id": workspace["id"],
                "slug": "street",
                "name": "Street",
                "labels": [
                    {"name": "person", "children": [{"name": "nose"}, {"name": "tail"}]},
                    {
                        "name": "car",
                        "attributes": [
                            {"name": "colour", "attribute_type": "select", "values": ["red"]}
                        ],
                    },
                    {"name": "daytime"},
                ],
            },
        )
    ).json()
    labels = {label["name"]: label for label in project["labels"]}
    task = (
        await client.post("/api/v1/tasks", json={"project_id": project["id"], "name": "Clip"})
    ).json()
    await client.post(
        f"/api/v1/tasks/{task['id']}/assets",
        files=[
            ("files", ("a.png", png("#ef4444"), "image/png")),
            ("files", ("b.png", png("#22c55e"), "image/png")),
        ],
    )
    job = (await client.get(f"/api/v1/tasks/{task['id']}/jobs")).json()[0]
    written = await client.patch(
        f"/api/v1/jobs/{job['id']}/annotations",
        json={
            "created_shapes": [
                {
                    "label_id": labels["car"]["id"],
                    "frame": 0,
                    "shape_type": "rectangle",
                    "points": [1, 2, 30, 40],
                    "attributes": {"colour": "red"},
                    "source": "model",
                    "confidence": 0.75,
                }
            ],
            "created_tracks": [
                {
                    "label_id": labels["car"]["id"],
                    "shape_type": "rectangle",
                    "shapes": [
                        {"frame": 0, "shape_type": "rectangle", "points": [5, 5, 20, 20]},
                        {"frame": 1, "shape_type": "rectangle", "points": [9, 5, 24, 20]},
                    ],
                }
            ],
            "created_tags": [{"label_id": labels["daytime"]["id"], "frame": 1}],
        },
    )
    assert written.status_code == 200, written.text
    version = (
        await client.post(f"/api/v1/projects/{project['id']}/versions", json={"name": "v1"})
    ).json()
    released = await client.post(
        f"/api/v1/projects/{project['id']}/versions/{version['id']}/release"
    )
    assert released.status_code == 200, released.text
    return {"project": project, "task": task, "job": job, "version": version}


def _listed(body: Any) -> list[dict[str, Any]]:
    return list(body["results"] if isinstance(body, dict) else body)


async def snapshot(client: httpx.AsyncClient, seeded: dict[str, Any]) -> dict[str, Any]:
    """What a person would compare: projects, labels, annotations, frames, a release."""
    project_id, task_id = seeded["project"]["id"], seeded["task"]["id"]
    document = (await client.get(f"/api/v1/jobs/{seeded['job']['id']}/annotations")).json()
    released = await client.post(
        f"/api/v1/projects/{project_id}/versions/{seeded['version']['id']}/export",
        json={"format": "curvevision"},
    )
    assert released.status_code == 200, released.text
    return {
        "projects": sorted(p["id"] for p in _listed((await client.get("/api/v1/projects")).json())),
        "labels": (await client.get(f"/api/v1/projects/{project_id}/labels")).json(),
        "shapes": document["shapes"],
        "tracks": document["tracks"],
        "tags": document["tags"],
        "frames": [
            (await client.get(f"/api/v1/tasks/{task_id}/frames/{index}/data")).content
            for index in range(2)
        ],
        "release": "exported",
    }


async def migrated(settings: Settings) -> Settings:
    configure_settings(settings)
    await asyncio.to_thread(desktop.migrate, settings)
    return settings


@pytest.fixture
async def source(tmp_path: Path) -> Any:
    settings, token = await asyncio.to_thread(desktop.bootstrap, tmp_path / "source")
    seeded = await with_api(settings, token, seed)
    before = await with_api(settings, token, lambda c: snapshot(c, seeded))
    archive = tmp_path / "backup.tar"
    engine = db_module.create_engine(settings)
    report = await backup.write_backup(engine, get_storage(settings), archive)
    await engine.dispose()
    reset_storage_cache()
    yield archive, report, seeded, before
    configure_settings(None)


@pytest.fixture(params=["sqlite", "postgresql"])
async def target(request: pytest.FixtureRequest, tmp_path: Path) -> Settings:
    if request.param == "sqlite":
        return await migrated(desktop.local_settings(tmp_path / "target"))
    if not POSTGRES:
        pytest.skip("set CURVEVISION_TEST_POSTGRES_URL to restore into PostgreSQL")
    settings = desktop.local_settings(tmp_path / "target").model_copy(
        update={"database_url": POSTGRES}
    )
    url = sa.make_url(POSTGRES).set(drivername="postgresql+psycopg")
    engine = sa.create_engine(url)
    with engine.begin() as connection:
        connection.execute(sa.text("DROP SCHEMA public CASCADE"))
        connection.execute(sa.text("CREATE SCHEMA public"))
    engine.dispose()
    return await migrated(settings)


async def restore_into(settings: Settings, archive: Path) -> backup.BackupReport:
    engine = db_module.create_engine(settings)
    try:
        return await backup.restore_backup(engine, get_storage(settings), archive)
    finally:
        await engine.dispose()
        reset_storage_cache()


async def test_a_restored_installation_serves_what_the_backup_held(
    source: Any, target: Settings
) -> None:
    archive, report, seeded, before = source
    assert report.objects >= 3  # two images, the release's frozen copy (and thumbnails)
    assert report.missing == []

    restored = await restore_into(target, archive)

    assert restored.tables == report.tables
    token = await desktop.provision(target)
    after = await with_api(target, token, lambda c: snapshot(c, seeded))
    assert after == before
    # A pending suggestion is still one: provenance is rows, and rows came back whole.
    [shape] = after["shapes"]
    assert (shape["source"], shape["confidence"]) == ("model", 0.75)


async def test_an_installation_that_is_not_empty_is_refused(source: Any, tmp_path: Path) -> None:
    archive, *_ = source
    settings, _token = await asyncio.to_thread(desktop.bootstrap, tmp_path / "in-use")

    with pytest.raises(backup.BackupError, match="not empty"):
        await restore_into(settings, archive)


async def test_another_schema_revision_is_refused(source: Any, tmp_path: Path) -> None:
    from alembic import command

    archive, *_ = source
    settings = await migrated(desktop.local_settings(tmp_path / "older"))
    await asyncio.to_thread(command.downgrade, desktop.alembic_config(settings), "-1")

    with pytest.raises(backup.BackupError, match="schema revision"):
        await restore_into(settings, archive)


async def test_a_damaged_object_is_refused(source: Any, tmp_path: Path) -> None:
    archive, *_ = source
    damaged = tmp_path / "damaged.tar"
    with tarfile.open(archive) as original, tarfile.open(damaged, "w") as copy:
        flipped = False
        for member in original:
            data = original.extractfile(member).read() if member.isfile() else b""  # type: ignore[union-attr]
            if member.name.startswith("storage/") and not flipped:
                data = bytes([data[0] ^ 0xFF]) + data[1:]
                flipped = True
            member.size = len(data)
            copy.addfile(member, io.BytesIO(data))
    target = await migrated(desktop.local_settings(tmp_path / "target"))

    with pytest.raises(backup.BackupError, match="damaged"):
        await restore_into(target, damaged)


def test_something_else_is_not_a_backup(tmp_path: Path) -> None:
    other = tmp_path / "other.tar"
    with tarfile.open(other, "w") as archive:
        payload = json.dumps({"format": "something-else"}).encode()
        info = tarfile.TarInfo("manifest.json")
        info.size = len(payload)
        archive.addfile(info, io.BytesIO(payload))

    with pytest.raises(backup.BackupError, match="not a CurveVision backup"):
        backup.read_manifest(other)


async def test_the_commands_back_up_and_restore_a_desktop_installation(
    source: Any, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """What a person types: `curvevision-server backup --desktop DIR` and `restore`."""
    from curvevision import cli

    _archive, _report, seeded, before = source
    written = tmp_path / "by-command.tar"

    assert (
        await asyncio.to_thread(
            cli.main, ["backup", str(written), "--desktop", str(tmp_path / "source")]
        )
        == 0
    )
    assert (
        await asyncio.to_thread(
            cli.main, ["restore", str(written), "--desktop", str(tmp_path / "restored")]
        )
        == 0
    )
    assert (
        await asyncio.to_thread(
            cli.main, ["restore", str(written), "--desktop", str(tmp_path / "restored")]
        )
        == 1
    )  # a second time: not empty any more

    said = capsys.readouterr()
    assert "Backed up" in said.out and "Restored" in said.out
    assert "not empty" in said.err
    restored = desktop.local_settings(tmp_path / "restored")
    configure_settings(restored)
    token = await desktop.provision(restored)
    assert await with_api(restored, token, lambda c: snapshot(c, seeded)) == before


def test_the_command_works_in_a_process_of_its_own(source: Any, tmp_path: Path) -> None:
    """As an operator runs it: a fresh interpreter, which has imported no model yet.

    In the test process every model is already registered, so the in-process test above
    could not see that `backup` looked tables up in a metadata only partly filled — every
    real `curvevision-server backup` failed with `KeyError: 'media_blobs'` until iteration 85,
    found by the first CI job to run it inside a deployment.
    """
    import subprocess
    import sys

    written = tmp_path / "fresh.tar"
    done = subprocess.run(
        [
            sys.executable,
            "-c",
            "import sys; from curvevision import cli; sys.exit(cli.main(sys.argv[1:]))",
            "backup",
            str(written),
            "--desktop",
            str(tmp_path / "source"),
        ],
        capture_output=True,
        text=True,
        timeout=120,
        check=False,
    )

    assert done.returncode == 0, done.stderr
    assert "Backed up" in done.stdout
    assert backup.read_manifest(written)["format"]
