"""Webhook management."""

from __future__ import annotations

import uuid

from fastapi import APIRouter, Query, status
from sqlalchemy import select

from curvevision.api.deps import IdentityDep, SessionDep, organization_context
from curvevision.core.errors import NotFoundError, ValidationError
from curvevision.domain.project import Project
from curvevision.domain.system import Webhook, WebhookDelivery
from curvevision.policy import Action, ResourceType, require
from curvevision.schemas.system import (
    WebhookCreate,
    WebhookCreated,
    WebhookDeliveryOut,
    WebhookOut,
    WebhookUpdate,
)
from curvevision.services import webhooks as webhook_service

router = APIRouter(prefix="/webhooks", tags=["webhooks"])


@router.get("", response_model=list[WebhookOut])
async def list_webhooks(
    identity: IdentityDep,
    session: SessionDep,
    organization_id: uuid.UUID = Query(...),
) -> list[WebhookOut]:
    context = await organization_context(session, identity, organization_id, ResourceType.WEBHOOK)
    require(identity.principal, Action.VIEW, context)
    result = await session.execute(
        select(Webhook)
        .where(Webhook.organization_id == organization_id)
        .order_by(Webhook.created_at.desc())
    )
    return [WebhookOut.model_validate(row) for row in result.scalars().all()]


@router.post("", response_model=WebhookCreated, status_code=status.HTTP_201_CREATED)
async def create_webhook(
    payload: WebhookCreate,
    identity: IdentityDep,
    session: SessionDep,
    organization_id: uuid.UUID = Query(...),
) -> WebhookCreated:
    """Register a webhook. The signing secret is returned once and never again."""
    context = await organization_context(session, identity, organization_id, ResourceType.WEBHOOK)
    require(identity.principal, Action.CREATE, context)
    if payload.project_id is not None:
        # Until iteration 96 any project id was taken, and a webhook in one workspace heard
        # every event of a project in another. The same answer whether the project exists
        # elsewhere or nowhere, so this does not reveal which.
        project = await session.get(Project, payload.project_id)
        if project is None or project.organization_id != organization_id:
            raise ValidationError("project_id names no project in this workspace")

    webhook = Webhook(
        organization_id=organization_id,
        project_id=payload.project_id,
        target_url=str(payload.target_url),
        secret=webhook_service.generate_secret(),
        events=[event.value for event in payload.events],
        description=payload.description,
        created_by_id=identity.user.id,
    )
    session.add(webhook)
    await session.commit()
    return WebhookCreated(**WebhookOut.model_validate(webhook).model_dump(), secret=webhook.secret)


@router.patch("/{webhook_id}", response_model=WebhookOut)
async def update_webhook(
    webhook_id: uuid.UUID,
    payload: WebhookUpdate,
    identity: IdentityDep,
    session: SessionDep,
) -> WebhookOut:
    webhook, organization_id = await _get_webhook(session, webhook_id)
    context = await organization_context(session, identity, organization_id, ResourceType.WEBHOOK)
    require(identity.principal, Action.UPDATE, context)

    if payload.target_url is not None:
        webhook.target_url = str(payload.target_url)
    if payload.events is not None:
        webhook.events = [event.value for event in payload.events]
    if payload.is_active is not None:
        webhook.is_active = payload.is_active
    if payload.description is not None:
        webhook.description = payload.description
    await session.commit()
    return WebhookOut.model_validate(webhook)


@router.delete("/{webhook_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_webhook(webhook_id: uuid.UUID, identity: IdentityDep, session: SessionDep) -> None:
    webhook, organization_id = await _get_webhook(session, webhook_id)
    context = await organization_context(session, identity, organization_id, ResourceType.WEBHOOK)
    require(identity.principal, Action.DELETE, context)
    await session.delete(webhook)
    await session.commit()


@router.get("/{webhook_id}/deliveries", response_model=list[WebhookDeliveryOut])
async def list_deliveries(
    webhook_id: uuid.UUID,
    identity: IdentityDep,
    session: SessionDep,
    limit: int = Query(20, ge=1, le=100),
) -> list[WebhookDeliveryOut]:
    """The webhook's most recent deliveries, newest first: what its receiver answered."""
    _webhook, organization_id = await _get_webhook(session, webhook_id)
    context = await organization_context(session, identity, organization_id, ResourceType.WEBHOOK)
    require(identity.principal, Action.VIEW, context)
    rows = await webhook_service.recent_deliveries(session, webhook_id, limit=limit)
    return [_delivery_out(row) for row in rows]


@router.post("/{webhook_id}/ping", response_model=WebhookDeliveryOut)
async def ping_webhook(
    webhook_id: uuid.UUID, identity: IdentityDep, session: SessionDep
) -> WebhookDeliveryOut:
    """Send a signed `ping` delivery now and answer with how the receiver took it."""
    webhook, organization_id = await _get_webhook(session, webhook_id)
    context = await organization_context(session, identity, organization_id, ResourceType.WEBHOOK)
    require(identity.principal, Action.UPDATE, context)
    delivery = await webhook_service.ping(session, webhook)
    await session.commit()
    return _delivery_out(delivery)


def _delivery_out(delivery: WebhookDelivery) -> WebhookDeliveryOut:
    out = WebhookDeliveryOut.model_validate(delivery)
    out.retrying = webhook_service.retrying(delivery)
    return out


async def _get_webhook(session: SessionDep, webhook_id: uuid.UUID) -> tuple[Webhook, uuid.UUID]:
    """Load a webhook together with the organization that scopes its permissions."""
    webhook = await session.get(Webhook, webhook_id)
    if webhook is None or webhook.organization_id is None:
        raise NotFoundError("Webhook not found")
    return webhook, webhook.organization_id
