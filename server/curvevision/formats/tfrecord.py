"""TFRecord: the TensorFlow Object Detection API's ``tf.train.Example`` records.

Independent implementation written from the published formats: TFRecord's framing (a
length, its masked CRC-32C, the record, its masked CRC-32C) and the ``tf.train.Example``
protocol buffer, encoded here by hand so that CurveVision needs neither TensorFlow nor
protobuf. The feature keys are the Object Detection API's own. No code from TensorFlow, the
Object Detection API or Datumaro is used; TensorFlow reads this module's exports, and
Datumaro reads them and writes the files it imports, in CI (`scripts/verify_tfrecord.py`).

The layout, as Datumaro (and so CVAT) writes and reads it::

    <subset>.tfrecord       one tf.train.Example per image
    label_map.pbtxt         item { id: 1 name: 'car' } ... — ids from 1, as the API requires

Each example carries the image (``image/encoded``, when the export includes images), its
``image/filename``, ``image/source_id``, ``image/height`` and ``image/width``, and per
object a box normalised to 0..1, ``image/object/class/text`` and ``/label``, and
``image/object/difficult`` and ``/truncated`` from checkbox attributes of those names.
Polygons, ellipses and masks are written as their box and, in ``image/object/mask``, a PNG
of the pixels they cover — but only on an image where every object has one, because the API
reads a mask for each object or for none.
"""

from __future__ import annotations

import hashlib
import re
import struct
from collections.abc import Iterator
from dataclasses import dataclass, field
from typing import Any

from curvevision.core.errors import ValidationError
from curvevision.core.logging import get_logger
from curvevision.domain.enums import AttributeType, ShapeType
from curvevision.formats.base import (
    AttributeSpec,
    DatasetView,
    ExportSink,
    FormatCapabilities,
    FrameRecord,
    ImportContext,
    ImportResult,
    ImportSource,
    LabelSpec,
    ShapeRecord,
    normalise_rectangle,
)
from curvevision.formats.raster import cover, mask_shape_from_image
from curvevision.formats.registry import register

logger = get_logger(__name__)

LABEL_MAP = "label_map.pbtxt"

#: The checkbox attributes written as the API's per-object integer flags.
FLAGS = ("difficult", "truncated")

#: Shapes written as their box and the mask they cover.
SEGMENTED = frozenset({ShapeType.MASK, ShapeType.POLYGON, ShapeType.ELLIPSE})

#: What an image's encoding is called in ``image/format``, by file extension.
IMAGE_FORMATS = {"jpg": "jpeg", "jpeg": "jpeg", "png": "png"}

_RECORD_FILE = re.compile(r"\.(tfrecords?|record)(-\d+-of-\d+)?$", re.IGNORECASE)


# --------------------------------------------------------------------------- CRC-32C


def _tables() -> tuple[list[int], ...]:
    """Slicing-by-8 tables for CRC-32C (Castagnoli, reflected polynomial 0x82F63B78)."""
    first = []
    for value in range(256):
        for _ in range(8):
            value = (value >> 1) ^ 0x82F63B78 if value & 1 else value >> 1
        first.append(value)
    tables = [first]
    for _ in range(7):
        previous = tables[-1]
        tables.append([(entry >> 8) ^ first[entry & 0xFF] for entry in previous])
    return tuple(tables)


_T = _tables()


def _crc32c_python(data: bytes) -> int:
    t0, t1, t2, t3, t4, t5, t6, t7 = _T
    crc = 0xFFFFFFFF
    whole = len(data) - len(data) % 8
    for low, high in struct.iter_unpack("<II", memoryview(data)[:whole]):
        low ^= crc
        crc = (
            t7[low & 0xFF]
            ^ t6[(low >> 8) & 0xFF]
            ^ t5[(low >> 16) & 0xFF]
            ^ t4[low >> 24]
            ^ t3[high & 0xFF]
            ^ t2[(high >> 8) & 0xFF]
            ^ t1[(high >> 16) & 0xFF]
            ^ t0[high >> 24]
        )
    for byte in data[whole:]:
        crc = t0[(crc ^ byte) & 0xFF] ^ (crc >> 8)
    return crc ^ 0xFFFFFFFF


