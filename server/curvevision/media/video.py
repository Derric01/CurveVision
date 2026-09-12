# Portions of this file are adapted from CVAT's `cvat/apps/engine/media_extractors.py`,
# at commit 1d0c39576c3239dcaf8ba7baee71a1b8de496c0e.
#
#   Copyright (C) 2019-2022 Intel Corporation
#   Copyright (C) CVAT.ai Corporation
#   SPDX-License-Identifier: MIT
#
# Copyright (C) CurveVision contributors, for the modifications.
# SPDX-License-Identifier: MIT
#
# What is adapted: the decoding strategy and the three pieces of hard-won knowledge that
# make it correct — counting frames by decoding rather than trusting container metadata,
# the `DURATION` metadata fallback for containers that omit a stream duration, and
# honouring rotation metadata while preserving presentation timestamps.
#
# What is different: CurveVision decodes from bytes or a filesystem path rather than
# CVAT's `Openable` abstraction, has no 3D/point-cloud dimension, no manifest, and no
# Django or DRF coupling. See docs/adr/0007-cvat-reuse-policy.md for why this file is
# adapted rather than re-derived.
"""Reading frames out of a video.

A video is one file that contains thousands of annotatable frames, so every operation here
answers one of two questions: *how many frames are there*, and *give me frame N*.

Both are harder than they look, which is exactly why this is adapted from a project that
has met the edge cases in production rather than written fresh.
"""

from __future__ import annotations

import io
from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from curvevision.core.errors import ValidationError
from curvevision.core.logging import get_logger

logger = get_logger(__name__)

#: Quality for frames handed to the editor. High enough that annotation is not guesswork,
#: low enough that a frame is a fast fetch.
FRAME_JPEG_QUALITY = 85


class VideoUnavailableError(RuntimeError):
    """PyAV is not installed, so nothing here can work.

    Video support is an optional extra: the server, the whole test suite and an
    image-only installation all work without it. This is raised rather than returning a
    sentinel so the caller cannot mistake "no video support" for "a video with no frames".
    """


def _require_av() -> Any:
    try:
        import av
    except ImportError as exc:  # pragma: no cover - depends on the install
        raise VideoUnavailableError(
            "Video support needs PyAV. Install the 'media' extra: "
            "pip install 'curvevision-server[media]'"
        ) from exc
    return av


@dataclass(frozen=True, slots=True)
class VideoMetadata:
    width: int | None
    height: int | None
    frame_rate: float | None
    duration_seconds: float | None
    #: Counted by decoding, never taken from the container. See `VideoReader.frame_count`.
    frame_count: int


