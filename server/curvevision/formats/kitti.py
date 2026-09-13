"""KITTI object detection labels.

Independent implementation written from the format's published specification (the
`devkit_object` README distributed with the KITTI 3D Object Detection benchmark). No code
from any other project is used.

KITTI is the format robotics and autonomous-driving work reaches for first, and its label
file is a plain-text table with fifteen columns per object::

    Car 0.00 0 -1.58 599.41 156.40 629.75 189.25 1.66 1.59 3.20 1.84 1.47 8.41 -1.56
    |   |    | |     |------ 2D box ---------| |--- dims ---| |-- location --| |- ry -|
    |   |    | alpha
    |   |    occlusion state (0-3)
    |   truncation (0.0-1.0)
    class

**CurveVision annotates 2D images, so the 3D columns are not invented.** A detection with no
3D extent is written with the devkit's own "unknown" convention — dimensions and location of
zero and a rotation of -10 — rather than plausible-looking numbers. A consumer that reads
those as real measurements would train on fiction, which is worse than a file it can tell is
2D-only.

Truncation and occlusion *are* carried, because CurveVision records `occluded` per shape and
because a box clipped to the frame edge is genuinely truncated. Both survive a round trip.
"""

from __future__ import annotations

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
)
from curvevision.formats.registry import register

logger = get_logger(__name__)

#: What the devkit writes when a dimension or location is not known. Rotation uses -10,
#: which is outside [-pi, pi] and so cannot be mistaken for a measured angle.
UNKNOWN_ROTATION = -10.0


#: KITTI's class column is a bare token: no spaces, and the readers are case-sensitive.
#: A label named "traffic light" would silently become two columns and shift every field
#: after it, so it is transliterated and the change is reported.
def kitti_token(name: str) -> str:
    return "_".join(name.split()) or "Unknown"


def _stem(name: str) -> str:
    """`images/0001.png` -> `0001`. KITTI pairs a label file to an image by basename."""
    base = name.rsplit("/", 1)[-1]
    return base.rsplit(".", 1)[0] if "." in base else base


