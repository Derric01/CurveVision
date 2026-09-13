"""Webhook delivery, and what happens when the receiver is having a bad day.

Delivery has been signed and recorded since the first iteration. What it never had was a
second attempt: a receiver that was restarting when the event fired lost it permanently, and
the operator had no way to know beyond reading the deliveries table. These tests cover the
retry, and they cover the two judgement calls it rests on.

**Who is wrong decides whether to retry.** A 5xx or a refused connection says the receiver is
struggling and the same request may well work later. A 4xx says the receiver understood and
rejected it — a rotated secret, a decommissioned path — and repeating it cannot change the
answer. Retrying those is not resilience; it is a slow burst of identical failing requests
against somebody's endpoint.

**A response is not a delivery.** The original code stamped `delivered_at` for *any*
response, 500 included, which made a failed delivery indistinguishable from a good one — and
would have made every retry a no-op, since a delivery that looks delivered is never retried.
That is fixed here and pinned by `test_a_server_error_is_not_recorded_as_delivered`.
"""

from __future__ import annotations

import json
import uuid
from typing import Any

import httpx
import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from curvevision.core.security import sign_webhook_payload
from curvevision.domain.enums import WebhookEvent
from curvevision.domain.system import Webhook, WebhookDelivery
from curvevision.services import webhooks as webhook_service

# ------------------------------------------------------------------ the retry policy


class TestShouldRetry:
    """Which failures are worth another go."""

    def test_a_request_that_never_reached_anybody_is_retried(self) -> None:
        # DNS failures, refused connections, TLS errors and read timeouts all arrive with no
        # status code at all. None of them says anything about the request being wrong.
        assert webhook_service.should_retry(None, attempts=1) is True

    @pytest.mark.parametrize("status", [500, 502, 503, 504])
    def test_a_server_error_is_retried(self, status: int) -> None:
        assert webhook_service.should_retry(status, attempts=1) is True

    @pytest.mark.parametrize("status", [400, 401, 403, 404, 410, 422])
    def test_a_client_error_is_not_retried(self, status: int) -> None:
        """The receiver understood and said no. Asking again cannot change the answer."""
        assert webhook_service.should_retry(status, attempts=1) is False

    @pytest.mark.parametrize("status", [408, 429])
    def test_the_two_client_errors_that_mean_later_are_retried(self, status: int) -> None:
        """408 and 429 both say "not now" rather than "not ever"."""
        assert webhook_service.should_retry(status, attempts=1) is True

    @pytest.mark.parametrize("status", [200, 201, 204, 302])
    def test_a_success_is_not_retried(self, status: int) -> None:
        assert webhook_service.should_retry(status, attempts=1) is False

    def test_retrying_stops_at_the_cap(self) -> None:
        assert webhook_service.should_retry(500, attempts=webhook_service.MAX_ATTEMPTS - 1) is True
        assert webhook_service.should_retry(500, attempts=webhook_service.MAX_ATTEMPTS) is False
        assert webhook_service.should_retry(None, attempts=webhook_service.MAX_ATTEMPTS) is False


class TestRetryDelay:
    def test_each_attempt_waits_twice_as_long(self) -> None:
        base = webhook_service.RETRY_BASE_SECONDS
        assert webhook_service.retry_delay(1, jitter=False) == base
        assert webhook_service.retry_delay(2, jitter=False) == base * 2
        assert webhook_service.retry_delay(3, jitter=False) == base * 4

    def test_the_wait_is_capped(self) -> None:
        """A long outage should keep retrying steadily, not drift to hours between tries."""
        assert webhook_service.retry_delay(40, jitter=False) == webhook_service.RETRY_MAX_SECONDS

    def test_jitter_spreads_the_herd_without_leaving_the_neighbourhood(self) -> None:
        # A receiver that falls over drops every delivery in flight at once; retrying them
        # all on the same schedule reproduces the spike exactly when it can least take it.
        base = webhook_service.retry_delay(3, jitter=False)
        samples = [webhook_service.retry_delay(3) for _ in range(200)]
        assert all(base * 0.8 <= sample <= base * 1.2 for sample in samples)
        assert len(set(samples)) > 1, "jitter that never varies is not jitter"


# ------------------------------------------------------------------- delivery itself


