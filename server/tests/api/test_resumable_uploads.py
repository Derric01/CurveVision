"""The resumable-upload protocol: create a session, PATCH chunks in, complete.

Exists for the same reason the direct multipart endpoint is not always enough: a large
file over a flaky connection should resume from where it dropped rather than restart, and
should not require buffering the whole thing in one request body.
"""

from __future__ import annotations

from typing import Any

import pytest

from tests.conftest import ApiActor

pytest.importorskip("PIL", reason="image dimensions are needed to exercise the media path")


def png_bytes(width: int = 64, height: int = 48, colour: str = "#334155") -> bytes:
    import io

    from PIL import Image

    buffer = io.BytesIO()
    Image.new("RGB", (width, height), colour).save(buffer, format="PNG")
    return buffer.getvalue()


async def create_task(actor: ApiActor, project: dict[str, Any]) -> dict[str, Any]:
    response = await actor.post(
        "/api/v1/tasks",
        json={"project_id": project["id"], "name": "Resumable", "media_kind": "image"},
    )
    assert response.status_code == 201, response.text
    return dict(response.json())


async def start_upload(
    actor: ApiActor, task_id: str, *, filename: str, size: int
) -> dict[str, Any]:
    response = await actor.post(
        f"/api/v1/tasks/{task_id}/uploads",
        json={"filename": filename, "size": size},
    )
    assert response.status_code == 201, response.text
    return dict(response.json())


async def patch_chunk(
    actor: ApiActor, task_id: str, upload_id: str, *, offset: int, data: bytes
) -> Any:
    return await actor.client.patch(
        f"/api/v1/tasks/{task_id}/uploads/{upload_id}",
        headers={**actor.headers, "Upload-Offset": str(offset)},
        content=data,
    )


