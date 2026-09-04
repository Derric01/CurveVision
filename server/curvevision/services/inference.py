"""Running models and turning predictions into editable suggestions.

Predictions land as annotations with ``source="model"`` and a confidence. They are ordinary
annotations -- selectable, editable, deletable -- shown distinctly in the editor and
individually acceptable. Editing one flips its provenance to ``model_corrected``, so the
dataset records that a human fixed a machine's guess.
"""

from __future__ import annotations

import uuid
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from curvevision.core.config import Settings
from curvevision.core.errors import NotFoundError, ValidationError
from curvevision.domain.annotation import Shape, Tag
from curvevision.domain.enums import SHAPE_MIN_POINTS, AnnotationSource, ShapeType
from curvevision.domain.identity import User
from curvevision.domain.media import MediaBlob
from curvevision.domain.system import ModelRegistration
from curvevision.domain.task import Job, Task
from curvevision.ml import (
    InferenceFrame,
    InferenceRequest,
    InferenceResult,
    get_provider,
)
from curvevision.services import annotations as annotation_service
from curvevision.services import media as media_service
from curvevision.storage import Storage


async def get_model(
    session: AsyncSession, model_id: uuid.UUID, organization_id: uuid.UUID | None
) -> ModelRegistration:
    model = await session.get(ModelRegistration, model_id)
    if model is None:
        raise NotFoundError("Model not found")
    if model.organization_id is not None and model.organization_id != organization_id:
        raise NotFoundError("Model not found")
    if not model.is_active:
        raise ValidationError(f"Model {model.name!r} is disabled")
    return model


async def collect_frames(
    session: AsyncSession,
    storage: Storage,
    task: Task,
    frames: list[int],
) -> list[InferenceFrame]:
    """Fetch the media bytes for the requested frames."""
    collected: list[InferenceFrame] = []
    for frame in frames:
        asset, _offset = await media_service.resolve_frame(session, task.id, frame)
        blob = await session.get(MediaBlob, asset.blob_id)
        if blob is None:
            continue
        collected.append(
            InferenceFrame(
                frame=frame,
                image=await storage.get(blob.storage_key),
                content_type=blob.content_type,
                width=blob.width,
                height=blob.height,
            )
        )
    return collected


async def run_inference(
    session: AsyncSession,
    settings: Settings,
    storage: Storage,
    *,
    model: ModelRegistration,
    job: Job,
    task: Task,
    frames: list[int],
    confidence_threshold: float,
    prompts: dict[str, Any],
) -> InferenceResult:
    if not frames:
        frames = list(range(job.start_frame, job.stop_frame + 1))
    invalid = [frame for frame in frames if not job.contains_frame(frame)]
    if invalid:
        raise ValidationError(
            f"Frames {invalid[:5]} are outside this job ({job.start_frame}-{job.stop_frame})"
        )

    inference_frames = await collect_frames(session, storage, task, frames)
    if not inference_frames:
        raise ValidationError("None of the requested frames have media available")

    provider = get_provider(model.provider)
    request = InferenceRequest(
        model=str(model.config.get("model", model.slug)),
        frames=inference_frames,
        confidence_threshold=confidence_threshold,
        prompts=prompts,
        options=dict(model.config.get("options", {})),
    )
    del settings  # provider timeouts come from the registration, not global settings
    return await provider.infer(request, model.config)


async def persist_predictions(
    session: AsyncSession,
    job: Job,
    result: InferenceResult,
    *,
    label_mapping: dict[str, uuid.UUID],
    actor: User,
) -> dict[str, Any]:
    """Write predictions into the job as suggestions.

    Unmapped model labels are dropped and reported rather than invented: silently creating
    labels from a model's vocabulary is how label schemas rot.
    """
    created_shapes = 0
    created_tags = 0
    skipped: dict[str, int] = {}

    for prediction in result.shapes:
        label_id = label_mapping.get(prediction.label)
        if label_id is None:
            skipped[prediction.label] = skipped.get(prediction.label, 0) + 1
            continue
        if not _geometry_is_usable(prediction.shape_type, prediction.points, prediction.elements):
            skipped[f"{prediction.label} (bad geometry)"] = (
                skipped.get(f"{prediction.label} (bad geometry)", 0) + 1
            )
            continue
        session.add(
            Shape(
                job_id=job.id,
                label_id=label_id,
                frame=prediction.frame,
                shape_type=prediction.shape_type,
                points=prediction.points,
                source=AnnotationSource.MODEL,
                confidence=prediction.confidence,
                attributes={},
                mask=prediction.mask,
                elements=prediction.elements,
                created_by_id=actor.id,
            )
        )
        created_shapes += 1

    for predicted_tag in result.tags:
        label_id = label_mapping.get(predicted_tag.label)
        if label_id is None:
            skipped[predicted_tag.label] = skipped.get(predicted_tag.label, 0) + 1
            continue
        session.add(
            Tag(
                job_id=job.id,
                label_id=label_id,
                frame=predicted_tag.frame,
                source=AnnotationSource.MODEL,
                confidence=predicted_tag.confidence,
                attributes={},
                created_by_id=actor.id,
            )
        )
        created_tags += 1

    await session.flush()
    job.annotation_version += 1
    await annotation_service.refresh_counts(session, job)

    warnings = list(result.warnings)
    if skipped:
        warnings.append(
            "Dropped predictions for unmapped or invalid labels: "
            + ", ".join(f"{name} x{count}" for name, count in sorted(skipped.items()))
        )

    return {
        "created_shapes": created_shapes,
        "created_tags": created_tags,
        "warnings": warnings,
        "annotation_version": job.annotation_version,
    }


def _geometry_is_usable(
    shape_type: ShapeType, points: list[float], elements: list[dict[str, Any]]
) -> bool:
    """Reject predictions the annotation layer would refuse anyway.

    A model that emits a two-point "polygon" should not fail the whole batch at the
    database boundary; it should have that one prediction dropped and reported.
    """
    if shape_type is ShapeType.SKELETON:
        return bool(elements)
    if shape_type is ShapeType.MASK:
        return True
    if len(points) % 2 != 0:
        return False
    return len(points) // 2 >= SHAPE_MIN_POINTS[shape_type]
