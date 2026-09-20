"""Media ingestion: hashing, deduplication, probing, thumbnails, chunk planning."""

from __future__ import annotations

import asyncio
import hashlib
import io
import uuid
import zipfile
from collections.abc import AsyncIterator
from pathlib import Path
from typing import IO

from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from curvevision.core.config import Settings
from curvevision.core.errors import ConflictError, NotFoundError, ValidationError
from curvevision.domain.enums import MediaKind, TaskStatus
from curvevision.domain.media import Asset, MediaBlob, MediaChunk, UploadSession
from curvevision.domain.task import Task
from curvevision.media import probe as media_probe
from curvevision.services import background as background_service
from curvevision.services import tasks as task_service
from curvevision.storage import ObjectNotFoundError, Storage


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
    return await _ingest_bytes(session, settings, storage, task, filename=filename, data=data)


async def _ingest_bytes(
    session: AsyncSession,
    settings: Settings,
    storage: Storage,
    task: Task,
    *,
    filename: str,
    data: bytes,
) -> Asset:
    """The common half of ``ingest_upload`` and a completed resumable upload: given the
    whole file's bytes, dedupe by hash and attach an asset.

    Split out so the two callers cannot drift -- a resumable upload's finished bytes are
    exactly a direct upload's bytes, just assembled from several requests instead of one.
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

    **This is O(n) in the frame index**, and remains so on purpose. Frames are decoded from
    the start of the file because a frame number has to identify the same picture every
    time (see `media.video.VideoReader.iterate_frames`). Chunked delivery is what makes
    scrubbing fast -- see `build_chunk` -- and this stays as the fallback for any frame
    whose chunk has not been built yet, so a video is never *unservable*, only slower.
    """
    from curvevision.media.video import VideoUnavailableError

    source = await _video_source(blob, storage)
    try:
        return await asyncio.to_thread(_decode_one, source, index)
    except VideoUnavailableError as exc:
        raise NotFoundError(str(exc)) from exc


def _decode_one(source: bytes | Path, index: int) -> bytes:
    from curvevision.media.video import VideoReader

    return VideoReader(source).frame_jpeg(index)


async def _video_source(blob: MediaBlob, storage: Storage) -> bytes | Path:
    """What to hand a ``VideoReader``: a path when we have one, bytes otherwise.

    A blob annotated in place is read straight from its path so a large video is not loaded
    into memory to reach one frame; a stored blob has to be fetched first.
    """
    if blob.source_path is not None:
        path = Path(blob.source_path)
        if not await asyncio.to_thread(path.is_file):
            raise NotFoundError(
                f"The file for this media has moved or cannot be read: {blob.source_path}"
            )
        return path
    return await read_blob(blob, storage)


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
        # An image contributes exactly one frame and nothing has to decode it to know that.
        # A video's count is whatever the container claimed, which is an estimate until
        # `correct_frame_counts` counts the frames for real.
        frame_count_exact=blob.kind is not MediaKind.VIDEO,
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


# ------------------------------------------------------------------------------ chunks
#
# A chunk is a contiguous run of a task's video frames, decoded once and stored as one ZIP
# of JPEGs named by task-global frame number. It exists for a single reason: decoding is
# sequential from the start of the file (a frame number has to identify the same picture
# every time -- see `media.video.VideoReader.iterate_frames`), so serving frame *n* on its
# own costs *n* decodes, and serving the 36 frames around it costs 36 x that. Building the
# chunk costs one pass and then those 36 frames are free.
#
# Chunks hold **video frames only**. An image asset already serves in constant time, and
# packing it into a chunk would mean re-encoding somebody's PNG as a JPEG to no benefit. A
# mixed task therefore has chunks with gaps in them, which is why every read falls back to
# decoding rather than assuming a hit.


def chunk_key(task_id: uuid.UUID, chunk_index: int, quality: str = "original") -> str:
    return f"chunks/{task_id}/{quality}/{chunk_index:06d}.zip"


def chunk_of(frame: int, settings: Settings) -> int:
    """Which chunk holds ``frame``. ``-1`` when chunking is switched off."""
    return frame // settings.frames_per_chunk if settings.frames_per_chunk > 0 else -1


def frame_entry_name(frame: int) -> str:
    """The name a frame has inside a chunk archive. Task-global, so it needs no context."""
    return f"{frame:06d}.jpg"


async def find_chunk(
    session: AsyncSession, task_id: uuid.UUID, chunk_index: int, quality: str = "original"
) -> MediaChunk | None:
    return (
        await session.execute(
            select(MediaChunk).where(
                MediaChunk.task_id == task_id,
                MediaChunk.index == chunk_index,
                MediaChunk.quality == quality,
            )
        )
    ).scalar_one_or_none()


