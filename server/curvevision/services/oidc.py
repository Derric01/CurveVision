"""Signing in through an OpenID Connect provider.

The authorization-code flow with PKCE, the server as a confidential client:

1. `start` builds the provider's authorization URL. Everything the callback will need —
   the nonce, the PKCE verifier, where to go afterwards — travels in `state`, signed and
   short-lived, so the server keeps no login state of its own and any replica can finish
   what another began.
2. The provider sends the browser back to the web application's `/auth/callback`, which
   checks that `state` is the one *this browser* started with (kept in its session storage:
   a login another site started cannot be finished here — login CSRF) and posts `code` and
   `state` to `complete`.
3. `complete` verifies `state`, exchanges the code (with the verifier and the client secret),
   and validates the ID token — signature against the provider's published keys, issuer,
   audience, expiry, nonce — then signs the person in.

**Who they are here.** An account is linked by the provider's issuer and `sub`
(`ExternalIdentity`), never by email alone: a first sign-in links to the account with the
same email only when the provider says that email is verified, and otherwise creates an
account (if `oidc_create_users`), or refuses. `oidc_allowed_domains` limits who may sign in
at all; `oidc_default_workspace` gives a new account a workspace to land in.

Until iteration 81 the documentation spoke of an `AuthBackend` seam for this; it did not
exist. No cookie is involved at any step, which keeps the API's no-cookies property
(`docs/SECURITY.md`): the tokens issued are the same bearer pair a password sign-in gets.
"""

from __future__ import annotations

import base64
import hashlib
import re
import secrets
import time
from dataclasses import dataclass
from typing import Any
from urllib.parse import urlencode

import httpx
import jwt
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from curvevision.core.config import Settings
from curvevision.core.db import utcnow
from curvevision.core.errors import AuthenticationError, CurveVisionError, NotFoundError
from curvevision.core.logging import get_logger
from curvevision.domain.enums import Role
from curvevision.domain.identity import (
    ExternalIdentity,
    Organization,
    OrganizationMembership,
    User,
)

logger = get_logger(__name__)

#: How long a sign-in may take at the provider before its `state` stops being accepted.
STATE_TTL_SECONDS = 600
#: Provider configuration and keys are fetched once and kept this long.
CACHE_SECONDS = 3600
_STATE_AUDIENCE = "curvevision:oidc-state"
_ALGORITHMS = ["RS256", "RS384", "RS512", "ES256", "ES384", "PS256"]


class ProviderUnavailableError(CurveVisionError):
    status_code = 502
    error_type = "oidc_provider_unavailable"
    title = "The sign-in provider could not be reached"


@dataclass(frozen=True, slots=True)
class Started:
    authorization_url: str
    state: str


@dataclass(slots=True)
class _Cached:
    value: dict[str, Any]
    fetched_at: float


_discovery: dict[str, _Cached] = {}
_keys: dict[str, _Cached] = {}


def client() -> httpx.AsyncClient:
    """Replaced in tests by one that talks to a provider in the same process."""
    return httpx.AsyncClient(timeout=15.0)


def enabled(settings: Settings) -> bool:
    return bool(
        settings.oidc_enabled
        and not settings.local_mode
        and settings.oidc_issuer
        and settings.oidc_client_id
        and settings.oidc_redirect_uri
    )


def _require(settings: Settings) -> None:
    if not enabled(settings):
        raise NotFoundError("Single sign-on is not configured on this server")


async def _get_json(url: str) -> dict[str, Any]:
    try:
        async with client() as http:
            response = await http.get(url)
            response.raise_for_status()
            body = response.json()
    except (httpx.HTTPError, ValueError) as exc:
        raise ProviderUnavailableError(f"Could not read {url}: {exc}") from exc
    if not isinstance(body, dict):
        raise ProviderUnavailableError(f"{url} did not answer with a JSON object")
    return body


