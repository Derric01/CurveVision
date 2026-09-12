"""Correcting a video task's frame count by actually counting.

A video task is created with an *estimated* frame count, because an exact one means
decoding the whole file and that cannot happen inside an upload request. Where the
container declares a frame count the estimate is exact and there is nothing to do. Where it
does not -- Matroska is the everyday example -- the estimate becomes `int(duration x rate)`,
and truncation loses a frame:

    7 frames at 3 fps -> duration 2.333s -> int(2.333 * 3) = 6

A task that says 6 when there are 7 does not look broken. The last frame is simply never
offered to an annotator, never labelled, and never exported. That is the failure these
tests exist to prevent, and `make_matroska` reproduces it exactly rather than describing it.
"""

from __future__ import annotations

import io
from typing import Any

import pytest

from curvevision.core.config import Settings
from tests.conftest import ApiActor

av = pytest.importorskip("av", reason="video support is an optional extra")
pytest.importorskip("PIL", reason="frames are served as JPEG")

#: Chosen so the estimate is provably wrong: 7 / 3 fps truncates to 6.
FRAMES = 7
RATE = 3


def make_matroska(
    frames: int = FRAMES, rate: int = RATE, width: int = 64, height: int = 48
) -> bytes:
    """A Matroska clip, which declares no frame count and so forces the estimate."""
    from PIL import Image

    buffer = io.BytesIO()
    with av.open(buffer, mode="w", format="matroska") as container:
        stream = container.add_stream("mpeg4", rate=rate)
        stream.width, stream.height, stream.pix_fmt = width, height, "yuv420p"
        for index in range(frames):
            shade = (index * 31) % 256
            picture = Image.new("RGB", (width, height), (shade, 255 - shade, 90))
            for packet in stream.encode(av.VideoFrame.from_image(picture)):
                container.mux(packet)
        for packet in stream.encode():
            container.mux(packet)
    return buffer.getvalue()


async def video_task(actor: ApiActor, project: dict[str, Any], data: bytes) -> dict[str, Any]:
    created = await actor.post(
        "/api/v1/tasks",
        json={"project_id": project["id"], "name": "Footage", "media_kind": "video"},
    )
    assert created.status_code == 201, created.text
    task = created.json()
    uploaded = await actor.post(
        f"/api/v1/tasks/{task['id']}/assets",
        files=[("files", ("clip.mkv", data, "video/x-matroska"))],
    )
    assert uploaded.status_code == 201, uploaded.text
    return dict((await actor.get(f"/api/v1/tasks/{task['id']}")).json())


# ------------------------------------------------------- the estimate really is wrong


def test_the_estimate_this_corrects_is_genuinely_wrong() -> None:
    """Without this, every test below could pass while correcting nothing.

    If the estimate ever becomes exact for Matroska, this fails first and says so, rather
    than leaving a suite that asserts a fix nobody needs.
    """
    from curvevision.media.probe import _estimate_frame_count
    from curvevision.media.video import VideoReader

    clip = make_matroska()
    meta = VideoReader(clip).metadata(count_frames=False)
    estimate = _estimate_frame_count(clip, meta.duration_seconds, meta.frame_rate)

    assert VideoReader(clip).frame_count() == FRAMES
    assert estimate == FRAMES - 1, "the container stopped under-declaring; revisit this file"


# ------------------------------------------------------------------ the correction


async def test_an_uploaded_video_ends_up_with_its_real_frame_count(
    owner: ApiActor, project: dict[str, Any]
) -> None:
    task = await video_task(owner, project, make_matroska())
    assert task["frame_count"] == FRAMES


async def test_the_frame_the_estimate_lost_is_servable(
    owner: ApiActor, project: dict[str, Any]
) -> None:
    """The whole point, stated as the user meets it: the last frame exists."""
    task = await video_task(owner, project, make_matroska())

    last = await owner.get(f"/api/v1/tasks/{task['id']}/frames/{FRAMES - 1}/data")
    assert last.status_code == 200, last.text
    assert last.content.startswith(b"\xff\xd8\xff")

    # And nothing beyond it, so the correction did not overshoot in the other direction.
    beyond = await owner.get(f"/api/v1/tasks/{task['id']}/frames/{FRAMES}/data")
    assert beyond.status_code == 404


