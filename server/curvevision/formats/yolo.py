"""YOLO detection and segmentation format.

Independent implementation written from the published Ultralytics YOLO dataset layout.

Layout produced:

    data.yaml                 class list and split paths
    images/train/<name>       (only when images are requested)
    labels/train/<name>.txt   one annotation per line

Detection lines are ``class cx cy w h`` with coordinates normalised to [0, 1].
Segmentation lines are ``class x1 y1 x2 y2 ...``, likewise normalised.

The two are never mixed. Ultralytics reads a label file holding any line longer than six
values as segments throughout (`verify_image_label`), so a box line beside a polygon line
became a two-point "polygon" and a wrong box; and it drops every segment of a dataset whose
box and segment counts differ (`YOLODataset.get_labels`). So a dataset with any polygon is
written as segments everywhere — a box as its four corners, from which Ultralytics derives
the same box — and one without is written as boxes. The importer reads four corners that
form an axis-aligned rectangle back as the rectangle.

Normalisation needs the frame dimensions. A frame whose size is unknown (media probing
unavailable) is skipped with a warning rather than exported with wrong numbers -- silently
writing coordinates normalised against a guessed size produces a dataset that trains badly
and gives no clue why.
"""

from __future__ import annotations

import json
from dataclasses import dataclass

from curvevision.domain.enums import ShapeType
from curvevision.formats.base import (
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
    rotated_corners,
)
from curvevision.formats.registry import register


def split_of(subset: str) -> str:
    """The directory a frame's subset is written under.

    Ultralytics trains from `images/train` (and `val`); every frame here is in the
    `default` subset, and writing it to `images/default` while `data.yaml` named
    `images/train` produced a dataset Ultralytics refused with "images not found" — every
    YOLO export, until iteration 66.
    """
    return "train" if subset in ("", "default") else subset


def split_lines(splits: set[str]) -> list[str]:
    """`data.yaml`'s split keys, pointing only at splits that were written.

    Ultralytics requires `val`; with no validation split it is the training images, which
    trains and reports, rather than a path that does not exist.
    """
    lines = ["train: images/train", f"val: images/{'val' if 'val' in splits else 'train'}"]
    if "test" in splits:
        lines.append("test: images/test")
    return lines


@dataclass(frozen=True, slots=True)
class _Row:
    """One shape, ready to be written as a box line or as a segment line."""

    class_index: int
    #: Its outline in pixels: a box's four corners, a rotated box's rotated corners.
    corners: list[float]
    width: int
    height: int
    #: Whether it is a polygon, which only a segment line can carry.
    polygon: bool

    def line(self, *, segments: bool) -> str | None:
        if segments:
            values = [
                coord / (self.width if index % 2 == 0 else self.height)
                for index, coord in enumerate(self.corners)
            ]
            clipped = [min(1.0, max(0.0, value)) for value in values]
            return " ".join([str(self.class_index), *(f"{value:.6f}" for value in clipped)])
        # The box around the outline, clipped to the image *before* it becomes centre and
        # size: clamping the four numbers separately moves the box and keeps its overhang.
        xs = [min(float(self.width), max(0.0, x)) for x in self.corners[0::2]]
        ys = [min(float(self.height), max(0.0, y)) for y in self.corners[1::2]]
        x1, x2, y1, y2 = min(xs), max(xs), min(ys), max(ys)
        if x2 <= x1 or y2 <= y1:
            return None  # entirely outside the image
        values = [
            (x1 + x2) / 2 / self.width,
            (y1 + y2) / 2 / self.height,
            (x2 - x1) / self.width,
            (y2 - y1) / self.height,
        ]
        return " ".join([str(self.class_index), *(f"{value:.6f}" for value in values)])


def _row(shape: ShapeRecord, class_index: int, width: int, height: int) -> _Row | None:
    if shape.shape_type is ShapeType.ROTATED_RECTANGLE and shape.rotation:
        # The stored points are the *unrotated* box: a 100x20 bar turned 90 degrees
        # occupies 20x100. Its rotated corners are what it covers.
        corners = rotated_corners(shape.points, shape.rotation)
    elif shape.shape_type in (ShapeType.RECTANGLE, ShapeType.ROTATED_RECTANGLE):
        x1, y1, x2, y2 = normalise_rectangle(shape.points)
        corners = [x1, y1, x2, y1, x2, y2, x1, y2]
    elif shape.shape_type is ShapeType.POLYGON and len(shape.points) >= 6:
        return _Row(class_index, list(shape.points), width, height, polygon=True)
    else:
        return None
    return _Row(class_index, corners, width, height, polygon=False)