class VideoReader:
    """Sequential access to a video's frames.

    Construct from bytes or from a path. A path is preferred where one exists: it lets the
    decoder read lazily instead of holding the whole file in memory, which matters when a
    desktop user annotates a 40 GB video in place.
    """

    def __init__(self, source: bytes | Path) -> None:
        self._source = source
        self._frame_count: int | None = None
        self._size: tuple[int, int] | None = None

    # ----------------------------------------------------------------- opening

    def _open(self) -> Any:
        """A decoding container. Callers must close it."""
        av = _require_av()
        try:
            if isinstance(self._source, Path):
                return av.open(str(self._source), "r")
            return av.open(io.BytesIO(self._source), "r")
        except VideoUnavailableError:
            raise
        except Exception as exc:
            raise ValidationError(f"Could not open this video: {exc}") from exc

    @staticmethod
    def _video_stream(container: Any) -> Any:
        stream = next((s for s in container.streams if s.type == "video"), None)
        if stream is None:
            raise ValidationError("This file contains no video stream")
        # Let the decoder use every core it can. Frame extraction is the slowest thing the
        # server does, and it is entirely CPU-bound.
        stream.thread_type = "AUTO"
        return stream

    # ----------------------------------------------------------------- reading

    def iterate_frames(self, wanted: Iterator[int] | None = None) -> Iterator[tuple[int, Any]]:
        """Yield `(index, frame)` for the requested frame numbers, in ascending order.

        Frames are counted in **decode order from the start of the file**, not sought to.
        Seeking lands on the nearest keyframe and container timestamps are frequently
        approximate, so a frame index obtained by seeking does not reliably identify the
        same picture twice. An annotation is anchored to a frame number, so "frame 412"
        must mean the same image every time it is asked for; sequential decoding is what
        guarantees that.

        `wanted` must be ascending. `None` yields every frame.
        """
        _require_av()  # fail with a useful message before opening anything
        upcoming = iter(wanted) if wanted is not None else None
        target = next(upcoming, None) if upcoming is not None else None
        if upcoming is not None and target is None:
            return

        container = self._open()
        try:
            stream = self._video_stream(container)
            for index, frame in enumerate(container.decode(stream)):
                if target is not None and index < target:
                    continue

                if self._size is None:
                    self._size = (frame.width, frame.height)
                yield index, frame

                if upcoming is not None:
                    target = next(upcoming, None)
                    if target is None:
                        return
        finally:
            container.close()

    @staticmethod
    def _rotation(frame: Any) -> int:
        """Degrees the picture must be turned to be the right way up, or 0.

        A phone video is commonly stored unrotated with a rotation flag beside it. Ignoring
        the flag means annotating a sideways image and exporting coordinates that match
        nothing anyone saw.

        Rotation is applied when a frame becomes an image rather than to the decoded frame
        itself. Turning a `VideoFrame` means a round trip through a pixel array, which
        would pull in NumPy for something Pillow already does on the very next line.
        """
        try:
            return int(getattr(frame, "rotation", 0) or 0) % 360
        except (TypeError, ValueError):  # pragma: no cover - defensive
            return 0

    def frame_count(self) -> int:
        """How many frames this video actually has.

        Counted by decoding every frame, and cached. Container metadata is not trusted:
        `stream.frames` is zero for many containers and wrong for others, and
        duration x frame-rate is an estimate that drifts on variable-frame-rate video. A
        task whose frame count is wrong sends annotators to frames that do not exist, so
        the expensive answer is the only correct one.
        """
        if self._frame_count is None:
            count = 0
            for _ in self.iterate_frames():
                count += 1
            self._frame_count = count
        return self._frame_count

    def frame(self, index: int) -> Any:
        """One decoded frame, by index."""
        if index < 0:
            raise ValidationError("Frame numbers start at 0")
        for found, frame in self.iterate_frames(iter([index])):
            if found == index:
                return frame
        raise ValidationError(f"This video has no frame {index}")

    def frame_image(self, index: int) -> Any:
        """One frame as a Pillow image, the right way up."""
        frame = self.frame(index)
        image = frame.to_image()
        rotation = self._rotation(frame)
        if rotation:
            # Pillow turns counter-clockwise; container rotation is clockwise.
            image = image.rotate(-rotation, expand=True)
        return image

    def frame_jpeg(self, index: int, quality: int = FRAME_JPEG_QUALITY) -> bytes:
        """One frame, encoded as a JPEG the editor can display."""
        buffer = io.BytesIO()
        self.frame_image(index).convert("RGB").save(
            buffer, format="JPEG", quality=quality, optimize=True
        )
        return buffer.getvalue()

    # ---------------------------------------------------------------- metadata

    def metadata(self, *, count_frames: bool = True) -> VideoMetadata:
        """Everything the task model needs to know about this video.

        `count_frames=False` skips the full decode, for callers that only want dimensions
        and are willing to record a frame count of zero until something authoritative
        fills it in.
        """
        container = self._open()
        try:
            stream = self._video_stream(container)
            width = stream.codec_context.width or None
            height = stream.codec_context.height or None
            rate = float(stream.average_rate) if stream.average_rate else None
            duration = self._duration_seconds(stream)
        finally:
            container.close()

        return VideoMetadata(
            width=width,
            height=height,
            frame_rate=rate,
            duration_seconds=duration,
            frame_count=self.frame_count() if count_frames else 0,
        )

    @staticmethod
    def _duration_seconds(stream: Any) -> float | None:
        """Duration in seconds, with the fallback real files need.

        Matroska in particular routinely omits the stream duration and records it as a
        `DURATION` tag shaped like `01:16:45.935000000`. Without this fallback such a video
        reports no duration at all.
        """
        if stream.duration and stream.time_base:
            return float(stream.duration * stream.time_base)

        raw = (stream.metadata or {}).get("DURATION")
        if not raw:
            return None
        try:
            hours, minutes, seconds = raw.split(":")
            return 3600 * float(hours) + 60 * float(minutes) + float(seconds)
        except (ValueError, AttributeError):
            logger.debug("unparseable DURATION metadata", extra={"duration": raw})
            return None