def crc32c(data: bytes) -> int:
    """CRC-32C of ``data``; the C implementation when ``google-crc32c`` is installed."""
    try:
        import google_crc32c  # type: ignore[import-not-found]
    except ImportError:
        return _crc32c_python(data)
    return int(google_crc32c.value(data))


def _masked(data: bytes) -> bytes:
    crc = crc32c(data)
    return struct.pack("<I", (((crc >> 15) | (crc << 17)) + 0xA282EAD8) & 0xFFFFFFFF)


def frame_record(data: bytes) -> bytes:
    """One record as TFRecord frames it."""
    length = struct.pack("<Q", len(data))
    return length + _masked(length) + data + _masked(data)


def read_records(blob: bytes) -> Iterator[bytes]:
    """The records of a TFRecord file, each checked against its CRCs."""
    offset = 0
    while offset < len(blob):
        if offset + 12 > len(blob):
            raise ValidationError(f"a record header is cut short at byte {offset}")
        length_bytes = blob[offset : offset + 8]
        if blob[offset + 8 : offset + 12] != _masked(length_bytes):
            raise ValidationError(f"the record length at byte {offset} fails its checksum")
        (length,) = struct.unpack("<Q", length_bytes)
        start, end = offset + 12, offset + 12 + length
        if end + 4 > len(blob):
            raise ValidationError(f"the record at byte {offset} is cut short")
        data = blob[start:end]
        if blob[end : end + 4] != _masked(data):
            raise ValidationError(f"the record at byte {offset} fails its checksum")
        yield data
        offset = end + 4


# --------------------------------------------------------------------------- tf.train.Example

#: A feature's values: bytes, floats or integers, as ``BytesList``, ``FloatList`` and
#: ``Int64List`` hold them.
Feature = list[bytes] | list[float] | list[int]


def _varint(value: int) -> bytes:
    value &= (1 << 64) - 1  # an int64 is written as its two's complement
    out = bytearray()
    while True:
        byte = value & 0x7F
        value >>= 7
        if value:
            out.append(byte | 0x80)
        else:
            out.append(byte)
            return bytes(out)


def _field(number: int, payload: bytes) -> bytes:
    """A length-delimited field (wire type 2)."""
    return _varint(number << 3 | 2) + _varint(len(payload)) + payload


def _feature(kind: str, values: list[Any]) -> bytes:
    if kind == "bytes":
        return _field(1, b"".join(_field(1, value) for value in values))
    if kind == "float":
        return _field(2, _field(1, struct.pack(f"<{len(values)}f", *values)) if values else b"")
    return _field(3, _field(1, b"".join(_varint(int(v)) for v in values)) if values else b"")


def encode_example(features: dict[str, tuple[str, list[Any]]]) -> bytes:
    """A ``tf.train.Example`` from ``{key: (kind, values)}``, kind one of bytes/float/int."""
    entries = b"".join(
        _field(1, _field(1, key.encode("utf-8")) + _field(2, _feature(kind, values)))
        for key, (kind, values) in sorted(features.items())
    )
    return _field(1, entries)


def _fields(data: bytes) -> Iterator[tuple[int, int, Any]]:
    """``(field number, wire type, value)`` for each field of a protocol buffer message."""
    offset = 0

    def varint() -> int:
        nonlocal offset
        result = shift = 0
        while True:
            if offset >= len(data):
                raise ValidationError("a protocol buffer is cut short")
            byte = data[offset]
            offset += 1
            result |= (byte & 0x7F) << shift
            if not byte & 0x80:
                return result
            shift += 7
            if shift > 63:
                raise ValidationError("a protocol buffer varint is too long")

    while offset < len(data):
        key = varint()
        number, wire = key >> 3, key & 7
        if wire == 0:
            yield number, wire, varint()
        elif wire == 1:
            yield number, wire, data[offset : offset + 8]
            offset += 8
        elif wire == 2:
            length = varint()
            if offset + length > len(data):
                raise ValidationError("a protocol buffer field is cut short")
            yield number, wire, data[offset : offset + length]
            offset += length
        elif wire == 5:
            yield number, wire, data[offset : offset + 4]
            offset += 4
        else:
            raise ValidationError(f"protocol buffer wire type {wire} is not supported")


