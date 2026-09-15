"""CVAT XML — the migration bridge.

Independent implementation written from the format's published schema
(https://docs.cvat.ai/docs/manual/advanced/xml_format/). No code from any other project is
used; this reads and writes the documented XML, it does not embed anything of theirs.

**This format exists so that work already done elsewhere is not stranded.** Someone with
years of annotation in CVAT should be able to bring it into CurveVision in one upload and
take it back out again unchanged if they decide to leave. A tool that can only be entered is
a trap, and a project that claims to be open should be the easiest one to walk away from.

It is also the most *expressive* format here, which is why it is the honest choice for a
lossless-ish migration: unlike COCO or KITTI it carries polygons, polylines, points, boxes,
ellipses, tags, per-object attributes **and** tracks with keyframes and `outside` flags — the
whole shape of what an annotator actually did.

Two shapes of the same format exist upstream:

* **"for images"** — a `<image>` element per frame holding shapes. Written here.
* **"for video"** — a `<track>` element per object holding per-frame boxes. Read here, and
  written when the dataset carries tracks, because exporting a tracked dataset as loose
  per-frame shapes throws away exactly the identity the user came for.
"""

from __future__ import annotations

import xml.etree.ElementTree as ET
from collections import defaultdict
from dataclasses import replace
from typing import Any
from xml.sax.saxutils import escape

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
)
from curvevision.formats.registry import register
from curvevision.formats.rle import (
    from_attribute as rle_from_attribute,
)
from curvevision.formats.rle import (
    mask_bounds,
    validate_mask,
)
from curvevision.formats.rle import (
    to_attribute as rle_to_attribute,
)

logger = get_logger(__name__)

#: CVAT element name for each of our shape types, and back.
TO_XML: dict[ShapeType, str] = {
    ShapeType.RECTANGLE: "box",
    ShapeType.POLYGON: "polygon",
    ShapeType.POLYLINE: "polyline",
    ShapeType.POINTS: "points",
    ShapeType.ELLIPSE: "ellipse",
    ShapeType.MASK: "mask",
}
FROM_XML: dict[str, ShapeType] = {value: key for key, value in TO_XML.items()}


def _points_attr(points: list[float]) -> str:
    """`[x1,y1,x2,y2]` to CVAT's `x1,y1;x2,y2`."""
    pairs = zip(points[0::2], points[1::2], strict=False)
    return ";".join(f"{x:.2f},{y:.2f}" for x, y in pairs)


def _mask_attrs(mask: dict[str, Any] | None) -> str | None:
    """CVAT's mask attributes, or None when there is no mask to write.

    `width` and `height` are *inclusive* spans in CVAT's element -- it writes
    `right - left + 1` -- which is exactly what our stored box already holds, so the two
    line up without arithmetic. A shape typed `mask` with no mask payload is dropped rather
    than written as an empty one: an empty mask covers nothing, and a reader cannot tell it
    from a mask that failed to serialise.
    """
    if not mask:
        return None
    try:
        validate_mask(mask)
    except ValidationError:
        return None
    return (
        f' rle="{rle_to_attribute([int(run) for run in mask["rle"]])}"'
        f' left="{int(mask["left"])}" top="{int(mask["top"])}"'
        f' width="{int(mask["width"])}" height="{int(mask["height"])}"'
    )


def _mask_from(element: ET.Element) -> dict[str, Any] | None:
    """A CVAT `<mask>` element back to a stored mask, or None if it carries no runs."""
    runs = rle_from_attribute(element.get("rle", ""))
    if not runs:
        return None
    return {
        "rle": runs,
        "left": int(element.get("left", "0") or 0),
        "top": int(element.get("top", "0") or 0),
        "width": int(element.get("width", "0") or 0),
        "height": int(element.get("height", "0") or 0),
    }


def _parse_points(value: str) -> list[float]:
    flat: list[float] = []
    for pair in value.split(";"):
        pair = pair.strip()
        if not pair:
            continue
        x, _, y = pair.partition(",")
        flat.extend([float(x), float(y)])
    return flat