def transport(handler: Any) -> httpx.AsyncClient:
    return httpx.AsyncClient(transport=httpx.MockTransport(handler))


async def make_webhook(session: AsyncSession, project_id: uuid.UUID | None = None) -> Webhook:
    hook = Webhook(
        project_id=project_id,
        target_url="https://receiver.example/hook",
        secret=webhook_service.generate_secret(),
        events=[WebhookEvent.TASK_COMPLETED.value],
        is_active=True,
    )
    session.add(hook)
    await session.flush()
    return hook


async def make_delivery(session: AsyncSession, hook: Webhook) -> WebhookDelivery:
    delivery = WebhookDelivery(
        webhook_id=hook.id,
        event=WebhookEvent.TASK_COMPLETED.value,
        payload={"task_id": "abc"},
    )
    session.add(delivery)
    await session.flush()
    return delivery


async def test_a_server_error_is_not_recorded_as_delivered(session: AsyncSession) -> None:
    """The bug that would have made every retry a no-op.

    `delivered_at` used to be stamped for any response at all. A 500 then looked exactly
    like a 200 to anything reading the table — including the retry scheduler, which skips
    anything already delivered.
    """
    hook = await make_webhook(session)
    delivery = await make_delivery(session, hook)

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(500, text="upstream exploded")

    async with transport(handler) as client:
        body, headers = webhook_service._delivery_body(hook, delivery)
        await webhook_service.attempt_delivery(hook, delivery, body, headers, client=client)

    assert delivery.status_code == 500
    assert delivery.delivered_at is None, "a 500 is not a delivery"
    assert delivery.error == "upstream exploded"
    assert delivery.attempts == 1


async def test_a_success_is_recorded_as_delivered(session: AsyncSession) -> None:
    hook = await make_webhook(session)
    delivery = await make_delivery(session, hook)

    async with transport(lambda request: httpx.Response(204)) as client:
        body, headers = webhook_service._delivery_body(hook, delivery)
        await webhook_service.attempt_delivery(hook, delivery, body, headers, client=client)

    assert delivery.status_code == 204
    assert delivery.delivered_at is not None
    assert delivery.error is None
    assert delivery.attempts == 1


