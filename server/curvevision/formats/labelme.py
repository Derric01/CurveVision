"""LabelMe JSON: the per-image files of the `labelme` annotation tool.

Independent implementation written from the file format: one JSON document beside each
image, naming it in ``imagePath`` (relative to the JSON file) and holding its ``shapes`` in
pixel coordinates. No code from labelme (GPL-3.0) is used; its own loader reads this
module's exports in CI (`scripts/verify_labelme.py`), which is the conformance check.

Not to be confused with *LabelMe 3.0*, the XML of the original MIT CSAIL tool, which CVAT
exports under that name — a different format this module does not read.

How the two vocabularies meet, shape by shape::

    labelme              CurveVision
    rectangle            rectangle             two corners
    oriented_rectangle   rotated rectangle     four real corners, read back as a rotated box
                                               when they are one and as a polygon when not
    polygon              polygon
    linestrip / line     polyline              a line is a polyline of two points
    circle               ellipse with rx = ry  centre, and a point on the circle
    point                points                one point; several are one object, shown by
                                               a shared ``group_id``
    mask                 mask                  the inclusive box, and a PNG of exactly it

An ellipse that is not a circle has no labelme shape, so it is written as the polygon that
traces it rather than as a circle of the wrong size. Skeletons and cuboids have no
equivalent and are left out.

Shape ``flags`` are booleans, which is exactly a checkbox attribute; ``description`` is free
text, which is an attribute named ``description``. The image's own ``flags`` mark the image
as a whole: a flag that is set is a tag.
"""

from __future__ import annotations

import base64
import io
import json
import math
from pathlib import PureWindowsPath
from typing import Any

from curvevision.core.errors import ValidationError
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
    normalise_rectangle,
    quad_to_shape,
    rotated_corners,
)
from curvevision.formats.registry import register
from curvevision.formats.rle import decode as rle_decode
from curvevision.formats.rle import encode as rle_encode
from curvevision.formats.rle import validate_mask

logger = get_logger(__name__)

#: The labelme file revision written. The tool's loader does not read it; it is there for
#: the people who do.
FILE_VERSION = "5.5.0"

#: Vertices of the polygon that stands in for an ellipse that is not a circle: enough that
#: its area is within a fraction of a percent of the ellipse's.
ELLIPSE_VERTICES = 36

#: The attribute a labelme ``description`` travels as.
DESCRIPTION = "description"

#: How many ``[x, y]`` pairs each labelme shape type needs, at least.
MINIMUM_POINTS = {
    "rectangle": 2,
    "oriented_rectangle": 4,
    "polygon": 3,
    "line": 2,
    "linestrip": 2,
    "circle": 2,
    "point": 1,
    "points": 1,
    "mask": 2,
}


def _parts(name: str) -> tuple[str, str]:
    """``nested/b.png`` -> (``nested/``, ``b.png``)."""
    folder, _, base = name.replace("\\", "/").rpartition("/")
    return (f"{folder}/" if folder else ""), base


def _stem(base: str) -> str:
    return base.rsplit(".", 1)[0] if "." in base else base


