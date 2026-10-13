"""Auth, users, organizations and API tokens."""

from __future__ import annotations

import uuid
from datetime import datetime

from pydantic import EmailStr, Field

from curvevision.domain.enums import Role
from curvevision.schemas.common import ORMModel, Slug, StrictModel


class UserOut(ORMModel):
    id: uuid.UUID
    email: str
    username: str
    full_name: str | None = None
    is_active: bool
    is_superuser: bool
    created_at: datetime
    last_login_at: datetime | None = None


class UserBrief(ORMModel):
    """Embedded user reference. Deliberately omits the email address."""

    id: uuid.UUID
    username: str
    full_name: str | None = None


class RegisterRequest(StrictModel):
    email: EmailStr
    username: str = Field(min_length=3, max_length=64, pattern=r"^[a-zA-Z0-9_.-]+$")
    password: str = Field(min_length=8, max_length=256)
    full_name: str | None = Field(default=None, max_length=200)


class LoginRequest(StrictModel):
    #: Email address or username; humans do not reliably remember which they used.
    identifier: str
    password: str


class SignInOptions(StrictModel):
    """How a person may sign in here, for the sign-in page to offer exactly that."""

    password: bool
    registration: bool
    #: Single sign-on through an OpenID Connect provider, when configured.
    sso: bool
    #: The sign-in button's label: "Sign in with <this>".
    sso_name: str | None = None


class SsoStartRequest(StrictModel):
    #: A path on this site to go to once signed in.
    return_to: str = "/"


class SsoStarted(StrictModel):
    #: Send the browser here.
    authorization_url: str
    #: Keep this, and compare it with the `state` the provider sends back: a sign-in this
    #: browser did not start must not be finished in it.
    state: str


class SsoCompleteRequest(StrictModel):
    code: str = Field(min_length=1, max_length=4096)
    state: str = Field(min_length=1, max_length=8192)


class TokenPair(StrictModel):
    access_token: str
    refresh_token: str
    token_type: str = "bearer"
    expires_in: int
    #: After single sign-on, the path the person was going to.
    return_to: str | None = None


class RefreshRequest(StrictModel):
    refresh_token: str


class PasswordChangeRequest(StrictModel):
    current_password: str
    new_password: str = Field(min_length=8, max_length=256)


class UserUpdate(StrictModel):
    full_name: str | None = Field(default=None, max_length=200)
    email: EmailStr | None = None


class ApiTokenCreate(StrictModel):
    name: str = Field(min_length=1, max_length=120)
    expires_at: datetime | None = None


class ApiTokenOut(ORMModel):
    id: uuid.UUID
    name: str
    public_id: str
    created_at: datetime
    expires_at: datetime | None = None
    last_used_at: datetime | None = None
    revoked_at: datetime | None = None


class ApiTokenCreated(ApiTokenOut):
    #: Shown exactly once. Only a hash of the secret half is stored server-side.
    token: str


class OrganizationCreate(StrictModel):
    slug: Slug = Field(min_length=2, max_length=64)
    name: str = Field(min_length=1, max_length=200)
    description: str | None = Field(default=None, max_length=2000)


class OrganizationUpdate(StrictModel):
    name: str | None = Field(default=None, min_length=1, max_length=200)
    description: str | None = Field(default=None, max_length=2000)


class OrganizationOut(ORMModel):
    id: uuid.UUID
    slug: str
    name: str
    description: str | None = None
    created_at: datetime


class OrganizationWithRole(OrganizationOut):
    role: Role


class MembershipCreate(StrictModel):
    #: Identify the member by user id, or by username/email for a friendlier API.
    user_id: uuid.UUID | None = None
    identifier: str | None = None
    role: Role = Role.ANNOTATOR


class MembershipUpdate(StrictModel):
    role: Role


class MembershipOut(ORMModel):
    id: uuid.UUID
    organization_id: uuid.UUID
    role: Role
    created_at: datetime
    user: UserBrief
