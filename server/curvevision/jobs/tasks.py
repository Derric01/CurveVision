"""Background job handlers.

Every handler is idempotent and takes a JSON-serialisable payload. Registration happens at
import time so the API process and the worker process agree on the handler table without
either wiring it up by hand.
"""

from __future__ import annotations

import uuid
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from curvevision.core.config import Settings, get_settings
from curvevision.core.db import get_sessionmaker
from curvevision.core.errors import ConflictError
from curvevision.core.logging import get_logger
from curvevision.core.observability import BACKGROUND_TASKS
from curvevision.domain.enums import MediaKind, WebhookEvent
from curvevision.domain.media import Asset, MediaBlob
from curvevision.domain.project import Project
from curvevision.domain.task import Task
from curvevision.jobs.base import job_handler
from curvevision.jobs.runner import report_progress
from curvevision.services import background as background_service
from curvevision.services import datasets as dataset_service
from curvevision.services import media as media_service
from curvevision.services import tasks as task_service
from curvevision.services import webhooks as webhook_service
from curvevision.storage import get_storage

logger = get_logger(__name__)


async def _schedule_chunk_build(
    session: AsyncSession, settings: Settings, task: Task
) -> str | None:
    """Queue the chunk build for a task whose frame numbering is now settled.

    **Why this is chained rather than fused into the count.** Counting and building both
    walk the video, so doing them in one pass looks like the obvious saving. It is not
    available in general: a chunk is addressed by the *task's* frame numbering, and that
    numbering is only known once **every** asset has been counted — an earlier asset gaining
    a frame shifts every later asset's offset, which is exactly why `discard_chunks` throws
    them all away wholesale. Emitting chunk 0 while asset 3 is still being counted would
    build chunks against numbering that is not final yet.

    A fused pass would therefore be correct only for a single-asset task and would need a
    second implementation for every other shape — two ways to do one thing, to save one
    decode pass that is already off the request path. Chaining keeps one implementation and
    gets the whole user-visible win: the first annotator to open the task no longer pays for
    the decode.

    **Deliberately not deduplicated by an idempotency key.** The obvious key — this task at
    this frame count — is wrong, and wrong in the direction that loses data rather than
    duplicating it: a later probe that *discards* the chunks (because the numbering moved and
    moved back, say) would enqueue a build that dedupes against the earlier, already-succeeded
    job, so it never runs and the task is left with no chunks at all. A key cannot express
    "the chunks from that build still exist", because that is not a property of the request.

    The cost of not deduplicating is one extra job row and a no-op pass over the chunk
    indices, since `build_chunk` returns an existing chunk rather than rebuilding it. The cost
    of deduplicating wrongly is a video task that silently serves no chunks. The invariant
    worth protecting is *the chunks exist after the probe*, so this enqueues every time.
    """
    if task.media_kind is not MediaKind.VIDEO:
        # Images already serve in constant time; `build_chunk` would return None for every
        # range and the job would be a no-op loop over every chunk index.
        return None
    if media_service.chunk_count(task.frame_count, settings.frames_per_chunk) <= 0:
        # No frames, or chunking disabled by configuration.
        return None

    queued = await background_service.enqueue(
        session,
        kind="media.build_chunks",
        payload={"task_id": str(task.id)},
        resource_type="task",
        resource_id=task.id,
    )
    return str(queued.id)


@job_handler("media.probe_task")
async def probe_task_media(payload: dict[str, Any]) -> dict[str, Any]:
    """Recompute a task's frame index after media changed, counting video frames exactly.

    A video task is created with an *estimated* frame count, because counting means
    decoding the whole file and that cannot happen inside an upload request. The estimate
    is wrong for any variable-frame-rate video, and wrong in the damaging direction when it
    is too high: the task offers frames that do not exist, and an annotator who steps onto
    one meets what looks like missing media.

    Idempotent by construction: it recounts from the assets rather than incrementing
    anything, so a second run finds the counts already correct and changes nothing.

    **It will not reshuffle work that exists.** `rebuild_jobs` refuses once a job carries
    annotations, and this does not argue with it: such a task keeps its estimate and the
    result says so, because silently repartitioning frames under an annotator would orphan
    what they drew. Rare by construction -- this runs immediately after upload, before
    anyone has opened the task.
    """
    task_id = uuid.UUID(payload["task_id"])
    settings = get_settings()
    storage = get_storage(settings)
    factory = get_sessionmaker()

    async with factory() as session:
        task = await session.get(Task, task_id)
        if task is None:
            return {"skipped": "task no longer exists"}

        estimated = task.frame_count
        corrected = await media_service.correct_frame_counts(session, storage, task)
        frame_count = await task_service.recount_frames(session, task)

        result: dict[str, Any] = {
            "task_id": str(task_id),
            "frame_count": frame_count,
            "corrected": [{"asset": name, "was": was, "now": now} for name, was, now in corrected],
        }

        if corrected:
            result["estimated_frame_count"] = estimated
            try:
                await task_service.rebuild_jobs(session, task)
            except ConflictError:
                # Annotations exist, so the frame ranges are not ours to move. Abandon the
                # correction entirely rather than leave a task whose jobs and frame count
                # disagree -- half-applied is worse than not applied.
                await session.rollback()
                # The numbering did not move, so any chunks already built are still
                # addressed correctly and the ones that are missing are still worth having.
                await session.refresh(task)
                chunk_job = await _schedule_chunk_build(session, settings, task)
                BACKGROUND_TASKS.labels("media.probe_task", "succeeded").inc()
                return {
                    "task_id": str(task_id),
                    "frame_count": estimated,
                    "skipped": "this task already has annotation work; its frame count is "
                    "still the upload-time estimate",
                    "chunk_build_task_id": chunk_job,
                }
            # The frame numbering moved, so every chunk addressed by it is stale.
            result["chunks_discarded"] = await media_service.discard_chunks(
                session, storage, task.id
            )

        await session.commit()

        # Only now: `correct_frame_counts` may have moved every chunk boundary, and
        # `discard_chunks` above has already thrown away anything addressed by the old
        # numbering. Queueing the build before this point would decode frames into chunks
        # that the same job then deletes.
        result["chunk_build_task_id"] = await _schedule_chunk_build(session, settings, task)

    BACKGROUND_TASKS.labels("media.probe_task", "succeeded").inc()
    return result


