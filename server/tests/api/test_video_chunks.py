"""Serving video frames out of pre-decoded chunks.

Reaching frame *n* of a video means decoding *n* frames: a frame number has to identify the
same picture every time, so seeking is not an option (see
`curvevision.media.video.VideoReader.iterate_frames`). Stepping through the first 36 frames
one at a time therefore costs 0+1+...+35 = 630 frame-decodes. A chunk is the same walk done
once, keeping everything it passes.

Two properties matter more than the speed, and both are asserted here:

* **A chunked frame is the same picture as a decoded one.** If chunking changed which image
  frame 7 is, every annotation drawn before it was built would silently move.
* **A missing chunk is never a missing frame.** Chunking off, storage that lost an archive,
  an image asset in the range -- every one of them still serves the frame, more slowly.
"""

from __future__ import annotations

import io
import zipfile
from typing import Any

import pytest

from curvevision.core.config import Settings
from tests.api.test_video_media import make_video, video_task
from tests.conftest import ApiActor

av = pytest.importorskip("av", reason="video support is an optional extra")
pytest.importorskip("PIL", reason="frames are served as JPEG")

#: Small, so a short test video spans several chunks and the boundaries get exercised.
FRAMES_PER_CHUNK = 4


@pytest.fixture
def settings(settings: Settings) -> Settings:
    """The shared settings, retuned so a 10-frame clip is three chunks rather than one."""
    settings.frames_per_chunk = FRAMES_PER_CHUNK
    return settings


def entries(archive: bytes) -> list[str]:
    with zipfile.ZipFile(io.BytesIO(archive)) as zipped:
        return sorted(zipped.namelist())


# ------------------------------------------------------------------ the chunk itself


async def test_a_chunk_holds_the_frames_of_its_range(
    owner: ApiActor, project: dict[str, Any]
) -> None:
    task = await video_task(owner, project, make_video(frames=10))

    response = await owner.get(f"/api/v1/tasks/{task['id']}/chunks/1")
    assert response.status_code == 200, response.text
    assert response.headers["content-type"].startswith("application/zip")

    assert entries(response.content) == [
        "000004.jpg",
        "000005.jpg",
        "000006.jpg",
        "000007.jpg",
    ]
    assert response.headers["x-curvevision-frames"] == "4-7"


async def test_every_entry_is_a_real_jpeg_of_the_right_size(
    owner: ApiActor, project: dict[str, Any]
) -> None:
    from PIL import Image

    task = await video_task(owner, project, make_video(frames=8, width=96, height=72))
    response = await owner.get(f"/api/v1/tasks/{task['id']}/chunks/0")

    with zipfile.ZipFile(io.BytesIO(response.content)) as zipped:
        for name in zipped.namelist():
            with Image.open(io.BytesIO(zipped.read(name))) as image:
                assert image.size == (96, 72), name


async def test_the_last_chunk_is_short_rather_than_padded(
    owner: ApiActor, project: dict[str, Any]
) -> None:
    """10 frames at 4 per chunk is 4 + 4 + 2, and the tail must not overhang the video."""
    task = await video_task(owner, project, make_video(frames=10))
    meta = (await owner.get(f"/api/v1/tasks/{task['id']}/media")).json()
    last = meta["chunk_count"] - 1

    response = await owner.get(f"/api/v1/tasks/{task['id']}/chunks/{last}")
    assert response.status_code == 200
    assert len(entries(response.content)) == meta["frame_count"] - last * FRAMES_PER_CHUNK


async def test_a_chunk_beyond_the_end_is_not_found(
    owner: ApiActor, project: dict[str, Any]
) -> None:
    task = await video_task(owner, project, make_video(frames=6))
    assert (await owner.get(f"/api/v1/tasks/{task['id']}/chunks/99")).status_code == 404
    assert (await owner.get(f"/api/v1/tasks/{task['id']}/chunks/-1")).status_code == 404


