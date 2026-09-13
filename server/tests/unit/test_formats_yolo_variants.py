"""The Ultralytics YOLO task variants, and the rotation bug that started them.

`yolo.py` already covered detection and segmentation. Writing the oriented-box exporter
surfaced that the detection one was **silently discarding rotation**: a rotated rectangle's
stored points are the *unrotated* box, and using them directly claims a 100x20 extent for a
bar that, turned 90 degrees, occupies 20x100. Worse, the format declared rotated rectangles
unsupported, so the export warning told the user they had been dropped while they were being
written wrong. Both halves are pinned here.
"""

from __future__ import annotations

import pytest

from curvevision.domain.enums import ShapeType
from curvevision.formats import (
    DatasetView,
    FrameRecord,
    ImportContext,
    LabelSpec,
    MemoryExportSink,
    MemoryImportSource,
    ShapeRecord,
    get_format,
)
from curvevision.formats.base import bounding_box, rotated_corners


def text(sink: MemoryExportSink, path: str) -> str:
    return sink.files[path].decode("utf-8")


def only_label_file(sink: MemoryExportSink) -> str:
    path = next(name for name in sink.files if name.startswith("labels/"))
    return text(sink, path)


def dataset(frames: list[FrameRecord], labels: list[LabelSpec] | None = None) -> DatasetView:
    return DatasetView(
        name="Scene",
        labels=labels or [LabelSpec(id="1", name="bar")],
        frames=iter(frames),
    )


def frame(
    shapes: list[ShapeRecord],
    *,
    tags: list[str] | None = None,
    media: bytes | None = None,
    width: int = 200,
    height: int = 200,
) -> FrameRecord:
    return FrameRecord(
        index=0,
        name="a.jpg",
        width=width,
        height=height,
        shapes=shapes,
        tags=tags or [],
        media=media,
    )


#: A 100x20 bar centred in a 200x200 frame. Rotated 90 degrees it occupies 20x100.
def bar(rotation: float = 90.0) -> ShapeRecord:
    return ShapeRecord(
        label="bar",
        shape_type=ShapeType.ROTATED_RECTANGLE,
        points=[50.0, 90.0, 150.0, 110.0],
        rotation=rotation,
    )


class TestRotatedCorners:
    def test_no_rotation_returns_the_box_corners(self) -> None:
        corners = rotated_corners([10.0, 20.0, 30.0, 40.0], 0.0)
        assert [round(v, 6) for v in corners] == [10, 20, 30, 20, 30, 40, 10, 40]

    def test_a_quarter_turn_swaps_the_extent(self) -> None:
        x, y, width, height = bounding_box(rotated_corners([50.0, 90.0, 150.0, 110.0], 90.0))
        assert (round(width), round(height)) == (20, 100)
        assert (round(x + width / 2), round(y + height / 2)) == (100, 100)

    def test_the_centre_never_moves(self) -> None:
        for angle in (0, 17, 45, 90, 180, 270, 359):
            corners = rotated_corners([50.0, 90.0, 150.0, 110.0], float(angle))
            x, y, width, height = bounding_box(corners)
            assert (round(x + width / 2), round(y + height / 2)) == (100, 100), angle

    def test_a_half_turn_restores_the_original_extent(self) -> None:
        _x, _y, width, height = bounding_box(rotated_corners([50.0, 90.0, 150.0, 110.0], 180.0))
        assert (round(width), round(height)) == (100, 20)


class TestYoloRotationBug:
    def test_a_rotated_box_exports_its_real_extent(self) -> None:
        """The bug. A 90-degree bar is 20 wide and 100 tall, not the other way round."""
        sink = MemoryExportSink()
        get_format("yolo").export(dataset([frame([bar()])]), sink)

        _cls, cx, cy, width, height = only_label_file(sink).split()
        assert (round(float(width) * 200), round(float(height) * 200)) == (20, 100)
        assert (round(float(cx) * 200), round(float(cy) * 200)) == (100, 100)

    def test_an_unrotated_box_is_unchanged(self) -> None:
        sink = MemoryExportSink()
        get_format("yolo").export(dataset([frame([bar(rotation=0.0)])]), sink)

        _cls, _cx, _cy, width, height = only_label_file(sink).split()
        assert (round(float(width) * 200), round(float(height) * 200)) == (100, 20)

    def test_the_capabilities_no_longer_claim_it_is_dropped(self) -> None:
        """The second half of the bug: the warning said the opposite of what happened."""
        capabilities = get_format("yolo").capabilities
        assert ShapeType.ROTATED_RECTANGLE in capabilities.shape_types
        assert capabilities.unsupported([ShapeType.ROTATED_RECTANGLE]) == []
        assert "yolo_obb" in (capabilities.notes or ""), (
            "it should point at the format that keeps the angle"
        )


