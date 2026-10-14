"""The long tail of dataset formats, through Datumaro when it is installed.

This project writes and reads fifteen formats itself, each held in CI to a reader written by
other people. Beyond them are dozens of single-dataset conventions — ImageNet's folders,
MNIST, WIDER FACE, DOTA's rotated boxes, Segment Anything's masks, LabelMe 3.0's XML — that
matter to whoever needs one and to nobody else. Datumaro (MIT, from Intel and the CVAT team)
already reads and writes them, so rather than write each, this bridges to it: a bridged
format turns a `DatasetView` into a Datumaro dataset in memory and has Datumaro write it,
and turns what Datumaro reads back into an `ImportResult`.

Datumaro is optional — ``pip install 'curvevision[datumaro]'`` — and heavy, so it is not in
the desktop application or the default server image. Without it no bridged format is
registered, and nothing here is imported.

**What each bridged format can carry is measured, not declared.**
`scripts/datumaro_bridge_probe.py` round-trips every shape type, a tag and an attribute
through each format — export through this bridge, import back through it — and records what
survives, and as what, in ``datumaro_formats.json``, which this module reads. CI re-measures
and fails when Datumaro's behaviour moves (`.github/workflows/datumaro.yml`).
"""

from __future__ import annotations

import importlib.util
import json
import tempfile
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Any

from curvevision.core.logging import get_logger
from curvevision.domain.enums import AttributeType, ShapeType
from curvevision.formats.base import (
    AttributeSpec,
    DatasetView,
    ExportSink,
    FormatCapabilities,
    ImportContext,
    ImportResult,
    ImportSource,
    LabelSpec,
    ShapeRecord,
    normalise_rectangle,
)
from curvevision.formats.registry import register
from curvevision.formats.rle import pixels as mask_pixels

logger = get_logger(__name__)

#: What the probe measured, per Datumaro format.
TABLE = Path(__file__).with_name("datumaro_formats.json")

#: The prefix that marks a bridged format's id — ``datumaro-imagenet`` — and keeps it a valid
#: file name, since an export's download is named after its format.
PREFIX = "datumaro-"


@dataclass(frozen=True)
class Bridged:
    """One Datumaro format, as the probe found it."""

    datumaro_id: str
    name: str
    shape_types: tuple[ShapeType, ...]
    tags: bool
    attributes: bool
    #: Shapes that come back as another type, by type: ``{"ellipse": "polygon"}``.
    becomes: dict[str, str]
    notes: str


def load_table() -> list[Bridged]:
    if not TABLE.exists():
        return []
    rows = json.loads(TABLE.read_text())
    return [
        Bridged(
            datumaro_id=row["datumaro_id"],
            name=row["name"],
            shape_types=tuple(ShapeType(value) for value in row["shape_types"]),
            tags=bool(row["tags"]),
            attributes=bool(row["attributes"]),
            becomes=dict(row.get("becomes", {})),
            notes=row.get("notes", ""),
        )
        for row in rows
    ]


def available() -> bool:
    """Whether Datumaro is installed — found without importing it, which is slow."""
    return importlib.util.find_spec("datumaro") is not None


