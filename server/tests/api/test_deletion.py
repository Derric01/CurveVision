"""Deleting a task or a project takes its stored media with it — but never media another
task still uses.

Until iteration 66 the job that removes unreferenced media existed and nothing queued it:
every deleted task's images stayed on disk for good.
"""

from __future__ import annotations

import io
from pathlib import Path
from typing import Any

import pytest

from curvevision.core.config import Settings
from tests.conftest import ApiActor

pytest.importorskip("PIL", reason="uploads are probed for their dimensions")


def png(colour: str) -> bytes:
    from PIL import Image

    buffer = io.BytesIO()
    Image.new("RGB", (64, 48), colour).save(buffer, format="PNG")
    return buffer.getvalue()


def stored(settings: Settings) -> set[str]:
    root = Path(settings.storage_local_root)
    return {str(p.relative_to(root)) for p in root.rglob("*") if p.is_file()}


async def task_with(actor: ApiActor, project: dict[str, Any], *images: bytes) -> dict[str, Any]:
    task = (
        await actor.post("/api/v1/tasks", json={"project_id": project["id"], "name": "Batch"})
    ).json()
    response = await actor.post(
        f"/api/v1/tasks/{task['id']}/assets",
        files=[("files", (f"{i}.png", data, "image/png")) for i, data in enumerate(images)],
    )
    assert response.status_code == 201, response.text
    return dict(task)


class TestDeletingReclaimsMedia:
    async def test_deleting_a_task_removes_its_images(
        self, owner: ApiActor, project: dict[str, Any], settings: Settings
    ) -> None:
        task = await task_with(owner, project, png("red"))
        assert stored(settings)  # the image and its thumbnail

        assert (await owner.delete(f"/api/v1/tasks/{task['id']}")).status_code == 204

        assert stored(settings) == set()

    async def test_an_image_another_task_uses_stays(
        self, owner: ApiActor, project: dict[str, Any], settings: Settings
    ) -> None:
        shared = png("blue")
        first = await task_with(owner, project, shared, png("green"))
        await task_with(owner, project, shared)
        before = stored(settings)

        await owner.delete(f"/api/v1/tasks/{first['id']}")

        after = stored(settings)
        assert after and after < before  # green went; blue, still used, did not
        frame = await owner.get(f"/api/v1/tasks/{first['id']}")
        assert frame.status_code == 404

    async def test_deleting_a_project_removes_its_tasks_images(
        self, owner: ApiActor, project: dict[str, Any], settings: Settings
    ) -> None:
        await task_with(owner, project, png("yellow"))
        await task_with(owner, project, png("purple"))

        assert (await owner.delete(f"/api/v1/projects/{project['id']}")).status_code == 204

        assert stored(settings) == set()