class TestYoloObb:
    def test_four_corners_preserve_the_angle(self) -> None:
        sink = MemoryExportSink()
        get_format("yolo_obb").export(dataset([frame([bar()])]), sink)

        parts = only_label_file(sink).split()
        assert len(parts) == 9, "a class and four xy pairs"
        pixels = [float(v) * 200 for v in parts[1:]]
        _x, _y, width, height = bounding_box(pixels)
        assert (round(width), round(height)) == (20, 100)

    def test_an_axis_aligned_rectangle_becomes_its_own_corners(self) -> None:
        box = ShapeRecord(
            label="bar", shape_type=ShapeType.RECTANGLE, points=[20.0, 40.0, 120.0, 60.0]
        )
        sink = MemoryExportSink()
        get_format("yolo_obb").export(dataset([frame([box])]), sink)

        pixels = [float(v) * 200 for v in only_label_file(sink).split()[1:]]
        assert [round(v) for v in pixels] == [20, 40, 120, 40, 120, 60, 20, 60]

    def test_round_trip_recovers_the_rotated_rectangle(self) -> None:
        obb = get_format("yolo_obb")
        sink = MemoryExportSink()
        obb.export(dataset([frame([bar()])]), sink)

        result = obb.import_(
            MemoryImportSource(sink.files),
            ImportContext(
                known_labels={},
                frame_by_name={"a.jpg": 0},
                frame_count=1,
                frame_sizes={0: (200, 200)},
            ),
        )

        assert len(result.shapes) == 1
        _index, shape = result.shapes[0]
        assert shape.shape_type is ShapeType.ROTATED_RECTANGLE
        x, y, width, height = bounding_box(rotated_corners(shape.points, shape.rotation))
        assert (round(width), round(height)) == (20, 100), "the angle survived"
        assert (round(x + width / 2), round(y + height / 2)) == (100, 100)

    def test_a_non_rectangular_quad_comes_back_as_a_polygon(self) -> None:
        """Squaring off a four-sided region would quietly move somebody's annotation."""
        files = {
            "data.yaml": b"names:\n  0: bar\n",
            "labels/train/a.txt": b"0 0.1 0.1 0.9 0.2 0.8 0.9 0.05 0.7\n",
        }
        result = get_format("yolo_obb").import_(
            MemoryImportSource(files),
            ImportContext(
                known_labels={},
                frame_by_name={"a.jpg": 0},
                frame_count=1,
                frame_sizes={0: (200, 200)},
            ),
        )

        assert len(result.shapes) == 1
        assert result.shapes[0][1].shape_type is ShapeType.POLYGON

    def test_a_wrong_width_line_is_reported_not_guessed(self) -> None:
        files = {"data.yaml": b"names:\n  0: bar\n", "labels/train/a.txt": b"0 0.1 0.1 0.9\n"}
        result = get_format("yolo_obb").import_(
            MemoryImportSource(files),
            ImportContext(
                known_labels={},
                frame_by_name={"a.jpg": 0},
                frame_count=1,
                frame_sizes={0: (200, 200)},
            ),
        )
        assert result.shapes == []
        assert any("8 coordinates" in warning for warning in result.warnings)


def skeleton(label: str = "person", *, missing: bool = False) -> ShapeRecord:
    elements = [
        {"points": [100.0, 40.0]},
        {"points": [100.0, 80.0], "occluded": True},
        {"points": [120.0, 120.0]},
    ]
    if missing:
        elements = elements[:2]
    return ShapeRecord(
        label=label, shape_type=ShapeType.SKELETON, points=[100.0, 40.0], elements=elements
    )


def person_label() -> LabelSpec:
    return LabelSpec(id="1", name="person", keypoints=("head", "shoulder", "hand"))