def _axis_aligned_box(points: list[float]) -> list[float] | None:
    """`[x1, y1, x2, y2]` when four corners are an axis-aligned rectangle, as a box is
    written in a segment file; otherwise None."""
    if len(points) != 8:
        return None
    xs, ys = points[0::2], points[1::2]
    left, right, top, bottom = min(xs), max(xs), min(ys), max(ys)
    if right <= left or bottom <= top:
        return None
    corners = {(round(x, 4), round(y, 4)) for x, y in zip(xs, ys, strict=True)}
    expected = {(round(x, 4), round(y, 4)) for x in (left, right) for y in (top, bottom)}
    return [left, top, right, bottom] if corners == expected else None


class YoloFormat:
    id = "yolo"
    name = "YOLO 1.1"
    version = "1.1"
    extension = "zip"
    capabilities = FormatCapabilities(
        shape_types=(ShapeType.RECTANGLE, ShapeType.ROTATED_RECTANGLE, ShapeType.POLYGON),
        supports_import=True,
        supports_export=True,
        supports_tracks=False,
        supports_tags=False,
        supports_attributes=False,
        notes=(
            "Bounding boxes and segmentation polygons. A dataset with any polygon is written "
            "as segments throughout -- boxes as their four corners, rotated boxes as their "
            "rotated corners -- because Ultralytics misreads a file that mixes the two; one "
            "without is written as boxes, a rotated rectangle as the axis-aligned box around "
            "its corners (use `yolo_obb` to keep the angle). YOLO has no attribute or track "
            "representation, so those are dropped. Frames with unknown dimensions cannot be "
            "normalised and are skipped."
        ),
    )

    # ----------------------------------------------------------------------- export

    def export(self, dataset: DatasetView, sink: ExportSink) -> None:
        class_names = [label.name for label in dataset.labels]
        class_index = {name: index for index, name in enumerate(class_names)}
        skipped: list[str] = []
        listed: list[str] = []
        written_frames: list[FrameRecord] = []
        # Label files wait until every frame is seen: whether they hold boxes or segments
        # depends on whether *any* frame has a polygon. They are small; images are not, and
        # go out as they come.
        pending: list[tuple[str, list[_Row]]] = []

        for frame in dataset:
            if not frame.width or not frame.height:
                skipped.append(frame.name)
                continue

            rows: list[_Row] = []
            for shape in frame.shapes:
                index = class_index.get(shape.label)
                if index is None:
                    continue
                row = _row(shape, index, frame.width, frame.height)
                if row is not None:
                    rows.append(row)

            written_frames.append(frame)
            stem = frame.name.rsplit("/", 1)[-1].rsplit(".", 1)[0]
            pending.append((f"labels/{split_of(frame.subset)}/{stem}.txt", rows))
            listed.append(f"images/{split_of(frame.subset)}/{frame.name.rsplit('/', 1)[-1]}")
            if frame.media is not None:
                sink.write(
                    f"images/{split_of(frame.subset)}/{frame.name.rsplit('/', 1)[-1]}", frame.media
                )

        segments = any(row.polygon for _path, rows in pending for row in rows)
        for path, rows in pending:
            lines = [line for row in rows if (line := row.line(segments=segments))]
            sink.write(path, "\n".join(lines) + "\n")

        # data.yaml is emitted as hand-written YAML: it is four keys, and a YAML
        # dependency for that would be gratuitous.
        names = "\n".join(f"  {i}: {name}" for i, name in enumerate(class_names))
        sink.write(
            "data.yaml",
            "\n".join(
                [
                    f"# {dataset.name} — exported by CurveVision",
                    "path: .",
                    *split_lines({split_of(frame.subset) for frame in written_frames}),
                    f"nc: {len(class_names)}",
                    "names:",
                    names,
                    "",
                ]
            ),
        )
        sink.write("train.txt", "\n".join(listed) + "\n")
        if skipped:
            sink.write(
                "curvevision_warnings.json",
                json.dumps(
                    {
                        "skipped_frames_missing_dimensions": skipped,
                        "reason": (
                            "YOLO coordinates are normalised against the frame size, "
                            "which was unknown for these frames."
                        ),
                    },
                    indent=2,
                ),
            )

    # ----------------------------------------------------------------------- import

    def import_(self, source: ImportSource, context: ImportContext) -> ImportResult:
        result = ImportResult()
        class_names = self._read_class_names(source)
        if not class_names:
            result.warnings.append(
                "No class list found (expected data.yaml, obj.names or classes.txt)"
            )
            return result

        for name in class_names:
            if name not in context.known_labels and context.create_missing_labels:
                result.new_labels.append(LabelSpec(id=name, name=name))

        matched: set[int] = set()
        for path in source.names():
            if not path.endswith(".txt") or path.rsplit("/", 1)[-1] in (
                "classes.txt",
                "train.txt",
                "val.txt",
                "test.txt",
                "obj.names",
            ):
                continue
            stem = path.rsplit("/", 1)[-1][:-4]
            frame = self._match_frame(stem, context)
            if frame is None:
                result.warnings.append(f"No frame matches label file {path!r}; skipped")
                continue
            size = context.frame_sizes.get(frame)
            if size is None:
                result.warnings.append(
                    f"{path}: frame {frame} has unknown dimensions, so normalised YOLO "
                    "coordinates cannot be converted to pixels; skipped"
                )
                continue
            matched.add(frame)

            content = source.read(path).decode("utf-8", errors="replace")
            for line_number, line in enumerate(content.splitlines(), start=1):
                parts = line.split()
                if not parts:
                    continue
                try:
                    shape = self._parse_line(parts, class_names, size)
                except ValueError as exc:
                    result.warnings.append(f"{path}:{line_number}: {exc}")
                    continue
                if shape is not None:
                    result.shapes.append((frame, shape))

        result.frames_matched = len(matched)
        return result

    def _parse_line(
        self, parts: list[str], class_names: list[str], size: tuple[int, int]
    ) -> ShapeRecord | None:
        try:
            class_index = int(parts[0])
            values = [float(value) for value in parts[1:]]
        except ValueError as exc:
            raise ValueError(f"could not parse numbers ({exc})") from exc
        if not 0 <= class_index < len(class_names):
            raise ValueError(f"class index {class_index} is outside the class list")

        width, height = size
        label = class_names[class_index]

        if len(values) == 4:
            cx, cy, bw, bh = values
            return ShapeRecord(
                label=label,
                shape_type=ShapeType.RECTANGLE,
                points=[
                    (cx - bw / 2) * width,
                    (cy - bh / 2) * height,
                    (cx + bw / 2) * width,
                    (cy + bh / 2) * height,
                ],
                source="imported",
            )
        if len(values) >= 6 and len(values) % 2 == 0:
            box = _axis_aligned_box(values)
            if box is not None:
                return ShapeRecord(
                    label=label,
                    shape_type=ShapeType.RECTANGLE,
                    points=[
                        box[0] * width,
                        box[1] * height,
                        box[2] * width,
                        box[3] * height,
                    ],
                    source="imported",
                )
            return ShapeRecord(
                label=label,
                shape_type=ShapeType.POLYGON,
                points=[
                    value * (width if index % 2 == 0 else height)
                    for index, value in enumerate(values)
                ],
                source="imported",
            )
        raise ValueError(f"unexpected value count {len(values)}")

    def _match_frame(self, stem: str, context: ImportContext) -> int | None:
        if stem in context.frame_by_name:
            return context.frame_by_name[stem]
        for name, frame in context.frame_by_name.items():
            base = name.rsplit("/", 1)[-1]
            if base.rsplit(".", 1)[0] == stem:
                return frame
        return None

    def _read_class_names(self, source: ImportSource) -> list[str]:
        for candidate in ("data.yaml", "data.yml", "dataset.yaml"):
            if source.exists(candidate):
                return self._parse_yaml_names(
                    source.read(candidate).decode("utf-8", errors="replace")
                )
        for candidate in ("obj.names", "classes.txt", "names.txt"):
            if source.exists(candidate):
                return [
                    line.strip()
                    for line in source.read(candidate)
                    .decode("utf-8", errors="replace")
                    .splitlines()
                    if line.strip()
                ]
        # Some archives nest everything one directory deep.
        for name in source.names():
            if name.endswith(("data.yaml", "obj.names", "classes.txt")):
                text = source.read(name).decode("utf-8", errors="replace")
                return (
                    self._parse_yaml_names(text)
                    if name.endswith((".yaml", ".yml"))
                    else [line.strip() for line in text.splitlines() if line.strip()]
                )
        return []

    @staticmethod
    def _parse_yaml_names(text: str) -> list[str]:
        """Extract the ``names:`` block without taking a YAML dependency.

        Handles both shapes Ultralytics emits: a mapping (``0: person``) and a flow list
        (``names: [person, car]``).
        """
        names: list[str] = []
        in_names = False
        for raw in text.splitlines():
            line = raw.rstrip()
            if not in_names:
                if line.startswith("names:"):
                    remainder = line[len("names:") :].strip()
                    if remainder.startswith("[") and remainder.endswith("]"):
                        return [
                            item.strip().strip("'\"")
                            for item in remainder[1:-1].split(",")
                            if item.strip()
                        ]
                    in_names = True
                continue
            if line and not line.startswith((" ", "\t", "-")):
                break
            stripped = line.strip().lstrip("- ")
            if not stripped:
                continue
            if ":" in stripped:
                _, _, value = stripped.partition(":")
                names.append(value.strip().strip("'\""))
            else:
                names.append(stripped.strip("'\""))
        return names


register(YoloFormat())

__all__ = ["YoloFormat"]