class LabelMeFormat:
    id = "labelme"
    name = "LabelMe (JSON)"
    version = "5"
    extension = "zip"
    capabilities = FormatCapabilities(
        shape_types=(
            ShapeType.RECTANGLE,
            ShapeType.ROTATED_RECTANGLE,
            ShapeType.POLYGON,
            ShapeType.POLYLINE,
            ShapeType.POINTS,
            ShapeType.ELLIPSE,
            ShapeType.MASK,
        ),
        supports_import=True,
        supports_export=True,
        supports_tracks=False,
        supports_tags=True,
        supports_attributes=True,
        notes=(
            "The labelme tool's per-image JSON, one file beside each image. Checkbox "
            "attributes travel as labelme flags and an attribute named 'description' as the "
            "shape's description; other attributes, occlusion, provenance and track identity "
            "have no place in the format and are not written. Tags are the image's flags. An "
            "ellipse that is not a circle is written as the polygon tracing it; several "
            "points are written as one labelme point each, sharing a group_id. Skeletons and "
            "cuboids are omitted. Masks need Pillow. Not LabelMe 3.0 (MIT CSAIL XML)."
        ),
    )

    # ----------------------------------------------------------------------- export

    def export(self, dataset: DatasetView, sink: ExportSink) -> None:
        checkboxes = {
            label.name: {
                attribute.name
                for attribute in label.attributes
                if attribute.attribute_type is AttributeType.CHECKBOX
            }
            for label in dataset.labels
        }
        used: set[str] = set()
        left_out: dict[str, int] = {}

        for frame in dataset:
            folder, base = _parts(frame.name)
            path = f"{folder}{_stem(base)}.json"
            if path in used:
                # `a.jpg` and `a.png` side by side: the second keeps its extension rather
                # than overwriting the first's annotations.
                path = f"{folder}{base}.json"
            used.add(path)

            shapes = self._shapes(frame, checkboxes, left_out)
            document = {
                "version": FILE_VERSION,
                "flags": dict.fromkeys(frame.tags, True),
                "shapes": shapes,
                "imagePath": base,
                "imageData": None,
                "imageHeight": frame.height,
                "imageWidth": frame.width,
            }
            sink.write(path, json.dumps(document, ensure_ascii=False, indent=2))
            if frame.has_media:
                sink.write_media(frame.name, frame)

        if left_out:
            logger.info("labelme: shapes with no labelme form left out", extra=left_out)

    def _shapes(
        self,
        frame: FrameRecord,
        checkboxes: dict[str, set[str]],
        left_out: dict[str, int],
    ) -> list[dict[str, Any]]:
        written: list[dict[str, Any]] = []
        # Groups for several points that are one object, numbered past any the annotator
        # used, so the two meanings never share a number.
        next_group = max((s.group for s in frame.shapes if s.group is not None), default=0) + 1

        for shape in frame.shapes:
            common = {
                "label": shape.label,
                "group_id": shape.group,
                "flags": _flags(shape.attributes, checkboxes.get(shape.label, set())),
                "description": _description(shape.attributes),
            }
            converted = _geometry(shape)
            if converted is None:
                left_out[shape.shape_type.value] = left_out.get(shape.shape_type.value, 0) + 1
                continue
            if shape.shape_type is ShapeType.POINTS:
                pairs = converted
                group = shape.group
                if len(pairs) > 1 and group is None:
                    group, next_group = next_group, next_group + 1
                for pair in pairs:
                    written.append(
                        {**common, "group_id": group, "shape_type": "point", "points": [pair]}
                    )
                continue
            kind, points, *mask = converted
            entry = {**common, "shape_type": kind, "points": points}
            if mask:
                entry["mask"] = mask[0]
            written.append(entry)
        return written

    # ----------------------------------------------------------------------- import

    def import_(self, source: ImportSource, context: ImportContext) -> ImportResult:
        result = ImportResult()
        known = dict(context.known_labels)
        by_base: dict[str, int] = {}
        by_stem: dict[str, int] = {}
        for name, index in context.frame_by_name.items():
            base = _parts(name)[1]
            by_base.setdefault(base, index)
            by_stem.setdefault(_stem(base), index)
        # Attributes each label the import creates turns out to need: a flag is a checkbox,
        # a description is text.
        created: dict[str, dict[str, AttributeType]] = {}
        refused: set[str] = set()

        def label_for(name: str, where: str) -> bool:
            if name in known:
                return True
            if not context.create_missing_labels:
                if name not in refused:
                    result.warnings.append(f"{where}: unknown label {name!r}; skipped")
                    refused.add(name)
                return False
            known[name] = name
            created[name] = {}
            return True

        for name in sorted(n for n in source.names() if n.lower().endswith(".json")):
            try:
                document = json.loads(source.read(name).decode("utf-8"))
            except (UnicodeDecodeError, json.JSONDecodeError):
                result.warnings.append(f"{name}: not valid JSON; skipped")
                continue
            if not isinstance(document, dict) or not isinstance(document.get("shapes"), list):
                continue  # some other JSON file in the archive: not a labelme document

            image = PureWindowsPath(str(document.get("imagePath") or "")).as_posix()
            base = image.rsplit("/", 1)[-1]
            frame = by_base.get(base)
            if frame is None:
                frame = by_stem.get(_stem(_parts(name)[1]))
            if frame is None:
                result.warnings.append(f"{name}: names {base or name!r}, not in this task; skipped")
                continue
            result.frames_matched += 1

            flags = document.get("flags")
            if isinstance(flags, dict):
                for tag, value in flags.items():
                    if value is True and isinstance(tag, str) and label_for(tag, name):
                        result.tags.append((frame, tag))

            for number, raw in enumerate(document["shapes"], 1):
                where = f"{name}: shape {number}"
                shape = _shape(raw, where, result.warnings)
                if shape is None or not label_for(shape.label, where):
                    continue
                if shape.label in created:
                    for attribute, value in shape.attributes.items():
                        created[shape.label][attribute] = (
                            AttributeType.TEXT
                            if attribute == DESCRIPTION and isinstance(value, str)
                            else AttributeType.CHECKBOX
                        )
                result.shapes.append((frame, shape))

        result.new_labels = [
            LabelSpec(
                id=label,
                name=label,
                attributes=tuple(
                    AttributeSpec(name=attribute, attribute_type=kind)
                    for attribute, kind in sorted(attributes.items())
                ),
            )
            for label, attributes in created.items()
        ]
        return result