class KittiFormat:
    id = "kitti"
    name = "KITTI Detection 1.0"
    version = "1.0"
    extension = "zip"
    capabilities = FormatCapabilities(
        shape_types=(ShapeType.RECTANGLE,),
        supports_import=True,
        supports_export=True,
        supports_tracks=False,
        supports_tags=False,
        supports_attributes=False,
        notes=(
            "2D boxes only. KITTI's 3D columns (dimensions, location, rotation) are written "
            "as the devkit's 'unknown' values rather than invented, because CurveVision "
            "annotates images and has no 3D extent to report. Polygons, polylines, points, "
            "ellipses and masks have no KITTI representation and are omitted. Labels "
            "containing spaces are written with underscores, since the format is "
            "space-delimited."
        ),
    )

    # ----------------------------------------------------------------------- export

    def export(self, dataset: DatasetView, sink: ExportSink) -> None:
        renamed: dict[str, str] = {}
        written = 0

        for frame in dataset:
            lines: list[str] = []
            for shape in frame.shapes:
                if shape.shape_type is not ShapeType.RECTANGLE:
                    continue
                token = kitti_token(shape.label)
                if token != shape.label:
                    renamed[shape.label] = token

                x1, y1, x2, y2 = normalise_rectangle(shape.points)
                truncation = _truncation(x1, y1, x2, y2, frame.width, frame.height)
                occlusion = 1 if shape.occluded else 0
                lines.append(
                    f"{token} {truncation:.2f} {occlusion} {UNKNOWN_ROTATION:.2f} "
                    f"{x1:.2f} {y1:.2f} {x2:.2f} {y2:.2f} "
                    f"0.00 0.00 0.00 0.00 0.00 0.00 {UNKNOWN_ROTATION:.2f}"
                )

            # KITTI expects a label file per image, empty when the image holds no objects.
            # Omitting empty files makes a reader treat those frames as unlabelled rather
            # than as labelled-and-empty, which are different facts about a dataset.
            sink.write(
                f"label_2/{_stem(frame.name)}.txt", "\n".join(lines) + ("\n" if lines else "")
            )
            written += 1
            if frame.media is not None:
                sink.write(f"image_2/{frame.name}", frame.media)

        sink.write(
            "README.txt",
            "KITTI object detection labels exported by CurveVision.\n\n"
            "Columns: type truncated occluded alpha x1 y1 x2 y2 h w l x y z rotation_y\n\n"
            "The 3D columns (h w l x y z rotation_y) are NOT measurements. CurveVision\n"
            "annotates 2D images; they carry the devkit's unknown values (zeros, and -10\n"
            "for rotation, which is outside the valid range) so that a reader can tell.\n",
        )
        if renamed:
            logger.info(
                "kitti: renamed labels for a space-delimited format", extra={"renamed": renamed}
            )

    # ----------------------------------------------------------------------- import

    def import_(self, source: ImportSource, context: ImportContext) -> ImportResult:
        result = ImportResult()
        seen_labels: dict[str, str] = dict(context.known_labels)
        by_stem = {_stem(name): index for name, index in context.frame_by_name.items()}

        label_files = [
            name for name in source.names() if name.endswith(".txt") and "label" in name.lower()
        ]
        if not label_files:
            label_files = [name for name in source.names() if name.endswith(".txt")]

        for name in sorted(label_files):
            stem = _stem(name)
            frame = by_stem.get(stem)
            if frame is None:
                result.warnings.append(f"{name}: no frame named {stem!r} in this task; skipped")
                continue

            matched = False
            for number, line in enumerate(source.read(name).decode("utf-8").splitlines(), 1):
                parts = line.split()
                if not parts:
                    continue
                if len(parts) < 8:
                    result.warnings.append(
                        f"{name}:{number}: expected at least 8 columns, got {len(parts)}; skipped"
                    )
                    continue
                # "DontCare" marks a region the benchmark ignores rather than an object.
                # Importing it as an annotation would invent a class nobody labelled.
                if parts[0] == "DontCare":
                    continue
                try:
                    occlusion = int(float(parts[2]))
                    box = [float(value) for value in parts[4:8]]
                except ValueError:
                    result.warnings.append(f"{name}:{number}: unreadable numbers; skipped")
                    continue

                label = parts[0]
                if label not in seen_labels:
                    if not context.create_missing_labels:
                        result.warnings.append(f"{name}:{number}: unknown label {label!r}; skipped")
                        continue
                    seen_labels[label] = label
                    result.new_labels.append(LabelSpec(id=label, name=label))

                result.shapes.append(
                    (
                        frame,
                        ShapeRecord(
                            label=label,
                            shape_type=ShapeType.RECTANGLE,
                            points=normalise_rectangle(box),
                            occluded=occlusion > 0,
                        ),
                    )
                )
                matched = True

            if matched:
                result.frames_matched += 1

        return result


def _truncation(
    x1: float, y1: float, x2: float, y2: float, width: int | None, height: int | None
) -> float:
    """How much of the box falls outside the image, in [0, 1].

    KITTI's own definition. Computed rather than written as zero because a box running off
    the frame edge is exactly what the column is for, and a reader that filters on it would
    otherwise keep objects it should discard.
    """
    if not width or not height:
        return 0.0
    full = (x2 - x1) * (y2 - y1)
    if full <= 0:
        return 0.0
    cx1, cy1 = max(0.0, x1), max(0.0, y1)
    cx2, cy2 = min(float(width), x2), min(float(height), y2)
    visible = max(0.0, cx2 - cx1) * max(0.0, cy2 - cy1)
    return round(min(1.0, max(0.0, 1.0 - visible / full)), 2)


register(KittiFormat())


__all__ = ["KittiFormat", "kitti_token"]