class DatumaroFormat:
    """A Datumaro format, through the bridge."""

    extension = "zip"
    version = "datumaro"

    def __init__(self, bridged: Bridged) -> None:
        self.bridged = bridged
        self.id = PREFIX + bridged.datumaro_id
        self.name = f"{bridged.name} (via Datumaro)"
        becomes = "; ".join(f"{a} comes back as {b}" for a, b in sorted(bridged.becomes.items()))
        self.capabilities = FormatCapabilities(
            shape_types=bridged.shape_types,
            supports_import=True,
            supports_export=True,
            supports_tracks=False,
            supports_tags=bridged.tags,
            supports_attributes=bridged.attributes,
            notes=" ".join(
                part
                for part in (
                    f"Written and read by Datumaro's {bridged.datumaro_id!r} format; what it "
                    "carries was measured by round-tripping every shape type through it.",
                    f"{becomes}." if becomes else "",
                    bridged.notes,
                )
                if part
            ),
        )

    # ----------------------------------------------------------------------- export

    def export(self, dataset: DatasetView, sink: ExportSink) -> None:
        from datumaro.components.dataset import Dataset

        with tempfile.TemporaryDirectory(prefix="curvevision-datumaro-") as workdir:
            media, out = Path(workdir) / "media", Path(workdir) / "out"
            items, categories, with_media = to_datumaro(dataset, media)
            converted = Dataset.from_iterable(items, categories=categories)
            converted.export(str(out), self.bridged.datumaro_id, save_media=with_media)
            for path in sorted(out.rglob("*")):
                if path.is_file():
                    sink.write(path.relative_to(out).as_posix(), path.read_bytes())

    # ----------------------------------------------------------------------- import

    def import_(self, source: ImportSource, context: ImportContext) -> ImportResult:
        from datumaro.components.dataset import Dataset

        result = ImportResult()
        with tempfile.TemporaryDirectory(prefix="curvevision-datumaro-") as workdir:
            root = Path(workdir)
            for name in source.names():
                relative = _safe_relative(name)
                if relative is None:
                    result.warnings.append(f"{name}: not a path inside the archive; skipped")
                    continue
                target = root / relative
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_bytes(source.read(name))
            try:
                dataset = Dataset.import_from(workdir, self.bridged.datumaro_id)
                items = list(dataset)
                categories = dataset.categories()
            except Exception as exc:  # Datumaro raises its own errors for a malformed dataset
                result.warnings.append(
                    f"Datumaro could not read this as {self.bridged.datumaro_id!r}: {exc}"
                )
                return result
            from_datumaro(items, categories, context, result)
        return result


def _safe_relative(name: str) -> PurePosixPath | None:
    path = PurePosixPath(name.replace("\\", "/"))
    if path.is_absolute() or ".." in path.parts or not path.parts:
        return None
    return path


# --------------------------------------------------------------------------- to Datumaro


def to_datumaro(dataset: DatasetView, media_dir: Path) -> tuple[list[Any], dict[Any, Any], bool]:
    """Datumaro items and categories for a dataset, and whether any frame has media.

    Datumaro holds a dataset in memory, which is why ADR 0004 did not build on it. What is
    held here is the annotations only: each image is written to ``media_dir`` as it streams
    past and read back by Datumaro when it writes it, and each mask is drawn when Datumaro
    asks for it.
    """
    import numpy as np
    from datumaro.components.annotation import (
        AnnotationType,
        Bbox,
        Ellipse,
        Label,
        LabelCategories,
        Mask,
        Points,
        Polygon,
        PolyLine,
        RotatedBbox,
    )
    from datumaro.components.dataset_base import DatasetItem
    from datumaro.components.media import Image

    labels = LabelCategories()
    index: dict[str, int] = {}
    for label in dataset.labels:
        index[label.name] = labels.add(label.name, attributes={a.name for a in label.attributes})
    items: list[Any] = []
    with_media = False
    for frame in dataset:
        stem = frame.name.replace("\\", "/").rsplit(".", 1)[0]
        size = (frame.height, frame.width) if frame.width and frame.height else None
        if frame.media is not None:
            with_media = True
            extension = "." + frame.name.rsplit(".", 1)[-1] if "." in frame.name else ".png"
            stored = media_dir / f"{len(items)}{extension}"
            stored.parent.mkdir(parents=True, exist_ok=True)
            stored.write_bytes(frame.media)
            media = Image.from_file(path=str(stored), size=size)
        else:
            media = Image.from_file(path=frame.name, size=size)
        annotations: list[Any] = [Label(index[tag]) for tag in frame.tags if tag in index]
        for shape in frame.shapes:
            label_index = index.get(shape.label)
            if label_index is None:
                continue
            common: dict[str, Any] = {
                "label": label_index,
                "z_order": shape.z_order,
                "group": shape.group or 0,
                "id": shape.track_id or 0,
                "attributes": {**shape.attributes, "occluded": shape.occluded},
            }
            points = [float(v) for v in shape.points]
            kind = shape.shape_type
            if kind is ShapeType.RECTANGLE:
                x1, y1, x2, y2 = normalise_rectangle(points)
                annotations.append(Bbox(x1, y1, x2 - x1, y2 - y1, **common))
            elif kind is ShapeType.ROTATED_RECTANGLE:
                x1, y1, x2, y2 = normalise_rectangle(points)
                annotations.append(
                    RotatedBbox(
                        (x1 + x2) / 2, (y1 + y2) / 2, x2 - x1, y2 - y1, shape.rotation, **common
                    )
                )
            elif kind is ShapeType.POLYGON:
                annotations.append(Polygon(points, **common))
            elif kind is ShapeType.POLYLINE:
                annotations.append(PolyLine(points, **common))
            elif kind is ShapeType.POINTS:
                annotations.append(Points(points, **common))
            elif kind is ShapeType.ELLIPSE:
                cx, cy, rx, ry = [*points, 0, 0, 0, 0][:4]
                annotations.append(Ellipse(cx - rx, cy - ry, cx + rx, cy + ry, **common))
            elif kind is ShapeType.MASK and shape.mask and size:
                annotations.append(Mask(image=_drawn(shape.mask, size, np), **common))
        items.append(
            DatasetItem(id=stem, subset=frame.subset, media=media, annotations=annotations)
        )
    return items, {AnnotationType.label: labels}, with_media


