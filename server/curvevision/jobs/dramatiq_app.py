"""Dramatiq wiring.

Isolated in its own module so that importing ``curvevision.jobs`` never requires the
``worker`` extra to be installed.
"""

from __future__ import annotations

import asyncio
from typing import Any

_configured = False


def configure_broker(redis_url: str) -> None:
    global _configured
    if _configured:
        return
    import dramatiq
    from dramatiq.brokers.redis import RedisBroker

    broker = RedisBroker(url=redis_url)
    dramatiq.set_broker(broker)
    _configured = True


try:  # pragma: no cover - depends on the optional worker extra
    import dramatiq

    @dramatiq.actor(max_retries=5, min_backoff=1_000, max_backoff=300_000, time_limit=3_600_000)
    def execute_background_task(task_id: str) -> None:
        """Dramatiq entrypoint: run one ``BackgroundTask`` row.

        Retries are Dramatiq's; idempotency is the handler's. ``run_task`` refuses to
        re-run a task that already succeeded, so a retry after a partial failure resumes
        rather than duplicates.
        """
        from curvevision.jobs.runner import run_task

        asyncio.run(run_task(task_id))

except ImportError:  # pragma: no cover - the API image does not need dramatiq

    class _Unavailable:
        def send_with_options(self, *_args: Any, **_kwargs: Any) -> None:
            raise RuntimeError(
                "The dramatiq job backend requires the 'worker' extra: "
                "pip install 'curvevision-server[worker]'"
            )

    execute_background_task = _Unavailable()
