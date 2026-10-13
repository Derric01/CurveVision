"""Backing up an installation, and restoring it, as one archive.

An annotation row whose image is missing is not a usable dataset, and neither half of an
installation is worth restoring alone — so one archive holds both: every row of every table,
and every stored object a row refers to. Until iteration 79 a backup was two commands an
operator ran by hand against two systems (`pg_dump`, `mc mirror`), for the server only, and
nothing ever restored one to check.

**The rows are written as JSON, not as a database dump**, table by table in foreign-key
order. That makes a backup independent of the database it came from: an archive from the
desktop application's SQLite restores into a team server's PostgreSQL, which is how a
person who started alone moves their work to a shared server. The cost is speed on very
large installations, where `pg_dump` remains the faster tool (`docs/OPERATIONS.md`).

**Objects are found from the rows** — every `storage_key` and `thumbnail_key` column, and
each released dataset version's frozen copy — since storage has no listing operation and
must not need one. An object a row names that storage does not have is reported, not
fatal: a backup of a damaged installation is still worth more than none. Each object's
SHA-256 is recorded, and checked on restore.

Media annotated in place by the desktop application (`MediaBlob.source_path`) is the
user's own files, where they left them, and is not copied; the archive says how many.

**A restore needs an empty installation at the same schema revision** as the backup, and
refuses anything else: merging two installations' rows is not a restore, and rows written
by another revision may not fit this one's columns. Run the same version's migrations
first (`alembic upgrade head`, which the server does on start).

Archive layout (an uncompressed tar — media is already compressed):

    manifest.json                    what is inside, and the counts to check it against
    database/<table>.jsonl.gz        one JSON object per row
    storage/<key>                    one file per stored object
"""

from __future__ import annotations

import contextlib
import gzip
import hashlib
import io
import json
import tarfile
import tempfile
import uuid
from collections.abc import Iterator
from dataclasses import dataclass, field
from datetime import UTC, date, datetime
from enum import Enum
from pathlib import Path
from typing import Any

import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncConnection, AsyncEngine

from curvevision import __version__
from curvevision.core.types import GUID, UTCDateTime
from curvevision.domain import Base  # every model registered: a CLI process imports none
from curvevision.storage import Storage
from curvevision.storage.base import ObjectNotFoundError

FORMAT = "curvevision-backup"
FORMAT_VERSION = 1
#: Columns holding a storage key, in whatever table has them.
_KEY_COLUMNS = ("storage_key", "thumbnail_key")
_BATCH = 500
_READ_CHUNK = 1024 * 1024


class BackupError(RuntimeError):
    """A backup or restore that cannot go ahead, with the reason a person can act on."""


@dataclass(slots=True)
class BackupReport:
    tables: dict[str, int] = field(default_factory=dict)
    objects: int = 0
    object_bytes: int = 0
    #: Keys a row names that storage does not hold.
    missing: list[str] = field(default_factory=list)
    #: Media the desktop application annotated in place, not copied.
    in_place_files: int = 0
    revision: str | None = None


# ----------------------------------------------------------------------------- values


def _encode(value: Any) -> Any:
    if isinstance(value, uuid.UUID):
        return str(value)
    if isinstance(value, datetime | date):
        return value.isoformat()
    if isinstance(value, Enum):
        return value.value
    raise TypeError(f"cannot back up a {type(value).__name__}")


def _decoder(column: sa.Column[Any]) -> Any:
    """How a JSON value goes back into `column`; `None` when it is used as it is."""
    kind = column.type
    if isinstance(kind, GUID):
        return uuid.UUID
    if isinstance(kind, UTCDateTime | sa.DateTime):

        def parse(text: str) -> datetime:
            parsed = datetime.fromisoformat(text)
            return parsed if parsed.tzinfo else parsed.replace(tzinfo=UTC)

        return parse
    if isinstance(kind, sa.Date):
        return date.fromisoformat
    return None


def _tables() -> list[sa.Table]:
    """Every data table, parents before children."""
    return [table for table in Base.metadata.sorted_tables if table.name != "alembic_version"]


