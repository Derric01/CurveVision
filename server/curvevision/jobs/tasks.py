"""Background job handlers.

Every handler is idempotent and takes a JSON-serialisable payload. Registration happens at
import time so the API process and the worker process agree on the handler table without
either wiring it up by hand.
"""

from __future__ import annotations

import uuid
from typing import Any

from sqlalchemy import select

from curvevision.core.config import get_settings
from curvevision.core.db import get_sessionmaker
from curvevision.core.logging import get_logger
from curvevision.core.observability import BACKGROUND_TASKS
from curvevision.domain.enums import WebhookEvent
from curvevision.domain.media import Asset, MediaBlob
from curvevision.domain.project import Project
from curvevision.domain.task import Task
from curvevision.jobs.base import job_handler
from curvevision.jobs.runner import report_progress
from curvevision.services import datasets as dataset_service
from curvevision.services import media as media_service
from curvevision.services import tasks as task_service
from curvevision.services import webhooks as webhook_service
from curvevision.storage import get_storage

logger = get_logger(__name__)


@job_handler("media.probe_task")
async def probe_task_media(payload: dict[str, Any]) -> dict[str, Any]:
    """Recompute a task's frame index after media changed.

    Idempotent by construction: it recomputes from the assets rather than incrementing
    anything, so running it twice produces the same state.
    """
    task_id = uuid.UUID(payload["task_id"])
    factory = get_sessionmaker()
    async with factory() as session:
        task = await session.get(Task, task_id)
        if task is None:
            return {"skipped": "task no longer exists"}
        frame_count = await task_service.recount_frames(session, task)
        await session.commit()
    BACKGROUND_TASKS.labels("media.probe_task", "succeeded").inc()
    return {"task_id": str(task_id), "frame_count": frame_count}


@job_handler("media.build_chunks")
async def build_media_chunks(payload: dict[str, Any]) -> dict[str, Any]:
    """Plan the frame chunks a task's media will be served in.

    **In Progress.** The chunk *plan* is computed and recorded; writing decoded frame
    archives to object storage requires the optional media extras and lands with video
    support. Image tasks are served directly and need no chunks, so this is a no-op for
    them today.
    """
    task_id = uuid.UUID(payload["task_id"])
    settings = get_settings()
    factory = get_sessionmaker()
    async with factory() as session:
        task = await session.get(Task, task_id)
        if task is None:
            return {"skipped": "task no longer exists"}
        planned = media_service.chunk_count(task.frame_count, settings.frames_per_chunk)
    if row_id := payload.get("_task_row_id"):
        await report_progress(str(row_id), 1.0, "chunk plan computed")
    return {
        "task_id": str(task_id),
        "chunks_planned": planned,
        "frames_per_chunk": settings.frames_per_chunk,
        "status": "planned",
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
            await storage.delete(blob.storage_key)
            if blob.thumbnail_key:
                await storage.delete(blob.thumbnail_key)
            await session.delete(blob)
            removed += 1
        await session.commit()
    return {"blobs_removed": removed}
