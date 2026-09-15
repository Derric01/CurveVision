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
import uuid
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
    asset.frame_count_exact = False  # an asset already marked exact is skipped
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
    asset.frame_count_exact = False  # an asset already marked exact is skipped
    row.frame_count = FRAMES - 1
    await session.commit()

    stale = {
        (chunk.id, chunk.start_frame, chunk.frame_count)
        for chunk in (
            await session.execute(select(MediaChunk).where(MediaChunk.task_id == task["id"]))
        ).scalars()
    }
    assert stale

    result = await probe_task_media({"task_id": task["id"]})

    assert result["corrected"], "the job should have found something to correct"
    assert result["chunks_discarded"] >= 1

    # Every chunk addressed by the old numbering is gone. This used to assert the count
    # reached zero, which was only true while nothing rebuilt them; the probe now chains a
    # build, so the claim has to be about identity rather than absence -- otherwise the
    # rebuild would make the test pass for the wrong reason.
    surviving = {
        (chunk.id, chunk.start_frame, chunk.frame_count)
        for chunk in (
            await session.execute(select(MediaChunk).where(MediaChunk.task_id == task["id"]))
        ).scalars()
    }
    assert stale & surviving == set(), "a stale chunk survived a renumbering"


# --------------------------------------------------- chunks are built ahead of the reader


async def test_uploading_a_video_builds_its_chunks_without_anybody_opening_it(
    owner: ApiActor, project: dict[str, Any], session: Any, settings: Settings
) -> None:
    """The first annotator should not be the one who pays for the decode.

    `media.build_chunks` existed and nothing enqueued it, so a chunk was only ever built by
    the request that first asked for one — which is the request a person is waiting on.
    Probing now chains the build once the frame numbering is settled.
    """
    from sqlalchemy import func, select

    from curvevision.domain.media import MediaChunk

    settings.frames_per_chunk = 4
    task = await video_task(owner, project, make_matroska())

    built = int(
        (
            await session.execute(
                select(func.count()).select_from(MediaChunk).where(MediaChunk.task_id == task["id"])
            )
        ).scalar_one()
    )
    # 7 frames at 4 per chunk is two chunks, and nothing in this test has requested a frame.
    assert built == 2, f"expected the upload to leave 2 chunks built, found {built}"


async def test_the_build_is_queued_after_the_count_is_settled_not_before(
    owner: ApiActor, project: dict[str, Any], session: Any, settings: Settings
) -> None:
    """Ordering is the whole correctness argument, so it is asserted rather than assumed.

    A chunk is addressed by the task's frame numbering. Queueing the build before
    `correct_frame_counts` has run would decode frames into chunks that the same job then
    discards — wasted work, and briefly a task whose chunks and frame count disagree.
    """
    from sqlalchemy import func, select

    from curvevision.domain.media import Asset, MediaChunk
    from curvevision.domain.task import Task
    from curvevision.jobs.tasks import probe_task_media

    settings.frames_per_chunk = 4
    task = await video_task(owner, project, make_matroska())

    # Put the task back to its pre-correction state, so probing has something to correct.
    row = await session.get(Task, task["id"])
    asset = (await session.execute(select(Asset).where(Asset.task_id == row.id))).scalar_one()
    asset.frame_count = FRAMES - 1
    asset.frame_count_exact = False  # an asset already marked exact is skipped
    row.frame_count = FRAMES - 1
    await session.commit()

    result = await probe_task_media({"task_id": task["id"]})

    assert result["corrected"], "the job should have found something to correct"
    assert result["chunk_build_task_id"], "the corrected task should have a build queued"

    # The surviving chunks cover the corrected range: the last frame the estimate lost is
    # inside a chunk, which is only true if the build ran against the final numbering.
    chunks = list(
        (
            await session.execute(select(MediaChunk).where(MediaChunk.task_id == task["id"]))
        ).scalars()
    )
    assert chunks, "the chained build produced nothing"
    last = max(chunk.start_frame + chunk.frame_count - 1 for chunk in chunks)
    assert last == FRAMES - 1, (
        f"the chunks stop at frame {last}, short of the corrected count, so they were "
        "built against the old numbering"
    )

    counted = int(
        (
            await session.execute(
                select(func.count()).select_from(MediaChunk).where(MediaChunk.task_id == task["id"])
            )
        ).scalar_one()
    )
    assert counted == 2


