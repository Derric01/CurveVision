"""Exports with images, assembled on disk rather than in memory.

An export of 10,000 photos is tens of gigabytes. It used to read every image into memory
and build the archive there too -- about twice the images' size in RAM, measured -- which
no laptop has. Now images are attached as files and streamed into an archive on disk, and
the archive is streamed back. `tests/benchmarks/test_ten_thousand_images.py` measures it at
scale; these pin the behaviour.
"""

from __future__ import annotations

import io
import uuid
import zipfile
from pathlib import Path
from typing import Any

import pytest

from curvevision.core.config import Settings
from curvevision.domain.project import Project
from curvevision.formats import FrameRecord, MemoryExportSink, ZipExportSink
from curvevision.services.datasets import build_dataset_view, export_dataset_file
from curvevision.storage import get_storage
from curvevision.storage.local import LocalStorage
from tests.conftest import ApiActor

Image = pytest.importorskip("PIL.Image")


def photo(index: int) -> bytes:
    buffer = io.BytesIO()
    Image.new("RGB", (40, 30), (index * 40 % 256, 20, 200)).save(buffer, format="PNG")
    return buffer.getvalue()


async def upload(owner: ApiActor, project: dict[str, Any], count: int) -> list[bytes]:
    task = (
        await owner.post("/api/v1/tasks", json={"project_id": project["id"], "name": "Photos"})
    ).json()
    images = [photo(i) for i in range(count)]
    response = await owner.post(
        f"/api/v1/tasks/{task['id']}/assets",
        files=[("files", (f"p{i}.png", data, "image/png")) for i, data in enumerate(images)],
    )
    assert response.is_success, response.text
    return images


async def test_images_are_attached_as_files_not_bytes(
    owner: ApiActor, project: dict[str, Any], sessionmaker_: Any, settings: Settings, tmp_path: Path
) -> None:
    await upload(owner, project, 3)

    async with sessionmaker_() as session:
        orm_project = await session.get(Project, uuid.UUID(project["id"]))
        view, _ = await build_dataset_view(
            session,
            orm_project,
            include_images=True,
            storage=get_storage(settings),
            media_dir=tmp_path,
        )
        frames = list(view)

    assert len(frames) == 3
    assert all(frame.media is None for frame in frames)  # nothing held in memory
    assert all(frame.media_path is not None and frame.media_path.is_file() for frame in frames)


async def test_the_archive_on_disk_carries_every_image_unchanged(
    owner: ApiActor, project: dict[str, Any], sessionmaker_: Any, settings: Settings, tmp_path: Path
) -> None:
    images = await upload(owner, project, 4)

    async with sessionmaker_() as session:
        orm_project = await session.get(Project, uuid.UUID(project["id"]))
        archive, warnings = await export_dataset_file(
            session,
            orm_project,
            workdir=tmp_path,
            format_id="coco",
            include_images=True,
            storage=get_storage(settings),
        )

    assert warnings == []
    with zipfile.ZipFile(archive) as opened:
        stored = {name: opened.read(name) for name in opened.namelist() if name.endswith(".png")}
        # Already compressed: stored, not deflated a second time.
        assert all(opened.getinfo(name).compress_type == zipfile.ZIP_STORED for name in stored)
    assert sorted(stored.values()) == sorted(images)


async def test_the_download_is_the_same_archive_and_leaves_nothing_behind(
    owner: ApiActor, project: dict[str, Any], settings: Settings, tmp_path: Path
) -> None:
    images = await upload(owner, project, 3)
    workdirs = tmp_path / "exports"
    settings.export_work_dir = str(workdirs)

    response = await owner.post(
        f"/api/v1/projects/{project['id']}/export", json={"format": "yolo", "include_images": True}
    )

    assert response.is_success, response.text
    assert response.headers["content-type"] == "application/zip"
    with zipfile.ZipFile(io.BytesIO(response.content)) as opened:
        stored = [opened.read(name) for name in opened.namelist() if name.endswith(".png")]
    assert sorted(stored) == sorted(images)
    assert list(workdirs.iterdir()) == []  # the working directory went once it was sent


def test_a_sink_writes_media_from_memory_or_from_disk_alike(tmp_path: Path) -> None:
    on_disk = tmp_path / "a.png"
    on_disk.write_bytes(b"from disk")
    frames = [
        FrameRecord(index=0, name="a.png", media_path=on_disk),
        FrameRecord(index=1, name="b.png", media=b"from memory"),
    ]

    memory = MemoryExportSink()
    archive = tmp_path / "out.zip"
    with ZipExportSink(archive) as sink:
        for frame in frames:
            sink.write_media(f"images/{frame.name}", frame)
            memory.write_media(f"images/{frame.name}", frame)
        assert sink.close() == b""  # the archive is the file

    with zipfile.ZipFile(archive) as opened:
        zipped = {name: opened.read(name) for name in opened.namelist()}
    assert (
        zipped
        == memory.files
        == {
            "images/a.png": b"from disk",
            "images/b.png": b"from memory",
        }
    )


async def test_local_storage_stores_a_file_without_reading_it_whole(tmp_path: Path) -> None:
    storage = LocalStorage(tmp_path / "store")
    source = tmp_path / "big.zip"
    source.write_bytes(b"x" * 10_000)

    stored = await storage.put_file("exports/big.zip", source, content_type="application/zip")

    assert stored.size == 10_000
    assert await storage.get("exports/big.zip") == source.read_bytes()
