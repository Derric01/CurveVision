"""Cityscapes: the ``gtFine`` layout of the Cityscapes dataset.

Independent implementation written from the dataset's published layout and label table. No
code from cityscapesScripts or Datumaro is used; both read this module's exports in CI
(`scripts/verify_cityscapes.py`), which is the conformance check.

Each image has up to five files under ``gtFine/<subset>/``, named after it::

    <name>_gtFine_polygons.json     every object as a polygon, in drawing order
    <name>_gtFine_labelIds.png      each pixel's class id
    <name>_gtFine_instanceIds.png   class id x 1000 + instance, for classes with instances
    <name>_gtFine_color.png         the class colours, to look at
    <name>_gtFine_labelTrainIds.png the training ids, when the labels are Cityscapes' own

and the image itself is ``leftImg8bit/<subset>/<name>_leftImg8bit.<ext>``, where the dataset
and Datumaro keep it.

**Class ids.** When every label of the project is a Cityscapes class — counting a crowd
(``cargroup``) and ``license plate`` — the ids, colours and training ids are Cityscapes' own,
so the files work with the dataset's own tools and with models trained on it, and no class
list is written: the dataset has none. Otherwise ``label_colors.txt`` lists the classes, one
``r g b name`` line per id, with id 0 the background and the project's labels after it in
order; every one has instances, and the training ids are the ids.

**The polygons file is what the other files are drawn from**, in the order the objects
appear, which is ascending z-order here: the shape in front is drawn last and wins the
pixel, as in the editor. That is how cityscapesScripts draws them too, and CI holds every
label image here to its rendering of the polygons file, pixel for pixel. Boxes and rotated
boxes are written as their corners, an ellipse as the polygon tracing it. A mask has no
polygon, so it is painted into the PNGs only.

On import a frame's polygons file is read when there is one — it is the vector truth.
Without it, each instance in ``instanceIds`` becomes a mask, pixel for pixel; a class with no
instances is one mask per class, which is what a semantic map holds. Without either, the
``labelIds`` map gives a mask per class.
"""

from __future__ import annotations

import array
import io
import json
import math
from collections.abc import Iterable
from typing import Any, NamedTuple

from curvevision.core.errors import ValidationError
from curvevision.core.logging import get_logger
from curvevision.domain.enums import ShapeType
from curvevision.formats.base import (
    DatasetView,
    ExportSink,
    FormatCapabilities,
    ImportContext,
    ImportResult,
    ImportSource,
    LabelSpec,
    ShapeRecord,
    normalise_rectangle,
    rotated_corners,
)
from curvevision.formats.registry import register
from curvevision.formats.rle import encode as rle_encode
from curvevision.formats.rle import pixels as mask_pixels

logger = get_logger(__name__)

GT_DIR = "gtFine"
IMAGE_DIR = "leftImg8bit"
IMAGE_SUFFIX = "_leftImg8bit"
POLYGONS = "_gtFine_polygons.json"
LABEL_IDS = "_gtFine_labelIds.png"
INSTANCE_IDS = "_gtFine_instanceIds.png"
COLOR = "_gtFine_color.png"
TRAIN_IDS = "_gtFine_labelTrainIds.png"
LABEL_COLORS = "label_colors.txt"
BACKGROUND = "background"
GROUP = "group"
#: A Cityscapes class that is annotated but never drawn into the label images (id -1).
LICENSE_PLATE = "license plate"

#: Vertices of the polygon that stands in for an ellipse.
ELLIPSE_VERTICES = 36

#: The largest value a 16-bit instance PNG holds.
MAX_INSTANCE_VALUE = 65535


class CityscapesClass(NamedTuple):
    name: str
    id: int
    train_id: int
    has_instances: bool
    color: tuple[int, int, int]