async def test_the_jobs_cover_the_corrected_range(owner: ApiActor, project: dict[str, Any]) -> None:
    """A corrected count is worthless if the jobs still partition the old one."""
    task = await video_task(owner, project, make_matroska())

    jobs = (await owner.get(f"/api/v1/tasks/{task['id']}/jobs")).json()
    assert jobs
    assert max(job["stop_frame"] for job in jobs) == FRAMES - 1


async def test_running_the_correction_again_changes_nothing(
    owner: ApiActor, project: dict[str, Any]
) -> None:
    """Idempotent: a retried job must not shift frame numbers under an annotator."""
    from curvevision.jobs.tasks import probe_task_media

    task = await video_task(owner, project, make_matroska())
    again = await probe_task_media({"task_id": task["id"]})

    assert again["corrected"] == []
    assert again["frame_count"] == FRAMES
    refreshed = (await owner.get(f"/api/v1/tasks/{task['id']}")).json()
    assert refreshed["frame_count"] == FRAMES


async def test_an_image_task_is_left_alone(owner: ApiActor, project: dict[str, Any]) -> None:
    from tests.api.test_workflow import png_bytes

    created = await owner.post(
        "/api/v1/tasks",
        json={"project_id": project["id"], "name": "Stills", "media_kind": "image"},
    )
    task = created.json()
    uploaded = await owner.post(
        f"/api/v1/tasks/{task['id']}/assets",
        files=[("files", ("a.png", png_bytes(), "image/png"))],
    )
    assert uploaded.status_code == 201

    from curvevision.jobs.tasks import probe_task_media

    result = await probe_task_media({"task_id": task["id"]})
    assert result["corrected"] == []
    assert result["frame_count"] == 1


# ------------------------------------------------------- what it refuses to trample


async def test_a_task_with_annotations_keeps_its_estimate(
    owner: ApiActor, project: dict[str, Any], session: Any
) -> None:
    """Correctness has a limit: somebody's work.

    Repartitioning a task's frames once annotations exist would orphan them. The job
    declines the whole correction rather than applying half of it, and says so, so the
    result is a task that is wrong in a known, reported way rather than one that is
    quietly inconsistent.
    """
    from sqlalchemy import select

    from curvevision.domain.media import Asset
    from curvevision.domain.task import Job, Task
    from curvevision.jobs.tasks import probe_task_media

    task = await video_task(owner, project, make_matroska())

    # Put the task back into the state it is in before the job runs, then give it work.
    row = await session.get(Task, task["id"])
    asset = (await session.execute(select(Asset).where(Asset.task_id == row.id))).scalar_one()
    asset.frame_count = FRAMES - 1
    row.frame_count = FRAMES - 1
    job = (await session.execute(select(Job).where(Job.task_id == row.id))).scalars().first()
    job.shape_count = 3  # somebody has drawn on this
    await session.commit()

    result = await probe_task_media({"task_id": task["id"]})

    assert "skipped" in result
    assert result["frame_count"] == FRAMES - 1
    refreshed = (await owner.get(f"/api/v1/tasks/{task['id']}")).json()
    assert refreshed["frame_count"] == FRAMES - 1, "the estimate must survive intact"


async def test_correcting_the_count_discards_the_stale_chunks(
    owner: ApiActor, project: dict[str, Any], session: Any, settings: Settings
) -> None:
    """Chunks are addressed by frame range, so a renumbering invalidates them."""
    from sqlalchemy import func, select

    from curvevision.domain.media import Asset, MediaChunk
    from curvevision.domain.task import Task
    from curvevision.jobs.tasks import probe_task_media

    settings.frames_per_chunk = 4
    task = await video_task(owner, project, make_matroska())

    # Build a chunk, then put the task back to its pre-correction state so the job has
    # something to correct and something to invalidate.
    assert (await owner.get(f"/api/v1/tasks/{task['id']}/chunks/0")).status_code == 200

    async def chunks() -> int:
        return int(
            (
                await session.execute(
                    select(func.count())
                    .select_from(MediaChunk)
                    .where(MediaChunk.task_id == task["id"])
                )
            ).scalar_one()
        )

    assert await chunks() >= 1

    row = await session.get(Task, task["id"])
    asset = (await session.execute(select(Asset).where(Asset.task_id == row.id))).scalar_one()
    asset.frame_count = FRAMES - 1
    row.frame_count = FRAMES - 1
    await session.commit()

    result = await probe_task_media({"task_id": task["id"]})

    assert result["corrected"], "the job should have found something to correct"
    assert result["chunks_discarded"] >= 1
    assert await chunks() == 0, "a stale chunk must not survive a renumbering"