async def test_an_image_task_queues_no_chunk_build(
    owner: ApiActor, project: dict[str, Any]
) -> None:
    """Images already serve in constant time; the job would be a no-op loop over every index."""
    from curvevision.jobs.tasks import probe_task_media
    from tests.api.test_workflow import png_bytes

    created = await owner.post(
        "/api/v1/tasks",
        json={"project_id": project["id"], "name": "Stills", "media_kind": "image"},
    )
    task = created.json()
    await owner.post(
        f"/api/v1/tasks/{task['id']}/assets",
        files=[("files", ("a.png", png_bytes(), "image/png"))],
    )

    result = await probe_task_media({"task_id": task["id"]})

    assert result.get("chunk_build_task_id") is None


async def test_probing_twice_still_leaves_the_chunks_built(
    owner: ApiActor, project: dict[str, Any], session: Any, settings: Settings
) -> None:
    """The invariant is *the chunks exist afterwards*, not *only one build was queued*.

    This test was originally written the other way round — asserting that a second probe
    reused the first build's job row, deduplicated on task and frame count. That key is
    wrong, and wrong in the direction that loses data: a probe which **discards** the chunks
    and then re-enqueues would dedupe against the earlier, already-succeeded job, so the
    build would never run and the task would be left serving no chunks at all. The key is
    gone, and this asserts what a user would notice instead.
    """
    from sqlalchemy import func, select

    from curvevision.domain.media import MediaChunk

    settings.frames_per_chunk = 4
    task = await video_task(owner, project, make_matroska())

    await probe_task_media_twice(task["id"])

    counted = int(
        (
            await session.execute(
                select(func.count()).select_from(MediaChunk).where(MediaChunk.task_id == task["id"])
            )
        ).scalar_one()
    )
    assert counted == 2, f"two probes should leave exactly two chunks, found {counted}"


async def probe_task_media_twice(task_id: str) -> None:
    from curvevision.jobs.tasks import probe_task_media

    await probe_task_media({"task_id": task_id})
    await probe_task_media({"task_id": task_id})


async def test_a_probe_that_discards_chunks_rebuilds_them(
    owner: ApiActor, project: dict[str, Any], session: Any, settings: Settings
) -> None:
    """The case the idempotency key got wrong, pinned so it cannot come back.

    Correcting a frame count discards every chunk, because they are addressed by the old
    numbering. The build chained afterwards must actually run — with a key on task and frame
    count it aliased the earlier build for the same numbering, which had already succeeded,
    so nothing re-ran and the task ended with zero chunks while every job row said
    "succeeded".
    """
    from sqlalchemy import select

    from curvevision.domain.media import Asset, MediaChunk
    from curvevision.domain.task import Task
    from curvevision.jobs.tasks import probe_task_media

    settings.frames_per_chunk = 4
    task = await video_task(owner, project, make_matroska())

    # Put the count back to the estimate so the next probe corrects it again — and so the
    # chunks built by the upload's probe are discarded and must be rebuilt.
    row = await session.get(Task, task["id"])
    asset = (await session.execute(select(Asset).where(Asset.task_id == row.id))).scalar_one()
    asset.frame_count = FRAMES - 1
    asset.frame_count_exact = False  # an asset already marked exact is skipped
    row.frame_count = FRAMES - 1
    await session.commit()

    result = await probe_task_media({"task_id": task["id"]})
    assert result["chunks_discarded"] >= 1, "this test needs the probe to discard something"

    chunks = list(
        (
            await session.execute(select(MediaChunk).where(MediaChunk.task_id == task["id"]))
        ).scalars()
    )
    assert chunks, "the chunks were discarded and never rebuilt"
    assert max(chunk.start_frame + chunk.frame_count - 1 for chunk in chunks) == FRAMES - 1


