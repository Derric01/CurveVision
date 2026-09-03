"""Domain enumerations.

Stored as strings rather than integers: readable in `psql`, stable across migrations, and
immune to the reordering bugs that integer enums invite.
"""

from __future__ import annotations

from enum import StrEnum


class Role(StrEnum):
    """Roles within an organization or project scope, ordered least to most privileged."""

    VIEWER = "viewer"
    ANNOTATOR = "annotator"
    REVIEWER = "reviewer"
    MAINTAINER = "maintainer"
    ADMIN = "admin"
    OWNER = "owner"


ROLE_RANK: dict[Role, int] = {
    Role.VIEWER: 0,
    Role.ANNOTATOR: 1,
    Role.REVIEWER: 2,
    Role.MAINTAINER: 3,
    Role.ADMIN: 4,
    Role.OWNER: 5,
}


class MediaKind(StrEnum):
    IMAGE = "image"
    VIDEO = "video"


class TaskStatus(StrEnum):
    DRAFT = "draft"  # created, media still uploading / processing
    READY = "ready"  # annotatable
    IN_PROGRESS = "in_progress"
    COMPLETED = "completed"
    ARCHIVED = "archived"


class JobState(StrEnum):
    NEW = "new"
    IN_PROGRESS = "in_progress"
    SUBMITTED = "submitted"  # annotator handed it to review
    ACCEPTED = "accepted"
    REJECTED = "rejected"


class JobKind(StrEnum):
    ANNOTATION = "annotation"
    GROUND_TRUTH = "ground_truth"  # honeypot / quality-comparison job


class ShapeType(StrEnum):
    RECTANGLE = "rectangle"
    ROTATED_RECTANGLE = "rotated_rectangle"
    POLYGON = "polygon"
    POLYLINE = "polyline"
    POINTS = "points"
    ELLIPSE = "ellipse"
    MASK = "mask"
    SKELETON = "skeleton"
    CUBOID = "cuboid"


#: Minimum number of coordinate pairs each geometry needs to be meaningful.
SHAPE_MIN_POINTS: dict[ShapeType, int] = {
    ShapeType.RECTANGLE: 2,
    ShapeType.ROTATED_RECTANGLE: 2,
    ShapeType.POLYGON: 3,
    ShapeType.POLYLINE: 2,
    ShapeType.POINTS: 1,
    ShapeType.ELLIPSE: 2,
    ShapeType.MASK: 2,
    ShapeType.SKELETON: 1,
    ShapeType.CUBOID: 8,
}


class AnnotationSource(StrEnum):
    """Provenance of an annotation. Survives export so dataset quality is auditable."""

    MANUAL = "manual"
    MODEL = "model"  # accepted as-is from a model
    MODEL_CORRECTED = "model_corrected"  # model prediction a human edited
    IMPORTED = "imported"
    INTERPOLATED = "interpolated"  # materialised from a track, not stored


class AttributeType(StrEnum):
    SELECT = "select"
    RADIO = "radio"
    CHECKBOX = "checkbox"
    TEXT = "text"
    NUMBER = "number"


class IssueState(StrEnum):
    OPEN = "open"
    RESOLVED = "resolved"


class BackgroundTaskState(StrEnum):
    PENDING = "pending"
    RUNNING = "running"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    CANCELLED = "cancelled"


class DatasetVersionState(StrEnum):
    DRAFT = "draft"
    RELEASED = "released"  # immutable from here on


class ModelKind(StrEnum):
    DETECTOR = "detector"
    SEGMENTER = "segmenter"
    INTERACTOR = "interactor"  # click/box-prompted segmentation
    TRACKER = "tracker"
    CLASSIFIER = "classifier"
    OCR = "ocr"


class WebhookEvent(StrEnum):
    PROJECT_CREATED = "project.created"
    PROJECT_UPDATED = "project.updated"
    TASK_CREATED = "task.created"
    TASK_UPDATED = "task.updated"
    TASK_COMPLETED = "task.completed"
    JOB_UPDATED = "job.updated"
    JOB_SUBMITTED = "job.submitted"
    JOB_ACCEPTED = "job.accepted"
    JOB_REJECTED = "job.rejected"
    ANNOTATIONS_UPDATED = "annotations.updated"
    ISSUE_CREATED = "issue.created"
    DATASET_RELEASED = "dataset.released"
