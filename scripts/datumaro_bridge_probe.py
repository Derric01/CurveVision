#!/usr/bin/env python3
"""Measure what each Datumaro format carries through CurveVision's bridge.

    python scripts/datumaro_bridge_probe.py          # rewrite datumaro_formats.json
    python scripts/datumaro_bridge_probe.py --check  # fail if it no longer matches

`server/curvevision/formats/datumaro_bridge.py` declares, for every bridged format, which
shape types, whether tags and whether attributes it carries — the declaration the export
API warns from before anything is dropped. Datumaro's formats are too many and too varied to
declare by reading their documentation, so this measures instead: for each format and each
shape type it exports a frame holding one shape of that type through the bridge, imports
the result back through it, and records whether the shape came back — with its label, and
its box within a pixel and a half of where it was — and as what. A tag and an attribute are
measured the same way. A format that carries nothing is not bridged.

Needs the server package (with Pillow) and ``datumaro`` in one interpreter. Run by
`.github/workflows/datumaro.yml`, which uses ``--check``.
"""

from __future__ import annotations

import io
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
TABLE = ROOT / "server" / "curvevision" / "formats" / "datumaro_formats.json"
WIDTH, HEIGHT = 64, 48

#: The Datumaro formats tried, with the name each is shown under. Formats this project
#: writes itself are not bridged; a format is kept only when something survives it.
CANDIDATES = {
    "datumaro": "Datumaro",
    "label_me": "LabelMe 3.0 (XML)",
    "dota": "DOTA",
    "segment_anything": "Segment Anything (SA-1B)",
    "camvid": "CamVid",
    "voc_segmentation": "Pascal VOC segmentation",
    "voc_instance_segmentation": "Pascal VOC instance segmentation",
    "coco_panoptic": "COCO panoptic",
    "imagenet": "ImageNet (folders)",
    "imagenet_txt": "ImageNet (text)",
    "cifar": "CIFAR",
    "mnist": "MNIST",
    "mnist_csv": "MNIST (CSV)",
    "lfw": "LFW",
    "vgg_face2": "VGGFace2",
    "wider_face": "WIDER FACE",
    "market1501": "Market-1501",
    "icdar_text_localization": "ICDAR text localization",
    "icdar_text_segmentation": "ICDAR text segmentation",
    "mvtec_classification": "MVTec AD (classification)",
    "mvtec_detection": "MVTec AD (detection)",
    "mvtec_segmentation": "MVTec AD (segmentation)",
}