# ------------------------------------------- saying so when the count is still a guess
#
# Everything above fixes the count. These cover the cases where it *cannot* be fixed, which
# used to be invisible: the job reported "this task already has annotation work" into a
# background-task row nobody reads, and the task went on offering frames that may not exist.
# `Asset.frame_count_exact` records whether a count was established by decoding, and
# `GET /tasks/{id}/media` is where a client can see it.


async def test_a_counted_video_says_its_frame_count_is_exact(
    owner: ApiActor, project: dict[str, Any]
) -> None:
    task = await video_task(owner, project, make_matroska())

    meta = (await owner.get(f"/api/v1/tasks/{task['id']}/media")).json()
    assert meta["frame_count"] == FRAMES
    assert meta["frame_count_exact"] is True
    assert meta["estimated_assets"] == []
    assert meta["estimated_asset_count"] == 0


async def test_an_image_task_is_exact_without_anything_decoding_it(
    owner: ApiActor, project: dict[str, Any]
) -> None:
    """An image contributes exactly one frame by definition. Nothing has to prove that."""
    from tests.api.test_workflow import png_bytes

    created = await owner.post(
        "/api/v1/tasks",
        json={"project_id": project["id"], "name": "Stills", "media_kind": "image"},
    )
    task = created.json()
    await owner.post(
        f"/api/v1/tasks/{task['id']}/assets",
        files=[("files", ("a.png", png_bytes(), "image/png"))],
    )

    meta = (await owner.get(f"/api/v1/tasks/{task['id']}/media")).json()
    assert meta["frame_count_exact"] is True

    assets = (await owner.get(f"/api/v1/tasks/{task['id']}/assets")).json()
    assert [asset["frame_count_exact"] for asset in assets] == [True]


async def test_a_task_that_kept_its_estimate_says_so_and_names_the_file(
    owner: ApiActor, project: dict[str, Any], session: Any
) -> None:
    """The gap this closes, stated as a user meets it.

    `test_a_task_with_annotations_keeps_its_estimate` above proves the correction is
    declined. That is the right call -- repartitioning frames would orphan somebody's work --
    but until now the task went on presenting a frame count that may overstate the media,
    with nothing anywhere saying it was provisional.
    """
    from sqlalchemy import select

    from curvevision.domain.media import Asset
    from curvevision.domain.task import Job, Task
    from curvevision.jobs.tasks import probe_task_media

    task = await video_task(owner, project, make_matroska())

    row = await session.get(Task, task["id"])
    asset = (await session.execute(select(Asset).where(Asset.task_id == row.id))).scalar_one()
    asset.frame_count = FRAMES - 1
    asset.frame_count_exact = False
    row.frame_count = FRAMES - 1
    job = (await session.execute(select(Job).where(Job.task_id == row.id))).scalars().first()
    job.shape_count = 3  # somebody has drawn on this
    await session.commit()

    result = await probe_task_media({"task_id": task["id"]})
    assert "skipped" in result

    meta = (await owner.get(f"/api/v1/tasks/{task['id']}/media")).json()
    assert meta["frame_count"] == FRAMES - 1, "the estimate must survive intact"
    assert meta["frame_count_exact"] is False
    assert meta["estimated_assets"] == ["clip.mkv"], "the warning has to name the file"
    assert meta["estimated_asset_count"] == 1


