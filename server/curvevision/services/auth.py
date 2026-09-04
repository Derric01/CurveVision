"""Authentication: registration, login, refresh rotation, API tokens."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

from sqlalchemy import func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from curvevision.core.config import Settings
from curvevision.core.db import utcnow
from curvevision.core.errors import AuthenticationError, ConflictError, NotFoundError
from curvevision.core.security import (
    MintedApiToken,
    create_token,
    decode_token,
    hash_opaque,
    hash_password,
    mint_api_token,
    needs_rehash,
    parse_api_token,
    validate_password_strength,
    verify_api_token_secret,
    verify_password,
)
from curvevision.domain.identity import ApiToken, RefreshToken, User
from curvevision.policy import Principal


async def register_user(
    session: AsyncSession,
    settings: Settings,
    *,
    email: str,
    username: str,
    password: str,
    full_name: str | None = None,
    is_superuser: bool = False,
) -> User:
    validate_password_strength(password, settings.password_min_length)

    existing = await session.execute(
        select(User).where(
            or_(
                func.lower(User.email) == email.lower(),
                func.lower(User.username) == username.lower(),
            )
        )
    )
    if existing.scalars().first() is not None:
        raise ConflictError("An account with that email address or username already exists")

    # The first account on a fresh instance becomes the administrator; otherwise a
    # self-hoster has no way in without shell access.
    if not is_superuser:
        count = (await session.execute(select(func.count()).select_from(User))).scalar_one()
        is_superuser = count == 0

    user = User(
        email=email.lower(),
        username=username,
        full_name=full_name,
        password_hash=hash_password(password),
        is_superuser=is_superuser,
    )
    session.add(user)
    await session.flush()
    return user


async def authenticate(session: AsyncSession, identifier: str, password: str) -> User:
    result = await session.execute(
        select(User).where(
            or_(
                func.lower(User.email) == identifier.lower(),
                func.lower(User.username) == identifier.lower(),
            )
        )
    )
    user = result.scalars().first()
    if user is None or not verify_password(password, user.password_hash):
        # One message for both cases: a distinct "no such user" is a user enumeration oracle.
        raise AuthenticationError("Incorrect credentials")
    if not user.is_active:
        raise AuthenticationError("This account is disabled")

    if needs_rehash(user.password_hash):
        user.password_hash = hash_password(password)
    user.last_login_at = utcnow()
    return user


async def issue_token_pair(
    session: AsyncSession,
    settings: Settings,
    user: User,
    *,
    user_agent: str | None = None,
    ip_address: str | None = None,
) -> tuple[str, str, int]:
    access, _ = create_token(settings, str(user.id), "access", username=user.username)
    refresh, claims = create_token(settings, str(user.id), "refresh")
    session.add(
        RefreshToken(
            user_id=user.id,
            token_hash=hash_opaque(refresh),
            expires_at=claims.expires_at,
            user_agent=(user_agent or "")[:300] or None,
            ip_address=(ip_address or "")[:64] or None,
        )
    )
    return access, refresh, settings.access_token_ttl_seconds


async def rotate_refresh_token(
    session: AsyncSession, settings: Settings, refresh_token: str
) -> tuple[User, str, str, int]:
    """Exchange a refresh token for a new pair, revoking the old one.

    Rotation means a stolen refresh token is usable at most once, and the legitimate
    holder's next refresh fails visibly rather than silently sharing the session.
    """
    claims = decode_token(settings, refresh_token, "refresh")
    token_hash = hash_opaque(refresh_token)
    result = await session.execute(
        select(RefreshToken).where(RefreshToken.token_hash == token_hash)
    )
    stored = result.scalar_one_or_none()
    if stored is None or stored.revoked_at is not None:
        raise AuthenticationError("Refresh token is no longer valid")
    if stored.expires_at.replace(tzinfo=stored.expires_at.tzinfo or UTC) < datetime.now(UTC):
        raise AuthenticationError("Refresh token has expired")

    user = await session.get(User, uuid.UUID(claims.subject))
    if user is None or not user.is_active:
        raise AuthenticationError("Account is unavailable")

    stored.revoked_at = utcnow()
    access, refresh, ttl = await issue_token_pair(
        session, settings, user, user_agent=stored.user_agent, ip_address=stored.ip_address
    )
    return user, access, refresh, ttl


async def revoke_refresh_token(session: AsyncSession, refresh_token: str) -> None:
    result = await session.execute(
        select(RefreshToken).where(RefreshToken.token_hash == hash_opaque(refresh_token))
    )
    stored = result.scalar_one_or_none()
    if stored is not None and stored.revoked_at is None:
        stored.revoked_at = utcnow()


async def revoke_all_sessions(session: AsyncSession, user_id: uuid.UUID) -> int:
    result = await session.execute(
        select(RefreshToken).where(
            RefreshToken.user_id == user_id, RefreshToken.revoked_at.is_(None)
        )
    )
    tokens = list(result.scalars().all())
    for token in tokens:
        token.revoked_at = utcnow()
    return len(tokens)


async def resolve_access_token(
    session: AsyncSession, settings: Settings, token: str
) -> tuple[User, Principal]:
    claims = decode_token(settings, token, "access")
    user = await session.get(User, uuid.UUID(claims.subject))
    if user is None or not user.is_active:
        raise AuthenticationError("Account is unavailable")
    return user, Principal(user.id, user.username, user.is_superuser)


# ------------------------------------------------------------------------- API tokens


async def create_api_token(
    session: AsyncSession,
    user: User,
    *,
    name: str,
    expires_at: datetime | None = None,
) -> tuple[ApiToken, MintedApiToken]:
    minted = mint_api_token()
    token = ApiToken(
        user_id=user.id,
        name=name,
        public_id=minted.public_id,
        secret_hash=minted.secret_hash,
        expires_at=expires_at,
    )
    session.add(token)
    await session.flush()
    return token, minted


async def resolve_api_token(session: AsyncSession, raw_token: str) -> tuple[User, Principal]:
    public_id, secret = parse_api_token(raw_token)
    result = await session.execute(select(ApiToken).where(ApiToken.public_id == public_id))
    token = result.scalar_one_or_none()
    if token is None or not verify_api_token_secret(secret, token.secret_hash):
        raise AuthenticationError("Invalid API token")
    if token.revoked_at is not None:
        raise AuthenticationError("API token has been revoked")
    if token.expires_at is not None:
        expires = token.expires_at
        if expires.tzinfo is None:
            expires = expires.replace(tzinfo=UTC)
        if expires < datetime.now(UTC):
            raise AuthenticationError("API token has expired")

    user = await session.get(User, token.user_id)
    if user is None or not user.is_active:
        raise AuthenticationError("Account is unavailable")

    token.last_used_at = utcnow()
    return user, Principal(user.id, user.username, user.is_superuser, api_token_id=token.id)


async def revoke_api_token(session: AsyncSession, user: User, token_id: uuid.UUID) -> None:
    token = await session.get(ApiToken, token_id)
    if token is None or token.user_id != user.id:
        raise NotFoundError("API token not found")
    token.revoked_at = utcnow()


async def change_password(
    session: AsyncSession,
    settings: Settings,
    user: User,
    current_password: str,
    new_password: str,
) -> None:
    if not verify_password(current_password, user.password_hash):
        raise AuthenticationError("Current password is incorrect")
    validate_password_strength(new_password, settings.password_min_length)
    user.password_hash = hash_password(new_password)
    # Changing a password ends every other session; that is the point of changing it.
    await revoke_all_sessions(session, user.id)
