"""Dramatiq wiring.

Isolated in its own module so that importing ``curvevision.jobs`` never requires the
``worker`` extra to be installed.
"""

from __future__ import annotations

import asyncio
from typing import Any

_configured_url: str | None = None


def configure_broker(redis_url: str) -> None:
    """Send and consume through the Redis at `redis_url`.

    The actor below is bound, when this module is imported, to the broker current at that
    moment, and setting a new broker afterwards does not move it — which is how every job
    once went to Dramatiq's default, localhost:6379, whatever the deployment said. So the
    module sets the broker from the settings before declaring the actor (the worker's
    path), and this rebinds the actor too (the API's, whose settings may be its own).
    """
    global _configured_url
    if redis_url == _configured_url:
        return
    import dramatiq
    from dramatiq.brokers.redis import RedisBroker

    broker = RedisBroker(url=redis_url)
    dramatiq.set_broker(broker)
    actor = globals().get("execute_background_task")
    if isinstance(actor, dramatiq.Actor):
        actor.broker = broker
        broker.declare_actor(actor)
    _configured_url = redis_url


_traced = False


def _trace_this_worker() -> None:
    """Turn tracing on in a worker process once, when its settings ask for it: the API
    process does it in `create_app`, which a worker never calls."""
    global _traced
    if _traced:
        return
    from curvevision.core import tracing
    from curvevision.core.config import get_settings
    from curvevision.core.db import get_engine

    tracing.configure(get_settings())
    tracing.instrument_engine(get_engine())
    _traced = True


try:  # pragma: no cover - depends on the optional worker extra
    import dramatiq

    from curvevision.core.config import get_settings

    # Before the actor is declared, so it is bound to the deployment's Redis. A worker
    # (`dramatiq curvevision.jobs.dramatiq_app`) does nothing else before consuming.
    configure_broker(get_settings().redis_url)

    # What the API's startup imports (`main.lifespan`), because handlers, formats and model
    # providers register themselves on import, and a worker imports nothing but this
    # module: without these it received every job and failed it as unknown.
    import curvevision.formats
    import curvevision.jobs.tasks
    import curvevision.ml  # noqa: F401

    @dramatiq.actor(max_retries=5, min_backoff=1_000, max_backoff=300_000, time_limit=3_600_000)
    def execute_background_task(task_id: str) -> None:
        """Dramatiq entrypoint: run one ``BackgroundTask`` row.

        Retries are Dramatiq's; idempotency is the handler's. ``run_task`` refuses to
        re-run a task that already succeeded, so a retry after a partial failure resumes
        rather than duplicates.
        """
        from curvevision.jobs.runner import run_task

        _trace_this_worker()
        asyncio.run(run_task(task_id))

except ImportError:  # pragma: no cover - the API image does not need dramatiq

    class _Unavailable:
        def send_with_options(self, *_args: Any, **_kwargs: Any) -> None:
            raise RuntimeError(
                "The dramatiq job backend requires the 'worker' extra: "
                "pip install 'curvevision-server[worker]'"
            )

    execute_background_task = _Unavailable()  # type: ignore[assignment]
