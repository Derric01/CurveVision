"""Organizations and membership endpoints."""

from __future__ import annotations

import uuid

from fastapi import APIRouter, status

from curvevision.api.deps import IdentityDep, SessionDep, organization_context
from curvevision.policy import Action, ResourceType, require
from curvevision.schemas.identity import (
    MembershipCreate,
    MembershipOut,
    MembershipUpdate,
    OrganizationCreate,
    OrganizationOut,
    OrganizationUpdate,
    OrganizationWithRole,
)
from curvevision.services import organizations as org_service

router = APIRouter(prefix="/organizations", tags=["organizations"])


@router.get("", response_model=list[OrganizationWithRole])
async def list_organizations(
    identity: IdentityDep, session: SessionDep
) -> list[OrganizationWithRole]:
    pairs = await org_service.list_user_organizations(session, identity.principal.user_id)
    return [
        OrganizationWithRole(**OrganizationOut.model_validate(org).model_dump(), role=role)
        for org, role in pairs
    ]


@router.post("", response_model=OrganizationOut, status_code=status.HTTP_201_CREATED)
async def create_organization(
    payload: OrganizationCreate, identity: IdentityDep, session: SessionDep
) -> OrganizationOut:
    # Any authenticated user may create an organization: they become its owner, which is
    # what makes self-serve onboarding work without an administrator in the loop.
    organization = await org_service.create_organization(
        session,
        slug=payload.slug,
        name=payload.name,
        description=payload.description,
        creator=identity.user,
    )
    await session.commit()
    return OrganizationOut.model_validate(organization)


@router.get("/{organization_id}", response_model=OrganizationOut)
async def read_organization(
    organization_id: uuid.UUID, identity: IdentityDep, session: SessionDep
) -> OrganizationOut:
    context = await organization_context(session, identity, organization_id)
    require(identity.principal, Action.VIEW, context)
    organization = await org_service.get_organization(session, organization_id)
    return OrganizationOut.model_validate(organization)


@router.patch("/{organization_id}", response_model=OrganizationOut)
async def update_organization(
    organization_id: uuid.UUID,
    payload: OrganizationUpdate,
    identity: IdentityDep,
    session: SessionDep,
) -> OrganizationOut:
    context = await organization_context(session, identity, organization_id)
    require(identity.principal, Action.UPDATE, context)
    organization = await org_service.get_organization(session, organization_id)
    if payload.name is not None:
        organization.name = payload.name
    if payload.description is not None:
        organization.description = payload.description
    await session.commit()
    return OrganizationOut.model_validate(organization)


@router.delete("/{organization_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_organization(
    organization_id: uuid.UUID, identity: IdentityDep, session: SessionDep
) -> None:
    context = await organization_context(session, identity, organization_id)
    require(identity.principal, Action.DELETE, context)
    organization = await org_service.get_organization(session, organization_id)
    await session.delete(organization)
    await session.commit()


@router.get("/{organization_id}/members", response_model=list[MembershipOut])
async def list_members(
    organization_id: uuid.UUID, identity: IdentityDep, session: SessionDep
) -> list[MembershipOut]:
    context = await organization_context(
        session, identity, organization_id, ResourceType.MEMBERSHIP
    )
    require(identity.principal, Action.VIEW, context)
    members = await org_service.list_members(session, organization_id)
    return [MembershipOut.model_validate(member) for member in members]


@router.post(
    "/{organization_id}/members",
    response_model=MembershipOut,
    status_code=status.HTTP_201_CREATED,
)
async def add_member(
    organization_id: uuid.UUID,
    payload: MembershipCreate,
    identity: IdentityDep,
    session: SessionDep,
) -> MembershipOut:
    context = await organization_context(
        session, identity, organization_id, ResourceType.MEMBERSHIP
    )
    require(identity.principal, Action.CREATE, context)
    organization = await org_service.get_organization(session, organization_id)
    membership = await org_service.add_member(
        session,
        organization,
        user_id=payload.user_id,
        identifier=payload.identifier,
        role=payload.role,
    )
    await session.commit()
    return MembershipOut.model_validate(membership)


@router.patch("/{organization_id}/members/{membership_id}", response_model=MembershipOut)
async def update_member(
    organization_id: uuid.UUID,
    membership_id: uuid.UUID,
    payload: MembershipUpdate,
    identity: IdentityDep,
    session: SessionDep,
) -> MembershipOut:
    context = await organization_context(
        session, identity, organization_id, ResourceType.MEMBERSHIP
    )
    require(identity.principal, Action.UPDATE, context)
    membership = await org_service.update_member_role(
        session, organization_id, membership_id, payload.role
    )
    await session.commit()
    return MembershipOut.model_validate(membership)


@router.delete("/{organization_id}/members/{membership_id}", status_code=status.HTTP_204_NO_CONTENT)
async def remove_member(
    organization_id: uuid.UUID,
    membership_id: uuid.UUID,
    identity: IdentityDep,
    session: SessionDep,
) -> None:
    context = await organization_context(
        session, identity, organization_id, ResourceType.MEMBERSHIP
    )
    require(identity.principal, Action.DELETE, context)
    await org_service.remove_member(session, organization_id, membership_id)
    await session.commit()