def _parents_first(table: sa.Table, rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Rows of a table that refers to itself (a label's parent), ordered so each row's
    parent is inserted before it; foreign keys are enforced on both databases."""
    own = [
        fk.parent.name
        for fk in table.foreign_keys
        if fk.column.table is table and fk.parent.name != fk.column.name
    ]
    if not own:
        return rows
    key = next(iter(table.primary_key.columns)).name
    by_id = {row[key]: row for row in rows}
    placed: set[Any] = set()
    ordered: list[dict[str, Any]] = []

    def place(row: dict[str, Any], seen: frozenset[Any] = frozenset()) -> None:
        if row[key] in placed:
            return
        for column in own:
            parent = by_id.get(row.get(column))
            if parent is not None and parent[key] not in seen:
                place(parent, seen | {row[key]})
        placed.add(row[key])
        ordered.append(row)

    for row in rows:
        place(row)
    return ordered


async def _revision(connection: AsyncConnection) -> str | None:
    try:
        result = await connection.execute(sa.text("SELECT version_num FROM alembic_version"))
    except sa.exc.DBAPIError:
        return None
    return result.scalar_one_or_none()


# ----------------------------------------------------------------------------- backup


async def _object_keys(connection: AsyncConnection) -> tuple[list[str], int]:
    """Every storage key a row names, and how many media files live in place."""
    keys: list[str] = []
    for table in _tables():
        for name in _KEY_COLUMNS:
            if name in table.columns:
                column = table.columns[name]
                result = await connection.execute(sa.select(column).where(column.is_not(None)))
                values: list[Any] = list(result.scalars())
                keys.extend(str(value) for value in values)
    if "dataset_versions" in Base.metadata.tables:
        versions = Base.metadata.tables["dataset_versions"]
        manifests: list[Any] = list(
            (await connection.execute(sa.select(versions.c.manifest))).scalars()
        )
        for manifest in manifests:
            if isinstance(manifest, dict) and manifest.get("snapshot_key"):
                keys.append(str(manifest["snapshot_key"]))
    blobs = Base.metadata.tables["media_blobs"]
    in_place = (
        await connection.execute(
            sa.select(sa.func.count()).select_from(blobs).where(blobs.c.source_path.is_not(None))
        )
    ).scalar_one()
    return sorted(set(keys)), int(in_place)


def _add_bytes(archive: tarfile.TarFile, name: str, data: bytes) -> None:
    info = tarfile.TarInfo(name)
    info.size = len(data)
    info.mtime = int(datetime.now(UTC).timestamp())
    archive.addfile(info, io.BytesIO(data))


async def write_backup(engine: AsyncEngine, storage: Storage, destination: Path) -> BackupReport:
    """Write the whole installation to `destination`, a tar archive."""
    report = BackupReport()
    objects: list[dict[str, Any]] = []
    destination.parent.mkdir(parents=True, exist_ok=True)
    partial = destination.with_name(destination.name + ".partial")

    with tarfile.open(partial, "w") as archive:
        # One transaction, so the rows describe one moment.
        async with engine.connect() as connection:
            report.revision = await _revision(connection)
            for table in _tables():
                lines = io.BytesIO()
                with gzip.GzipFile(fileobj=lines, mode="wb", mtime=0) as packed:
                    count = 0
                    for row in (await connection.execute(sa.select(table))).mappings():
                        packed.write(json.dumps(dict(row), default=_encode).encode() + b"\n")
                        count += 1
                report.tables[table.name] = count
                _add_bytes(archive, f"database/{table.name}.jsonl.gz", lines.getvalue())
            keys, report.in_place_files = await _object_keys(connection)

        for key in keys:
            with tempfile.TemporaryFile() as spool:
                digest = hashlib.sha256()
                try:
                    async for chunk in storage.stream(key):
                        spool.write(chunk)
                        digest.update(chunk)
                except ObjectNotFoundError:
                    report.missing.append(key)
                    continue
                size = spool.tell()
                spool.seek(0)
                info = tarfile.TarInfo(f"storage/{key}")
                info.size = size
                info.mtime = int(datetime.now(UTC).timestamp())
                archive.addfile(info, spool)
            objects.append({"key": key, "size": size, "sha256": digest.hexdigest()})
            report.objects += 1
            report.object_bytes += size

        manifest = {
            "format": FORMAT,
            "format_version": FORMAT_VERSION,
            "curvevision_version": __version__,
            "alembic_revision": report.revision,
            "database": engine.dialect.name,
            "created_at": datetime.now(UTC).isoformat(),
            "tables": report.tables,
            "objects": objects,
            "missing_objects": report.missing,
            "in_place_files": report.in_place_files,
        }
        _add_bytes(archive, "manifest.json", json.dumps(manifest, indent=2).encode())

    # Only a finished archive takes the name: an interrupted backup must not look like one.
    partial.replace(destination)
    return report


# ---------------------------------------------------------------------------- restore


def read_manifest(source: Path) -> dict[str, Any]:
    try:
        with tarfile.open(source, "r") as archive:
            member = archive.extractfile("manifest.json")
            if member is None:
                raise KeyError("manifest.json")
            manifest = json.loads(member.read())
    except (tarfile.TarError, KeyError, json.JSONDecodeError, OSError) as exc:
        raise BackupError(f"{source} is not a CurveVision backup ({exc})") from exc
    if manifest.get("format") != FORMAT:
        raise BackupError(f"{source} is not a CurveVision backup")
    if int(manifest.get("format_version", 0)) > FORMAT_VERSION:
        raise BackupError(
            f"{source} was written by a newer CurveVision (backup format "
            f"{manifest['format_version']}); restore it with that version or later"
        )
    return dict(manifest)


def _rows(archive: tarfile.TarFile, table: sa.Table) -> Iterator[dict[str, Any]]:
    try:
        member = archive.extractfile(f"database/{table.name}.jsonl.gz")
    except KeyError:
        return  # a table this backup's revision did not have; the revision check covers it
    if member is None:
        return
    decoders = {column.name: decoder for column in table.columns if (decoder := _decoder(column))}
    with gzip.GzipFile(fileobj=member) as packed:
        for line in packed:
            row = json.loads(line)
            for name, decode in decoders.items():
                if row.get(name) is not None:
                    row[name] = decode(row[name])
            yield row


def _chunks(member: io.BufferedReader) -> Iterator[bytes]:
    while chunk := member.read(_READ_CHUNK):
        yield chunk


async def restore_backup(engine: AsyncEngine, storage: Storage, source: Path) -> BackupReport:
    """Restore `source` into an empty installation at the same schema revision."""
    manifest = read_manifest(source)
    report = BackupReport(revision=manifest.get("alembic_revision"))

    async with engine.begin() as connection:
        revision = await _revision(connection)
        if revision != manifest.get("alembic_revision"):
            raise BackupError(
                f"This database is at schema revision {revision or 'none'}, the backup at "
                f"{manifest.get('alembic_revision')}. Restore with the CurveVision version "
                f"that wrote it ({manifest.get('curvevision_version')}), after its migrations."
            )
        for table in _tables():
            if (await connection.execute(sa.select(sa.func.count()).select_from(table))).scalar():
                raise BackupError(
                    f"This installation is not empty (table {table.name!r} has rows). A "
                    "restore replaces nothing and merges nothing: restore into a new one."
                )

    # Objects first: rows that name an object storage does not have yet would be served
    # as broken media to anyone who looked in between.
    expected = {entry["key"]: entry for entry in manifest.get("objects", [])}
    with tarfile.open(source, "r") as archive:
        for member in archive:
            if not member.isfile() or not member.name.startswith("storage/"):
                continue
            key = member.name.removeprefix("storage/")
            entry = expected.get(key)
            if entry is None:
                raise BackupError(f"The backup holds {key!r}, which its manifest does not list")
            handle = archive.extractfile(member)
            if handle is None:
                continue
            digest = hashlib.sha256()

            def hashed(stream: Iterator[bytes], digest: Any = digest) -> Iterator[bytes]:
                for chunk in stream:
                    digest.update(chunk)
                    yield chunk

            await storage.put(key, hashed(_chunks(handle)))  # type: ignore[arg-type]
            if digest.hexdigest() != entry["sha256"]:
                with contextlib.suppress(Exception):
                    await storage.delete(key)
                raise BackupError(f"{key!r} in the backup is damaged: its checksum does not match")
            report.objects += 1
            report.object_bytes += member.size
        if report.objects != len(expected):
            raise BackupError(
                f"The backup lists {len(expected)} objects and holds {report.objects}"
            )

        async with engine.begin() as connection:
            for table in _tables():
                rows = _parents_first(table, list(_rows(archive, table)))
                for start in range(0, len(rows), _BATCH):
                    await connection.execute(sa.insert(table), rows[start : start + _BATCH])
                report.tables[table.name] = len(rows)
                if len(rows) != int(manifest.get("tables", {}).get(table.name, len(rows))):
                    raise BackupError(f"Table {table.name!r} in the backup is incomplete")

    report.missing = list(manifest.get("missing_objects", []))
    report.in_place_files = int(manifest.get("in_place_files", 0))
    return report


__all__ = [
    "FORMAT",
    "FORMAT_VERSION",
    "BackupError",
    "BackupReport",
    "read_manifest",
    "restore_backup",
    "write_backup",
]
