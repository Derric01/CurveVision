"""Media inspection and thumbnailing.

Pillow and PyAV are **optional**: the server starts, and the whole test suite passes,
without them. Missing dimensions degrade to ``None`` rather than crashing, which keeps the
contributor onramp to "install Python, run pytest".
"""

from __future__ import annotations

import io
import mimetypes
from dataclasses import dataclass
from pathlib import Path

from curvevision.core.config import Settings
from curvevision.core.errors import ValidationError
from curvevision.core.logging import get_logger
from curvevision.domain.enums import MediaKind

logger = get_logger(__name__)


@dataclass(frozen=True, slots=True)
class MediaInfo:
    kind: MediaKind
    content_type: str
    width: int | None = None
    height: int | None = None
    frame_count: int = 1
    duration_seconds: float | None = None
    frame_rate: float | None = None


#: Leading bytes that identify a format. Extension checks alone are trivially bypassed, so
#: uploads are validated by content as well as by name.
_MAGIC: tuple[tuple[bytes, str], ...] = (
    (b"\xff\xd8\xff", "image/jpeg"),
    (b"\x89PNG\r\n\x1a\n", "image/png"),
    (b"GIF87a", "image/gif"),
    (b"GIF89a", "image/gif"),
    (b"BM", "image/bmp"),
    (b"II*\x00", "image/tiff"),
    (b"MM\x00*", "image/tiff"),
    (b"\x1a\x45\xdf\xa3", "video/x-matroska"),
    (b"RIFF", "container/riff"),  # WebP or AVI; resolved below
)


def sniff_content_type(header: bytes, filename: str) -> str:
    for magic, content_type in _MAGIC:
        if header.startswith(magic):
            if content_type == "container/riff" and len(header) >= 12:
                fourcc = header[8:12]
                if fourcc == b"WEBP":
                    return "image/webp"
                if fourcc == b"AVI ":
                    return "video/x-msvideo"
                return "application/octet-stream"
            return content_type
    # MP4/MOV: 'ftyp' box at offset 4.
    if len(header) >= 12 and header[4:8] == b"ftyp":
        return "video/mp4"
    guessed, _ = mimetypes.guess_type(filename)
    return guessed or "application/octet-stream"


def classify(filename: str, content_type: str, settings: Settings) -> MediaKind:
    suffix = Path(filename).suffix.lower()
    if content_type.startswith("video/") or suffix in settings.allowed_video_extensions:
        return MediaKind.VIDEO
    if content_type.startswith("image/") or suffix in settings.allowed_image_extensions:
        return MediaKind.IMAGE
    raise ValidationError(
        f"Unsupported media type {content_type!r} for {filename!r}. "
        f"Images: {', '.join(settings.allowed_image_extensions)}. "
        f"Videos: {', '.join(settings.allowed_video_extensions)}."
    )


def validate_upload(filename: str, header: bytes, size: int, settings: Settings) -> str:
    """Validate an upload by name, magic bytes and size. Returns the content type."""
    if size > settings.max_upload_bytes:
        raise ValidationError(
            f"File is {size} bytes; the limit is {settings.max_upload_bytes} bytes"
        )
    suffix = Path(filename).suffix.lower()
    allowed = set(settings.allowed_image_extensions) | set(settings.allowed_video_extensions)
    if suffix not in allowed:
        raise ValidationError(f"File extension {suffix!r} is not accepted")

    content_type = sniff_content_type(header, filename)
    if content_type == "application/octet-stream":
        raise ValidationError(
            f"{filename!r} does not look like a supported image or video "
            "(its contents do not match any known format)"
        )
    # Cheap defence against an image extension wrapping a video payload and vice versa.
    declared = classify(filename, "", settings)
    detected = MediaKind.VIDEO if content_type.startswith("video/") else MediaKind.IMAGE
    if declared is not detected:
        raise ValidationError(
            f"{filename!r} has a {declared.value} extension but contains {detected.value} data"
        )
    return content_type


def probe(data: bytes, filename: str, content_type: str, kind: MediaKind) -> MediaInfo:
    """Read dimensions and, for video, frame count and rate."""
    if kind is MediaKind.IMAGE:
        return _probe_image(data, content_type)
    return _probe_video(data, filename, content_type)


def _probe_image(data: bytes, content_type: str) -> MediaInfo:
    try:
        from PIL import Image
    except ImportError:
        logger.debug("Pillow is not installed; image dimensions unavailable")
        return MediaInfo(kind=MediaKind.IMAGE, content_type=content_type)

    try:
        with Image.open(io.BytesIO(data)) as image:
            width, height = image.size
    except Exception as exc:
        raise ValidationError(f"Could not read this image: {exc}") from exc
    return MediaInfo(kind=MediaKind.IMAGE, content_type=content_type, width=width, height=height)


def _probe_video(data: bytes, filename: str, content_type: str) -> MediaInfo:
    try:
        import av
    except ImportError:
        logger.info(
            "PyAV is not installed; video metadata unavailable. "
            "Install the 'media' extra for video support.",
            extra={"filename": filename},
        )
        return MediaInfo(kind=MediaKind.VIDEO, content_type=content_type, frame_count=0)

    del av  # only imported to detect availability; the reader does the work

    from curvevision.media.video import VideoReader

    reader = VideoReader(data)
    try:
        # `count_frames=False` on purpose: an exact count means decoding every frame, and
        # this runs inside the upload request. A two-hour video would time out. The
        # estimate below is what the task is created with; `frame_count` on the reader is
        # the authoritative answer when something is willing to pay for it.
        meta = reader.metadata(count_frames=False)
    except ValidationError:
        raise
    except Exception as exc:
        raise ValidationError(f"Could not read this video: {exc}") from exc

    frames = _estimate_frame_count(data, meta.duration_seconds, meta.frame_rate)
    return MediaInfo(
        kind=MediaKind.VIDEO,
        content_type=content_type,
        width=meta.width,
        height=meta.height,
        frame_count=frames,
        duration_seconds=meta.duration_seconds,
        frame_rate=meta.frame_rate,
    )


def _estimate_frame_count(data: bytes, duration: float | None, rate: float | None) -> int:
    """A frame count good enough to create a task with, cheaply.

    Container metadata first, then duration x frame rate. Both are approximations --
    `stream.frames` is zero in many containers and wrong in others, and the product drifts
    on variable-frame-rate video -- which is why `VideoReader.frame_count()` exists and
    counts properly. Nothing here should be presented to a user as exact.
    """
    import av

    try:
        with av.open(io.BytesIO(data)) as container:
            stream = next((s for s in container.streams if s.type == "video"), None)
            declared = int(stream.frames) if stream is not None and stream.frames else 0
    except Exception:  # pragma: no cover - already opened successfully once above
        declared = 0

    if declared:
        return declared
    if duration and rate:
        return max(1, int(duration * rate))
    return 0


def make_thumbnail(data: bytes, max_edge: int) -> bytes | None:
    """Downscale an image to fit ``max_edge``. Returns ``None`` without Pillow."""
    try:
        from PIL import Image
    except ImportError:
        return None
    try:
        with Image.open(io.BytesIO(data)) as source:
            thumbnail = source.convert("RGB")
            thumbnail.thumbnail((max_edge, max_edge))
            buffer = io.BytesIO()
            thumbnail.save(buffer, format="JPEG", quality=82, optimize=True)
            return buffer.getvalue()
    except Exception:
        logger.warning("thumbnail generation failed")
        return None
