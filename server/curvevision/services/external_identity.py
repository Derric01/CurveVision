"""Who a person signing in through an identity provider is here.

One set of rules for every kind of single sign-on — OpenID Connect (`services/oidc.py`) and
SAML (`services/saml.py`) — so that neither can be the weaker door.

An account is linked by the provider's issuer and its stable identifier for the person
(`ExternalIdentity`), never by email alone: a first sign-in links to the account with the
same email only when the provider vouches for that email, and otherwise creates an account
(when the policy allows) or refuses. ``allowed_domains`` limits who may sign in at all;
``default_workspace`` gives a new account a workspace to land in.
"""

from __future__ import annotations

import re
import secrets
from dataclasses import dataclass

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from curvevision.core.errors import AuthenticationError
from curvevision.core.logging import get_logger
from curvevision.domain.enums import Role
from curvevision.domain.identity import (
    ExternalIdentity,
    Organization,
    OrganizationMembership,
    User,
)

logger = get_logger(__name__)


@dataclass(frozen=True, slots=True)
class Policy:
    """How one kind of single sign-on may create and link accounts."""

    #: Marks the unusable password of an account made at first sign-on: ``"oidc"``.
    kind: str
    create_users: bool
    allowed_domains: tuple[str, ...]
    default_workspace: str


@dataclass(frozen=True, slots=True)
class Claimed:
    """What a provider says about the person signing in."""

    issuer: str
    subject: str
    email: str
    #: Whether the provider vouches for the email: only then may it link an existing
    #: account, or make a new one.
    email_verified: bool
    full_name: str | None = None
    username_hint: str = ""


def _username_base(claimed: Claimed) -> str:
    raw = claimed.username_hint or claimed.email.split("@")[0]
    cleaned = re.sub(r"[^A-Za-z0-9_.-]", "", raw)[:48]
    return cleaned or "user"


async def _free_username(session: AsyncSession, base: str) -> str:
    candidate, suffix = base, 1
    while (
        await session.execute(select(User).where(func.lower(User.username) == candidate.lower()))
    ).scalar_one_or_none() is not None:
        suffix += 1
        candidate = f"{base}{suffix}"
    return candidate


async def person(session: AsyncSession, policy: Policy, claimed: Claimed) -> User:
    """The account ``claimed`` signs in to — found, linked or made — or a refusal."""
    email = claimed.email.strip().lower()
    verified = claimed.email_verified

    if policy.allowed_domains:
        domain = email.rsplit("@", 1)[-1] if "@" in email else ""
        if not verified or domain not in {d.lower() for d in policy.allowed_domains}:
            raise AuthenticationError("Your account at the sign-in provider may not sign in here")

    link = (
        await session.execute(
            select(ExternalIdentity).where(
                ExternalIdentity.issuer == claimed.issuer,
                ExternalIdentity.subject == claimed.subject,
            )
        )
    ).scalar_one_or_none()
    if link is not None:
        user = await session.get(User, link.user_id)
        if user is None:  # pragma: no cover - the foreign key cascades
            raise AuthenticationError("Account is unavailable")
        link.email = email or link.email
        return user

    user = None
    if email and verified:
        user = (
            await session.execute(select(User).where(func.lower(User.email) == email))
        ).scalar_one_or_none()
    if user is None:
        if not policy.create_users:
            raise AuthenticationError(
                "There is no account here for you yet. Ask an administrator to add one."
            )
        if not email or not verified:
            raise AuthenticationError(
                "The sign-in provider did not give a verified email address, which a new "
                "account here needs."
            )
        user = User(
            email=email,
            username=await _free_username(session, _username_base(claimed)),
            full_name=claimed.full_name or None,
            # Unusable: the person signs in through the provider. A password can be set
            # later by an administrator, or not at all.
            password_hash=f"!{policy.kind}:" + secrets.token_hex(16),
        )
        session.add(user)
        await session.flush()
        await _join_default_workspace(session, policy, user)
        logger.info(
            "account created at first single sign-on",
            extra={"user_id": str(user.id), "kind": policy.kind},
        )

    session.add(
        ExternalIdentity(
            user_id=user.id, issuer=claimed.issuer, subject=claimed.subject, email=email
        )
    )
    await session.flush()
    return user


async def _join_default_workspace(session: AsyncSession, policy: Policy, user: User) -> None:
    if not policy.default_workspace:
        return
    workspace = (
        await session.execute(
            select(Organization).where(Organization.slug == policy.default_workspace)
        )
    ).scalar_one_or_none()
    if workspace is None:
        logger.warning(
            "the default workspace for single sign-on names no workspace",
            extra={"slug": policy.default_workspace, "kind": policy.kind},
        )
        return
    session.add(
        OrganizationMembership(organization_id=workspace.id, user_id=user.id, role=Role.ANNOTATOR)
    )


__all__ = ["Claimed", "Policy", "person"]
