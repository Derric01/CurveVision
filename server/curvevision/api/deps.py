"""FastAPI dependencies.

The pattern every router follows: resolve the resource **once**, build a
``ResourceContext`` from it, then ask the policy engine. There is no
"check the role inside the handler" anywhere in the codebase.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from typing import Annotated

from fastapi import Depends, Header, Path, Request
from sqlalchemy.ext.asyncio import AsyncSession

from curvevision.core.config import Settings, get_settings
from curvevision.core.db import session_scope
from curvevision.core.errors import AuthenticationError, NotFoundError
from curvevision.core.logging import principal_id_var
from curvevision.domain.identity import User
from curvevision.domain.project import Project
from curvevision.domain.task import Job, Task
from curvevision.policy import Action, Principal, ResourceContext, ResourceType, require
from curvevision.services import auth as auth_service
from curvevision.services import organizations as org_service
from curvevision.services import projects as project_service
from curvevision.services import tasks as task_service


def request_settings(request: Request) -> Settings:
    """The Settings the *application* was built with.

    Not `get_settings()`: an app constructed as `create_app(settings)` — the desktop
    sidecar, every test — would otherwise have its engine bound to one configuration and
    its request handlers reading another from the environment. That divergence is silent
    and the symptoms are baffling (media written to the wrong directory, `local_mode`
    false inside a local install), so the app object is the single source of truth.
    """
    settings = getattr(request.app.state, "settings", None)
    return settings if isinstance(settings, Settings) else get_settings()


SessionDep = Annotated[AsyncSession, Depends(session_scope)]
SettingsDep = Annotated[Settings, Depends(request_settings)]


@dataclass(frozen=True, slots=True)
class Identity:
    """The authenticated caller."""

    user: User
    principal: Principal


async def _authenticate(
    session: AsyncSession, settings: Settings, authorization: str | None
) -> Identity:
    if not authorization or not authorization.lower().startswith("bearer "):
        raise AuthenticationError("Missing bearer credentials")
    token = authorization.split(" ", 1)[1].strip()

    # Two credential shapes share the Authorization header: `cv_...` API tokens for the
    # SDK/CLI/CI, and JWT access tokens for browser sessions.
    if token.startswith("cv_"):
        user, principal = await auth_service.resolve_api_token(session, token)
    else:
        user, principal = await auth_service.resolve_access_token(session, settings, token)

    principal_id_var.set(str(principal.user_id))
    return Identity(user=user, principal=principal)


async def current_identity(
    session: SessionDep,
    settings: SettingsDep,
    authorization: Annotated[str | None, Header()] = None,
) -> Identity:
    return await _authenticate(session, settings, authorization)


async def optional_identity(
    session: SessionDep,
    settings: SettingsDep,
    authorization: Annotated[str | None, Header()] = None,
) -> Identity | None:
    if not authorization:
        return None
    try:
        return await _authenticate(session, settings, authorization)
    except AuthenticationError:
        return None


IdentityDep = Annotated[Identity, Depends(current_identity)]


def client_ip(request: Request) -> str | None:
    """Best-effort client address.

    ``X-Forwarded-For`` is only consulted for the audit log, never for authorization, so a
    spoofed header cannot grant access.
    """
    forwarded = request.headers.get("x-forwarded-for")
    if forwarded:
        return forwarded.split(",")[0].strip()[:64]
    return request.client.host if request.client else None


# ----------------------------------------------------------------- resource resolution


@dataclass(frozen=True, slots=True)
class ProjectScope:
    project: Project
    identity: Identity
    context: ResourceContext

    def authorize(self, action: Action, resource: ResourceType | None = None) -> None:
        context = self.context if resource is None else _retype(self.context, resource)
        require(self.identity.principal, action, context)


@dataclass(frozen=True, slots=True)
class TaskScope:
    task: Task
    project: Project
    identity: Identity
    context: ResourceContext

    def authorize(self, action: Action, resource: ResourceType | None = None) -> None:
        context = self.context if resource is None else _retype(self.context, resource)
        require(self.identity.principal, action, context)


@dataclass(frozen=True, slots=True)
class JobScope:
    job: Job
    task: Task
    project: Project
    identity: Identity
    context: ResourceContext

    def authorize(self, action: Action, resource: ResourceType | None = None) -> None:
        context = self.context if resource is None else _retype(self.context, resource)
        require(self.identity.principal, action, context)


def _retype(context: ResourceContext, resource: ResourceType) -> ResourceContext:
    """Reuse a loaded context for a different resource type in the same scope."""
    return ResourceContext(
        resource_type=resource,
        organization_role=context.organization_role,
        owner_id=context.owner_id,
        assignee_id=context.assignee_id,
        reviewer_id=context.reviewer_id,
        author_id=context.author_id,
        job_state=context.job_state,
        job_kind=context.job_kind,
        open_assignment=context.open_assignment,
        extra=context.extra,
    )


async def project_scope(
    session: SessionDep,
    identity: IdentityDep,
    project_id: Annotated[uuid.UUID, Path()],
) -> ProjectScope:
    project = await project_service.get_project(session, project_id)
    role = await org_service.get_role(session, project.organization_id, identity.principal.user_id)
    if role is None and not identity.principal.is_superuser:
        # Not a member: report "not found" rather than "forbidden" so that project
        # existence is not observable to outsiders.
        raise NotFoundError("Project not found")
    context = ResourceContext(
        resource_type=ResourceType.PROJECT,
        organization_role=role,
        owner_id=project.owner_id,
        open_assignment=project.open_assignment,
    )
    scope = ProjectScope(project=project, identity=identity, context=context)
    scope.authorize(Action.VIEW)
    return scope


async def task_scope(
    session: SessionDep,
    identity: IdentityDep,
    task_id: Annotated[uuid.UUID, Path()],
) -> TaskScope:
    task = await task_service.get_task(session, task_id)
    project = await project_service.get_project(session, task.project_id)
    role = await org_service.get_role(session, project.organization_id, identity.principal.user_id)
    if role is None and not identity.principal.is_superuser:
        raise NotFoundError("Task not found")
    context = ResourceContext(
        resource_type=ResourceType.TASK,
        organization_role=role,
        owner_id=task.owner_id or project.owner_id,
        assignee_id=task.assignee_id,
        open_assignment=project.open_assignment,
    )
    scope = TaskScope(task=task, project=project, identity=identity, context=context)
    scope.authorize(Action.VIEW)
    return scope


async def job_scope(
    session: SessionDep,
    identity: IdentityDep,
    job_id: Annotated[uuid.UUID, Path()],
) -> JobScope:
    job = await task_service.get_job(session, job_id)
    task = await task_service.get_task(session, job.task_id)
    project = await project_service.get_project(session, task.project_id)
    role = await org_service.get_role(session, project.organization_id, identity.principal.user_id)
    if role is None and not identity.principal.is_superuser:
        raise NotFoundError("Job not found")
    context = ResourceContext(
        resource_type=ResourceType.JOB,
        organization_role=role,
        owner_id=task.owner_id or project.owner_id,
        assignee_id=job.assignee_id,
        reviewer_id=job.reviewer_id,
        job_state=job.state,
        job_kind=job.kind,
        open_assignment=project.open_assignment,
    )
    scope = JobScope(job=job, task=task, project=project, identity=identity, context=context)
    scope.authorize(Action.VIEW)
    return scope


ProjectScopeDep = Annotated[ProjectScope, Depends(project_scope)]
TaskScopeDep = Annotated[TaskScope, Depends(task_scope)]
JobScopeDep = Annotated[JobScope, Depends(job_scope)]


async def organization_context(
    session: AsyncSession,
    identity: Identity,
    organization_id: uuid.UUID,
    resource: ResourceType = ResourceType.ORGANIZATION,
) -> ResourceContext:
    role = await org_service.get_role(session, organization_id, identity.principal.user_id)
    if role is None and not identity.principal.is_superuser:
        raise NotFoundError("Organization not found")
    return ResourceContext(resource_type=resource, organization_role=role)
