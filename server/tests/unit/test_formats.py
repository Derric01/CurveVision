"""Dataset format plugins.

Every format is tested for a round trip through its own reader, so an exporter that
produces something its importer cannot read fails here rather than in a user's dataset.
"""

from __future__ import annotations

import json
import xml.etree.ElementTree as ET

import pytest

from curvevision.core.errors import UnsupportedFormatError
from curvevision.domain.enums import ShapeType
from curvevision.formats import (
    DatasetView,
    FrameRecord,
    ImportContext,
    LabelSpec,
    MemoryExportSink,
    MemoryImportSource,
    ShapeRecord,
    all_formats,
    get_format,
)


def sample_dataset() -> DatasetView:
    labels = [LabelSpec(id="1", name="car"), LabelSpec(id="2", name="pedestrian")]
    frames = [
        FrameRecord(
            index=0,
            name="frame_000.jpg",
            width=800,
            height=600,
            shapes=[
                ShapeRecord(
                    label="car",
                    shape_type=ShapeType.RECTANGLE,
                    points=[100.0, 100.0, 300.0, 250.0],
                ),
                ShapeRecord(
                    label="pedestrian",
                    shape_type=ShapeType.POLYGON,
                    points=[400.0, 200.0, 460.0, 200.0, 460.0, 400.0, 400.0, 400.0],
                ),
            ],
        ),
        FrameRecord(index=1, name="frame_001.jpg", width=800, height=600, shapes=[]),
    ]
    return DatasetView(name="Street Scenes", labels=labels, frames=iter(frames))


def import_context() -> ImportContext:
    return ImportContext(
        known_labels={},
        frame_by_name={"frame_000.jpg": 0, "frame_001.jpg": 1},
        frame_count=2,
        frame_sizes={0: (800, 600), 1: (800, 600)},
    )


class TestRegistry:
    def test_built_in_formats_are_registered(self) -> None:
        ids = {fmt.id for fmt in all_formats()}
        assert {"coco", "yolo", "voc", "curvevision"} <= ids

    def test_unknown_format_reports_what_is_available(self) -> None:
        with pytest.raises(UnsupportedFormatError) as excinfo:
            get_format("nonexistent")
        assert "coco" in str(excinfo.value)

    def test_every_format_declares_its_capabilities_honestly(self) -> None:
        for fmt in all_formats():
            assert fmt.capabilities.shape_types, f"{fmt.id} declares no shape types"
            assert fmt.extension
            assert fmt.version

    def test_capabilities_report_what_would_be_dropped(self) -> None:
        coco = get_format("coco")
        dropped = coco.capabilities.unsupported([ShapeType.RECTANGLE, ShapeType.POLYLINE])
        assert dropped == [ShapeType.POLYLINE]


class TestCoco:
    def test_export_produces_a_valid_coco_document(self) -> None:
        sink = MemoryExportSink()
        get_format("coco").export(sample_dataset(), sink)

        document = json.loads(sink.files["annotations/instances_default.json"])
        assert [category["name"] for category in document["categories"]] == [
            "car",
            "pedestrian",
        ]
        assert len(document["images"]) == 2
        assert len(document["annotations"]) == 2

        box = next(a for a in document["annotations"] if a["category_id"] == 1)
        assert box["bbox"] == [100.0, 100.0, 200.0, 150.0]
        assert box["area"] == pytest.approx(30000.0)

        polygon = next(a for a in document["annotations"] if a["category_id"] == 2)
        assert polygon["segmentation"] == [[400, 200, 460, 200, 460, 400, 400, 400]]
        assert polygon["area"] == pytest.approx(12000.0)

    def test_round_trip_preserves_geometry(self) -> None:
        sink = MemoryExportSink()
        coco = get_format("coco")
        coco.export(sample_dataset(), sink)

        result = coco.import_(MemoryImportSource(sink.files), import_context())

        assert result.frames_matched == 2
        assert {label.name for label in result.new_labels} == {"car", "pedestrian"}
        by_type = {shape.shape_type: shape for _, shape in result.shapes}
        assert by_type[ShapeType.RECTANGLE].points == [100.0, 100.0, 300.0, 250.0]
        assert by_type[ShapeType.POLYGON].points == [400, 200, 460, 200, 460, 400, 400, 400]

    def test_import_reports_images_it_cannot_match(self) -> None:
        source = MemoryImportSource(
            {
                "annotations/instances.json": json.dumps(
                    {
                        "images": [{"id": 1, "file_name": "unknown.jpg"}],
                        "annotations": [],
                        "categories": [{"id": 1, "name": "car"}],
                    }
                ).encode()
            }
        )
        result = get_format("coco").import_(source, import_context())
        assert result.frames_matched == 0
        assert any("unknown.jpg" in warning for warning in result.warnings)

    def test_import_of_a_malformed_document_does_not_raise(self) -> None:
        source = MemoryImportSource({"annotations.json": b"{not json"})
        result = get_format("coco").import_(source, import_context())
        assert result.shapes == []
        assert result.warnings