async def test_a_retry_sends_the_same_delivery_id_so_a_receiver_can_deduplicate(
    session: AsyncSession,
) -> None:
    """A retry is the *same* event again, and a receiver needs to be able to tell.

    If each attempt carried a fresh id, a receiver that processed the first attempt and then
    timed out replying would process the retry as a second, separate event — which for
    something like "task completed" means doing the downstream work twice.
    """
    hook = await make_webhook(session)
    delivery = await make_delivery(session, hook)
    seen: list[dict[str, Any]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(
            {
                "delivery": request.headers["x-curvevision-delivery"],
                "signature": request.headers["x-curvevision-signature"],
                "body": request.content,
            }
        )
        return httpx.Response(503)

    async with transport(handler) as client:
        body, headers = webhook_service._delivery_body(hook, delivery)
        await webhook_service.attempt_delivery(hook, delivery, body, headers, client=client)
        await webhook_service.redeliver(session, delivery.id, client=client)

    assert len(seen) == 2
    assert seen[0]["delivery"] == seen[1]["delivery"] == str(delivery.id)
    assert seen[0]["body"] == seen[1]["body"], "a retry must resend the same bytes"
    assert delivery.attempts == 2


async def test_every_attempt_carries_a_signature_that_verifies(session: AsyncSession) -> None:
    """A retry that shipped an unsigned or wrongly signed body would be rejected forever."""
    hook = await make_webhook(session)
    delivery = await make_delivery(session, hook)
    checked: list[bool] = []

    def handler(request: httpx.Request) -> httpx.Response:
        expected = sign_webhook_payload(hook.secret, request.content)
        checked.append(request.headers["x-curvevision-signature"] == expected)
        return httpx.Response(500)

    async with transport(handler) as client:
        body, headers = webhook_service._delivery_body(hook, delivery)
        await webhook_service.attempt_delivery(hook, delivery, body, headers, client=client)
        await webhook_service.redeliver(session, delivery.id, client=client)

    assert checked == [True, True]


async def test_a_retry_that_succeeds_clears_the_earlier_error(session: AsyncSession) -> None:
    """Otherwise a delivered event keeps an error on it and reads as both."""
    hook = await make_webhook(session)
    delivery = await make_delivery(session, hook)
    responses = [httpx.Response(503, text="try later"), httpx.Response(200)]

    def handler(request: httpx.Request) -> httpx.Response:
        return responses.pop(0)

    async with transport(handler) as client:
        body, headers = webhook_service._delivery_body(hook, delivery)
        await webhook_service.attempt_delivery(hook, delivery, body, headers, client=client)
        assert delivery.error == "try later"

        await webhook_service.redeliver(session, delivery.id, client=client)

    assert delivery.status_code == 200
    assert delivery.delivered_at is not None
    assert delivery.error is None
    assert delivery.attempts == 2


async def test_a_delivery_that_already_succeeded_is_not_sent_again(
    session: AsyncSession,
) -> None:
    """A duplicate retry job, or one queued before the success landed, must not re-send."""
    hook = await make_webhook(session)
    delivery = await make_delivery(session, hook)
    calls = {"count": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        calls["count"] += 1
        return httpx.Response(200)

    async with transport(handler) as client:
        body, headers = webhook_service._delivery_body(hook, delivery)
        await webhook_service.attempt_delivery(hook, delivery, body, headers, client=client)
        await webhook_service.redeliver(session, delivery.id, client=client)
        await webhook_service.redeliver(session, delivery.id, client=client)

    assert calls["count"] == 1, "a delivered event must not be delivered twice"
    assert delivery.attempts == 1


async def test_a_deactivated_webhook_is_not_retried(session: AsyncSession) -> None:
    """Turning a webhook off must stop the retries already in flight, not just new events."""
    hook = await make_webhook(session)
    delivery = await make_delivery(session, hook)
    calls = {"count": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        calls["count"] += 1
        return httpx.Response(500)

    async with transport(handler) as client:
        body, headers = webhook_service._delivery_body(hook, delivery)
        await webhook_service.attempt_delivery(hook, delivery, body, headers, client=client)
        hook.is_active = False
        await session.flush()
        await webhook_service.redeliver(session, delivery.id, client=client)

    assert calls["count"] == 1


async def test_a_network_failure_leaves_no_status_code(session: AsyncSession) -> None:
    """Which is what `should_retry` reads to tell "never arrived" from "was rejected"."""
    hook = await make_webhook(session)
    delivery = await make_delivery(session, hook)

    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("connection refused")

    async with transport(handler) as client:
        body, headers = webhook_service._delivery_body(hook, delivery)
        await webhook_service.attempt_delivery(hook, delivery, body, headers, client=client)

    assert delivery.status_code is None
    assert delivery.delivered_at is None
    assert "refused" in (delivery.error or "")
    assert webhook_service.should_retry(delivery.status_code, attempts=delivery.attempts) is True


async def test_exhausted_names_the_deliveries_nobody_will_try_again(
    session: AsyncSession,
) -> None:
    """Derived from what is already recorded rather than stored as its own column."""
    hook = await make_webhook(session)

    delivered = await make_delivery(session, hook)
    async with transport(lambda request: httpx.Response(200)) as client:
        body, headers = webhook_service._delivery_body(hook, delivered)
        await webhook_service.attempt_delivery(hook, delivered, body, headers, client=client)
    assert webhook_service.exhausted(delivered) is False

    rejected = await make_delivery(session, hook)
    async with transport(lambda request: httpx.Response(404)) as client:
        body, headers = webhook_service._delivery_body(hook, rejected)
        await webhook_service.attempt_delivery(hook, rejected, body, headers, client=client)
    assert webhook_service.exhausted(rejected) is True, "a 404 is never retried, so it is done"

    trying = await make_delivery(session, hook)
    async with transport(lambda request: httpx.Response(503)) as client:
        body, headers = webhook_service._delivery_body(hook, trying)
        await webhook_service.attempt_delivery(hook, trying, body, headers, client=client)
    assert webhook_service.exhausted(trying) is False, "still has attempts left"

    trying.attempts = webhook_service.MAX_ATTEMPTS
    assert webhook_service.exhausted(trying) is True


async def test_the_body_is_the_event_the_receiver_expects(session: AsyncSession) -> None:
    """The wire format is a contract; a retry rebuilds it from the stored payload."""
    hook = await make_webhook(session)
    delivery = await make_delivery(session, hook)

    body, headers = webhook_service._delivery_body(hook, delivery)
    decoded = json.loads(body)

    assert decoded == {
        "event": WebhookEvent.TASK_COMPLETED.value,
        "delivery_id": str(delivery.id),
        "data": {"task_id": "abc"},
    }
    assert headers["x-curvevision-event"] == WebhookEvent.TASK_COMPLETED.value


# ------------------------------------------------------- scheduling the next attempt


class RecordingQueue:
    """A queue that records rather than runs.

    The test suite's real queue runs jobs synchronously, which for a retry means actually
    posting to the receiver — and then scheduling the next retry from inside that, all the
    way to the cap. What these tests are about is *what gets scheduled and when*, so the
    scheduling is captured instead of executed.
    """

    def __init__(self) -> None:
        self.scheduled: list[dict[str, Any]] = []

    async def enqueue(
        self,
        kind: str,
        payload: dict[str, Any],
        *,
        task_id: str,
        queue: str = "default",
        delay_seconds: float = 0.0,
    ) -> Any:
        from curvevision.jobs.base import JobSubmission

        self.scheduled.append(
            {"kind": kind, "payload": payload, "queue": queue, "delay": delay_seconds}
        )
        return JobSubmission(task_id=task_id, queue=queue, kind=kind)


@pytest.fixture
def recording_queue() -> Any:
    from curvevision.jobs import set_job_queue

    queue = RecordingQueue()
    set_job_queue(queue)
    yield queue
    set_job_queue(None)


async def test_a_failing_delivery_schedules_a_delayed_retry(
    session: AsyncSession, recording_queue: Any
) -> None:
    from curvevision.jobs.tasks import _schedule_retries

    hook = await make_webhook(session)
    delivery = await make_delivery(session, hook)
    delivery.status_code = 503
    delivery.attempts = 1

    await _schedule_retries(session, [delivery])

    assert len(recording_queue.scheduled) == 1
    scheduled = recording_queue.scheduled[0]
    assert scheduled["kind"] == "webhook.retry"
    assert scheduled["payload"]["delivery_id"] == str(delivery.id)
    assert scheduled["queue"] == "webhooks"
    # Delayed, not immediate: retrying a struggling receiver at once is how you keep it down.
    base = webhook_service.retry_delay(1, jitter=False)
    assert base * 0.8 <= scheduled["delay"] <= base * 1.2


async def test_a_rejected_delivery_schedules_nothing(
    session: AsyncSession, recording_queue: Any
) -> None:
    """A 404 will be a 404 next time too."""
    from curvevision.jobs.tasks import _schedule_retries

    hook = await make_webhook(session)
    delivery = await make_delivery(session, hook)
    delivery.status_code = 404
    delivery.attempts = 1

    await _schedule_retries(session, [delivery])

    assert recording_queue.scheduled == []


async def test_a_delivered_event_schedules_nothing(
    session: AsyncSession, recording_queue: Any
) -> None:
    from curvevision.core.db import utcnow
    from curvevision.jobs.tasks import _schedule_retries

    hook = await make_webhook(session)
    delivery = await make_delivery(session, hook)
    delivery.status_code = 200
    delivery.delivered_at = utcnow()
    delivery.attempts = 1

    await _schedule_retries(session, [delivery])

    assert recording_queue.scheduled == []


async def test_the_retry_chain_terminates_rather_than_running_forever(
    session: AsyncSession, recording_queue: Any
) -> None:
    """The cap is what stops a permanently broken receiver being retried indefinitely."""
    from curvevision.jobs.tasks import _schedule_retries

    hook = await make_webhook(session)
    delivery = await make_delivery(session, hook)
    delivery.status_code = 500

    for attempt in range(1, webhook_service.MAX_ATTEMPTS + 2):
        delivery.attempts = attempt
        await _schedule_retries(session, [delivery])

    # One retry queued per attempt below the cap, and nothing after it.
    assert len(recording_queue.scheduled) == webhook_service.MAX_ATTEMPTS - 1
    delays = [entry["delay"] for entry in recording_queue.scheduled]
    assert delays == sorted(delays), "each wait should be at least as long as the last"