#: The Cityscapes label table: name, id, training id (255 is ignored), whether the class has
#: instances, colour. ``license plate`` (id -1) is not drawn into the label images and is
#: left out.
CITYSCAPES_CLASSES: tuple[CityscapesClass, ...] = tuple(
    CityscapesClass(*row)
    for row in (
        ("unlabeled", 0, 255, False, (0, 0, 0)),
        ("ego vehicle", 1, 255, False, (0, 0, 0)),
        ("rectification border", 2, 255, False, (0, 0, 0)),
        ("out of roi", 3, 255, False, (0, 0, 0)),
        ("static", 4, 255, False, (0, 0, 0)),
        ("dynamic", 5, 255, False, (111, 74, 0)),
        ("ground", 6, 255, False, (81, 0, 81)),
        ("road", 7, 0, False, (128, 64, 128)),
        ("sidewalk", 8, 1, False, (244, 35, 232)),
        ("parking", 9, 255, False, (250, 170, 160)),
        ("rail track", 10, 255, False, (230, 150, 140)),
        ("building", 11, 2, False, (70, 70, 70)),
        ("wall", 12, 3, False, (102, 102, 156)),
        ("fence", 13, 4, False, (190, 153, 153)),
        ("guard rail", 14, 255, False, (180, 165, 180)),
        ("bridge", 15, 255, False, (150, 100, 100)),
        ("tunnel", 16, 255, False, (150, 120, 90)),
        ("pole", 17, 5, False, (153, 153, 153)),
        ("polegroup", 18, 255, False, (153, 153, 153)),
        ("traffic light", 19, 6, False, (250, 170, 30)),
        ("traffic sign", 20, 7, False, (220, 220, 0)),
        ("vegetation", 21, 8, False, (107, 142, 35)),
        ("terrain", 22, 9, False, (152, 251, 152)),
        ("sky", 23, 10, False, (70, 130, 180)),
        ("person", 24, 11, True, (220, 20, 60)),
        ("rider", 25, 12, True, (255, 0, 0)),
        ("car", 26, 13, True, (0, 0, 142)),
        ("truck", 27, 14, True, (0, 0, 70)),
        ("bus", 28, 15, True, (0, 60, 100)),
        ("caravan", 29, 255, True, (0, 0, 90)),
        ("trailer", 30, 255, True, (0, 0, 110)),
        ("train", 31, 16, True, (0, 80, 100)),
        ("motorcycle", 32, 17, True, (0, 0, 230)),
        ("bicycle", 33, 18, True, (119, 11, 32)),
    )
)

#: Shapes that enclose an area and so have a polygon, or pixels, to draw.
DRAWN = frozenset(
    {
        ShapeType.RECTANGLE,
        ShapeType.ROTATED_RECTANGLE,
        ShapeType.POLYGON,
        ShapeType.ELLIPSE,
        ShapeType.MASK,
    }
)


class ClassMap:
    """Which id, colour and training id each label is written with."""

    def __init__(self, classes: Iterable[CityscapesClass], *, cityscapes: bool) -> None:
        self.classes = list(classes)
        self.by_name = {entry.name: entry for entry in self.classes}
        self.by_id = {entry.id: entry for entry in self.classes}
        #: Whether these are Cityscapes' own classes, so training ids mean something.
        self.cityscapes = cityscapes

    def resolve(self, name: str) -> tuple[CityscapesClass, bool] | None:
        """The class a label is drawn as, and whether it is a crowd of that class.

        As cityscapesScripts reads it: ``cargroup`` is a crowd of cars, drawn with the car
        id and no instance number. ``None`` for what is not drawn at all.
        """
        if name in self.by_name:
            return self.by_name[name], False
        if self.cityscapes and name.endswith(GROUP) and name[: -len(GROUP)] in self.by_name:
            return self.by_name[name[: -len(GROUP)]], True
        return None

    @classmethod
    def for_labels(cls, labels: list[LabelSpec]) -> ClassMap:
        own = {entry.name for entry in CITYSCAPES_CLASSES}
        if labels and all(_is_cityscapes(label.name, own) for label in labels):
            return cls(CITYSCAPES_CLASSES, cityscapes=True)
        entries = [CityscapesClass(BACKGROUND, 0, 0, False, (0, 0, 0))]
        for label in labels:
            if label.name == BACKGROUND or any(e.name == label.name for e in entries):
                continue
            index = len(entries)
            entries.append(CityscapesClass(label.name, index, index, True, _rgb(label.color)))
        return cls(entries, cityscapes=False)

    @classmethod
    def parse(cls, text: str) -> ClassMap:
        """``label_colors.txt``: ``r g b name`` per line, the line's position its id."""
        own = {entry.name: entry for entry in CITYSCAPES_CLASSES}
        entries: list[CityscapesClass] = []
        for line in text.splitlines():
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            parts = line.split(maxsplit=3)
            try:
                color = (int(parts[0]), int(parts[1]), int(parts[2]))
                name = parts[3].strip()
            except (IndexError, ValueError):
                raise ValidationError(f"{LABEL_COLORS}: {line!r} is not 'r g b name'") from None
            index = len(entries)
            known = own.get(name)
            entries.append(
                CityscapesClass(
                    name,
                    index,
                    known.train_id if known else index,
                    known.has_instances if known else True,
                    color,
                )
            )
        return cls(entries, cityscapes=False)

    def label_colors(self) -> str:
        return "".join(
            f"{entry.color[0]} {entry.color[1]} {entry.color[2]} {entry.name}\n"
            for entry in self.classes
        )


