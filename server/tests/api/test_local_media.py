"""Annotating files where they already live.

The desktop application's normal path: a person points at a folder on their disk and
starts drawing. Nothing is copied, so these tests assert both halves of that promise --
that the frames really are served, and that the bytes really were not duplicated.

They also assert the other half of the design: that none of it exists on a shared server,
where a path in a request body would name a file on the *server's* disk.
"""

from __future__ import annotations

import io
import zipfile
from pathlib import Path
from typing import Any

import httpx
import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from curvevision.core.config import Settings
from curvevision.domain.media import Asset, MediaBlob
from tests.conftest import ApiActor

pytest.importorskip("PIL", reason="image dimensions are needed to exercise the media path")


def png_bytes(width: int = 640, height: int = 480, colour: str = "#1d4ed8") -> bytes:
    from PIL import Image

    buffer = io.BytesIO()
    Image.new("RGB", (width, height), colour).save(buffer, format="PNG")
    return buffer.getvalue()


@pytest.fixture
def local_settings(settings: Settings) -> Settings:
    """The same test configuration, but as a desktop installation."""
    return settings.model_copy(update={"local_mode": True})


@pytest.fixture
def photo_folder(tmp_path: Path) -> Path:
    """A folder shaped like something on a real disk: nested, with noise in it."""
    root = tmp_path / "Pictures" / "survey"
    (root / "nested").mkdir(parents=True)

    (root / "b_second.png").write_bytes(png_bytes(colour="#059669"))
    (root / "a_first.png").write_bytes(png_bytes(colour="#dc2626"))
    (root / "nested" / "c_third.png").write_bytes(png_bytes(colour="#7c3aed"))
    # Not media, and not somebody's dataset.
    (root / "notes.txt").write_text("field notes")
    (root / ".DS_Store").write_bytes(b"\x00\x01")
    return root


async def make_task(actor: ApiActor, project: dict[str, Any]) -> dict[str, Any]:
    created = await actor.post(
        "/api/v1/tasks",
        json={"project_id": project["id"], "name": "On disk", "media_kind": "image"},
    )
    assert created.status_code == 201, created.text
    return dict(created.json())


# --------------------------------------------------------------------------- the gate


async def test_local_import_does_not_exist_on_a_shared_server(
    owner: ApiActor, project: dict[str, Any], tmp_path: Path
) -> None:
    """Without local mode the route is not merely forbidden, it is absent.

    A path in a request body names a file on the server's disk. An operator who never
    turns on local mode should not have this in their attack surface at all.
    """
    task = await make_task(owner, project)
    response = await owner.post(
        f"/api/v1/tasks/{task['id']}/local-import", json={"path": str(tmp_path)}
    )
    assert response.status_code == 404


# ------------------------------------------------------------------ importing in place


@pytest.fixture
async def local_app(
    local_settings: Settings, sessionmaker_: async_sessionmaker[AsyncSession]
) -> Any:
    """The application as a desktop installation, sharing the test database."""
    from curvevision.core import db as db_module
    from curvevision.core.config import configure_settings
    from curvevision.core.db import session_scope
    from curvevision.jobs import InlineJobQueue, set_job_queue
    from curvevision.main import create_app

    application = create_app(local_settings)

    async def override_session() -> Any:
        async with sessionmaker_() as active:
            yield active

    application.dependency_overrides[session_scope] = override_session
    configure_settings(local_settings)
    db_module._sessionmaker = sessionmaker_
    set_job_queue(InlineJobQueue(wait=True))

    yield application

    set_job_queue(None)
    db_module._sessionmaker = None
    configure_settings(None)
    application.dependency_overrides.clear()


@pytest.fixture
async def local_owner(local_app: Any) -> ApiActor:
    from tests.conftest import register

    transport = httpx.ASGITransport(app=local_app)
    async with httpx.AsyncClient(transport=transport, base_url="http://desktop") as http_client:
        import curvevision.formats
        import curvevision.jobs.tasks
        import curvevision.ml  # noqa: F401

        yield await register(http_client, "desktop-user")