# ------------------------------------------------------- the frame is the same picture


async def test_a_frame_from_a_chunk_is_the_same_picture_as_a_decoded_one(
    owner: ApiActor, project: dict[str, Any], settings: Settings
) -> None:
    """The property everything else rests on.

    A frame number anchors an annotation. If building a chunk changed which picture frame 7
    is, every box drawn on it before the chunk existed would now be on the wrong image --
    silently, with nothing to notice it by.
    """
    task = await video_task(owner, project, make_video(frames=10))

    # With chunking off, this is the single-frame decode path.
    settings.frames_per_chunk = 0
    decoded = {
        frame: (await owner.get(f"/api/v1/tasks/{task['id']}/frames/{frame}/data")).content
        for frame in range(10)
    }

    settings.frames_per_chunk = FRAMES_PER_CHUNK
    for frame, expected in decoded.items():
        served = await owner.get(f"/api/v1/tasks/{task['id']}/frames/{frame}/data")
        assert served.status_code == 200, frame
        assert served.content == expected, f"frame {frame} changed picture once chunked"


async def test_the_chunk_entry_and_the_frame_endpoint_agree(
    owner: ApiActor, project: dict[str, Any]
) -> None:
    task = await video_task(owner, project, make_video(frames=8))

    archive = (await owner.get(f"/api/v1/tasks/{task['id']}/chunks/1")).content
    with zipfile.ZipFile(io.BytesIO(archive)) as zipped:
        from_chunk = zipped.read("000005.jpg")

    served = await owner.get(f"/api/v1/tasks/{task['id']}/frames/5/data")
    assert served.content == from_chunk


# -------------------------------------------------------------- built once, then reused


async def test_serving_a_frame_builds_its_chunk_and_the_next_frames_reuse_it(
    owner: ApiActor, project: dict[str, Any], session: Any, settings: Settings
) -> None:
    """The lazy path: when a chunk is absent, asking for a frame builds it, once.

    Uploading now pre-builds chunks, so this starts by discarding them to reach the state
    the test is about. That state is not hypothetical — it is what a task looks like when
    chunking was disabled at upload, when the build job failed, and in the window after a
    frame-count correction discards every chunk. The fallback is what guarantees a frame is
    never *unavailable*, only slower, so it keeps its own test.
    """
    from sqlalchemy import func, select

    from curvevision.domain.media import MediaChunk
    from curvevision.domain.task import Task as TaskRow
    from curvevision.services import media as media_service
    from curvevision.storage import get_storage

    task = await video_task(owner, project, make_video(frames=10))

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

    row = await session.get(TaskRow, task["id"])
    await media_service.discard_chunks(session, get_storage(settings), row.id)
    await session.commit()
    assert await chunks() == 0, "the precondition this test is about"

    await owner.get(f"/api/v1/tasks/{task['id']}/frames/0/data")
    assert await chunks() == 1, "the first frame of a chunk builds the whole chunk"

    # The other three frames of that chunk are now free, and build nothing further.
    for frame in (1, 2, 3):
        assert (
            await owner.get(f"/api/v1/tasks/{task['id']}/frames/{frame}/data")
        ).status_code == 200
    assert await chunks() == 1

    await owner.get(f"/api/v1/tasks/{task['id']}/frames/4/data")
    assert await chunks() == 2, "crossing into the next chunk builds that one"


async def test_a_chunk_is_not_rebuilt_when_it_is_requested_again(
    owner: ApiActor, project: dict[str, Any]
) -> None:
    task = await video_task(owner, project, make_video(frames=8))

    first = await owner.get(f"/api/v1/tasks/{task['id']}/chunks/0")
    again = await owner.get(f"/api/v1/tasks/{task['id']}/chunks/0")
    assert first.status_code == again.status_code == 200
    assert first.content == again.content


# ------------------------------------------------------- a missing chunk is never fatal


