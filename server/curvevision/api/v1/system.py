"""Health, readiness, background-task status and the format catalogue."""

from __future__ import annotations

import uuid

from fastapi import APIRouter, Query, status
from sqlalchemy import select, text

from curvevision import __version__
from curvevision.api.deps import IdentityDep, SessionDep, SettingsDep
from curvevision.core.errors import NotFoundError, PermissionDeniedError
from curvevision.core.pagination import Page, PageParamsDep, paginate
from curvevision.domain.enums import BackgroundTaskState
from curvevision.domain.system import BackgroundTask
from curvevision.formats import registry as format_registry
from curvevision.schemas.system import (
    BackgroundTaskOut,
    FormatCapabilitiesOut,
    HealthOut,
)

router = APIRouter(tags=["system"])


@router.get("/health", response_model=HealthOut)
async def health(session: SessionDep, settings: SettingsDep) -> HealthOut:
    """Liveness plus a real dependency check.

    A health endpoint that only proves the process is running tells an operator nothing
    useful, so this one actually touches the database.
    """
    try:
        await session.execute(text("SELECT 1"))
        database = "ok"
    except Exception:
        database = "unavailable"

    return HealthOut(
        status="ok" if database == "ok" else "degraded",
        version=__version__,
        environment=settings.environment,
        database=database,
        storage=settings.storage_backend,
        job_queue=settings.job_queue_backend,
        local_mode=settings.local_mode,
    )


@router.get("/formats", response_model=list[FormatCapabilitiesOut])
async def list_formats() -> list[FormatCapabilitiesOut]:
    """Dataset formats this instance can import and export.

    Each format declares what survives a round trip, so a client can warn *before* an
    export silently drops annotations the target format cannot represent.
    """
    return [
        FormatCapabilitiesOut(
            id=fmt.id,
            name=fmt.name,
            version=fmt.version,
            extension=fmt.extension,
            supports_import=fmt.capabilities.supports_import,
            supports_export=fmt.capabilities.supports_export,
            shape_types=[shape.value for shape in fmt.capabilities.shape_types],
            supports_tracks=fmt.capabilities.supports_tracks,
            supports_tags=fmt.capabilities.supports_tags,
            supports_attributes=fmt.capabilities.supports_attributes,
            notes=fmt.capabilities.notes,
        )
        for fmt in format_registry.all_formats()
    ]


@router.get("/background-tasks", response_model=Page[BackgroundTaskOut])
async def list_background_tasks(
    identity: IdentityDep,
    session: SessionDep,
    params: PageParamsDep,
    state: BackgroundTaskState | None = Query(default=None),
    resource_id: uuid.UUID | None = Query(default=None),
) -> Page[BackgroundTaskOut]:
    statement = select(BackgroundTask)
    if not identity.principal.is_superuser:
        statement = statement.where(BackgroundTask.created_by_id == identity.principal.user_id)
    if state is not None:
        statement = statement.where(BackgroundTask.state == state)
    if resource_id is not None:
        statement = statement.where(BackgroundTask.resource_id == resource_id)

    total, rows = await paginate(
        session, statement.order_by(BackgroundTask.created_at.desc()), params
    )
    return Page(
        count=total,
        limit=params.limit,
        offset=params.offset,
        results=[BackgroundTaskOut.model_validate(row) for row in rows],
    )


@router.get("/background-tasks/{task_id}", response_model=BackgroundTaskOut)
async def read_background_task(
    task_id: uuid.UUID, identity: IdentityDep, session: SessionDep
) -> BackgroundTaskOut:
    task = await session.get(BackgroundTask, task_id)
    if task is None:
        raise NotFoundError("Background task not found")
    if not identity.principal.is_superuser and task.created_by_id != identity.principal.user_id:
        raise PermissionDeniedError("This background task belongs to another user")
    return BackgroundTaskOut.model_validate(task)


@router.delete("/background-tasks/{task_id}", status_code=status.HTTP_204_NO_CONTENT)
async def cancel_background_task(
    task_id: uuid.UUID, identity: IdentityDep, session: SessionDep
) -> None:
    """Request cancellation.

    Cancellation is cooperative: a task already running finishes its current step. Marking
    it cancelled prevents a retry from picking it up again.
    """
    task = await session.get(BackgroundTask, task_id)
    if task is None:
        raise NotFoundError("Background task not found")
    if not identity.principal.is_superuser and task.created_by_id != identity.principal.user_id:
        raise PermissionDeniedError("This background task belongs to another user")
    if task.state in (BackgroundTaskState.PENDING, BackgroundTaskState.RUNNING):
        task.state = BackgroundTaskState.CANCELLED
    await session.commit()
