"""Masks, from the run-length encoding up to what each format writes.

`docs/ROADMAP.md` said "RLE storage and export exist". Storage did. Export did not, and the
two formats that should have carried a mask were the ones failing:

* **`cvat_xml`** declared `ShapeType.MASK` and its notes claimed masks were carried. Its
  writer fell through to the generic branch and emitted `<mask points="10,10;13,12">` -- the
  two corners of the bounding box. Every pixel was dropped, into a file that parsed cleanly.
* **`segmentation_mask`**, the *segmentation mask* format, matched no mask shape at all and
  exported a frame of masks as a page of background.

Only the native JSON, which passes the payload through verbatim, carried one. These tests
pin both halves of the fix, and the ones marked with a comment were confirmed to fail
against the old code.
"""

from __future__ import annotations

import io
import xml.etree.ElementTree as ET

import pytest

from curvevision.core.errors import ValidationError
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
from curvevision.formats.rle import (
    decode,
    encode,
    from_attribute,
    mask_bounds,
    pixels,
    to_attribute,
    validate_mask,
)

#: An L in a 4x3 box:  ###.
#:                     #...
#:                     ....
L_SHAPE = [True] * 3 + [False] + [True] + [False] * 7
BOX = (4, 3)


def mask_at(left: int = 10, top: int = 10, flags: list[bool] | None = None) -> dict:
    width, height = BOX
    return {
        "rle": encode(flags if flags is not None else L_SHAPE, width, height),
        "left": left,
        "top": top,
        "width": width,
        "height": height,
    }


def mask_shape(label: str = "car", z_order: int = 0, **kwargs) -> ShapeRecord:
    payload = mask_at(**kwargs)
    left, top, right, bottom = mask_bounds(payload)
    return ShapeRecord(
        label=label,
        shape_type=ShapeType.MASK,
        points=[float(left), float(top), float(right), float(bottom)],
        z_order=z_order,
        mask=payload,
    )


def one_frame(shapes: list[ShapeRecord], width: int = 64, height: int = 64) -> DatasetView:
    return DatasetView(
        name="masks",
        labels=[LabelSpec(id="1", name="car"), LabelSpec(id="2", name="pedestrian")],
        frames=[FrameRecord(index=0, name="a.png", width=width, height=height, shapes=shapes)],
    )


# ------------------------------------------------------------------ the encoding


class TestRunLengthEncoding:
    def test_the_runs_start_with_background(self) -> None:
        """A leading zero is normal and load-bearing, not a quirk to strip."""
        assert encode([True, True, False], 3, 1) == [0, 2]
        assert encode([False, True], 2, 1) == [1, 1]

    def test_decode_inverts_encode(self) -> None:
        assert decode(encode(L_SHAPE, *BOX), *BOX) == L_SHAPE

    @pytest.mark.parametrize(
        "flags",
        [
            [False] * 12,
            [True] * 12,
            [True] + [False] * 11,
            [False] * 11 + [True],
            L_SHAPE,
        ],
    )
    def test_every_shape_round_trips(self, flags: list[bool]) -> None:
        assert decode(encode(flags, *BOX), *BOX) == flags

    def test_an_empty_mask_encodes_to_nothing_rather_than_a_run_of_zeros(self) -> None:
        """A trailing background run says nothing the box does not already say."""
        assert encode([False] * 12, *BOX) == []

    # Every encoder that trims a trailing background run produces a short list. Refusing
    # one would make this module unable to read its own output.
    def test_a_short_run_list_leaves_the_rest_background(self) -> None:
        assert decode([0, 2], *BOX) == [True, True] + [False] * 10

    def test_runs_past_the_end_are_truncated_rather_than_overflowing(self) -> None:
        assert decode([0, 9999], *BOX) == [True] * 12

    # Silently reading it as zero would shift every pixel after it, producing a mask that
    # is wrong rather than one that is absent.
    def test_a_negative_run_is_corrupt_data_and_says_so(self) -> None:
        with pytest.raises(ValidationError, match="negative"):
            decode([0, -4], *BOX)

    def test_a_box_with_no_area_is_refused(self) -> None:
        with pytest.raises(ValidationError, match="positive box"):
            decode([0, 1], 0, 5)
        with pytest.raises(ValidationError, match="positive box"):
            encode([], 4, 0)

    def test_encode_refuses_a_pixel_count_that_does_not_match_the_box(self) -> None:
        with pytest.raises(ValidationError, match="expected 12 pixels"):
            encode([True, False], *BOX)


