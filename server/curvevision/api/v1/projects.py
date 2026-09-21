"""Project and label endpoints."""

from __future__ import annotations

import uuid

from fastapi import APIRouter, Query, status
from sqlalchemy import func, select
from sqlalchemy.orm import selectinload

from curvevision.api.deps import (
    IdentityDep,
    ProjectScopeDep,
    SessionDep,
    organization_context,
)
from curvevision.core.errors import NotFoundError
from curvevision.core.pagination import Page, PageParamsDep, paginate
from curvevision.domain.identity import OrganizationMembership
from curvevision.domain.project import Label, Project
from curvevision.domain.task import Task
from curvevision.policy import Action, ResourceType, require
from curvevision.schemas.project import (
    LabelIn,
    LabelOut,
    ProjectCreate,
    ProjectDetail,
    ProjectOut,
    ProjectStatistics,
    ProjectUpdate,
)
from curvevision.services import projects as project_service

router = APIRouter(prefix="/projects", tags=["projects"])


@router.get("", response_model=Page[ProjectOut])
async def list_projects(
    identity: IdentityDep,
    session: SessionDep,
    params: PageParamsDep,
    organization_id: uuid.UUID | None = Query(default=None),
    search: str | None = Query(default=None, max_length=200),
) -> Page[ProjectOut]:
    """List projects visible to the caller.

    Visibility is enforced in the query, not after the fact: a project the caller cannot
    see never enters the result set, so pagination counts stay correct.
    """
    statement = select(Project).options(selectinload(Project.labels))
    if not identity.principal.is_superuser:
        member_orgs = select(OrganizationMembership.organization_id).where(
            OrganizationMembership.user_id == identity.principal.user_id
        )
        statement = statement.where(Project.organization_id.in_(member_orgs))
    if organization_id is not None:
        statement = statement.where(Project.organization_id == organization_id)
    if search:
        pattern = f"%{search.lower()}%"
        statement = statement.where(func.lower(Project.name).like(pattern))

    total, rows = await paginate(session, statement.order_by(Project.created_at.desc()), params)
    return Page(
        count=total,
        limit=params.limit,
        offset=params.offset,
        results=[ProjectOut.model_validate(row) for row in rows],
    )


@router.post("", response_model=ProjectDetail, status_code=status.HTTP_201_CREATED)
async def create_project(
    payload: ProjectCreate, identity: IdentityDep, session: SessionDep
) -> ProjectDetail:
    context = await organization_context(
        session, identity, payload.organization_id, ResourceType.PROJECT
    )
    require(identity.principal, Action.CREATE, context)
    project = await project_service.create_project(
        session,
        organization_id=payload.organization_id,
        slug=payload.slug,
        name=payload.name,
        description=payload.description,
        open_assignment=payload.open_assignment,
        labels=payload.labels,
        owner=identity.user,
    )
    await session.commit()
    return await _project_detail(session, project)


@router.get("/{project_id}", response_model=ProjectDetail)
async def read_project(scope: ProjectScopeDep, session: SessionDep) -> ProjectDetail:
    return await _project_detail(session, scope.project)


@router.patch("/{project_id}", response_model=ProjectDetail)
async def update_project(
    payload: ProjectUpdate, scope: ProjectScopeDep, session: SessionDep
) -> ProjectDetail:
    scope.authorize(Action.UPDATE)
    project = scope.project
    sent = payload.model_fields_set
    if payload.name is not None:
        project.name = payload.name
    # See `update_task`: for a nullable field an omitted one and an explicit null are
    # different instructions, and `is not None` reads both as "leave it alone".
    if "description" in sent:
        project.description = payload.description
    if payload.open_assignment is not None:
        project.open_assignment = payload.open_assignment
    if "owner_id" in sent:
        project.owner_id = payload.owner_id
    await session.commit()
    if "owner_id" in sent:
        # `owner` is eagerly loaded and survives the commit, so the response would name
        # whoever owned the project before the write.
        await session.refresh(project, ["owner_id", "owner"])
    return await _project_detail(session, project)