async def discovery(settings: Settings) -> dict[str, Any]:
    issuer = settings.oidc_issuer.rstrip("/")
    cached = _discovery.get(issuer)
    if cached and time.monotonic() - cached.fetched_at < CACHE_SECONDS:
        return cached.value
    document = await _get_json(f"{issuer}/.well-known/openid-configuration")
    for key in ("authorization_endpoint", "token_endpoint", "jwks_uri", "issuer"):
        if not document.get(key):
            raise ProviderUnavailableError(f"The provider's configuration has no {key}")
    _discovery[issuer] = _Cached(document, time.monotonic())
    return document


async def _signing_key(jwks_uri: str, kid: str | None) -> Any:
    """The provider's key for `kid`, refetching the key set once if it is not known —
    which is what a provider rotating its keys looks like from here."""
    for attempt in range(2):
        cached = _keys.get(jwks_uri)
        if attempt or not cached or time.monotonic() - cached.fetched_at >= CACHE_SECONDS:
            cached = _Cached(await _get_json(jwks_uri), time.monotonic())
            _keys[jwks_uri] = cached
        for key in cached.value.get("keys", []):
            if kid is None or key.get("kid") == kid:
                return jwt.PyJWK(key).key
    raise AuthenticationError(
        "The sign-in provider's token is signed with a key it does not publish"
    )


def _pkce_challenge(verifier: str) -> str:
    digest = hashlib.sha256(verifier.encode("ascii")).digest()
    return base64.urlsafe_b64encode(digest).rstrip(b"=").decode("ascii")


async def start(settings: Settings, *, return_to: str = "/") -> Started:
    _require(settings)
    config = await discovery(settings)
    nonce = secrets.token_urlsafe(24)
    verifier = secrets.token_urlsafe(48)
    # Only a path on this site: an open redirect after sign-in is a phishing aid.
    target = return_to if return_to.startswith("/") and not return_to.startswith("//") else "/"
    state = jwt.encode(
        {
            "aud": _STATE_AUDIENCE,
            "exp": int(time.time()) + STATE_TTL_SECONDS,
            "nonce": nonce,
            "verifier": verifier,
            "return_to": target,
            "jti": secrets.token_urlsafe(12),
        },
        settings.secret_key,
        algorithm="HS256",
    )
    query = urlencode(
        {
            "response_type": "code",
            "client_id": settings.oidc_client_id,
            "redirect_uri": settings.oidc_redirect_uri,
            "scope": settings.oidc_scopes,
            "state": state,
            "nonce": nonce,
            "code_challenge": _pkce_challenge(verifier),
            "code_challenge_method": "S256",
        }
    )
    separator = "&" if "?" in config["authorization_endpoint"] else "?"
    return Started(f"{config['authorization_endpoint']}{separator}{query}", state)


def _read_state(settings: Settings, state: str) -> dict[str, Any]:
    try:
        claims: dict[str, Any] = jwt.decode(
            state, settings.secret_key, algorithms=["HS256"], audience=_STATE_AUDIENCE
        )
    except jwt.ExpiredSignatureError as exc:
        raise AuthenticationError("This sign-in took too long. Start it again.") from exc
    except jwt.PyJWTError as exc:
        raise AuthenticationError("This sign-in was not started here") from exc
    return claims


async def _exchange(settings: Settings, config: dict[str, Any], code: str, verifier: str) -> str:
    try:
        async with client() as http:
            response = await http.post(
                config["token_endpoint"],
                data={
                    "grant_type": "authorization_code",
                    "code": code,
                    "redirect_uri": settings.oidc_redirect_uri,
                    "code_verifier": verifier,
                },
                # client_secret_basic: what every provider supports.
                auth=(settings.oidc_client_id, settings.oidc_client_secret),
            )
    except httpx.HTTPError as exc:
        raise ProviderUnavailableError(f"Could not reach the token endpoint: {exc}") from exc
    if response.status_code >= 400:
        raise AuthenticationError(
            "The sign-in provider refused the sign-in: "
            + str(_safe_json(response).get("error_description") or response.status_code)
        )
    token = _safe_json(response).get("id_token")
    if not isinstance(token, str):
        raise AuthenticationError("The sign-in provider returned no ID token")
    return token


