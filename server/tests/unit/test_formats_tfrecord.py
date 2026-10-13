"""TFRecord: the TensorFlow Object Detection API's records, written without TensorFlow.

The framing, its CRC-32C and the ``tf.train.Example`` protocol buffer are all written by
hand here, so each is pinned on its own — the checksum to its published check value, the
encoding to bytes protobuf itself produces — before the format is. CI then has TensorFlow
and Datumaro read the result (`scripts/verify_tfrecord.py`).

Written from the published formats; no code from TensorFlow or Datumaro is used.
"""

from __future__ import annotations

import io
import struct

import pytest

from curvevision.core.errors import ValidationError
from curvevision.domain.enums import AttributeType, ShapeType
from curvevision.formats import (
    AttributeSpec,
    DatasetView,
    FrameRecord,
    ImportContext,
    LabelSpec,
    MemoryExportSink,
    MemoryImportSource,
    ShapeRecord,
    get_format,
)
from curvevision.formats.rle import decode as rle_decode
from curvevision.formats.rle import encode as rle_encode
from curvevision.formats.tfrecord import (
    _crc32c_python,
    crc32c,
    decode_example,
    encode_example,
    frame_record,
    parse_label_map,
    read_records,
)

Image = pytest.importorskip("PIL.Image")

FORMAT = get_format("tfrecord")
WIDTH, HEIGHT = 40, 30
LABELS = [
    LabelSpec(
        "1",
        "car",
        attributes=(
            AttributeSpec("difficult", AttributeType.CHECKBOX),
            AttributeSpec("truncated", AttributeType.CHECKBOX),
        ),
    ),
    LabelSpec("2", "tree"),
]


class TestFraming:
    def test_crc32c_is_castagnolis(self) -> None:
        # The standard check value: CRC-32C of the ASCII digits 1 to 9.
        assert _crc32c_python(b"123456789") == 0xE3069283
        assert crc32c(b"") == 0
        # Every alignment of the eight-byte inner loop agrees with a byte at a time.
        for length in range(20):
            data = bytes(range(7, 7 + length))
            crc = 0xFFFFFFFF
            for byte in data:
                crc ^= byte
                for _ in range(8):
                    crc = (crc >> 1) ^ 0x82F63B78 if crc & 1 else crc >> 1
            assert _crc32c_python(data) == crc ^ 0xFFFFFFFF

    def test_a_record_is_its_length_checksums_and_bytes(self) -> None:
        framed = frame_record(b"hello")
        assert framed[:8] == struct.pack("<Q", 5)
        assert framed[12:17] == b"hello"
        assert len(framed) == 8 + 4 + 5 + 4
        assert list(read_records(framed + frame_record(b""))) == [b"hello", b""]

    def test_a_damaged_or_cut_record_is_refused(self) -> None:
        framed = bytearray(frame_record(b"hello"))
        framed[13] ^= 0xFF
        with pytest.raises(ValidationError, match="fails its checksum"):
            list(read_records(bytes(framed)))
        with pytest.raises(ValidationError, match="cut short"):
            list(read_records(frame_record(b"hello")[:-2]))