class TestStoredMasks:
    def test_pixels_are_absolute_frame_coordinates(self) -> None:
        assert sorted(pixels(mask_at(left=10, top=10))) == [
            (10, 10),
            (10, 11),
            (11, 10),
            (12, 10),
        ]

    def test_moving_the_box_moves_every_pixel(self) -> None:
        moved = sorted(pixels(mask_at(left=100, top=200)))
        assert moved == [(100, 200), (100, 201), (101, 200), (102, 200)]

    def test_bounds_are_inclusive_because_that_is_what_the_element_means(self) -> None:
        # CVAT writes width as `right - left + 1`, so a 4-wide box at x=10 ends at x=13.
        assert mask_bounds(mask_at(left=10, top=10)) == (10, 10, 13, 12)

    def test_a_mask_missing_its_box_is_refused_rather_than_read_as_empty(self) -> None:
        with pytest.raises(ValidationError, match="missing"):
            validate_mask({"rle": [0, 4], "left": 1})

    def test_something_that_is_not_a_mask_at_all_is_refused(self) -> None:
        with pytest.raises(ValidationError, match="must be an object"):
            validate_mask([0, 4])
        with pytest.raises(ValidationError, match="list of run lengths"):
            validate_mask({"rle": "0,4", "left": 0, "top": 0, "width": 2, "height": 2})

    def test_the_attribute_is_comma_separated_with_no_brackets(self) -> None:
        assert to_attribute([0, 3, 1, 1]) == "0,3,1,1"
        assert from_attribute("0,3,1,1") == [0, 3, 1, 1]

    def test_the_attribute_survives_the_spaces_a_hand_edit_adds(self) -> None:
        assert from_attribute(" 0, 3 ,1,1 ") == [0, 3, 1, 1]

    def test_an_empty_attribute_is_an_empty_mask_rather_than_a_parse_error(self) -> None:
        assert from_attribute("") == []
        assert from_attribute("   ") == []

    def test_an_unreadable_attribute_says_so(self) -> None:
        with pytest.raises(ValidationError, match="unreadable mask rle"):
            from_attribute("0,three,1")


# --------------------------------------------------------- the segmentation format


def lit_pixels(png: bytes) -> set[tuple[int, int]]:
    from PIL import Image

    image = Image.open(io.BytesIO(png))
    width, height = image.size
    return {(x, y) for y in range(height) for x in range(width) if image.getpixel((x, y)) != 0}


def only_png(sink: MemoryExportSink) -> bytes:
    names = [name for name in sink.files if name.endswith(".png")]
    assert len(names) == 1, f"expected one PNG, found {names}"
    return sink.files[names[0]]


class TestSegmentationMask:
    # The regression. Before the fix a mask matched nothing in `FILLABLE` and the frame
    # exported as a page of background, in a PNG that looked perfectly well-formed.
    def test_a_mask_is_painted_pixel_for_pixel(self) -> None:
        pytest.importorskip("PIL")
        sink = MemoryExportSink()
        get_format("segmentation_mask").export(one_frame([mask_shape()]), sink)

        assert lit_pixels(only_png(sink)) == {(10, 10), (10, 11), (11, 10), (12, 10)}

    def test_the_pixels_carry_the_label_s_class_index(self) -> None:
        pytest.importorskip("PIL")
        from PIL import Image

        sink = MemoryExportSink()
        get_format("segmentation_mask").export(one_frame([mask_shape(label="pedestrian")]), sink)

        image = Image.open(io.BytesIO(only_png(sink)))
        # "car" is 1 and "pedestrian" is 2, from the project's label order rather than
        # from what happens to appear in this frame.
        assert image.getpixel((10, 10)) == 2

    def test_the_format_now_declares_that_it_carries_masks(self) -> None:
        capabilities = get_format("segmentation_mask").capabilities
        assert ShapeType.MASK in capabilities.shape_types
        assert "Masks are written pixel for pixel" in (capabilities.notes or "")

    def test_a_mask_in_front_wins_the_pixel(self) -> None:
        """Overlaps resolve by z-order, and a mask is no exception to that."""
        pytest.importorskip("PIL")
        from PIL import Image

        behind = ShapeRecord(
            label="car",
            shape_type=ShapeType.RECTANGLE,
            points=[0.0, 0.0, 40.0, 40.0],
            z_order=0,
        )
        sink = MemoryExportSink()
        get_format("segmentation_mask").export(
            one_frame([behind, mask_shape(label="pedestrian", z_order=1)]), sink
        )

        image = Image.open(io.BytesIO(only_png(sink)))
        assert image.getpixel((10, 10)) == 2, "the mask in front did not win its pixel"
        assert image.getpixel((30, 30)) == 1, "the box behind lost a pixel the mask never covered"

    def test_a_mask_shape_with_no_payload_paints_nothing_rather_than_raising(self) -> None:
        pytest.importorskip("PIL")
        empty = ShapeRecord(
            label="car", shape_type=ShapeType.MASK, points=[0.0, 0.0, 1.0, 1.0], mask=None
        )
        sink = MemoryExportSink()
        get_format("segmentation_mask").export(one_frame([empty]), sink)

        assert lit_pixels(only_png(sink)) == set()

    def test_a_corrupt_mask_costs_its_own_shape_and_not_the_export(self) -> None:
        pytest.importorskip("PIL")
        corrupt = ShapeRecord(
            label="car",
            shape_type=ShapeType.MASK,
            points=[0.0, 0.0, 1.0, 1.0],
            mask={"rle": [0, 2], "left": 0},  # no box
        )
        sink = MemoryExportSink()
        get_format("segmentation_mask").export(one_frame([corrupt, mask_shape()]), sink)

        # The good mask still lands; only the broken one is missing.
        assert lit_pixels(only_png(sink)) == {(10, 10), (10, 11), (11, 10), (12, 10)}


