"""Local filesystem access, for the desktop application only.

A desktop user's images are already on their disk. Making them upload those images into
an application directory before they can annotate is a copy nobody asked for and, for a
folder of any size, a wait nobody should sit through. These routes attach files where
they are.

**Every route here is refused unless the server is running in local mode.** On a shared
instance a path names a file on the *server's* disk, so accepting one from a request would
be arbitrary file disclosure -- a whole class of vulnerability that this module avoids by
not existing outside the one configuration where a path is meaningful. The refusal is a
404: an operator who never enables local mode should not have these endpoints in their
attack surface at all.

Inside local mode, reading any file the user can read is the *feature*. The single
account, the loopback-only bind and the per-launch token are what bound it.
"""

from __future__ import annotations

import asyncio
from pathlib import Path

from fastapi import APIRouter

from curvevision.api.deps import SessionDep, SettingsDep, TaskScopeDep
from curvevision.core.config import Settings
from curvevision.core.errors import NotFoundError, ValidationError
from curvevision.domain.enums import TaskStatus
from curvevision.policy import Action, ResourceType
from curvevision.schemas.task import AssetOut, LocalImportRequest, LocalImportResult
from curvevision.services import media as media_service
from curvevision.services import tasks as task_service

router = APIRouter(tags=["local"])


def _require_local_mode(settings: Settings) -> None:
    if not settings.local_mode:
        raise NotFoundError("Not Found")


@router.post("/tasks/{task_id}/local-import", response_model=LocalImportResult)
async def import_local_path(
    payload: LocalImportRequest,
    scope: TaskScopeDep,
    session: SessionDep,
    settings: SettingsDep,
) -> LocalImportResult:
    """Attach a local file or folder to this task without copying anything.

    A folder is scanned for media by extension, sorted by path so that frame numbers are
    reproducible, and attached in that order. Files that cannot be read or are not media
    are reported in ``skipped`` rather than failing the whole import -- a folder of 3,000
    photographs with one corrupt file should still produce 2,999 annotatable frames.
    """
    _require_local_mode(settings)
    scope.authorize(Action.CREATE, ResourceType.ASSET)

    # Walking a folder of 50,000 files must not block the event loop.
    candidates = await asyncio.to_thread(
        media_service.import_targets, Path(payload.path), settings, recursive=payload.recursive
    )

    created: list[AssetOut] = []
    skipped: list[str] = []
    position = await media_service.next_position(session, scope.task.id)
    for candidate in candidates:
        try:
            asset = await media_service.ingest_local_file(
                session, settings, scope.task, path=candidate, position=position
            )
        except (ValidationError, OSError) as exc:
            # One unreadable file must not cost the other 2,999.
            skipped.append(f"{candidate.name}: {exc}")
            continue
        created.append(AssetOut.model_validate(asset))
        position += 1

    if created:
        await task_service.recount_frames(session, scope.task)
        await task_service.rebuild_jobs(session, scope.task)
        if scope.task.status is TaskStatus.DRAFT and scope.task.frame_count:
            scope.task.status = TaskStatus.READY
    await session.commit()

    return LocalImportResult(
        task_id=scope.task.id,
        imported=created,
        skipped=skipped,
        frame_count=scope.task.frame_count,
    )
