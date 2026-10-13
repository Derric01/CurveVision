#!/usr/bin/env python3
"""CurveVision's LabelMe export, read by labelme itself — and labelme's files, read by us.

    python scripts/verify_labelme.py

`server/tests/unit/test_formats_labelme.py` holds the format to what this project believes
labelme means. A belief can be wrong in the same way as the code, so this runs the tool:

1. **Our files, labelme's reader.** An export with every shape CurveVision writes — boxes,
   a rotated box, polygon, polyline, points, a circle, an ellipse that is not one, a mask —
   is opened with labelme's own `LabelFile`, which checks the image beside it and its size,
   and every shape is drawn with labelme's own rasteriser. What it draws is compared with
   the source geometry, computed here: a box's area, a circle's, the mask pixel for pixel.
2. **labelme's files, our reader.** A document written by labelme's own `write_label_file`
   is imported, and what arrives is compared with what was written.

Written from the format; labelme (GPL-3.0) is used here only as a separate program's reader
and writer, never shipped. Needs the server package and `labelme` in one interpreter. Run by
`.github/workflows/labelme.yml`.
"""

from __future__ import annotations

import base64
import io
import json
import math
import sys
import tempfile
from pathlib import Path

WIDTH, HEIGHT = 160, 120


def main() -> int:
    import numpy as np
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
        TagRecord,
        get_format,
    )
    from curvevision.formats.rle import encode
    from labelme import utils
    from labelme._label_file import LabelFile, write_label_file
    from PIL import Image

    failures: list[str] = []

    def check(condition: bool, passed: str, failed: str) -> None:
        if condition:
            print(f"  ok   {passed}")
        else:
            failures.append(failed)
            print(f"  FAIL {failed}")

    def picture(kind: str) -> bytes:
        buffer = io.BytesIO()
        Image.new("RGB", (WIDTH, HEIGHT), "#336699").save(buffer, format=kind)
        return buffer.getvalue()

    # A 6x4 mask at (100, 70), with a hole and a missing corner: no symmetry, so a mask
    # painted flipped or turned cannot pass for this one.
    mask_flags = [
        not (x in (1, 2) and y in (1, 2)) and (x, y) != (5, 0) for y in range(4) for x in range(6)
    ]
    mask = {"rle": encode(mask_flags, 6, 4), "left": 100, "top": 70, "width": 6, "height": 4}
    shapes = [
        ShapeRecord("car", ShapeType.RECTANGLE, [10, 10, 40, 30], attributes={"parked": True}),
        ShapeRecord("car", ShapeType.ROTATED_RECTANGLE, [60, 10, 100, 30], rotation=30),
        ShapeRecord("road", ShapeType.POLYGON, [10, 60, 60, 60, 35, 100]),
        ShapeRecord("road", ShapeType.POLYLINE, [70, 100, 90, 110, 110, 100]),
        ShapeRecord("sign", ShapeType.POINTS, [130, 10, 140, 20]),
        ShapeRecord("sign", ShapeType.ELLIPSE, [130, 50, 10, 10]),
        ShapeRecord("sign", ShapeType.ELLIPSE, [130, 90, 15, 6]),
        ShapeRecord("road", ShapeType.MASK, [100, 70, 105, 73], mask=mask),
    ]
    labels = [
        LabelSpec("1", "car", attributes=(AttributeSpec("parked", AttributeType.CHECKBOX),)),
        LabelSpec("2", "road"),
        LabelSpec("3", "sign"),
        LabelSpec("4", "night"),
    ]
    frames = [
        FrameRecord(0, "street/a.png", WIDTH, HEIGHT, shapes=shapes, tags=["night"],
                    tag_records=[TagRecord("night")], media=picture("PNG")),
        FrameRecord(1, "street/b.jpg", WIDTH, HEIGHT, media=picture("JPEG")),
    ]
    sink = MemoryExportSink()
    get_format("labelme").export(DatasetView("Street", labels, iter(frames)), sink)

    with tempfile.TemporaryDirectory(prefix="curvevision-labelme-") as workdir:
        root = Path(workdir)
        for path, data in sink.files.items():
            (root / path).parent.mkdir(parents=True, exist_ok=True)
            (root / path).write_bytes(data)

        print("our export, labelme's reader")
        documents = sorted(root.rglob("*.json"))
        loaded = {}
        for path in documents:
            try:
                loaded[path.name] = LabelFile(str(path))
            except Exception as exc:  # what labelme refuses is the finding
                loaded[path.name] = exc
        check(
            all(isinstance(v, LabelFile) for v in loaded.values()) and len(loaded) == 2,
            "labelme opens every file, with its image beside it at the size it says",
            f"labelme refused: {[str(v) for v in loaded.values() if not isinstance(v, LabelFile)]}",
        )
        first = loaded.get("a.json")
        if not isinstance(first, LabelFile):
            return finish(failures)

        kinds = [shape["shape_type"] for shape in first.shapes]
        check(
            sorted(kinds) == sorted(["rectangle", "oriented_rectangle", "polygon", "linestrip",
                                     "point", "point", "circle", "polygon", "mask"]),
            "every shape arrives as the labelme shape it should be",
            f"labelme read the shapes as {kinds}",
        )
        check(first.flags == {"night": True}, "the tag is the image's flag",
              f"image flags {first.flags}")
        car = next(s for s in first.shapes if s["shape_type"] == "rectangle")
        check(car["flags"] == {"parked": True}, "a checkbox is the shape's flag",
              f"the box's flags {car['flags']}")

        def drawn(shape: dict) -> np.ndarray:
            if shape["shape_type"] == "mask":
                _, instances = utils.shapes_to_label((HEIGHT, WIDTH), [shape], {shape["label"]: 1})
                return instances > 0
            return utils.shape_to_mask((HEIGHT, WIDTH), shape["points"], shape["shape_type"])

        by_kind = {}
        for shape in first.shapes:
            by_kind.setdefault(shape["shape_type"], []).append(shape)

        area = drawn(car).sum()
        check(abs(area - 31 * 21) <= 1, "the box covers the pixels it bounds",
              f"labelme drew the box over {area} pixels, not {31 * 21}")
        oriented = drawn(by_kind["oriented_rectangle"][0]).sum()
        check(abs(oriented - 40 * 20) <= 0.15 * 40 * 20,
              "the rotated box is drawn at its size, turned",
              f"labelme drew the rotated box over {oriented} pixels, about {40 * 20} expected")
        circle = drawn(by_kind["circle"][0]).sum()
        check(abs(circle - math.pi * 100) <= 0.15 * math.pi * 100,
              "the circle is drawn at its radius", f"labelme drew the circle over {circle} pixels")
        ellipse = max(by_kind["polygon"], key=lambda s: len(s["points"]))
        traced = drawn(ellipse).sum()
        check(abs(traced - math.pi * 15 * 6) <= 0.2 * math.pi * 15 * 6,
              "an ellipse that is not a circle is drawn as itself, not as a circle",
              f"labelme drew the ellipse over {traced} pixels, about {math.pi * 90:.0f} expected")
        painted = drawn(by_kind["mask"][0])
        expected = np.zeros((HEIGHT, WIDTH), dtype=bool)
        for index, flag in enumerate(mask_flags):
            expected[70 + index // 6, 100 + index % 6] = flag
        check(bool((painted == expected).all()),
              "the mask is painted pixel for pixel, hole and all",
              f"labelme painted {int((painted != expected).sum())} pixels differently from "
              f"the mask ({int(painted.sum())} painted, {int(expected.sum())} in the mask)")

        print("\nlabelme's file, our reader")
        written = root / "c.json"
        (root / "c.png").write_bytes(picture("PNG"))
        crop = io.BytesIO()
        Image.fromarray(np.array([[1, 0], [1, 1]], dtype=np.uint8) * 255).save(crop, format="PNG")
        write_label_file(
            str(written),
            shapes=[
                {"label": "car", "points": [[5, 5], [25, 15]], "shape_type": "rectangle",
                 "group_id": 7, "flags": {"parked": False}, "description": "red", "mask": None},
                {"label": "sign", "points": [[50, 50], [50, 58]], "shape_type": "circle",
                 "group_id": None, "flags": {}, "description": "", "mask": None},
                {"label": "road", "points": [[0, 100], [20, 110]], "shape_type": "line",
                 "group_id": None, "flags": {}, "description": "", "mask": None},
            ],
            image_path="c.png",
            image_height=HEIGHT,
            image_width=WIDTH,
            flags={"night": True},
        )
        body = json.loads(written.read_text())
        body["shapes"].append({
            "label": "road", "points": [[10, 10], [11, 11]], "shape_type": "mask",
            "group_id": None, "flags": {}, "description": "",
            "mask": base64.b64encode(crop.getvalue()).decode(),
        })
        written.write_text(json.dumps(body))
        LabelFile(str(written))  # still a file labelme accepts
        result = get_format("labelme").import_(
            MemoryImportSource({"c.json": written.read_bytes()}),
            ImportContext(known_labels={}, frame_by_name={"c.png": 0}, frame_count=1),
        )
        arrived = {s.shape_type: s for _, s in result.shapes}
        check(result.warnings == [] and len(result.shapes) == 4,
              "every shape labelme wrote is imported",
              f"imported {len(result.shapes)}: {result.warnings}")
        box = arrived.get(ShapeType.RECTANGLE)
        check(box is not None and box.points == [5, 5, 25, 15] and box.group == 7
              and box.attributes == {"parked": False, "description": "red"},
              "a box keeps its corners, group, flag and description", f"the box arrived as {box}")
        circle_back = arrived.get(ShapeType.ELLIPSE)
        check(circle_back is not None and circle_back.points == [50, 50, 8, 8],
              "a circle arrives as an ellipse of its radius",
              f"the circle arrived as {circle_back}")
        mask_back = arrived.get(ShapeType.MASK)
        check(mask_back is not None and mask_back.mask is not None
              and (mask_back.mask["left"], mask_back.mask["top"]) == (10, 10)
              and mask_back.mask["rle"] == encode([True, False, True, True], 2, 2),
              "a mask arrives pixel for pixel", f"the mask arrived as {mask_back}")
        check([tag for _, tag in result.tags] == ["night"], "the image's flag is a tag",
              f"tags {result.tags}")

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
