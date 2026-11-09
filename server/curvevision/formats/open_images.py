"""Open Images: the CSV annotation files of Google's Open Images Dataset (V5 to V7).

Independent implementation written from the dataset's published file descriptions. No code
from Datumaro or the dataset's own tools is used; Datumaro reads this module's exports and
writes the files it imports in CI (`scripts/verify_open_images.py`), which is the
conformance check.

The layout, as the dataset ships it and Datumaro (and so CVAT) reads it::

    annotations/class-descriptions.csv                    LabelName,DisplayName — no header
    annotations/<subset>-images-with-rotation.csv         the images, one row each
    annotations/<subset>-annotations-human-imagelabels.csv  image-level labels (tags)
    annotations/<subset>-annotations-bbox.csv              boxes, normalised to 0..1
    annotations/<subset>-annotations-object-segmentation.csv  a row per mask
    annotations/images.meta                                 each image's height and width
    masks/<subset>/<image>_<label>_<box id>.png            one binary PNG per mask
    images/<subset>/<image id>.<ext>

**Boxes are normalised**, so the image's size is needed both ways: an export writes it to
``images.meta`` (which Datumaro reads when the images are not included) and an import takes
it from the task.

**Every segmented object is also a box**, as in the dataset: a mask, polygon or ellipse is
written as a mask *and* a box of the same extent, the mask row naming the box by its
coordinates. On import a box that a mask names is the same object, and arrives once, as the
mask, with the box's flags.

The dataset's box flags are ``IsOccluded`` — the shape's own occlusion — and
``IsTruncated``, ``IsGroupOf``, ``IsDepiction``, ``IsInside``, which travel as checkbox
attributes named ``truncated``, ``is_group_of``, ``is_depiction`` and ``is_inside`` (the
names Datumaro gives them); ``-1`` means not recorded. An image-level label with confidence
0 is the dataset saying the class is *absent*, so it is not a tag. Machine-generated
image-level labels are predictions and are not imported.
"""

from __future__ import annotations

import csv
import io
import math
import re
import shlex
from collections.abc import Iterable, Iterator
from dataclasses import dataclass, field
from typing import Any

from curvevision.core.logging import get_logger
from curvevision.domain.enums import AttributeType, ShapeType
from curvevision.formats.base import (
    AttributeSpec,
    DatasetView,
    ExportSink,
    FormatCapabilities,
    FrameRecord,
    ImportContext,
    ImportResult,
    ImportSource,
    LabelSpec,
    ShapeRecord,
    TagRecord,
    normalise_rectangle,
)
from curvevision.formats.raster import cover, mask_shape_from_image
from curvevision.formats.registry import register

logger = get_logger(__name__)

ANNOTATIONS = "annotations"
CLASS_DESCRIPTIONS = "class-descriptions.csv"
IMAGE_META = "images.meta"
IMAGES_SUFFIX = "-images-with-rotation.csv"
LABELS_SUFFIX = "-annotations-human-imagelabels.csv"
BOXES_SUFFIX = "-annotations-bbox.csv"
MASKS_SUFFIX = "-annotations-object-segmentation.csv"

IMAGE_FIELDS = (
    "ImageID",
    "Subset",
    "OriginalURL",
    "OriginalLandingURL",
    "License",
    "AuthorProfileURL",
    "Author",
    "Title",
    "OriginalSize",
    "OriginalMD5",
    "Thumbnail300KURL",
    "Rotation",
)
LABEL_FIELDS = ("ImageID", "Source", "LabelName", "Confidence")
BOX_FIELDS = (
    "ImageID",
    "Source",
    "LabelName",
    "Confidence",
    "XMin",
    "XMax",
    "YMin",
    "YMax",
    "IsOccluded",
    "IsTruncated",
    "IsGroupOf",
    "IsDepiction",
    "IsInside",
)
MASK_FIELDS = (
    "MaskPath",
    "ImageID",
    "LabelName",
    "BoxID",
    "BoxXMin",
    "BoxXMax",
    "BoxYMin",
    "BoxYMax",
    "PredictedIoU",
    "Clicks",
)

#: The dataset's box flags other than occlusion, and the attribute each travels as.
FLAGS = (
    ("IsTruncated", "truncated"),
    ("IsGroupOf", "is_group_of"),
    ("IsDepiction", "is_depiction"),
    ("IsInside", "is_inside"),
)