async def test_a_file_that_cannot_be_decoded_stays_marked_as_an_estimate(
    owner: ApiActor, project: dict[str, Any], session: Any, monkeypatch: Any
) -> None:
    """The other way a count stays provisional: the decode itself fails.

    A file that moved, or a codec this build has no decoder for. `correct_frame_counts`
    deliberately leaves the estimate in place rather than replacing it with zero, and the
    flag is what makes that visible instead of silent.
    """
    from sqlalchemy import select

    from curvevision.core.errors import ValidationError
    from curvevision.domain.media import Asset
    from curvevision.domain.task import Task
    from curvevision.jobs.tasks import probe_task_media
    from curvevision.services import media as media_service

    task = await video_task(owner, project, make_matroska())

    row = await session.get(Task, task["id"])
    asset = (await session.execute(select(Asset).where(Asset.task_id == row.id))).scalar_one()
    asset.frame_count = FRAMES - 1
    asset.frame_count_exact = False
    row.frame_count = FRAMES - 1
    await session.commit()

    async def undecodable(*_args: Any, **_kwargs: Any) -> int:
        raise ValidationError("no decoder for this codec")

    monkeypatch.setattr(media_service, "exact_frame_count", undecodable)

    result = await probe_task_media({"task_id": task["id"]})
    assert result["corrected"] == []

    meta = (await owner.get(f"/api/v1/tasks/{task['id']}/media")).json()
    assert meta["frame_count"] == FRAMES - 1, "the estimate stands; zero would be worse"
    assert meta["frame_count_exact"] is False
    assert meta["estimated_assets"] == ["clip.mkv"]


async def test_a_correct_estimate_is_still_marked_once_something_checks_it(
    owner: ApiActor, project: dict[str, Any], session: Any
) -> None:
    """The flag records the decode, not the change.

    An estimate that happened to be right is indistinguishable from one that was not until
    something counts the frames. Marking only the assets whose number *moved* would leave
    every correctly-estimated video warning forever.
    """
    from sqlalchemy import select

    from curvevision.domain.media import Asset
    from curvevision.jobs.tasks import probe_task_media

    task = await video_task(owner, project, make_matroska())

    # The count is already right; only the record of having checked is removed.
    found = await session.execute(select(Asset).where(Asset.task_id == task["id"]))
    asset = found.scalar_one()
    asset.frame_count_exact = False
    await session.commit()

    result = await probe_task_media({"task_id": task["id"]})
    assert result["corrected"] == [], "nothing should have moved"

    meta = (await owner.get(f"/api/v1/tasks/{task['id']}/media")).json()
    assert meta["frame_count_exact"] is True
    assert meta["frame_count"] == FRAMES


async def test_a_counted_video_is_not_decoded_a_second_time(
    owner: ApiActor, project: dict[str, Any], session: Any, monkeypatch: Any
) -> None:
    """Adding three photographs must not re-decode a two-hour clip.

    Before the flag existed, `needs_exact_count` meant only "this task holds video", so
    every later upload scheduled a job that walked every video again to arrive at numbers
    already on the rows.
    """
    from curvevision.services import media as media_service
    from tests.api.test_workflow import png_bytes

    task = await video_task(owner, project, make_matroska())

    assert await media_service.needs_exact_count(session, uuid.UUID(task["id"])) is False

    decodes = 0
    original = media_service.exact_frame_count

    async def counting(*args: Any, **kwargs: Any) -> int:
        nonlocal decodes
        decodes += 1
        return await original(*args, **kwargs)

    monkeypatch.setattr(media_service, "exact_frame_count", counting)

    added = await owner.post(
        f"/api/v1/tasks/{task['id']}/assets",
        files=[("files", ("a.png", png_bytes(), "image/png"))],
    )
    assert added.status_code == 201, added.text
    assert decodes == 0, f"the clip was decoded {decodes} more time(s) for an image upload"

    # And even a probe run by hand finds nothing left to do.
    from curvevision.jobs.tasks import probe_task_media

    assert (await probe_task_media({"task_id": task["id"]}))["corrected"] == []
    assert decodes == 0


# ------------------------------------------------------------ asking for a recount


async def test_recount_queues_a_probe(owner: ApiActor, project: dict[str, Any]) -> None:
    """A client that can see the count is provisional needs a way to act on it."""
    task = await video_task(owner, project, make_matroska())

    response = await owner.post(f"/api/v1/tasks/{task['id']}/media/recount")

    assert response.status_code == 202, response.text
    assert response.json()["kind"] == "media.probe_task"


