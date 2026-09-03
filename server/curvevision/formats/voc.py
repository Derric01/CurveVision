"""Pascal VOC detection format.

Independent implementation written from the published PASCAL VOC annotation schema.

Layout produced:

    Annotations/<name>.xml     one XML document per frame
    ImageSets/Main/default.txt frame list
    JPEGImages/<name>          (only when images are requested)
    labelmap.txt               class list

VOC represents axis-aligned bounding boxes and nothing else, so polygons, polylines, points
and skeletons are dropped. ``xml.etree.ElementTree`` is used for writing; parsing uses the
same module with entity resolution left at Python's safe defaults (it does not resolve
external entities), which is what matters for untrusted uploads.
"""

from __future__ import annotations

import xml.etree.ElementTree as ET
from xml.etree.ElementTree import ParseError

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


class PascalVocFormat:
    id = "voc"
    name = "Pascal VOC 1.1"
    version = "1.1"
    extension = "zip"
    capabilities = FormatCapabilities(
        shape_types=(ShapeType.RECTANGLE,),
        supports_import=True,
        supports_export=True,
        supports_tracks=False,
        supports_tags=False,
        supports_attributes=True,
        notes=(
            "Axis-aligned bounding boxes only. Polygons, polylines, points, ellipses and "
            "skeletons have no VOC representation and are dropped."
        ),
    )

    # ----------------------------------------------------------------------- export

    def export(self, dataset: DatasetView, sink: ExportSink) -> None:
        stems: list[str] = []
        for frame in dataset:
            base = frame.name.rsplit("/", 1)[-1]
            stem = base.rsplit(".", 1)[0]
            stems.append(stem)

            root = ET.Element("annotation")
            ET.SubElement(root, "folder").text = "JPEGImages"
            ET.SubElement(root, "filename").text = base
            source = ET.SubElement(root, "source")
            ET.SubElement(source, "database").text = dataset.name

            size = ET.SubElement(root, "size")
            ET.SubElement(size, "width").text = str(frame.width or 0)
            ET.SubElement(size, "height").text = str(frame.height or 0)
            ET.SubElement(size, "depth").text = "3"
            ET.SubElement(root, "segmented").text = "0"

            for shape in frame.shapes:
                if shape.shape_type not in (
                    ShapeType.RECTANGLE,
                    ShapeType.ROTATED_RECTANGLE,
                ):
                    continue
                x1, y1, x2, y2 = normalise_rectangle(shape.points)
                obj = ET.SubElement(root, "object")
                ET.SubElement(obj, "name").text = shape.label
                ET.SubElement(obj, "pose").text = "Unspecified"
                ET.SubElement(obj, "truncated").text = "0"
                ET.SubElement(obj, "occluded").text = "1" if shape.occluded else "0"
                ET.SubElement(obj, "difficult").text = "0"
                box = ET.SubElement(obj, "bndbox")
                ET.SubElement(box, "xmin").text = f"{x1:.2f}"
                ET.SubElement(box, "ymin").text = f"{y1:.2f}"
                ET.SubElement(box, "xmax").text = f"{x2:.2f}"
                ET.SubElement(box, "ymax").text = f"{y2:.2f}"
                if shape.attributes:
                    attributes = ET.SubElement(obj, "attributes")
                    for name, value in shape.attributes.items():
                        node = ET.SubElement(attributes, "attribute")
                        ET.SubElement(node, "name").text = str(name)
                        ET.SubElement(node, "value").text = str(value)

            sink.write(
                f"Annotations/{stem}.xml",
                ET.tostring(root, encoding="unicode", xml_declaration=True),
            )
            if frame.media is not None:
                sink.write(f"JPEGImages/{base}", frame.media)

        sink.write("ImageSets/Main/default.txt", "\n".join(stems) + "\n")
        sink.write(
            "labelmap.txt",
            "\n".join(f"{label.name}:{label.color}::" for label in dataset.labels) + "\n",
        )

    # ----------------------------------------------------------------------- import

    def import_(self, source: ImportSource, context: ImportContext) -> ImportResult:
        result = ImportResult()
        seen_labels: set[str] = set()
        matched: set[int] = set()

        xml_files = [name for name in source.names() if name.lower().endswith(".xml")]
        if not xml_files:
            result.warnings.append("No VOC XML annotation files found in the archive")
            return result

        for path in xml_files:
            try:
                root = ET.fromstring(source.read(path).decode("utf-8", errors="replace"))
            except ParseError as exc:
                result.warnings.append(f"{path}: malformed XML ({exc})")
                continue

            filename = (root.findtext("filename") or "").strip()
            stem = (filename or path.rsplit("/", 1)[-1]).rsplit(".", 1)[0]
            frame = self._match_frame(filename, stem, context)
            if frame is None:
                result.warnings.append(f"No frame matches {path!r}; skipped")
                continue
            matched.add(frame)

            for obj in root.findall("object"):
                name = (obj.findtext("name") or "").strip()
                box = obj.find("bndbox")
                if not name or box is None:
                    continue
                try:
                    x1 = float(box.findtext("xmin") or 0)
                    y1 = float(box.findtext("ymin") or 0)
                    x2 = float(box.findtext("xmax") or 0)
                    y2 = float(box.findtext("ymax") or 0)
                except ValueError:
                    result.warnings.append(f"{path}: object {name!r} has an unreadable bndbox")
                    continue

                if name not in seen_labels:
                    seen_labels.add(name)
                    if name not in context.known_labels and context.create_missing_labels:
                        result.new_labels.append(LabelSpec(id=name, name=name))

                attributes: dict[str, str] = {}
                for node in obj.findall("attributes/attribute"):
                    key = (node.findtext("name") or "").strip()
                    if key:
                        attributes[key] = (node.findtext("value") or "").strip()

                result.shapes.append(
                    (
                        frame,
                        ShapeRecord(
                            label=name,
                            shape_type=ShapeType.RECTANGLE,
                            points=[x1, y1, x2, y2],
                            occluded=(obj.findtext("occluded") or "0").strip() == "1",
                            attributes=attributes,
                            source="imported",
                        ),
                    )
                )

        result.frames_matched = len(matched)
        return result

    @staticmethod
    def _match_frame(filename: str, stem: str, context: ImportContext) -> int | None:
        if filename and filename in context.frame_by_name:
            return context.frame_by_name[filename]
        for name, frame in context.frame_by_name.items():
            base = name.rsplit("/", 1)[-1]
            if base == filename or base.rsplit(".", 1)[0] == stem:
                return frame
        return None


register(PascalVocFormat())

__all__ = ["PascalVocFormat"]