class TestExample:
    def test_encodes_byte_for_byte_as_protobuf_does(self) -> None:
        # What protobuf's own SerializeToString (deterministic) gives for the same examples.
        assert encode_example({"a": ("int", [1, 300])}) == bytes.fromhex(
            "0a0e0a0c0a016112071a050a0301ac02"
        )
        assert encode_example(
            {"b": ("bytes", [b"xy"]), "f": ("float", [0.5, -2.25])}
        ) == bytes.fromhex("0a200a0b0a016212060a040a0278790a110a0166120c120a0a080000003f000010c0")
        assert decode_example(bytes.fromhex("0a0e0a0c0a016112071a050a0301ac02")) == {"a": [1, 300]}

    def test_round_trips_every_kind_including_negative_and_empty(self) -> None:
        features = {
            "b": ("bytes", [b"", b"x" * 300]),
            "f": ("float", [0.5, -2.25]),
            "i": ("int", [-1, 0, 2**40]),
            "e": ("float", []),
        }
        assert decode_example(encode_example(features)) == {
            "b": [b"", b"x" * 300],
            "f": [0.5, -2.25],
            "i": [-1, 0, 2**40],
            "e": [],
        }

    def test_reads_unpacked_repeated_values_too(self) -> None:
        # proto2 writers may emit one field per value instead of a packed run.
        float_list = b"\x0d" + struct.pack("<f", 1.5) + b"\x0d" + struct.pack("<f", 2.5)
        int_list = b"\x08\x05\x08\x07"

        def entry(key: bytes, feature: bytes) -> bytes:
            body = b"\x0a" + bytes([len(key)]) + key + b"\x12" + bytes([len(feature)]) + feature
            return b"\x0a" + bytes([len(body)]) + body

        features = entry(b"f", b"\x12" + bytes([len(float_list)]) + float_list) + entry(
            b"i", b"\x1a" + bytes([len(int_list)]) + int_list
        )
        example = b"\x0a" + bytes([len(features)]) + features
        assert decode_example(example) == {"f": [1.5, 2.5], "i": [5, 7]}

    def test_a_cut_message_is_refused(self) -> None:
        with pytest.raises(ValidationError):
            decode_example(encode_example({"a": ("bytes", [b"abc"])})[:-1])


class TestLabelMap:
    def test_reads_names_display_names_and_either_quote(self) -> None:
        text = """
        item { id: 1 name: 'car' }
        item {
          name: "it's"
          id: 2
        }
        item { id: 3 display_name: "person" }
        item { id: 4 name: "/m/01g317" display_name: "tree" }
        """
        assert parse_label_map(text) == {1: "car", 2: "it's", 3: "person", 4: "/m/01g317"}


def exported(*frames: FrameRecord) -> MemoryExportSink:
    sink = MemoryExportSink()
    FORMAT.export(DatasetView(name="Street", labels=LABELS, frames=iter(frames)), sink)
    return sink


def examples(sink: MemoryExportSink, path: str = "default.tfrecord") -> list[dict]:
    return [decode_example(record) for record in read_records(sink.files[path])]


def picture() -> bytes:
    buffer = io.BytesIO()
    Image.new("RGB", (WIDTH, HEIGHT), "#336699").save(buffer, format="PNG")
    return buffer.getvalue()