def _is_cityscapes(name: str, own: set[str]) -> bool:
    return (
        name in own
        or name == LICENSE_PLATE
        or (name.endswith(GROUP) and name[: -len(GROUP)] in own)
    )


class CityscapesFormat:
    id = "cityscapes"
    name = "Cityscapes"
    version = "1.0"
    extension = "zip"
    capabilities = FormatCapabilities(
        shape_types=(
            ShapeType.RECTANGLE,
            ShapeType.ROTATED_RECTANGLE,
            ShapeType.POLYGON,
            ShapeType.ELLIPSE,
            ShapeType.MASK,
        ),
        supports_import=True,
        supports_export=True,
        supports_tracks=False,
        supports_tags=False,
        supports_attributes=False,
        notes=(
            "The gtFine layout: per image a polygons JSON and label-id, instance-id, "
            "training-id and colour PNGs, with the image under leftImg8bit. When every label "
            "is a Cityscapes class the ids, colours and training ids are Cityscapes' own; "
            "otherwise label_colors.txt numbers the labels after a background. Boxes and "
            "ellipses are written as polygons; masks are painted into the PNGs only, since "
            "the polygons file has no place for them, so a frame whose "
            "polygons file is present imports from it and its masks do not return. Lines, "
            "points, cuboids and skeletons cover no area and are omitted. Needs Pillow."
        ),
    )

    # ----------------------------------------------------------------------- export

    def export(self, dataset: DatasetView, sink: ExportSink) -> None:
        try:
            from PIL import Image, ImageDraw
        except ImportError:  # pragma: no cover - exercised by the capability note
            raise RuntimeError(
                "Cityscapes export needs Pillow. Install the media extra: "
                "pip install 'curvevision[media]'"
            ) from None

        classes = ClassMap.for_labels(dataset.labels)
        if not classes.cityscapes:
            sink.write(LABEL_COLORS, classes.label_colors())
        train_background = 255 if classes.cityscapes else 0
        skipped: list[str] = []
        overflow = 0

        for frame in dataset:
            width, height = frame.width or 0, frame.height or 0
            if width <= 0 or height <= 0:
                skipped.append(frame.name)
                continue
            stem = frame.name.replace("\\", "/").rsplit(".", 1)[0]
            folder = f"{GT_DIR}/{frame.subset}/{stem}"

            label_ids = Image.new("L", (width, height), 0)
            instance_ids = Image.new("I", (width, height), 0)
            color = Image.new("RGB", (width, height), (0, 0, 0))
            train_ids = Image.new("L", (width, height), train_background)
            images = (label_ids, instance_ids, color, train_ids)
            drawers = [ImageDraw.Draw(image) for image in images]
            objects: list[dict[str, Any]] = []
            instances: dict[str, int] = {}

            for shape in sorted(frame.shapes, key=lambda item: item.z_order):
                if shape.shape_type not in DRAWN:
                    continue
                resolved = classes.resolve(shape.label)
                if resolved is None:
                    if classes.cityscapes and shape.label == LICENSE_PLATE:
                        polygon = _polygon(shape) if shape.shape_type is not ShapeType.MASK else []
                        if len(polygon) >= 3:
                            objects.append(
                                {"label": shape.label, "polygon": [list(p) for p in polygon]}
                            )
                    continue
                entry, crowd = resolved
                if entry.id > 255:
                    continue
                instance = entry.id
                if entry.has_instances and not crowd:
                    number = instances.get(entry.name, 0)
                    instances[entry.name] = number + 1
                    if entry.id * 1000 + number <= MAX_INSTANCE_VALUE:
                        instance = entry.id * 1000 + number
                    else:
                        overflow += 1
                fills: tuple[int | tuple[int, int, int], ...] = (
                    entry.id,
                    instance,
                    entry.color,
                    entry.train_id,
                )
                if shape.shape_type is ShapeType.MASK:
                    points = _mask_points(shape)
                    for draw, fill in zip(drawers, fills, strict=True):
                        if points:
                            draw.point(points, fill=fill)
                    continue
                polygon = _polygon(shape)
                if len(polygon) < 3:
                    continue
                objects.append({"label": shape.label, "polygon": [list(p) for p in polygon]})
                for draw, fill in zip(drawers, fills, strict=True):
                    draw.polygon(polygon, fill=fill)

            sink.write(
                f"{folder}{POLYGONS}",
                json.dumps({"imgHeight": height, "imgWidth": width, "objects": objects}, indent=2),
            )
            sink.write(f"{folder}{LABEL_IDS}", _png(label_ids))
            # 16-bit, as the dataset's own are; Pillow no longer writes 32-bit PNGs.
            sink.write(f"{folder}{INSTANCE_IDS}", _png(instance_ids.convert("I;16")))
            sink.write(f"{folder}{COLOR}", _png(color))
            sink.write(f"{folder}{TRAIN_IDS}", _png(train_ids))
            if frame.media is not None:
                extension = frame.name.rsplit(".", 1)[-1] if "." in frame.name else "png"
                sink.write(
                    f"{IMAGE_DIR}/{frame.subset}/{stem}{IMAGE_SUFFIX}.{extension}", frame.media
                )

        if skipped:
            logger.info("cityscapes: skipped frames with no known size", extra={"frames": skipped})
        if overflow:
            logger.warning(
                "cityscapes: instances past what a 16-bit PNG holds were written by class",
                extra={"count": overflow},
            )

    # ----------------------------------------------------------------------- import

    def import_(self, source: ImportSource, context: ImportContext) -> ImportResult:
        result = ImportResult()
        names = source.names()
        known = dict(context.known_labels)
        created: dict[str, LabelSpec] = {}
        refused: set[str] = set()

        classes = ClassMap(CITYSCAPES_CLASSES, cityscapes=True)
        maps = sorted(n for n in names if n.rsplit("/", 1)[-1] == LABEL_COLORS)
        if maps:
            try:
                classes = ClassMap.parse(source.read(maps[0]).decode("utf-8"))
            except (UnicodeDecodeError, ValidationError) as exc:
                result.warnings.append(f"{maps[0]}: {exc}; using the Cityscapes classes")

        by_path: dict[str, int] = {}
        by_stem: dict[str, int] = {}
        for name, index in context.frame_by_name.items():
            stem = name.replace("\\", "/").rsplit(".", 1)[0]
            by_path.setdefault(stem, index)
            by_stem.setdefault(stem.rsplit("/", 1)[-1], index)

        def frame_for(item: str) -> int | None:
            for candidate in (item, f"{item}{IMAGE_SUFFIX}"):
                if candidate in by_path:
                    return by_path[candidate]
            base = item.rsplit("/", 1)[-1]
            for candidate in (base, f"{base}{IMAGE_SUFFIX}"):
                if candidate in by_stem:
                    return by_stem[candidate]
            return None

        def label_for(name: str, where: str) -> bool:
            if name in known:
                return True
            if not context.create_missing_labels:
                if name not in refused:
                    result.warnings.append(f"{where}: unknown label {name!r}; skipped")
                    refused.add(name)
                return False
            known[name] = name
            resolved = classes.resolve(name)
            created[name] = (
                LabelSpec(id=name, name=name, color=_hex(resolved[0].color))
                if resolved
                else LabelSpec(id=name, name=name)
            )
            return True

        # Every item that has any gtFine file, with the files it has.
        items: dict[str, dict[str, str]] = {}
        for name in names:
            for suffix in (POLYGONS, INSTANCE_IDS, LABEL_IDS):
                if name.endswith(suffix):
                    items.setdefault(_item(name, suffix), {})[suffix] = name

        for item, files in sorted(items.items()):
            where = files.get(POLYGONS) or files.get(INSTANCE_IDS) or files[LABEL_IDS]
            frame = frame_for(item)
            if frame is None:
                result.warnings.append(f"{where}: no image {item!r} in this task; skipped")
                continue
            result.frames_matched += 1
            if POLYGONS in files:
                shapes = _read_polygons(source, files[POLYGONS], result.warnings)
            else:
                path = files.get(INSTANCE_IDS) or files[LABEL_IDS]
                shapes = _read_ids(source, path, classes, result.warnings)
            for shape in shapes:
                if label_for(shape.label, where):
                    result.shapes.append((frame, shape))

        result.new_labels = list(created.values())
        return result


