"""Reading frames out of a video.

Every test here encodes a real video with PyAV and decodes it back, because the whole
point of this module is the gap between what a container *claims* and what it *contains*.
A mocked decoder would test the mock.
"""

from __future__ import annotations

import io
from pathlib import Path

import pytest

from curvevision.core.errors import ValidationError
from curvevision.media.video import VideoReader

av = pytest.importorskip("av", reason="video support is an optional extra")


def make_video(
    frames: int = 12,
    width: int = 64,
    height: int = 48,
    rate: int = 6,
    container_format: str = "mp4",
) -> bytes:
    """A real encoded video whose frames are individually identifiable.

    Each frame is a flat colour derived from its index, so a decoded frame can be checked
    against the frame that was *meant* to be there rather than merely "some image".

    Built with Pillow rather than NumPy: this repository already depends on Pillow for
    images, and a test helper is a poor reason to add a second array library.
    """
    from PIL import Image

    buffer = io.BytesIO()
    with av.open(buffer, mode="w", format=container_format) as container:
        stream = container.add_stream("mpeg4", rate=rate)
        stream.width = width
        stream.height = height
        stream.pix_fmt = "yuv420p"

        for index in range(frames):
            shade = index * 20 % 256
            picture = Image.new("RGB", (width, height), (shade, 255 - shade, 128))
            frame = av.VideoFrame.from_image(picture)
            for packet in stream.encode(frame):
                container.mux(packet)
        for packet in stream.encode():
            container.mux(packet)

    return buffer.getvalue()


# --------------------------------------------------------------------------- counting


def test_frame_count_is_measured_by_decoding_not_believed_from_metadata() -> None:
    """The container's own frame count is unreliable; ours is not."""
    data = make_video(frames=12)
    assert VideoReader(data).frame_count() == 12


def test_frame_count_is_cached_so_a_second_ask_is_free() -> None:
    reader = VideoReader(make_video(frames=7))
    assert reader.frame_count() == 7
    # Nothing to assert about timing in a unit test; assert the cache is actually used.
    assert reader._frame_count == 7
    assert reader.frame_count() == 7


@pytest.mark.parametrize("frames", [1, 5, 30])
def test_frame_count_is_exact_across_lengths(frames: int) -> None:
    assert VideoReader(make_video(frames=frames)).frame_count() == frames


# ------------------------------------------------------------------------ addressing


def test_every_frame_is_reachable_by_its_index() -> None:
    data = make_video(frames=10)
    reader = VideoReader(data)
    for index in range(10):
        frame = reader.frame(index)
        assert (frame.width, frame.height) == (64, 48)


def test_a_frame_index_means_the_same_picture_every_time() -> None:
    """Annotations are anchored to frame numbers, so this is the load-bearing property."""
    reader = VideoReader(make_video(frames=10))
    first = reader.frame_jpeg(6)
    second = reader.frame_jpeg(6)
    assert first == second

    # And a different index is a different picture, so indexing is not off by one or stuck.
    assert reader.frame_jpeg(2) != first


def test_asking_past_the_end_is_a_validation_error_not_a_crash() -> None:
    reader = VideoReader(make_video(frames=4))
    with pytest.raises(ValidationError, match="no frame 99"):
        reader.frame(99)


def test_a_negative_frame_number_is_refused() -> None:
    with pytest.raises(ValidationError, match="start at 0"):
        VideoReader(make_video(frames=4)).frame(-1)


def test_iterate_frames_yields_only_what_was_asked_for_in_order() -> None:
    reader = VideoReader(make_video(frames=20))
    seen = [index for index, _ in reader.iterate_frames(iter([0, 5, 11, 19]))]
    assert seen == [0, 5, 11, 19]


def test_iterating_nothing_decodes_nothing() -> None:
    reader = VideoReader(make_video(frames=5))
    assert list(reader.iterate_frames(iter([]))) == []


def test_iterating_everything_yields_a_contiguous_range() -> None:
    reader = VideoReader(make_video(frames=8))
    assert [index for index, _ in reader.iterate_frames()] == list(range(8))


# --------------------------------------------------------------------------- encoding


def test_a_frame_encodes_to_a_jpeg_the_editor_can_display() -> None:
    jpeg = VideoReader(make_video(frames=3)).frame_jpeg(1)
    assert jpeg.startswith(b"\xff\xd8\xff")  # JPEG SOI

    from PIL import Image

    with Image.open(io.BytesIO(jpeg)) as image:
        assert image.size == (64, 48)


# --------------------------------------------------------------------------- metadata


def test_metadata_reports_what_the_task_model_needs() -> None:
    meta = VideoReader(make_video(frames=12, width=80, height=60, rate=6)).metadata()
    assert (meta.width, meta.height) == (80, 60)
    assert meta.frame_count == 12
    assert meta.frame_rate == pytest.approx(6, abs=0.5)


def test_metadata_can_skip_the_expensive_count() -> None:
    meta = VideoReader(make_video(frames=12)).metadata(count_frames=False)
    assert meta.frame_count == 0
    assert meta.width == 64


def test_a_matroska_duration_tag_is_parsed_when_the_stream_omits_one() -> None:
    """The fallback exists because Matroska routinely omits the stream duration."""

    class FakeStream:
        duration = None
        time_base = None
        metadata = {"DURATION": "01:16:45.935000000"}

    assert VideoReader._duration_seconds(FakeStream()) == pytest.approx(4605.935)


def test_an_unparseable_duration_tag_degrades_to_unknown_rather_than_raising() -> None:
    class FakeStream:
        duration = None
        time_base = None
        metadata = {"DURATION": "not a duration"}

    assert VideoReader._duration_seconds(FakeStream()) is None


def test_no_duration_anywhere_is_unknown() -> None:
    class FakeStream:
        duration = None
        time_base = None
        metadata: dict[str, str] = {}

    assert VideoReader._duration_seconds(FakeStream()) is None


# ----------------------------------------------------------------------------- errors


def test_something_that_is_not_a_video_is_a_validation_error() -> None:
    with pytest.raises(ValidationError, match="Could not open"):
        VideoReader(b"this is not a video").frame_count()


def test_a_file_with_no_video_stream_says_so() -> None:
    # A valid container carrying only audio.
    buffer = io.BytesIO()
    with av.open(buffer, mode="w", format="mp4") as container:
        stream = container.add_stream("aac", rate=44100)
        stream.layout = "mono"
    data = buffer.getvalue()

    reader = VideoReader(data)
    with pytest.raises(ValidationError):
        reader.frame_count()


# ------------------------------------------------------------------------ from a path


def test_reading_from_a_path_avoids_holding_the_file_in_memory(tmp_path: Path) -> None:
    """The desktop application annotates videos where they sit, which can be very large."""
    path = tmp_path / "clip.mp4"
    path.write_bytes(make_video(frames=6))

    reader = VideoReader(path)
    assert reader.frame_count() == 6
    assert reader.frame_jpeg(3).startswith(b"\xff\xd8\xff")