def measure() -> list[dict]:
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
    )
    from curvevision.formats.base import bounding_box
    from curvevision.formats.datumaro_bridge import Bridged, DatumaroFormat
    from curvevision.formats.rle import encode
    from PIL import Image

    buffer = io.BytesIO()
    Image.new("RGB", (WIDTH, HEIGHT), "#336699").save(buffer, format="PNG")
    picture = buffer.getvalue()

    mask_flags = [True] * 30  # a 6x5 block
    probes = {
        ShapeType.RECTANGLE: ShapeRecord("thing", ShapeType.RECTANGLE, [10, 8, 40, 30]),
        ShapeType.ROTATED_RECTANGLE: ShapeRecord(
            "thing", ShapeType.ROTATED_RECTANGLE, [10, 8, 40, 30], rotation=30
        ),
        ShapeType.POLYGON: ShapeRecord("thing", ShapeType.POLYGON, [10, 8, 40, 8, 25, 30]),
        ShapeType.POLYLINE: ShapeRecord("thing", ShapeType.POLYLINE, [10, 8, 40, 30, 20, 40]),
        ShapeType.POINTS: ShapeRecord("thing", ShapeType.POINTS, [10, 8, 40, 30]),
        ShapeType.ELLIPSE: ShapeRecord("thing", ShapeType.ELLIPSE, [32, 24, 12, 8]),
        ShapeType.MASK: ShapeRecord(
            "thing",
            ShapeType.MASK,
            [20, 10, 25, 14],
            mask={"rle": encode(mask_flags, 6, 5), "left": 20, "top": 10, "width": 6, "height": 5},
        ),
    }
    labels = [
        LabelSpec(
            "1",
            "thing",
            attributes=(AttributeSpec("note", AttributeType.TEXT),),
        ),
        LabelSpec("2", "other"),
    ]
    context = ImportContext(
        known_labels={},
        frame_by_name={"probe.png": 0},
        frame_count=1,
        frame_sizes={0: (WIDTH, HEIGHT)},
    )

    def round_trip(fmt: DatumaroFormat, frame: FrameRecord):
        sink = MemoryExportSink()
        try:
            fmt.export(DatasetView("Probe", labels, iter([frame])), sink)
            files = {k: v if isinstance(v, bytes) else v.encode() for k, v in sink.files.items()}
            return fmt.import_(MemoryImportSource(files), context)
        except Exception as exc:  # what a format refuses is the finding
            return exc

    def extent(shape: ShapeRecord) -> list[float]:
        if shape.shape_type is ShapeType.MASK and shape.mask:
            m = shape.mask
            return [m["left"], m["top"], m["left"] + m["width"] - 1, m["top"] + m["height"] - 1]
        if shape.shape_type is ShapeType.ELLIPSE:
            cx, cy, rx, ry = shape.points[:4]
            return [cx - rx, cy - ry, cx + rx, cy + ry]
        if shape.shape_type in (ShapeType.RECTANGLE, ShapeType.ROTATED_RECTANGLE):
            return list(shape.points[:4])
        return list(bounding_box(shape.points))

    table = []
    for datumaro_id, name in CANDIDATES.items():
        everything = Bridged(datumaro_id, name, tuple(probes), True, True, {}, "")
        fmt = DatumaroFormat(everything)
        carried, becomes = [], {}
        for kind, probe in probes.items():
            frame = FrameRecord(
                0, "probe.png", WIDTH, HEIGHT, shapes=[probe], tags=["other"], media=picture
            )
            result = round_trip(fmt, frame)
            if isinstance(result, Exception):
                continue
            back = [s for _, s in result.shapes if s.label == "thing"]
            if len(back) != 1:
                continue
            sent = extent(probe) if kind is not ShapeType.ROTATED_RECTANGLE else None
            got = extent(back[0])
            if sent is not None and any(abs(a - b) > 1.5 for a, b in zip(sent, got, strict=True)):
                continue
            carried.append(kind.value)
            if back[0].shape_type is not kind:
                becomes[kind.value] = back[0].shape_type.value
        tagged = round_trip(
            fmt, FrameRecord(0, "probe.png", WIDTH, HEIGHT, tags=["other"], media=picture)
        )
        tags = not isinstance(tagged, Exception) and [t for _, t in tagged.tags] == ["other"]
        attributes = False
        if carried:
            first = probes[ShapeType(carried[0])]
            noted = ShapeRecord(
                first.label,
                first.shape_type,
                first.points,
                mask=first.mask,
                rotation=first.rotation,
                attributes={"note": "kept"},
            )
            result = round_trip(
                fmt,
                FrameRecord(
                    0, "probe.png", WIDTH, HEIGHT, shapes=[noted], tags=["other"], media=picture
                ),
            )
            attributes = not isinstance(result, Exception) and any(
                s.attributes.get("note") == "kept" for _, s in result.shapes
            )
        if not carried and not tags:
            print(f"  -    {datumaro_id}: carries nothing through the bridge; not bridged")
            continue
        print(
            f"  +    {datumaro_id}: {carried or 'no shapes'}"
            f"{', tags' if tags else ''}{', attributes' if attributes else ''}"
            f"{f', {becomes}' if becomes else ''}"
        )
        table.append(
            {
                "datumaro_id": datumaro_id,
                "name": name,
                "shape_types": carried,
                "tags": tags,
                "attributes": attributes,
                "becomes": becomes,
            }
        )
    return table


def main() -> int:
    table = measure()
    text = json.dumps(table, indent=2) + "\n"
    if "--check" in sys.argv:
        recorded = TABLE.read_text() if TABLE.exists() else ""
        if recorded != text:
            print(f"\n{TABLE.relative_to(ROOT)} no longer matches what Datumaro does.")
            print("Rerun without --check, review the difference, and commit it.")
            return 1
        print(f"\n{TABLE.relative_to(ROOT)} matches: {len(table)} formats bridged.")
        return 0
    TABLE.write_text(text)
    print(f"\nwrote {TABLE.relative_to(ROOT)}: {len(table)} formats bridged.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