def _drawn(mask: dict[str, Any], size: tuple[int, int], np: Any) -> Any:
    """A full-image mask, drawn only when Datumaro asks for it."""

    def draw() -> Any:
        image = np.zeros(size, dtype=bool)
        for x, y in mask_pixels(mask):
            if 0 <= y < size[0] and 0 <= x < size[1]:
                image[y, x] = True
        return image

    return draw


# --------------------------------------------------------------------------- from Datumaro


def from_datumaro(
    items: list[Any], categories: dict[Any, Any], context: ImportContext, result: ImportResult
) -> None:
    """Fill ``result`` from Datumaro items, matched to the task's frames by name."""
    import numpy as np
    from datumaro.components.annotation import AnnotationType

    names = categories.get(AnnotationType.label)
    label_names = [entry.name for entry in names.items] if names is not None else []
    frames: dict[str, int] = {}
    stems: dict[str, int] = {}
    for name, index in context.frame_by_name.items():
        stem = name.replace("\\", "/").rsplit(".", 1)[0]
        frames.setdefault(stem, index)
        stems.setdefault(stem.rsplit("/", 1)[-1], index)
    known = dict(context.known_labels)
    created: dict[str, dict[str, AttributeType]] = {}
    refused: set[str] = set()
    unmatched: list[str] = []
    skipped: dict[str, int] = {}
    matched: set[int] = set()

    def label_for(index: Any) -> str | None:
        if index is None or not 0 <= int(index) < len(label_names):
            return None
        name = label_names[int(index)]
        if name in known:
            return name
        if not context.create_missing_labels:
            if name not in refused:
                result.warnings.append(f"unknown label {name!r}; skipped")
                refused.add(name)
            return None
        known[name] = name
        created[name] = {}
        return name

    for item in items:
        item_id = str(item.id).replace("\\", "/")
        frame = frames.get(item_id)
        if frame is None:
            frame = stems.get(item_id.rsplit("/", 1)[-1])
        if frame is None:
            # Some formats name an item by more than its image — ImageNet's are
            # ``label:name`` — so the image file's own name is tried too.
            path = str(getattr(item.media, "path", "") or "").replace("\\", "/")
            stem = path.rsplit("/", 1)[-1].rsplit(".", 1)[0]
            frame = stems.get(stem) if stem else None
        if frame is None:
            unmatched.append(item_id)
            continue
        matched.add(frame)
        for annotation in item.annotations:
            kind = annotation.type
            if kind is AnnotationType.label:
                label = label_for(annotation.label)
                if label is not None:
                    result.tags.append((frame, label))
                continue
            label = label_for(getattr(annotation, "label", None))
            if label is None:
                if getattr(annotation, "label", None) is None:
                    skipped[kind.name] = skipped.get(kind.name, 0) + 1
                continue
            shape = _shape(annotation, label, np)
            if shape is None:
                skipped[kind.name] = skipped.get(kind.name, 0) + 1
                continue
            if label in created:
                for attribute, value in shape.attributes.items():
                    created[label].setdefault(attribute, _attribute_type(value))
            result.shapes.append((frame, shape))

    result.frames_matched = len(matched)
    if unmatched:
        more = f" and {len(unmatched) - 5} more" if len(unmatched) > 5 else ""
        result.warnings.append(
            f"{len(unmatched)} item(s) not in this task were skipped: "
            + ", ".join(unmatched[:5])
            + more
        )
    for kind_name, count in sorted(skipped.items()):
        result.warnings.append(f"{count} {kind_name} annotation(s) have no CurveVision form")
    result.new_labels = [
        LabelSpec(
            id=label,
            name=label,
            attributes=tuple(
                AttributeSpec(name=name, attribute_type=kind)
                for name, kind in sorted(attributes.items())
            ),
        )
        for label, attributes in created.items()
    ]


