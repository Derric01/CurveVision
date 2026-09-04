"""COCO detection/segmentation/keypoints format.

Independent implementation written from the published COCO dataset specification
(https://cocodataset.org/#format-data). No code from any other project is used.

What survives a round trip: rectangles (as ``bbox``), polygons (as ``segmentation``), and
skeletons (as ``keypoints``). Polylines and points have no COCO representation and are
reported as dropped before the export runs.
"""

from __future__ import annotations

import json
from typing import Any

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
    polygon_area,
    rectangle_to_polygon,
)
from curvevision.formats.registry import register

logger = get_logger(__name__)


class CocoFormat:
    id = "coco"
    name = "COCO 1.0"
    version = "1.0"
    extension = "zip"
    capabilities = FormatCapabilities(
        shape_types=(ShapeType.RECTANGLE, ShapeType.POLYGON, ShapeType.SKELETON),
        supports_import=True,
        supports_export=True,
        supports_tracks=False,
        supports_tags=False,
        supports_attributes=True,
        notes=(
            "Polylines, points and ellipses have no COCO representation and are omitted. "
            "Attributes are exported into a non-standard `attributes` key that COCO "
            "readers ignore."
        ),
    )

    # ----------------------------------------------------------------------- export

    def export(self, dataset: DatasetView, sink: ExportSink) -> None:
        categories: list[dict[str, Any]] = []
        category_ids: dict[str, int] = {}
        for index, label in enumerate(dataset.labels, start=1):
            category_ids[label.name] = index
            category: dict[str, Any] = {
                "id": index,
                "name": label.name,
                "supercategory": "",
            }
            if label.keypoints:
                category["keypoints"] = list(label.keypoints)
                # COCO skeleton edges are 1-based vertex pairs.
                category["skeleton"] = [[a + 1, b + 1] for a, b in label.skeleton_edges]
            categories.append(category)

        images: list[dict[str, Any]] = []
        annotations: list[dict[str, Any]] = []
        annotation_id = 1

        for frame in dataset:
            image_id = frame.index + 1
            images.append(
                {
                    "id": image_id,
                    "file_name": frame.name,
                    "width": frame.width or 0,
                    "height": frame.height or 0,
                }
            )
            if frame.media is not None:
                sink.write(f"images/{frame.name}", frame.media)

            for shape in frame.shapes:
                entry = self._annotation_entry(shape, image_id, annotation_id, category_ids)
                if entry is None:
                    continue
                annotations.append(entry)
                annotation_id += 1

        document = {
            "info": {
                "description": dataset.name,
                "version": "1.0",
                "contributor": "CurveVision",
            },
            "licenses": [],
            "images": images,
            "annotations": annotations,
            "categories": categories,
        }
        sink.write("annotations/instances_default.json", json.dumps(document, indent=1))

    def _annotation_entry(
        self,
        shape: ShapeRecord,
        image_id: int,
        annotation_id: int,
        category_ids: dict[str, int],
    ) -> dict[str, Any] | None:
        category_id = category_ids.get(shape.label)
        if category_id is None:
            return None

        entry: dict[str, Any] = {
            "id": annotation_id,
            "image_id": image_id,
            "category_id": category_id,
            "iscrowd": 0,
            "attributes": {"occluded": shape.occluded, **shape.attributes},
        }

        match shape.shape_type:
            case ShapeType.RECTANGLE | ShapeType.ROTATED_RECTANGLE:
                x1, y1, x2, y2 = normalise_rectangle(shape.points)
                entry["bbox"] = [x1, y1, x2 - x1, y2 - y1]
                entry["area"] = (x2 - x1) * (y2 - y1)
                entry["segmentation"] = []
                if shape.shape_type is ShapeType.ROTATED_RECTANGLE:
                    entry["attributes"]["rotation"] = shape.rotation
            case ShapeType.POLYGON:
                x, y, width, height = bounding_box(shape.points)
                entry["segmentation"] = [list(shape.points)]
                entry["bbox"] = [x, y, width, height]
                entry["area"] = polygon_area(shape.points)
            case ShapeType.SKELETON:
                keypoints: list[float] = []
                visible = 0
                for element in shape.elements:
                    points = element.get("points", [0.0, 0.0])
                    # COCO visibility: 0 absent, 1 labelled but hidden, 2 labelled+visible.
                    if element.get("outside"):
                        visibility = 0
                    elif element.get("occluded"):
                        visibility = 1
                    else:
                        visibility = 2
                        visible += 1
                    keypoints.extend([points[0], points[1], visibility])
                x, y, width, height = bounding_box(
                    [coord for i, coord in enumerate(keypoints) if i % 3 != 2]
                )
                entry["keypoints"] = keypoints
                entry["num_keypoints"] = visible
                entry["bbox"] = [x, y, width, height]
                entry["area"] = width * height
                entry["segmentation"] = []
            case _:
                # Not representable; the caller was warned by `capabilities.unsupported`.
                return None

        return entry

    # ----------------------------------------------------------------------- import

    def import_(self, source: ImportSource, context: ImportContext) -> ImportResult:
        result = ImportResult()
        annotation_files = [
            name
            for name in source.names()
            if name.endswith(".json") and "annotation" in name.lower()
        ] or [name for name in source.names() if name.endswith(".json")]

        if not annotation_files:
            result.warnings.append("No COCO JSON file found in the archive")
            return result

        for path in annotation_files:
            try:
                document = json.loads(source.read(path))
            except json.JSONDecodeError as exc:
                result.warnings.append(f"{path}: not valid JSON ({exc})")
                continue
            self._import_document(document, context, result)
        return result

    def _import_document(
        self, document: dict[str, Any], context: ImportContext, result: ImportResult
    ) -> None:
        categories = {int(category["id"]): category for category in document.get("categories", [])}
        for category in categories.values():
            name = str(category["name"])
            if name not in context.known_labels and context.create_missing_labels:
                result.new_labels.append(
                    LabelSpec(
                        id=name,
                        name=name,
                        keypoints=tuple(category.get("keypoints", ())),
                        skeleton_edges=tuple(
                            (a - 1, b - 1) for a, b in category.get("skeleton", [])
                        ),
                    )
                )

        frame_by_image: dict[int, int] = {}
        for image in document.get("images", []):
            file_name = str(image.get("file_name", ""))
            frame = context.frame_by_name.get(file_name)
            if frame is None:
                # Fall back to the basename: exporters vary on whether they include a
                # directory prefix, and matching on that alone would drop everything.
                frame = context.frame_by_name.get(file_name.rsplit("/", 1)[-1])
            if frame is None:
                result.warnings.append(f"No frame matches image {file_name!r}; skipped")
                continue
            frame_by_image[int(image["id"])] = frame

        result.frames_matched = len(frame_by_image)

        for annotation in document.get("annotations", []):
            frame = frame_by_image.get(int(annotation.get("image_id", -1)))
            if frame is None:
                continue
            category = categories.get(int(annotation.get("category_id", -1)))
            if category is None:
                continue
            label = str(category["name"])
            attributes = {
                key: value
                for key, value in (annotation.get("attributes") or {}).items()
                if key not in ("occluded", "rotation")
            }
            occluded = bool((annotation.get("attributes") or {}).get("occluded", False))

            segmentation = annotation.get("segmentation") or []
            keypoints = annotation.get("keypoints") or []

            if keypoints and category.get("keypoints"):
                elements = []
                names = category["keypoints"]
                for i, name in enumerate(names):
                    x, y, visibility = keypoints[i * 3 : i * 3 + 3]
                    elements.append(
                        {
                            "name": name,
                            "points": [float(x), float(y)],
                            "occluded": visibility == 1,
                            "outside": visibility == 0,
                        }
                    )
                result.shapes.append(
                    (
                        frame,
                        ShapeRecord(
                            label=label,
                            shape_type=ShapeType.SKELETON,
                            points=[],
                            elements=elements,
                            attributes=attributes,
                            occluded=occluded,
                            source="imported",
                        ),
                    )
                )
            elif (
                isinstance(segmentation, list)
                and segmentation
                and isinstance(segmentation[0], list)
            ):
                for polygon in segmentation:
                    if len(polygon) < 6:
                        continue
                    result.shapes.append(
                        (
                            frame,
                            ShapeRecord(
                                label=label,
                                shape_type=ShapeType.POLYGON,
                                points=[float(value) for value in polygon],
                                attributes=attributes,
                                occluded=occluded,
                                source="imported",
                            ),
                        )
                    )
            elif bbox := annotation.get("bbox"):
                x, y, width, height = (float(value) for value in bbox[:4])
                result.shapes.append(
                    (
                        frame,
                        ShapeRecord(
                            label=label,
                            shape_type=ShapeType.RECTANGLE,
                            points=[x, y, x + width, y + height],
                            attributes=attributes,
                            occluded=occluded,
                            source="imported",
                        ),
                    )
                )
            else:
                result.warnings.append(
                    f"Annotation {annotation.get('id')} has no usable geometry; skipped"
                )


register(CocoFormat())


__all__ = ["CocoFormat", "rectangle_to_polygon"]