async def _assets_in_range(
    session: AsyncSession, task_id: uuid.UUID, start: int, stop: int
) -> list[Asset]:
    """Every asset contributing at least one frame to the inclusive range."""
    result = await session.execute(
        select(Asset)
        .where(
            Asset.task_id == task_id,
            Asset.start_frame <= stop,
            Asset.start_frame + Asset.frame_count > start,
        )
        .order_by(Asset.start_frame)
    )
    return list(result.scalars())


def _build_archive(frames: list[tuple[int, bytes]]) -> bytes:
    """Pack JPEGs into a ZIP, uncompressed.

    `ZIP_STORED`, not `ZIP_DEFLATED`: JPEG is already compressed, so deflating it spends
    CPU to grow the file by a fraction of a percent. Stored entries also mean a reader can
    take one frame out of the archive without inflating anything around it.
    """
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, mode="w", compression=zipfile.ZIP_STORED) as archive:
        for frame, jpeg in frames:
            archive.writestr(frame_entry_name(frame), jpeg)
    return buffer.getvalue()


def _decode_chunk_frames(
    source: bytes | Path, offsets: list[int], start_frame: int
) -> list[tuple[int, bytes]]:
    """One decode pass over `source`, returning `(task frame, jpeg)` for each offset."""
    from curvevision.media.video import VideoReader

    reader = VideoReader(source)
    return [
        (start_frame + offset, jpeg) for offset, jpeg in reader.frames_jpeg(iter(sorted(offsets)))
    ]


async def build_chunk(
    session: AsyncSession,
    settings: Settings,
    storage: Storage,
    task: Task,
    chunk_index: int,
    *,
    quality: str = "original",
) -> MediaChunk | None:
    """Decode one chunk's video frames in a single pass and store them as one archive.

    Returns the existing row when the chunk is already built, so this is safe to call from
    a request handler that just missed the cache and from the background job at the same
    time. Returns ``None`` when the range holds no video frames at all -- an image-only
    task has nothing to gain here and gets no empty archives written for it.
    """
    total = chunk_count(task.frame_count, settings.frames_per_chunk)
    if chunk_index < 0 or chunk_index >= total:
        raise NotFoundError(f"This task has no chunk {chunk_index}")

    existing = await find_chunk(session, task.id, chunk_index, quality)
    if existing is not None:
        return existing

    start, stop = chunk_range(chunk_index, settings.frames_per_chunk, task.frame_count)
    frames: list[tuple[int, bytes]] = []

    for asset in await _assets_in_range(session, task.id, start, stop):
        blob = await session.get(MediaBlob, asset.blob_id)
        if blob is None or blob.kind is not MediaKind.VIDEO:
            continue  # Images are already served in constant time; see the note above.

        first = max(start, asset.start_frame)
        last = min(stop, asset.start_frame + asset.frame_count - 1)
        offsets = list(range(first - asset.start_frame, last - asset.start_frame + 1))
        if not offsets:  # pragma: no cover - defensive
            continue

        source = await _video_source(blob, storage)
        # Decoding is CPU-bound and must not hold the event loop for the length of a clip.
        frames.extend(
            await asyncio.to_thread(_decode_chunk_frames, source, offsets, asset.start_frame)
        )

    if not frames:
        return None

    frames.sort(key=lambda pair: pair[0])
    archive = _build_archive(frames)
    key = chunk_key(task.id, chunk_index, quality)
    await storage.put(key, archive, content_type="application/zip")

    chunk = MediaChunk(
        task_id=task.id,
        index=chunk_index,
        quality=quality,
        start_frame=start,
        frame_count=len(frames),
        storage_key=key,
        size_bytes=len(archive),
        content_type="application/zip",
    )
    try:
        # A SAVEPOINT, not the whole transaction: losing this race must not discard
        # whatever else the caller had pending.
        async with session.begin_nested():
            session.add(chunk)
            await session.flush()
    except IntegrityError:
        # Another request built the same chunk first. Its archive is at the same key and
        # holds the same frames, so the loser simply uses the winner's row.
        return await find_chunk(session, task.id, chunk_index, quality)
    return chunk


# --------------------------------------------------------------- exact frame counts
#
# A video task is created with an *estimated* frame count, because an exact one means
# decoding the whole file and that cannot happen inside an upload request. The estimate
# comes from container metadata or duration x frame rate, and both drift -- `stream.frames`
# is zero in many containers and wrong in others, and the product is wrong for any
# variable-frame-rate video, which is most phone footage.
#
# An overestimate is the damaging direction: the task offers frames that do not exist, and
# an annotator who steps onto one sees a failure that looks like missing media. Correcting
# it is what these do.


