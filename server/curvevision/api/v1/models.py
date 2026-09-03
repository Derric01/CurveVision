"""Model registrations and AI-assisted annotation."""

from __future__ import annotations

import uuid

from fastapi import APIRouter, Query, status
from sqlalchemy import or_, select

from curvevision.api.deps import (
    IdentityDep,
    JobScopeDep,
    SessionDep,
    SettingsDep,
    organization_context,
)
from curvevision.core.errors import ConflictError, NotFoundError
from curvevision.domain.system import ModelRegistration
from curvevision.policy import Action, ResourceType, require
from curvevision.schemas.system import (
    InferenceRunRequest,
    InferenceRunResult,
    ModelRegistrationCreate,
    ModelRegistrationOut,
    ModelRegistrationUpdate,
)
from curvevision.services import inference as inference_service
from curvevision.storage import get_storage

router = APIRouter(prefix="/models", tags=["ai"])


@router.get("", response_model=list[ModelRegistrationOut])
async def list_models(
    identity: IdentityDep,
    session: SessionDep,
    organization_id: uuid.UUID | None = Query(default=None),
) -> list[ModelRegistrationOut]:
    """Models available to the caller.

    A registration with a null ``organization_id`` is instance-wide, which is how a
    self-hoster shares one inference endpoint across every workspace.
    """
    statement = select(ModelRegistration).where(ModelRegistration.is_active.is_(True))
    if organization_id is not None:
        context = await organization_context(session, identity, organization_id, ResourceType.MODEL)
        require(identity.principal, Action.VIEW, context)
        statement = statement.where(
            or_(
                ModelRegistration.organization_id == organization_id,
                ModelRegistration.organization_id.is_(None),
            )
        )
    elif not identity.principal.is_superuser:
        from curvevision.domain.identity import OrganizationMembership

        member_orgs = select(OrganizationMembership.organization_id).where(
            OrganizationMembership.user_id == identity.principal.user_id
        )
        statement = statement.where(
            or_(
                ModelRegistration.organization_id.in_(member_orgs),
                ModelRegistration.organization_id.is_(None),
            )
        )

    result = await session.execute(statement.order_by(ModelRegistration.name))
    return [ModelRegistrationOut.model_validate(row) for row in result.scalars().all()]


@router.post("", response_model=ModelRegistrationOut, status_code=status.HTTP_201_CREATED)
async def register_model(
    payload: ModelRegistrationCreate,
    identity: IdentityDep,
    session: SessionDep,
    organization_id: uuid.UUID = Query(...),
) -> ModelRegistrationOut:
    context = await organization_context(session, identity, organization_id, ResourceType.MODEL)
    require(identity.principal, Action.CREATE, context)

    existing = await session.execute(
        select(ModelRegistration).where(
            ModelRegistration.organization_id == organization_id,
            ModelRegistration.slug == payload.slug,
        )
    )
    if existing.scalar_one_or_none() is not None:
        raise ConflictError(f"A model with the slug {payload.slug!r} already exists here")

    model = ModelRegistration(
        organization_id=organization_id,
        slug=payload.slug,
        name=payload.name,
        description=payload.description,
        provider=payload.provider,
        kind=payload.kind,
        config=payload.config,
        output_labels=payload.output_labels,
    )
    session.add(model)
    await session.commit()
    return ModelRegistrationOut.model_validate(model)


@router.patch("/{model_id}", response_model=ModelRegistrationOut)
async def update_model(
    model_id: uuid.UUID,
    payload: ModelRegistrationUpdate,
    identity: IdentityDep,
    session: SessionDep,
) -> ModelRegistrationOut:
    model = await session.get(ModelRegistration, model_id)
    if model is None:
        raise NotFoundError("Model not found")
    if model.organization_id is not None:
        context = await organization_context(
            session, identity, model.organization_id, ResourceType.MODEL
        )
        require(identity.principal, Action.UPDATE, context)
    elif not identity.principal.is_superuser:
        raise NotFoundError("Model not found")

    for field in ("name", "description", "config", "output_labels", "is_active"):
        value = getattr(payload, field)
        if value is not None:
            setattr(model, field, value)
    await session.commit()
    return ModelRegistrationOut.model_validate(model)


@router.delete("/{model_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_model(model_id: uuid.UUID, identity: IdentityDep, session: SessionDep) -> None:
    model = await session.get(ModelRegistration, model_id)
    if model is None:
        raise NotFoundError("Model not found")
    if model.organization_id is not None:
        context = await organization_context(
            session, identity, model.organization_id, ResourceType.MODEL
        )
        require(identity.principal, Action.DELETE, context)
    elif not identity.principal.is_superuser:
        raise NotFoundError("Model not found")
    await session.delete(model)
    await session.commit()


inference_router = APIRouter(tags=["ai"])


@inference_router.post("/jobs/{job_id}/inference", response_model=InferenceRunResult)
async def run_inference(
    payload: InferenceRunRequest,
    scope: JobScopeDep,
    session: SessionDep,
    settings: SettingsDep,
) -> InferenceRunResult:
    """Run a model over frames of this job and write the predictions as suggestions.

    Predictions are ordinary annotations with ``source="model"`` and a confidence: the
    annotator accepts, edits or rejects them individually. Nothing about this endpoint is
    required to annotate manually.
    """
    scope.authorize(Action.RUN_INFERENCE, ResourceType.ANNOTATION)
    model = await inference_service.get_model(
        session, payload.model_id, scope.project.organization_id
    )

    result = await inference_service.run_inference(
        session,
        settings,
        get_storage(settings),
        model=model,
        job=scope.job,
        task=scope.task,
        frames=payload.frames,
        confidence_threshold=payload.confidence_threshold,
        prompts=payload.prompts,
    )

    if not payload.persist:
        return InferenceRunResult(
            shapes=[
                {
                    "frame": shape.frame,
                    "label": shape.label,
                    "type": shape.shape_type.value,
                    "points": shape.points,
                    "confidence": shape.confidence,
                    "elements": shape.elements,
                    "mask": shape.mask,
                }
                for shape in result.shapes
            ],
            frames_processed=len({shape.frame for shape in result.shapes}),
            warnings=result.warnings,
        )

    summary = await inference_service.persist_predictions(
        session,
        scope.job,
        result,
        label_mapping=payload.label_mapping,
        actor=scope.identity.user,
    )
    await session.commit()
    return InferenceRunResult(
        frames_processed=len({shape.frame for shape in result.shapes}),
        created_shapes=int(summary["created_shapes"]),
        created_tags=int(summary["created_tags"]),
        annotation_version=int(summary["annotation_version"]),
        warnings=list(summary["warnings"]),
    )