class TestYoloPose:
    def test_a_line_carries_a_box_then_three_values_per_keypoint(self) -> None:
        sink = MemoryExportSink()
        get_format("yolo_pose").export(dataset([frame([skeleton()])], [person_label()]), sink)

        parts = only_label_file(sink).split()
        assert len(parts) == 1 + 4 + 3 * 3
        assert parts[0] == "0"

    def test_visibility_follows_occluded_and_outside(self) -> None:
        sink = MemoryExportSink()
        get_format("yolo_pose").export(dataset([frame([skeleton()])], [person_label()]), sink)

        parts = only_label_file(sink).split()
        visibilities = [parts[5 + i * 3 + 2] for i in range(3)]
        assert visibilities == ["2", "1", "2"], "visible, occluded, visible"

    def test_a_missing_joint_is_padded_not_dropped(self) -> None:
        """A short line shifts every later value into the wrong joint."""
        sink = MemoryExportSink()
        get_format("yolo_pose").export(
            dataset([frame([skeleton(missing=True)])], [person_label()]), sink
        )

        parts = only_label_file(sink).split()
        assert len(parts) == 1 + 4 + 3 * 3, "still three keypoints wide"
        assert parts[-1] == "0", "the absent joint is marked invisible"

    def test_the_box_wraps_only_the_located_keypoints(self) -> None:
        sink = MemoryExportSink()
        get_format("yolo_pose").export(dataset([frame([skeleton()])], [person_label()]), sink)
        _cls, _cx, _cy, width, height = only_label_file(sink).split()[:5]
        # Keypoints span x 100..120, y 40..120.
        assert round(float(width) * 200) == 20
        assert round(float(height) * 200) == 80

    def test_data_yaml_declares_the_keypoint_shape(self) -> None:
        """Ultralytics needs `kpt_shape` to size the model's pose head."""
        sink = MemoryExportSink()
        get_format("yolo_pose").export(dataset([frame([skeleton()])], [person_label()]), sink)
        assert "kpt_shape: [3, 3]" in text(sink, "data.yaml")

    def test_a_project_with_no_keypoints_says_so_rather_than_writing_nothing(self) -> None:
        sink = MemoryExportSink()
        get_format("yolo_pose").export(dataset([frame([])]), sink)
        assert "No label in this project declares keypoints" in text(sink, "README.txt")

    def test_import_is_refused_with_a_reason(self) -> None:
        result = get_format("yolo_pose").import_(
            MemoryImportSource({}),
            ImportContext(known_labels={}, frame_by_name={}, frame_count=0),
        )
        assert result.shapes == []
        assert any("not what they are called" in w for w in result.warnings)
        assert get_format("yolo_pose").capabilities.supports_import is False


class TestYoloClassification:
    def test_the_directory_tree_is_the_annotation(self) -> None:
        sink = MemoryExportSink()
        get_format("yolo_classification").export(
            dataset([frame([], tags=["daytime"], media=b"JPEGBYTES")]), sink
        )
        assert sink.files["images/default/daytime/a.jpg"] == b"JPEGBYTES"

    def test_two_tags_on_one_frame_are_reported_not_guessed(self) -> None:
        """One directory, two claims. Filing under the first asserts something nobody did."""
        sink = MemoryExportSink()
        get_format("yolo_classification").export(
            dataset([frame([], tags=["daytime", "raining"], media=b"X")]), sink
        )

        assert not any(name.startswith("images/") for name in sink.files)
        readme = text(sink, "README.txt")
        assert "more than one tag" in readme
        assert "daytime, raining" in readme

    def test_a_frame_without_its_image_is_reported(self) -> None:
        """This format has no annotation without the image, so a silent skip is useless."""
        sink = MemoryExportSink()
        get_format("yolo_classification").export(dataset([frame([], tags=["daytime"])]), sink)
        assert "image was not included" in text(sink, "README.txt")

    def test_it_declares_that_it_holds_no_shapes(self) -> None:
        capabilities = get_format("yolo_classification").capabilities
        assert capabilities.shape_types == ()
        assert capabilities.supports_tags is True
        assert capabilities.unsupported([ShapeType.RECTANGLE]) == [ShapeType.RECTANGLE]


class TestRegistry:
    def test_all_five_yolo_tasks_are_registered(self) -> None:
        from curvevision.formats import all_formats

        ids = {fmt.id for fmt in all_formats()}
        assert {"yolo", "yolo_obb", "yolo_pose", "yolo_classification"} <= ids

    @pytest.mark.parametrize("format_id", ["yolo_obb", "yolo_pose", "yolo_classification"])
    def test_each_declares_its_capabilities(self, format_id: str) -> None:
        fmt = get_format(format_id)
        assert fmt.extension and fmt.version and fmt.capabilities.notes
