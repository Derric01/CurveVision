"""Review, datasets, background tasks, webhooks and models."""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from pydantic import AnyHttpUrl, Field

from curvevision.domain.enums import (
    BackgroundTaskState,
    DatasetVersionState,
    IssueState,
    ModelKind,
    WebhookEvent,
)
from curvevision.schemas.common import ORMModel, Slug, StrictModel
from curvevision.schemas.identity import UserBrief

# --------------------------------------------------------------------------- review


class CommentIn(StrictModel):
    body: str = Field(min_length=1, max_length=10_000)


class CommentOut(ORMModel):
    id: uuid.UUID
    issue_id: uuid.UUID
    body: str
    created_at: datetime
    author: UserBrief | None = None


class IssueCreate(StrictModel):
    frame: int = Field(default=0, ge=0)
    position: list[float] = Field(default_factory=list)
    shape_id: uuid.UUID | None = None
    track_id: uuid.UUID | None = None
    body: str = Field(min_length=1, max_length=10_000)


class IssueUpdate(StrictModel):
    state: IssueState | None = None
    position: list[float] | None = None


class IssueOut(ORMModel):
    id: uuid.UUID
    job_id: uuid.UUID
    frame: int
    position: list[float]
    shape_id: uuid.UUID | None = None
    track_id: uuid.UUID | None = None
    state: IssueState
    created_at: datetime
    resolved_at: datetime | None = None
    comments: list[CommentOut] = Field(default_factory=list)


class AnnotationEventOut(ORMModel):
    id: uuid.UUID
    job_id: uuid.UUID
    action: str
    summary: dict[str, Any]
    annotation_version: int
    created_at: datetime


# -------------------------------------------------------------------------- datasets


class DatasetVersionCreate(StrictModel):
    name: str = Field(min_length=1, max_length=120)
    description: str | None = None
    #: Restrict the snapshot to these tasks; empty means the whole project.
    task_ids: list[uuid.UUID] = Field(default_factory=list)


class DatasetVersionOut(ORMModel):
    id: uuid.UUID
    project_id: uuid.UUID
    name: str
    description: str | None = None
    state: DatasetVersionState
    content_hash: str | None = None
    statistics: dict[str, Any]
    created_at: datetime
    released_at: datetime | None = None


class ExportRequest(StrictModel):
    format: str = Field(description="Format id, e.g. 'coco', 'yolo', 'voc', 'curvevision'")
    #: Restrict the export to these tasks; empty means everything in scope.
    task_ids: list[uuid.UUID] = Field(default_factory=list)
    include_images: bool = False
    #: Export only jobs that passed review. The default for anything used to train.
    only_accepted: bool = False


class ImportRequest(StrictModel):
    format: str
    task_id: uuid.UUID
    #: What to do about annotations already present.
    conflict_policy: str = Field(default="append", pattern="^(append|replace)$")
    #: Create labels named in the file but missing from the project schema.
    create_missing_labels: bool = True


class FormatCapabilitiesOut(StrictModel):
    id: str
    name: str
    version: str
    extension: str
    supports_import: bool
    supports_export: bool
    shape_types: list[str]
    supports_tracks: bool
    supports_tags: bool
    supports_attributes: bool
    notes: str | None = None


# ------------------------------------------------------------------ background tasks


class BackgroundTaskOut(ORMModel):
    id: uuid.UUID
    kind: str
    state: BackgroundTaskState
    progress: float
    message: str | None = None
    error: str | None = None
    result: dict[str, Any]
    resource_type: str | None = None
    resource_id: uuid.UUID | None = None
    created_at: datetime
    started_at: datetime | None = None
    finished_at: datetime | None = None


# -------------------------------------------------------------------------- webhooks


class WebhookCreate(StrictModel):
    target_url: AnyHttpUrl
    events: list[WebhookEvent] = Field(min_length=1)
    description: str | None = Field(default=None, max_length=500)
    project_id: uuid.UUID | None = None


class WebhookUpdate(StrictModel):
    target_url: AnyHttpUrl | None = None
    events: list[WebhookEvent] | None = None
    is_active: bool | None = None
    description: str | None = Field(default=None, max_length=500)


class WebhookOut(ORMModel):
    id: uuid.UUID
    organization_id: uuid.UUID | None = None
    project_id: uuid.UUID | None = None
    target_url: str
    events: list[str]
    is_active: bool
    description: str | None = None
    created_at: datetime


