"""Uploads scanned for malware before they are stored.

Three layers, each tested where it can be:

* **clamd itself** — `ClamdScanner` against a real clamd when `CURVEVISION_TEST_CLAMD`
  names one (`host:port`). A clean file passes, the EICAR test file is reported with the
  signature clamd names, and a clamd that is not there is an error, never a pass.
* **The protocol** — against a stand-in that speaks clamd's `INSTREAM` framing and records
  what it was sent, so the framing is checked on every run, with no service.
* **The upload** — through the API, with the scanner replaced: an infected file is refused
  and nothing is stored; one that cannot be scanned is refused too (fail closed).

Until iteration 77 the docs described a `ContentScanner` hook for ClamAV that did not exist.
"""

from __future__ import annotations

import asyncio
import io
import os
import struct
from typing import Any

import pytest

from curvevision.media import scanning
from curvevision.media.scanning import (
    ClamdScanner,
    ScannerUnavailableError,
    ScanResult,
    check_upload,
    parse_reply,
)
from tests.conftest import ApiActor

#: The standard antivirus test file: harmless, and detected by every scanner on purpose.
EICAR = rb"X5O!P%@AP[4\PZX54(P^)7CC)7}$EICAR-STANDARD-ANTIVIRUS-TEST-FILE!$H+H*"
CLAMD = os.environ.get("CURVEVISION_TEST_CLAMD")


def png() -> bytes:
    from PIL import Image

    buffer = io.BytesIO()
    Image.new("RGB", (32, 24), "#334155").save(buffer, format="PNG")
    return buffer.getvalue()


# ------------------------------------------------------------------------- real clamd


@pytest.mark.skipif(not CLAMD, reason="set CURVEVISION_TEST_CLAMD=host:port to scan with clamd")
class TestAgainstClamd:
    def scanner(self) -> ClamdScanner:
        host, port = str(CLAMD).rsplit(":", 1)
        return ClamdScanner(host=host, port=int(port), timeout_seconds=30)

    async def test_a_clean_file_is_clean(self) -> None:
        assert await self.scanner().scan(png(), filename="clean.png") == ScanResult(clean=True)

    async def test_the_eicar_test_file_is_found_and_named(self) -> None:
        result = await self.scanner().scan(EICAR, filename="eicar.com")
        assert not result.clean
        assert result.signature and "eicar" in result.signature.lower()

    async def test_a_file_of_several_chunks_is_accepted_and_scanned(self) -> None:
        large = png() + b"\0" * (3 * scanning.CHUNK_BYTES)
        assert await self.scanner().scan(large, filename="large.png") == ScanResult(clean=True)


async def test_a_clamd_that_is_not_there_is_an_error_not_a_pass() -> None:
    """Nothing listens on port 1."""
    with pytest.raises(ScannerUnavailableError):
        await ClamdScanner(host="127.0.0.1", port=1, timeout_seconds=5).scan(b"x", filename="f")


# ----------------------------------------------------------------- the INSTREAM framing


