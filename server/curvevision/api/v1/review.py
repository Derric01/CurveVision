"""Review: issues and comment threads."""

from __future__ import annotations

import uuid

from fastapi import APIRouter, Query, status
from sqlalchemy import select
from sqlalchemy.orm import selectinload

from curvevision.api.deps import JobScopeDep, SessionDep
from curvevision.core.db import utcnow
from curvevision.core.errors import NotFoundError
from curvevision.domain.enums import IssueState
from curvevision.domain.review import Comment, Issue
from curvevision.policy import Action, ResourceContext, ResourceType, require
from curvevision.schemas.system import (
    CommentIn,
    CommentOut,
    IssueCreate,
    IssueOut,
    IssueUpdate,
)

router = APIRouter(prefix="/jobs/{job_id}", tags=["review"])


@router.get("/issues", response_model=list[IssueOut])
async def list_issues(
    scope: JobScopeDep,
    session: SessionDep,
    state: IssueState | None = Query(default=None),
) -> list[IssueOut]:
    scope.authorize(Action.VIEW, ResourceType.ISSUE)
    statement = (
        select(Issue)
        .where(Issue.job_id == scope.job.id)
        .options(selectinload(Issue.comments).selectinload(Comment.author))
        .order_by(Issue.created_at)
    )
    if state is not None:
        statement = statement.where(Issue.state == state)
    issues = (await session.execute(statement)).scalars().all()
    return [IssueOut.model_validate(issue) for issue in issues]


@router.post("/issues", response_model=IssueOut, status_code=status.HTTP_201_CREATED)
async def create_issue(payload: IssueCreate, scope: JobScopeDep, session: SessionDep) -> IssueOut:
    """Open an issue anchored to a frame, and optionally to a specific shape.

    The first comment is created with the issue: an issue with no explanation is not
    actionable, so the API does not allow one.
    """
    scope.authorize(Action.CREATE, ResourceType.ISSUE)
    issue = Issue(
        job_id=scope.job.id,
        frame=payload.frame,
        position=payload.position,
        shape_id=payload.shape_id,
        track_id=payload.track_id,
        created_by_id=scope.identity.user.id,
    )
    session.add(issue)
    await session.flush()
    session.add(Comment(issue_id=issue.id, author_id=scope.identity.user.id, body=payload.body))
    await session.commit()
    return await _issue_out(session, issue.id)


@router.patch("/issues/{issue_id}", response_model=IssueOut)
async def update_issue(
    issue_id: uuid.UUID, payload: IssueUpdate, scope: JobScopeDep, session: SessionDep
) -> IssueOut:
    issue = await _get_issue(session, scope.job.id, issue_id)
    require(scope.identity.principal, Action.UPDATE, _issue_context(scope.context, issue))
    if payload.position is not None:
        issue.position = payload.position
    if payload.state is not None and payload.state is not issue.state:
        issue.state = payload.state
        if payload.state is IssueState.RESOLVED:
            issue.resolved_at = utcnow()
            issue.resolved_by_id = scope.identity.user.id
        else:
            issue.resolved_at = None
            issue.resolved_by_id = None
    await session.commit()
    return await _issue_out(session, issue_id)


@router.delete("/issues/{issue_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_issue(issue_id: uuid.UUID, scope: JobScopeDep, session: SessionDep) -> None:
    issue = await _get_issue(session, scope.job.id, issue_id)
    require(scope.identity.principal, Action.DELETE, _issue_context(scope.context, issue))
    await session.delete(issue)
    await session.commit()


@router.post(
    "/issues/{issue_id}/comments",
    response_model=CommentOut,
    status_code=status.HTTP_201_CREATED,
)
async def add_comment(
    issue_id: uuid.UUID, payload: CommentIn, scope: JobScopeDep, session: SessionDep
) -> CommentOut:
    scope.authorize(Action.CREATE, ResourceType.COMMENT)
    issue = await _get_issue(session, scope.job.id, issue_id)
    comment = Comment(issue_id=issue.id, author_id=scope.identity.user.id, body=payload.body)
    session.add(comment)
    await session.commit()
    await session.refresh(comment, ["author"])
    return CommentOut.model_validate(comment)


@router.delete("/issues/{issue_id}/comments/{comment_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_comment(
    issue_id: uuid.UUID,
    comment_id: uuid.UUID,
    scope: JobScopeDep,
    session: SessionDep,
) -> None:
    await _get_issue(session, scope.job.id, issue_id)
    comment = await session.get(Comment, comment_id)
    if comment is None or comment.issue_id != issue_id:
        raise NotFoundError("Comment not found")
    context = scope.context
    require(
        scope.identity.principal,
        Action.DELETE,
        ResourceContext(
            resource_type=ResourceType.COMMENT,
            organization_role=context.organization_role,
            author_id=comment.author_id,
            open_assignment=context.open_assignment,
        ),
    )
    await session.delete(comment)
    await session.commit()


# --------------------------------------------------------------------------- helpers


def _issue_context(context: ResourceContext, issue: Issue) -> ResourceContext:
    return ResourceContext(
        resource_type=ResourceType.ISSUE,
        organization_role=context.organization_role,
        author_id=issue.created_by_id,
        open_assignment=context.open_assignment,
    )


async def _get_issue(session: SessionDep, job_id: uuid.UUID, issue_id: uuid.UUID) -> Issue:
    issue = await session.get(Issue, issue_id)
    if issue is None or issue.job_id != job_id:
        raise NotFoundError("Issue not found")
    return issue


async def _issue_out(session: SessionDep, issue_id: uuid.UUID) -> IssueOut:
    result = await session.execute(
        select(Issue)
        .where(Issue.id == issue_id)
        .options(selectinload(Issue.comments).selectinload(Comment.author))
    )
    return IssueOut.model_validate(result.scalar_one())