class WebhookCreated(WebhookOut):
    #: Shown once. Sign deliveries with it: `sha256=HMAC(secret, body)`.
    secret: str


# ---------------------------------------------------------------------------- models


class ModelRegistrationCreate(StrictModel):
    slug: Slug = Field(min_length=2, max_length=64)
    name: str = Field(min_length=1, max_length=200)
    description: str | None = None
    provider: str = "http"
    kind: ModelKind
    config: dict[str, Any] = Field(default_factory=dict)
    output_labels: list[str] = Field(default_factory=list)


class ModelRegistrationUpdate(StrictModel):
    name: str | None = None
    description: str | None = None
    config: dict[str, Any] | None = None
    output_labels: list[str] | None = None
    is_active: bool | None = None


class ModelRegistrationOut(ORMModel):
    id: uuid.UUID
    organization_id: uuid.UUID | None = None
    slug: str
    name: str
    description: str | None = None
    provider: str
    kind: ModelKind
    output_labels: list[str]
    is_active: bool
    created_at: datetime


class InferenceRunRequest(StrictModel):
    model_id: uuid.UUID
    job_id: uuid.UUID
    #: Frames to run on; empty means every frame in the job.
    frames: list[int] = Field(default_factory=list)
    #: Maps a model output label to a project label id. Unmapped outputs are dropped.
    label_mapping: dict[str, uuid.UUID] = Field(default_factory=dict)
    confidence_threshold: float = Field(default=0.5, ge=0.0, le=1.0)
    #: Write predictions straight into the job as `source="model"` suggestions.
    persist: bool = True
    #: Interactive segmentation prompts, for `interactor` models.
    prompts: dict[str, Any] = Field(default_factory=dict)


class InferenceRunResult(StrictModel):
    background_task_id: uuid.UUID | None = None
    #: Predictions returned without being written, when `persist` was false. This is the
    #: interactive path: the editor previews them and the annotator decides.
    shapes: list[dict[str, Any]] = Field(default_factory=list)
    frames_processed: int = 0
    #: Counts and warnings when predictions were written into the job.
    created_shapes: int = 0
    created_tags: int = 0
    annotation_version: int | None = None
    warnings: list[str] = Field(default_factory=list)


class SuggestionDecision(StrictModel):
    """Accept or reject model-produced annotations."""

    shape_ids: list[uuid.UUID] = Field(default_factory=list)
    track_ids: list[uuid.UUID] = Field(default_factory=list)
    tag_ids: list[uuid.UUID] = Field(default_factory=list)
    accepted: bool


class HealthOut(StrictModel):
    status: str
    version: str
    environment: str
    database: str
    storage: str
    job_queue: str
    #: True when this instance is a single-user desktop installation. The web client reads
    #: it to skip the sign-in screen and hide the multi-user chrome; it is not a secret,
    #: and a local instance is only reachable from the machine it runs on anyway.
    local_mode: bool = False


class QualityReportOut(ORMModel):
    """What comparing a job against ground truth found."""

    id: uuid.UUID
    task_id: uuid.UUID
    job_id: uuid.UUID | None
    ground_truth_job_id: uuid.UUID | None
    iou_threshold: float
    precision: float
    recall: float
    f1: float
    #: Per-label scores and the classified conflicts. See `services/comparison.py`.
    details: dict[str, Any] = Field(default_factory=dict)
    created_at: datetime


class QualityRequest(StrictModel):
    """Ask for a job to be scored.

    `iou_threshold` is how much overlap counts as "the same object". 0.5 is the COCO
    convention and a reasonable default for boxes; raise it when the task needs tight
    geometry and lower it when objects are small or annotators trace loosely.
    """

    iou_threshold: float = Field(default=0.5, gt=0.0, le=1.0)


class GroundTruthJobIn(StrictModel):
    """Create the job that holds a task's ground truth.

    Both frame bounds are optional and default to the whole task. Narrowing the range is the
    normal case on video: a reviewer annotates a slice properly, and every job overlapping
    that slice is scored on it.
    """

    start_frame: int | None = Field(default=None, ge=0)
    stop_frame: int | None = Field(default=None, ge=0)
    assignee_id: uuid.UUID | None = None
