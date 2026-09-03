"""Media ingestion: hashing, deduplication, probing, thumbnails, chunk planning."""

from __future__ import annotations

import hashlib
import uuid

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from curvevision.core.config import Settings
from curvevision.core.errors import NotFoundError
from curvevision.domain.enums import MediaKind
from curvevision.domain.media import Asset, MediaBlob
from curvevision.domain.task import Task
from curvevision.media import probe as media_probe
from curvevision.storage import Storage


def blob_key(sha256: str, filename: str) -> str:
    """Content-addressed storage key.

    Sharding by the first four hex characters keeps any one directory small, which matters
    for filesystem backends at scale.
    """
    suffix = filename.rsplit(".", 1)[-1].lower() if "." in filename else "bin"
    return f"blobs/{sha256[:2]}/{sha256[2:4]}/{sha256}.{suffix}"


async def ingest_upload(
    session: AsyncSession,
    settings: Settings,
    storage: Storage,
    task: Task,
    *,
    filename: str,
    data: bytes,
) -> Asset:
    """Store one uploaded file and attach it to ``task`` as the next asset.

    Identical bytes uploaded into three tasks are stored once: the blob is keyed by its
    SHA-256, so re-uploading is cheap and idempotent.
    """
    content_type = media_probe.validate_upload(filename, data[:32], len(data), settings)
    kind = media_probe.classify(filename, content_type, settings)
    digest = hashlib.sha256(data).hexdigest()

    blob = (
        await session.execute(select(MediaBlob).where(MediaBlob.sha256 == digest))
    ).scalar_one_or_none()

    if blob is None:
        info = media_probe.probe(data, filename, content_type, kind)
        key = blob_key(digest, filename)
        await storage.put(key, data, content_type=content_type)

        thumbnail_key: str | None = None
        if kind is MediaKind.IMAGE:
            thumbnail = media_probe.make_thumbnail(data, settings.thumbnail_max_edge)
            if thumbnail is not None:
                thumbnail_key = f"thumbnails/{digest[:2]}/{digest}.jpg"
                await storage.put(thumbnail_key, thumbnail, content_type="image/jpeg")

        blob = MediaBlob(
            sha256=digest,
            storage_key=key,
            size_bytes=len(data),
            content_type=content_type,
            kind=kind,
            width=info.width,
            height=info.height,
            frame_count=info.frame_count,
            duration_seconds=info.duration_seconds,
            frame_rate=info.frame_rate,
            thumbnail_key=thumbnail_key,
        )
        session.add(blob)
        await session.flush()

    next_position = (
        int(
            (
                await session.execute(
                    select(func.coalesce(func.max(Asset.position), -1)).where(
                        Asset.task_id == task.id
                    )
                )
            ).scalar_one()
        )
        + 1
    )

    asset = Asset(
        task_id=task.id,
        blob_id=blob.id,
        name=filename,
        position=next_position,
        start_frame=task.frame_count,
        frame_count=max(1, blob.frame_count or 1),
    )
    session.add(asset)
    await session.flush()
    return asset


async def list_assets(session: AsyncSession, task_id: uuid.UUID) -> list[Asset]:
    result = await session.execute(
        select(Asset).where(Asset.task_id == task_id).order_by(Asset.position)
    )
    return list(result.scalars().all())


async def get_asset(session: AsyncSession, task_id: uuid.UUID, asset_id: uuid.UUID) -> Asset:
    asset = await session.get(Asset, asset_id)
    if asset is None or asset.task_id != task_id:
        raise NotFoundError("Asset not found")
    return asset


async def resolve_frame(session: AsyncSession, task_id: uuid.UUID, frame: int) -> tuple[Asset, int]:
    """Map a task-global frame number to ``(asset, offset within that asset)``.

    Images contribute one frame each; a video contributes its whole frame count. This is
    what lets frame numbers stay contiguous across a mixed task.
    """
    result = await session.execute(
        select(Asset)
        .where(Asset.task_id == task_id, Asset.start_frame <= frame)
        .order_by(Asset.start_frame.desc())
        .limit(1)
    )
    asset = result.scalar_one_or_none()
    if asset is None or frame >= asset.start_frame + asset.frame_count:
        raise NotFoundError(f"Frame {frame} does not exist in this task")
    return asset, frame - asset.start_frame


def chunk_count(frame_count: int, frames_per_chunk: int) -> int:
    if frames_per_chunk <= 0:
        return 0
    return (frame_count + frames_per_chunk - 1) // frames_per_chunk


def chunk_range(chunk_index: int, frames_per_chunk: int, frame_count: int) -> tuple[int, int]:
    """Inclusive ``(start, stop)`` frame range for a chunk."""
    start = chunk_index * frames_per_chunk
    stop = min(start + frames_per_chunk - 1, frame_count - 1)
    return start, stop


async def delete_asset(session: AsyncSession, asset: Asset) -> None:
    """Detach an asset from its task.

    The underlying blob is intentionally left in storage: it may be referenced by another
    task, and reclaiming it belongs to a garbage-collection job that can verify that
    safely, not to an interactive delete.
    """
    await session.delete(asset)
