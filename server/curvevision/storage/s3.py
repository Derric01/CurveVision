"""S3-compatible storage.

Works against AWS S3, MinIO, Cloudflare R2, Ceph RGW and Backblaze B2 -- the S3 API is the
de-facto standard, which is why the abstraction has exactly one remote implementation
rather than one per vendor.

``boto3`` is synchronous, so every call is offloaded to a worker thread. That is the right
trade here: these calls are network-bound and infrequent relative to database work, and an
async S3 client would mean a less-proven dependency.
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator, Iterable
from typing import TYPE_CHECKING, Any

from curvevision.core.config import Settings
from curvevision.storage.base import ObjectNotFoundError, StoredObject

if TYPE_CHECKING:  # pragma: no cover - typing only
    pass

DEFAULT_CHUNK = 1024 * 1024


class S3Storage:
    def __init__(self, settings: Settings) -> None:
        try:
            import boto3
            from botocore.config import Config
        except ImportError as exc:  # pragma: no cover - exercised by deployment, not tests
            raise RuntimeError(
                "S3 storage requires the 's3' extra: pip install 'curvevision-server[s3]'"
            ) from exc

        self._bucket = settings.s3_bucket
        self._region = settings.s3_region
        self._presign_ttl = settings.presigned_url_ttl_seconds

        def client(endpoint_url: str | None) -> Any:
            return boto3.client(
                "s3",
                endpoint_url=endpoint_url,
                region_name=settings.s3_region,
                aws_access_key_id=settings.s3_access_key_id,
                aws_secret_access_key=settings.s3_secret_access_key,
                config=Config(
                    signature_version="s3v4",
                    s3={"addressing_style": "path" if settings.s3_force_path_style else "auto"},
                    retries={"max_attempts": 5, "mode": "standard"},
                ),
            )

        self._client: Any = client(settings.s3_endpoint_url)
        # A presigned URL names the host it was signed for, so one meant for a browser is
        # signed against the address the browser uses — and only when there is one.
        self._presigner: Any = (
            client(settings.s3_public_endpoint_url) if settings.s3_public_endpoint_url else None
        )

    @property
    def bucket(self) -> str:
        return self._bucket

    async def ensure_bucket(self) -> bool:
        """Create the bucket if it does not exist. Returns whether it had to be created.

        `curvevision-server storage-init` runs this before the server's first upload, so a
        new installation fails on a clear setup step rather than on a confusing S3 error in
        the middle of someone's upload.
        """

        def _ensure() -> bool:
            from botocore.exceptions import ClientError

            try:
                self._client.head_bucket(Bucket=self._bucket)
                return False
            except ClientError as exc:
                status = exc.response.get("ResponseMetadata", {}).get("HTTPStatusCode")
                if status != 404 and exc.response.get("Error", {}).get("Code") not in (
                    "404",
                    "NoSuchBucket",
                    "NotFound",
                ):
                    raise
            # Outside us-east-1, S3 wants to be told where; everywhere else it is the default.
            location = (
                {"CreateBucketConfiguration": {"LocationConstraint": self._region}}
                if self._region and self._region != "us-east-1"
                else {}
            )
            self._client.create_bucket(Bucket=self._bucket, **location)
            return True

        return await asyncio.to_thread(_ensure)

    async def put(
        self,
        key: str,
        data: bytes | Iterable[bytes],
        *,
        content_type: str = "application/octet-stream",
    ) -> StoredObject:
        body = data if isinstance(data, bytes) else b"".join(data)
        await asyncio.to_thread(
            self._client.put_object,
            Bucket=self._bucket,
            Key=key,
            Body=body,
            ContentType=content_type,
        )
        return StoredObject(key=key, size=len(body), content_type=content_type)

    async def append(self, key: str, data: bytes) -> int:
        """Read-modify-write append.

        S3 has no native append. Resumable uploads therefore stage parts locally or use
        multipart uploads; this path exists for small objects and for API parity, and is
        intentionally not the mechanism behind large-file uploads.
        """
        try:
            existing = await self.get(key)
        except ObjectNotFoundError:
            existing = b""
        merged = existing + data
        await self.put(key, merged)
        return len(merged)

    async def get(self, key: str) -> bytes:
        def _get() -> bytes:
            try:
                response = self._client.get_object(Bucket=self._bucket, Key=key)
            except self._client.exceptions.NoSuchKey as exc:
                raise ObjectNotFoundError(key) from exc
            body: bytes = response["Body"].read()
            return body

        return await asyncio.to_thread(_get)

    async def stream(self, key: str, chunk_size: int = DEFAULT_CHUNK) -> AsyncIterator[bytes]:
        def _open() -> Any:
            try:
                return self._client.get_object(Bucket=self._bucket, Key=key)["Body"]
            except self._client.exceptions.NoSuchKey as exc:
                raise ObjectNotFoundError(key) from exc

        body = await asyncio.to_thread(_open)
        try:
            while chunk := await asyncio.to_thread(body.read, chunk_size):
                yield chunk
        finally:
            await asyncio.to_thread(body.close)

    async def delete(self, key: str) -> None:
        await asyncio.to_thread(self._client.delete_object, Bucket=self._bucket, Key=key)

    async def exists(self, key: str) -> bool:
        def _head() -> bool:
            try:
                self._client.head_object(Bucket=self._bucket, Key=key)
                return True
            except Exception:
                return False

        return await asyncio.to_thread(_head)

    def public_url(self, key: str, *, expires_in: int | None = None) -> str | None:
        if self._presigner is None:
            return None
        url: str = self._presigner.generate_presigned_url(
            "get_object",
            Params={"Bucket": self._bucket, "Key": key},
            ExpiresIn=expires_in or self._presign_ttl,
        )
        return url