# --------------------------------------------------------------------------- export


def _flags(attributes: dict[str, Any], checkbox_names: set[str]) -> dict[str, bool]:
    flags: dict[str, bool] = {}
    for name in sorted(checkbox_names):
        value = attributes.get(name)
        if isinstance(value, bool):
            flags[name] = value
        elif isinstance(value, str) and value.lower() in ("true", "false"):
            flags[name] = value.lower() == "true"
    return flags


def _description(attributes: dict[str, Any]) -> str:
    value = attributes.get(DESCRIPTION)
    return value if isinstance(value, str) else ""


def _pairs(flat: list[float]) -> list[list[float]]:
    return [[flat[i], flat[i + 1]] for i in range(0, len(flat) - 1, 2)]


def _geometry(shape: ShapeRecord) -> Any:
    """``(labelme shape type, points[, mask])``; for points, the pairs; ``None`` to leave out."""
    kind = shape.shape_type
    if kind in (ShapeType.RECTANGLE, ShapeType.ROTATED_RECTANGLE) and len(shape.points) >= 4:
        if shape.rotation:
            return "oriented_rectangle", _pairs(rotated_corners(shape.points, shape.rotation))
        x1, y1, x2, y2 = normalise_rectangle(shape.points)
        return "rectangle", [[x1, y1], [x2, y2]]
    if kind is ShapeType.POLYGON and len(shape.points) >= 6:
        return "polygon", _pairs(shape.points)
    if kind is ShapeType.POLYLINE and len(shape.points) >= 4:
        return "linestrip", _pairs(shape.points)
    if kind is ShapeType.POINTS and len(shape.points) >= 2:
        return _pairs(shape.points)
    if kind is ShapeType.ELLIPSE and len(shape.points) >= 4:
        cx, cy, rx, ry = shape.points[:4]
        if math.isclose(rx, ry):  # a circle looks the same however it is turned
            return "circle", [[cx, cy], [cx + rx, cy]]
        return "polygon", _ellipse_polygon(cx, cy, rx, ry, shape.rotation)
    if kind is ShapeType.MASK and shape.mask:
        encoded = _mask_png(shape.mask)
        if encoded is None:
            return None
        left, top = int(shape.mask["left"]), int(shape.mask["top"])
        right = left + int(shape.mask["width"]) - 1
        bottom = top + int(shape.mask["height"]) - 1
        return "mask", [[left, top], [right, bottom]], encoded
    return None


def _ellipse_polygon(
    cx: float, cy: float, rx: float, ry: float, rotation: float
) -> list[list[float]]:
    turn = math.radians(rotation)
    cos, sin = math.cos(turn), math.sin(turn)
    points: list[list[float]] = []
    for step in range(ELLIPSE_VERTICES):
        angle = 2 * math.pi * step / ELLIPSE_VERTICES
        dx, dy = rx * math.cos(angle), ry * math.sin(angle)
        points.append([round(cx + dx * cos - dy * sin, 3), round(cy + dx * sin + dy * cos, 3)])
    return points


def _mask_png(mask: dict[str, Any]) -> str | None:
    try:
        from PIL import Image
    except ImportError:
        logger.warning("labelme: masks need Pillow (the media extra); a mask was left out")
        return None
    try:
        validate_mask(mask)
    except ValidationError:
        logger.warning("labelme: an unreadable mask was left out")
        return None
    width, height = int(mask["width"]), int(mask["height"])
    flags = rle_decode([int(run) for run in mask["rle"]], width, height)
    image = Image.new("1", (width, height), 0)
    image.putdata([1 if flag else 0 for flag in flags])
    buffer = io.BytesIO()
    image.save(buffer, format="PNG")
    return base64.b64encode(buffer.getvalue()).decode("ascii")