#: Shapes written as a mask and the box around it.
SEGMENTED = frozenset({ShapeType.MASK, ShapeType.POLYGON, ShapeType.ELLIPSE})

#: How close a mask's recorded box must be to a box to be the same object: the dataset
#: writes six decimal places.
SAME_BOX = 1e-6

_UNSAFE = re.compile(r"[^A-Za-z0-9_.-]+")


class OpenImagesFormat:
    id = "open_images"
    name = "Open Images (CSV)"
    version = "V7"
    extension = "zip"
    capabilities = FormatCapabilities(
        shape_types=(
            ShapeType.RECTANGLE,
            ShapeType.POLYGON,
            ShapeType.ELLIPSE,
            ShapeType.MASK,
        ),
        supports_import=True,
        supports_export=True,
        supports_tracks=False,
        supports_tags=True,
        supports_attributes=True,
        notes=(
            "Open Images' CSV files: boxes normalised to the image, image-level labels as "
            "tags, and masks as one binary PNG each. Polygons and ellipses are written as the "
            "masks they cover, so they come back as masks; every mask is also written as its "
            "box, as the dataset does, and arrives once. Occlusion is the box's IsOccluded; "
            "checkbox attributes named truncated, is_group_of, is_depiction and is_inside are "
            "its other flags. Other attributes, rotated boxes, lines, points, cuboids and "
            "skeletons have no place in the format. Masks need Pillow."
        ),
    )

    # ----------------------------------------------------------------------- export

    def export(self, dataset: DatasetView, sink: ExportSink) -> None:
        sink.write(
            f"{ANNOTATIONS}/{CLASS_DESCRIPTIONS}",
            _csv(
                ({"LabelName": label.name, "DisplayName": label.name} for label in dataset.labels),
                ("LabelName", "DisplayName"),
                header=False,
            ),
        )
        rows: dict[str, dict[str, list[dict[str, Any]]]] = {}
        sizes: list[tuple[str, int, int]] = []
        unsized: list[str] = []

        for frame in dataset:
            image_id = _image_id(frame.name)
            subset = frame.subset
            files = rows.setdefault(subset, {"images": [], "labels": [], "boxes": [], "masks": []})
            files["images"].append({"ImageID": image_id, "Subset": subset})
            if frame.has_media:
                extension = frame.name.rsplit(".", 1)[-1] if "." in frame.name else "jpg"
                sink.write_media(f"images/{subset}/{image_id}.{extension}", frame)
            for tag in _tags(frame):
                files["labels"].append(
                    {
                        "ImageID": image_id,
                        "Source": "verification" if tag.source != "model" else "machine",
                        "LabelName": tag.label,
                        "Confidence": _number(tag.confidence if tag.confidence is not None else 1),
                    }
                )
            width, height = frame.width or 0, frame.height or 0
            if width <= 0 or height <= 0:
                if frame.shapes:
                    unsized.append(frame.name)
                continue
            sizes.append((image_id, height, width))
            box_number = 0
            for shape in frame.shapes:
                if shape.shape_type is ShapeType.RECTANGLE:
                    box = normalise_rectangle(list(shape.points))
                    files["boxes"].append(_box_row(image_id, shape, box, width, height))
                    continue
                if shape.shape_type not in SEGMENTED:
                    continue
                drawn = cover(shape, width, height)
                if drawn is None:
                    continue
                png, box = drawn
                box_id = f"{box_number:08x}"
                box_number += 1
                mask_name = f"{_safe(image_id)}_{_safe(shape.label)}_{box_id}.png"
                sink.write(f"masks/{subset}/{mask_name}", png)
                normalised = _normalised(box, width, height)
                files["boxes"].append(_box_row(image_id, shape, box, width, height))
                files["masks"].append(
                    {
                        "MaskPath": mask_name,
                        "ImageID": image_id,
                        "LabelName": shape.label,
                        "BoxID": box_id,
                        "BoxXMin": normalised[0],
                        "BoxXMax": normalised[1],
                        "BoxYMin": normalised[2],
                        "BoxYMax": normalised[3],
                        "PredictedIoU": "",
                        "Clicks": "",
                    }
                )

        for subset, files in rows.items():
            sink.write(
                f"{ANNOTATIONS}/{subset}{IMAGES_SUFFIX}", _csv(files["images"], IMAGE_FIELDS)
            )
            for key, suffix, fields in (
                ("labels", LABELS_SUFFIX, LABEL_FIELDS),
                ("boxes", BOXES_SUFFIX, BOX_FIELDS),
                ("masks", MASKS_SUFFIX, MASK_FIELDS),
            ):
                if files[key]:
                    sink.write(f"{ANNOTATIONS}/{subset}{suffix}", _csv(files[key], fields))
        if sizes:
            sink.write(
                f"{ANNOTATIONS}/{IMAGE_META}",
                "# <image name> <height> <width>\n\n"
                + "".join(f"{shlex.quote(i)} {h} {w}\n" for i, h, w in sizes),
            )
        if unsized:
            logger.info(
                "open_images: shapes on frames of unknown size were left out",
                extra={"frames": unsized},
            )

    # ----------------------------------------------------------------------- import

    def import_(self, source: ImportSource, context: ImportContext) -> ImportResult:
        reader = _Reader(source, context)
        reader.read()
        return reader.result


