"""Background job queue construction and the two backends."""

from __future__ import annotations

import asyncio
from typing import Any

from curvevision.core.config import Settings, get_settings
from curvevision.core.logging import get_logger
from curvevision.jobs.base import (
    QUEUES,
    JobHandler,
    JobQueue,
    JobSubmission,
    get_handler,
    job_handler,
    register_handler,
    registered_kinds,
)
from curvevision.jobs.runner import report_progress, run_task

logger = get_logger(__name__)

__all__ = [
    "QUEUES",
    "InlineJobQueue",
    "JobHandler",
    "JobQueue",
    "JobSubmission",
    "get_handler",
    "get_job_queue",
    "job_handler",
    "register_handler",
    "registered_kinds",
    "report_progress",
    "run_task",
    "set_job_queue",
]


class InlineJobQueue:
    """Runs the job immediately, in this process.

    Used by the test suite and by ``development`` mode so that import/export, media
    processing and inference can be exercised end to end with no broker running. The
    handler is scheduled on the running event loop rather than awaited, so an HTTP request
    that enqueues work still returns promptly.
    """

    def __init__(self, *, wait: bool = False) -> None:
        self._wait = wait
        self._pending: set[asyncio.Task[Any]] = set()

    async def enqueue(
        self,
        kind: str,
        payload: dict[str, Any],
        *,
        task_id: str,
        queue: str = "default",
        delay_seconds: float = 0.0,
    ) -> JobSubmission:
        get_handler(kind)  # fail fast on an unregistered kind, before the row is committed
        if self._wait:
            # Deliberately ignores the delay. This mode exists so a test can drive a job to
            # completion synchronously; honouring a webhook's 80-second backoff would make
            # the suite sleep for real, and the thing under test is the retry happening, not
            # the wall clock.
            await run_task(task_id)
        else:
            task = asyncio.create_task(self._run_quietly(task_id, delay_seconds))
            self._pending.add(task)
            task.add_done_callback(self._pending.discard)
        return JobSubmission(task_id=task_id, queue=queue, kind=kind)

    async def drain(self) -> None:
        """Await every scheduled job. Test helper."""
        while self._pending:
            await asyncio.gather(*tuple(self._pending), return_exceptions=True)

    @staticmethod
    async def _run_quietly(task_id: str, delay_seconds: float = 0.0) -> None:
        try:
            if delay_seconds > 0:
                await asyncio.sleep(delay_seconds)
            await run_task(task_id)
        except asyncio.CancelledError:
            # Shutdown while waiting out a backoff. The row stays pending rather than being
            # marked failed, because nothing was attempted.
            raise
        except Exception:
            logger.warning("inline job failed", extra={"task_id": task_id})


class DramatiqJobQueue:
    """Publishes to Redis via Dramatiq.

    Dramatiq is wrapped rather than used directly across the codebase so that swapping it
    -- or running without a broker at all -- stays a one-line configuration change.
    """

    def __init__(self, settings: Settings) -> None:
        from curvevision.jobs.dramatiq_app import configure_broker, execute_background_task

        configure_broker(settings.redis_url)
        self._actor = execute_background_task

    async def enqueue(
        self,
        kind: str,
        payload: dict[str, Any],
        *,
        task_id: str,
        queue: str = "default",
        delay_seconds: float = 0.0,
    ) -> JobSubmission:
        get_handler(kind)
        options: dict[str, Any] = {"args": (task_id,), "queue_name": queue}
        if delay_seconds > 0:
            # Dramatiq counts the delay in milliseconds.
            options["delay"] = int(delay_seconds * 1000)
        await asyncio.to_thread(self._actor.send_with_options, **options)
        return JobSubmission(task_id=task_id, queue=queue, kind=kind)


_queue: JobQueue | None = None


def get_job_queue(settings: Settings | None = None) -> JobQueue:
    global _queue
    if _queue is None:
        settings = settings or get_settings()
        _queue = (
            DramatiqJobQueue(settings)
            if settings.job_queue_backend == "dramatiq"
            else InlineJobQueue()
        )
    return _queue


def set_job_queue(queue: JobQueue | None) -> None:
    """Override the process-wide queue. Used by tests and by the worker entrypoint."""
    global _queue
    _queue = queue