class StandIn:
    """Speaks clamd's `INSTREAM` framing, records the stream, and answers `reply`."""

    def __init__(self, reply: bytes) -> None:
        self.reply = reply
        self.command = b""
        self.received = b""
        self.chunks = 0

    async def handle(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        self.command = await reader.readuntil(b"\0")
        while True:
            (length,) = struct.unpack(">I", await reader.readexactly(4))
            if length == 0:
                break
            self.received += await reader.readexactly(length)
            self.chunks += 1
        writer.write(self.reply)
        await writer.drain()
        writer.close()


async def scan_with(stand_in: StandIn, data: bytes) -> ScanResult:
    server = await asyncio.start_server(stand_in.handle, "127.0.0.1", 0)
    port = server.sockets[0].getsockname()[1]
    try:
        return await ClamdScanner(host="127.0.0.1", port=port).scan(data, filename="f.png")
    finally:
        server.close()
        await server.wait_closed()


async def test_the_whole_file_is_sent_in_length_prefixed_chunks() -> None:
    stand_in = StandIn(b"stream: OK\0")
    data = bytes(range(256)) * 700  # 179,200 bytes: three chunks

    assert await scan_with(stand_in, data) == ScanResult(clean=True)
    assert stand_in.command == b"zINSTREAM\0"
    assert stand_in.received == data
    assert stand_in.chunks == 3


async def test_a_signature_is_reported_as_clamd_names_it() -> None:
    result = await scan_with(StandIn(b"stream: Win.Test.EICAR_HDB-1 FOUND\0"), b"x")
    assert result == ScanResult(clean=False, signature="Win.Test.EICAR_HDB-1")


async def test_a_file_clamd_could_not_scan_is_not_clean() -> None:
    with pytest.raises(ScannerUnavailableError, match="size limit"):
        await scan_with(StandIn(b"INSTREAM size limit exceeded. ERROR\0"), b"x")


@pytest.mark.parametrize(
    ("reply", "expected"),
    [
        ("stream: OK", ScanResult(clean=True)),
        ("stream: Eicar-Signature FOUND", ScanResult(clean=False, signature="Eicar-Signature")),
    ],
)
def test_replies_are_read_as_clamd_writes_them(reply: str, expected: ScanResult) -> None:
    assert parse_reply(reply, filename="f") == expected


# ----------------------------------------------------------------------- the upload


class Stub:
    def __init__(self, result: ScanResult | None) -> None:
        self.result = result
        self.scanned: list[str] = []

    async def scan(self, data: bytes, *, filename: str) -> ScanResult:
        self.scanned.append(filename)
        if self.result is None:
            raise ScannerUnavailableError("clamd is unreachable")
        return self.result


async def upload(actor: ApiActor, project: dict[str, Any]) -> tuple[Any, list[Any]]:
    task = (
        await actor.post("/api/v1/tasks", json={"project_id": project["id"], "name": "Scan"})
    ).json()
    response = await actor.post(
        f"/api/v1/tasks/{task['id']}/assets",
        files=[("files", ("street.png", png(), "image/png"))],
    )
    assets = (await actor.get(f"/api/v1/tasks/{task['id']}/assets")).json()
    return response, assets


class TestAnUpload:
    async def test_found_infected_is_refused_and_not_stored(
        self, owner: ApiActor, project: dict[str, Any], monkeypatch: pytest.MonkeyPatch
    ) -> None:
        stub = Stub(ScanResult(clean=False, signature="Win.Test.EICAR_HDB-1"))
        monkeypatch.setattr(scanning, "make_scanner", lambda settings: stub)

        response, assets = await upload(owner, project)

        assert response.status_code == 422, response.text
        assert "Win.Test.EICAR_HDB-1" in response.json()["detail"]
        assert stub.scanned == ["street.png"]
        assert assets == []

    async def test_that_cannot_be_scanned_is_refused(
        self, owner: ApiActor, project: dict[str, Any], monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(scanning, "make_scanner", lambda settings: Stub(None))

        response, assets = await upload(owner, project)

        assert response.status_code == 503, response.text
        assert response.json()["title"] == "Content scanning is unavailable"
        assert assets == []

    async def test_found_clean_is_stored(
        self, owner: ApiActor, project: dict[str, Any], monkeypatch: pytest.MonkeyPatch
    ) -> None:
        stub = Stub(ScanResult(clean=True))
        monkeypatch.setattr(scanning, "make_scanner", lambda settings: stub)

        response, assets = await upload(owner, project)

        assert response.status_code in (200, 201), response.text
        assert len(assets) == 1


async def test_fail_open_accepts_what_cannot_be_scanned() -> None:
    await check_upload(Stub(None), b"x", filename="f.png", fail_open=True)
    with pytest.raises(ScannerUnavailableError):
        await check_upload(Stub(None), b"x", filename="f.png", fail_open=False)