# --------------------------------------------------------------------------- export


def _rgb(color: str) -> tuple[int, int, int]:
    value = color.lstrip("#")
    try:
        if len(value) == 3:
            value = "".join(c * 2 for c in value)
        return int(value[0:2], 16), int(value[2:4], 16), int(value[4:6], 16)
    except (ValueError, IndexError):
        return (0, 0, 0)


def _hex(color: tuple[int, int, int]) -> str:
    return "#{:02x}{:02x}{:02x}".format(*color)


def _round(points: list[float]) -> list[tuple[float, float]]:
    return [(round(x, 3), round(y, 3)) for x, y in zip(points[0::2], points[1::2], strict=False)]


def _polygon(shape: ShapeRecord) -> list[tuple[float, float]]:
    points = list(shape.points)
    if shape.shape_type is ShapeType.RECTANGLE:
        x1, y1, x2, y2 = normalise_rectangle(points)
        if shape.rotation:
            return _round(rotated_corners([x1, y1, x2, y2], shape.rotation))
        return _round([x1, y1, x2, y1, x2, y2, x1, y2])
    if shape.shape_type is ShapeType.ROTATED_RECTANGLE:
        return _round(rotated_corners(points[:4], shape.rotation))
    if shape.shape_type is ShapeType.ELLIPSE:
        cx, cy, rx, ry = [*points, 0, 0, 0, 0][:4]
        turn = math.radians(shape.rotation)
        cos, sin = math.cos(turn), math.sin(turn)
        traced: list[float] = []
        for step in range(ELLIPSE_VERTICES):
            angle = 2 * math.pi * step / ELLIPSE_VERTICES
            dx, dy = rx * math.cos(angle), ry * math.sin(angle)
            traced += [cx + dx * cos - dy * sin, cy + dx * sin + dy * cos]
        return _round(traced)
    return _round(points)


