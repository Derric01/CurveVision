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
        conditions.append(Webhook.project_id == project_id)
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

        body = json.dumps(
            {"event": event.value, "delivery_id": str(delivery.id), "data": payload},
            default=str,
        ).encode("utf-8")
        headers = {
            "content-type": "application/json",
            "user-agent": "CurveVision-Webhook/1.0",
            "x-curvevision-event": event.value,
            "x-curvevision-delivery": str(delivery.id),
            "x-curvevision-signature": sign_webhook_payload(hook.secret, body),
        }

        try:
            if client is not None:
                response = await client.post(hook.target_url, content=body, headers=headers)
            else:
                async with httpx.AsyncClient(timeout=DELIVERY_TIMEOUT) as owned:
                    response = await owned.post(hook.target_url, content=body, headers=headers)
            delivery.status_code = response.status_code
            delivery.delivered_at = utcnow()
            if response.status_code >= 400:
                delivery.error = response.text[:1000]
        except httpx.HTTPError as exc:
            delivery.error = str(exc)[:1000]
            logger.warning(
                "webhook delivery failed",
                extra={"webhook_id": str(hook.id), "event": event.value, "error": str(exc)},
            )
        finally:
            delivery.attempts += 1
            deliveries.append(delivery)

    return deliveries
