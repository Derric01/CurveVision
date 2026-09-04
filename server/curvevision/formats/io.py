"""Concrete sinks and sources: zip archives and in-memory maps."""

from __future__ import annotations

import io
import zipfile
from pathlib import Path


class ZipExportSink:
    """Streams format output into a zip archive."""

    def __init__(self) -> None:
        self._buffer = io.BytesIO()
        self._zip = zipfile.ZipFile(
            self._buffer, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=6
        )
        self._closed = False

    def write(self, path: str, data: bytes | str) -> None:
        payload = data.encode("utf-8") if isinstance(data, str) else data
        self._zip.writestr(path, payload)

    def close(self) -> bytes:
        if not self._closed:
            self._zip.close()
            self._closed = True
        return self._buffer.getvalue()

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
