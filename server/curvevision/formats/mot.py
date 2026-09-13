"""MOTChallenge multi-object tracking.

Independent implementation written from the MOTChallenge devkit's published description of
`gt.txt` / `det.txt`. No code from any other project is used.

One CSV row per object per frame::

    frame, id, bb_left, bb_top, bb_width, bb_height, conf, class, visibility
    1,     3,  794.2,   247.6,  71.2,     174.9,     1,    1,     1.0

This is the first CurveVision format that carries **object identity across frames**, which is
what makes it worth having: a detection format tells you a car is in frame 40, a tracking
format tells you it is the *same* car as in frame 39. Robotics and video work live on that
distinction, and it is the thing an exporter is most likely to get quietly wrong.

Three decisions worth knowing:

* **`id` comes from `track_id`, not from row order.** A shape with no track is given an id
  from a counter that cannot collide with a real one. Numbering rows instead would produce a
  file that looks like tracking data and describes nothing.
* **Frames are 1-based**, as the format requires, where CurveVision is 0-based throughout.
  Off-by-one here is invisible in a diff and shifts every annotation by one frame.
* **`conf` is 1 for ground truth.** MOT uses it for detector confidence; a hand-drawn box is
  not a detection with a score, and writing a model's confidence into a ground-truth file
  would misrepresent what the row is.
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
    bounding_box,
    normalise_rectangle,
)
from curvevision.formats.registry import register

logger = get_logger(__name__)

#: Ids at or above this are synthesised for untracked shapes. Far above any plausible real
#: track id, so a consumer that sees one can tell it was not an annotator's object identity.
SYNTHETIC_ID_BASE = 1_000_000


class MotFormat:
    id = "mot"
    name = "MOTChallenge 1.1"
    version = "1.1"
    extension = "zip"
    capabilities = FormatCapabilities(
        shape_types=(ShapeType.RECTANGLE,),
        supports_import=True,
        supports_export=True,
        supports_tracks=True,
        supports_tags=False,
        supports_attributes=False,
        notes=(
            "Boxes only, with object identity carried across frames. Polygons, polylines, "
            "points, ellipses and masks have no MOT representation and are omitted. "
            "Class names are written to a sidecar `labels.txt`, because MOT itself stores "
            "only a numeric class column and a file without the names is not portable."
        ),
    )

    # ----------------------------------------------------------------------- export

    def export(self, dataset: DatasetView, sink: ExportSink) -> None:
        rows: list[str] = []
        class_ids: dict[str, int] = {}
        synthetic = SYNTHETIC_ID_BASE
        untracked = 0

        for frame in dataset:
            for shape in frame.shapes:
                if shape.shape_type is not ShapeType.RECTANGLE:
                    continue
                x, y, width, height = bounding_box(normalise_rectangle(shape.points))

                if shape.label not in class_ids:
                    # MOT classes are 1-based; 0 is not a valid class in the devkit.
                    class_ids[shape.label] = len(class_ids) + 1

                if shape.track_id is not None:
                    identity = shape.track_id
                else:
                    identity = synthetic
                    synthetic += 1
                    untracked += 1

                visibility = 0.5 if shape.occluded else 1.0
                rows.append(
                    f"{frame.index + 1},{identity},{x:.2f},{y:.2f},{width:.2f},{height:.2f},"
                    f"1,{class_ids[shape.label]},{visibility:.1f}"
                )

        sink.write("gt/gt.txt", "\n".join(rows) + ("\n" if rows else ""))
        sink.write(
            "labels.txt",
            "\n".join(name for name, _ in sorted(class_ids.items(), key=lambda item: item[1]))
            + ("\n" if class_ids else ""),
        )

        for frame in dataset:
            if frame.media is not None:
                sink.write(f"img1/{frame.name}", frame.media)

        if untracked:
            logger.info(
                "mot: shapes without a track were given synthetic ids", extra={"count": untracked}
            )

    # ----------------------------------------------------------------------- import

    def import_(self, source: ImportSource, context: ImportContext) -> ImportResult:
        result = ImportResult()
        names = source.names()

        gt = next((name for name in names if name.endswith("gt/gt.txt")), None)
        if gt is None:
            gt = next((name for name in names if name.endswith("gt.txt")), None)
        if gt is None:
            gt = next((name for name in names if name.endswith("det.txt")), None)
        if gt is None:
            result.warnings.append("no gt.txt or det.txt in the archive; nothing imported")
            return result

        class_names = self._class_names(source, names)
        seen_labels = dict(context.known_labels)
        matched_frames: set[int] = set()

        for number, line in enumerate(source.read(gt).decode("utf-8").splitlines(), 1):
            if not line.strip():
                continue
            parts = [part.strip() for part in line.split(",")]
            if len(parts) < 6:
                result.warnings.append(
                    f"{gt}:{number}: expected at least 6 columns, got {len(parts)}; skipped"
                )
                continue
            try:
                frame_number = int(float(parts[0]))
                track_id = int(float(parts[1]))
                left, top, width, height = (float(value) for value in parts[2:6])
                class_id = int(float(parts[7])) if len(parts) > 7 and parts[7] else 1
                visibility = float(parts[8]) if len(parts) > 8 and parts[8] else 1.0
            except ValueError:
                result.warnings.append(f"{gt}:{number}: unreadable numbers; skipped")
                continue

            # MOT counts frames from 1. Silently keeping its numbering would shift every
            # annotation in the task by one frame, which nothing downstream would flag.
            frame = frame_number - 1
            if frame < 0 or frame >= context.frame_count:
                result.warnings.append(
                    f"{gt}:{number}: frame {frame_number} is outside this task "
                    f"(1-{context.frame_count}); skipped"
                )
                continue

            label = class_names.get(class_id, f"class_{class_id}")
            if label not in seen_labels:
                if not context.create_missing_labels:
                    result.warnings.append(f"{gt}:{number}: unknown label {label!r}; skipped")
                    continue
                seen_labels[label] = label
                result.new_labels.append(LabelSpec(id=label, name=label))

            result.shapes.append(
                (
                    frame,
                    ShapeRecord(
                        label=label,
                        shape_type=ShapeType.RECTANGLE,
                        points=[left, top, left + width, top + height],
                        occluded=visibility < 1.0,
                        # Ids we synthesised on the way out are not object identity on the way
                        # back in; dropping them stops a round trip inventing tracks.
                        track_id=None if track_id >= SYNTHETIC_ID_BASE else track_id,
                    ),
                )
            )
            matched_frames.add(frame)

        result.frames_matched = len(matched_frames)
        return result

    @staticmethod
    def _class_names(source: ImportSource, names: list[str]) -> dict[int, str]:
        """Class id to name, from the sidecar this exporter writes.

        Without it every object becomes `class_1`, which is a working import of a useless
        dataset — so the sidecar is written on export and looked for on import.
        """
        sidecar = next((name for name in names if name.endswith("labels.txt")), None)
        if sidecar is None:
            return {}
        lines = source.read(sidecar).decode("utf-8").splitlines()
        return {index: line.strip() for index, line in enumerate(lines, 1) if line.strip()}


register(MotFormat())


__all__ = ["SYNTHETIC_ID_BASE", "MotFormat"]
