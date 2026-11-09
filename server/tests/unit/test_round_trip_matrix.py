"""Every format, one awkward dataset, and what comes back.

The per-format tests each use their own tidy example. This sends every format that both
writes and reads the same awkward frame -- sub-pixel coordinates, a box exactly the size of
the image, label names in French and Japanese and with a space, a 1920x1080 frame, and
every geometry the format declares -- and holds each to one invariant: **every shape comes
back, under its label, covering the same region.**

A format may change a shape's *type* where it has no word for it (a rotated box becomes a
polygon in COCO, everything is an oriented box in YOLO OBB, a polygon is a mask in Open
Images). That is a conversion the format's capabilities admit to, not a loss. What it may
not do is move it, resize it, rename its label or drop it.
"""

from __future__ import annotations

import math

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
    all_formats,
)

WIDTH, HEIGHT = 1920, 1080
LABELS = ["car", "vélo", "自転車", "traffic light"]

#: One or more of each geometry. Ellipses are `[cx, cy, rx, ry]`.
GEOMETRY: dict[ShapeType, list[list[float]]] = {
    ShapeType.RECTANGLE: [[10.25, 20.75, 300.5, 250.125], [0.0, 0.0, 1920.0, 1080.0]],
    ShapeType.POLYGON: [[400.5, 200.25, 460.75, 200.0, 460.0, 400.5, 400.0, 400.0]],
    ShapeType.POLYLINE: [[50.5, 60.5, 70.25, 80.75, 90.0, 60.0]],
    ShapeType.POINTS: [[700.25, 800.75]],
    ShapeType.ELLIPSE: [[1500.0, 500.0, 60.5, 30.25]],
    ShapeType.ROTATED_RECTANGLE: [[800.0, 300.0, 900.0, 350.0]],
}

#: Formats that store pixels or integers, so a region may move by up to a pixel.
RASTER = {"open_images", "tfrecord", "cityscapes", "segmentation_mask"}


def region(shape: ShapeRecord) -> tuple[float, float, float, float]:
    """The shape's bounding box in pixels, clipped to the frame."""
    p = shape.points
    if shape.shape_type is ShapeType.ELLIPSE:
        cx, cy, rx, ry = p[:4]
        box = (cx - abs(rx), cy - abs(ry), cx + abs(rx), cy + abs(ry))
    elif shape.shape_type is ShapeType.ROTATED_RECTANGLE and shape.rotation:
        x1, y1, x2, y2 = p[:4]
        cx, cy = (x1 + x2) / 2, (y1 + y2) / 2
        theta = math.radians(shape.rotation)
        corners = [
            (
                cx + (x - cx) * math.cos(theta) - (y - cy) * math.sin(theta),
                cy + (x - cx) * math.sin(theta) + (y - cy) * math.cos(theta),
            )
            for x, y in ((x1, y1), (x2, y1), (x2, y2), (x1, y2))
        ]
        xs, ys = [c[0] for c in corners], [c[1] for c in corners]
        box = (min(xs), min(ys), max(xs), max(ys))
    else:
        xs, ys = p[0::2], p[1::2]
        box = (min(xs), min(ys), max(xs), max(ys))
    return (max(0.0, box[0]), max(0.0, box[1]), min(WIDTH, box[2]), min(HEIGHT, box[3]))


def round_trippable() -> list[str]:
    return [
        fmt.id
        for fmt in all_formats()
        if fmt.capabilities.supports_import and fmt.capabilities.supports_export
    ]


@pytest.mark.parametrize("format_id", round_trippable())
def test_every_shape_comes_back_under_its_label_covering_the_same_region(
    format_id: str,
) -> None:
    fmt = next(f for f in all_formats() if f.id == format_id)
    sent: list[ShapeRecord] = []
    for shape_type, geometries in GEOMETRY.items():
        if shape_type not in fmt.capabilities.shape_types:
            continue
        for points in geometries:
            sent.append(
                ShapeRecord(
                    label=LABELS[len(sent) % len(LABELS)],
                    shape_type=shape_type,
                    points=list(points),
                    rotation=30.0 if shape_type is ShapeType.ROTATED_RECTANGLE else 0.0,
                )
            )
    assert sent, f"{format_id} declares no geometry this test sends"
    view = DatasetView(
        name="matrix",
        labels=[LabelSpec(id=str(i), name=name) for i, name in enumerate(LABELS)],
        frames=[
            FrameRecord(index=0, name="img_000.jpg", width=WIDTH, height=HEIGHT, shapes=sent),
            FrameRecord(index=1, name="img_001.jpg", width=WIDTH, height=HEIGHT),
        ],
    )

    sink = MemoryExportSink()
    fmt.export(view, sink)
    result = fmt.import_(
        MemoryImportSource(sink.files),
        ImportContext(
            known_labels={},
            frame_by_name={"img_000.jpg": 0, "img_001.jpg": 1},
            frame_count=2,
            frame_sizes={0: (WIDTH, HEIGHT), 1: (WIDTH, HEIGHT)},
        ),
    )

    received = [shape for frame, shape in result.shapes if frame == 0]
    assert all(frame in (0, 1) for frame, _ in result.shapes)
    assert len(received) == len(sent), [s.shape_type.value for s in received]
    tolerance = 1.0 if format_id in RASTER else 0.01
    unmatched = list(received)
    for shape in sent:
        expected = region(shape)
        match = next(
            (
                candidate
                for candidate in unmatched
                if candidate.label == shape.label
                and all(
                    abs(a - b) <= tolerance
                    for a, b in zip(region(candidate), expected, strict=True)
                )
            ),
            None,
        )
        assert match is not None, (
            f"{format_id}: {shape.shape_type.value} {shape.label!r} at {expected} did not "
            f"come back; got {[(s.shape_type.value, s.label, region(s)) for s in unmatched]}"
        )
        unmatched.remove(match)