async def test_recount_fixes_a_count_whose_first_attempt_failed(
    owner: ApiActor, project: dict[str, Any], session: Any
) -> None:
    """End to end: the state the warning describes, and the button that clears it."""
    from sqlalchemy import select

    from curvevision.domain.media import Asset
    from curvevision.domain.task import Task

    task = await video_task(owner, project, make_matroska())

    row = await session.get(Task, task["id"])
    asset = (await session.execute(select(Asset).where(Asset.task_id == row.id))).scalar_one()
    asset.frame_count = FRAMES - 1
    asset.frame_count_exact = False
    row.frame_count = FRAMES - 1
    await session.commit()

    before = (await owner.get(f"/api/v1/tasks/{task['id']}/media")).json()
    assert before["frame_count_exact"] is False
    assert before["frame_count"] == FRAMES - 1

    assert (await owner.post(f"/api/v1/tasks/{task['id']}/media/recount")).status_code == 202

    after = (await owner.get(f"/api/v1/tasks/{task['id']}/media")).json()
    assert after["frame_count_exact"] is True
    assert after["frame_count"] == FRAMES
    assert after["estimated_assets"] == []


async def test_an_annotator_cannot_ask_for_a_recount(
    owner: ApiActor,
    client: Any,
    organization: dict[str, Any],
    project: dict[str, Any],
) -> None:
    """Recounting repartitions jobs, so it is an update to the task, not a read of it."""
    from curvevision.domain.enums import Role
    from tests.conftest import add_member, register

    task = await video_task(owner, project, make_matroska())
    annotator = await register(client, "casual")
    await add_member(owner, organization["id"], annotator, Role.ANNOTATOR)

    # The warning is a read, and an annotator is exactly who needs to see it.
    assert (await annotator.get(f"/api/v1/tasks/{task['id']}/media")).status_code == 200

    response = await annotator.post(f"/api/v1/tasks/{task['id']}/media/recount")
    assert response.status_code == 403, response.text


# ------------------------------------------------ the count after media is removed


async def test_deleting_an_asset_leaves_the_task_counting_only_what_is_left(
    owner: ApiActor, project: dict[str, Any]
) -> None:
    """Removing media has to remove its frames, or the task offers frames with no file.

    Found by `scripts/verify_frame_count_warning.py`, which deletes an uncountable clip and
    expected the invented frames to go with it. `delete_asset` marked the row deleted and
    `recount_frames` then queried the assets -- but the sessionmaker is built with
    `autoflush=False`, so the DELETE had not reached the database and the recount summed the
    asset it was supposed to be dropping. The task kept a frame count, and job ranges, for
    media that was gone.
    """
    from tests.api.test_workflow import png_bytes

    created = await owner.post(
        "/api/v1/tasks",
        json={"project_id": project["id"], "name": "Stills", "media_kind": "image"},
    )
    task = created.json()
    uploaded = await owner.post(
        f"/api/v1/tasks/{task['id']}/assets",
        files=[
            ("files", ("a.png", png_bytes(), "image/png")),
            ("files", ("b.png", png_bytes(), "image/png")),
            ("files", ("c.png", png_bytes(), "image/png")),
        ],
    )
    assert uploaded.status_code == 201, uploaded.text
    assert (await owner.get(f"/api/v1/tasks/{task['id']}")).json()["frame_count"] == 3

    assets = (await owner.get(f"/api/v1/tasks/{task['id']}/assets")).json()
    removed = next(asset for asset in assets if asset["name"] == "b.png")
    deleted = await owner.delete(f"/api/v1/tasks/{task['id']}/assets/{removed['id']}")
    assert deleted.status_code == 204, deleted.text

    refreshed = (await owner.get(f"/api/v1/tasks/{task['id']}")).json()
    assert refreshed["frame_count"] == 2, "the removed asset's frame is still being counted"

    # And the frames that remain are renumbered contiguously, so frame 1 is now `c.png`
    # rather than a hole where `b.png` used to be.
    remaining = (await owner.get(f"/api/v1/tasks/{task['id']}/assets")).json()
    assert [(a["name"], a["start_frame"]) for a in remaining] == [("a.png", 0), ("c.png", 1)]

    jobs = (await owner.get(f"/api/v1/tasks/{task['id']}/jobs")).json()
    assert max(job["stop_frame"] for job in jobs) == 1, "a job still covers the deleted frame"
