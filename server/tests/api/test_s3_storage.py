"""The S3 storage backend, against a real S3-compatible server.

Every server deployment keeps its media in S3, and until iteration 84 nothing tested that
code against an S3 server at all: the API suite runs on local storage. These tests run
when `CURVEVISION_TEST_S3_URL` names one — CI starts Versity Gateway for them — and skip
otherwise. Credentials come from `CURVEVISION_TEST_S3_ACCESS_KEY` and
`CURVEVISION_TEST_S3_SECRET_KEY`.

Each test uses a bucket of its own, created by `storage-init`: the command an operator (and
`docker-compose.yml`, and the Helm CI job) runs before the server's first upload.
"""

from __future__ import annotations

import os
import uuid
from collections.abc import Iterator

import httpx
import pytest

from curvevision import cli
from curvevision.core.config import Settings, configure_settings
from curvevision.storage import get_storage, reset_storage_cache
from curvevision.storage.base import ObjectNotFoundError

S3_URL = os.environ.get("CURVEVISION_TEST_S3_URL")
pytestmark = pytest.mark.skipif(
    not S3_URL, reason="set CURVEVISION_TEST_S3_URL to test against an S3 server"
)
pytest.importorskip("boto3", reason="the s3 extra")


@pytest.fixture
def s3_settings(tmp_path: object) -> Iterator[Settings]:
    settings = Settings(
        environment="test",
        secret_key="test-secret-key-not-used-anywhere-real",
        database_url="sqlite+aiosqlite:///:memory:",
        storage_backend="s3",
        s3_endpoint_url=S3_URL,
        s3_bucket=f"cv-test-{uuid.uuid4().hex[:12]}",
        s3_access_key_id=os.environ.get("CURVEVISION_TEST_S3_ACCESS_KEY", "curvevision"),
        s3_secret_access_key=os.environ.get("CURVEVISION_TEST_S3_SECRET_KEY", "curvevision"),
    )
    configure_settings(settings)
    reset_storage_cache()
    yield settings
    reset_storage_cache()
    configure_settings(None)


def test_storage_init_creates_the_bucket_and_is_safe_to_repeat(
    s3_settings: Settings, capsys: pytest.CaptureFixture[str]
) -> None:
    assert cli.main(["storage-init"]) == 0
    assert cli.main(["storage-init"]) == 0

    said = capsys.readouterr().out
    assert f"Created bucket {s3_settings.s3_bucket}" in said
    assert f"Bucket {s3_settings.s3_bucket} already exists" in said


async def test_objects_go_in_come_back_and_go_away(s3_settings: Settings) -> None:
    storage = get_storage(s3_settings)
    assert await storage.ensure_bucket() is True  # type: ignore[attr-defined]

    stored = await storage.put(
        "blobs/ab/frame.png", b"\x89PNG not really", content_type="image/png"
    )
    assert (stored.key, stored.size) == ("blobs/ab/frame.png", 15)
    assert await storage.get("blobs/ab/frame.png") == b"\x89PNG not really"
    assert await storage.exists("blobs/ab/frame.png")
    assert b"".join([chunk async for chunk in storage.stream("blobs/ab/frame.png", 4)]) == (
        b"\x89PNG not really"
    )

    # No public endpoint configured: a browser is never sent to the server's own address.
    assert storage.public_url("blobs/ab/frame.png") is None
    # With one, what a browser is handed is a presigned URL that works as is.
    reset_storage_cache()
    public = get_storage(s3_settings.model_copy(update={"s3_public_endpoint_url": S3_URL}))
    url = public.public_url("blobs/ab/frame.png")
    assert url is not None
    async with httpx.AsyncClient() as client:
        fetched = await client.get(url)
    assert fetched.status_code == 200
    assert fetched.content == b"\x89PNG not really"

    assert await storage.append("uploads/partial", b"one,") == 4
    assert await storage.append("uploads/partial", b"two") == 7
    assert await storage.get("uploads/partial") == b"one,two"

    await storage.delete("blobs/ab/frame.png")
    assert not await storage.exists("blobs/ab/frame.png")
    with pytest.raises(ObjectNotFoundError):
        await storage.get("blobs/ab/frame.png")
