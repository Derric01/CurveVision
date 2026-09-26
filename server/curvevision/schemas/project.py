"""Projects and the label schema."""

from __future__ import annotations

import uuid
from datetime import datetime

from pydantic import Field, field_validator

from curvevision.domain.enums import AttributeType, ShapeType
from curvevision.schemas.common import ORMModel, Slug, StrictModel
from curvevision.schemas.identity import UserBrief

HEX_COLOR = r"^#(?:[0-9a-fA-F]{3}|[0-9a-fA-F]{6}|[0-9a-fA-F]{8})$"


class AttributeIn(StrictModel):
    id: uuid.UUID | None = None
    name: str = Field(min_length=1, max_length=120)
    attribute_type: AttributeType
    values: list[str] = Field(default_factory=list)
    default_value: str | None = None
    mutable: bool = False
    required: bool = False
    position: int = 0

    @field_validator("values")
    @classmethod
    def _choices_required(cls, values: list[str]) -> list[str]:
        return values

    def model_post_init(self, _context: object) -> None:
        needs_choices = self.attribute_type in (
            AttributeType.SELECT,
            AttributeType.RADIO,
        )
        if needs_choices and not self.values:
            raise ValueError(f"{self.attribute_type} attributes require at least one value")
        if self.default_value is not None:
            self._check_default(self.default_value, needs_choices=needs_choices)

    def _check_default(self, default: str, *, needs_choices: bool) -> None:
        """Refuse a default the attribute would itself refuse as a value.

        `validate_attributes` writes the default into every annotation drawn without a
        value, and does not check it on the way — so an invalid one used to be recorded
        silently and then refused on that annotation's next save.
        """
        if needs_choices and default not in self.values:
            raise ValueError(
                f"The default of {self.name!r} must be one of: {', '.join(self.values)}"
            )
        if self.attribute_type is AttributeType.CHECKBOX and default.lower() not in (
            "true",
            "false",
        ):
            raise ValueError(f"The default of {self.name!r} must be true or false")
        if self.attribute_type is AttributeType.NUMBER:
            try:
                float(default)
            except ValueError as exc:
                raise ValueError(f"The default of {self.name!r} must be a number") from exc


class AttributeOut(ORMModel):
    id: uuid.UUID
    name: str
    attribute_type: AttributeType
    values: list[str]
    default_value: str | None = None
    mutable: bool
    required: bool
    position: int


class LabelIn(StrictModel):
    id: uuid.UUID | None = None
    name: str = Field(min_length=1, max_length=120)
    color: str = Field(default="#38bdf8", pattern=HEX_COLOR)
    position: int = 0
    allowed_shape_types: list[ShapeType] = Field(default_factory=list)
    skeleton_edges: list[list[int]] = Field(default_factory=list)
    attributes: list[AttributeIn] = Field(default_factory=list)
    #: Keypoint sub-labels, for skeleton labels.
    children: list[LabelIn] = Field(default_factory=list)

    def model_post_init(self, _context: object) -> None:
        # Values are stored on annotations keyed by attribute name, so two attributes of one
        # name would share one value -- and the table's unique constraint would turn the
        # write into a 500 rather than saying why.
        names = [attribute.name for attribute in self.attributes]
        duplicates = sorted({name for name in names if names.count(name) > 1})
        if duplicates:
            raise ValueError(f"A label cannot have two attributes named {', '.join(duplicates)}")


class LabelOut(ORMModel):
    id: uuid.UUID
    project_id: uuid.UUID
    parent_id: uuid.UUID | None = None
    name: str
    color: str
    position: int
    allowed_shape_types: list[str]
    skeleton_edges: list[list[int]]
    attributes: list[AttributeOut] = Field(default_factory=list)
    children: list[LabelOut] = Field(default_factory=list)


class ProjectCreate(StrictModel):
    organization_id: uuid.UUID
    slug: Slug = Field(min_length=2, max_length=64)
    name: str = Field(min_length=1, max_length=200)
    description: str | None = None
    open_assignment: bool = True
    labels: list[LabelIn] = Field(default_factory=list)


class ProjectUpdate(StrictModel):
    name: str | None = Field(default=None, min_length=1, max_length=200)
    description: str | None = None
    open_assignment: bool | None = None
    owner_id: uuid.UUID | None = None


class ProjectOut(ORMModel):
    id: uuid.UUID
    organization_id: uuid.UUID
    slug: str
    name: str
    description: str | None = None
    open_assignment: bool
    created_at: datetime
    updated_at: datetime
    owner: UserBrief | None = None


class ProjectDetail(ProjectOut):
    labels: list[LabelOut] = Field(default_factory=list)
    task_count: int = 0


class ProjectStatistics(StrictModel):
    project_id: uuid.UUID
    task_count: int
    job_count: int
    frame_count: int
    shape_count: int
    track_count: int
    tag_count: int
    #: Annotation count per label name -- the class-distribution check every dataset needs
    #: before anyone trains on it.
    label_distribution: dict[str, int]
    jobs_by_state: dict[str, int]
    frames_without_annotations: int


LabelIn.model_rebuild()
LabelOut.model_rebuild()
