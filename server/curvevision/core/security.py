"""Password hashing, JWT issuance/verification, and API-token minting.

Argon2id (the PHC winner and current OWASP recommendation) is used directly via
``argon2-cffi``; ``passlib`` is deliberately not a dependency because it has been
effectively unmaintained since 2020 and auth is a poor place for stale code.
"""

from __future__ import annotations

import hashlib
import hmac
import secrets
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any, Literal

import jwt
from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerificationError, VerifyMismatchError

from curvevision.core.config import Settings
from curvevision.core.errors import AuthenticationError, ValidationError

_hasher = PasswordHasher()

TokenKind = Literal["access", "refresh"]
API_TOKEN_PREFIX = "cv"


# --------------------------------------------------------------------------- passwords


def hash_password(password: str) -> str:
    return _hasher.hash(password)


def verify_password(password: str, hashed: str) -> bool:
    try:
        return _hasher.verify(hashed, password)
    except (VerifyMismatchError, VerificationError, InvalidHashError):
        return False


def needs_rehash(hashed: str) -> bool:
    try:
        return _hasher.check_needs_rehash(hashed)
    except InvalidHashError:
        return True


def validate_password_strength(password: str, minimum_length: int) -> None:
    if len(password) < minimum_length:
        raise ValidationError(f"Password must be at least {minimum_length} characters long")
    if password.lower() in _COMMON_PASSWORDS:
        raise ValidationError("Password is too common")


_COMMON_PASSWORDS = frozenset(
    {
        "password",
        "password1",
        "password123",
        "12345678",
        "123456789",
        "1234567890",
        "qwertyuiop",
        "letmein123",
        "iloveyou1",
        "admin12345",
        "welcome123",
        "curvevision",
        "changeme123",
    }
)


# ------------------------------------------------------------------------------- JWT


@dataclass(frozen=True, slots=True)
class TokenClaims:
    subject: str
    kind: TokenKind
    jti: str
    expires_at: datetime
    extra: dict[str, Any]


def create_token(
    settings: Settings,
    subject: str,
    kind: TokenKind,
    *,
    ttl_seconds: int | None = None,
    **extra: Any,
) -> tuple[str, TokenClaims]:
    now = datetime.now(UTC)
    if ttl_seconds is None:
        ttl_seconds = (
            settings.access_token_ttl_seconds
            if kind == "access"
            else settings.refresh_token_ttl_seconds
        )
    expires_at = now + timedelta(seconds=ttl_seconds)
    jti = secrets.token_urlsafe(16)
    payload: dict[str, Any] = {
        "sub": subject,
        "kind": kind,
        "jti": jti,
        "iat": int(now.timestamp()),
        "exp": int(expires_at.timestamp()),
        "iss": "curvevision",
        **extra,
    }
    encoded = jwt.encode(payload, settings.secret_key, algorithm="HS256")
    return encoded, TokenClaims(subject, kind, jti, expires_at, extra)


def decode_token(settings: Settings, token: str, expected_kind: TokenKind) -> TokenClaims:
    try:
        payload = jwt.decode(token, settings.secret_key, algorithms=["HS256"], issuer="curvevision")
    except jwt.ExpiredSignatureError as exc:
        raise AuthenticationError("Token has expired") from exc
    except jwt.InvalidTokenError as exc:
        raise AuthenticationError("Invalid token") from exc

    if payload.get("kind") != expected_kind:
        raise AuthenticationError(f"Expected a {expected_kind} token")
    reserved = {"sub", "kind", "jti", "iat", "exp", "iss"}
    return TokenClaims(
        subject=str(payload["sub"]),
        kind=expected_kind,
        jti=str(payload.get("jti", "")),
        expires_at=datetime.fromtimestamp(payload["exp"], UTC),
        extra={k: v for k, v in payload.items() if k not in reserved},
    )


# ------------------------------------------------------------------------- API tokens


@dataclass(frozen=True, slots=True)
class MintedApiToken:
    """A freshly minted API token. ``plaintext`` is shown to the user exactly once."""

    public_id: str
    plaintext: str
    secret_hash: str


def mint_api_token() -> MintedApiToken:
    public_id = secrets.token_hex(8)
    secret = secrets.token_urlsafe(32)
    plaintext = f"{API_TOKEN_PREFIX}_{public_id}_{secret}"
    return MintedApiToken(public_id, plaintext, hash_api_token_secret(secret))


def hash_api_token_secret(secret: str) -> str:
    return hashlib.sha256(secret.encode("utf-8")).hexdigest()


def parse_api_token(token: str) -> tuple[str, str]:
    """Split ``cv_<public_id>_<secret>``; raises ``AuthenticationError`` if malformed."""
    parts = token.split("_", 2)
    if len(parts) != 3 or parts[0] != API_TOKEN_PREFIX or not parts[1] or not parts[2]:
        raise AuthenticationError("Malformed API token")
    return parts[1], parts[2]


def verify_api_token_secret(secret: str, expected_hash: str) -> bool:
    return hmac.compare_digest(hash_api_token_secret(secret), expected_hash)


# ---------------------------------------------------------------------------- misc


def hash_opaque(value: str) -> str:
    """Hash a bearer-style opaque value (refresh tokens, webhook replay ids)."""
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def sign_webhook_payload(secret: str, payload: bytes) -> str:
    return "sha256=" + hmac.new(secret.encode("utf-8"), payload, hashlib.sha256).hexdigest()
