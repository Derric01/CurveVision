#!/usr/bin/env python3
"""CurveVision's TFRecord export, read by TensorFlow and Datumaro — and Datumaro's, by us.

    python scripts/verify_tfrecord.py

`server/tests/unit/test_formats_tfrecord.py` holds the framing, the protocol buffer and the
format to what this project believes they are. CurveVision writes all three by hand, with
neither TensorFlow nor protobuf, so a belief that is wrong would be wrong everywhere at
once. This has the real readers check it:

1. **TensorFlow reads our records** with ``tf.data.TFRecordDataset`` — which verifies every
   length and data checksum — and parses each with the Object Detection API's own feature
   specification (``TfExampleDecoder``'s keys); the image decodes, its SHA-256 is its
   ``image/key/sha256``, every box is where it was drawn and every mask PNG, decoded by
   TensorFlow, is the shape's pixels.
2. **Datumaro reads our export** as ``tf_detection_api``: labels from ``label_map.pbtxt``,
   boxes and masks.
3. **Datumaro's export, our reader**: a dataset written by Datumaro's exporter — through
   TensorFlow's own ``TFRecordWriter`` and protobuf — is imported and compared.

Written from the formats; TensorFlow and Datumaro are used here only as separate programs'
readers and writers, never shipped. Needs the server package (with Pillow), numpy,
``tensorflow-cpu`` and ``datumaro`` in one interpreter. Run by
`.github/workflows/tfrecord.yml`.
"""

from __future__ import annotations

import hashlib
import io
import os
import sys
import tempfile
from pathlib import Path

os.environ.setdefault("TF_CPP_MIN_LOG_LEVEL", "3")

WIDTH, HEIGHT = 64, 48


