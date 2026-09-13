"""Segmentation masks as indexed PNGs.

Independent implementation written from the Pascal VOC segmentation convention, which is
what "segmentation mask" means in practice across this ecosystem. No code from any other
project is used.

One PNG per frame, the same size as the image, where each pixel's value is the **class
index** of whatever covers it. Index 0 is background. A palette makes the file viewable —
otherwise every class is a barely-distinguishable shade of near-black and nobody can eyeball
whether an export is right.

This is the shape perception work actually consumes: a semantic-segmentation network trains
on exactly this array, and a robot's occupancy or traversability layer is the same idea with
different classes. Polygons are the natural source, so they are rasterised here rather than
being dropped the way the box formats drop them.

**Z-order decides who wins an overlap.** Two polygons covering the same pixel is normal —
a wheel on a car, a sign in front of a building — and a mask has room for exactly one answer
per pixel. Painting in ascending z-order means the shape an annotator put in front is the one
that survives, which is the same rule the editor draws by.
"""

from __future__ import annotations

from io import BytesIO

from curvevision.core.logging import get_logger
from curvevision.domain.enums import ShapeType
from curvevision.formats.base import (
    DatasetView,
    ExportSink,
    FormatCapabilities,
    ImportContext,
    ImportResult,
    ImportSource,
    ShapeRecord,
    normalise_rectangle,
)
from curvevision.formats.registry import register

logger = get_logger(__name__)

#: Shapes that enclose an area and can therefore be painted. A polyline and a point set
#: cover no pixels; rasterising them would invent a region the annotator did not draw.
FILLABLE = frozenset({ShapeType.RECTANGLE, ShapeType.POLYGON, ShapeType.ELLIPSE})


#: Pascal VOC's palette generator. Reproduced because the *look* of these files is a
#: convention people recognise, and because a random palette makes two exports of the same
#: dataset incomparable by eye.
def voc_palette(count: int = 256) -> list[int]:
    palette: list[int] = []
    for index in range(count):
        r = g = b = 0
        value = index
        for shift in range(8):
            r |= ((value >> 0) & 1) << (7 - shift)
            g |= ((value >> 1) & 1) << (7 - shift)
            b |= ((value >> 2) & 1) << (7 - shift)
            value >>= 3
        palette.extend([r, g, b])
    return palette


class SegmentationMaskFormat:
    id = "segmentation_mask"
    name = "Segmentation mask (indexed PNG)"
    version = "1.0"
    extension = "zip"
    capabilities = FormatCapabilities(
        shape_types=(ShapeType.RECTANGLE, ShapeType.POLYGON, ShapeType.ELLIPSE),
        supports_import=False,
        supports_export=True,
        supports_tracks=False,
        supports_tags=False,
        supports_attributes=False,
        notes=(
            "One indexed PNG per frame; pixel values are class indices, 0 is background. "
            "Polygons, boxes and ellipses are rasterised; polylines and point sets cover no "
            "area and are omitted. Overlaps are resolved by z-order, so the shape in front "
            "wins. Export only: a mask cannot be turned back into the polygons it came "
            "from, and inventing contours on import would produce shapes nobody drew."
        ),
    )

    # ----------------------------------------------------------------------- export

    def export(self, dataset: DatasetView, sink: ExportSink) -> None:
        try:
            from PIL import Image, ImageDraw
        except ImportError:  # pragma: no cover - exercised by the capability note
            raise RuntimeError(
                "Segmentation-mask export needs Pillow. Install the media extra: "
                "pip install 'curvevision[media]'"
            ) from None

        # Class indices come from the project's label order, not from what happens to
        # appear: a frame with no cars must still agree with one that has them about which
        # index "car" is, or the masks cannot be trained on together.
        class_index = {label.name: index for index, label in enumerate(dataset.labels, start=1)}
        palette = voc_palette()
        skipped: set[str] = set()

        for frame in dataset:
            width = frame.width or 0
            height = frame.height or 0
            if width <= 0 or height <= 0:
                skipped.add(frame.name)
                continue

            mask = Image.new("P", (width, height), 0)
            mask.putpalette(palette)
            draw = ImageDraw.Draw(mask)

            # Ascending z-order, so the shape in front is painted last and wins the pixel.
            for shape in sorted(frame.shapes, key=lambda item: item.z_order):
                if shape.shape_type not in FILLABLE:
                    continue
                index = class_index.get(shape.label)
                if index is None or index > 255:
                    continue
                _fill(draw, shape, index)

            buffer = BytesIO()
            mask.save(buffer, format="PNG")
            stem = frame.name.rsplit(".", 1)[0] if "." in frame.name else frame.name
            sink.write(f"SegmentationClass/{stem}.png", buffer.getvalue())
            if frame.media is not None:
                sink.write(f"JPEGImages/{frame.name}", frame.media)

        sink.write(
            "labelmap.txt",
            "# label:color_rgb:parts:actions\n"
            "background:0,0,0::\n"
            + "".join(
                f"{label.name}:"
                f"{palette[index * 3]},{palette[index * 3 + 1]},{palette[index * 3 + 2]}::\n"
                for index, label in enumerate(dataset.labels, start=1)
                if index <= 255
            ),
        )
        if skipped:
            logger.info(
                "segmentation: skipped frames with no known size", extra={"frames": sorted(skipped)}
            )

    # ----------------------------------------------------------------------- import

    def import_(self, source: ImportSource, context: ImportContext) -> ImportResult:
        """Refused, deliberately.

        A mask is a raster; the polygons that produced it are gone. Tracing contours back
        out would produce shapes with hundreds of vertices that no annotator drew and that
        nobody can edit — a plausible-looking import that quietly replaces someone's work
        with a machine's approximation of it. Better to say no.
        """
        return ImportResult(
            warnings=[
                "Segmentation masks cannot be imported: a mask does not record the polygons "
                "it was painted from, and reconstructing them would invent geometry. Import "
                "COCO or CVAT XML instead, both of which carry real polygons."
            ]
        )


def _fill(draw: object, shape: ShapeRecord, index: int) -> None:
    """Paint one shape into the mask at `index`."""
    points = list(shape.points)
    if shape.shape_type is ShapeType.RECTANGLE:
        x1, y1, x2, y2 = normalise_rectangle(points)
        draw.rectangle([x1, y1, x2, y2], fill=index)  # type: ignore[attr-defined]
    elif shape.shape_type is ShapeType.ELLIPSE:
        cx, cy, rx, ry = [*points, 0, 0, 0, 0][:4]
        draw.ellipse([cx - rx, cy - ry, cx + rx, cy + ry], fill=index)  # type: ignore[attr-defined]
    else:
        pairs = list(zip(points[0::2], points[1::2], strict=False))
        if len(pairs) >= 3:
            draw.polygon(pairs, fill=index)  # type: ignore[attr-defined]


register(SegmentationMaskFormat())


__all__ = ["SegmentationMaskFormat", "voc_palette"]