class TestResumableUploads:
    async def test_a_file_sent_in_several_chunks_is_reassembled_correctly(
        self, owner: ApiActor, project: dict[str, Any]
    ) -> None:
        task = await create_task(owner, project)
        data = png_bytes()
        session_out = await start_upload(owner, task["id"], filename="clip.png", size=len(data))
        assert session_out["received_bytes"] == 0
        assert session_out["completed"] is False

        midpoint = len(data) // 2
        first = await patch_chunk(
            owner, task["id"], session_out["id"], offset=0, data=data[:midpoint]
        )
        assert first.status_code == 200, first.text
        assert first.json()["received_bytes"] == midpoint

        second = await patch_chunk(
            owner, task["id"], session_out["id"], offset=midpoint, data=data[midpoint:]
        )
        assert second.status_code == 200, second.text
        assert second.json()["received_bytes"] == len(data)

        complete = await owner.post(
            f"/api/v1/tasks/{task['id']}/uploads/{session_out['id']}/complete"
        )
        assert complete.status_code == 201, complete.text
        asset = complete.json()
        assert asset["name"] == "clip.png"
        assert asset["frame_count"] == 1

        detail = (await owner.get(f"/api/v1/tasks/{task['id']}")).json()
        assert detail["frame_count"] == 1
        assert detail["status"] == "ready"

    async def test_the_current_offset_can_be_read_back(
        self, owner: ApiActor, project: dict[str, Any]
    ) -> None:
        """A client that lost track of its own progress asks rather than guesses."""
        task = await create_task(owner, project)
        data = png_bytes()
        session_out = await start_upload(owner, task["id"], filename="a.png", size=len(data))
        await patch_chunk(owner, task["id"], session_out["id"], offset=0, data=data[:10])

        status = await owner.get(f"/api/v1/tasks/{task['id']}/uploads/{session_out['id']}")
        assert status.status_code == 200
        assert status.json()["received_bytes"] == 10

    async def test_a_chunk_at_the_wrong_offset_is_rejected_with_the_real_one(
        self, owner: ApiActor, project: dict[str, Any]
    ) -> None:
        """The client resends the whole chunk on a dropped response; a stale offset would
        otherwise duplicate bytes silently rather than fail loudly."""
        task = await create_task(owner, project)
        data = png_bytes()
        session_out = await start_upload(owner, task["id"], filename="a.png", size=len(data))
        await patch_chunk(owner, task["id"], session_out["id"], offset=0, data=data[:10])

        retried_from_zero = await patch_chunk(
            owner, task["id"], session_out["id"], offset=0, data=data[:10]
        )
        assert retried_from_zero.status_code == 409
        assert retried_from_zero.json()["received_bytes"] == 10

    async def test_completing_before_every_byte_has_arrived_is_refused(
        self, owner: ApiActor, project: dict[str, Any]
    ) -> None:
        task = await create_task(owner, project)
        data = png_bytes()
        session_out = await start_upload(owner, task["id"], filename="a.png", size=len(data))
        await patch_chunk(owner, task["id"], session_out["id"], offset=0, data=data[:10])

        response = await owner.post(
            f"/api/v1/tasks/{task['id']}/uploads/{session_out['id']}/complete"
        )
        assert response.status_code == 422

    async def test_a_declared_size_over_the_limit_is_refused_before_any_bytes_move(
        self, owner: ApiActor, project: dict[str, Any]
    ) -> None:
        task = await create_task(owner, project)
        response = await owner.post(
            f"/api/v1/tasks/{task['id']}/uploads",
            json={"filename": "huge.png", "size": 10 * 1024 * 1024 * 1024},
        )
        assert response.status_code == 422

    async def test_aborting_frees_the_partial_bytes_and_the_session(
        self, owner: ApiActor, project: dict[str, Any]
    ) -> None:
        task = await create_task(owner, project)
        data = png_bytes()
        session_out = await start_upload(owner, task["id"], filename="a.png", size=len(data))
        await patch_chunk(owner, task["id"], session_out["id"], offset=0, data=data[:10])

        aborted = await owner.delete(f"/api/v1/tasks/{task['id']}/uploads/{session_out['id']}")
        assert aborted.status_code == 204

        gone = await owner.get(f"/api/v1/tasks/{task['id']}/uploads/{session_out['id']}")
        assert gone.status_code == 404

    async def test_a_zero_byte_declared_upload_fails_cleanly_rather_than_with_a_server_error(
        self, owner: ApiActor, project: dict[str, Any]
    ) -> None:
        """`declared_size=0` is "complete" the instant it is created, before any chunk was
        ever appended -- so the storage key behind it was never written. That must not
        surface as an unhandled storage error."""
        task = await create_task(owner, project)
        session_out = await start_upload(owner, task["id"], filename="empty.png", size=0)
        assert session_out["received_bytes"] == 0

        response = await owner.post(
            f"/api/v1/tasks/{task['id']}/uploads/{session_out['id']}/complete"
        )
        assert response.status_code == 422, response.text

    async def test_identical_bytes_resumed_and_sent_directly_dedupe_to_one_blob(
        self, owner: ApiActor, project: dict[str, Any]
    ) -> None:
        """A resumable upload's finished bytes must land through the exact same dedupe path
        a direct upload uses."""
        from sqlalchemy import func, select

        from curvevision.core.db import get_sessionmaker
        from curvevision.domain.media import MediaBlob

        task = await create_task(owner, project)
        data = png_bytes()

        direct = await owner.post(
            f"/api/v1/tasks/{task['id']}/assets",
            files=[("files", ("direct.png", data, "image/png"))],
        )
        assert direct.status_code == 201, direct.text

        session_out = await start_upload(owner, task["id"], filename="resumed.png", size=len(data))
        await patch_chunk(owner, task["id"], session_out["id"], offset=0, data=data)
        complete = await owner.post(
            f"/api/v1/tasks/{task['id']}/uploads/{session_out['id']}/complete"
        )
        assert complete.status_code == 201, complete.text

        async with get_sessionmaker()() as db_session:
            blobs = (
                await db_session.execute(select(func.count()).select_from(MediaBlob))
            ).scalar_one()
        assert blobs == 1
