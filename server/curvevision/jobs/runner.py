"""Executes a background job against its ``BackgroundTask`` row.

Both queue backends funnel through ``run_task``, so state transitions, progress, error
capture and idempotency behave identically whether the work ran inline in a test or on a
worker in production.
"""

from __future__ import annotations

from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from curvevision.core.db import get_sessionmaker, utcnow
from curvevision.core.logging import get_logger
from curvevision.domain.enums import BackgroundTaskState
from curvevision.domain.system import BackgroundTask
from curvevision.jobs.base import get_handler

logger = get_logger(__name__)

#: States from which a task must not be re-run. Re-delivery of a finished task is a
#: no-op rather than an error: at-least-once delivery is the normal case.
_TERMINAL = frozenset({BackgroundTaskState.SUCCEEDED, BackgroundTaskState.CANCELLED})


async def run_task(task_id: str) -> dict[str, Any]:
    """Run the background task identified by ``task_id``. Safe to call more than once."""
    factory = get_sessionmaker()
    async with factory() as session:
        task = await _load(session, task_id)
        if task is None:
            logger.warning("background task missing", extra={"task_id": task_id})
            return {}
        if task.state in _TERMINAL:
            logger.info(
                "background task already finished; skipping",
                extra={"task_id": task_id, "state": task.state},
            )
            return dict(task.result)

        handler = get_handler(task.kind)
        task.state = BackgroundTaskState.RUNNING
        task.started_at = utcnow()
        task.attempts += 1
        task.error = None
        await session.commit()
        payload = dict(task.payload)
        kind = task.kind

    try:
        result = await handler(payload)
    except Exception as exc:
        logger.exception("background task failed", extra={"task_id": task_id, "kind": kind})
        async with factory() as session:
            task = await _load(session, task_id)
            if task is not None:
                task.state = BackgroundTaskState.FAILED
                task.error = f"{type(exc).__name__}: {exc}"
                task.finished_at = utcnow()
                await session.commit()
        raise

    async with factory() as session:
        task = await _load(session, task_id)
        if task is not None:
            task.state = BackgroundTaskState.SUCCEEDED
            task.result = result
            task.progress = 1.0
            task.finished_at = utcnow()
            await session.commit()
    return result


async def report_progress(task_id: str, progress: float, message: str | None = None) -> None:
    """Update a running task's progress. Best effort: never fails the job."""
    factory = get_sessionmaker()
    try:
        async with factory() as session:
            task = await _load(session, task_id)
            if task is None:
                return
            task.progress = max(0.0, min(1.0, progress))
            if message:
                task.message = message[:500]
            await session.commit()
    except Exception:
        logger.warning("failed to report progress", extra={"task_id": task_id})


async def _load(session: AsyncSession, task_id: str) -> BackgroundTask | None:
    result = await session.execute(select(BackgroundTask).where(BackgroundTask.id == task_id))
    return result.scalar_one_or_none()
