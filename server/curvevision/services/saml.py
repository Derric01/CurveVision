"""Signing in through a SAML 2.0 identity provider.

Web-browser SSO, the service provider's side, with OneLogin's ``python3-saml`` doing the
part that must not be written twice — parsing the response safely, verifying its XML
signature against the provider's certificate, and checking its issuer, audience,
destination, recipient and validity window. What this module adds is how a sign-in is tied
to the browser tab that started it, without cookies or any login state on the server:

1. ``start``: the tab makes a random *verifier*, keeps it, and sends its SHA-256. The server
   mints the AuthnRequest's ID from that hash, a timestamp and an HMAC under the server's
   secret — ``_<time>.<mac>`` — and sends the browser to the provider (HTTP-Redirect). The
   RelayState is the path to return to, inside SAML's 80-byte limit.
2. ``acs``: the provider posts its Response to the assertion consumer service. It is
   validated, must answer a request (an unsolicited response is refused) minted here within
   the last ten minutes, and its identity is packed, signed, into a two-minute *login code*
   that the browser is sent back to the web application with.
3. ``complete``: the tab posts the login code with its verifier. Only the tab holding the
   verifier whose hash the request ID was minted from can finish — so a response captured
   and replayed, or a login code that leaks through history or a referrer, signs nobody in
   — and only then is the account found, linked or made (`services/external_identity.py`).

The identity is the response's ``NameID`` under the provider's entity ID; the email comes
from ``saml_email_attribute`` or the attributes providers commonly use, or from a NameID in
email format. A SAML provider is one the operator configured, so the email it asserts is
trusted to link and create accounts unless ``saml_trust_email`` is off.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import time
from dataclasses import dataclass
from typing import Any
from urllib.parse import urlsplit

import jwt

from curvevision.core.config import Settings
from curvevision.core.db import utcnow
from curvevision.core.errors import AuthenticationError, CurveVisionError, NotFoundError
from curvevision.core.logging import get_logger
from curvevision.domain.identity import User
from curvevision.services import external_identity

logger = get_logger(__name__)

#: How long a sign-in may take at the provider.
REQUEST_TTL_SECONDS = 600
#: How long the browser has to bring the login code back.
CODE_TTL_SECONDS = 120
#: SAML's limit on RelayState (bindings, 3.4.3).
RELAY_STATE_LIMIT = 80
#: How long fetched provider metadata is kept.
CACHE_SECONDS = 3600

_CODE_AUDIENCE = "curvevision:saml-login"

#: Where providers commonly put the email address, most specific first.
EMAIL_ATTRIBUTES = (
    "email",
    "mail",
    "emailAddress",
    "urn:oid:0.9.2342.19200300.100.1.3",
    "http://schemas.xmlsoap.org/ws/2005/05/identity/claims/emailaddress",
)
NAME_ATTRIBUTES = (
    "displayName",
    "name",
    "cn",
    "urn:oid:2.16.840.1.113730.3.1.241",
    "http://schemas.xmlsoap.org/ws/2005/05/identity/claims/name",
)
_EMAIL_FORMAT = "urn:oasis:names:tc:SAML:1.1:nameid-format:emailAddress"


class SamlUnavailableError(CurveVisionError):
    status_code = 501
    error_type = "saml_unavailable"
    title = "SAML single sign-on needs the 'saml' extra"


@dataclass(frozen=True, slots=True)
class Started:
    url: str


@dataclass(frozen=True, slots=True)
class Answered:
    """Where the assertion consumer service sends the browser."""

    location: str


def enabled(settings: Settings) -> bool:
    return bool(
        settings.saml_enabled
        and not settings.local_mode
        and settings.saml_acs_url
        and settings.saml_callback_url
        and (
            settings.saml_idp_metadata_url
            or (settings.saml_idp_sso_url and settings.saml_idp_x509_cert)
        )
    )


def _require(settings: Settings) -> None:
    if not enabled(settings):
        raise NotFoundError("SAML single sign-on is not configured here")


# --------------------------------------------------------------------------- request IDs


def _b64(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).decode().rstrip("=")


def verifier_hash(verifier: str) -> str:
    return _b64(hashlib.sha256(verifier.encode()).digest())


def _mac(settings: Settings, issued: int, hashed: str) -> str:
    message = f"curvevision:saml-request|{issued}|{hashed}".encode()
    return _b64(hmac.new(settings.secret_key.encode(), message, hashlib.sha256).digest()[:24])


def mint_request_id(settings: Settings, hashed: str, *, now: float | None = None) -> str:
    """An AuthnRequest ID only this server could make, for the tab holding the verifier.

    An ``xs:ID``: it starts with an underscore and holds only NCName characters.
    """
    issued = int(now if now is not None else time.time())
    return f"_{issued:x}.{_mac(settings, issued, hashed)}"


def _issued(request_id: str) -> int | None:
    if not request_id.startswith("_") or "." not in request_id:
        return None
    try:
        return int(request_id[1:].split(".", 1)[0], 16)
    except ValueError:
        return None


def _fresh(request_id: str, *, now: float | None = None) -> bool:
    issued = _issued(request_id)
    current = now if now is not None else time.time()
    return issued is not None and 0 <= current - issued <= REQUEST_TTL_SECONDS


def belongs_to(settings: Settings, request_id: str, verifier: str) -> bool:
    """Whether ``request_id`` was minted here for the tab holding ``verifier``."""
    issued = _issued(request_id)
    if issued is None:
        return False
    expected = f"_{issued:x}.{_mac(settings, issued, verifier_hash(verifier))}"
    return hmac.compare_digest(expected, request_id)


# --------------------------------------------------------------------------- settings


_metadata: dict[str, tuple[float, dict[str, Any]]] = {}


def _onelogin() -> Any:
    try:
        from onelogin.saml2 import auth, idp_metadata_parser
        from onelogin.saml2 import settings as saml_settings
    except ImportError as exc:  # pragma: no cover - exercised when the extra is absent
        raise SamlUnavailableError(
            "Install it with: pip install 'curvevision-server[saml]'"
        ) from exc
    return auth, idp_metadata_parser, saml_settings


def _idp(settings: Settings) -> dict[str, Any]:
    if not settings.saml_idp_metadata_url:
        return {
            "entityId": settings.saml_idp_entity_id or settings.saml_idp_sso_url,
            "singleSignOnService": {
                "url": settings.saml_idp_sso_url,
                "binding": "urn:oasis:names:tc:SAML:2.0:bindings:HTTP-Redirect",
            },
            "x509cert": settings.saml_idp_x509_cert,
        }
    cached = _metadata.get(settings.saml_idp_metadata_url)
    if cached and time.monotonic() - cached[0] < CACHE_SECONDS:
        return cached[1]
    _, parser, _ = _onelogin()
    try:
        parsed = parser.OneLogin_Saml2_IdPMetadataParser.parse_remote(
            settings.saml_idp_metadata_url, timeout=10
        )
    except Exception as exc:  # the parser raises for network and XML alike
        raise AuthenticationError(
            f"The identity provider's metadata could not be read: {exc}"
        ) from exc
    idp = dict(parsed.get("idp") or {})
    if not idp.get("singleSignOnService") or not (idp.get("x509cert") or idp.get("x509certMulti")):
        raise AuthenticationError(
            "The identity provider's metadata names no sign-in service or key"
        )
    _metadata[settings.saml_idp_metadata_url] = (time.monotonic(), idp)
    return idp


def sp_entity_id(settings: Settings) -> str:
    if settings.saml_sp_entity_id:
        return settings.saml_sp_entity_id
    parts = urlsplit(settings.saml_acs_url)
    return f"{parts.scheme}://{parts.netloc}/api/v1/auth/saml/metadata"


def _saml_settings(settings: Settings) -> dict[str, Any]:
    return {
        "strict": True,
        "debug": False,
        "sp": {
            "entityId": sp_entity_id(settings),
            "assertionConsumerService": {
                "url": settings.saml_acs_url,
                "binding": "urn:oasis:names:tc:SAML:2.0:bindings:HTTP-POST",
            },
            "NameIDFormat": "urn:oasis:names:tc:SAML:1.1:nameid-format:unspecified",
            "x509cert": "",
            "privateKey": "",
        },
        "idp": _idp(settings),
        "security": {
            # The assertion itself must be signed: a signed envelope around an unsigned
            # assertion proves nothing about the assertion.
            "wantAssertionsSigned": True,
            "wantMessagesSigned": False,
            "wantNameId": True,
            "rejectDeprecatedAlgorithm": True,
            "requestedAuthnContext": False,
            "rejectUnsolicitedResponsesWithInResponseTo": False,
        },
    }


def _request_data(settings: Settings, post: dict[str, str] | None = None) -> dict[str, Any]:
    """The request as python3-saml checks it: always the configured ACS URL.

    Built from configuration rather than from the incoming request, so that a proxy in
    front of the server cannot make the Destination check compare against the wrong URL.
    """
    parts = urlsplit(settings.saml_acs_url)
    https = parts.scheme == "https"
    return {
        "https": "on" if https else "off",
        "http_host": parts.hostname or "",
        "server_port": str(parts.port or (443 if https else 80)),
        "script_name": parts.path,
        "get_data": {},
        "post_data": post or {},
    }


def metadata(settings: Settings) -> str:
    """This service provider's metadata, for the identity provider's administrator."""
    _require(settings)
    _, _, saml_settings = _onelogin()
    built = saml_settings.OneLogin_Saml2_Settings(_saml_settings(settings), sp_validation_only=True)
    xml: str = (
        built.get_sp_metadata().decode()
        if isinstance(built.get_sp_metadata(), bytes)
        else built.get_sp_metadata()
    )
    return xml


# --------------------------------------------------------------------------- the flow


def _return_path(return_to: str) -> str:
    """A path on this site, short enough for RelayState; anything else is the home page."""
    if (
        not return_to.startswith("/")
        or return_to.startswith("//")
        or "\\" in return_to
        or len(return_to.encode()) > RELAY_STATE_LIMIT
    ):
        return "/"
    return return_to


def start(settings: Settings, *, verifier_sha256: str, return_to: str = "/") -> Started:
    _require(settings)
    if len(verifier_sha256) != 43:
        raise AuthenticationError("The sign-in was started without a proper verifier")
    auth, _, _ = _onelogin()
    request_id = mint_request_id(settings, verifier_sha256)

    from onelogin.saml2.authn_request import OneLogin_Saml2_Authn_Request

    class _Request(OneLogin_Saml2_Authn_Request):
        def _generate_request_id(self) -> str:
            return request_id

    handler = auth.OneLogin_Saml2_Auth(_request_data(settings), _saml_settings(settings))
    handler.authn_request_class = _Request
    url: str = handler.login(return_to=_return_path(return_to))
    return Started(url=url)


def _first(attributes: dict[str, list[str]], names: tuple[str, ...]) -> str:
    for name in names:
        for value in attributes.get(name) or []:
            if value and value.strip():
                return value.strip()
    return ""


def acs(settings: Settings, *, saml_response: str, relay_state: str) -> Answered:
    """Validate the provider's answer; send the browser back to the web application."""
    _require(settings)
    callback = settings.saml_callback_url

    def refused(message: str) -> Answered:
        from urllib.parse import urlencode

        return Answered(f"{callback}?{urlencode({'saml_error': message})}")

    auth, _, _ = _onelogin()
    handler = auth.OneLogin_Saml2_Auth(
        _request_data(settings, {"SAMLResponse": saml_response, "RelayState": relay_state}),
        _saml_settings(settings),
    )
    try:
        handler.process_response(request_id=None)
    except Exception as exc:  # malformed input is refused, never a server error
        logger.info("saml: a response could not be processed", extra={"error": str(exc)})
        return refused("The identity provider's answer could not be read.")
    errors = handler.get_errors()
    if errors or not handler.is_authenticated():
        reason = handler.get_last_error_reason() or ", ".join(errors) or "not authenticated"
        logger.info("saml: a response was refused", extra={"reason": reason})
        return refused("The identity provider's answer was not valid for this site.")

    in_response_to = handler.get_last_response_in_response_to() or ""
    if not in_response_to:
        return refused("Sign-ins must start here; this one started at the identity provider.")
    if not _fresh(in_response_to):
        return refused("This sign-in took too long. Start it again.")

    attributes: dict[str, list[str]] = handler.get_attributes() or {}
    name_id = str(handler.get_nameid() or "")
    email = _first(
        attributes,
        (settings.saml_email_attribute,) if settings.saml_email_attribute else EMAIL_ATTRIBUTES,
    )
    if not email and handler.get_nameid_format() == _EMAIL_FORMAT:
        email = name_id
    issuer = str(_idp(settings).get("entityId") or "")
    now = int(time.time())
    code = jwt.encode(
        {
            "aud": _CODE_AUDIENCE,
            "iat": now,
            "exp": now + CODE_TTL_SECONDS,
            "rid": in_response_to,
            "iss_idp": issuer,
            "sub": name_id,
            "email": email,
            "name": _first(attributes, NAME_ATTRIBUTES),
            "return_to": _return_path(relay_state or "/"),
        },
        settings.secret_key,
        algorithm="HS256",
    )
    from urllib.parse import urlencode

    return Answered(f"{callback}?{urlencode({'saml': code})}")