@pytest.fixture
async def local_project(local_owner: ApiActor) -> dict[str, Any]:
    organization = await local_owner.post(
        "/api/v1/organizations", json={"slug": "local", "name": "My Workspace"}
    )
    assert organization.status_code == 201, organization.text
    created = await local_owner.post(
        "/api/v1/projects",
        json={
            "organization_id": organization.json()["id"],
            "slug": "survey",
            "name": "Survey",
            "labels": [{"name": "bird", "color": "#f59e0b"}],
        },
    )
    assert created.status_code == 201, created.text
    return dict(created.json())


async def test_a_folder_is_annotatable_without_copying_anything(
    local_owner: ApiActor,
    local_project: dict[str, Any],
    photo_folder: Path,
    local_settings: Settings,
    sessionmaker_: async_sessionmaker[AsyncSession],
) -> None:
    task = await make_task(local_owner, local_project)

    response = await local_owner.post(
        f"/api/v1/tasks/{task['id']}/local-import", json={"path": str(photo_folder)}
    )
    assert response.status_code == 200, response.text
    body = response.json()

    # Sorted by path, so the same folder always yields the same frame numbers.
    assert [asset["name"] for asset in body["imported"]] == [
        "a_first.png",
        "b_second.png",
        "c_third.png",
    ]
    assert body["frame_count"] == 3
    assert body["skipped"] == []

    # Nothing was written into the application's storage directory.
    storage_root = Path(local_settings.storage_local_root)
    assert not storage_root.exists() or not any(storage_root.rglob("*.png"))

    async with sessionmaker_() as session:
        blobs = (await session.execute(select(MediaBlob))).scalars().all()
        assert len(blobs) == 3
        for blob in blobs:
            assert blob.storage_key is None
            assert blob.source_path is not None
            assert Path(blob.source_path).is_file()
            # Probed from the real file, not guessed.
            assert (blob.width, blob.height) == (640, 480)


async def test_frames_are_served_from_the_original_file(
    local_owner: ApiActor, local_project: dict[str, Any], photo_folder: Path
) -> None:
    task = await make_task(local_owner, local_project)
    await local_owner.post(
        f"/api/v1/tasks/{task['id']}/local-import", json={"path": str(photo_folder)}
    )

    for frame, filename in enumerate(["a_first.png", "b_second.png", "nested/c_third.png"]):
        served = await local_owner.get(f"/api/v1/tasks/{task['id']}/frames/{frame}/data")
        assert served.status_code == 200, served.text
        assert served.content == (photo_folder / filename).read_bytes()

    info = await local_owner.get(f"/api/v1/tasks/{task['id']}/frames/0")
    assert info.status_code == 200
    # No thumbnail is generated: that would be a derived copy of a file we were asked not
    # to copy.
    assert info.json()["thumbnail_url"] is None


async def test_a_single_file_can_be_imported(
    local_owner: ApiActor, local_project: dict[str, Any], photo_folder: Path
) -> None:
    task = await make_task(local_owner, local_project)
    response = await local_owner.post(
        f"/api/v1/tasks/{task['id']}/local-import",
        json={"path": str(photo_folder / "a_first.png")},
    )
    assert response.status_code == 200, response.text
    assert [asset["name"] for asset in response.json()["imported"]] == ["a_first.png"]


async def test_a_non_recursive_import_stays_in_the_top_folder(
    local_owner: ApiActor, local_project: dict[str, Any], photo_folder: Path
) -> None:
    task = await make_task(local_owner, local_project)
    response = await local_owner.post(
        f"/api/v1/tasks/{task['id']}/local-import",
        json={"path": str(photo_folder), "recursive": False},
    )
    assert response.status_code == 200, response.text
    assert [asset["name"] for asset in response.json()["imported"]] == [
        "a_first.png",
        "b_second.png",
    ]


