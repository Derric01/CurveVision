"""Webhook dispatch.

Deliveries are HMAC-SHA256 signed so a receiver can verify the payload actually came from
this instance:

    X-CurveVision-Signature: sha256=<hex>
    X-CurveVision-Event: task.completed
    X-CurveVision-Delivery: <uuid>

The signature covers the exact request body. Verify it with a constant-time comparison.
"""

from __future__ import annotations

import json
import random
import secrets
import uuid
from typing import Any

import httpx
from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from curvevision.core.db import utcnow
from curvevision.core.logging import get_logger
from curvevision.core.security import sign_webhook_payload
from curvevision.domain.enums import WebhookEvent
from curvevision.domain.system import Webhook, WebhookDelivery

logger = get_logger(__name__)

DELIVERY_TIMEOUT = 10.0

#: Where deliveries go when no client is passed: the network, unless a test puts an
#: `httpx.MockTransport` here to stand in for a receiver.
transport: httpx.AsyncBaseTransport | None = None

#: How many times a delivery is attempted in total, the first attempt included.
MAX_ATTEMPTS = 5
#: The first retry waits this long; each subsequent one doubles it.
RETRY_BASE_SECONDS = 10.0
#: No single wait exceeds this, so a long outage retries steadily rather than at ever
#: longer intervals until the last attempt lands hours later.
RETRY_MAX_SECONDS = 600.0


def should_retry(status_code: int | None, *, attempts: int) -> bool:
    """Whether a failed delivery is worth attempting again.

    The distinction that matters is **who is wrong**. A 5xx, a timeout or a refused
    connection say the receiver is having a bad time; the same request later may well
    succeed, and giving up would lose an event the operator asked for. A 4xx says the
    receiver understood the request and rejected it — a 401 with a rotated secret, a 404 at a
    decommissioned path, a 410 Gone — and repeating it cannot change the answer. Retrying
    those is not resilience: it is a slow burst of identical failing requests against
    somebody's endpoint, which looks exactly like an attack and fills their logs.

    Two 4xx codes are exceptions, because both explicitly mean "later, not never":

    * **408 Request Timeout** — the receiver gave up waiting, not the request being wrong.
    * **429 Too Many Requests** — the receiver is asking for a pause, so pausing and
      retrying is the co-operative response rather than a workaround.

    A delivery with no status code at all never reached a receiver — DNS, TLS, connect and
    read timeouts all land here — and is always worth another go.
    """
    if attempts >= MAX_ATTEMPTS:
        return False
    if status_code is None:
        return True
    if status_code < 400:
        return False
    if status_code in (408, 429):
        return True
    return status_code >= 500


def retry_delay(attempts: int, *, jitter: bool = True) -> float:
    """Seconds to wait before attempt number ``attempts + 1``.

    Exponential and capped. The jitter is not decoration: a receiver that falls over drops
    every delivery in flight at once, and retrying them all on the same schedule reproduces
    the original spike exactly when the receiver is least able to take it. Spreading them
    over ±20% turns one thundering herd into an ordinary trickle.
    """
    base = min(RETRY_BASE_SECONDS * (2 ** max(0, attempts - 1)), RETRY_MAX_SECONDS)
    if not jitter:
        return base
    # Not cryptography: this spreads load, and a predictable spread is fine.
    return base * random.uniform(0.8, 1.2)


def exhausted(delivery: WebhookDelivery) -> bool:
    """True when a delivery has stopped being retried and never succeeded.

    Derived rather than stored: a delivery that ran out of attempts and one that failed
    unretryably are the same thing to an operator — nobody is going to try again — and a
    column duplicating what `attempts`, `status_code` and `delivered_at` already say is one
    more thing that can disagree with them.
    """
    if delivery.status_code is not None and delivery.status_code < 400:
        return False
    return not should_retry(delivery.status_code, attempts=delivery.attempts)


def generate_secret() -> str:
    return secrets.token_urlsafe(32)


