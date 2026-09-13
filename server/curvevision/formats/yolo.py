"""YOLO detection and segmentation format.

Independent implementation written from the published Ultralytics YOLO dataset layout.

Layout produced:

    data.yaml                 class list and split paths
    images/train/<name>       (only when images are requested)
    labels/train/<name>.txt   one annotation per line

Detection lines are ``class cx cy w h`` with coordinates normalised to [0, 1].
Segmentation lines are ``class x1 y1 x2 y2 ...``, likewise normalised.

Normalisation needs the frame dimensions. A frame whose size is unknown (media probing
unavailable) is skipped with a warning rather than exported with wrong numbers -- silently
writing coordinates normalised against a guessed size produces a dataset that trains badly
and gives no clue why.
"""

from __future__ import annotations

import json

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
    bounding_box,
    normalise_rectangle,
    rotated_corners,
)
from curvevision.formats.registry import register


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
            "Bounding boxes and segmentation polygons. A rotated rectangle is written as "
            "the axis-aligned box around its rotated corners -- correct for a detection "
            "dataset, but the angle itself is lost; use `yolo_obb` to keep it. YOLO has no "
            "attribute or track representation, so those are dropped. Frames with unknown "
            "dimensions cannot be normalised and are skipped."
        ),
    )

    # ----------------------------------------------------------------------- export

    def export(self, dataset: DatasetView, sink: ExportSink) -> None:
        class_names = [label.name for label in dataset.labels]
        class_index = {name: index for index, name in enumerate(class_names)}
        skipped: list[str] = []
        listed: list[str] = []

        for frame in dataset:
            if not frame.width or not frame.height:
                skipped.append(frame.name)
                continue

            lines: list[str] = []
            for shape in frame.shapes:
                index = class_index.get(shape.label)
                if index is None:
                    continue
                line = self._shape_line(shape, index, frame.width, frame.height)
                if line:
                    lines.append(line)

            stem = frame.name.rsplit("/", 1)[-1].rsplit(".", 1)[0]
            sink.write(f"labels/{frame.subset}/{stem}.txt", "\n".join(lines) + "\n")
            listed.append(f"images/{frame.subset}/{frame.name.rsplit('/', 1)[-1]}")
            if frame.media is not None:
                sink.write(f"images/{frame.subset}/{frame.name.rsplit('/', 1)[-1]}", frame.media)

        # data.yaml is emitted as hand-written YAML: it is four keys, and a YAML
        # dependency for that would be gratuitous.
        names = "\n".join(f"  {i}: {name}" for i, name in enumerate(class_names))
        sink.write(
            "data.yaml",
            "\n".join(
                [
                    f"# {dataset.name} — exported by CurveVision",
                    "path: .",
                    "train: images/train",
                    "val: images/val",
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

    def _shape_line(
        self, shape: ShapeRecord, class_index: int, width: int, height: int
    ) -> str | None:
        if shape.shape_type is ShapeType.ROTATED_RECTANGLE and shape.rotation:
            # The stored points are the *unrotated* box. Using them directly claims a
            # 100x20 extent for a bar that, turned 90 degrees, occupies 20x100 -- a box
            # that does not contain the object it names. Take the rotated corners' bounds.
            x, y, bw_px, bh_px = bounding_box(rotated_corners(shape.points, shape.rotation))
            values = [
                (x + bw_px / 2) / width,
                (y + bh_px / 2) / height,
                bw_px / width,
                bh_px / height,
            ]
        elif shape.shape_type in (ShapeType.RECTANGLE, ShapeType.ROTATED_RECTANGLE):
            x1, y1, x2, y2 = normalise_rectangle(shape.points)
            cx = ((x1 + x2) / 2) / width
            cy = ((y1 + y2) / 2) / height
            bw = (x2 - x1) / width
            bh = (y2 - y1) / height
            values = [cx, cy, bw, bh]
        elif shape.shape_type is ShapeType.POLYGON:
            values = [
                coord / (width if index % 2 == 0 else height)
                for index, coord in enumerate(shape.points)
            ]
        else:
            return None

        clamped = [min(1.0, max(0.0, value)) for value in values]
        return " ".join([str(class_index), *(f"{value:.6f}" for value in clamped)])

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
