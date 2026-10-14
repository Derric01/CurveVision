"""Authentication endpoints."""

from __future__ import annotations

import uuid

from fastapi import APIRouter, Form, Request, status
from fastapi.responses import RedirectResponse, Response

from curvevision.api.deps import IdentityDep, SessionDep, SettingsDep, client_ip
from curvevision.core.errors import PermissionDeniedError
from curvevision.domain.system import AuditEvent
from curvevision.schemas.identity import (
    ApiTokenCreate,
    ApiTokenCreated,
    ApiTokenOut,
    LoginRequest,
    PasswordChangeRequest,
    RefreshRequest,
    RegisterRequest,
    SamlCompleteRequest,
    SamlStarted,
    SamlStartRequest,
    SignInOptions,
    SsoCompleteRequest,
    SsoStarted,
    SsoStartRequest,
    TokenPair,
    UserOut,
    UserUpdate,
)
from curvevision.services import auth as auth_service
from curvevision.services import oidc, saml

router = APIRouter(tags=["auth"])


@router.post("/auth/register", response_model=UserOut, status_code=status.HTTP_201_CREATED)
async def register(
    payload: RegisterRequest,
    session: SessionDep,
    settings: SettingsDep,
    request: Request,
) -> UserOut:
    if not settings.allow_registration or (settings.oidc_only and oidc.enabled(settings)):
        raise PermissionDeniedError("Registration is disabled on this instance")
    user = await auth_service.register_user(
        session,
        settings,
        email=payload.email,
        username=payload.username,
        password=payload.password,
        full_name=payload.full_name,
    )
    session.add(
        AuditEvent(
            actor_id=user.id,
            action="user.registered",
            resource_type="user",
            resource_id=user.id,
            ip_address=client_ip(request),
        )
    )
    await session.commit()
    return UserOut.model_validate(user)


@router.post("/auth/login", response_model=TokenPair)
async def login(
    payload: LoginRequest,
    session: SessionDep,
    settings: SettingsDep,
    request: Request,
) -> TokenPair:
    if settings.oidc_only and oidc.enabled(settings):
        raise PermissionDeniedError(
            f"Sign in with {settings.oidc_display_name}: passwords are not used here"
        )
    user = await auth_service.authenticate(session, payload.identifier, payload.password)
    access, refresh, ttl = await auth_service.issue_token_pair(
        session,
        settings,
        user,
        user_agent=request.headers.get("user-agent"),
        ip_address=client_ip(request),
    )
    session.add(
        AuditEvent(
            actor_id=user.id,
            action="user.login",
            resource_type="user",
            resource_id=user.id,
            ip_address=client_ip(request),
            user_agent=(request.headers.get("user-agent") or "")[:300] or None,
        )
    )
    await session.commit()
    return TokenPair(access_token=access, refresh_token=refresh, expires_in=ttl)


@router.get("/auth/options", response_model=SignInOptions)
async def sign_in_options(settings: SettingsDep) -> SignInOptions:
    """What the sign-in page should offer. Needs no authentication."""
    sso = oidc.enabled(settings)
    only_sso = sso and settings.oidc_only
    with_saml = saml.enabled(settings)
    return SignInOptions(
        password=not only_sso,
        registration=settings.allow_registration and not only_sso,
        sso=sso,
        sso_name=settings.oidc_display_name if sso else None,
        saml=with_saml,
        saml_name=settings.saml_display_name if with_saml else None,
    )


@router.post("/auth/sso/start", response_model=SsoStarted)
async def sso_start(payload: SsoStartRequest, settings: SettingsDep) -> SsoStarted:
    """Begin a sign-in at the OpenID Connect provider (`services/oidc.py`)."""
    started = await oidc.start(settings, return_to=payload.return_to)
    return SsoStarted(authorization_url=started.authorization_url, state=started.state)


@router.post("/auth/sso/complete", response_model=TokenPair)
async def sso_complete(
    payload: SsoCompleteRequest, session: SessionDep, settings: SettingsDep, request: Request
) -> TokenPair:
    """Finish it with the provider's `code` and the `state` it was started with."""
    user, return_to = await oidc.complete(session, settings, code=payload.code, state=payload.state)
    access, refresh, ttl = await auth_service.issue_token_pair(
        session,
        settings,
        user,
        user_agent=request.headers.get("user-agent"),
        ip_address=client_ip(request),
    )
    session.add(
        AuditEvent(
            actor_id=user.id,
            action="user.login.sso",
            resource_type="user",
            resource_id=user.id,
            ip_address=client_ip(request),
            user_agent=(request.headers.get("user-agent") or "")[:300] or None,
        )
    )
    await session.commit()
    return TokenPair(
        access_token=access, refresh_token=refresh, expires_in=ttl, return_to=return_to
    )


@router.get("/auth/saml/metadata", response_class=Response)
async def saml_metadata(settings: SettingsDep) -> Response:
    """This service provider's SAML metadata, for the identity provider's administrator."""
    return Response(saml.metadata(settings), media_type="application/samlmetadata+xml")


