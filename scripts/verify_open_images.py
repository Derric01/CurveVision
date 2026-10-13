#!/usr/bin/env python3
"""CurveVision's Open Images export, read by Datumaro — and Datumaro's, read by us.

    python scripts/verify_open_images.py

`server/tests/unit/test_formats_open_images.py` holds the format to what this project
believes Open Images means. A belief can be wrong in the same way as the code, so this has
a reader written by other people check it, both ways:

1. **Our export, Datumaro's reader.** Every box must arrive at its pixel coordinates with
   its flags, every mask pixel for pixel, every tag as an image-level label — with and
   without the images in the archive (``images.meta`` carries their sizes).
2. **Datumaro's export, our reader.** A dataset built in Datumaro and written by its own
   Open Images exporter — boxes with flags, a mask grouped with its box, image labels — is
   imported, and what arrives is compared with what was written.

Written from the format; Datumaro is used here only as a separate program's reader and
writer, never shipped. Needs the server package (with Pillow), numpy and ``datumaro`` in
one interpreter. Run by `.github/workflows/open_images.yml`.
"""

from __future__ import annotations

import io
import shutil
import sys
import tempfile
from pathlib import Path

WIDTH, HEIGHT = 80, 60


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
    from curvevision.formats.rle import decode, encode
    from datumaro.components.annotation import AnnotationType, Bbox, Label, Mask
    from datumaro.components.dataset import Dataset
    from datumaro.components.dataset_base import DatasetItem
    from datumaro.components.media import Image as DmImage
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

    fmt = get_format("open_images")

    # A 6x4 mask with a hole and a missing corner: no symmetry, so a mask painted flipped or
    # turned cannot pass for this one.
    flags = [not (x in (2, 3) and y == 1) and (x, y) != (5, 3) for y in range(4) for x in range(6)]
    mask = {"rle": encode(flags, 6, 4), "left": 50, "top": 40, "width": 6, "height": 4}
    expected_mask = np.zeros((HEIGHT, WIDTH), dtype=bool)
    for index, flag in enumerate(flags):
        expected_mask[40 + index // 6, 50 + index % 6] = flag
    labels = [
        LabelSpec(
            "1",
            "Car",
            attributes=(
                AttributeSpec("truncated", AttributeType.CHECKBOX),
                AttributeSpec("is_group_of", AttributeType.CHECKBOX),
            ),
        ),
        LabelSpec("2", "Tree"),
        LabelSpec("3", "Daytime"),
    ]
    shapes = [
        ShapeRecord(
            "Car",
            ShapeType.RECTANGLE,
            [8, 6, 40, 30],
            occluded=True,
            attributes={"truncated": True, "is_group_of": False},
        ),
        ShapeRecord("Tree", ShapeType.MASK, [50, 40, 55, 43], mask=mask),
    ]
    frames = [
        FrameRecord(
            0,
            "street/a.png",
            WIDTH,
            HEIGHT,
            shapes=shapes,
            tags=["Daytime"],
            tag_records=[TagRecord("Daytime")],
            media=picture(),
        ),
        FrameRecord(1, "street/b.png", WIDTH, HEIGHT, media=picture()),
    ]

    def export(with_media: bool) -> MemoryExportSink:
        sink = MemoryExportSink()
        chosen = (
            frames
            if with_media
            else [
                FrameRecord(
                    f.index,
                    f.name,
                    f.width,
                    f.height,
                    shapes=f.shapes,
                    tags=f.tags,
                    tag_records=f.tag_records,
                )
                for f in frames
            ]
        )
        fmt.export(DatasetView("Street", labels, iter(chosen)), sink)
        return sink

    with tempfile.TemporaryDirectory(prefix="curvevision-open-images-") as workdir:
        for with_media in (True, False):
            kind = "with its images" if with_media else "without its images"
            print(f"our export {kind}, Datumaro's reader")
            root = Path(workdir) / f"ours-{with_media}"
            for path, data in export(with_media).files.items():
                (root / path).parent.mkdir(parents=True, exist_ok=True)
                (root / path).write_bytes(data if isinstance(data, bytes) else data.encode())
            dataset = Dataset.import_from(str(root), "open_images")
            names = dataset.categories()[AnnotationType.label]
            items = {item.id: item for item in dataset}
            check(
                sorted(items) == ["street/a", "street/b"],
                "every frame is an item, named by its path",
                f"Datumaro read items {sorted(items)}",
            )
            item = items.get("street/a")
            if item is None:
                continue
            by_type: dict[AnnotationType, list] = {}
            for annotation in item.annotations:
                by_type.setdefault(annotation.type, []).append(annotation)
            tags = [names[a.label].name for a in by_type.get(AnnotationType.label, [])]
            check(tags == ["Daytime"], "the tag is an image-level label", f"labels {tags}")
            boxes = {names[b.label].name: b for b in by_type.get(AnnotationType.bbox, [])}
            car = boxes.get("Car")
            check(
                car is not None and np.allclose(car.get_bbox(), [8, 6, 32, 24], atol=1e-3),
                "the box arrives at its pixel coordinates",
                f"the car arrived as {car and car.get_bbox()}",
            )
            check(
                car is not None
                and car.attributes.get("occluded") is True
                and car.attributes.get("truncated") is True
                and car.attributes.get("is_group_of") is False
                and "is_inside" not in car.attributes,
                "its flags arrive, and the one never recorded is absent",
                f"the car's attributes {car and car.attributes}",
            )
            masks = by_type.get(AnnotationType.mask, [])
            tree = masks[0] if masks else None
            check(
                len(masks) == 1
                and tree is not None
                and names[tree.label].name == "Tree"
                and bool((tree.image == expected_mask).all()),
                "the mask arrives pixel for pixel, hole and all",
                f"masks {[(names[m.label].name, int(m.image.sum())) for m in masks]}",
            )
            tree_box = boxes.get("Tree")
            check(
                tree_box is not None
                and tree is not None
                and tree.group == tree_box.group
                and np.allclose(tree_box.get_bbox(), [50, 40, 6, 4], atol=1e-3),
                "the mask's box is written too, and Datumaro pairs the two",
                f"the tree's box {tree_box and tree_box.get_bbox()}",
            )

        print("\nDatumaro's export, our reader")
        built = Dataset.from_iterable(
            [
                DatasetItem(
                    id="img1",
                    subset="train",
                    media=DmImage.from_numpy(np.zeros((HEIGHT, WIDTH, 3), dtype=np.uint8)),
                    annotations=[
                        Label(2),
                        Bbox(
                            10,
                            12,
                            20,
                            16,
                            label=0,
                            group=1,
                            attributes={"occluded": True, "is_depiction": True},
                        ),
                        Mask(image=expected_mask, label=1, group=2),
                        Bbox(50, 40, 6, 4, label=1, group=2),
                    ],
                ),
            ],
            categories=["Car", "Tree", "Daytime"],
        )
        theirs = Path(workdir) / "theirs"
        built.export(str(theirs), "open_images", save_media=True)
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
            result.warnings == []
            and sorted(arrived) == ["Car", "Tree"]
            and len(result.shapes) == 2,
            "every object arrives once — the mask and its box as one",
            f"imported {[(s.label, s.shape_type.value) for _, s in result.shapes]}: "
            f"{result.warnings}",
        )
        car = arrived.get("Car")
        check(
            car is not None
            and car.points == [10, 12, 30, 28]
            and car.occluded
            and car.attributes == {"is_depiction": True},
            "a box keeps its corners, occlusion and flags",
            f"the car arrived as {car}",
        )
        tree = arrived.get("Tree")
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
        check(
            [tag for _, tag in result.tags] == ["Daytime"],
            "an image label is a tag",
            f"tags {result.tags}",
        )
        shutil.rmtree(theirs)

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
