"""Filesystem-backed storage.

The default for single-node self-hosting and for the entire test suite. No dependency at
all, which is what lets a contributor run everything with nothing installed but Python.
"""

from __future__ import annotations

import asyncio
import shutil
from collections.abc import AsyncIterator, Iterable
from pathlib import Path

from curvevision.storage.base import ObjectNotFoundError, StorageError, StoredObject

DEFAULT_CHUNK = 1024 * 1024


class LocalStorage:
    def __init__(self, root: str | Path) -> None:
        self.root = Path(root).resolve()
        self.root.mkdir(parents=True, exist_ok=True)

    def _path(self, key: str) -> Path:
        # Keys come from internal code, but a traversal bug here would be a filesystem
        # escape, so the containment check is unconditional.
        candidate = (self.root / key.lstrip("/")).resolve()
        if not candidate.is_relative_to(self.root):
            raise StorageError(f"Refusing to access key outside the storage root: {key!r}")
        return candidate

    async def put(
        self,
        key: str,
        data: bytes | Iterable[bytes],
        *,
        content_type: str = "application/octet-stream",
    ) -> StoredObject:
        path = self._path(key)

        def _write() -> int:
            path.parent.mkdir(parents=True, exist_ok=True)
            tmp = path.with_suffix(path.suffix + ".part")
            written = 0
            with tmp.open("wb") as fh:
                if isinstance(data, bytes):
                    fh.write(data)
                    written = len(data)
                else:
                    for chunk in data:
                        fh.write(chunk)
                        written += len(chunk)
            # Atomic replace so a reader never observes a partial object.
            tmp.replace(path)
            return written

        size = await asyncio.to_thread(_write)
        return StoredObject(key=key, size=size, content_type=content_type)

    async def append(self, key: str, data: bytes) -> int:
        path = self._path(key)

        def _append() -> int:
            path.parent.mkdir(parents=True, exist_ok=True)
            with path.open("ab") as fh:
                fh.write(data)
            return path.stat().st_size

        return await asyncio.to_thread(_append)

    async def get(self, key: str) -> bytes:
        path = self._path(key)

        def _read() -> bytes:
            try:
                return path.read_bytes()
            except FileNotFoundError as exc:
                raise ObjectNotFoundError(key) from exc

        return await asyncio.to_thread(_read)

    async def stream(self, key: str, chunk_size: int = DEFAULT_CHUNK) -> AsyncIterator[bytes]:
        path = self._path(key)
        if not path.exists():
            raise ObjectNotFoundError(key)
        handle = await asyncio.to_thread(path.open, "rb")
        try:
            while chunk := await asyncio.to_thread(handle.read, chunk_size):
                yield chunk
        finally:
            await asyncio.to_thread(handle.close)

    async def delete(self, key: str) -> None:
        path = self._path(key)

        def _delete() -> None:
            if path.is_dir():
                shutil.rmtree(path, ignore_errors=True)
            else:
                path.unlink(missing_ok=True)

        await asyncio.to_thread(_delete)

    async def exists(self, key: str) -> bool:
        return await asyncio.to_thread(self._path(key).exists)

    def public_url(self, key: str, *, expires_in: int = 3600) -> str | None:
        # Local files are never directly reachable by the browser; the caller proxies.
        return None