def _safe_json(response: httpx.Response) -> dict[str, Any]:
    try:
        body = response.json()
    except ValueError:
        return {}
    return body if isinstance(body, dict) else {}


async def _verified_claims(
    settings: Settings, config: dict[str, Any], id_token: str, nonce: str
) -> dict[str, Any]:
    try:
        header = jwt.get_unverified_header(id_token)
    except jwt.PyJWTError as exc:
        raise AuthenticationError("The sign-in provider's ID token is malformed") from exc
    key = await _signing_key(config["jwks_uri"], header.get("kid"))
    try:
        claims: dict[str, Any] = jwt.decode(
            id_token,
            key,
            algorithms=_ALGORITHMS,
            audience=settings.oidc_client_id,
            issuer=config["issuer"],
            leeway=60,
            options={"require": ["exp", "iat", "sub", "iss", "aud"]},
        )
    except jwt.PyJWTError as exc:
        raise AuthenticationError(f"The sign-in provider's ID token is not valid: {exc}") from exc
    if claims.get("nonce") != nonce:
        raise AuthenticationError("The ID token was not issued for this sign-in")
    return claims


def _username_base(claims: dict[str, Any]) -> str:
    raw = str(claims.get("preferred_username") or str(claims.get("email", "")).split("@")[0])
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


async def _person(
    session: AsyncSession, settings: Settings, issuer: str, claims: dict[str, Any]
) -> User:
    subject = str(claims["sub"])
    email = str(claims.get("email") or "").strip().lower()
    verified = claims.get("email_verified") is True

    if settings.oidc_allowed_domains:
        domain = email.rsplit("@", 1)[-1] if "@" in email else ""
        if not verified or domain not in {d.lower() for d in settings.oidc_allowed_domains}:
            raise AuthenticationError("Your account at the sign-in provider may not sign in here")

    link = (
        await session.execute(
            select(ExternalIdentity).where(
                ExternalIdentity.issuer == issuer, ExternalIdentity.subject == subject
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
        if not settings.oidc_create_users:
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
            username=await _free_username(session, _username_base(claims)),
            full_name=str(claims.get("name") or "") or None,
            # Unusable: the person signs in through the provider. A password can be set
            # later by an administrator, or not at all.
            password_hash="!oidc:" + secrets.token_hex(16),
        )
        session.add(user)
        await session.flush()
        await _join_default_workspace(session, settings, user)
        logger.info("account created at first single sign-on", extra={"user_id": str(user.id)})

    session.add(ExternalIdentity(user_id=user.id, issuer=issuer, subject=subject, email=email))
    await session.flush()
    return user


async def _join_default_workspace(session: AsyncSession, settings: Settings, user: User) -> None:
    if not settings.oidc_default_workspace:
        return
    workspace = (
        await session.execute(
            select(Organization).where(Organization.slug == settings.oidc_default_workspace)
        )
    ).scalar_one_or_none()
    if workspace is None:
        logger.warning(
            "oidc_default_workspace names no workspace",
            extra={"slug": settings.oidc_default_workspace},
        )
        return
    session.add(
        OrganizationMembership(organization_id=workspace.id, user_id=user.id, role=Role.ANNOTATOR)
    )


async def complete(
    session: AsyncSession, settings: Settings, *, code: str, state: str
) -> tuple[User, str]:
    """The person a provider's answer signs in, and where they were going."""
    _require(settings)
    claims = _read_state(settings, state)
    config = await discovery(settings)
    id_token = await _exchange(settings, config, code, str(claims["verifier"]))
    identity = await _verified_claims(settings, config, id_token, str(claims["nonce"]))
    user = await _person(session, settings, str(config["issuer"]), identity)
    if not user.is_active:
        raise AuthenticationError("This account is disabled")
    user.last_login_at = utcnow()
    return user, str(claims.get("return_to") or "/")


def reset_caches() -> None:
    _discovery.clear()
    _keys.clear()


__all__ = [
    "ProviderUnavailableError",
    "Started",
    "complete",
    "discovery",
    "enabled",
    "reset_caches",
    "start",
]
