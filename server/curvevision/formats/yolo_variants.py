"""The Ultralytics YOLO task variants: oriented boxes, pose and classification.

Independent implementations written from the published Ultralytics dataset specifications
(https://docs.ultralytics.com/datasets/). No code from any other project is used.

`yolo.py` covers the two everyday tasks — detection and instance segmentation. Ultralytics
trains three more, each with its own label grammar, and each is what somebody's industry
actually needs:

* **Oriented bounding boxes (OBB).** Aerial and satellite imagery, industrial inspection,
  document layout: anything where objects are not axis-aligned and a straight box wraps
  mostly background. This is the only format here that keeps a rotated rectangle's *angle*.
* **Pose.** Human and animal keypoints, which CurveVision stores as skeletons.
* **Classification.** One label per image, expressed as a directory tree rather than a
  label file — which is why it needs its own exporter rather than a flag.

All three share `yolo.py`'s layout conventions (`data.yaml`, `images/<split>/`,
`labels/<split>/`) except classification, whose layout *is* the annotation.
"""

from __future__ import annotations

from curvevision.core.logging import get_logger
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
    bounding_box,
    normalise_rectangle,
    rotated_corners,
)
from curvevision.formats.registry import register

logger = get_logger(__name__)


def _clamp(value: float) -> float:
    return 0.0 if value < 0.0 else 1.0 if value > 1.0 else value


def _stem(name: str) -> str:
    base = name.rsplit("/", 1)[-1]
    return base.rsplit(".", 1)[0] if "." in base else base


def _data_yaml(dataset_name: str, class_names: list[str], extra: str = "") -> str:
    """`data.yaml` is four keys; a YAML dependency for that would be gratuitous."""
    names = "\n".join(f"  {index}: {name}" for index, name in enumerate(class_names))
    return "\n".join(
        [
            f"# {dataset_name} — exported by CurveVision",
            "path: .",
            "train: images/train",
            "val: images/val",
            *([extra] if extra else []),
            f"nc: {len(class_names)}",
            "names:",
            names,
            "",
        ]
    )


def _write_media(sink: ExportSink, frame: FrameRecord) -> None:
    if frame.media is not None:
        sink.write(f"images/{frame.subset}/{frame.name.rsplit('/', 1)[-1]}", frame.media)


# ------------------------------------------------------------------- oriented boxes