@job_handler("media.build_chunks")
async def build_media_chunks(payload: dict[str, Any]) -> dict[str, Any]:
    """Decode a task's video frames ahead of time, one pass per chunk.

    Idempotent: `build_chunk` returns the existing row rather than rebuilding, so a retry
    after a partial run costs only the chunks that are still missing. Each chunk is
    committed as it is built, which is what makes an interrupted run useful rather than
    wasted -- and what lets the editor start using the early chunks while the rest are
    still decoding.

    An image task produces no chunks at all: `build_chunk` returns ``None`` for a range
    with no video in it, because images already serve in constant time.

    A frame is *never* blocked on this job. `GET .../frames/{n}/data` falls back to
    decoding that one frame whenever its chunk is not built yet, so this only ever makes
    things faster, never makes them unavailable.
    """
    task_id = uuid.UUID(payload["task_id"])
    settings = get_settings()
    factory = get_sessionmaker()
    row_id = payload.get("_task_row_id")

    built = 0
    async with factory() as session:
        task = await session.get(Task, task_id)
        if task is None:
            return {"skipped": "task no longer exists"}
        planned = media_service.chunk_count(task.frame_count, settings.frames_per_chunk)
        storage = get_storage(settings)

        for index in range(planned):
            chunk = await media_service.build_chunk(session, settings, storage, task, index)
            await session.commit()
            if chunk is not None:
                built += 1
            if row_id:
                await report_progress(
                    str(row_id), (index + 1) / planned, f"chunk {index + 1} of {planned}"
                )

    return {
        "task_id": str(task_id),
        "chunks_planned": planned,
        "chunks_built": built,
        "frames_per_chunk": settings.frames_per_chunk,
        "status": "built",
    }


@job_handler("dataset.export")
async def export_dataset_job(payload: dict[str, Any]) -> dict[str, Any]:
    """Produce an export archive and store it for download."""
    project_id = uuid.UUID(payload["project_id"])
    format_id = str(payload["format"])
    settings = get_settings()
    storage = get_storage(settings)
    factory = get_sessionmaker()

    async with factory() as session:
        project = await session.get(Project, project_id)
        if project is None:
            return {"skipped": "project no longer exists"}
        archive, warnings = await dataset_service.export_dataset(
            session,
            project,
            format_id=format_id,
            task_ids=[uuid.UUID(value) for value in payload.get("task_ids", [])] or None,
            only_accepted=bool(payload.get("only_accepted", False)),
            include_images=bool(payload.get("include_images", False)),
            storage=storage if payload.get("include_images") else None,
        )

    key = f"exports/{project_id}/{payload.get('_task_row_id', uuid.uuid4())}.zip"
    await storage.put(key, archive, content_type="application/zip")
    BACKGROUND_TASKS.labels("dataset.export", "succeeded").inc()
    return {
        "storage_key": key,
        "size_bytes": len(archive),
        "format": format_id,
        "warnings": warnings,
    }


@job_handler("webhook.dispatch")
async def dispatch_webhook(payload: dict[str, Any]) -> dict[str, Any]:
    """Deliver one event to every subscribed webhook."""
    event = WebhookEvent(payload["event"])
    factory = get_sessionmaker()
    async with factory() as session:
        deliveries = await webhook_service.dispatch(
            session,
            event,
            payload.get("data", {}),
            organization_id=(
                uuid.UUID(payload["organization_id"]) if payload.get("organization_id") else None
            ),
            project_id=(uuid.UUID(payload["project_id"]) if payload.get("project_id") else None),
        )
        await session.commit()
        succeeded = sum(
            1
            for delivery in deliveries
            if delivery.status_code is not None and delivery.status_code < 400
        )
    return {"attempted": len(deliveries), "succeeded": succeeded}


@job_handler("media.reclaim_blobs")
async def reclaim_orphan_blobs(payload: dict[str, Any]) -> dict[str, Any]:
    """Delete stored blobs no asset references any more.

    Deletion is deferred to this job rather than done inline when an asset is removed,
    because a blob is shared across tasks and an interactive delete cannot safely prove it
    is unreferenced.
    """
    del payload
    factory = get_sessionmaker()
    settings = get_settings()
    storage = get_storage(settings)
    removed = 0
    async with factory() as session:
        referenced = select(Asset.blob_id).distinct()
        orphans = (
            (await session.execute(select(MediaBlob).where(MediaBlob.id.not_in(referenced))))
            .scalars()
            .all()
        )
        for blob in orphans:
            # A blob annotated in place points at a file the user owns. Forgetting the
            # row is right; deleting their picture is not.
            if blob.storage_key is not None:
                await storage.delete(blob.storage_key)
            if blob.thumbnail_key:
                await storage.delete(blob.thumbnail_key)
            await session.delete(blob)
            removed += 1
        await session.commit()
    return {"blobs_removed": removed}
