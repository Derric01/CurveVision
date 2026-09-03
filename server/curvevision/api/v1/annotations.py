"""Annotation endpoints -- the editor's hot path."""

from __future__ import annotations

from fastapi import APIRouter, Query, status
from sqlalchemy import select

from curvevision.api.deps import JobScopeDep, SessionDep
from curvevision.core.pagination import Page, PageParamsDep, paginate
from curvevision.domain.review import AnnotationEvent
from curvevision.policy import Action, ResourceType
from curvevision.schemas.annotation import (
    AnnotationDocument,
    AnnotationWrite,
    AnnotationWriteResult,
    FrameAnnotations,
    ShapeOut,
    TagOut,
    TrackOut,
)
from curvevision.schemas.system import AnnotationEventOut, SuggestionDecision
from curvevision.services import annotations as annotation_service

router = APIRouter(prefix="/jobs/{job_id}", tags=["annotations"])


@router.get("/annotations", response_model=AnnotationDocument)
async def read_annotations(
    scope: JobScopeDep,
    session: SessionDep,
    start_frame: int | None = Query(default=None, ge=0),
    stop_frame: int | None = Query(default=None, ge=0),
) -> AnnotationDocument:
    """Every annotation in the job, tracks unexpanded.

    The editor loads this once per job and interpolates client-side while scrubbing; the
    per-frame endpoint below exists for scripted clients that want the server to do it.
    """
    scope.authorize(Action.VIEW, ResourceType.ANNOTATION)
    data = await annotation_service.load_annotations(
        session, scope.job, start_frame=start_frame, stop_frame=stop_frame
    )
    return AnnotationDocument(
        job_id=data["job_id"],
        annotation_version=data["annotation_version"],
        shapes=[ShapeOut.model_validate(shape) for shape in data["shapes"]],
        tracks=[TrackOut.model_validate(track) for track in data["tracks"]],
        tags=[TagOut.model_validate(tag) for tag in data["tags"]],
    )


@router.patch("/annotations", response_model=AnnotationWriteResult)
async def write_annotations(
    payload: AnnotationWrite, scope: JobScopeDep, session: SessionDep
) -> AnnotationWriteResult:
    """Apply one autosave batch.

    Send the ``annotation_version`` you last read; a stale version is rejected with 409 so
    two annotators cannot silently overwrite one another.
    """
    scope.authorize(Action.UPDATE, ResourceType.ANNOTATION)
    schema = await annotation_service.LabelSchema.load(session, scope.project.id)
    result = await annotation_service.write_annotations(
        session, scope.job, payload, actor=scope.identity.user, schema=schema
    )
    await session.commit()
    return AnnotationWriteResult.model_validate(result)


@router.delete("/annotations", status_code=status.HTTP_204_NO_CONTENT)
async def clear_annotations(scope: JobScopeDep, session: SessionDep) -> None:
    scope.authorize(Action.DELETE, ResourceType.ANNOTATION)
    await annotation_service.clear_annotations(session, scope.job)
    scope.job.annotation_version += 1
    await session.commit()


@router.get("/frames/{frame}/annotations", response_model=FrameAnnotations)
async def read_frame_annotations(
    frame: int, scope: JobScopeDep, session: SessionDep
) -> FrameAnnotations:
    """Everything visible on one frame, with tracks already interpolated server-side."""
    scope.authorize(Action.VIEW, ResourceType.ANNOTATION)
    data = await annotation_service.frame_annotations(session, scope.job, frame)
    return FrameAnnotations(
        frame=data["frame"],
        shapes=[ShapeOut.model_validate(shape) for shape in data["shapes"]],
        tags=[TagOut.model_validate(tag) for tag in data["tags"]],
    )


@router.post("/suggestions", response_model=dict[str, int])
async def decide_suggestions(
    payload: SuggestionDecision, scope: JobScopeDep, session: SessionDep
) -> dict[str, int]:
    """Accept or reject model-produced annotations in bulk."""
    scope.authorize(Action.UPDATE, ResourceType.ANNOTATION)
    counts = await annotation_service.decide_suggestions(
        session,
        scope.job,
        shape_ids=payload.shape_ids,
        track_ids=payload.track_ids,
        tag_ids=payload.tag_ids,
        accepted=payload.accepted,
    )
    await session.commit()
    return counts


@router.get("/history", response_model=Page[AnnotationEventOut])
async def annotation_history(
    scope: JobScopeDep, session: SessionDep, params: PageParamsDep
) -> Page[AnnotationEventOut]:
    statement = select(AnnotationEvent).where(AnnotationEvent.job_id == scope.job.id)
    total, rows = await paginate(
        session, statement.order_by(AnnotationEvent.created_at.desc()), params
    )
    return Page(
        count=total,
        limit=params.limit,
        offset=params.offset,
        results=[AnnotationEventOut.model_validate(row) for row in rows],
    )