def main() -> int:
    import numpy as np
    import tensorflow as tf
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
    from curvevision.formats.rle import decode, encode
    from PIL import Image

    failures: list[str] = []

    def check(condition: bool, passed: str, failed: str) -> None:
        if condition:
            print(f"  ok   {passed}")
        else:
            failures.append(failed)
            print(f"  FAIL {failed}")

    def picture() -> bytes:
        buffer = io.BytesIO()
        Image.new("RGB", (WIDTH, HEIGHT), "#336699").save(buffer, format="PNG")
        return buffer.getvalue()

    fmt = get_format("tfrecord")

    # A 6x4 mask with a hole and a missing corner: no symmetry, so a mask painted flipped or
    # turned cannot pass for this one.
    flags = [not (x in (2, 3) and y == 1) and (x, y) != (5, 3) for y in range(4) for x in range(6)]
    mask = {"rle": encode(flags, 6, 4), "left": 40, "top": 30, "width": 6, "height": 4}
    expected_mask = np.zeros((HEIGHT, WIDTH), dtype=bool)
    for index, flag in enumerate(flags):
        expected_mask[30 + index // 6, 40 + index % 6] = flag
    labels = [
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
    boxes_frame = FrameRecord(
        0,
        "street/a.png",
        WIDTH,
        HEIGHT,
        media=picture(),
        shapes=[
            ShapeRecord(
                "car",
                ShapeType.RECTANGLE,
                [8, 6, 40, 30],
                attributes={"difficult": True, "truncated": False},
            ),
            ShapeRecord("tree", ShapeType.RECTANGLE, [44, 2, 60, 20]),
        ],
    )
    masks_frame = FrameRecord(
        1,
        "street/b.png",
        WIDTH,
        HEIGHT,
        media=picture(),
        shapes=[
            ShapeRecord("tree", ShapeType.MASK, [40, 30, 45, 33], mask=mask),
            ShapeRecord("car", ShapeType.POLYGON, [4, 4, 24, 4, 14, 20]),
        ],
    )
    sink = MemoryExportSink()
    fmt.export(DatasetView("Street", labels, iter([boxes_frame, masks_frame])), sink)

    # What the polygon covers, drawn as the export draws it, to compare TensorFlow's mask with.
    from PIL import ImageDraw

    drawn = Image.new("L", (WIDTH, HEIGHT), 0)
    ImageDraw.Draw(drawn).polygon([(4, 4), (24, 4), (14, 20)], fill=255)
    expected_polygon = np.array(drawn) > 0

    with tempfile.TemporaryDirectory(prefix="curvevision-tfrecord-") as workdir:
        root = Path(workdir) / "ours"
        for path, data in sink.files.items():
            (root / path).parent.mkdir(parents=True, exist_ok=True)
            (root / path).write_bytes(data if isinstance(data, bytes) else data.encode())

        print("our records, TensorFlow's reader and the Object Detection API's features")
        # TfExampleDecoder's keys_to_features, for boxes and PNG instance masks.
        spec = {
            "image/encoded": tf.io.FixedLenFeature((), tf.string, default_value=""),
            "image/format": tf.io.FixedLenFeature((), tf.string, default_value="jpeg"),
            "image/filename": tf.io.FixedLenFeature((), tf.string, default_value=""),
            "image/key/sha256": tf.io.FixedLenFeature((), tf.string, default_value=""),
            "image/source_id": tf.io.FixedLenFeature((), tf.string, default_value=""),
            "image/height": tf.io.FixedLenFeature((), tf.int64, default_value=1),
            "image/width": tf.io.FixedLenFeature((), tf.int64, default_value=1),
            "image/object/bbox/xmin": tf.io.VarLenFeature(tf.float32),
            "image/object/bbox/xmax": tf.io.VarLenFeature(tf.float32),
            "image/object/bbox/ymin": tf.io.VarLenFeature(tf.float32),
            "image/object/bbox/ymax": tf.io.VarLenFeature(tf.float32),
            "image/object/class/label": tf.io.VarLenFeature(tf.int64),
            "image/object/class/text": tf.io.VarLenFeature(tf.string),
            "image/object/difficult": tf.io.VarLenFeature(tf.int64),
            "image/object/mask": tf.io.VarLenFeature(tf.string),
        }
        parsed = [
            tf.io.parse_single_example(record, spec)
            for record in tf.data.TFRecordDataset(str(root / "default.tfrecord"))
        ]
        check(
            len(parsed) == 2,
            "TensorFlow reads every record, every checksum verified",
            f"TensorFlow read {len(parsed)} records",
        )
        if len(parsed) == 2:
            first, second = parsed

            def dense(example, key, default=0):
                return tf.sparse.to_dense(example[key], default_value=default).numpy()

            image = tf.io.decode_image(first["image/encoded"]).numpy()
            encoded = first["image/encoded"].numpy()
            check(
                image.shape == (HEIGHT, WIDTH, 3)
                and first["image/key/sha256"].numpy().decode()
                == hashlib.sha256(encoded).hexdigest()
                and first["image/format"].numpy() == b"png"
                and (int(first["image/height"]), int(first["image/width"])) == (HEIGHT, WIDTH),
                "the image decodes, at its size, its SHA-256 its key",
                f"image {image.shape}, format {first['image/format'].numpy()}",
            )
            boxes = np.stack(
                [dense(first, f"image/object/bbox/{k}") for k in ("xmin", "ymin", "xmax", "ymax")],
                axis=1,
            )
            expected = np.array(
                [
                    [8 / WIDTH, 6 / HEIGHT, 40 / WIDTH, 30 / HEIGHT],
                    [44 / WIDTH, 2 / HEIGHT, 60 / WIDTH, 20 / HEIGHT],
                ]
            )
            check(
                np.allclose(boxes, expected, atol=1e-6),
                "every box is where it was drawn, normalised",
                f"boxes {boxes.tolist()}",
            )
            check(
                dense(first, "image/object/class/label").tolist() == [1, 2]
                and dense(first, "image/object/class/text", b"").tolist() == [b"car", b"tree"]
                and dense(first, "image/object/difficult").tolist() == [1, 0],
                "classes by id and name, and the difficult flag",
                f"labels {dense(first, 'image/object/class/label').tolist()}",
            )
            check(
                dense(first, "image/object/mask", b"").tolist() == [],
                "an image of boxes has no masks",
                "masks were written for boxes",
            )
            masks = [
                tf.squeeze(tf.io.decode_png(png, channels=1), axis=2).numpy() > 0
                for png in dense(second, "image/object/mask", b"")
            ]
            check(
                len(masks) == 2
                and bool((masks[0] == expected_mask).all())
                and bool((masks[1] == expected_polygon).all()),
                "each mask, decoded by TensorFlow, is the shape's pixels",
                f"{len(masks)} masks, covering {[int(m.sum()) for m in masks]} pixels",
            )
            boxes = np.stack(
                [dense(second, f"image/object/bbox/{k}") for k in ("xmin", "ymin", "xmax", "ymax")],
                axis=1,
            )
            check(
                np.allclose(
                    boxes[0], [40 / WIDTH, 30 / HEIGHT, 46 / WIDTH, 34 / HEIGHT], atol=1e-6
                ),
                "a mask's box is the edges of its pixels",
                f"the mask's box {boxes[0]}",
            )

        print("\nDatumaro reads the export")
        from datumaro.components.annotation import AnnotationType, Bbox, Mask
        from datumaro.components.dataset import Dataset
        from datumaro.components.dataset_base import DatasetItem
        from datumaro.components.media import Image as DmImage

        dataset = Dataset.import_from(str(root), "tf_detection_api")
        names = dataset.categories()[AnnotationType.label]
        items = {item.id: item for item in dataset}
        check(
            sorted(items) == ["street/a", "street/b"],
            "every image is an item",
            f"Datumaro read {sorted(items)}",
        )
        if "street/a" in items:
            read = sorted(
                (names[a.label].name, [round(v, 3) for v in a.get_bbox()])
                for a in items["street/a"].annotations
                if a.type is AnnotationType.bbox
            )
            check(
                read == [("car", [8, 6, 32, 24]), ("tree", [44, 2, 16, 18])],
                "Datumaro reads every box and its label",
                f"Datumaro read {read}",
            )
        if "street/b" in items:
            # The decoded PNG as it is; the API's decoder takes any value above 0 as the mask.
            dm_masks = {
                names[a.label].name: np.squeeze(a.image) > 0
                for a in items["street/b"].annotations
                if a.type is AnnotationType.mask
            }
            check(
                sorted(dm_masks) == ["car", "tree"]
                and bool((dm_masks["tree"] == expected_mask).all()),
                "Datumaro reads the masks pixel for pixel",
                f"Datumaro read {sorted(dm_masks)}",
            )

        print("\nDatumaro's export, our reader")
        built = Dataset.from_iterable(
            [
                DatasetItem(
                    id="img1",
                    media=DmImage.from_numpy(np.zeros((HEIGHT, WIDTH, 3), dtype=np.uint8)),
                    annotations=[
                        Bbox(10, 12, 20, 16, label=0),
                        Mask(image=expected_mask, label=1),
                    ],
                ),
            ],
            categories=["car", "tree"],
        )
        theirs = Path(workdir) / "theirs"
        built.export(str(theirs), "tf_detection_api", save_media=True, save_masks=True)
        files = {
            str(path.relative_to(theirs)).replace("\\", "/"): path.read_bytes()
            for path in theirs.rglob("*")
            if path.is_file()
        }
        result = fmt.import_(
            MemoryImportSource(files),
            ImportContext(
                known_labels={},
                frame_by_name={"img1.jpg": 0},
                frame_count=1,
                frame_sizes={0: (WIDTH, HEIGHT)},
            ),
        )
        arrived = {s.label: s for _, s in result.shapes}
        check(
            result.warnings == [] and sorted(arrived) == ["car", "tree"],
            "every object Datumaro wrote is read",
            f"read {sorted(arrived)}: {result.warnings}",
        )
        # Datumaro writes a mask for every object when masks are on; a box's is empty and
        # it stays a box.
        car = arrived.get("car")
        check(
            car is not None
            and car.shape_type is ShapeType.RECTANGLE
            and [round(v, 3) for v in car.points] == [10, 12, 30, 28],
            "a box is at its pixels",
            f"the car arrived as {car}",
        )
        tree = arrived.get("tree")
        region = np.zeros((HEIGHT, WIDTH), dtype=bool)
        if tree is not None and tree.mask:
            m = tree.mask
            for index, flag in enumerate(decode(m["rle"], m["width"], m["height"])):
                region[m["top"] + index // m["width"], m["left"] + index % m["width"]] = flag
        check(
            tree is not None
            and tree.shape_type is ShapeType.MASK
            and bool((region == expected_mask).all()),
            "a mask arrives pixel for pixel",
            f"the tree arrived as {tree}",
        )

    return finish(failures)


def finish(failures: list[str]) -> int:
    print()
    if failures:
        print(f"{len(failures)} check(s) failed")
        return 1
    print("every check passed")
    return 0


if __name__ == "__main__":
    sys.exit(main())