async def test_frames_still_serve_with_chunking_switched_off(
    owner: ApiActor, project: dict[str, Any], settings: Settings
) -> None:
    """`frames_per_chunk = 0` is a supported configuration, not a broken one."""
    task = await video_task(owner, project, make_video(frames=6))
    settings.frames_per_chunk = 0

    response = await owner.get(f"/api/v1/tasks/{task['id']}/frames/3/data")
    assert response.status_code == 200
    assert response.content.startswith(b"\xff\xd8\xff")


async def test_a_frame_survives_losing_its_chunk_archive(
    owner: ApiActor, project: dict[str, Any], settings: Settings
) -> None:
    """The row can outlive its bytes -- a pruned cache, a half-restored backup.

    Falling back to decoding is what keeps that an inconvenience rather than an outage.
    """
    from curvevision.services import media as media_service
    from curvevision.storage import get_storage

    task = await video_task(owner, project, make_video(frames=8))
    before = await owner.get(f"/api/v1/tasks/{task['id']}/frames/1/data")
    assert before.status_code == 200

    await get_storage(settings).delete(media_service.chunk_key(task["id"], 0))

    after = await owner.get(f"/api/v1/tasks/{task['id']}/frames/1/data")
    assert after.status_code == 200
    assert after.content == before.content


async def test_two_builders_racing_for_the_same_chunk_produce_one_row(
    owner: ApiActor,
    project: dict[str, Any],
    settings: Settings,
    session: Any,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Two requests can miss the same chunk at the same moment and both start building.

    The unique constraint is what actually decides it. The loser must return the winner's
    row rather than raising -- and, critically, must not roll back whatever else its caller
    had pending, which is why the insert sits in a SAVEPOINT.

    Simulated rather than threaded: the interleaving that matters is "the row appeared
    between the lookup and the insert", and forcing exactly that is more reliable than
    hoping two coroutines collide.
    """
    from sqlalchemy import func, select

    from curvevision.domain.media import MediaChunk
    from curvevision.domain.task import Task as TaskRow
    from curvevision.services import media as media_service
    from curvevision.storage import get_storage

    task = await video_task(owner, project, make_video(frames=8))
    # The winner builds chunk 0 for real.
    assert (await owner.get(f"/api/v1/tasks/{task['id']}/chunks/0")).status_code == 200

    real_find = media_service.find_chunk
    misses = {"left": 1}

    async def find_once_blind(*args: Any, **kwargs: Any) -> Any:
        if misses["left"]:
            misses["left"] -= 1
            return None  # The loser's lookup, racing ahead of the winner's commit.
        return await real_find(*args, **kwargs)

    monkeypatch.setattr(media_service, "find_chunk", find_once_blind)

    row = await session.get(TaskRow, task["id"])
    loser = await media_service.build_chunk(session, settings, get_storage(settings), row, 0)

    assert loser is not None, "the loser must end up with the winner's chunk"
    assert loser.index == 0
    # Counted for chunk 0 specifically, which is what the race was over. Counting every
    # chunk on the task used to mean the same thing only because nothing else had been
    # built; uploading now pre-builds them all, and the claim was never about how many
    # chunks a task has.
    count = (
        await session.execute(
            select(func.count())
            .select_from(MediaChunk)
            .where(MediaChunk.task_id == task["id"], MediaChunk.index == 0)
        )
    ).scalar_one()
    assert count == 1, "the race must not leave a duplicate behind"


async def test_an_image_task_gets_no_chunks_and_serves_frames_anyway(
    owner: ApiActor, project: dict[str, Any]
) -> None:
    """Images already serve in constant time; packing them into archives buys nothing."""
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
    assert uploaded.status_code == 201, uploaded.text

    assert (await owner.get(f"/api/v1/tasks/{task['id']}/chunks/0")).status_code == 404
    served = await owner.get(f"/api/v1/tasks/{task['id']}/frames/0/data")
    assert served.status_code == 200
