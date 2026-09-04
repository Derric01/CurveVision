"""Annotation-scale benchmarks.

Excluded from the default run (`-m benchmark`), because they are measurements rather than
assertions. They exist so that a performance claim in the docs can be checked rather than
believed:

    pytest -m benchmark -s

Each benchmark prints wall-clock timings for 1k / 10k / 100k annotations. The assertions
are deliberately loose ceilings -- they catch an order-of-magnitude regression without
turning a slow CI runner into a red build.
"""

from __future__ import annotations

import time
import uuid
from typing import Any

import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from curvevision.domain.annotation import Shape, Track, TrackShape
from curvevision.domain.enums import MediaKind, ShapeType
from curvevision.domain.identity import Organization, User
from curvevision.domain.project import Label, Project
from curvevision.domain.task import Job, Task
from curvevision.services.annotations import load_annotations
from curvevision.services.interpolation import Keyframe, interpolate_track

pytestmark = pytest.mark.benchmark

SCALES = (1_000, 10_000, 100_000)


async def _world(session: AsyncSession, frames: int) -> tuple[Job, Label]:
    user = User(
        email=f"bench{uuid.uuid4().hex[:6]}@example.com",
        username=f"bench{uuid.uuid4().hex[:6]}",
        password_hash="x",
    )
    organization = Organization(slug=f"org{uuid.uuid4().hex[:6]}", name="Bench")
    session.add_all([user, organization])
    await session.flush()

    project = Project(
        organization_id=organization.id, slug=f"p{uuid.uuid4().hex[:6]}", name="Bench"
    )
    session.add(project)
    await session.flush()

    label = Label(project_id=project.id, name="object")
    task = Task(project_id=project.id, name="Bench", media_kind=MediaKind.IMAGE, frame_count=frames)
    session.add_all([label, task])
    await session.flush()

    job = Job(task_id=task.id, index=0, start_frame=0, stop_frame=max(0, frames - 1))
    session.add(job)
    await session.flush()
    return job, label


def _report(label: str, count: int, seconds: float) -> None:
    rate = count / seconds if seconds else float("inf")
    print(f"  {label:<28} {count:>7,} in {seconds:7.3f}s  ({rate:>10,.0f}/s)")


@pytest.mark.parametrize("count", SCALES)
async def test_bulk_shape_write(
    sessionmaker_: async_sessionmaker[AsyncSession], count: int
) -> None:
    """How fast can a job take on a large annotation set?

    Uses a bulk insert rather than the batch API, so this measures the storage layer
    rather than validation. The API path is covered by the functional tests.
    """
    async with sessionmaker_() as session:
        job, label = await _world(session, frames=max(1, count // 10))

        rows = [
            {
                "id": uuid.uuid4(),
                "job_id": job.id,
                "label_id": label.id,
                "frame": index % max(1, job.stop_frame + 1),
                "shape_type": ShapeType.RECTANGLE,
                "points": [index % 900, index % 700, index % 900 + 40, index % 700 + 30],
                "rotation": 0.0,
                "occluded": False,
                "outside": False,
                "z_order": 0,
                "attributes": {},
                "elements": [],
                "source": "manual",
            }
            for index in range(count)
        ]

        started = time.perf_counter()
        await session.run_sync(lambda sync: sync.bulk_insert_mappings(Shape, rows))
        await session.commit()
        elapsed = time.perf_counter() - started
        _report("write shapes", count, elapsed)

        started = time.perf_counter()
        loaded = await load_annotations(session, job)
        read_elapsed = time.perf_counter() - started
        _report("read shapes", len(loaded["shapes"]), read_elapsed)

        assert len(loaded["shapes"]) == count
        # Loose ceilings: an order-of-magnitude regression fails, a slow runner does not.
        assert elapsed < max(5.0, count / 2_000)
        assert read_elapsed < max(10.0, count / 2_000)


@pytest.mark.parametrize("count", (1_000, 10_000))
async def test_track_read_with_keyframes(
    sessionmaker_: async_sessionmaker[AsyncSession], count: int
) -> None:
    """Reading a job whose annotations are tracks rather than flat shapes."""
    async with sessionmaker_() as session:
        job, label = await _world(session, frames=count)

        track = Track(job_id=job.id, label_id=label.id, shape_type=ShapeType.RECTANGLE)
        session.add(track)
        await session.flush()

        rows = [
            {
                "id": uuid.uuid4(),
                "track_id": track.id,
                "frame": frame,
                "points": [frame % 900, frame % 700, frame % 900 + 30, frame % 700 + 20],
                "rotation": 0.0,
                "occluded": False,
                "outside": False,
                "keyframe": True,
                "z_order": 0,
                "attributes": {},
                "elements": [],
            }
            for frame in range(count)
        ]
        await session.run_sync(lambda sync: sync.bulk_insert_mappings(TrackShape, rows))
        await session.commit()

        started = time.perf_counter()
        loaded = await load_annotations(session, job)
        elapsed = time.perf_counter() - started
        _report("read track keyframes", count, elapsed)

        assert len(loaded["tracks"][0].shapes) == count
        assert elapsed < max(10.0, count / 1_000)


@pytest.mark.parametrize("keyframes", (10, 1_000, 10_000))
def test_interpolation_throughput(keyframes: int) -> None:
    """Interpolation is pure CPU and runs on every seek, so its cost matters."""
    frames = [
        Keyframe(frame=index * 10, points=[index, index, index + 20, index + 15])
        for index in range(keyframes)
    ]
    probes = min(10_000, keyframes * 10)

    started = time.perf_counter()
    for frame in range(probes):
        interpolate_track(frames, ShapeType.RECTANGLE, frame)
    elapsed = time.perf_counter() - started
    _report(f"interpolate ({keyframes} kf)", probes, elapsed)


@pytest.mark.parametrize("count", (1_000, 10_000))
async def test_export_throughput(
    sessionmaker_: async_sessionmaker[AsyncSession], count: int
) -> None:
    """Export must stream: cost should scale linearly and not blow up memory."""
    from curvevision.services.datasets import export_dataset

    async with sessionmaker_() as session:
        job, label = await _world(session, frames=max(1, count // 10))

        rows: list[dict[str, Any]] = [
            {
                "id": uuid.uuid4(),
                "job_id": job.id,
                "label_id": label.id,
                "frame": index % max(1, job.stop_frame + 1),
                "shape_type": ShapeType.RECTANGLE,
                "points": [0.0, 0.0, 10.0, 10.0],
                "rotation": 0.0,
                "occluded": False,
                "outside": False,
                "z_order": 0,
                "attributes": {},
                "elements": [],
                "source": "manual",
            }
            for index in range(count)
        ]
        await session.run_sync(lambda sync: sync.bulk_insert_mappings(Shape, rows))
        await session.commit()

        task = await session.get(Task, job.task_id)
        assert task is not None
        project = await session.get(Project, task.project_id)
        assert project is not None

        started = time.perf_counter()
        archive, _warnings = await export_dataset(session, project, format_id="curvevision")
        elapsed = time.perf_counter() - started
        _report("export shapes", count, elapsed)
        assert len(archive) > 0
        assert elapsed < max(20.0, count / 500)