@router.delete("/{project_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_project(scope: ProjectScopeDep, session: SessionDep) -> None:
    scope.authorize(Action.DELETE)
    await session.delete(scope.project)
    await session.commit()


@router.get("/{project_id}/statistics", response_model=ProjectStatistics)
async def project_statistics(scope: ProjectScopeDep, session: SessionDep) -> ProjectStatistics:
    stats = await project_service.project_statistics(session, scope.project.id)
    return ProjectStatistics.model_validate(stats)


# ---------------------------------------------------------------------------- labels


@router.get("/{project_id}/labels", response_model=list[LabelOut])
async def list_labels(scope: ProjectScopeDep, session: SessionDep) -> list[LabelOut]:
    scope.authorize(Action.VIEW, ResourceType.LABEL)
    labels = await project_service.load_project_labels(session, scope.project.id)
    return [LabelOut.model_validate(label) for label in labels]


@router.post("/{project_id}/labels", response_model=LabelOut, status_code=status.HTTP_201_CREATED)
async def create_label(payload: LabelIn, scope: ProjectScopeDep, session: SessionDep) -> LabelOut:
    """Add a label to a project that already exists.

    A label sent without a position goes to the **end** of the schema. It used to take
    position 0 like every other unpositioned label, and the listing orders by
    `(position, name)` — so `van` added to a `car`/`pedestrian` schema appeared first, in
    an order nobody chose. The schema's order is the order the editor's label picker shows
    and the order its shortcuts run in, so it is not cosmetic.
    """
    scope.authorize(Action.CREATE, ResourceType.LABEL)
    existing = await session.execute(
        select(func.count())
        .select_from(Label)
        .where(Label.project_id == scope.project.id, Label.parent_id.is_(None))
    )
    label = await project_service.create_label(
        session, scope.project, payload, default_position=existing.scalar_one()
    )
    await session.commit()
    return await _label_out(session, label.id)


@router.put("/{project_id}/labels/{label_id}", response_model=LabelOut)
async def update_label(
    label_id: uuid.UUID, payload: LabelIn, scope: ProjectScopeDep, session: SessionDep
) -> LabelOut:
    scope.authorize(Action.UPDATE, ResourceType.LABEL)
    label = await _get_label(session, scope.project.id, label_id)
    await project_service.update_label(session, label, payload)
    await session.commit()
    return await _label_out(session, label_id)


@router.delete("/{project_id}/labels/{label_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_label(label_id: uuid.UUID, scope: ProjectScopeDep, session: SessionDep) -> None:
    scope.authorize(Action.DELETE, ResourceType.LABEL)
    label = await _get_label(session, scope.project.id, label_id)
    await project_service.delete_label(session, label)
    await session.commit()


# --------------------------------------------------------------------------- helpers


async def _get_label(session: SessionDep, project_id: uuid.UUID, label_id: uuid.UUID) -> Label:
    label = await session.get(Label, label_id)
    if label is None or label.project_id != project_id:
        raise NotFoundError("Label not found")
    return label


async def _label_out(session: SessionDep, label_id: uuid.UUID) -> LabelOut:
    result = await session.execute(
        select(Label)
        .where(Label.id == label_id)
        .options(
            selectinload(Label.attributes),
            selectinload(Label.children).selectinload(Label.attributes),
        )
        # Without this the eager loads below are skipped for an instance already in the
        # session, which after a commit under `expire_on_commit=False` still holds the
        # collection as it was *before* the write: an update that replaced a label's
        # attributes came back reporting the old ones, including attributes it had just
        # deleted. A caller rendering the response would show a schema that no longer
        # exists.
        .execution_options(populate_existing=True)
    )
    label = result.scalar_one()
    return LabelOut.model_validate(label)


async def _project_detail(session: SessionDep, project: Project) -> ProjectDetail:
    labels = await project_service.load_project_labels(session, project.id)
    task_count = (
        await session.execute(
            select(func.count()).select_from(Task).where(Task.project_id == project.id)
        )
    ).scalar_one()
    # Built from the flat projection plus explicitly loaded relations, rather than
    # validating the ORM object directly: the latter would lazily touch `labels`.
    return ProjectDetail(
        **ProjectOut.model_validate(project).model_dump(),
        labels=[LabelOut.model_validate(label) for label in labels],
        task_count=int(task_count),
    )