def _count_frames(source: bytes | Path) -> int:
    from curvevision.media.video import VideoReader

    return VideoReader(source).frame_count()


async def needs_exact_count(session: AsyncSession, task_id: uuid.UUID) -> bool:
    """Whether this task holds a video whose frame count has not been counted yet.

    Asked before enqueueing the job rather than inside it, so a folder of 50,000
    photographs does not schedule background work that would find nothing to do.

    The `frame_count_exact` half matters as much as the video half: adding three photographs
    to a task that already holds a counted two-hour clip would otherwise schedule a job that
    decodes the whole clip again to learn a number already on the row.
    """
    found = await session.execute(
        select(Asset.id)
        .join(MediaBlob, MediaBlob.id == Asset.blob_id)
        .where(
            Asset.task_id == task_id,
            MediaBlob.kind == MediaKind.VIDEO,
            Asset.frame_count_exact.is_(False),
        )
        .limit(1)
    )
    return found.first() is not None


async def finish_ingestion(session: AsyncSession, task: Task, *, created: bool = True) -> None:
    """Recount frames, rebuild jobs and mark the task ready once media has landed.

    Every upload path -- direct multipart, local-folder import, and a completed resumable
    upload -- ends with the same sequence. It used to be copied at each call site; a third
    copy for the resumable path was the "the next feature needs a fourth" case that earns
    pulling it out.

    ``created`` is False when a batch imported nothing at all (an empty folder, every file
    skipped) -- recounting and rebuilding jobs off zero new assets would be pure overhead,
    and a task with no media at all must not flip from draft to ready.
    """
    if created:
        await task_service.recount_frames(session, task)
        await task_service.rebuild_jobs(session, task)
        if task.status is TaskStatus.DRAFT and task.frame_count:
            task.status = TaskStatus.READY
    await session.commit()

    # A video's frame count is an estimate until something decodes it; correct it in the
    # background rather than making this request wait for a two-hour clip.
    if created and await needs_exact_count(session, task.id):
        await background_service.enqueue(
            session,
            kind="media.probe_task",
            payload={"task_id": str(task.id)},
            resource_type="task",
            resource_id=task.id,
        )


def _upload_key(upload_session_id: uuid.UUID) -> str:
    return f"uploads/{upload_session_id}.part"


async def create_upload_session(
    session: AsyncSession,
    settings: Settings,
    task: Task,
    *,
    filename: str,
    size: int,
    created_by_id: uuid.UUID | None,
) -> UploadSession:
    """Start a resumable upload: an offset-based ``PATCH`` protocol for large media over a
    flaky connection, where a single multipart request either buffers the whole file in
    memory or dies with the connection and forces a full restart.

    Only what is knowable before any bytes exist is validated here -- name and declared
    size. The rest (magic bytes, actual content) is validated once the bytes are in hand,
    at `complete_upload`.
    """
    media_probe.validate_declared_upload(filename, size, settings)
    upload_session = UploadSession(
        id=uuid.uuid4(),
        task_id=task.id,
        created_by_id=created_by_id,
        filename=filename,
        declared_size=size,
        received_bytes=0,
        storage_key="",
        completed=False,
    )
    upload_session.storage_key = _upload_key(upload_session.id)
    session.add(upload_session)
    await session.flush()
    return upload_session


async def get_upload_session(
    session: AsyncSession, task_id: uuid.UUID, upload_id: uuid.UUID
) -> UploadSession:
    upload_session = await session.get(UploadSession, upload_id)
    if upload_session is None or upload_session.task_id != task_id:
        raise NotFoundError("Upload session not found")
    return upload_session


async def append_upload_chunk(
    storage: Storage,
    upload_session: UploadSession,
    *,
    offset: int,
    data: bytes,
) -> UploadSession:
    """Append one chunk at ``offset``, the byte position the client believes it is resuming
    from.

    Requiring the caller to state the offset it thinks it is at -- rather than trusting a
    lost connection's last acknowledged write -- is what makes the protocol safe to retry:
    a chunk resent after a dropped response is rejected as a mismatch instead of appended
    twice, and a client that lost track of its own progress is told the real one rather than
    silently corrupting the object.
    """
    if upload_session.completed:
        raise ConflictError("This upload has already completed")
    if offset != upload_session.received_bytes:
        raise ConflictError(
            f"Offset mismatch: sent {offset}, upload is at {upload_session.received_bytes}",
            received_bytes=upload_session.received_bytes,
        )
    if upload_session.received_bytes + len(data) > upload_session.declared_size:
        raise ValidationError(
            f"Chunk would exceed the declared size of {upload_session.declared_size} bytes"
        )

    upload_session.received_bytes = await storage.append(upload_session.storage_key, data)
    return upload_session