def png_pixels(png: bytes) -> set[tuple[int, int]]:
    image = Image.open(io.BytesIO(png)).convert("L")
    return {(i % WIDTH, i // WIDTH) for i, v in enumerate(image.tobytes()) if v}


class TestExport:
    def test_writes_a_label_map_and_a_record_file_per_subset(self) -> None:
        sink = exported(
            FrameRecord(0, "a.png", WIDTH, HEIGHT),
            FrameRecord(1, "b.png", WIDTH, HEIGHT, subset="train"),
        )
        assert sorted(sink.files) == ["default.tfrecord", "label_map.pbtxt", "train.tfrecord"]
        assert parse_label_map(sink.files["label_map.pbtxt"].decode()) == {1: "car", 2: "tree"}

    def test_an_image_its_size_name_and_key(self) -> None:
        media = picture()
        (example,) = examples(exported(FrameRecord(0, "street/a.png", WIDTH, HEIGHT, media=media)))
        assert example["image/height"] == [HEIGHT] and example["image/width"] == [WIDTH]
        assert example["image/filename"] == [b"street/a.png"]
        assert example["image/encoded"] == [media]
        assert example["image/format"] == [b"png"]
        import hashlib

        assert example["image/key/sha256"] == [hashlib.sha256(media).hexdigest().encode()]

    def test_boxes_are_normalised_with_classes_and_flags(self) -> None:
        (example,) = examples(
            exported(
                FrameRecord(
                    0,
                    "a.png",
                    WIDTH,
                    HEIGHT,
                    shapes=[
                        ShapeRecord(
                            "car",
                            ShapeType.RECTANGLE,
                            [20, 15, 4, 3],
                            attributes={"difficult": True},
                        ),
                        ShapeRecord("tree", ShapeType.RECTANGLE, [0, 0, 40, 30]),
                    ],
                )
            )
        )
        assert example["image/object/bbox/xmin"] == pytest.approx([0.1, 0.0])
        assert example["image/object/bbox/xmax"] == pytest.approx([0.5, 1.0])
        assert example["image/object/bbox/ymin"] == pytest.approx([0.1, 0.0])
        assert example["image/object/class/label"] == [1, 2]
        assert example["image/object/class/text"] == [b"car", b"tree"]
        assert example["image/object/difficult"] == [1, 0]
        assert example["image/object/truncated"] == [0, 0]
        assert "image/object/mask" not in example

    def test_masks_are_written_when_every_object_has_one(self) -> None:
        flags = [True, False, True, True]
        mask = {"rle": rle_encode(flags, 2, 2), "left": 30, "top": 20, "width": 2, "height": 2}
        (example,) = examples(
            exported(
                FrameRecord(
                    0,
                    "a.png",
                    WIDTH,
                    HEIGHT,
                    shapes=[
                        ShapeRecord("tree", ShapeType.MASK, [30, 20, 31, 21], mask=mask),
                        ShapeRecord("car", ShapeType.ELLIPSE, [10, 10, 3, 3]),
                    ],
                )
            )
        )
        first, second = example["image/object/mask"]
        assert png_pixels(first) == {(30, 20), (30, 21), (31, 21)}
        assert len(png_pixels(second)) > 20
        # The mask's box is the edges of its pixels.
        assert example["image/object/bbox/xmin"][0] == pytest.approx(30 / WIDTH)
        assert example["image/object/bbox/xmax"][0] == pytest.approx(32 / WIDTH)

    def test_a_box_beside_a_mask_means_no_masks_on_that_image(self) -> None:
        mask = {"rle": rle_encode([True], 1, 1), "left": 1, "top": 1, "width": 1, "height": 1}
        (example,) = examples(
            exported(
                FrameRecord(
                    0,
                    "a.png",
                    WIDTH,
                    HEIGHT,
                    shapes=[
                        ShapeRecord("tree", ShapeType.MASK, [1, 1, 1, 1], mask=mask),
                        ShapeRecord("car", ShapeType.RECTANGLE, [5, 5, 9, 9]),
                    ],
                )
            )
        )
        assert len(example["image/object/bbox/xmin"]) == 2
        assert "image/object/mask" not in example

    def test_what_the_format_cannot_hold_is_left_out(self) -> None:
        (example,) = examples(
            exported(
                FrameRecord(
                    0,
                    "a.png",
                    WIDTH,
                    HEIGHT,
                    shapes=[
                        ShapeRecord("car", ShapeType.ROTATED_RECTANGLE, [5, 5, 15, 15], rotation=9),
                        ShapeRecord("car", ShapeType.POLYLINE, [0, 0, 10, 10]),
                    ],
                )
            )
        )
        assert "image/object/bbox/xmin" not in example

    def test_a_frame_of_unknown_size_is_left_out(self) -> None:
        sink = exported(FrameRecord(0, "a.png", None, None))
        assert "default.tfrecord" not in sink.files


def context(*names: str, create: bool = True) -> ImportContext:
    return ImportContext(
        known_labels={},
        frame_by_name={name: i for i, name in enumerate(names)},
        frame_count=len(names),
        frame_sizes=dict.fromkeys(range(len(names)), (WIDTH, HEIGHT)),
        create_missing_labels=create,
    )


def record(**features: tuple[str, list]) -> bytes:
    return frame_record(encode_example(dict(features)))


class TestImport:
    def test_an_export_comes_back(self) -> None:
        flags = [True, False, True, True]
        mask = {"rle": rle_encode(flags, 2, 2), "left": 30, "top": 20, "width": 2, "height": 2}
        sink = exported(
            FrameRecord(
                0,
                "street/a.png",
                WIDTH,
                HEIGHT,
                shapes=[
                    ShapeRecord(
                        "car", ShapeType.RECTANGLE, [4, 3, 20, 15], attributes={"truncated": True}
                    )
                ],
            ),
            FrameRecord(
                1,
                "street/b.png",
                WIDTH,
                HEIGHT,
                shapes=[ShapeRecord("tree", ShapeType.MASK, [30, 20, 31, 21], mask=mask)],
            ),
        )
        result = FORMAT.import_(
            MemoryImportSource(sink.files), context("street/a.png", "street/b.png")
        )
        assert result.warnings == []
        (_, car), (_, tree) = result.shapes
        assert car.shape_type is ShapeType.RECTANGLE
        assert car.points == pytest.approx([4, 3, 20, 15], abs=1e-4)
        assert car.attributes == {"difficult": False, "truncated": True}
        m = tree.mask or {}
        decoded = rle_decode(m["rle"], m["width"], m["height"])
        assert (tree.shape_type, m["left"], m["top"], decoded) == (
            ShapeType.MASK,
            30,
            20,
            flags,
        )

    def test_classes_come_from_the_label_map_when_the_record_names_none(self) -> None:
        result = FORMAT.import_(
            MemoryImportSource(
                {
                    "label_map.pbtxt": b"item { id: 7 name: 'bus' }",
                    "train.record-00000-of-00002": record(
                        **{
                            "image/filename": ("bytes", [b"x.jpg"]),
                            "image/object/bbox/xmin": ("float", [0.25]),
                            "image/object/bbox/xmax": ("float", [0.5]),
                            "image/object/bbox/ymin": ("float", [0.0]),
                            "image/object/bbox/ymax": ("float", [1.0]),
                            "image/object/class/label": ("int", [7]),
                        }
                    ),
                }
            ),
            context("x.jpg"),
        )
        assert result.warnings == []
        ((_, bus),) = result.shapes
        # No size in the record: the task's is used.
        assert (bus.label, bus.points) == ("bus", [10, 0, 20, 30])

    def test_what_cannot_be_read_is_reported_and_the_rest_imports(self) -> None:
        good = record(
            **{
                "image/filename": ("bytes", [b"a.jpg"]),
                "image/object/bbox/xmin": ("float", [0.1, 0.1]),
                "image/object/bbox/xmax": ("float", [0.2, 0.2]),
                "image/object/bbox/ymin": ("float", [0.1, 0.1]),
                "image/object/bbox/ymax": ("float", [0.2, 0.2]),
                "image/object/class/text": ("bytes", [b"car", b""]),
            }
        )
        uneven = record(
            **{
                "image/filename": ("bytes", [b"a.jpg"]),
                "image/object/bbox/xmin": ("float", [0.1, 0.2]),
                "image/object/bbox/xmax": ("float", [0.2]),
                "image/object/bbox/ymin": ("float", [0.1]),
                "image/object/bbox/ymax": ("float", [0.2]),
            }
        )
        elsewhere = record(**{"image/filename": ("bytes", [b"other.jpg"])})
        damaged = bytearray(good)
        damaged[20] ^= 0xFF
        result = FORMAT.import_(
            MemoryImportSource(
                {
                    "a.tfrecord": good + uneven + elsewhere,
                    "b.tfrecord": bytes(damaged),
                }
            ),
            context("a.jpg"),
        )
        assert len(result.shapes) == 1
        joined = "\n".join(result.warnings)
        assert "record 1: object 2 has no class" in joined
        assert "record 2: box coordinate lists differ in length" in joined
        assert "b.tfrecord: the record at byte 0 fails its checksum" in joined
        assert "1 image(s) not in this task were skipped: other.jpg" in joined

    def test_unknown_labels_are_refused_once_when_labels_may_not_be_created(self) -> None:
        one = record(
            **{
                "image/filename": ("bytes", [b"a.jpg"]),
                "image/object/bbox/xmin": ("float", [0.1, 0.3]),
                "image/object/bbox/xmax": ("float", [0.2, 0.4]),
                "image/object/bbox/ymin": ("float", [0.1, 0.1]),
                "image/object/bbox/ymax": ("float", [0.2, 0.2]),
                "image/object/class/text": ("bytes", [b"car", b"car"]),
            }
        )
        result = FORMAT.import_(
            MemoryImportSource({"a.tfrecord": one}), context("a.jpg", create=False)
        )
        assert result.shapes == []
        assert len(result.warnings) == 1 and "unknown label 'car'" in result.warnings[0]
