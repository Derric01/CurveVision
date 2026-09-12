"""Media ingestion: hashing, deduplication, probing, thumbnails, chunk planning."""

from __future__ import annotations

import asyncio
import hashlib
import uuid
from collections.abc import AsyncIterator
from pathlib import Path
from typing import IO

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from curvevision.core.config import Settings
from curvevision.core.errors import NotFoundError, ValidationError
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

    return await _attach(session, task, blob, name=filename)


async def ingest_local_file(
    session: AsyncSession,
    settings: Settings,
    task: Task,
    *,
    path: Path,
    position: int | None = None,
) -> Asset:
    """Attach a file that is already on this machine, without copying its bytes.

    This is the desktop application's normal path. Someone with 40 GB of images in a
    folder should not have to wait for 40 GB to be duplicated into an application
    directory before they can draw a box: the blob records where the file *is*.

    The file stays the user's. CurveVision reads it, never writes it, and never deletes
    it -- the garbage collector skips blobs owned this way.

    Only reachable in local mode. On a shared server a path names a file on the server's
    disk, so accepting one would be arbitrary file disclosure; the API layer refuses the
    route outright rather than relying on a check here.
    """
    resolved, data = await asyncio.to_thread(_read_source_file, path)
    content_type = media_probe.validate_upload(resolved.name, data[:32], len(data), settings)
    kind = media_probe.classify(resolved.name, content_type, settings)
    digest = hashlib.sha256(data).hexdigest()

    blob = (
        await session.execute(select(MediaBlob).where(MediaBlob.sha256 == digest))
    ).scalar_one_or_none()

    if blob is None:
        info = media_probe.probe(data, resolved.name, content_type, kind)
        blob = MediaBlob(
            sha256=digest,
            storage_key=None,
            source_path=str(resolved),
            size_bytes=len(data),
            content_type=content_type,
            kind=kind,
            width=info.width,
            height=info.height,
            frame_count=info.frame_count,
            duration_seconds=info.duration_seconds,
            frame_rate=info.frame_rate,
            # No thumbnail: generating one would mean writing a derived copy of a file the
            # user did not ask us to copy. The editor scales the original instead.
            thumbnail_key=None,
        )
        session.add(blob)
        await session.flush()
    elif blob.source_path is None and blob.storage_key is None:  # pragma: no cover - defensive
        blob.source_path = str(resolved)

    return await _attach(session, task, blob, name=resolved.name, position=position)


def _read_source_file(path: Path) -> tuple[Path, bytes]:
    """Resolve and read a file. Synchronous: callers run it off the event loop."""
    resolved = path.expanduser().resolve()
    if not resolved.is_file():
        raise ValidationError(f"{resolved} is not a file")
    return resolved, resolved.read_bytes()


def import_targets(root: Path, settings: Settings, *, recursive: bool = True) -> list[Path]:
    """Every media file at or under ``root``, in a stable order.

    Sorted by path so that frame numbers are reproducible: importing the same folder twice
    must not shuffle which image is frame 7.

    Synchronous, and deliberately so -- walking a folder of 50,000 files is exactly the
    kind of blocking call that must not run on the event loop. Callers use a worker thread.
    """
    resolved = root.expanduser().resolve()
    if resolved.is_file():
        return [resolved]
    if not resolved.is_dir():
        raise ValidationError(f"{root} does not exist on this machine")

    suffixes = {
        suffix.lower()
        for suffix in (*settings.allowed_image_extensions, *settings.allowed_video_extensions)
    }
    entries = resolved.rglob("*") if recursive else resolved.glob("*")
    return sorted(
        entry
        for entry in entries
        # Dotfiles are skipped: `.DS_Store` and friends are not somebody's dataset.
        if entry.suffix.lower() in suffixes and not entry.name.startswith(".") and entry.is_file()
    )


# ------------------------------------------------------------------------- reading bytes
#
# A blob's bytes live either in object storage or at a path on this machine. These three
# functions are the only place that distinction exists; everything else asks for a blob.


def is_presignable(blob: MediaBlob) -> bool:
    """Whether a direct URL could be issued for this blob.

    Never for a file annotated in place: there is no bucket to sign against, and the point
    is that the bytes were never uploaded anywhere.
    """
    return blob.source_path is None and blob.storage_key is not None