def _attribute_type(value: Any) -> AttributeType:
    if isinstance(value, bool):
        return AttributeType.CHECKBOX
    if isinstance(value, int | float):
        return AttributeType.NUMBER
    return AttributeType.TEXT


def _shape(annotation: Any, label: str, np: Any) -> ShapeRecord | None:
    from datumaro.components.annotation import AnnotationType

    from curvevision.formats.rle import encode as rle_encode

    attributes = {
        str(key): value
        for key, value in dict(annotation.attributes).items()
        if key != "occluded" and isinstance(value, bool | int | float | str)
    }
    common: dict[str, Any] = {
        "label": label,
        "occluded": bool(annotation.attributes.get("occluded", False)),
        "z_order": int(getattr(annotation, "z_order", 0) or 0),
        "group": int(annotation.group) or None,
        "attributes": attributes,
    }
    kind = annotation.type
    if kind is AnnotationType.bbox:
        x, y, w, h = (float(v) for v in annotation.get_bbox())
        return ShapeRecord(shape_type=ShapeType.RECTANGLE, points=[x, y, x + w, y + h], **common)
    if kind is AnnotationType.rotated_bbox:
        cx, cy, w, h = (
            float(v) for v in (annotation.cx, annotation.cy, annotation.w, annotation.h)
        )
        return ShapeRecord(
            shape_type=ShapeType.ROTATED_RECTANGLE,
            points=[cx - w / 2, cy - h / 2, cx + w / 2, cy + h / 2],
            rotation=float(annotation.r),
            **common,
        )
    if kind is AnnotationType.polygon:
        return ShapeRecord(
            shape_type=ShapeType.POLYGON, points=[float(v) for v in annotation.points], **common
        )
    if kind is AnnotationType.polyline:
        return ShapeRecord(
            shape_type=ShapeType.POLYLINE, points=[float(v) for v in annotation.points], **common
        )
    if kind is AnnotationType.points:
        return ShapeRecord(
            shape_type=ShapeType.POINTS, points=[float(v) for v in annotation.points], **common
        )
    if kind is AnnotationType.ellipse:
        x1, y1, x2, y2 = (float(v) for v in annotation.points)
        return ShapeRecord(
            shape_type=ShapeType.ELLIPSE,
            points=[(x1 + x2) / 2, (y1 + y2) / 2, abs(x2 - x1) / 2, abs(y2 - y1) / 2],
            **common,
        )
    if kind is AnnotationType.mask:
        image = np.asarray(annotation.image).astype(bool)
        if image.ndim != 2 or not image.any():
            return None
        rows, columns = np.nonzero(image)
        top, bottom = int(rows.min()), int(rows.max())
        left, right = int(columns.min()), int(columns.max())
        crop = image[top : bottom + 1, left : right + 1]
        width, height = right - left + 1, bottom - top + 1
        return ShapeRecord(
            shape_type=ShapeType.MASK,
            points=[left, top, right, bottom],
            mask={
                "rle": rle_encode([bool(v) for v in crop.ravel()], width, height),
                "left": left,
                "top": top,
                "width": width,
                "height": height,
            },
            **common,
        )
    return None


# --------------------------------------------------------------------------- registration


def register_available() -> list[DatumaroFormat]:
    """Register every measured format, when Datumaro is installed; nothing otherwise."""
    if not available():
        return []
    registered = []
    for bridged in load_table():
        fmt = DatumaroFormat(bridged)
        register(fmt)
        registered.append(fmt)
    return registered


__all__ = ["PREFIX", "DatumaroFormat", "available", "load_table", "register_available"]
