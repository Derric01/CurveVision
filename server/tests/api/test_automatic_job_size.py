"""A large image task is split into jobs on its own; a video, or a chosen size, is left alone.

The web app never sets a job size, so every task used to be one job: 100,000 images became
one job whose editor opened ~70 MB of annotations. `tasks.AUTO_IMAGE_JOB_SIZE` splits an
image task that did not choose a size. These run with a size of 3 so they stay fast.
"""

from __future__ import annotations

import io
from typing import Any

import pytest

from curvevision.services import tasks as task_service
from tests.conftest import ApiActor

Image = pytest.importorskip("PIL.Image")


def photo(index: int) -> bytes:
    buffer = io.BytesIO()
    Image.new("RGB", (16, 12), (index * 30 % 256, 10, 10)).save(buffer, format="PNG")
    return buffer.getvalue()


@pytest.fixture(autouse=True)
def small_jobs(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(task_service, "AUTO_IMAGE_JOB_SIZE", 3)


async def task_with_images(
    owner: ApiActor, project: dict[str, Any], count: int, **settings: Any
) -> list[dict[str, Any]]:
    task = (
        await owner.post(
            "/api/v1/tasks", json={"project_id": project["id"], "name": "Batch", **settings}
        )
    ).json()
    response = await owner.post(
        f"/api/v1/tasks/{task['id']}/assets",
        files=[("files", (f"p{i}.png", photo(i), "image/png")) for i in range(count)],
    )
    assert response.is_success, response.text
    jobs = (await owner.get(f"/api/v1/tasks/{task['id']}/jobs")).json()
    return sorted(jobs, key=lambda job: job["start_frame"])


async def test_a_large_image_task_is_split_into_jobs(
    owner: ApiActor, project: dict[str, Any]
) -> None:
    jobs = await task_with_images(owner, project, 7)

    assert [(job["start_frame"], job["stop_frame"]) for job in jobs] == [(0, 2), (3, 5), (6, 6)]


async def test_a_task_within_the_size_stays_one_job(
    owner: ApiActor, project: dict[str, Any]
) -> None:
    jobs = await task_with_images(owner, project, 3)

    assert [(job["start_frame"], job["stop_frame"]) for job in jobs] == [(0, 2)]


async def test_a_job_size_the_task_chose_wins(owner: ApiActor, project: dict[str, Any]) -> None:
    jobs = await task_with_images(owner, project, 7, segment_size=5)

    assert [(job["start_frame"], job["stop_frame"]) for job in jobs] == [(0, 4), (5, 6)]


async def test_uploading_more_images_resplits_while_nothing_is_annotated(
    owner: ApiActor, project: dict[str, Any]
) -> None:
    task = (
        await owner.post("/api/v1/tasks", json={"project_id": project["id"], "name": "Grow"})
    ).json()
    for first in (0, 2, 4):
        response = await owner.post(
            f"/api/v1/tasks/{task['id']}/assets",
            files=[("files", (f"p{i}.png", photo(i), "image/png")) for i in (first, first + 1)],
        )
        assert response.is_success, response.text

    jobs = (await owner.get(f"/api/v1/tasks/{task['id']}/jobs")).json()
    assert sorted((job["start_frame"], job["stop_frame"]) for job in jobs) == [(0, 2), (3, 5)]