def _mask_points(shape: ShapeRecord) -> list[tuple[int, int]]:
    if not shape.mask:
        return []
    try:
        return list(mask_pixels(shape.mask))
    except ValidationError:
        logger.warning("cityscapes: skipping an unreadable mask", extra={"label": shape.label})
        return []


def _png(image: Any) -> bytes:
    buffer = io.BytesIO()
    image.save(buffer, format="PNG")
    return buffer.getvalue()


# --------------------------------------------------------------------------- import


def _item(path: str, suffix: str) -> str:
    """``gtFine/val/frankfurt/x_gtFine_labelIds.png`` -> ``frankfurt/x``."""
    stem = path[: -len(suffix)]
    parts = stem.split("/")
    if GT_DIR in parts:
        parts = parts[parts.index(GT_DIR) + 2 :]  # past gtFine/<subset>/
    return "/".join(parts) or stem


def _read_polygons(source: ImportSource, path: str, warnings: list[str]) -> list[ShapeRecord]:
    try:
        document = json.loads(source.read(path).decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError):
        warnings.append(f"{path}: not valid JSON; skipped")
        return []
    objects = document.get("objects") if isinstance(document, dict) else None
    if not isinstance(objects, list):
        warnings.append(f"{path}: no 'objects' list; skipped")
        return []
    shapes: list[ShapeRecord] = []
    for number, raw in enumerate(objects, 1):
        where = f"{path}: object {number}"
        if not isinstance(raw, dict) or not isinstance(raw.get("label"), str):
            warnings.append(f"{where}: no label; skipped")
            continue
        if raw.get("deleted"):
            continue
        polygon = raw.get("polygon")
        if not (
            isinstance(polygon, list)
            and all(
                isinstance(pair, list | tuple)
                and len(pair) == 2
                and all(isinstance(v, int | float) and not isinstance(v, bool) for v in pair)
                for pair in polygon
            )
        ):
            warnings.append(f"{where}: polygon is not a list of [x, y]; skipped")
            continue
        if len(polygon) < 3:
            warnings.append(f"{where}: a polygon needs 3 points, has {len(polygon)}; skipped")
            continue
        shapes.append(
            ShapeRecord(
                label=raw["label"],
                shape_type=ShapeType.POLYGON,
                points=[float(v) for pair in polygon for v in pair],
                # Later objects are drawn over earlier ones; z-order says the same.
                z_order=number - 1,
            )
        )
    return shapes


