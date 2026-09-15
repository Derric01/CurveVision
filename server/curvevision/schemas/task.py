"""Tasks, jobs and media assets."""

from __future__ import annotations

import uuid
from datetime import datetime

from pydantic import Field

from curvevision.domain.enums import JobKind, JobState, MediaKind, TaskStatus
from curvevision.schemas.common import ORMModel, StrictModel
from curvevision.schemas.identity import UserBrief


class TaskCreate(StrictModel):
    project_id: uuid.UUID
    name: str = Field(min_length=1, max_length=300)
    description: str | None = None
    media_kind: MediaKind = MediaKind.IMAGE
    #: Frames per job. 0 creates a single job covering the whole task.
    segment_size: int = Field(default=0, ge=0, le=100_000)
    overlap: int = Field(default=0, ge=0, le=1_000)
    assignee_id: uuid.UUID | None = None


class TaskUpdate(StrictModel):
    name: str | None = Field(default=None, min_length=1, max_length=300)
    description: str | None = None
    status: TaskStatus | None = None
    assignee_id: uuid.UUID | None = None


class TaskOut(ORMModel):
    id: uuid.UUID
    project_id: uuid.UUID
    name: str
    description: str | None = None
    status: TaskStatus
    media_kind: MediaKind
    frame_count: int
    segment_size: int
    overlap: int
    created_at: datetime
    updated_at: datetime
    owner: UserBrief | None = None
    assignee: UserBrief | None = None


class TaskProgress(StrictModel):
    task_id: uuid.UUID
    job_count: int
    jobs_by_state: dict[str, int]
    completed_frames: int
    total_frames: int
    #: 0..1. Jobs in `accepted` count fully; `submitted` counts as half, because work in
    #: review is real progress but is not finished.
    completion: float


class TaskDetail(TaskOut):
    progress: TaskProgress | None = None


class JobUpdate(StrictModel):
    state: JobState | None = None
    assignee_id: uuid.UUID | None = None
    reviewer_id: uuid.UUID | None = None
    locked: bool | None = None


class JobOut(ORMModel):
    id: uuid.UUID
    task_id: uuid.UUID
    index: int
    kind: JobKind
    state: JobState
    start_frame: int
    stop_frame: int
    annotation_version: int
    shape_count: int
    track_count: int
    tag_count: int
    locked: bool
    created_at: datetime
    updated_at: datetime
    submitted_at: datetime | None = None
    reviewed_at: datetime | None = None
    assignee: UserBrief | None = None
    reviewer: UserBrief | None = None


class JobReviewRequest(StrictModel):
    #: True accepts the work, False sends it back to the annotator.
    accepted: bool
    comment: str | None = Field(default=None, max_length=5000)


class AssetOut(ORMModel):
    id: uuid.UUID
    task_id: uuid.UUID
    name: str
    position: int
    start_frame: int
    frame_count: int
    #: False while `frame_count` is still the upload-time estimate rather than a decoded
    #: count. Always True for an image, which contributes exactly one frame by definition.
    frame_count_exact: bool = True
    created_at: datetime


class LocalImportRequest(StrictModel):
    """Attach media that is already on this machine. Desktop (local) mode only."""

    #: An absolute path to a file or a folder. `~` is expanded.
    path: str = Field(min_length=1, max_length=4000)
    recursive: bool = True


class LocalImportResult(StrictModel):
    task_id: uuid.UUID
    imported: list[AssetOut]
    #: Files that were found but could not be attached, each with the reason. A folder of
    #: photographs with one corrupt file still imports the rest.
    skipped: list[str] = Field(default_factory=list)
    frame_count: int


class FrameInfo(StrictModel):
    """Everything the editor needs to display one frame."""

    frame: int
    asset_id: uuid.UUID
    name: str
    width: int | None = None
    height: int | None = None
    #: Frame offset inside the asset; always 0 for images.
    offset: int = 0
    media_url: str
    thumbnail_url: str | None = None


class TaskMediaMeta(StrictModel):
    task_id: uuid.UUID
    media_kind: MediaKind
    frame_count: int
    frames_per_chunk: int
    chunk_count: int
    #: False when at least one asset's frame count is still the upload-time estimate, so
    #: `frame_count` may offer frames the media does not contain. Always True for a task of
    #: images, and for a video task whose probe has finished.
    frame_count_exact: bool = True
    #: The names of the assets that are still estimates, so a warning can name the file
    #: rather than only say that something is wrong. Capped at `ESTIMATED_ASSET_SAMPLE`
    #: because a task may hold tens of thousands of assets; `estimated_asset_count` is the
    #: true total.
    estimated_assets: list[str] = Field(default_factory=list)
    estimated_asset_count: int = 0


#: How many filenames `TaskMediaMeta.estimated_assets` carries. Enough to be useful in a
#: warning, few enough that the response stays a fixed size.
ESTIMATED_ASSET_SAMPLE = 5


class UploadInit(StrictModel):
    filename: str = Field(min_length=1, max_length=500)
    size: int = Field(ge=0)


class UploadSessionOut(ORMModel):
    id: uuid.UUID
    task_id: uuid.UUID
    filename: str
    declared_size: int
    received_bytes: int
    completed: bool
