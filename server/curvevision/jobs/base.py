"""Background job abstraction.

Two implementations back this: ``InlineJobQueue`` (runs the handler immediately, used in
tests and in dev so a contributor needs no broker) and ``DramatiqJobQueue`` (Redis broker,
queue routing, retries).

Every handler must be **idempotent**. A ``BackgroundTask`` row keyed by an idempotency key
is the contract: a redelivery after a worker crash finds the existing row and resumes
rather than duplicating work.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any, Protocol

#: A handler receives the deserialised payload and returns a JSON-serialisable result.
JobHandler = Callable[[dict[str, Any]], Awaitable[dict[str, Any]]]


@dataclass(frozen=True, slots=True)
class JobSubmission:
    task_id: str
    queue: str
    kind: str


class JobQueue(Protocol):
    async def enqueue(
        self,
        kind: str,
        payload: dict[str, Any],
        *,
        task_id: str,
        queue: str = ...,
        delay_seconds: float = ...,
    ) -> JobSubmission:
        """Schedule ``kind`` for execution against the ``BackgroundTask`` row ``task_id``.

        ``delay_seconds`` holds the job back for at least that long. It exists for retry
        backoff: a webhook whose receiver is down must be tried again later rather than
        immediately, and "later" has to be expressed to the queue rather than by sleeping
        inside a handler and holding a worker.
        """


_HANDLERS: dict[str, JobHandler] = {}

#: Queue names. One worker image serves all of them; an operator who wants isolation runs
#: `curvevision-worker --queues media` rather than deploying a different service.
QUEUES = ("default", "media", "import", "export", "inference", "webhooks", "quality")


def register_handler(kind: str, handler: JobHandler) -> None:
    if kind in _HANDLERS and _HANDLERS[kind] is not handler:
        raise ValueError(f"Duplicate background job handler for {kind!r}")
    _HANDLERS[kind] = handler


def job_handler(kind: str) -> Callable[[JobHandler], JobHandler]:
    def decorator(fn: JobHandler) -> JobHandler:
        register_handler(kind, fn)
        return fn

    return decorator


def get_handler(kind: str) -> JobHandler:
    try:
        return _HANDLERS[kind]
    except KeyError as exc:
        raise LookupError(f"No background job handler registered for {kind!r}") from exc


def registered_kinds() -> list[str]:
    return sorted(_HANDLERS)