def _read_ids(
    source: ImportSource, path: str, classes: ClassMap, warnings: list[str]
) -> list[ShapeRecord]:
    """A mask per instance of an instance-id PNG, or per class of a label-id one."""
    try:
        from PIL import Image
    except ImportError:
        warnings.append(f"{path}: label images need Pillow (the media extra); skipped")
        return []
    try:
        image = Image.open(io.BytesIO(source.read(path)))
        image.load()
    except OSError:
        warnings.append(f"{path}: not a readable PNG; skipped")
        return []
    width, height = image.size
    values = array.array("i", image.convert("I").tobytes())  # any depth, as 32-bit ints
    regions: dict[int, list[int]] = {}
    for offset, value in enumerate(values):
        if value:
            regions.setdefault(value, []).append(offset)

    shapes: list[ShapeRecord] = []
    unknown: set[int] = set()
    for value, offsets in sorted(regions.items()):
        class_id = value // 1000 if value >= 1000 else value
        entry = classes.by_id.get(class_id)
        if entry is None:
            unknown.add(class_id)
            continue
        if class_id == 0:
            continue
        xs = [offset % width for offset in offsets]
        ys = [offset // width for offset in offsets]
        left, top = min(xs), min(ys)
        box_width, box_height = max(xs) - left + 1, max(ys) - top + 1
        flags = [False] * (box_width * box_height)
        for x, y in zip(xs, ys, strict=True):
            flags[(y - top) * box_width + (x - left)] = True
        mask = {
            "rle": rle_encode(flags, box_width, box_height),
            "left": left,
            "top": top,
            "width": box_width,
            "height": box_height,
        }
        shapes.append(
            ShapeRecord(
                label=entry.name,
                shape_type=ShapeType.MASK,
                points=[left, top, left + box_width - 1, top + box_height - 1],
                mask=mask,
            )
        )
    if unknown:
        warnings.append(f"{path}: class ids {sorted(unknown)} are in no label map; skipped")
    del height
    return shapes


register(CityscapesFormat())


__all__ = ["CITYSCAPES_CLASSES", "CityscapesFormat"]