def _policy(settings: Settings) -> external_identity.Policy:
    return external_identity.Policy(
        kind="saml",
        create_users=settings.saml_create_users,
        allowed_domains=tuple(settings.saml_allowed_domains),
        default_workspace=settings.saml_default_workspace,
    )


async def complete(
    session: Any, settings: Settings, *, code: str, verifier: str
) -> tuple[User, str]:
    """The person a login code signs in, for the tab holding the verifier."""
    _require(settings)
    try:
        claims: dict[str, Any] = jwt.decode(
            code, settings.secret_key, algorithms=["HS256"], audience=_CODE_AUDIENCE
        )
    except jwt.ExpiredSignatureError as exc:
        raise AuthenticationError("This sign-in took too long. Start it again.") from exc
    except jwt.PyJWTError as exc:
        raise AuthenticationError("This sign-in was not finished here") from exc
    if not belongs_to(settings, str(claims.get("rid") or ""), verifier):
        raise AuthenticationError(
            "This sign-in was not started in this browser tab, so it was not finished here."
        )
    subject = str(claims.get("sub") or "")
    if not subject:
        raise AuthenticationError("The identity provider did not say who you are")
    user = await external_identity.person(
        session,
        _policy(settings),
        external_identity.Claimed(
            issuer=str(claims.get("iss_idp") or ""),
            subject=subject,
            email=str(claims.get("email") or ""),
            email_verified=settings.saml_trust_email and bool(claims.get("email")),
            full_name=str(claims.get("name") or "") or None,
        ),
    )
    if not user.is_active:
        raise AuthenticationError("This account is disabled")
    user.last_login_at = utcnow()
    return user, str(claims.get("return_to") or "/")


def reset_caches() -> None:
    _metadata.clear()


__all__ = [
    "Answered",
    "SamlUnavailableError",
    "Started",
    "acs",
    "belongs_to",
    "complete",
    "enabled",
    "metadata",
    "mint_request_id",
    "reset_caches",
    "start",
    "verifier_hash",
]