async def complete_upload(
    session: AsyncSession,
    settings: Settings,
    storage: Storage,
    task: Task,
    upload_session: UploadSession,
) -> Asset:
    """Finalise a resumable upload once every declared byte has arrived.

    Reads the assembled bytes back and hands them to the same dedupe/probe path a direct
    upload uses, so the two can never validate or store a file differently. The temporary
    upload key is removed once the content-addressed blob owns the bytes (or already did,
    for a file whose hash matches one already stored).
    """
    if upload_session.completed:
        raise ConflictError("This upload has already completed")
    if upload_session.received_bytes != upload_session.declared_size:
        raise ValidationError(
            f"Upload incomplete: {upload_session.received_bytes} of "
            f"{upload_session.declared_size} bytes received"
        )

    try:
        data = await storage.get(upload_session.storage_key)
    except ObjectNotFoundError:
        # A declared size of 0 is "complete" the instant the session is created, with no
        # chunk ever appended and so no key ever written. Falling through with empty bytes
        # reuses `_ingest_bytes`'s own rejection of an empty file (the same one a direct
        # upload gets) instead of a storage error escaping as an unhandled 500.
        data = b""
    asset = await _ingest_bytes(
        session, settings, storage, task, filename=upload_session.filename, data=data
    )
    await storage.delete(upload_session.storage_key)
    upload_session.completed = True
    return asset


async def abort_upload(
    session: AsyncSession, storage: Storage, upload_session: UploadSession
) -> None:
    """Cancel an in-progress upload: reclaim its partial bytes and drop the session record.

    Mirrors `delete_asset`'s explicit flush, for the same reason -- nothing else recounts
    frames off the back of this one, but leaving the delete unflushed would let a caller
    that immediately re-queries upload sessions for this task still see it.
    """
    if upload_session.completed:
        raise ConflictError("This upload has already completed")
    await storage.delete(upload_session.storage_key)
    await session.delete(upload_session)
    await session.flush()


async def estimated_assets(session: AsyncSession, task_id: uuid.UUID) -> list[Asset]:
    """This task's assets whose frame count has never been verified, in position order.

    Empty for a task of images, and for a video task the probe has finished with. Anything
    it returns is a file whose frame count may overstate the media, which is what the task
    page warns about.
    """
    result = await session.execute(
        select(Asset)
        .where(Asset.task_id == task_id, Asset.frame_count_exact.is_(False))
        .order_by(Asset.position)
    )
    return list(result.scalars().all())


async def exact_frame_count(blob: MediaBlob, storage: Storage) -> int:
    """How many frames this video really has, by decoding all of them."""
    source = await _video_source(blob, storage)
    return await asyncio.to_thread(_count_frames, source)


async def correct_frame_counts(
    session: AsyncSession, storage: Storage, task: Task
) -> list[tuple[str, int, int]]:
    """Replace every video asset's estimated frame count with a counted one.

    Returns `(asset name, before, after)` for each one that actually changed, so a caller
    can report what it did rather than claiming to have done something.

    Every asset it manages to count is marked `frame_count_exact`, including the ones whose
    count already matched: the flag records *that the file was decoded*, not that the number
    moved, and a correct estimate is still only an estimate until something checks it. An
    asset it could not count keeps the flag False, which is how the task page knows there is
    still a file to warn about.

    The blob's own count is corrected too: it is content-addressed, so the same video
    attached to a second task starts with the right number instead of re-earning the
    estimate.
    """
    assets = (
        await session.execute(
            select(Asset).where(Asset.task_id == task.id).order_by(Asset.position)
        )
    ).scalars()

    changed: list[tuple[str, int, int]] = []
    verified = 0
    for asset in assets:
        if asset.frame_count_exact:
            # Already decoded once. Blobs are content-addressed and immutable, so counting
            # again would walk the whole file to learn what is already on the row.
            continue
        blob = await session.get(MediaBlob, asset.blob_id)
        if blob is None or blob.kind is not MediaKind.VIDEO:
            continue
        try:
            counted = await exact_frame_count(blob, storage)
        except (NotFoundError, ValidationError):
            # A file that moved, or one this build cannot decode. The estimate stands --
            # it is wrong, but replacing it with zero would be worse -- and the asset stays
            # marked inexact, so the fact that it stood is visible rather than swallowed.
            continue
        if counted <= 0:
            continue
        asset.frame_count_exact = True
        verified += 1
        if counted == asset.frame_count:
            continue
        changed.append((asset.name, asset.frame_count, counted))
        asset.frame_count = counted
        blob.frame_count = counted

    if changed or verified:
        await session.flush()
    return changed


