"""Scanning uploaded files for malware before they are stored.

Off by default (`content_scanner = "none"`): an annotation tool's uploads are images and
video an organisation already holds, and requiring a virus scanner to run one would put
self-hosting out of reach. An operator who accepts files from people they do not trust
turns it on, and gets ClamAV through clamd's `INSTREAM` command — the protocol every clamd
speaks, over TCP or a Unix socket, with no client library:

    zINSTREAM\\0  <len:u32 big-endian><chunk> ...  <0:u32>   ->   "stream: OK\\0"
                                                            or   "stream: <name> FOUND\\0"
                                                            or   "<reason> ERROR\\0"

Until iteration 77 `SECURITY.md` and `ARCHITECTURE.md` described a `ContentScanner` hook
"for ClamAV or a vendor scanner"; no such hook existed. This is it, and its first scanner.

**It fails closed.** An upload that cannot be scanned — clamd down, timing out, answering
an error — is refused (`ScannerUnavailableError`, a 503), because a scanner an attacker can
switch off by overloading it is not a control. `content_scanner_fail_open` exists for an
operator who would rather keep accepting uploads through an outage, and says so in the log.
"""

from __future__ import annotations

import asyncio
import contextlib
import struct
from dataclasses import dataclass
from typing import Protocol

from curvevision.core.config import Settings
from curvevision.core.errors import CurveVisionError, ValidationError
from curvevision.core.logging import get_logger

logger = get_logger(__name__)

#: How much is sent per `INSTREAM` chunk. clamd's own limit is per stream
#: (`StreamMaxLength`, 25 MB by default), not per chunk.
CHUNK_BYTES = 64 * 1024


class ScannerUnavailableError(CurveVisionError):
    """The file could not be scanned, so it was not accepted."""

    status_code = 503
    error_type = "scanner_unavailable"
    title = "Content scanning is unavailable"


class InfectedUploadError(ValidationError):
    """The scanner found something in the file."""

    error_type = "infected_upload"
    title = "The upload was refused by the content scanner"


@dataclass(frozen=True, slots=True)
class ScanResult:
    clean: bool
    #: What the scanner found, as it names it; `None` when clean.
    signature: str | None = None


class ContentScanner(Protocol):
    async def scan(self, data: bytes, *, filename: str) -> ScanResult:
        """Scan one file's bytes. Raises `ScannerUnavailableError` when it cannot."""
        ...


class NoScanner:
    """Accepts everything: the default, and the desktop build's only scanner."""

    async def scan(self, data: bytes, *, filename: str) -> ScanResult:
        return ScanResult(clean=True)


class ClamdScanner:
    """ClamAV, through clamd's `INSTREAM` command."""

    def __init__(
        self,
        *,
        host: str = "127.0.0.1",
        port: int = 3310,
        socket_path: str | None = None,
        timeout_seconds: float = 30.0,
    ) -> None:
        self.host = host
        self.port = port
        self.socket_path = socket_path
        self.timeout_seconds = timeout_seconds

    async def scan(self, data: bytes, *, filename: str) -> ScanResult:
        try:
            reply = await asyncio.wait_for(self._instream(data), self.timeout_seconds)
        except (OSError, TimeoutError, asyncio.IncompleteReadError) as exc:
            raise ScannerUnavailableError(
                f"Could not scan {filename!r}: clamd is unreachable ({exc or type(exc).__name__})"
            ) from exc
        return parse_reply(reply, filename=filename)

    async def _instream(self, data: bytes) -> str:
        if self.socket_path:
            reader, writer = await asyncio.open_unix_connection(self.socket_path)
        else:
            reader, writer = await asyncio.open_connection(self.host, self.port)
        try:
            writer.write(b"zINSTREAM\0")
            for start in range(0, len(data), CHUNK_BYTES):
                chunk = data[start : start + CHUNK_BYTES]
                writer.write(struct.pack(">I", len(chunk)) + chunk)
                await writer.drain()
            writer.write(struct.pack(">I", 0))
            await writer.drain()
            reply = await reader.readuntil(b"\0")
            return reply.rstrip(b"\0").decode("utf-8", errors="replace")
        finally:
            writer.close()
            with contextlib.suppress(OSError):
                await writer.wait_closed()


def parse_reply(reply: str, *, filename: str) -> ScanResult:
    """clamd's answer to one stream: `stream: OK`, `stream: <name> FOUND`, or an error."""
    text = reply.strip()
    if text.endswith("ERROR"):
        # e.g. "INSTREAM size limit exceeded. ERROR": unscanned, so not accepted.
        raise ScannerUnavailableError(f"Could not scan {filename!r}: clamd answered {text!r}")
    body = text.split(":", 1)[1].strip() if ":" in text else text
    if body == "OK":
        return ScanResult(clean=True)
    if body.endswith("FOUND"):
        return ScanResult(clean=False, signature=body[: -len("FOUND")].strip() or "unknown")
    raise ScannerUnavailableError(f"Could not scan {filename!r}: unexpected reply {text!r}")


def make_scanner(settings: Settings) -> ContentScanner:
    if settings.content_scanner == "clamd":
        return ClamdScanner(
            host=settings.clamd_host,
            port=settings.clamd_port,
            socket_path=settings.clamd_socket,
            timeout_seconds=settings.clamd_timeout_seconds,
        )
    return NoScanner()


async def check_upload(
    scanner: ContentScanner, data: bytes, *, filename: str, fail_open: bool = False
) -> None:
    """Refuse `data` unless the scanner says it is clean (or, if allowed, cannot say)."""
    try:
        result = await scanner.scan(data, filename=filename)
    except ScannerUnavailableError as exc:
        if not fail_open:
            raise
        logger.warning("accepting %r unscanned (content_scanner_fail_open): %s", filename, exc)
        return
    if not result.clean:
        logger.warning("refused %r: the content scanner found %s", filename, result.signature)
        raise InfectedUploadError(
            f"{filename!r} was refused: the content scanner found {result.signature}"
        )


__all__ = [
    "ClamdScanner",
    "ContentScanner",
    "InfectedUploadError",
    "NoScanner",
    "ScanResult",
    "ScannerUnavailableError",
    "check_upload",
    "make_scanner",
    "parse_reply",
]
