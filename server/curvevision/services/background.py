"""Creating and enqueueing background work.

Everything slow -- frame extraction, export, import, inference, quality reports -- goes
through here so it gets the same durable row, the same idempotency guarantee and the same
progress reporting regardless of which queue backend is configured.
"""

from __future__ import annotations

import uuid
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from curvevision.domain.enums import BackgroundTaskState
from curvevision.domain.system import BackgroundTask
from curvevision.jobs import JobQueue, get_job_queue

#: Which queue each job kind is routed to. One worker image serves all of them; an
#: operator wanting isolation runs `curvevision-worker --queues media` rather than
#: deploying a separate service per queue.
QUEUE_ROUTING: dict[str, str] = {
    "media.probe_task": "media",
    "media.build_chunks": "media",
    "media.reclaim_blobs": "media",
    "dataset.export": "export",
    "dataset.import": "import",
    "inference.run": "inference",
    "webhook.dispatch": "webhooks",
    "webhook.retry": "webhooks",
    "quality.report": "quality",
}


async def enqueue(
    session: AsyncSession,
    *,
    kind: str,
    payload: dict[str, Any],
    created_by_id: uuid.UUID | None = None,
    resource_type: str | None = None,
    resource_id: uuid.UUID | None = None,
    idempotency_key: str | None = None,
    queue: JobQueue | None = None,
    delay_seconds: float = 0.0,
) -> BackgroundTask:
    """Create a ``BackgroundTask`` row and schedule it.

    When ``idempotency_key`` matches an existing task, that task is returned untouched
    rather than a duplicate being created -- which is what makes a client safe to retry.
    """
    if idempotency_key:
        existing = (
            await session.execute(
                select(BackgroundTask).where(BackgroundTask.idempotency_key == idempotency_key)
            )
        ).scalar_one_or_none()
        if existing is not None:
            return existing

    task = BackgroundTask(
        kind=kind,
        state=BackgroundTaskState.PENDING,
        payload=payload,
        idempotency_key=idempotency_key,
        created_by_id=created_by_id,
        resource_type=resource_type,
        resource_id=resource_id,
    )
    session.add(task)
    await session.flush()

    # Handlers frequently need their own row id (to report progress, or to name an output
    # object deterministically), so it is injected rather than threaded through by hand.
    task.payload = {**payload, "_task_row_id": str(task.id)}
    await session.flush()

    # The row must be visible before a worker can pick it up.
    await session.commit()

    job_queue = queue or get_job_queue()
    await job_queue.enqueue(
        kind,
        task.payload,
        task_id=str(task.id),
        queue=QUEUE_ROUTING.get(kind, "default"),
        delay_seconds=delay_seconds,
    )
    return task