# --------------------------------------------------------------------------- export


def _image_id(name: str) -> str:
    """``street/a.png`` -> ``street/a``: the frame's path, without its extension."""
    path = name.replace("\\", "/")
    folder, _, base = path.rpartition("/")
    stem = base.rsplit(".", 1)[0] if "." in base else base
    return f"{folder}/{stem}" if folder else stem


def _safe(text: str) -> str:
    return _UNSAFE.sub("-", text).strip("-") or "x"


def _tags(frame: FrameRecord) -> Iterable[TagRecord]:
    if frame.tag_records is not None:
        return frame.tag_records
    return [TagRecord(label) for label in frame.tags]


def _number(value: float) -> str:
    return f"{value:.6f}".rstrip("0").rstrip(".") if value != int(value) else str(int(value))


def _normalised(box: list[float], width: int, height: int) -> tuple[str, str, str, str]:
    """``(XMin, XMax, YMin, YMax)`` in 0..1, six places, as the dataset writes them."""
    x1, y1, x2, y2 = box
    return (
        f"{min(max(x1 / width, 0), 1):.6f}",
        f"{min(max(x2 / width, 0), 1):.6f}",
        f"{min(max(y1 / height, 0), 1):.6f}",
        f"{min(max(y2 / height, 0), 1):.6f}",
    )


def _box_row(
    image_id: str, shape: ShapeRecord, box: list[float], width: int, height: int
) -> dict[str, Any]:
    x_min, x_max, y_min, y_max = _normalised(box, width, height)
    row: dict[str, Any] = {
        "ImageID": image_id,
        "Source": "freeform",
        "LabelName": shape.label,
        "Confidence": _number(shape.confidence if shape.confidence is not None else 1),
        "XMin": x_min,
        "XMax": x_max,
        "YMin": y_min,
        "YMax": y_max,
        "IsOccluded": 1 if shape.occluded else 0,
    }
    for column, attribute in FLAGS:
        value = shape.attributes.get(attribute)
        row[column] = int(value) if isinstance(value, bool) else -1
    return row


def _csv(rows: Iterable[dict[str, Any]], fields: tuple[str, ...], *, header: bool = True) -> str:
    buffer = io.StringIO()
    writer = csv.DictWriter(buffer, fields, lineterminator="\n")
    if header:
        writer.writeheader()
    writer.writerows(rows)
    return buffer.getvalue()


# --------------------------------------------------------------------------- import


@dataclass
class _Box:
    frame: int
    shape: ShapeRecord
    normalised: tuple[float, float, float, float]
    merged: bool = False


