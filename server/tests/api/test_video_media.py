"""Annotating a video.

A video is one file holding thousands of frames, so the bytes of the blob are emphatically
not the bytes of a frame. Serving the container when the editor asked for frame 3 makes a
video task unusable, which is what these tests exist to prevent coming back.
"""

from __future__ import annotations

import io
from typing import Any

import pytest

from tests.conftest import ApiActor

av = pytest.importorskip("av", reason="video support is an optional extra")
pytest.importorskip("PIL", reason="frames are served as JPEG")


def make_video(frames: int = 10, width: int = 64, height: int = 48, rate: int = 5) -> bytes:
    """A real encoded video whose frames differ from one another."""
    from PIL import Image

    buffer = io.BytesIO()
    with av.open(buffer, mode="w", format="mp4") as container:
        stream = container.add_stream("mpeg4", rate=rate)
        stream.width = width
        stream.height = height
        stream.pix_fmt = "yuv420p"
        for index in range(frames):
            shade = (index * 24) % 256
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
        files=[("files", ("clip.mp4", data, "video/mp4"))],
    )
    assert uploaded.status_code == 201, uploaded.text

    refreshed = await actor.get(f"/api/v1/tasks/{task['id']}")
    assert refreshed.status_code == 200
    return dict(refreshed.json())


async def test_a_video_becomes_a_task_with_many_frames(
    owner: ApiActor, project: dict[str, Any]
) -> None:
    task = await video_task(owner, project, make_video(frames=10))
    # One uploaded file, but a task with many annotatable frames.
    assert task["frame_count"] > 1


async def test_a_frame_request_returns_that_frame_not_the_whole_video(
    owner: ApiActor, project: dict[str, Any]
) -> None:
    """The regression this file exists for.

    Serving the container meant the editor received a multi-megabyte video where it
    expected one picture, and every frame of a video task looked identical.
    """
    data = make_video(frames=10)
    task = await video_task(owner, project, data)

    response = await owner.get(f"/api/v1/tasks/{task['id']}/frames/0/data")
    assert response.status_code == 200, response.text

    assert response.headers["content-type"].startswith("image/jpeg")
    assert response.content.startswith(b"\xff\xd8\xff")  # JPEG, not MP4
    assert response.content != data
    assert len(response.content) < len(data)


async def test_different_frames_are_different_pictures(
    owner: ApiActor, project: dict[str, Any]
) -> None:
    task = await video_task(owner, project, make_video(frames=10))

    first = await owner.get(f"/api/v1/tasks/{task['id']}/frames/0/data")
    later = await owner.get(f"/api/v1/tasks/{task['id']}/frames/4/data")
    assert first.status_code == later.status_code == 200
    assert first.content != later.content


async def test_a_frame_number_means_the_same_picture_every_time(
    owner: ApiActor, project: dict[str, Any]
) -> None:
    """Annotations are anchored to frame numbers; this is what makes that meaningful."""
    task = await video_task(owner, project, make_video(frames=10))

    once = await owner.get(f"/api/v1/tasks/{task['id']}/frames/3/data")
    twice = await owner.get(f"/api/v1/tasks/{task['id']}/frames/3/data")
    assert once.content == twice.content


async def test_a_served_frame_is_a_real_image_of_the_right_size(
    owner: ApiActor, project: dict[str, Any]
) -> None:
    from PIL import Image

    task = await video_task(owner, project, make_video(frames=6, width=96, height=72))
    response = await owner.get(f"/api/v1/tasks/{task['id']}/frames/2/data")

    with Image.open(io.BytesIO(response.content)) as image:
        assert image.size == (96, 72)


async def test_a_frame_beyond_the_end_is_not_found(
    owner: ApiActor, project: dict[str, Any]
) -> None:
    task = await video_task(owner, project, make_video(frames=5))
    response = await owner.get(f"/api/v1/tasks/{task['id']}/frames/9999/data")
    assert response.status_code == 404


async def test_the_video_is_stored_once_however_many_frames_are_read(
    owner: ApiActor, project: dict[str, Any]
) -> None:
    """Frames are decoded on demand, not exploded into thousands of stored images."""
    task = await video_task(owner, project, make_video(frames=8))

    assets = await owner.get(f"/api/v1/tasks/{task['id']}/assets")
    assert assets.status_code == 200
    assert len(assets.json()) == 1

    for frame in (0, 1, 2):
        served = await owner.get(f"/api/v1/tasks/{task['id']}/frames/{frame}/data")
        assert served.status_code == 200

    still_one = await owner.get(f"/api/v1/tasks/{task['id']}/assets")
    assert len(still_one.json()) == 1
