"""When the server hands a browser a presigned URL, and when it streams the bytes itself.

A presigned URL is only useful if the browser can reach the address in it. The S3 endpoint
the server uses is very often one only it can reach — `http://s3:9000` inside Docker
Compose or a Kubernetes cluster — and until iteration 85 the server redirected every image
request there regardless, so the editor of a default Compose deployment never showed an
image. Now it presigns only for an address the operator says browsers can reach
(`s3_public_endpoint_url`), and streams through the API otherwise.

Presigning is local computation in botocore: nothing here touches a network.
"""

from __future__ import annotations

from urllib.parse import urlsplit

import pytest

pytest.importorskip("boto3", reason="the s3 extra")

from curvevision.core.config import Settings
from curvevision.storage.s3 import S3Storage


def storage(**overrides: object) -> S3Storage:
    return S3Storage(
        Settings(
            environment="test",
            secret_key="test-secret-key-not-used-anywhere-real",
            storage_backend="s3",
            s3_endpoint_url="http://s3:9000",
            s3_access_key_id="key",
            s3_secret_access_key="secret",
            **overrides,  # type: ignore[arg-type]
        )
    )


def test_an_endpoint_only_the_server_can_reach_is_never_handed_to_a_browser() -> None:
    assert storage().public_url("blobs/ab/frame.png") is None


def test_a_public_endpoint_is_what_the_presigned_url_names() -> None:
    url = storage(s3_public_endpoint_url="https://media.example.com").public_url(
        "blobs/ab/frame.png", expires_in=60
    )

    assert url is not None
    parts = urlsplit(url)
    assert (parts.scheme, parts.netloc) == ("https", "media.example.com")
    assert parts.path == "/curvevision/blobs/ab/frame.png"
    assert "X-Amz-Signature=" in parts.query
    assert "X-Amz-Expires=60" in parts.query