async def discard_chunks(session: AsyncSession, storage: Storage, task_id: uuid.UUID) -> int:
    """Delete a task's chunks, rows and stored archives alike. Returns how many.

    Chunks are addressed by frame range, so any change to a task's frame numbering
    invalidates them wholesale -- an earlier asset gaining a frame shifts every later
    asset's offset, and reasoning about which chunks survive that costs more than
    rebuilding them. They are a cache; the next request rebuilds what is wanted.
    """
    chunks = list(
        (await session.execute(select(MediaChunk).where(MediaChunk.task_id == task_id))).scalars()
    )
    for chunk in chunks:
        # Storage first: a row without bytes falls back to decoding, but bytes without a
        # row are a leak nothing will ever collect.
        await storage.delete(chunk.storage_key)
        await session.delete(chunk)
    if chunks:
        await session.flush()
    return len(chunks)


async def video_frame_jpeg(
    session: AsyncSession,
    settings: Settings,
    storage: Storage,
    task: Task,
    blob: MediaBlob,
    frame: int,
    offset: int,
) -> bytes:
    """One video frame, from a chunk when possible and by decoding when not.

    Three steps, in cost order:

    1. **A built chunk** -- unzip one entry. Constant time, and the case that matters:
       an annotator stepping through frames hits it for 35 of every 36 frames.
    2. **Build the chunk, then take the frame out of it.** Reaching frame *n* means
       decoding *n* frames either way, so finishing the pass to the end of the chunk is
       nearly free and makes the next 35 frames constant-time. Stepping through frames
       0-35 costs 630 frame-decodes without this and 36 with it.
    3. **Decode the single frame.** Chunking disabled, no video in the range, or storage
       that lost the archive. Slower, always correct, and the reason a video is never
       *unservable* -- only slower.

    The caller commits: a chunk built here is worth keeping even though the request that
    paid for it is a read.
    """
    index = chunk_of(frame, settings)
    if index >= 0:
        jpeg = await frame_from_chunk(session, settings, storage, task.id, frame)
        if jpeg is not None:
            return jpeg

        built = await build_chunk(session, settings, storage, task, index)
        if built is not None:
            jpeg = await frame_from_chunk(session, settings, storage, task.id, frame)
            if jpeg is not None:
                return jpeg

    return await render_video_frame(blob, storage, offset)


async def frame_from_chunk(
    session: AsyncSession,
    settings: Settings,
    storage: Storage,
    task_id: uuid.UUID,
    frame: int,
    *,
    quality: str = "original",
) -> bytes | None:
    """One frame's JPEG out of an already-built chunk, or ``None`` if there isn't one.

    Deliberately never builds. A missing chunk means the caller falls back to decoding that
    one frame, which is slower but always correct; making a frame request wait on a whole
    chunk build would turn a slow scrub into a stalled one.
    """
    if settings.frames_per_chunk <= 0:
        return None
    chunk = await find_chunk(session, task_id, frame // settings.frames_per_chunk, quality)
    if chunk is None:
        return None
    try:
        archive = await storage.get(chunk.storage_key)
    except ObjectNotFoundError:
        # The row outlived its bytes. Fall back rather than 500 -- the frame is still
        # servable, and the next build will replace the archive.
        return None
    return await asyncio.to_thread(_extract_frame, archive, frame)


def _extract_frame(archive: bytes, frame: int) -> bytes | None:
    try:
        with zipfile.ZipFile(io.BytesIO(archive)) as zipped:
            return zipped.read(frame_entry_name(frame))
    except (KeyError, zipfile.BadZipFile):
        return None


async def delete_asset(session: AsyncSession, asset: Asset) -> None:
    """Detach an asset from its task.

    The underlying blob is intentionally left in storage: it may be referenced by another
    task, and reclaiming it belongs to a garbage-collection job that can verify that
    safely, not to an interactive delete.

    **The flush is not optional.** The sessionmaker is built with ``autoflush=False``, so
    without it the DELETE does not reach the database until the caller commits -- and every
    caller recounts the task's frames first. That recount would then query the assets, still
    see this one, and leave the task counting a frame whose file is gone, with a job range
    covering it. A browser harness found that: it deleted an unreadable clip and the task
    went on offering the frames the clip had invented.
    """
    await session.delete(asset)
    await session.flush()