@router.post("/auth/saml/start", response_model=SamlStarted)
async def saml_start(payload: SamlStartRequest, settings: SettingsDep) -> SamlStarted:
    """Begin a sign-in at the SAML identity provider (`services/saml.py`)."""
    started = saml.start(
        settings, verifier_sha256=payload.verifier_sha256, return_to=payload.return_to
    )
    return SamlStarted(url=started.url)


@router.post("/auth/saml/acs", include_in_schema=False)
async def saml_acs(
    settings: SettingsDep,
    saml_response: str = Form(alias="SAMLResponse", max_length=2_000_000),
    relay_state: str = Form(default="", alias="RelayState", max_length=1024),
) -> RedirectResponse:
    """The assertion consumer service: the identity provider posts its answer here."""
    answered = saml.acs(settings, saml_response=saml_response, relay_state=relay_state)
    return RedirectResponse(answered.location, status_code=status.HTTP_303_SEE_OTHER)


@router.post("/auth/saml/complete", response_model=TokenPair)
async def saml_complete(
    payload: SamlCompleteRequest, session: SessionDep, settings: SettingsDep, request: Request
) -> TokenPair:
    """Finish it with the login code and the verifier this tab kept."""
    user, return_to = await saml.complete(
        session, settings, code=payload.code, verifier=payload.verifier
    )
    access, refresh, ttl = await auth_service.issue_token_pair(
        session,
        settings,
        user,
        user_agent=request.headers.get("user-agent"),
        ip_address=client_ip(request),
    )
    session.add(
        AuditEvent(
            actor_id=user.id,
            action="user.login.saml",
            resource_type="user",
            resource_id=user.id,
            ip_address=client_ip(request),
            user_agent=(request.headers.get("user-agent") or "")[:300] or None,
        )
    )
    await session.commit()
    return TokenPair(
        access_token=access, refresh_token=refresh, expires_in=ttl, return_to=return_to
    )


@router.post("/auth/refresh", response_model=TokenPair)
async def refresh_tokens(
    payload: RefreshRequest, session: SessionDep, settings: SettingsDep
) -> TokenPair:
    _, access, refresh, ttl = await auth_service.rotate_refresh_token(
        session, settings, payload.refresh_token
    )
    await session.commit()
    return TokenPair(access_token=access, refresh_token=refresh, expires_in=ttl)


@router.post("/auth/logout", status_code=status.HTTP_204_NO_CONTENT)
async def logout(payload: RefreshRequest, session: SessionDep) -> None:
    await auth_service.revoke_refresh_token(session, payload.refresh_token)
    await session.commit()


@router.get("/auth/me", response_model=UserOut)
async def read_me(identity: IdentityDep) -> UserOut:
    return UserOut.model_validate(identity.user)


@router.patch("/auth/me", response_model=UserOut)
async def update_me(payload: UserUpdate, identity: IdentityDep, session: SessionDep) -> UserOut:
    if payload.full_name is not None:
        identity.user.full_name = payload.full_name
    if payload.email is not None:
        identity.user.email = payload.email.lower()
    await session.commit()
    return UserOut.model_validate(identity.user)


@router.post("/auth/password", status_code=status.HTTP_204_NO_CONTENT)
async def change_password(
    payload: PasswordChangeRequest,
    identity: IdentityDep,
    session: SessionDep,
    settings: SettingsDep,
) -> None:
    await auth_service.change_password(
        session, settings, identity.user, payload.current_password, payload.new_password
    )
    session.add(
        AuditEvent(
            actor_id=identity.user.id,
            action="user.password_changed",
            resource_type="user",
            resource_id=identity.user.id,
        )
    )
    await session.commit()


@router.get("/auth/tokens", response_model=list[ApiTokenOut])
async def list_api_tokens(identity: IdentityDep, session: SessionDep) -> list[ApiTokenOut]:
    await session.refresh(identity.user, ["api_tokens"])
    return [ApiTokenOut.model_validate(token) for token in identity.user.api_tokens]


@router.post("/auth/tokens", response_model=ApiTokenCreated, status_code=status.HTTP_201_CREATED)
async def create_api_token(
    payload: ApiTokenCreate, identity: IdentityDep, session: SessionDep
) -> ApiTokenCreated:
    token, minted = await auth_service.create_api_token(
        session, identity.user, name=payload.name, expires_at=payload.expires_at
    )
    session.add(
        AuditEvent(
            actor_id=identity.user.id,
            action="api_token.created",
            resource_type="api_token",
            resource_id=token.id,
        )
    )
    await session.commit()
    return ApiTokenCreated(**ApiTokenOut.model_validate(token).model_dump(), token=minted.plaintext)


@router.delete("/auth/tokens/{token_id}", status_code=status.HTTP_204_NO_CONTENT)
async def revoke_api_token(token_id: uuid.UUID, identity: IdentityDep, session: SessionDep) -> None:
    await auth_service.revoke_api_token(session, identity.user, token_id)
    session.add(
        AuditEvent(
            actor_id=identity.user.id,
            action="api_token.revoked",
            resource_type="api_token",
            resource_id=token_id,
        )
    )
    await session.commit()


@router.post("/auth/sessions/revoke", status_code=status.HTTP_204_NO_CONTENT)
async def revoke_all_sessions(identity: IdentityDep, session: SessionDep) -> None:
    await auth_service.revoke_all_sessions(session, identity.user.id)
    await session.commit()
