#!/usr/bin/env python3
"""CurveVision's Cityscapes export, drawn by cityscapesScripts and read by Datumaro.

    python scripts/verify_cityscapes.py

`server/tests/unit/test_formats_cityscapes.py` holds the format to what this project
believes Cityscapes means. A belief can be wrong in the same way as the code, so this runs
the dataset's own tools:

1. **Our polygons, their drawing.** cityscapesScripts reads every polygons file of an export
   and draws the label, training-id, colour and instance images from it, as the dataset's own
   label images were made. Each must equal the image exported beside it, pixel for pixel —
   with a mask, which has no polygon, painted over theirs.
2. **Their label images, our reader.** Instance and label-id images drawn by cityscapesScripts
   from a polygons file are imported without it, and each instance must arrive as the mask of
   exactly its pixels.
3. **Datumaro reads the export** — both kinds: Cityscapes' own classes, and a project's own
   labels numbered in ``label_colors.txt``.

Written from the format; cityscapesScripts and Datumaro are used here only as separate
programs' readers, never shipped. Needs the server package (with Pillow), numpy,
``cityscapesscripts`` and ``datumaro`` in one interpreter. Run by
`.github/workflows/cityscapes.yml`.
"""

from __future__ import annotations

import io
import json
import sys
import tempfile
from pathlib import Path

WIDTH, HEIGHT = 96, 64


