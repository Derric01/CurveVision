"""Concrete sinks and sources: zip archives and in-memory maps."""

from __future__ import annotations

import io
import zipfile
from pathlib import Path

from curvevision.formats.base import FrameRecord

#: Already-compressed image formats: deflating them again costs CPU and saves nothing.
_STORED_SUFFIXES = (".jpg", ".jpeg", ".png", ".webp", ".gif", ".avif", ".heic", ".mp4", ".webm")


class ZipExportSink:
    """Streams format output into a zip archive.

    In memory by default. Given ``path``, the archive is written to that file instead, and
    images given as files (``FrameRecord.media_path``) are streamed into it from disk, so
    an export's memory stays flat however many gigabytes of images it carries. ``close``
    then returns ``b""``; the archive is the file.
    """

    def __init__(self, path: Path | None = None) -> None:
        self._path = path
        self._buffer: io.BytesIO | None = None if path is not None else io.BytesIO()
        target: Path | io.BytesIO = path if path is not None else self._buffer  # type: ignore[assignment]
        self._zip = zipfile.ZipFile(
            target, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=6, allowZip64=True
        )
        self._closed = False

    def write(self, path: str, data: bytes | str) -> None:
        payload = data.encode("utf-8") if isinstance(data, str) else data
        self._zip.writestr(path, payload, compress_type=_compression(path))

    def write_media(self, path: str, frame: FrameRecord) -> None:
        if frame.media_path is not None:
            self._zip.write(frame.media_path, arcname=path, compress_type=_compression(path))
        elif frame.media is not None:
            self.write(path, frame.media)

    def close(self) -> bytes:
        if not self._closed:
            self._zip.close()
            self._closed = True
        return self._buffer.getvalue() if self._buffer is not None else b""

    def __enter__(self) -> ZipExportSink:
        return self

    def __exit__(self, *_exc: object) -> None:
        if not self._closed:
            self._zip.close()
            self._closed = True


class MemoryExportSink:
    """Collects output into a dict. Used by tests and by format round-trip checks."""

    def __init__(self) -> None:
        self.files: dict[str, bytes] = {}

    def write(self, path: str, data: bytes | str) -> None:
        self.files[path] = data.encode("utf-8") if isinstance(data, str) else data

    def write_media(self, path: str, frame: FrameRecord) -> None:
        media = frame.read_media()
        if media is not None:
            self.files[path] = media


def _compression(path: str) -> int:
    return zipfile.ZIP_STORED if path.lower().endswith(_STORED_SUFFIXES) else zipfile.ZIP_DEFLATED


class ZipImportSource:
    """Reads a format's input from a zip archive.

    Archive members are validated on open: a path escaping the archive root (``../``, an
    absolute path, a drive letter) is rejected rather than trusted, because importers pass
    these names to path-joining code.
    """

    def __init__(self, data: bytes) -> None:
        self._zip = zipfile.ZipFile(io.BytesIO(data))
        self._names = [name for name in self._zip.namelist() if not name.endswith("/")]
        for name in self._names:
            if name.startswith("/") or ".." in Path(name).parts or Path(name).is_absolute():
                raise ValueError(f"Refusing to read unsafe archive path: {name!r}")

    def names(self) -> list[str]:
        return list(self._names)

    def read(self, path: str) -> bytes:
        return self._zip.read(path)

    def exists(self, path: str) -> bool:
        return path in self._names


class MemoryImportSource:
    """A dict-backed source, for tests and for single-file imports."""

    def __init__(self, files: dict[str, bytes]) -> None:
        self.files = files

    def names(self) -> list[str]:
        return sorted(self.files)

    def read(self, path: str) -> bytes:
        return self.files[path]

    def exists(self, path: str) -> bool:
        return path in self.files