def _signed(value: int) -> int:
    return value - (1 << 64) if value >= 1 << 63 else value


def _decode_feature(data: bytes) -> Feature:
    for kind, _wire, body in _fields(data):
        if kind == 1:  # BytesList
            return [value for number, _w, value in _fields(body) if number == 1]
        if kind == 2:  # FloatList: packed, or one fixed32 per value
            floats: list[float] = []
            for number, wire, value in _fields(body):
                if number == 1 and wire == 2:
                    floats.extend(
                        struct.unpack(f"<{len(value) // 4}f", value[: len(value) // 4 * 4])
                    )
                elif number == 1 and wire == 5:
                    floats.append(struct.unpack("<f", value)[0])
            return floats
        if kind == 3:  # Int64List: packed, or one varint per value
            ints: list[int] = []
            for number, wire, value in _fields(body):
                if number == 1 and wire == 2:
                    ints.extend(_signed(v) for _n, _w, v in _fields(_packed_varints(value)))
                elif number == 1 and wire == 0:
                    ints.append(_signed(value))
            return ints
    return []


def _packed_varints(body: bytes) -> bytes:
    """Packed varints, re-tagged as field 1 so `_fields` can walk them."""
    out = bytearray()
    offset = 0
    while offset < len(body):
        start = offset
        while body[offset] & 0x80:
            offset += 1
            if offset >= len(body):
                raise ValidationError("a packed varint is cut short")
        offset += 1
        out += b"\x08" + body[start:offset]
    return bytes(out)


def decode_example(data: bytes) -> dict[str, Feature]:
    """The features of a serialised ``tf.train.Example``."""
    features: dict[str, Feature] = {}
    for number, wire, body in _fields(data):
        if number != 1 or wire != 2:
            continue
        for entry_number, _w, entry in _fields(body):
            if entry_number != 1:
                continue
            key, value = "", b""
            for part, _pw, content in _fields(entry):
                if part == 1:
                    key = content.decode("utf-8", errors="replace")
                elif part == 2:
                    value = content
            features[key] = _decode_feature(value)
    return features


# --------------------------------------------------------------------------- the format


class TFRecordFormat:
    id = "tfrecord"
    name = "TFRecord (TF Object Detection API)"
    version = "1.0"
    extension = "zip"
    capabilities = FormatCapabilities(
        shape_types=(
            ShapeType.RECTANGLE,
            ShapeType.POLYGON,
            ShapeType.ELLIPSE,
            ShapeType.MASK,
        ),
        supports_import=True,
        supports_export=True,
        supports_tracks=False,
        supports_tags=False,
        supports_attributes=True,
        notes=(
            "The TensorFlow Object Detection API's tf.train.Example records, one file per "
            "subset, with label_map.pbtxt. Boxes are normalised to the image; checkbox "
            "attributes named difficult and truncated are the API's per-object flags. "
            "Polygons, ellipses and masks are written as their box plus a PNG mask, but "
            "masks only on an image where every object has one (the API reads all or none), "
            "so they come back as masks; elsewhere only their box. Rotated boxes, lines, "
            "points, cuboids, skeletons, tags and other attributes are not written. Masks "
            "need Pillow. Written in pure Python: install google-crc32c to checksum large "
            "exports faster."
        ),
    )

    # ----------------------------------------------------------------------- export

    def export(self, dataset: DatasetView, sink: ExportSink) -> None:
        ids = {label.name: index for index, label in enumerate(dataset.labels, start=1)}
        sink.write(
            LABEL_MAP,
            "".join(
                f"item {{\n  id: {i}\n  name: {_quoted(name)}\n}}\n\n" for name, i in ids.items()
            ),
        )
        records: dict[str, list[bytes]] = {}
        unsized: list[str] = []
        for frame in dataset:
            width, height = frame.width or 0, frame.height or 0
            if width <= 0 or height <= 0:
                unsized.append(frame.name)
                continue
            records.setdefault(frame.subset, []).append(
                frame_record(encode_example(_example(frame, ids, width, height)))
            )
        for subset, framed in records.items():
            sink.write(f"{subset}.tfrecord", b"".join(framed))
        if unsized:
            logger.info("tfrecord: frames of unknown size were left out", extra={"frames": unsized})

    # ----------------------------------------------------------------------- import

    def import_(self, source: ImportSource, context: ImportContext) -> ImportResult:
        reader = _Reader(source, context)
        reader.read()
        return reader.result


def _quoted(text: str) -> str:
    """A text-format string: single-quoted as the API's own label maps are, when it can be."""
    if "'" not in text and "\\" not in text:
        return f"'{text}'"
    return '"' + text.replace("\\", "\\\\").replace('"', '\\"') + '"'


def _example(
    frame: FrameRecord, ids: dict[str, int], width: int, height: int
) -> dict[str, tuple[str, list[Any]]]:
    filename = frame.name.replace("\\", "/")
    extension = filename.rsplit(".", 1)[-1].lower() if "." in filename else ""
    encoded = frame.media or b""
    features: dict[str, tuple[str, list[Any]]] = {
        "image/height": ("int", [height]),
        "image/width": ("int", [width]),
        "image/filename": ("bytes", [filename.encode("utf-8")]),
        "image/source_id": ("bytes", [filename.encode("utf-8")]),
        "image/encoded": ("bytes", [encoded]),
        "image/format": ("bytes", [IMAGE_FORMATS.get(extension, "").encode() if encoded else b""]),
        "image/key/sha256": (
            "bytes",
            [hashlib.sha256(encoded).hexdigest().encode() if encoded else b""],
        ),
    }
    objects: list[tuple[ShapeRecord, list[float], bytes | None]] = []
    for shape in frame.shapes:
        if shape.label not in ids:
            continue
        if shape.shape_type is ShapeType.RECTANGLE:
            objects.append((shape, normalise_rectangle(list(shape.points)), None))
        elif shape.shape_type in SEGMENTED:
            covered = cover(shape, width, height)
            if covered is not None:
                png, box = covered
                objects.append((shape, box, png))
    if not objects:
        return features
    xmin, xmax, ymin, ymax = [], [], [], []
    for _shape, (x1, y1, x2, y2), _png in objects:
        xmin.append(min(max(x1 / width, 0.0), 1.0))
        xmax.append(min(max(x2 / width, 0.0), 1.0))
        ymin.append(min(max(y1 / height, 0.0), 1.0))
        ymax.append(min(max(y2 / height, 0.0), 1.0))
    features.update(
        {
            "image/object/bbox/xmin": ("float", xmin),
            "image/object/bbox/xmax": ("float", xmax),
            "image/object/bbox/ymin": ("float", ymin),
            "image/object/bbox/ymax": ("float", ymax),
            "image/object/class/text": (
                "bytes",
                [s.label.encode("utf-8") for s, _b, _p in objects],
            ),
            "image/object/class/label": ("int", [ids[s.label] for s, _b, _p in objects]),
        }
    )
    for flag in FLAGS:
        features[f"image/object/{flag}"] = (
            "int",
            [1 if s.attributes.get(flag) is True else 0 for s, _b, _p in objects],
        )
    masks = [png for _s, _b, png in objects]
    if all(png is not None for png in masks):
        features["image/object/mask"] = ("bytes", masks)
    return features


# --------------------------------------------------------------------------- import


def parse_label_map(text: str) -> dict[int, str]:
    """``id -> name`` from a text-format label map; ``display_name`` when there is no name."""
    labels: dict[int, str] = {}
    for body in re.findall(r"item\s*\{(.*?)\}", text, flags=re.DOTALL):
        found = re.search(r"\bid\s*:\s*(-?\d+)", body)
        name = _string(body, "name") or _string(body, "display_name")
        if found and name is not None:
            labels[int(found.group(1))] = name
    return labels


def _string(body: str, key: str) -> str | None:
    match = re.search(rf"\b{key}\s*:\s*(?:'((?:[^'\\]|\\.)*)'|\"((?:[^\"\\]|\\.)*)\")", body)
    if match is None:
        return None
    raw = match.group(1) if match.group(1) is not None else match.group(2)
    return re.sub(r"\\(.)", r"\1", raw)


@dataclass
class _Reader:
    source: ImportSource
    context: ImportContext
    result: ImportResult = field(default_factory=ImportResult)
    label_map: dict[int, str] = field(default_factory=dict)
    known: dict[str, str] = field(default_factory=dict)
    created: dict[str, set[str]] = field(default_factory=dict)
    refused: set[str] = field(default_factory=set)
    frames: dict[str, int] = field(default_factory=dict)
    stems: dict[str, int] = field(default_factory=dict)
    unmatched: list[str] = field(default_factory=list)

    def read(self) -> None:
        names = self.source.names()
        self.known = dict(self.context.known_labels)
        for name, index in self.context.frame_by_name.items():
            path = name.replace("\\", "/")
            self.frames.setdefault(path, index)
            base = path.rsplit("/", 1)[-1]
            self.frames.setdefault(base, index)
            self.stems.setdefault(base.rsplit(".", 1)[0], index)
        for name in sorted(n for n in names if n.lower().endswith(".pbtxt")):
            try:
                self.label_map.update(parse_label_map(self.source.read(name).decode("utf-8")))
            except UnicodeDecodeError:
                self.result.warnings.append(f"{name}: not UTF-8 text; skipped")
        matched: set[int] = set()
        for name in sorted(n for n in names if _RECORD_FILE.search(n)):
            try:
                for number, record in enumerate(read_records(self.source.read(name)), 1):
                    frame = self._example(f"{name}: record {number}", record)
                    if frame is not None:
                        matched.add(frame)
            except ValidationError as exc:
                self.result.warnings.append(f"{name}: {exc}; the rest of the file was skipped")
        self.result.frames_matched = len(matched)
        if self.unmatched:
            more = f" and {len(self.unmatched) - 5} more" if len(self.unmatched) > 5 else ""
            self.result.warnings.append(
                f"{len(self.unmatched)} image(s) not in this task were skipped: "
                + ", ".join(self.unmatched[:5])
                + more
            )
        self.result.new_labels = [
            LabelSpec(
                id=label,
                name=label,
                attributes=tuple(
                    AttributeSpec(name=a, attribute_type=AttributeType.CHECKBOX)
                    for a in sorted(flags)
                ),
            )
            for label, flags in self.created.items()
        ]

    def _frame(self, features: dict[str, Feature]) -> tuple[int | None, str]:
        names = [
            value.decode("utf-8", errors="replace")
            for key in ("image/filename", "image/source_id")
            for value in features.get(key, [])
            if isinstance(value, bytes) and value
        ]
        for name in names:
            path = name.replace("\\", "/")
            for candidate in (path, path.rsplit("/", 1)[-1]):
                if candidate in self.frames:
                    return self.frames[candidate], name
            stem = path.rsplit("/", 1)[-1].rsplit(".", 1)[0]
            if stem in self.stems:
                return self.stems[stem], name
        return None, names[0] if names else "?"

    def _label(self, name: str, where: str) -> str | None:
        if name in self.known:
            return name
        if not self.context.create_missing_labels:
            if name not in self.refused:
                self.result.warnings.append(f"{where}: unknown label {name!r}; skipped")
                self.refused.add(name)
            return None
        self.known[name] = name
        self.created[name] = set()
        return name

    def _example(self, where: str, record: bytes) -> int | None:
        try:
            features = decode_example(record)
        except ValidationError as exc:
            self.result.warnings.append(f"{where}: {exc}; skipped")
            return None
        frame, name = self._frame(features)
        if frame is None:
            self.unmatched.append(name)
            return None
        width = _first_int(features.get("image/width"))
        height = _first_int(features.get("image/height"))
        if not width or not height:
            width, height = self.context.frame_sizes.get(frame, (0, 0))
        boxes = [
            features.get(f"image/object/bbox/{k}", []) for k in ("xmin", "ymin", "xmax", "ymax")
        ]
        count = len(boxes[0])
        if count == 0:
            return frame
        if any(len(column) != count for column in boxes):
            self.result.warnings.append(f"{where}: box coordinate lists differ in length; skipped")
            return frame
        if width <= 0 or height <= 0:
            self.result.warnings.append(f"{where}: the image's size is unknown; skipped")
            return frame
        texts = features.get("image/object/class/text", [])
        ids = features.get("image/object/class/label", [])
        masks = features.get("image/object/mask", [])
        flags = {flag: features.get(f"image/object/{flag}", []) for flag in FLAGS}
        for index in range(count):
            label_name = _name(texts, ids, index, self.label_map)
            if label_name is None:
                self.result.warnings.append(f"{where}: object {index + 1} has no class; skipped")
                continue
            label = self._label(label_name, where)
            if label is None:
                continue
            attributes = {
                flag: bool(values[index])
                for flag, values in flags.items()
                if len(values) == count and isinstance(values[index], int)
            }
            if label in self.created:
                self.created[label].update(attributes)
            x1, y1, x2, y2 = (float(column[index]) for column in boxes)
            shape: ShapeRecord | None = None
            png = masks[index] if len(masks) == count else b""
            if isinstance(png, bytes) and png:
                shape = _mask(png, label, width, height, where, self.result.warnings)
            if shape is None:
                shape = ShapeRecord(
                    label=label,
                    shape_type=ShapeType.RECTANGLE,
                    # A float32 holds about seven significant digits: 0.1 of 640 reads back as
                    # 64.00000095. A thousandth of a pixel is all it can mean.
                    points=normalise_rectangle(
                        [round(v, 3) for v in (x1 * width, y1 * height, x2 * width, y2 * height)]
                    ),
                )
            shape.attributes = attributes
            self.result.shapes.append((frame, shape))
        return frame


def _first_int(values: Feature | None) -> int:
    return int(values[0]) if values and isinstance(values[0], int) else 0


def _name(texts: Feature, ids: Feature, index: int, label_map: dict[int, str]) -> str | None:
    if index < len(texts) and isinstance(texts[index], bytes) and texts[index]:
        return texts[index].decode("utf-8", errors="replace")  # type: ignore[union-attr]
    if index < len(ids) and isinstance(ids[index], int):
        return label_map.get(int(ids[index]))
    return None


def _mask(
    png: bytes, label: str, width: int, height: int, where: str, warnings: list[str]
) -> ShapeRecord | None:
    try:
        import io

        from PIL import Image
    except ImportError:
        warnings.append(f"{where}: masks need Pillow (the media extra); read as boxes")
        return None
    try:
        with Image.open(io.BytesIO(png)) as raw:
            image = raw.convert("L")
    except OSError:
        warnings.append(f"{where}: a mask is not a readable PNG; read as its box")
        return None
    if image.size != (width, height):
        image = image.resize((width, height), Image.Resampling.NEAREST)
    return mask_shape_from_image(image, label)


register(TFRecordFormat())


__all__ = [
    "TFRecordFormat",
    "crc32c",
    "decode_example",
    "encode_example",
    "frame_record",
    "parse_label_map",
    "read_records",
]