class YoloObbFormat:
    """`class x1 y1 x2 y2 x3 y3 x4 y4`, normalised, clockwise.

    The point of this format is the one thing plain YOLO cannot express: **the angle**. A
    conveyor part, a ship in a satellite image, a line of text on a scanned page — wrap any
    of them in an axis-aligned box and most of the box is background, which is why detectors
    trained on straight boxes do badly on them.
    """

    id = "yolo_obb"
    name = "Ultralytics YOLO OBB 1.0"
    version = "1.0"
    extension = "zip"
    capabilities = FormatCapabilities(
        shape_types=(ShapeType.ROTATED_RECTANGLE, ShapeType.RECTANGLE, ShapeType.POLYGON),
        supports_import=True,
        supports_export=True,
        supports_tracks=False,
        supports_tags=False,
        supports_attributes=False,
        notes=(
            "Four corners per object, so a rotated rectangle keeps its angle — which plain "
            "YOLO discards. An axis-aligned rectangle is written as its own four corners. A "
            "polygon is written as its minimum-area enclosing quadrilateral, which is a "
            "real approximation and is why polygons belong in `yolo` segmentation instead. "
            "Frames with unknown dimensions cannot be normalised and are skipped."
        ),
    )

    def export(self, dataset: DatasetView, sink: ExportSink) -> None:
        class_names = [label.name for label in dataset.labels]
        class_index = {name: index for index, name in enumerate(class_names)}
        skipped: list[str] = []

        for frame in dataset:
            if not frame.width or not frame.height:
                skipped.append(frame.name)
                continue
            lines: list[str] = []
            for shape in frame.shapes:
                index = class_index.get(shape.label)
                if index is None:
                    continue
                corners = self._corners(shape)
                if corners is None:
                    continue
                values = [
                    _clamp(coord / (frame.width if i % 2 == 0 else frame.height))
                    for i, coord in enumerate(corners)
                ]
                lines.append(" ".join([str(index), *(f"{v:.6f}" for v in values)]))

            sink.write(f"labels/{frame.subset}/{_stem(frame.name)}.txt", "\n".join(lines) + "\n")
            _write_media(sink, frame)

        sink.write("data.yaml", _data_yaml(dataset.name, class_names))
        if skipped:
            logger.info("yolo_obb: frames skipped for unknown size", extra={"frames": skipped})

    @staticmethod
    def _corners(shape: ShapeRecord) -> list[float] | None:
        if shape.shape_type is ShapeType.ROTATED_RECTANGLE:
            return rotated_corners(shape.points, shape.rotation)
        if shape.shape_type is ShapeType.RECTANGLE:
            x1, y1, x2, y2 = normalise_rectangle(shape.points)
            return [x1, y1, x2, y1, x2, y2, x1, y2]
        if shape.shape_type is ShapeType.POLYGON and len(shape.points) >= 6:
            x, y, width, height = bounding_box(shape.points)
            return [x, y, x + width, y, x + width, y + height, x, y + height]
        return None

    def import_(self, source: ImportSource, context: ImportContext) -> ImportResult:
        """Read four-corner labels back as rotated rectangles.

        The corners describe a quadrilateral, which is more general than a rotated
        rectangle; anything that is not close to rectangular comes back as a **polygon**
        rather than being forced into a box it does not fit. Silently squaring off a
        four-sided region is how an import quietly moves somebody's annotation.
        """
        result = ImportResult()
        class_names = _read_class_names(source)
        if not class_names:
            result.warnings.append("No class list found (expected data.yaml)")
            return result

        seen = dict(context.known_labels)
        matched: set[int] = set()

        for name in sorted(n for n in source.names() if n.endswith(".txt")):
            if "label" not in name.lower():
                continue
            frame = _frame_for(name, context)
            if frame is None:
                result.warnings.append(f"{name}: no frame matches; skipped")
                continue
            size = context.frame_sizes.get(frame)
            if size is None:
                result.warnings.append(f"{name}: frame size unknown, cannot denormalise; skipped")
                continue
            width, height = size

            for number, line in enumerate(source.read(name).decode("utf-8").splitlines(), 1):
                parts = line.split()
                if not parts:
                    continue
                if len(parts) != 9:
                    result.warnings.append(
                        f"{name}:{number}: OBB needs a class and 8 coordinates, got "
                        f"{len(parts) - 1}; skipped"
                    )
                    continue
                try:
                    class_id = int(parts[0])
                    coords = [float(value) for value in parts[1:]]
                except ValueError:
                    result.warnings.append(f"{name}:{number}: unreadable numbers; skipped")
                    continue
                if class_id >= len(class_names):
                    result.warnings.append(
                        f"{name}:{number}: class {class_id} is not in the class list; skipped"
                    )
                    continue

                pixels = [
                    coord * (width if i % 2 == 0 else height) for i, coord in enumerate(coords)
                ]
                label = class_names[class_id]
                if label not in seen:
                    if not context.create_missing_labels:
                        result.warnings.append(f"{name}:{number}: unknown label; skipped")
                        continue
                    seen[label] = label
                    result.new_labels.append(LabelSpec(id=label, name=label))

                shape = _quad_to_shape(pixels, label)
                result.shapes.append((frame, shape))
                matched.add(frame)

        result.frames_matched = len(matched)
        return result


def _quad_to_shape(pixels: list[float], label: str) -> ShapeRecord:
    """A quadrilateral as a rotated rectangle when it is one, else as a polygon."""
    import math

    corners = [(pixels[i], pixels[i + 1]) for i in range(0, 8, 2)]
    # Side lengths and the angle of the first edge.
    sides = [math.dist(corners[i], corners[(i + 1) % 4]) for i in range(4)]
    opposite_equal = abs(sides[0] - sides[2]) <= max(1e-6, 0.02 * max(sides[0], sides[2])) and abs(
        sides[1] - sides[3]
    ) <= max(1e-6, 0.02 * max(sides[1], sides[3]))

    # Right angles: adjacent edges' dot product near zero.
    def edge(i: int) -> tuple[float, float]:
        a, b = corners[i], corners[(i + 1) % 4]
        return (b[0] - a[0], b[1] - a[1])

    square = True
    for i in range(4):
        ex, ey = edge(i)
        fx, fy = edge((i + 1) % 4)
        length = math.hypot(ex, ey) * math.hypot(fx, fy)
        if length == 0 or abs((ex * fx + ey * fy) / length) > 0.02:
            square = False
            break

    if opposite_equal and square:
        cx = sum(x for x, _ in corners) / 4
        cy = sum(y for _, y in corners) / 4
        angle = math.degrees(math.atan2(edge(0)[1], edge(0)[0]))
        half_w, half_h = sides[0] / 2, sides[1] / 2
        return ShapeRecord(
            label=label,
            shape_type=ShapeType.ROTATED_RECTANGLE,
            points=[cx - half_w, cy - half_h, cx + half_w, cy + half_h],
            rotation=round(angle, 4),
        )
    return ShapeRecord(label=label, shape_type=ShapeType.POLYGON, points=list(pixels))


