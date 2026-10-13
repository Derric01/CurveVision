"""Shapes as the pixels they cover, and pixels back as a mask shape.

For formats that carry segmentation as one full-image binary PNG per object (Open Images,
the TensorFlow Object Detection API): a mask is written pixel for pixel, and a polygon or
ellipse as the mask it covers. Needs Pillow; callers report its absence in their own words.
"""

from __future__ import annotations

import io
import math
from typing import Any

from curvevision.core.errors import ValidationError
from curvevision.core.logging import get_logger
from curvevision.domain.enums import ShapeType
from curvevision.formats.base import ShapeRecord
from curvevision.formats.rle import encode as rle_encode
from curvevision.formats.rle import pixels as mask_pixels

logger = get_logger(__name__)

#: Vertices of the polygon that stands in for a rotated ellipse.
ELLIPSE_VERTICES = 36


def cover(shape: ShapeRecord, width: int, height: int) -> tuple[bytes, list[float]] | None:
    """A full-image binary PNG of the pixels ``shape`` covers, and the box around them.

    The box is ``[left, top, right, bottom]`` on pixel edges — ``right`` and ``bottom`` are
    one past the last pixel covered — so a box of a single pixel has an area of one.
    ``None`` when the shape covers nothing, cannot be read, or Pillow is not installed.
    """
    try:
        from PIL import Image, ImageDraw
    except ImportError:
        logger.warning("a mask needs Pillow (the media extra); a shape was left out")
        return None
    image = Image.new("L", (width, height), 0)
    draw = ImageDraw.Draw(image)
    points = list(shape.points)
    if shape.shape_type is ShapeType.MASK:
        try:
            covered = list(mask_pixels(shape.mask or {}))
        except ValidationError:
            logger.warning("an unreadable mask was left out", extra={"label": shape.label})
            return None
        covered = [(x, y) for x, y in covered if 0 <= x < width and 0 <= y < height]
        if covered:
            draw.point(covered, fill=255)
    elif shape.shape_type is ShapeType.ELLIPSE:
        cx, cy, rx, ry = [*points, 0, 0, 0, 0][:4]
        if shape.rotation:
            turn = math.radians(shape.rotation)
            cos, sin = math.cos(turn), math.sin(turn)
            traced = []
            for step in range(ELLIPSE_VERTICES):
                angle = 2 * math.pi * step / ELLIPSE_VERTICES
                dx, dy = rx * math.cos(angle), ry * math.sin(angle)
                traced.append((cx + dx * cos - dy * sin, cy + dx * sin + dy * cos))
            draw.polygon(traced, fill=255)
        else:
            draw.ellipse([cx - rx, cy - ry, cx + rx, cy + ry], fill=255)
    else:
        pairs = list(zip(points[0::2], points[1::2], strict=False))
        if len(pairs) < 3:
            return None
        draw.polygon(pairs, fill=255)
    bounds = image.getbbox()
    if bounds is None:
        return None
    left, top, right, bottom = bounds
    buffer = io.BytesIO()
    image.convert("1").save(buffer, format="PNG")
    return buffer.getvalue(), [float(left), float(top), float(right), float(bottom)]


def mask_shape_from_image(image: Any, label: str) -> ShapeRecord | None:
    """A mask shape of the non-zero pixels of a mode ``L`` image, cropped to them."""
    bounds = image.getbbox()
    if bounds is None:
        return None
    left, top, right, bottom = bounds
    width, height = right - left, bottom - top
    flags = [value > 0 for value in image.crop(bounds).tobytes()]  # mode "L": a byte a pixel
    return ShapeRecord(
        label=label,
        shape_type=ShapeType.MASK,
        points=[left, top, right - 1, bottom - 1],
        mask={
            "rle": rle_encode(flags, width, height),
            "left": left,
            "top": top,
            "width": width,
            "height": height,
        },
    )


__all__ = ["cover", "mask_shape_from_image"]
