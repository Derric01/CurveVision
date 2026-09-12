"""The dataset format plugin contract.

Two properties drive this design:

**Streaming.** ``DatasetView`` is an *iterator* over frames, not a materialised list. A
server exporting a 500k-image dataset must never hold it in memory. This is the main reason
CurveVision implements its own registry rather than delegating to Datumaro, which is
excellent but loads datasets into memory by design (see
docs/adr/0004-streaming-format-registry.md).

**Honest capabilities.** Every format declares what survives a round trip, so the API can
warn *before* an export silently drops polylines that COCO cannot represent.

Formats deal in plain dataclasses and never import the ORM, which keeps them unit-testable
without a database.
"""

from __future__ import annotations

from collections.abc import Iterable, Iterator
from dataclasses import dataclass, field
from typing import Any, Protocol, runtime_checkable

from curvevision.domain.enums import AttributeType, ShapeType


@dataclass(frozen=True, slots=True)
class AttributeSpec:
    name: str
    attribute_type: AttributeType
    values: tuple[str, ...] = ()
    mutable: bool = False


@dataclass(frozen=True, slots=True)
class LabelSpec:
    id: str
    name: str
    color: str = "#38bdf8"
    attributes: tuple[AttributeSpec, ...] = ()
    #: Keypoint names for skeleton labels, in the order elements are stored.
    keypoints: tuple[str, ...] = ()
    skeleton_edges: tuple[tuple[int, int], ...] = ()


@dataclass(slots=True)
class ShapeRecord:
    """One annotation, flattened for export.

    Tracks are materialised into per-frame shapes before reaching a format, with
    ``track_id`` preserved so formats that support object identity can use it.
    """

    label: str
    shape_type: ShapeType
    points: list[float]
    rotation: float = 0.0
    occluded: bool = False
    z_order: int = 0
    group: int | None = None
    track_id: int | None = None
    attributes: dict[str, Any] = field(default_factory=dict)
    source: str = "manual"
    confidence: float | None = None
    mask: dict[str, Any] | None = None
    elements: list[dict[str, Any]] = field(default_factory=list)


@dataclass(slots=True)
class FrameRecord:
    index: int
    name: str
    width: int | None = None
    height: int | None = None
    shapes: list[ShapeRecord] = field(default_factory=list)
    tags: list[str] = field(default_factory=list)
    #: Bytes of the frame's media, present only when the export requested images.
    media: bytes | None = None
    subset: str = "default"


@dataclass(slots=True)
class DatasetView:
    """A read-only, streaming projection of a dataset."""

    name: str
    labels: list[LabelSpec]
    frames: Iterable[FrameRecord]
    metadata: dict[str, Any] = field(default_factory=dict)

    def __iter__(self) -> Iterator[FrameRecord]:
        return iter(self.frames)


@dataclass(frozen=True, slots=True)
class FormatCapabilities:
    shape_types: tuple[ShapeType, ...]
    supports_import: bool = True
    supports_export: bool = True
    supports_tracks: bool = False
    supports_tags: bool = False
    supports_attributes: bool = False
    supports_images: bool = True
    notes: str | None = None

    def unsupported(self, shape_types: Iterable[ShapeType]) -> list[ShapeType]:
        """Which of ``shape_types`` this format would drop."""
        return sorted(
            {shape for shape in shape_types if shape not in self.shape_types},
            key=lambda shape: shape.value,
        )


class ExportSink(Protocol):
    """Where a format writes its output files."""

    def write(self, path: str, data: bytes | str) -> None: ...


class ImportSource(Protocol):
    """Where a format reads its input files."""

    def names(self) -> list[str]:
        """Every file path in the source, using forward slashes."""

    def read(self, path: str) -> bytes: ...

    def exists(self, path: str) -> bool: ...


@dataclass(slots=True)
class ImportContext:
    """What an importer needs to resolve names against the target project."""

    #: Existing project labels, by name.
    known_labels: dict[str, str]
    #: Frame index by media filename, so an importer can attach annotations to the right
    #: frame without guessing from ordering.
    frame_by_name: dict[str, int]
    frame_count: int
    #: ``(width, height)`` per frame index. Formats that store normalised coordinates
    #: (YOLO) need this to recover pixels; a missing entry means the dimensions are
    #: unknown and the annotation must be skipped rather than guessed.
    frame_sizes: dict[int, tuple[int, int]] = field(default_factory=dict)
    create_missing_labels: bool = True


@dataclass(slots=True)
class ImportResult:
    """What an importer produced. Plain data; the service turns it into rows."""

    shapes: list[tuple[int, ShapeRecord]] = field(default_factory=list)
    tags: list[tuple[int, str]] = field(default_factory=list)
    new_labels: list[LabelSpec] = field(default_factory=list)
    #: Human-readable notes: files skipped, labels invented, annotations dropped.
    warnings: list[str] = field(default_factory=list)
    frames_matched: int = 0


@runtime_checkable
class DatasetFormat(Protocol):
    id: str
    name: str
    version: str
    extension: str
    capabilities: FormatCapabilities

    def export(self, dataset: DatasetView, sink: ExportSink) -> None: ...

    def import_(self, source: ImportSource, context: ImportContext) -> ImportResult: ...


# ------------------------------------------------------------------- geometry helpers


def bounding_box(points: list[float]) -> tuple[float, float, float, float]:
    """``(x_min, y_min, width, height)`` for a flat coordinate list."""
    if not points:
        return (0.0, 0.0, 0.0, 0.0)
    xs = points[0::2]
    ys = points[1::2]
    x_min, x_max = min(xs), max(xs)
    y_min, y_max = min(ys), max(ys)
    return (x_min, y_min, x_max - x_min, y_max - y_min)


def polygon_area(points: list[float]) -> float:
    """Shoelace area of a closed polygon."""
    pairs = list(zip(points[0::2], points[1::2], strict=False))
    if len(pairs) < 3:
        return 0.0
    total = 0.0
    for i in range(len(pairs)):
        x1, y1 = pairs[i]
        x2, y2 = pairs[(i + 1) % len(pairs)]
        total += x1 * y2 - x2 * y1
    return abs(total) / 2.0


def rectangle_to_polygon(points: list[float]) -> list[float]:
    """``[x1,y1,x2,y2]`` to a four-vertex polygon, for formats without a box primitive."""
    x1, y1, x2, y2 = points[:4]
    return [x1, y1, x2, y1, x2, y2, x1, y2]


def normalise_rectangle(points: list[float]) -> list[float]:
    """Order a rectangle's corners so x1<=x2 and y1<=y2.

    Dragging a box up and to the left produces a "negative" rectangle; every consumer
    downstream assumes the normalised form.
    """
    x1, y1, x2, y2 = points[:4]
    return [min(x1, x2), min(y1, y2), max(x1, x2), max(y1, y2)]