# --------------------------------------------------------------------------- pose


class YoloPoseFormat:
    """`class cx cy w h  px py v  px py v  ...`, normalised.

    Every object of a class must carry the **same number of keypoints in the same order**,
    because the model's output head is a fixed size. A skeleton missing a joint is padded
    with a zero-visibility entry rather than shortened — a short line silently shifts every
    value after it into the wrong joint.
    """

    id = "yolo_pose"
    name = "Ultralytics YOLO Pose 1.0"
    version = "1.0"
    extension = "zip"
    capabilities = FormatCapabilities(
        shape_types=(ShapeType.SKELETON,),
        supports_import=False,
        supports_export=True,
        supports_tracks=False,
        supports_tags=False,
        supports_attributes=False,
        notes=(
            "Skeletons only; every other shape type is omitted. Keypoint order comes from "
            "the label's declared keypoint names, and a missing joint is padded with "
            "visibility 0 so every line has the same width — a short line would shift "
            "every later value into the wrong joint. Visibility is Ultralytics' 0/1/2. "
            "Import is not implemented: see the note in `import_`."
        ),
    )

    def export(self, dataset: DatasetView, sink: ExportSink) -> None:
        skeleton_labels = [label for label in dataset.labels if label.keypoints]
        if not skeleton_labels:
            sink.write(
                "README.txt",
                "No label in this project declares keypoints, so there are no poses to\n"
                "export. Add keypoint names to a label's schema and annotate skeletons.\n",
            )

        class_names = [label.name for label in skeleton_labels]
        class_index = {name: index for index, name in enumerate(class_names)}
        # One keypoint count for the whole file: Ultralytics' `kpt_shape` is global.
        keypoint_count = max((len(label.keypoints) for label in skeleton_labels), default=0)
        skipped: list[str] = []

        for frame in dataset:
            if not frame.width or not frame.height:
                skipped.append(frame.name)
                continue
            lines: list[str] = []
            for shape in frame.shapes:
                if shape.shape_type is not ShapeType.SKELETON:
                    continue
                index = class_index.get(shape.label)
                if index is None:
                    continue
                line = self._pose_line(shape, index, frame.width, frame.height, keypoint_count)
                if line:
                    lines.append(line)
            sink.write(f"labels/{frame.subset}/{_stem(frame.name)}.txt", "\n".join(lines) + "\n")
            _write_media(sink, frame)

        sink.write(
            "data.yaml",
            _data_yaml(dataset.name, class_names, extra=f"kpt_shape: [{keypoint_count}, 3]"),
        )
        if skipped:
            logger.info("yolo_pose: frames skipped for unknown size", extra={"frames": skipped})

    @staticmethod
    def _pose_line(
        shape: ShapeRecord, class_index: int, width: int, height: int, keypoint_count: int
    ) -> str | None:
        flat: list[float] = []
        visibilities: list[int] = []
        for element in shape.elements[:keypoint_count]:
            point = element.get("points", [0.0, 0.0])
            if element.get("outside"):
                visibility = 0
            elif element.get("occluded"):
                visibility = 1
            else:
                visibility = 2
            flat.extend([float(point[0]), float(point[1])])
            visibilities.append(visibility)

        # Pad to the declared width. A model's pose head is a fixed size; a short line is
        # not a smaller object, it is a corrupt one.
        while len(visibilities) < keypoint_count:
            flat.extend([0.0, 0.0])
            visibilities.append(0)

        located = [
            (flat[i], flat[i + 1]) for i in range(0, len(flat), 2) if visibilities[i // 2] > 0
        ]
        if not located:
            return None
        x, y, box_w, box_h = bounding_box([coord for point in located for coord in point])
        # A box derived from keypoints collapses whenever they are collinear -- one visible
        # joint, or an arm seen straight on -- and a zero-area box is dropped by every
        # trainer that reads these files. The object is really there; only its extent is
        # unmeasurable from joints alone, so it gets the smallest box that is not nothing
        # rather than being silently discarded.
        box_w = max(box_w, 1.0)
        box_h = max(box_h, 1.0)
        values = [
            _clamp((x + box_w / 2) / width),
            _clamp((y + box_h / 2) / height),
            _clamp(box_w / width),
            _clamp(box_h / height),
        ]
        parts = [str(class_index), *(f"{value:.6f}" for value in values)]
        for i, visibility in enumerate(visibilities):
            parts.extend(
                [
                    f"{_clamp(flat[i * 2] / width):.6f}",
                    f"{_clamp(flat[i * 2 + 1] / height):.6f}",
                    str(visibility),
                ]
            )
        return " ".join(parts)

    def import_(self, source: ImportSource, context: ImportContext) -> ImportResult:
        """Not implemented, and declared so rather than half-done.

        Reading poses back needs the target project's labels to already declare keypoint
        names in the same order the file was written in — `data.yaml` carries the count but
        not the names. Guessing the order would attach every joint to the wrong name, which
        is worse than refusing. Import CVAT XML or COCO Keypoints, both of which name them.
        """
        return ImportResult(
            warnings=[
                "YOLO Pose import is not implemented: the format records how many keypoints "
                "there are but not what they are called, and guessing the order would "
                "mislabel every joint. Import COCO (keypoints) or CVAT XML instead."
            ]
        )


# ----------------------------------------------------------------- classification


class YoloClassificationFormat:
    """One class per image, expressed as `images/<split>/<class>/<file>`.

    The odd one out: there are no label files at all, because in this format the **directory
    tree is the annotation**. That is also its limit — an image with two tags has one
    directory to live in, so a frame carrying more than one is reported rather than filed
    under whichever happened to be first.
    """

    id = "yolo_classification"
    name = "Ultralytics YOLO Classification 1.0"
    version = "1.0"
    extension = "zip"
    capabilities = FormatCapabilities(
        shape_types=(),
        supports_import=False,
        supports_export=True,
        supports_tracks=False,
        supports_tags=True,
        supports_attributes=False,
        supports_images=True,
        notes=(
            "Whole-image tags only — no shapes of any kind, because the format has nowhere "
            "to put them. The directory tree is the annotation, so export images alongside "
            "the annotations or the archive is an empty skeleton. A frame with two tags "
            "cannot be filed twice and is reported instead of guessed at."
        ),
    )

    def export(self, dataset: DatasetView, sink: ExportSink) -> None:
        ambiguous: list[str] = []
        untagged: list[str] = []
        without_media: list[str] = []
        classes: set[str] = set()

        for frame in dataset:
            if not frame.tags:
                untagged.append(frame.name)
                continue
            if len(set(frame.tags)) > 1:
                # Filing it under the first tag would assert a classification nobody made.
                ambiguous.append(f"{frame.name}: {', '.join(sorted(set(frame.tags)))}")
                continue
            tag = frame.tags[0]
            classes.add(tag)
            if frame.media is None:
                without_media.append(frame.name)
                continue
            sink.write(f"images/{frame.subset}/{tag}/{frame.name.rsplit('/', 1)[-1]}", frame.media)

        report = ["Ultralytics YOLO classification export by CurveVision.", ""]
        report.append(f"Classes: {', '.join(sorted(classes)) or '(none)'}")
        if without_media:
            report += [
                "",
                "Frames whose image was not included in this export (re-run with",
                "images enabled — this format has no annotation without them):",
                *(f"  {name}" for name in without_media),
            ]
        if ambiguous:
            report += [
                "",
                "Frames with more than one tag, which this format cannot express;",
                "each was skipped rather than filed under one of them:",
                *(f"  {entry}" for entry in ambiguous),
            ]
        if untagged:
            report += ["", f"Frames with no tag: {len(untagged)}"]
        sink.write("README.txt", "\n".join(report) + "\n")

    def import_(self, source: ImportSource, context: ImportContext) -> ImportResult:
        """Not implemented: matching directory names to this task's frames needs the images.

        The annotation here *is* the file layout, so an import would have to match each
        image in the archive against a frame of the task by content or by name, and a
        mismatch silently tags the wrong picture.
        """
        return ImportResult(
            warnings=[
                "YOLO classification import is not implemented: in this format the "
                "directory tree is the annotation, so importing means matching images to "
                "frames, and a mismatch tags the wrong picture."
            ]
        )


# ------------------------------------------------------------------------ helpers


def _read_class_names(source: ImportSource) -> list[str]:
    """Class names from `data.yaml`, in index order."""
    name = next((n for n in source.names() if n.endswith("data.yaml")), None)
    if name is None:
        return []
    names: dict[int, str] = {}
    in_names = False
    for line in source.read(name).decode("utf-8").splitlines():
        stripped = line.strip()
        if stripped.startswith("names:"):
            in_names = True
            continue
        if in_names:
            if not line.startswith((" ", "\t")) or not stripped:
                break
            key, _, value = stripped.partition(":")
            try:
                names[int(key)] = value.strip()
            except ValueError:
                continue
    return [names[index] for index in sorted(names)]


def _frame_for(label_path: str, context: ImportContext) -> int | None:
    stem = _stem(label_path)
    for name, index in context.frame_by_name.items():
        if _stem(name) == stem:
            return index
    return None


register(YoloObbFormat())
register(YoloPoseFormat())
register(YoloClassificationFormat())


__all__ = ["YoloClassificationFormat", "YoloObbFormat", "YoloPoseFormat"]
