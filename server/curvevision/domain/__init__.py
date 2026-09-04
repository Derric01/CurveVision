"""The CurveVision domain model.

Importing this package registers every mapped class with ``Base.metadata``, which is what
Alembic autogenerate and ``create_all`` rely on.
"""

from curvevision.core.db import Base
from curvevision.domain.annotation import Shape, Tag, Track, TrackShape
from curvevision.domain.identity import (
    ApiToken,
    Organization,
    OrganizationMembership,
    RefreshToken,
    User,
)
from curvevision.domain.media import Asset, MediaBlob, MediaChunk, UploadSession
from curvevision.domain.project import AttributeDefinition, Label, Project
from curvevision.domain.review import AnnotationEvent, Comment, Issue, QualityReport
from curvevision.domain.system import (
    AuditEvent,
    BackgroundTask,
    DatasetVersion,
    ModelRegistration,
    Webhook,
    WebhookDelivery,
)
from curvevision.domain.task import Job, Task

__all__ = [
    "AnnotationEvent",
    "ApiToken",
    "Asset",
    "AttributeDefinition",
    "AuditEvent",
    "BackgroundTask",
    "Base",
    "Comment",
    "DatasetVersion",
    "Issue",
    "Job",
    "Label",
    "MediaBlob",
    "MediaChunk",
    "ModelRegistration",
    "Organization",
    "OrganizationMembership",
    "Project",
    "QualityReport",
    "RefreshToken",
    "Shape",
    "Tag",
    "Task",
    "Track",
    "TrackShape",
    "UploadSession",
    "User",
    "Webhook",
    "WebhookDelivery",
]