class TestYolo:
    def test_export_normalises_coordinates(self) -> None:
        sink = MemoryExportSink()
        get_format("yolo").export(sample_dataset(), sink)

        lines = sink.files["labels/default/frame_000.txt"].decode().strip().splitlines()
        assert len(lines) == 2

        class_index, cx, cy, w, h = lines[0].split()
        assert class_index == "0"
        # Box (100,100)-(300,250) in an 800x600 frame.
        assert float(cx) == pytest.approx(200 / 800, abs=1e-6)
        assert float(cy) == pytest.approx(175 / 600, abs=1e-6)
        assert float(w) == pytest.approx(200 / 800, abs=1e-6)
        assert float(h) == pytest.approx(150 / 600, abs=1e-6)

        assert b"names:" in sink.files["data.yaml"]

    def test_round_trip_recovers_pixel_coordinates(self) -> None:
        sink = MemoryExportSink()
        yolo = get_format("yolo")
        yolo.export(sample_dataset(), sink)

        result = yolo.import_(MemoryImportSource(sink.files), import_context())

        rectangles = [s for _, s in result.shapes if s.shape_type is ShapeType.RECTANGLE]
        assert len(rectangles) == 1
        assert rectangles[0].points == pytest.approx([100.0, 100.0, 300.0, 250.0], abs=0.5)

    def test_frames_without_dimensions_are_skipped_not_guessed(self) -> None:
        """Exporting with a guessed frame size would silently produce a bad dataset."""
        dataset = DatasetView(
            name="unknown-size",
            labels=[LabelSpec(id="1", name="car")],
            frames=iter(
                [
                    FrameRecord(
                        index=0,
                        name="a.jpg",
                        width=None,
                        height=None,
                        shapes=[
                            ShapeRecord(
                                label="car",
                                shape_type=ShapeType.RECTANGLE,
                                points=[0, 0, 10, 10],
                            )
                        ],
                    )
                ]
            ),
        )
        sink = MemoryExportSink()
        get_format("yolo").export(dataset, sink)

        assert "labels/default/a.txt" not in sink.files
        notes = json.loads(sink.files["curvevision_warnings.json"])
        assert notes["skipped_frames_missing_dimensions"] == ["a.jpg"]

    def test_flow_style_names_are_parsed(self) -> None:
        source = MemoryImportSource(
            {
                "data.yaml": b"names: [car, pedestrian]\n",
                "labels/train/frame_000.txt": b"1 0.5 0.5 0.25 0.25\n",
            }
        )
        result = get_format("yolo").import_(source, import_context())
        assert [shape.label for _, shape in result.shapes] == ["pedestrian"]

    def test_unparseable_line_is_reported_not_fatal(self) -> None:
        source = MemoryImportSource(
            {
                "classes.txt": b"car\n",
                "labels/train/frame_000.txt": b"0 0.5 0.5 0.2 0.2\nnonsense line\n",
            }
        )
        result = get_format("yolo").import_(source, import_context())
        assert len(result.shapes) == 1
        assert result.warnings


class TestPascalVoc:
    def test_export_produces_one_document_per_frame(self) -> None:
        sink = MemoryExportSink()
        get_format("voc").export(sample_dataset(), sink)

        assert "Annotations/frame_000.xml" in sink.files
        assert "Annotations/frame_001.xml" in sink.files

        root = ET.fromstring(sink.files["Annotations/frame_000.xml"].decode())
        assert root.findtext("size/width") == "800"
        objects = root.findall("object")
        # Only the rectangle survives; VOC has no polygon representation.
        assert len(objects) == 1
        assert objects[0].findtext("name") == "car"
        assert objects[0].findtext("bndbox/xmin") == "100.00"

    def test_round_trip_preserves_boxes(self) -> None:
        sink = MemoryExportSink()
        voc = get_format("voc")
        voc.export(sample_dataset(), sink)

        result = voc.import_(MemoryImportSource(sink.files), import_context())

        assert len(result.shapes) == 1
        frame, shape = result.shapes[0]
        assert frame == 0
        assert shape.label == "car"
        assert shape.points == [100.0, 100.0, 300.0, 250.0]

    def test_malformed_xml_is_reported_not_fatal(self) -> None:
        source = MemoryImportSource({"Annotations/a.xml": b"<annotation><unclosed>"})
        result = get_format("voc").import_(source, import_context())
        assert result.shapes == []
        assert result.warnings


class TestNativeFormat:
    def test_round_trip_is_lossless_for_every_field(self) -> None:
        labels = [LabelSpec(id="1", name="car", color="#ff0000")]
        original = ShapeRecord(
            label="car",
            shape_type=ShapeType.POLYLINE,
            points=[1.0, 2.0, 3.0, 4.0, 5.0, 6.0],
            rotation=12.5,
            occluded=True,
            z_order=3,
            group=7,
            track_id=42,
            attributes={"colour": "red"},
            source="model_corrected",
            confidence=0.87,
        )
        dataset = DatasetView(
            name="lossless",
            labels=labels,
            frames=iter(
                [
                    FrameRecord(
                        index=0,
                        name="frame_000.jpg",
                        width=800,
                        height=600,
                        shapes=[original],
                        tags=["daytime"],
                    )
                ]
            ),
        )

        sink = MemoryExportSink()
        native = get_format("curvevision")
        native.export(dataset, sink)
        result = native.import_(MemoryImportSource(sink.files), import_context())

        assert result.tags == [(0, "daytime")]
        _, restored = result.shapes[0]
        assert restored.shape_type is ShapeType.POLYLINE
        assert restored.points == original.points
        assert restored.rotation == original.rotation
        assert restored.occluded == original.occluded
        assert restored.z_order == original.z_order
        assert restored.group == original.group
        assert restored.track_id == original.track_id
        assert restored.attributes == original.attributes
        assert restored.source == original.source
        assert restored.confidence == original.confidence

    def test_supports_every_shape_type(self) -> None:
        capabilities = get_format("curvevision").capabilities
        assert set(capabilities.shape_types) == set(ShapeType)

    def test_schema_version_mismatch_is_warned_about(self) -> None:
        source = MemoryImportSource(
            {
                "dataset.json": json.dumps(
                    {"schema_version": "99.0", "labels": [], "frames": []}
                ).encode()
            }
        )
        result = get_format("curvevision").import_(source, import_context())
        assert any("99.0" in warning for warning in result.warnings)
