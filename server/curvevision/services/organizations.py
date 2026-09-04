"""Organizations and membership."""

from __future__ import annotations

import uuid

from sqlalchemy import func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from curvevision.core.errors import ConflictError, NotFoundError, ValidationError
from curvevision.domain.enums import Role
from curvevision.domain.identity import Organization, OrganizationMembership, User


async def create_organization(
    session: AsyncSession,
    *,
    slug: str,
    name: str,
    description: str | None,
    creator: User,
) -> Organization:
    existing = await session.execute(select(Organization).where(Organization.slug == slug))
    if existing.scalar_one_or_none() is not None:
        raise ConflictError(f"An organization with the slug {slug!r} already exists")

    organization = Organization(
        slug=slug, name=name, description=description, created_by_id=creator.id
    )
    session.add(organization)
    await session.flush()
    session.add(
        OrganizationMembership(organization_id=organization.id, user_id=creator.id, role=Role.OWNER)
    )
    await session.flush()
    return organization


async def get_organization(session: AsyncSession, organization_id: uuid.UUID) -> Organization:
    organization = await session.get(Organization, organization_id)
    if organization is None:
        raise NotFoundError("Organization not found")
    return organization


async def get_role(
    session: AsyncSession, organization_id: uuid.UUID, user_id: uuid.UUID
) -> Role | None:
    result = await session.execute(
        select(OrganizationMembership.role).where(
            OrganizationMembership.organization_id == organization_id,
            OrganizationMembership.user_id == user_id,
        )
    )
    role = result.scalar_one_or_none()
    return Role(role) if role is not None else None


async def list_user_organizations(
    session: AsyncSession, user_id: uuid.UUID
) -> list[tuple[Organization, Role]]:
    result = await session.execute(
        select(Organization, OrganizationMembership.role)
        .join(
            OrganizationMembership,
            OrganizationMembership.organization_id == Organization.id,
        )
        .where(OrganizationMembership.user_id == user_id)
        .order_by(Organization.name)
    )
    return [(org, Role(role)) for org, role in result.all()]


async def add_member(
    session: AsyncSession,
    organization: Organization,
    *,
    user_id: uuid.UUID | None = None,
    identifier: str | None = None,
    role: Role = Role.ANNOTATOR,
) -> OrganizationMembership:
    if user_id is None and identifier is None:
        raise ValidationError("Provide either user_id or identifier")

    if user_id is not None:
        user = await session.get(User, user_id)
    else:
        assert identifier is not None
        result = await session.execute(
            select(User).where(
                or_(
                    func.lower(User.email) == identifier.lower(),
                    func.lower(User.username) == identifier.lower(),
                )
            )
        )
        user = result.scalars().first()

    if user is None:
        raise NotFoundError("User not found")

    existing = await session.execute(
        select(OrganizationMembership).where(
            OrganizationMembership.organization_id == organization.id,
            OrganizationMembership.user_id == user.id,
        )
    )
    if existing.scalar_one_or_none() is not None:
        raise ConflictError("That user is already a member of this organization")

    membership = OrganizationMembership(organization_id=organization.id, user_id=user.id, role=role)
    session.add(membership)
    await session.flush()
    await session.refresh(membership, ["user"])
    return membership


async def list_members(
    session: AsyncSession, organization_id: uuid.UUID
) -> list[OrganizationMembership]:
    result = await session.execute(
        select(OrganizationMembership)
        .where(OrganizationMembership.organization_id == organization_id)
        .options(selectinload(OrganizationMembership.user))
        .order_by(OrganizationMembership.created_at)
    )
    return list(result.scalars().all())


async def update_member_role(
    session: AsyncSession,
    organization_id: uuid.UUID,
    membership_id: uuid.UUID,
    role: Role,
) -> OrganizationMembership:
    membership = await session.get(OrganizationMembership, membership_id)
    if membership is None or membership.organization_id != organization_id:
        raise NotFoundError("Membership not found")

    if membership.role is Role.OWNER and role is not Role.OWNER:
        await _assert_not_last_owner(session, organization_id, membership.id)

    membership.role = role
    await session.flush()
    await session.refresh(membership, ["user"])
    return membership


async def remove_member(
    session: AsyncSession, organization_id: uuid.UUID, membership_id: uuid.UUID
) -> None:
    membership = await session.get(OrganizationMembership, membership_id)
    if membership is None or membership.organization_id != organization_id:
        raise NotFoundError("Membership not found")
    if membership.role is Role.OWNER:
        await _assert_not_last_owner(session, organization_id, membership.id)
    await session.delete(membership)


async def _assert_not_last_owner(
    session: AsyncSession, organization_id: uuid.UUID, excluding_id: uuid.UUID
) -> None:
    """An organization without an owner is unadministrable, so the last one is protected."""
    result = await session.execute(
        select(func.count())
        .select_from(OrganizationMembership)
        .where(
            OrganizationMembership.organization_id == organization_id,
            OrganizationMembership.role == Role.OWNER,
            OrganizationMembership.id != excluding_id,
        )
    )
    if result.scalar_one() == 0:
        raise ConflictError("This is the organization's only owner. Promote another member first.")