async def matching_webhooks(
    session: AsyncSession,
    event: WebhookEvent,
    *,
    organization_id: uuid.UUID | None,
    project_id: uuid.UUID | None,
) -> list[Webhook]:
    """Webhooks subscribed to ``event`` for this project or its organization."""
    conditions = []
    if project_id is not None:
        # A project's webhook must also belong to the project's workspace. Creating one is
        # checked for that (iteration 96); this keeps a row written before the check from
        # hearing another workspace's project.
        same_workspace = (
            Webhook.organization_id == organization_id if organization_id is not None else True
        )
        conditions.append((Webhook.project_id == project_id) & same_workspace)
    if organization_id is not None:
        conditions.append(
            (Webhook.organization_id == organization_id) & Webhook.project_id.is_(None)
        )
    if not conditions:
        return []

    result = await session.execute(
        select(Webhook).where(Webhook.is_active.is_(True), or_(*conditions))
    )
    # The events list is a JSON column; filtering it in SQL portably is more trouble than
    # the in-Python filter is worth at the scale a webhook list reaches.
    return [hook for hook in result.scalars().all() if event.value in hook.events]


async def emit(
    session: AsyncSession,
    event: WebhookEvent,
    data: dict[str, Any],
    *,
    organization_id: uuid.UUID | None,
    project_id: uuid.UUID | None = None,
    actor_id: uuid.UUID | None = None,
) -> bool:
    """Queue ``event`` for every webhook subscribed to it. Returns whether anything was queued.

    Call it once the change is committed: the delivery runs as a background job, in a
    session of its own (under Dramatiq, in another process), and must find what it reports.
    It commits, as queueing does. When no active webhook wants the event nothing is queued,
    so a workspace without webhooks pays one query per change rather than one job.

    ``data`` is the resource as the API returns it, under its kind (``{"task": {...}}``).
    """
    hooks = await matching_webhooks(
        session, event, organization_id=organization_id, project_id=project_id
    )
    if not hooks:
        return False
    from curvevision.services import background  # imports the job queue, which imports this

    await background.enqueue(
        session,
        kind="webhook.dispatch",
        payload={
            "event": event.value,
            "data": data,
            "organization_id": str(organization_id) if organization_id else None,
            "project_id": str(project_id) if project_id else None,
        },
        created_by_id=actor_id,
    )
    return True


async def emit_for_project(
    session: AsyncSession,
    event: WebhookEvent,
    data: dict[str, Any],
    project: Any,
    *,
    actor_id: uuid.UUID | None = None,
) -> bool:
    """`emit` for a change inside ``project``: its webhooks and its workspace's hear it."""
    return await emit(
        session,
        event,
        data,
        organization_id=project.organization_id,
        project_id=project.id,
        actor_id=actor_id,
    )


#: The event a ping sends. Not a `WebhookEvent`: nothing subscribes to it, and every
#: webhook can be pinged.
PING = "ping"


async def ping(
    session: AsyncSession, hook: Webhook, *, client: httpx.AsyncClient | None = None
) -> WebhookDelivery:
    """Send one signed test delivery now, whatever the webhook subscribes to, and record it.

    Attempted once and never retried: whoever pressed the button is waiting for the answer.
    """
    delivery = WebhookDelivery(
        webhook_id=hook.id,
        event=PING,
        payload={"webhook": {"id": str(hook.id), "target_url": hook.target_url}},
    )
    session.add(delivery)
    await session.flush()
    body, headers = _delivery_body(hook, delivery)
    return await attempt_delivery(hook, delivery, body, headers, client=client)


async def recent_deliveries(
    session: AsyncSession, webhook_id: uuid.UUID, *, limit: int = 20
) -> list[WebhookDelivery]:
    result = await session.execute(
        select(WebhookDelivery)
        .where(WebhookDelivery.webhook_id == webhook_id)
        .order_by(WebhookDelivery.created_at.desc(), WebhookDelivery.id.desc())
        .limit(limit)
    )
    return list(result.scalars().all())


def retrying(delivery: WebhookDelivery) -> bool:
    """Whether a delivery that has not succeeded will be attempted again."""
    if delivery.delivered_at is not None or delivery.event == PING:
        return False
    return not exhausted(delivery)


