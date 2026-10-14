"""Background jobs go to the Redis the deployment names.

`@dramatiq.actor` binds the actor to whichever broker is current when the module is
imported. Until iteration 85 that was Dramatiq's default — Redis on localhost:6379 — in
both processes: the worker never set a broker before importing the module, and the API set
one only after, which leaves an already-declared actor where it was. Under Docker Compose,
where Redis is `redis:6379`, no background job was ever enqueued or consumed; the first CI
job to run the compose stack found the worker retrying localhost.

Each test imports the module in a fresh interpreter, as the worker and the API do.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import textwrap
import uuid

import pytest

pytest.importorskip("dramatiq", reason="the worker extra")

REDIS = os.environ.get("CURVEVISION_TEST_REDIS_URL")


def fresh_interpreter(code: str, redis_url: str) -> dict[str, object]:
    env = {**os.environ, "CURVEVISION_REDIS_URL": redis_url}
    done = subprocess.run(
        [sys.executable, "-c", textwrap.dedent(code)],
        env=env,
        capture_output=True,
        text=True,
        timeout=60,
        check=False,
    )
    assert done.returncode == 0, done.stderr
    return dict(json.loads(done.stdout.strip().splitlines()[-1]))


WHERE = """
def where(broker):
    kwargs = broker.client.connection_pool.connection_kwargs
    return {"host": kwargs.get("host"), "port": kwargs.get("port"), "db": kwargs.get("db")}
"""


def test_the_worker_consumes_from_the_configured_redis() -> None:
    """What `dramatiq curvevision.jobs.dramatiq_app` does: import, then use the broker."""
    seen = fresh_interpreter(
        WHERE
        + """
import json, dramatiq
from curvevision.jobs import dramatiq_app
actor = dramatiq_app.execute_background_task
print(json.dumps({"global": where(dramatiq.get_broker()), "actor": where(actor.broker),
                  "same": actor.broker is dramatiq.get_broker()}))
""",
        "redis://redis:6390/3",
    )
    assert seen["global"] == {"host": "redis", "port": 6390, "db": 3}
    assert seen["same"] is True


def test_the_api_enqueues_where_it_is_configured() -> None:
    """What `DramatiqJobQueue` does: import, then `configure_broker(settings.redis_url)`."""
    seen = fresh_interpreter(
        WHERE
        + """
import json, dramatiq
from curvevision.jobs import dramatiq_app
dramatiq_app.configure_broker("redis://elsewhere:7000/1")
actor = dramatiq_app.execute_background_task
print(json.dumps({"actor": where(actor.broker), "same": actor.broker is dramatiq.get_broker()}))
""",
        "redis://redis:6390/3",
    )
    assert seen["actor"] == {"host": "elsewhere", "port": 7000, "db": 1}
    assert seen["same"] is True


@pytest.mark.skipif(not REDIS, reason="set CURVEVISION_TEST_REDIS_URL to run against Redis")
def test_an_enqueued_job_lands_in_that_redis() -> None:
    queue = f"probe-{uuid.uuid4().hex[:8]}"
    seen = fresh_interpreter(
        f"""
import json, redis
from curvevision.jobs import dramatiq_app
actor = dramatiq_app.execute_background_task
actor.queue_name = {queue!r}
actor.broker.declare_queue({queue!r})
actor.send_with_options(args=("not-a-real-task",))
client = redis.Redis.from_url({REDIS!r})
waiting = client.hlen("dramatiq:{queue}.msgs")
client.delete("dramatiq:{queue}", "dramatiq:{queue}.msgs")
print(json.dumps({{"waiting": waiting}}))
""",
        REDIS or "",
    )
    assert seen["waiting"] == 1


def test_the_worker_knows_every_kind_of_job_the_api_queues() -> None:
    """A worker imports only this module; the API's handlers register as its startup imports
    them. Until iteration 85 the worker therefore failed every job it received."""
    seen = fresh_interpreter(
        """
import json
from curvevision.jobs import dramatiq_app, get_handler
kinds = ["media.probe_task", "media.reclaim_blobs"]
found = {}
for kind in kinds:
    try:
        get_handler(kind)
        found[kind] = True
    except LookupError:
        found[kind] = False
print(json.dumps(found))
""",
        "redis://redis:6390/3",
    )
    assert seen == {"media.probe_task": True, "media.reclaim_blobs": True}
