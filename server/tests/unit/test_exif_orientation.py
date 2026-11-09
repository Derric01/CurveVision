"""A phone photo's EXIF rotation, applied everywhere the annotator's coordinates are used.

A portrait photo from a phone is usually stored as landscape pixels plus an EXIF tag saying
"turn me a quarter". The browser obeys the tag, so the annotator draws on the upright
picture. The server used to read the size from the raw pixels -- width and height the wrong
way round -- so YOLO's normalised coordinates were divided by the wrong numbers, and a model
asked to pre-label the photo was shown it sideways and answered in a rotated space. The
tests marked *was wrong* failed against the old code.
"""

from __future__ import annotations

import io

import pytest

from curvevision.domain.enums import MediaKind
from curvevision.media.probe import make_thumbnail, probe, upright

Image = pytest.importorskip("PIL.Image")

ORIENTATION = 0x0112


def photo(width: int, height: int, orientation: int | None, fmt: str = "JPEG") -> bytes:
    """`width` x `height` stored pixels, red in the top-left quarter, tagged `orientation`."""
    image = Image.new("RGB", (width, height), (0, 0, 255))
    image.paste((255, 0, 0), (0, 0, width // 2, height // 2))
    exif = Image.Exif()
    if orientation is not None:
        exif[ORIENTATION] = orientation
    buffer = io.BytesIO()
    image.save(
        buffer, format=fmt, exif=exif.tobytes(), **({"quality": 95} if fmt == "JPEG" else {})
    )
    return buffer.getvalue()


@pytest.mark.parametrize(
    ("orientation", "size"),
    [
        (None, (640, 480)),
        (1, (640, 480)),
        (2, (640, 480)),
        (3, (640, 480)),
        (4, (640, 480)),
        (5, (480, 640)),
        (6, (480, 640)),  # the common one: a phone held upright
        (7, (480, 640)),
        (8, (480, 640)),
    ],
)
def test_the_recorded_size_is_the_size_the_annotator_sees(
    orientation: int | None, size: tuple[int, int]
) -> None:  # was wrong for 5-8
    info = probe(photo(640, 480, orientation), "p.jpg", "image/jpeg", MediaKind.IMAGE)

    assert (info.width, info.height) == size


def test_an_image_without_a_rotation_is_passed_through_untouched() -> None:
    data = photo(64, 48, None)

    assert upright(data, "image/jpeg") == (data, "image/jpeg")
    tagged_upright = photo(64, 48, 1)
    assert upright(tagged_upright, "image/jpeg") == (tagged_upright, "image/jpeg")


@pytest.mark.parametrize("fmt", ["JPEG", "PNG"])
def test_a_model_is_shown_the_upright_picture(fmt: str) -> None:  # was wrong
    content_type = f"image/{fmt.lower()}"
    # Orientation 6: the stored top-left corner is shown at the top right.
    data, sent_type = upright(photo(64, 48, 6, fmt), content_type)

    with Image.open(io.BytesIO(data)) as shown:
        assert shown.size == (48, 64)
        assert shown.getexif().get(ORIENTATION, 1) == 1  # nothing left to rotate twice
        rgb = shown.convert("RGB")
        top_right, top_left = rgb.getpixel((40, 8)), rgb.getpixel((8, 8))
    assert sent_type == content_type
    assert top_right[0] > 200 and top_right[2] < 60  # red
    assert top_left[2] > 200 and top_left[0] < 60  # blue


def test_bytes_that_are_not_an_image_are_left_alone() -> None:
    assert upright(b"not an image", "image/jpeg") == (b"not an image", "image/jpeg")
    assert upright(b"\x00\x00", "video/mp4") == (b"\x00\x00", "video/mp4")


def test_the_thumbnail_is_upright() -> None:  # was sideways
    thumbnail = make_thumbnail(photo(640, 480, 6), max_edge=128)

    assert thumbnail is not None
    with Image.open(io.BytesIO(thumbnail)) as small:
        assert small.size == (96, 128)