async def dispatch(
    session: AsyncSession,
    event: WebhookEvent,
    payload: dict[str, Any],
    *,
    organization_id: uuid.UUID | None = None,
    project_id: uuid.UUID | None = None,
    client: httpx.AsyncClient | None = None,
) -> list[WebhookDelivery]:
    """Deliver ``event`` to every matching webhook, recording each attempt.

    A failed delivery is recorded, not raised: a broken receiver must never fail the user
    action that triggered it. Retries are the background queue's job.
    """
    hooks = await matching_webhooks(
        session, event, organization_id=organization_id, project_id=project_id
    )
    deliveries: list[WebhookDelivery] = []

    for hook in hooks:
        delivery = WebhookDelivery(webhook_id=hook.id, event=event.value, payload=payload)
        session.add(delivery)
        await session.flush()

        body, headers = _delivery_body(hook, delivery)
        await attempt_delivery(hook, delivery, body, headers, client=client)
        deliveries.append(delivery)

    return deliveries


def _delivery_body(hook: Webhook, delivery: WebhookDelivery) -> tuple[bytes, dict[str, str]]:
    """The exact bytes and headers for a delivery, signed.

    Rebuilt from the stored payload rather than carried between attempts, so a retry sends
    byte-for-byte what the first attempt sent -- including the same `delivery_id`, which is
    what lets a receiver deduplicate a retry it has already processed.
    """
    body = json.dumps(
        {"event": delivery.event, "delivery_id": str(delivery.id), "data": delivery.payload},
        default=str,
    ).encode("utf-8")
    headers = {
        "content-type": "application/json",
        "user-agent": "CurveVision-Webhook/1.0",
        "x-curvevision-event": delivery.event,
        "x-curvevision-delivery": str(delivery.id),
        "x-curvevision-signature": sign_webhook_payload(hook.secret, body),
    }
    return body, headers


async def attempt_delivery(
    hook: Webhook,
    delivery: WebhookDelivery,
    body: bytes,
    headers: dict[str, str],
    *,
    client: httpx.AsyncClient | None = None,
) -> WebhookDelivery:
    """POST once and record what happened. Never raises for a bad receiver.

    `error` is cleared on the way in: a delivery that failed, was retried and then succeeded
    must not keep the first attempt's error text, which would read as a delivery that both
    succeeded and failed.
    """
    delivery.error = None
    try:
        if client is not None:
            response = await client.post(hook.target_url, content=body, headers=headers)
        else:
            async with httpx.AsyncClient(timeout=DELIVERY_TIMEOUT, transport=transport) as owned:
                response = await owned.post(hook.target_url, content=body, headers=headers)
        delivery.status_code = response.status_code
        if response.status_code >= 400:
            delivery.error = response.text[:1000]
        else:
            # Only a success stamps this: it is what "did the receiver get it" means, and a
            # 500 that set it would make a failed delivery indistinguishable from a good one.
            delivery.delivered_at = utcnow()
    except httpx.HTTPError as exc:
        # No status code at all -- nothing reached a receiver.
        delivery.status_code = None
        delivery.error = str(exc)[:1000]
        logger.warning(
            "webhook delivery failed",
            extra={
                "webhook_id": str(hook.id),
                "event": delivery.event,
                "attempt": delivery.attempts + 1,
                "error": str(exc),
            },
        )
    finally:
        delivery.attempts += 1
    return delivery


async def redeliver(
    session: AsyncSession,
    delivery_id: uuid.UUID,
    *,
    client: httpx.AsyncClient | None = None,
) -> WebhookDelivery | None:
    """Re-attempt one delivery. Returns it, or None when there is nothing to re-attempt."""
    delivery = await session.get(WebhookDelivery, delivery_id)
    if delivery is None:
        return None
    if delivery.delivered_at is not None:
        # Already succeeded -- a duplicate retry job, or one queued before the success
        # landed. Sending again would deliver the event twice.
        return delivery
    hook = await session.get(Webhook, delivery.webhook_id)
    if hook is None or not hook.is_active:
        return delivery

    body, headers = _delivery_body(hook, delivery)
    return await attempt_delivery(hook, delivery, body, headers, client=client)