# --------------------------------------------------------------------------- import


def _shape(raw: Any, where: str, warnings: list[str]) -> ShapeRecord | None:
    if not isinstance(raw, dict) or not isinstance(raw.get("label"), str):
        warnings.append(f"{where}: no label; skipped")
        return None
    label: str = raw["label"]
    # Files from labelme before shape types existed hold polygons and say nothing.
    kind = raw.get("shape_type") or "polygon"
    points = raw.get("points")
    if not (
        isinstance(points, list)
        and points
        and all(
            isinstance(pair, list | tuple)
            and len(pair) == 2
            and all(isinstance(v, int | float) and not isinstance(v, bool) for v in pair)
            for pair in points
        )
    ):
        warnings.append(f"{where}: points are not a list of [x, y]; skipped")
        return None
    if kind not in MINIMUM_POINTS:
        warnings.append(f"{where}: shape type {kind!r} has no CurveVision form; skipped")
        return None
    if len(points) < MINIMUM_POINTS[kind]:
        warnings.append(
            f"{where}: a {kind} needs {MINIMUM_POINTS[kind]} points, has {len(points)}; skipped"
        )
        return None

    flat = [float(v) for pair in points for v in pair]
    attributes: dict[str, Any] = {}
    flags = raw.get("flags")
    if isinstance(flags, dict):
        attributes.update(
            {k: v for k, v in flags.items() if isinstance(k, str) and isinstance(v, bool)}
        )
    description = raw.get("description")
    if isinstance(description, str) and description:
        attributes[DESCRIPTION] = description
    group = raw.get("group_id")
    common: dict[str, Any] = {
        "label": label,
        "group": group if isinstance(group, int) and not isinstance(group, bool) else None,
        "attributes": attributes,
    }

    if kind == "rectangle":
        return ShapeRecord(
            shape_type=ShapeType.RECTANGLE, points=normalise_rectangle(flat[:4]), **common
        )
    if kind == "oriented_rectangle":
        quad = quad_to_shape(flat[:8], label)
        quad.group, quad.attributes = common["group"], attributes
        return quad
    if kind == "polygon":
        return ShapeRecord(shape_type=ShapeType.POLYGON, points=flat, **common)
    if kind in ("line", "linestrip"):
        return ShapeRecord(shape_type=ShapeType.POLYLINE, points=flat, **common)
    if kind == "circle":
        cx, cy, px, py = flat[:4]
        radius = math.hypot(px - cx, py - cy)
        return ShapeRecord(shape_type=ShapeType.ELLIPSE, points=[cx, cy, radius, radius], **common)
    if kind in ("point", "points"):
        return ShapeRecord(shape_type=ShapeType.POINTS, points=flat, **common)
    return _mask_shape(raw, flat, common, where, warnings)


def _mask_shape(
    raw: dict[str, Any], flat: list[float], common: dict[str, Any], where: str, warnings: list[str]
) -> ShapeRecord | None:
    encoded = raw.get("mask")
    if not isinstance(encoded, str):
        warnings.append(f"{where}: a mask without its PNG; skipped")
        return None
    try:
        from PIL import Image
    except ImportError:
        warnings.append(f"{where}: masks need Pillow (the media extra); skipped")
        return None
    left, top = int(flat[0]), int(flat[1])
    right, bottom = int(flat[2]), int(flat[3])
    width, height = right - left + 1, bottom - top + 1
    try:
        image = Image.open(io.BytesIO(base64.b64decode(encoded))).convert("L")
    except (ValueError, OSError):
        warnings.append(f"{where}: the mask is not a readable PNG; skipped")
        return None
    if image.size != (width, height) or width <= 0 or height <= 0:
        warnings.append(
            f"{where}: a {image.size[0]}x{image.size[1]} mask for a {width}x{height} box; skipped"
        )
        return None
    flags = [value > 0 for value in image.tobytes()]  # mode "L": one byte per pixel
    mask = {
        "rle": rle_encode(flags, width, height),
        "left": left,
        "top": top,
        "width": width,
        "height": height,
    }
    return ShapeRecord(
        shape_type=ShapeType.MASK,
        points=[float(left), float(top), float(right), float(bottom)],
        mask=mask,
        **common,
    )


register(LabelMeFormat())


__all__ = ["LabelMeFormat"]