async def read_blob(blob: MediaBlob, storage: Storage) -> bytes:
    if blob.source_path is not None:
        try:
            return await asyncio.to_thread(Path(blob.source_path).read_bytes)
        except OSError as exc:
            # The file is the user's; they are free to move or delete it, and the honest
            # answer then is that this frame's media is gone, not that the server broke.
            raise NotFoundError(
                f"The file for this media has moved or cannot be read: {blob.source_path}"
            ) from exc
    if blob.storage_key is None:  # pragma: no cover - defensive
        raise NotFoundError("Media has neither stored bytes nor a source path")
    return await storage.get(blob.storage_key)


async def open_blob(
    blob: MediaBlob, storage: Storage, chunk_size: int = 1024 * 1024
) -> AsyncIterator[bytes]:
    """Open a blob for streaming, failing *before* the response begins.

    Awaiting the open is the whole point. A source file belongs to the user, who may have
    moved or deleted it; discovering that from inside a response body iterator means the
    status line has already been sent and the caller sees a truncated 200 instead of a
    404. So the handle is opened here and the generator below only reads from it.
    """
    if blob.source_path is not None:
        path = Path(blob.source_path)
        try:
            handle = await asyncio.to_thread(path.open, "rb")
        except OSError as exc:
            raise NotFoundError(
                f"The file for this media has moved or cannot be read: {path}"
            ) from exc
        return _read_handle(handle, chunk_size)
    if blob.storage_key is None:  # pragma: no cover - defensive
        raise NotFoundError("Media has neither stored bytes nor a source path")
    return storage.stream(blob.storage_key)


async def render_video_frame(blob: MediaBlob, storage: Storage, index: int) -> bytes:
    """One frame of a video, as a JPEG.

    Decoding is CPU-bound and runs off the event loop. A blob annotated in place is read
    straight from its path so a large video is not loaded into memory to reach one frame;
    a stored blob has to be fetched first.

    **This is O(n) in the frame index.** Frames are decoded from the start of the file
    because a frame number has to identify the same picture every time (see
    `media.video.VideoReader.iterate_frames`), and scrubbing deep into a long video is
    therefore slow. The fix is the chunked delivery the media pipeline is designed around
    -- `MediaChunk` and the chunk plan exist, the extraction does not -- and it is tracked
    as *In Progress* rather than hidden behind a cache that would only mask it.
    """
    from curvevision.media.video import VideoReader, VideoUnavailableError

    source: bytes | Path
    if blob.source_path is not None:
        source = Path(blob.source_path)
        if not await asyncio.to_thread(source.is_file):
            raise NotFoundError(
                f"The file for this media has moved or cannot be read: {blob.source_path}"
            )
    else:
        source = await read_blob(blob, storage)

    try:
        return await asyncio.to_thread(VideoReader(source).frame_jpeg, index)
    except VideoUnavailableError as exc:
        raise NotFoundError(str(exc)) from exc


async def _read_handle(handle: IO[bytes], chunk_size: int) -> AsyncIterator[bytes]:
    try:
        while chunk := await asyncio.to_thread(handle.read, chunk_size):
            yield chunk
    finally:
        await asyncio.to_thread(handle.close)


async def next_position(session: AsyncSession, task_id: uuid.UUID) -> int:
    """The ordinal the next asset added to this task should take."""
    highest = (
        await session.execute(
            select(func.coalesce(func.max(Asset.position), -1)).where(Asset.task_id == task_id)
        )
    ).scalar_one()
    return int(highest) + 1


async def _attach(
    session: AsyncSession,
    task: Task,
    blob: MediaBlob,
    *,
    name: str,
    position: int | None = None,
) -> Asset:
    """Append ``blob`` to ``task`` as its next asset.

    ``position`` may be supplied by a caller importing many files at once, so a folder of
    50,000 images does not run 50,000 `MAX(position)` queries.

    ``start_frame`` is provisional here. `tasks.recount_frames` reindexes every asset's
    offset from position order once the batch is in, and that is the authoritative pass --
    frame numbers have to be contiguous across the whole task, which no single insert can
    know.
    """
    if position is None:
        position = await next_position(session, task.id)

    asset = Asset(
        task_id=task.id,
        blob_id=blob.id,
        name=name,
        position=position,
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