@dataclass
class _Reader:
    source: ImportSource
    context: ImportContext
    result: ImportResult = field(default_factory=ImportResult)
    names: list[str] = field(default_factory=list)
    display: dict[str, str] = field(default_factory=dict)
    known: dict[str, str] = field(default_factory=dict)
    created: dict[str, set[str]] = field(default_factory=dict)
    refused: set[str] = field(default_factory=set)
    frames: dict[str, int] = field(default_factory=dict)
    stems: dict[str, int] = field(default_factory=dict)
    matched: set[int] = field(default_factory=set)
    unmatched: set[str] = field(default_factory=set)
    unsized: set[str] = field(default_factory=set)
    boxes: list[_Box] = field(default_factory=list)

    def read(self) -> None:
        self.names = self.source.names()
        self.known = dict(self.context.known_labels)
        for name, index in self.context.frame_by_name.items():
            stem = _image_id(name)
            self.frames.setdefault(stem, index)
            self.stems.setdefault(stem.rsplit("/", 1)[-1], index)
        self._read_classes()
        for name in self._files(("imagelabels.csv", "imagelabels-boxable.csv")):
            if "machine" in name.rsplit("/", 1)[-1]:
                self.result.warnings.append(
                    f"{name}: machine-generated image labels are predictions; not imported"
                )
                continue
            self._read_labels(name)
        for name in self._files(("-bbox.csv",)):
            self._read_boxes(name)
        for name in self._files(("-object-segmentation.csv",)):
            self._read_masks(name)
        self.result.shapes.extend((b.frame, b.shape) for b in self.boxes if not b.merged)
        self.result.frames_matched = len(self.matched)
        if self.unmatched:
            listed = ", ".join(sorted(self.unmatched)[:5])
            more = f" and {len(self.unmatched) - 5} more" if len(self.unmatched) > 5 else ""
            self.result.warnings.append(
                f"annotations for {len(self.unmatched)} image(s) not in this task were skipped: "
                f"{listed}{more}"
            )
        if self.unsized:
            self.result.warnings.append(
                f"boxes and masks of {len(self.unsized)} image(s) of unknown size were skipped: "
                + ", ".join(sorted(self.unsized)[:5])
            )
        self.result.new_labels = [
            LabelSpec(
                id=label,
                name=label,
                attributes=tuple(
                    AttributeSpec(name=a, attribute_type=AttributeType.CHECKBOX)
                    for a in sorted(attributes)
                ),
            )
            for label, attributes in self.created.items()
        ]

    # -- helpers

    def _files(self, endings: tuple[str, ...]) -> list[str]:
        return sorted(n for n in self.names if n.lower().endswith(endings))

    def _rows(self, name: str) -> Iterator[dict[str, str]]:
        try:
            text = self.source.read(name).decode("utf-8-sig")
        except UnicodeDecodeError:
            self.result.warnings.append(f"{name}: not UTF-8 text; skipped")
            return
        yield from csv.DictReader(io.StringIO(text))

    def _read_classes(self) -> None:
        for name in self._files(("class-descriptions.csv", "class-descriptions-boxable.csv")):
            try:
                text = self.source.read(name).decode("utf-8-sig")
            except UnicodeDecodeError:
                self.result.warnings.append(f"{name}: not UTF-8 text; skipped")
                continue
            for row in csv.reader(io.StringIO(text)):
                if len(row) >= 2 and row[:2] != ["LabelName", "DisplayName"]:
                    self.display.setdefault(row[0], row[1] or row[0])

    def _frame(self, image_id: str) -> int | None:
        frame = self.frames.get(image_id)
        if frame is None:
            frame = self.stems.get(image_id.rsplit("/", 1)[-1])
        if frame is None:
            self.unmatched.add(image_id)
            return None
        self.matched.add(frame)
        return frame

    def _label(self, label_name: str, where: str) -> str | None:
        name = self.display.get(label_name, label_name)
        if name in self.known:
            return name
        if not self.context.create_missing_labels:
            if name not in self.refused:
                self.result.warnings.append(f"{where}: unknown label {name!r}; skipped")
                self.refused.add(name)
            return None
        self.known[name] = name
        self.created[name] = set()
        return name

    def _size(self, frame: int, image_id: str) -> tuple[int, int] | None:
        size = self.context.frame_sizes.get(frame)
        if not size or size[0] <= 0 or size[1] <= 0:
            self.unsized.add(image_id)
            return None
        return size

    # -- the files

    def _read_labels(self, name: str) -> None:
        for number, row in enumerate(self._rows(name), 2):
            frame = self._frame(row.get("ImageID") or "")
            if frame is None:
                continue
            try:
                confidence = float(row.get("Confidence") or 1)
            except ValueError:
                self.result.warnings.append(f"{name}: line {number}: confidence is not a number")
                continue
            if confidence <= 0:
                continue  # the class was verified absent
            label = self._label(row.get("LabelName") or "", f"{name}: line {number}")
            if label is not None:
                self.result.tags.append((frame, label))

    def _read_boxes(self, name: str) -> None:
        for number, row in enumerate(self._rows(name), 2):
            where = f"{name}: line {number}"
            image_id = row.get("ImageID") or ""
            frame = self._frame(image_id)
            if frame is None:
                continue
            size = self._size(frame, image_id)
            if size is None:
                continue
            coords = _coords(row, ("XMin", "XMax", "YMin", "YMax"))
            if coords is None:
                self.result.warnings.append(f"{where}: box coordinates are not numbers; skipped")
                continue
            label = self._label(row.get("LabelName") or "", where)
            if label is None:
                continue
            width, height = size
            x_min, x_max, y_min, y_max = coords
            attributes: dict[str, Any] = {}
            for column, attribute in FLAGS:
                if (row.get(column) or "").strip() in ("0", "1"):
                    attributes[attribute] = row[column].strip() == "1"
            if label in self.created:
                self.created[label].update(attributes)
            shape = ShapeRecord(
                label=label,
                shape_type=ShapeType.RECTANGLE,
                points=normalise_rectangle(
                    [x_min * width, y_min * height, x_max * width, y_max * height]
                ),
                occluded=(row.get("IsOccluded") or "").strip() == "1",
                attributes=attributes,
            )
            self.boxes.append(_Box(frame, shape, coords))

    def _read_masks(self, name: str) -> None:
        try:
            from PIL import Image
        except ImportError:
            self.result.warnings.append(f"{name}: masks need Pillow (the media extra); skipped")
            return
        by_base: dict[str, list[str]] = {}
        for path in self.names:
            by_base.setdefault(path.rsplit("/", 1)[-1], []).append(path)
        for number, row in enumerate(self._rows(name), 2):
            where = f"{name}: line {number}"
            image_id = row.get("ImageID") or ""
            frame = self._frame(image_id)
            if frame is None:
                continue
            size = self._size(frame, image_id)
            if size is None:
                continue
            mask_name = (row.get("MaskPath") or "").replace("\\", "/").rsplit("/", 1)[-1]
            candidates = by_base.get(mask_name, [])
            if not mask_name or not candidates:
                self.result.warnings.append(f"{where}: mask {mask_name!r} is not in the archive")
                continue
            label = self._label(row.get("LabelName") or "", where)
            if label is None:
                continue
            try:
                with Image.open(io.BytesIO(self.source.read(sorted(candidates)[0]))) as raw:
                    image = raw.convert("L")
            except OSError:
                self.result.warnings.append(f"{where}: {mask_name} is not a readable image")
                continue
            if image.size != size:
                # The dataset's masks are not at the image's resolution; scaled as Datumaro
                # scales them, to the nearest pixel.
                image = image.resize(size, Image.Resampling.NEAREST)
            shape = mask_shape_from_image(image, label)
            if shape is None:
                self.result.warnings.append(f"{where}: {mask_name} covers no pixels; skipped")
                continue
            coords = _coords(row, ("BoxXMin", "BoxXMax", "BoxYMin", "BoxYMax"))
            if coords is not None:
                box = next(
                    (
                        b
                        for b in self.boxes
                        if b.frame == frame
                        and not b.merged
                        and b.shape.label == label
                        and all(
                            abs(a - c) <= SAME_BOX
                            for a, c in zip(b.normalised, coords, strict=True)
                        )
                    ),
                    None,
                )
                if box is not None:
                    box.merged = True
                    shape.occluded = box.shape.occluded
                    shape.attributes = dict(box.shape.attributes)
            self.result.shapes.append((frame, shape))


def _coords(
    row: dict[str, str], columns: tuple[str, ...]
) -> tuple[float, float, float, float] | None:
    try:
        values = tuple(float(row[column]) for column in columns)
    except (KeyError, TypeError, ValueError):
        return None
    if not all(math.isfinite(v) for v in values):
        return None
    return values[0], values[1], values[2], values[3]


register(OpenImagesFormat())


__all__ = ["OpenImagesFormat"]
