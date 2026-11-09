"""CurveVision native format -- lossless.

Every other format loses something. This one is the escape hatch: a full-fidelity JSON
document that round-trips every geometry type, attribute, track, tag, group, z-order,
provenance and confidence value CurveVision can represent.

Until iteration 72 three of those were written and not read back, so a backup restored
less than it held: provenance and confidence were dropped on import (a model's pending
suggestion came back as finished work), a tag was written as its label alone, and every
frame of a track came back as a keyframe. A tag is now an object, `{label, attributes,
source, confidence}` (a bare label, as older documents have, still reads), and a track's
shape says whether its frame was a `keyframe`.

Use it for backups, for moving a dataset between CurveVision instances, and as the
reference when adding a new format (a new format's round-trip test compares against this).
"""

from __future__ import annotations

import json
from typing import Any

from curvevision.domain.enums import AttributeType, ShapeType
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
    TagRecord,
)
from curvevision.formats.registry import register

SCHEMA_VERSION = "1.0"


class CurveVisionFormat:
    id = "curvevision"
    name = "CurveVision JSON"
    version = SCHEMA_VERSION
    extension = "zip"
    capabilities = FormatCapabilities(
        shape_types=tuple(ShapeType),
        supports_import=True,
        supports_export=True,
        supports_tracks=True,
        supports_tags=True,
        supports_attributes=True,
        notes="Lossless. Every geometry type, attribute, tag and provenance flag round-trips.",
    )

    # ----------------------------------------------------------------------- export

    def export(self, dataset: DatasetView, sink: ExportSink) -> None:
        frames: list[dict[str, Any]] = []
        for frame in dataset:
            frames.append(
                {
                    "index": frame.index,
                    "name": frame.name,
                    "width": frame.width,
                    "height": frame.height,
                    "subset": frame.subset,
                    # Which image this is, by content: what lets a frozen copy of the
                    # annotations find its pictures again (`read_view`).
                    "media_sha256": frame.media_sha256,
                    "tags": [
                        {
                            "label": tag.label,
                            "attributes": dict(tag.attributes),
                            "source": tag.source,
                            "confidence": tag.confidence,
                        }
                        for tag in _tags_of(frame)
                    ],
                    "shapes": [self._shape_to_json(shape) for shape in frame.shapes],
                }
            )
            if frame.has_media:
                sink.write_media(f"data/{frame.name}", frame)

        document = {
            "schema": "curvevision/dataset",
            "schema_version": SCHEMA_VERSION,
            "name": dataset.name,
            "metadata": dataset.metadata,
            "labels": [self._label_to_json(label) for label in dataset.labels],
            "frames": frames,
        }
        sink.write("dataset.json", json.dumps(document, indent=2, default=str))

    @staticmethod
    def _label_to_json(label: LabelSpec) -> dict[str, Any]:
        return {
            "name": label.name,
            "color": label.color,
            "keypoints": list(label.keypoints),
            "skeleton_edges": [list(edge) for edge in label.skeleton_edges],
            "attributes": [
                {
                    "name": attribute.name,
                    "type": attribute.attribute_type.value,
                    "values": list(attribute.values),
                    "mutable": attribute.mutable,
                }
                for attribute in label.attributes
            ],
        }

    @staticmethod
    def _shape_to_json(shape: ShapeRecord) -> dict[str, Any]:
        return {
            "label": shape.label,
            "type": shape.shape_type.value,
            "points": list(shape.points),
            "rotation": shape.rotation,
            "occluded": shape.occluded,
            "z_order": shape.z_order,
            "group": shape.group,
            "track_id": shape.track_id,
            "attributes": dict(shape.attributes),
            "source": shape.source,
            "confidence": shape.confidence,
            "mask": shape.mask,
            "elements": list(shape.elements),
            **({"keyframe": shape.keyframe} if shape.track_id is not None else {}),
        }

    # ----------------------------------------------------------------------- import

    def import_(self, source: ImportSource, context: ImportContext) -> ImportResult:
        # Every source and confidence here was written from an annotation's own.
        result = ImportResult(carries_provenance=True)
        path = next(
            (name for name in source.names() if name.rsplit("/", 1)[-1] == "dataset.json"),
            None,
        )
        if path is None:
            result.warnings.append("No dataset.json found in the archive")
            return result

        try:
            document = json.loads(source.read(path))
        except json.JSONDecodeError as exc:
            result.warnings.append(f"{path}: not valid JSON ({exc})")
            return result

        schema_version = str(document.get("schema_version", "0"))
        if schema_version.split(".")[0] != SCHEMA_VERSION.split(".")[0]:
            result.warnings.append(
                f"Document uses schema version {schema_version}; this server writes "
                f"{SCHEMA_VERSION}. Import proceeded, but check the result."
            )

        for label in document.get("labels", []):
            spec = _label_spec(label)
            if spec is None or spec.name in context.known_labels:
                continue
            if context.create_missing_labels:
                result.new_labels.append(spec)

        matched: set[int] = set()
        for frame_doc in document.get("frames", []):
            frame = self._match_frame(frame_doc, context)
            if frame is None:
                result.warnings.append(
                    f"No frame matches {frame_doc.get('name')!r}; annotations skipped"
                )
                continue
            matched.add(frame)

            for tag in frame_doc.get("tags", []):
                if (record := _tag_record(tag)) is not None:
                    result.tags.append((frame, record))

            for shape_doc in frame_doc.get("shapes", []):
                shape = _shape_record(shape_doc)
                if shape is None:
                    result.warnings.append(f"Unknown shape type {shape_doc.get('type')!r}; skipped")
                    continue
                result.shapes.append((frame, shape))

        result.frames_matched = len(matched)
        return result

    @staticmethod
    def _match_frame(frame_doc: dict[str, Any], context: ImportContext) -> int | None:
        name = str(frame_doc.get("name", ""))
        if name in context.frame_by_name:
            return context.frame_by_name[name]
        base = name.rsplit("/", 1)[-1]
        for candidate, frame in context.frame_by_name.items():
            if candidate.rsplit("/", 1)[-1] == base:
                return frame
        # Fall back to the recorded index when names do not line up, which happens when a
        # dataset is re-imported into a task whose media was uploaded under new filenames.
        index = frame_doc.get("index")
        if isinstance(index, int) and 0 <= index < context.frame_count:
            return index
        return None