# ------------------------------------------------------------------- CVAT XML


def only_xml(sink: MemoryExportSink) -> str:
    names = [name for name in sink.files if name.endswith(".xml")]
    assert len(names) == 1, f"expected one XML file, found {names}"
    return sink.files[names[0]].decode()


class TestCvatXmlMasks:
    # The regression. `<mask points="10.00,10.00;13.00,12.00">` parses cleanly, says
    # nothing about the pixels, and was written by a format whose notes claimed masks.
    def test_a_mask_is_written_as_runs_and_a_box_not_as_two_corners(self) -> None:
        sink = MemoryExportSink()
        get_format("cvat_xml").export(one_frame([mask_shape()]), sink)

        element = ET.fromstring(only_xml(sink)).find(".//mask")
        assert element is not None
        assert element.get("rle") == "0,3,1,1"
        assert element.get("left") == "10"
        assert element.get("top") == "10"
        assert element.get("width") == "4"
        assert element.get("height") == "3"
        assert element.get("points") is None, "the corners are not the mask"

    def test_the_mask_survives_a_round_trip_through_the_file(self) -> None:
        original = mask_shape()
        sink = MemoryExportSink()
        get_format("cvat_xml").export(one_frame([original]), sink)

        result = get_format("cvat_xml").import_(
            MemoryImportSource({"annotations.xml": only_xml(sink).encode()}),
            ImportContext(known_labels={"car": "1"}, frame_by_name={"a.png": 0}, frame_count=1),
        )
        assert result.warnings == []
        assert len(result.shapes) == 1
        _frame, restored = result.shapes[0]
        assert restored.shape_type is ShapeType.MASK
        assert restored.mask == original.mask
        # And the same pixels come back out the far side, which is the claim that matters.
        assert sorted(pixels(restored.mask or {})) == sorted(pixels(original.mask or {}))

    def test_the_points_come_back_as_the_inclusive_box(self) -> None:
        """Bounds, hit-testing and the label chip all read `points`."""
        sink = MemoryExportSink()
        get_format("cvat_xml").export(one_frame([mask_shape()]), sink)
        result = get_format("cvat_xml").import_(
            MemoryImportSource({"annotations.xml": only_xml(sink).encode()}),
            ImportContext(known_labels={"car": "1"}, frame_by_name={"a.png": 0}, frame_count=1),
        )
        _frame, restored = result.shapes[0]
        assert restored.points == [10.0, 10.0, 13.0, 12.0]

    def test_a_mask_shape_with_no_payload_is_dropped_rather_than_written_empty(self) -> None:
        """A reader cannot tell an empty mask from one that failed to serialise."""
        empty = ShapeRecord(
            label="car", shape_type=ShapeType.MASK, points=[0.0, 0.0, 1.0, 1.0], mask=None
        )
        sink = MemoryExportSink()
        get_format("cvat_xml").export(one_frame([empty]), sink)

        assert ET.fromstring(only_xml(sink)).find(".//mask") is None

    def test_a_mask_element_with_no_runs_is_skipped_with_a_reason(self) -> None:
        xml = (
            '<?xml version="1.0"?><annotations><image id="0" name="a.png" width="64" '
            'height="64"><mask label="car" rle="" left="0" top="0" width="2" height="2">'
            "</mask></image></annotations>"
        )
        result = get_format("cvat_xml").import_(
            MemoryImportSource({"annotations.xml": xml.encode()}),
            ImportContext(known_labels={"car": "1"}, frame_by_name={"a.png": 0}, frame_count=1),
        )
        assert result.shapes == []
        assert any("carried no rle" in warning for warning in result.warnings)

    def test_an_unreadable_rle_is_skipped_rather_than_failing_the_import(self) -> None:
        xml = (
            '<?xml version="1.0"?><annotations><image id="0" name="a.png" width="64" '
            'height="64"><mask label="car" rle="0,three" left="0" top="0" width="2" '
            'height="2"></mask></image></annotations>'
        )
        result = get_format("cvat_xml").import_(
            MemoryImportSource({"annotations.xml": xml.encode()}),
            ImportContext(known_labels={"car": "1"}, frame_by_name={"a.png": 0}, frame_count=1),
        )
        assert result.shapes == []
        assert result.warnings, "a dropped mask has to be reported"
