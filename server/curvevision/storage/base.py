"""Object storage abstraction.

Seven methods. Deliberately small: the platform stores and retrieves opaque bytes at
opaque keys, and everything else (lifecycle, replication, encryption) belongs to whatever
implements this, not to CurveVision.
"""

from __future__ import annotations

from collections.abc import AsyncIterator, Iterable
from dataclasses import dataclass
from typing import Protocol, runtime_checkable


@dataclass(frozen=True, slots=True)
class StoredObject:
    key: str
    size: int
    content_type: str


class StorageError(RuntimeError):
    pass


class ObjectNotFoundError(StorageError):
    pass


@runtime_checkable
class Storage(Protocol):
    """Byte store addressed by string keys."""

    async def put(
        self, key: str, data: bytes | Iterable[bytes], *, content_type: str = ...
    ) -> StoredObject:
        """Write ``data`` at ``key``, replacing anything already there."""

    async def append(self, key: str, data: bytes) -> int:
        """Append to ``key``, creating it if absent. Returns the new total size.

        Used by the resumable-upload protocol.
        """

    async def get(self, key: str) -> bytes:
        """Read the whole object. Raises ``ObjectNotFoundError`` if absent."""

    # Declared with `def`, not `async def`: implementations are async *generators*, so
    # calling this returns the iterator directly rather than a coroutine yielding one.
    def stream(self, key: str, chunk_size: int = ...) -> AsyncIterator[bytes]:
        """Yield the object in chunks, for responses that must not be buffered."""

    async def delete(self, key: str) -> None:
        """Remove ``key``. Succeeds if it does not exist."""

    async def exists(self, key: str) -> bool: ...

    def public_url(self, key: str, *, expires_in: int = ...) -> str | None:
        """A time-limited direct URL, or ``None`` when the backend cannot presign.

        Callers must fall back to proxying through the API when this returns ``None``.
        """