async def test_a_missing_path_is_a_validation_error_not_a_crash(
    local_owner: ApiActor, local_project: dict[str, Any], tmp_path: Path
) -> None:
    task = await make_task(local_owner, local_project)
    response = await local_owner.post(
        f"/api/v1/tasks/{task['id']}/local-import", json={"path": str(tmp_path / "nowhere")}
    )
    assert response.status_code == 422
    assert "does not exist" in response.text


async def test_one_unreadable_file_does_not_lose_the_rest(
    local_owner: ApiActor, local_project: dict[str, Any], photo_folder: Path
) -> None:
    """A folder of 3,000 photographs with one corrupt file yields 2,999 frames."""
    (photo_folder / "truncated.png").write_bytes(b"\x89PNG\r\n\x1a\n-not-really-an-image")
    task = await make_task(local_owner, local_project)

    response = await local_owner.post(
        f"/api/v1/tasks/{task['id']}/local-import", json={"path": str(photo_folder)}
    )
    assert response.status_code == 200, response.text
    body = response.json()

    assert len(body["imported"]) == 3
    assert len(body["skipped"]) == 1
    assert "truncated.png" in body["skipped"][0]


async def test_a_file_the_user_moved_reports_a_missing_frame_not_a_server_error(
    local_owner: ApiActor, local_project: dict[str, Any], photo_folder: Path
) -> None:
    """The file belongs to the user. They may delete it, and we must say so plainly."""
    task = await make_task(local_owner, local_project)
    await local_owner.post(
        f"/api/v1/tasks/{task['id']}/local-import",
        json={"path": str(photo_folder / "a_first.png")},
    )
    (photo_folder / "a_first.png").unlink()

    served = await local_owner.get(f"/api/v1/tasks/{task['id']}/frames/0/data")
    assert served.status_code == 404


async def test_exporting_with_images_reads_them_from_disk(
    local_owner: ApiActor, local_project: dict[str, Any], photo_folder: Path
) -> None:
    """An export must contain the pictures even though they were never uploaded."""
    task = await make_task(local_owner, local_project)
    await local_owner.post(
        f"/api/v1/tasks/{task['id']}/local-import", json={"path": str(photo_folder)}
    )

    export = await local_owner.post(
        f"/api/v1/projects/{local_project['id']}/export",
        json={"format": "coco", "include_images": True},
    )
    assert export.status_code == 200, export.text

    with zipfile.ZipFile(io.BytesIO(export.content)) as archive:
        images = [name for name in archive.namelist() if name.endswith(".png")]
        assert len(images) == 3
        assert archive.read(images[0])


async def test_the_garbage_collector_never_deletes_the_user_s_files(
    local_owner: ApiActor,
    local_project: dict[str, Any],
    photo_folder: Path,
    sessionmaker_: async_sessionmaker[AsyncSession],
) -> None:
    """Forgetting the row is right. Deleting somebody's photograph is not."""
    from curvevision.jobs.tasks import reclaim_orphan_blobs

    task = await make_task(local_owner, local_project)
    await local_owner.post(
        f"/api/v1/tasks/{task['id']}/local-import", json={"path": str(photo_folder)}
    )

    deleted = await local_owner.delete(f"/api/v1/tasks/{task['id']}")
    assert deleted.status_code == 204

    async with sessionmaker_() as session:
        assert (await session.execute(select(Asset))).scalars().all() == []

    result = await reclaim_orphan_blobs({})
    assert result["blobs_removed"] == 3

    for name in ("a_first.png", "b_second.png", "nested/c_third.png"):
        assert (photo_folder / name).is_file()


async def test_health_advertises_local_mode(local_owner: ApiActor) -> None:
    """The web client reads this to skip the sign-in screen."""
    response = await local_owner.get("/api/v1/health")
    assert response.status_code == 200
    assert response.json()["local_mode"] is True