def read_view(document: dict[str, Any]) -> DatasetView:
    """The dataset a `dataset.json` describes, as a view any format can export.

    The inverse of `CurveVisionFormat.export`, losslessly: it is how a released dataset
    version, frozen in this format, is exported later in any other. Frames carry no image
    bytes; `media_sha256` says which image each was, for a caller that can fetch it.
    """
    labels = [spec for label in document.get("labels", []) if (spec := _label_spec(label))]
    frames: list[FrameRecord] = []
    for position, frame in enumerate(document.get("frames", [])):
        tag_records = [tag for doc in frame.get("tags", []) if (tag := _tag_record(doc))]
        frames.append(
            FrameRecord(
                index=int(frame.get("index", position)),
                name=str(frame.get("name", "")),
                width=frame.get("width"),
                height=frame.get("height"),
                subset=str(frame.get("subset") or "default"),
                tags=[tag.label for tag in tag_records],
                tag_records=tag_records,
                shapes=[shape for doc in frame.get("shapes", []) if (shape := _shape_record(doc))],
                media_sha256=frame.get("media_sha256"),
            )
        )
    return DatasetView(
        name=str(document.get("name", "")),
        labels=labels,
        frames=frames,
        metadata=dict(document.get("metadata") or {}),
    )


def _label_spec(label: dict[str, Any]) -> LabelSpec | None:
    name = str(label.get("name", "")).strip()
    if not name:
        return None
    return LabelSpec(
        id=name,
        name=name,
        color=str(label.get("color", "#38bdf8")),
        keypoints=tuple(label.get("keypoints", ())),
        skeleton_edges=tuple(
            (int(edge[0]), int(edge[1]))
            for edge in label.get("skeleton_edges", [])
            if len(edge) == 2
        ),
        attributes=tuple(_attribute_spec(attribute) for attribute in label.get("attributes", [])),
    )


def _shape_record(shape_doc: dict[str, Any]) -> ShapeRecord | None:
    try:
        shape_type = ShapeType(shape_doc["type"])
    except (KeyError, ValueError):
        return None
    return ShapeRecord(
        label=str(shape_doc.get("label", "")),
        shape_type=shape_type,
        points=[float(value) for value in shape_doc.get("points", [])],
        rotation=float(shape_doc.get("rotation", 0.0)),
        occluded=bool(shape_doc.get("occluded", False)),
        z_order=int(shape_doc.get("z_order", 0)),
        group=shape_doc.get("group"),
        track_id=shape_doc.get("track_id"),
        attributes=dict(shape_doc.get("attributes", {})),
        source=str(shape_doc.get("source", "imported")),
        confidence=shape_doc.get("confidence"),
        mask=shape_doc.get("mask"),
        elements=list(shape_doc.get("elements", [])),
        keyframe=bool(shape_doc.get("keyframe", True)),
    )


def _tags_of(frame: FrameRecord) -> list[TagRecord]:
    """A frame's tags in full; one built with labels only gets them with nothing else."""
    if frame.tag_records is not None:
        return frame.tag_records
    return [TagRecord(label=label) for label in frame.tags]


def _tag_record(doc: Any) -> TagRecord | None:
    """A tag as this format writes it, or as a bare label (documents before iteration 72)."""
    if isinstance(doc, str):
        return TagRecord(label=doc, source="imported") if doc else None
    if not isinstance(doc, dict) or not str(doc.get("label") or ""):
        return None
    confidence = doc.get("confidence")
    return TagRecord(
        label=str(doc["label"]),
        attributes=dict(doc.get("attributes") or {}),
        source=str(doc.get("source") or "imported"),
        confidence=float(confidence) if isinstance(confidence, int | float) else None,
    )


def _attribute_spec(attribute: dict[str, Any]) -> Any:
    from curvevision.formats.base import AttributeSpec

    try:
        attribute_type = AttributeType(attribute.get("type", "text"))
    except ValueError:
        attribute_type = AttributeType.TEXT
    return AttributeSpec(
        name=str(attribute.get("name", "")),
        attribute_type=attribute_type,
        values=tuple(attribute.get("values", ())),
        mutable=bool(attribute.get("mutable", False)),
    )


register(CurveVisionFormat())

__all__ = ["SCHEMA_VERSION", "CurveVisionFormat", "read_view"]