class CvatXmlFormat:
    id = "cvat_xml"
    name = "CVAT XML 1.1"
    version = "1.1"
    extension = "zip"
    capabilities = FormatCapabilities(
        shape_types=(
            ShapeType.RECTANGLE,
            ShapeType.POLYGON,
            ShapeType.POLYLINE,
            ShapeType.POINTS,
            ShapeType.ELLIPSE,
            ShapeType.MASK,
        ),
        supports_import=True,
        supports_export=True,
        supports_tracks=True,
        supports_tags=True,
        supports_attributes=True,
        notes=(
            "The most expressive format here, and the one to use when moving a project "
            "between CurveVision and CVAT in either direction. Carries boxes, polygons, "
            "polylines, points, ellipses, masks, tags, per-object attributes and tracks "
            "with keyframes. Skeletons are written as their element points and lose the "
            "parent/child structure, which is the one real gap."
        ),
    )

    # ----------------------------------------------------------------------- export

    def export(self, dataset: DatasetView, sink: ExportSink) -> None:
        frames = list(dataset)
        tracked = any(shape.track_id is not None for frame in frames for shape in frame.shapes)

        lines: list[str] = [
            '<?xml version="1.0" encoding="utf-8"?>',
            "<annotations>",
            "  <version>1.1</version>",
            "  <meta>",
            "    <task>",
        ]
        lines.append(f"      <size>{len(frames)}</size>")
        lines.append(f"      <mode>{'interpolation' if tracked else 'annotation'}</mode>")
        lines.append("      <labels>")
        for label in dataset.labels:
            lines.append("        <label>")
            lines.append(f"          <name>{escape(label.name)}</name>")
            lines.append(f"          <color>{escape(label.color or '')}</color>")
            lines.append("          <attributes>")
            for attribute in label.attributes:
                lines.append("            <attribute>")
                lines.append(f"              <name>{escape(attribute.name)}</name>")
                lines.append(
                    f"              <input_type>{escape(attribute.attribute_type)}</input_type>"
                )
                lines.append(
                    f"              <values>{escape(chr(10).join(attribute.values))}</values>"
                )
                lines.append("            </attribute>")
            lines.append("          </attributes>")
            lines.append("        </label>")
        lines.append("      </labels>")
        lines.append("    </task>")
        lines.append("    <dumped>CurveVision</dumped>")
        lines.append("  </meta>")

        if tracked:
            lines.extend(self._track_elements(frames))
        else:
            lines.extend(self._image_elements(frames))

        lines.append("</annotations>")
        sink.write("annotations.xml", "\n".join(lines) + "\n")

        for frame in frames:
            if frame.media is not None:
                sink.write(f"images/{frame.name}", frame.media)

    def _image_elements(self, frames: list[Any]) -> list[str]:
        lines: list[str] = []
        for frame in frames:
            attrs = (
                f'  <image id="{frame.index}" name="{escape(frame.name)}"'
                f' width="{frame.width or 0}" height="{frame.height or 0}">'
            )
            lines.append(attrs)
            for shape in frame.shapes:
                lines.extend(self._shape_element(shape, indent="    "))
            for tag in frame.tags:
                lines.append(f'    <tag label="{escape(tag)}" source="manual"></tag>')
            lines.append("  </image>")
        return lines

    def _track_elements(self, frames: list[Any]) -> list[str]:
        """Group shapes by track id and emit `<track>` elements.

        A shape with no track id still has to go somewhere: it becomes a one-frame track
        with `outside="1"` on the following frame, which is how CVAT itself represents an
        object that exists on exactly one frame. Dropping them would lose real annotations
        because the *rest* of the dataset happened to use tracks.
        """
        by_track: dict[int, list[tuple[int, Any]]] = defaultdict(list)
        loose: list[tuple[int, Any]] = []
        for frame in frames:
            for shape in frame.shapes:
                if shape.track_id is None:
                    loose.append((frame.index, shape))
                else:
                    by_track[shape.track_id].append((frame.index, shape))

        lines: list[str] = []
        next_id = (max(by_track) + 1) if by_track else 0
        for index, shape in loose:
            by_track[next_id] = [(index, shape)]
            next_id += 1

        for track_id in sorted(by_track):
            entries = sorted(by_track[track_id], key=lambda item: item[0])
            label = entries[0][1].label
            lines.append(f'  <track id="{track_id}" label="{escape(label)}" source="manual">')
            for frame_index, shape in entries:
                lines.extend(
                    self._shape_element(shape, indent="    ", frame=frame_index, keyframe=True)
                )
            # Close the track: CVAT reads the trailing `outside` shape as the object leaving,
            # and without it a consumer interpolates the object forever.
            last_frame, last_shape = entries[-1]
            lines.extend(
                self._shape_element(
                    last_shape, indent="    ", frame=last_frame + 1, keyframe=True, outside=True
                )
            )
            lines.append("  </track>")
        return lines

    def _shape_element(
        self,
        shape: ShapeRecord,
        *,
        indent: str,
        frame: int | None = None,
        keyframe: bool = False,
        outside: bool = False,
    ) -> list[str]:
        element = TO_XML.get(shape.shape_type)
        if element is None:
            return []

        parts = [f'{indent}<{element} label="{escape(shape.label)}"']
        if frame is not None:
            parts.append(f' frame="{frame}"')
            parts.append(f' keyframe="{1 if keyframe else 0}"')
        parts.append(f' occluded="{1 if shape.occluded else 0}"')
        parts.append(f' outside="{1 if outside else 0}"')
        parts.append(f' z_order="{shape.z_order}"')
        if shape.rotation:
            parts.append(f' rotation="{shape.rotation:.2f}"')

        if shape.shape_type is ShapeType.RECTANGLE:
            x1, y1, x2, y2 = normalise_rectangle(shape.points)
            parts.append(f' xtl="{x1:.2f}" ytl="{y1:.2f}" xbr="{x2:.2f}" ybr="{y2:.2f}"')
        elif shape.shape_type is ShapeType.ELLIPSE:
            cx, cy, rx, ry = [*list(shape.points), 0, 0, 0, 0][:4]
            parts.append(f' cx="{cx:.2f}" cy="{cy:.2f}" rx="{rx:.2f}" ry="{ry:.2f}"')
        elif shape.shape_type is ShapeType.MASK:
            # A mask's geometry is its runs, not its `points`. Writing `points` here -- which
            # is what this did until the mask export was finished -- emitted the two corners
            # of the bounding box and silently dropped every pixel, while the format's own
            # capabilities claimed masks were carried.
            attributes = _mask_attrs(shape.mask)
            if attributes is None:
                return []
            parts.append(attributes)
        else:
            parts.append(f' points="{_points_attr(shape.points)}"')

        lines = ["".join(parts) + ">"]
        for name, value in sorted(shape.attributes.items()):
            lines.append(
                f'{indent}  <attribute name="{escape(name)}">{escape(str(value))}</attribute>'
            )
        lines.append(f"{indent}</{element}>")
        return lines

    # ----------------------------------------------------------------------- import

    def import_(self, source: ImportSource, context: ImportContext) -> ImportResult:
        result = ImportResult()
        name = next((n for n in source.names() if n.endswith(".xml")), None)
        if name is None:
            result.warnings.append("no .xml file in the archive; nothing imported")
            return result

        try:
            root = ET.fromstring(source.read(name).decode("utf-8"))
        except ET.ParseError as error:
            result.warnings.append(f"{name}: not valid XML ({error}); nothing imported")
            return result

        seen_labels = dict(context.known_labels)
        matched: set[int] = set()

        def ensure_label(label: str, where: str) -> bool:
            if label in seen_labels:
                return True
            if not context.create_missing_labels:
                result.warnings.append(f"{where}: unknown label {label!r}; skipped")
                return False
            seen_labels[label] = label
            result.new_labels.append(LabelSpec(id=label, name=label))
            return True

        # --- "for images": shapes nested under <image>
        for image in root.findall("image"):
            frame = self._frame_of(image, context, result)
            if frame is None:
                continue
            for child in image:
                if child.tag == "tag":
                    label = child.get("label", "")
                    if label and ensure_label(label, f"{name}: tag"):
                        result.tags.append((frame, label))
                    continue
                shape = self._shape_from(child, name, result)
                if shape is None:
                    continue
                if not ensure_label(shape.label, f"{name}: {child.tag}"):
                    continue
                result.shapes.append((frame, shape))
                matched.add(frame)

        # --- "for video": per-frame boxes nested under <track>
        for track in root.findall("track"):
            label = track.get("label", "")
            try:
                track_id = int(track.get("id", "0"))
            except ValueError:
                track_id = 0
            for child in track:
                if child.get("outside") == "1":
                    # The object leaving. It marks the end of a run rather than a position,
                    # so importing it would place a box where the object is not.
                    continue
                try:
                    frame = int(child.get("frame", "-1"))
                except ValueError:
                    continue
                if frame < 0 or frame >= context.frame_count:
                    result.warnings.append(
                        f"{name}: track {track_id} frame {frame} is outside this task; skipped"
                    )
                    continue
                shape = self._shape_from(child, name, result, label=label)
                if shape is None:
                    continue
                if not ensure_label(shape.label, f"{name}: track {track_id}"):
                    continue
                result.shapes.append((frame, replace(shape, track_id=track_id)))
                matched.add(frame)

        result.frames_matched = len(matched)
        return result

    @staticmethod
    def _frame_of(image: ET.Element, context: ImportContext, result: ImportResult) -> int | None:
        """Prefer the filename, fall back to the id.

        Matching on `name` is what makes an import land on the right frame when the task's
        media was uploaded in a different order from the export — which is the normal case
        when someone re-uploads their images alongside an annotation file.
        """
        filename = image.get("name", "")
        if filename in context.frame_by_name:
            return context.frame_by_name[filename]
        base = filename.rsplit("/", 1)[-1]
        if base in context.frame_by_name:
            return context.frame_by_name[base]
        try:
            index = int(image.get("id", "-1"))
        except ValueError:
            return None
        if 0 <= index < context.frame_count:
            return index
        result.warnings.append(f"no frame matches image {filename!r}; skipped")
        return None

    @staticmethod
    def _shape_from(
        element: ET.Element, source_name: str, result: ImportResult, *, label: str | None = None
    ) -> ShapeRecord | None:
        shape_type = FROM_XML.get(element.tag)
        if shape_type is None:
            return None

        mask: dict[str, Any] | None = None
        try:
            if shape_type is ShapeType.RECTANGLE:
                points = [float(element.get(key, "0")) for key in ("xtl", "ytl", "xbr", "ybr")]
            elif shape_type is ShapeType.ELLIPSE:
                points = [float(element.get(key, "0")) for key in ("cx", "cy", "rx", "ry")]
            elif shape_type is ShapeType.MASK:
                mask = _mask_from(element)
                if mask is None:
                    result.warnings.append(f"{source_name}: a mask carried no rle; skipped")
                    return None
                left, top, right, bottom = mask_bounds(mask)
                # The corners as well as the runs: bounds, hit-testing and the label chip all
                # read `points`, and a mask with none of them would be selectable nowhere.
                points = [float(left), float(top), float(right), float(bottom)]
            else:
                points = _parse_points(element.get("points", ""))
        except (ValueError, ValidationError):
            result.warnings.append(f"{source_name}: unreadable {element.tag} coordinates; skipped")
            return None

        if not points:
            return None

        attributes = {
            child.get("name", ""): (child.text or "")
            for child in element.findall("attribute")
            if child.get("name")
        }
        try:
            rotation = float(element.get("rotation", "0") or 0)
        except ValueError:
            rotation = 0.0

        return ShapeRecord(
            label=label or element.get("label", "") or "",
            shape_type=shape_type,
            points=points,
            rotation=rotation,
            occluded=element.get("occluded") == "1",
            z_order=int(element.get("z_order", "0") or 0),
            attributes=attributes,
            mask=mask,
        )


register(CvatXmlFormat())


__all__ = ["CvatXmlFormat"]