def main() -> int:
    import numpy as np
    from cityscapesscripts.helpers.annotation import Annotation
    from cityscapesscripts.helpers.labels import name2label
    from cityscapesscripts.preparation.json2instanceImg import createInstanceImage
    from cityscapesscripts.preparation.json2labelImg import createLabelImage
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

    def array(data: bytes) -> np.ndarray:
        return np.array(Image.open(io.BytesIO(data)))

    fmt = get_format("cityscapes")

    # A 5x4 mask with a hole and a missing corner: no symmetry, so a mask painted flipped or
    # turned cannot pass for this one.
    flags = [not (x in (1, 2) and y == 1) and (x, y) != (4, 3) for y in range(4) for x in range(5)]
    mask = {"rle": encode(flags, 5, 4), "left": 80, "top": 50, "width": 5, "height": 4}
    street = [
        ShapeRecord("road", ShapeType.POLYGON, [0, 40, 95, 40, 95, 63, 0, 63], z_order=0),
        ShapeRecord("car", ShapeType.RECTANGLE, [10, 30, 30, 45], z_order=1),
        ShapeRecord("car", ShapeType.POLYGON, [25, 35, 45, 33, 44, 50, 26, 52], z_order=2),
        ShapeRecord("cargroup", ShapeType.POLYGON, [50, 30, 70, 30, 70, 45], z_order=3),
        ShapeRecord("license plate", ShapeType.RECTANGLE, [14, 40, 20, 43], z_order=4),
        ShapeRecord("person", ShapeType.ELLIPSE, [60, 15, 6, 10], z_order=5),
        ShapeRecord("traffic sign", ShapeType.ROTATED_RECTANGLE, [5, 5, 15, 12], rotation=20,
                    z_order=6),
        ShapeRecord("person", ShapeType.POLYGON, [75, 10, 85, 10, 80, 30], z_order=7),
    ]
    painted = [*street, ShapeRecord("building", ShapeType.MASK, [80, 50, 84, 53], mask=mask,
                                    z_order=9)]
    cityscapes_labels = [
        LabelSpec(str(i), name)
        for i, name in enumerate(["road", "car", "cargroup", "license plate", "person",
                                  "traffic sign", "building"], 1)
    ]
    frames = [
        FrameRecord(0, "aachen/street.png", WIDTH, HEIGHT, shapes=street, media=picture()),
        FrameRecord(1, "aachen/painted.png", WIDTH, HEIGHT, shapes=painted, media=picture()),
    ]
    sink = MemoryExportSink()
    fmt.export(DatasetView("Street", cityscapes_labels, iter(frames)), sink)

    with tempfile.TemporaryDirectory(prefix="curvevision-cityscapes-") as workdir:
        root = Path(workdir) / "cityscapes"
        for path, data in sink.files.items():
            (root / path).parent.mkdir(parents=True, exist_ok=True)
            (root / path).write_bytes(data if isinstance(data, bytes) else data.encode())

        print("our polygons, cityscapesScripts' drawing")
        for stem in ("street", "painted"):
            base = root / "gtFine" / "default" / "aachen" / f"{stem}_gtFine_"
            annotation = Annotation()
            annotation.fromJsonFile(str(base) + "polygons.json")
            theirs = {
                "labelIds": np.array(createLabelImage(annotation, "ids")),
                "labelTrainIds": np.array(createLabelImage(annotation, "trainIds")),
                "color": np.array(createLabelImage(annotation, "color"))[..., :3],
                "instanceIds": np.array(createInstanceImage(annotation, "ids")),
            }
            if stem == "painted":  # the mask has no polygon: paint it over their drawing
                building = name2label["building"]
                for index, flag in enumerate(flags):
                    if flag:
                        y, x = 50 + index // 5, 80 + index % 5
                        theirs["labelIds"][y, x] = building.id
                        theirs["labelTrainIds"][y, x] = building.trainId
                        theirs["color"][y, x] = building.color
                        theirs["instanceIds"][y, x] = building.id
            for kind, expected in theirs.items():
                ours = array((Path(str(base) + f"{kind}.png")).read_bytes())
                differ = int((ours.astype(np.int64) != expected.astype(np.int64)).sum())
                check(differ == 0, f"{stem}: {kind} is cityscapesScripts' drawing, pixel for pixel",
                      f"{stem}: {kind} differs from cityscapesScripts' drawing in {differ} values")
        check(not (root / "label_colors.txt").exists(),
              "Cityscapes' own classes are not listed again in label_colors.txt",
              "label_colors.txt was written for Cityscapes' own classes")

        print("\ncityscapesScripts' label images, our reader")
        written = {"imgHeight": HEIGHT, "imgWidth": WIDTH, "objects": [
            {"label": "car", "polygon": [[10, 10], [30, 10], [30, 25], [10, 25]]},
            {"label": "car", "polygon": [[40, 12], [60, 12], [50, 30]]},
            {"label": "road", "polygon": [[0, 40], [95, 40], [95, 63], [0, 63]]},
        ]}
        annotation = Annotation()
        annotation.fromJsonText(json.dumps(written))
        instance_image = createInstanceImage(annotation, "ids")
        buffer = io.BytesIO()
        # json2instanceImg saves the 32-bit image as it is, which Pillow writes as 16 bits.
        instance_image.convert("I;16").save(buffer, format="PNG")
        result = fmt.import_(
            MemoryImportSource({"gtFine/val/x/y_gtFine_instanceIds.png": buffer.getvalue()}),
            ImportContext(known_labels={}, frame_by_name={"y_leftImg8bit.png": 0}, frame_count=1),
        )
        drawn = np.array(instance_image)
        arrived = {}
        for _, shape in result.shapes:
            region = np.zeros((HEIGHT, WIDTH), dtype=bool)
            m = shape.mask or {}
            pixels = decode(m["rle"], m["width"], m["height"])
            for index, flag in enumerate(pixels):
                if flag:
                    region[m["top"] + index // m["width"], m["left"] + index % m["width"]] = True
            arrived.setdefault(shape.label, []).append(region)
        expected_regions = {
            "car": [drawn == 26000, drawn == 26001],
            "road": [drawn == 7],
        }
        same = sorted(arrived) == sorted(expected_regions) and all(
            len(arrived[k]) == len(v)
            and all((a == b).all() for a, b in zip(arrived[k], v, strict=True))
            for k, v in expected_regions.items()
        )
        check(result.warnings == [] and same,
              "each instance arrives as the mask of exactly its pixels, the road as one",
              f"imported {[(s.label, s.points) for _, s in result.shapes]}: {result.warnings}")

        print("\nDatumaro reads the export")
        from datumaro.components.annotation import AnnotationType
        from datumaro.components.dataset import Dataset

        dataset = Dataset.import_from(str(root), "cityscapes")
        items = {item.id: item for item in dataset}
        check(sorted(items) == ["aachen/painted", "aachen/street"]
              and all(item.media is not None for item in items.values()),
              "every frame is an item, its image found beside it",
              f"Datumaro read items {sorted(items)}")
        names = dataset.categories()[AnnotationType.label]
        train = array((root / "gtFine/default/aachen/painted_gtFine_labelTrainIds.png")
                      .read_bytes())
        masks = {names[a.label].name: a.image for a in items["aachen/painted"].annotations}
        # Datumaro skips label index 0 when it reads a label image, which is its background
        # everywhere but in its training-id map, where index 0 is the road: it reads no road
        # from any Cityscapes training-id image, the dataset's own included.
        expected = {
            "car": train == 13, "person": train == 11,
            "trafficsign": train == 7, "building": train == 2,
        }
        check(sorted(masks) == sorted(expected)
              and all((masks[k] == v).all() for k, v in expected.items()),
              "every class is the mask of its pixels, under Cityscapes' training ids",
              f"Datumaro read {sorted(masks)}")

        custom = Path(workdir) / "custom"
        sink = MemoryExportSink()
        fmt.export(DatasetView("Yard", [LabelSpec("1", "crate", color="#ff8800"),
                                        LabelSpec("2", "pallet", color="#0088ff")],
                               iter([FrameRecord(0, "yard.png", WIDTH, HEIGHT, media=picture(),
                                                 shapes=[
            ShapeRecord("pallet", ShapeType.RECTANGLE, [5, 5, 40, 30]),
            ShapeRecord("crate", ShapeType.POLYGON, [20, 10, 50, 10, 50, 40], z_order=1),
            ShapeRecord("crate", ShapeType.RECTANGLE, [60, 20, 80, 50]),
        ])])), sink)
        for path, data in sink.files.items():
            (custom / path).parent.mkdir(parents=True, exist_ok=True)
            (custom / path).write_bytes(data if isinstance(data, bytes) else data.encode())
        dataset = Dataset.import_from(str(custom), "cityscapes")
        names = dataset.categories()[AnnotationType.label]
        item = next(iter(dataset))
        ids = array((custom / "gtFine/default/yard_gtFine_labelIds.png").read_bytes())
        masks = {names[a.label].name: a.image for a in item.annotations}
        check(sorted(masks) == ["crate", "pallet"]
              and (masks["pallet"] == (ids == 2)).all() and (masks["crate"] == (ids == 1)).all(),
              "a project's own labels arrive by name, each the mask of its pixels",
              f"Datumaro read {sorted(masks)} from a project's own labels")
        listed = (custom / "label_colors.txt").read_text().splitlines()
        check(listed == ["0 0 0 background", "255 136 0 crate", "0 136 255 pallet"],
              "label_colors.txt lists the background, then the labels with their colours",
              f"label_colors.txt is {listed}")
        print(json.dumps({"datumaro_items": len(items)}))

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
