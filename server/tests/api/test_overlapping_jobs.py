"""What happens to annotations on frames that two jobs both cover.

A task with `overlap > 0` hands the same frames to two annotators on purpose: it is how a
track stays continuous across a job seam, because the annotator of job 2 can see where the
object was at the end of job 1. The cost is that the overlap region is annotated **twice**,
and an export that simply concatenates every job's shapes ships each of those objects twice.

Nobody notices. The archive is well-formed, the counts look plausible, and a model trained
on it learns from duplicated boxes with no error anywhere. This file is the guard.
"""

from __future__ import annotations

import io
import json
import zipfile
from typing import Any

from tests.conftest import ApiActor


def png_bytes(width: int = 64, height: int = 48) -> bytes:
    from PIL import Image

    buffer = io.BytesIO()
    Image.new("RGB", (width, height), "#334155").save(buffer, format="PNG")
    return buffer.getvalue()


async def overlapping_task(
    owner: ApiActor,
    project: dict[str, Any],
    *,
    frames: int = 6,
    segment_size: int = 4,
    overlap: int = 2,
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    task = (
        await owner.post(
            "/api/v1/tasks",
            json={
                "project_id": project["id"],
                "name": "Seamed",
                "media_kind": "image",
                "segment_size": segment_size,
                "overlap": overlap,
            },
        )
    ).json()
    uploaded = await owner.post(
        f"/api/v1/tasks/{task['id']}/assets",
        files=[("files", (f"f{i}.png", png_bytes(), "image/png")) for i in range(frames)],
    )
    assert uploaded.status_code == 201, uploaded.text
    jobs = (await owner.get(f"/api/v1/tasks/{task['id']}/jobs")).json()
    return task, list(jobs)


async def draw(
    actor: ApiActor, job_id: str, label_id: str, boxes: list[tuple[int, list[float]]]
) -> dict[str, Any]:
    document = (await actor.get(f"/api/v1/jobs/{job_id}/annotations")).json()
    response = await actor.patch(
        f"/api/v1/jobs/{job_id}/annotations",
        json={
            "annotation_version": document["annotation_version"],
            "created_shapes": [
                {
                    "frame": frame,
                    "label_id": label_id,
                    "shape_type": "rectangle",
                    "points": points,
                    "rotation": 0.0,
                    "occluded": False,
                    "outside": False,
                    "z_order": 0,
                    "attributes": {},
                }
                for frame, points in boxes
            ],
        },
    )
    assert response.status_code == 200, response.text
    return dict(response.json())


def coco_annotations(archive: bytes) -> list[dict[str, Any]]:
    with zipfile.ZipFile(io.BytesIO(archive)) as bundle:
        name = next(n for n in bundle.namelist() if n.endswith(".json"))
        return list(json.loads(bundle.read(name))["annotations"])


async def test_jobs_really_do_overlap(owner: ApiActor, project: dict[str, Any]) -> None:
    """The premise. Without this the tests below could pass by covering nothing."""
    _task, jobs = await overlapping_task(owner, project)

    ranges = sorted((job["start_frame"], job["stop_frame"]) for job in jobs)
    assert len(ranges) >= 2, ranges
    assert ranges[0][1] >= ranges[1][0], "the second job must start inside the first"


async def test_one_object_annotated_in_both_jobs_is_exported_once(
    owner: ApiActor, project: dict[str, Any]
) -> None:
    """The duplicate-on-export bug.

    Frame 3 belongs to job 1 *and* job 2. Both annotators drew the same car, because it is
    the same car. Concatenating the jobs ships it twice, and a dataset with silently doubled
    objects is worse than one that fails to build.
    """
    task, jobs = await overlapping_task(owner, project)
    label = project["labels"][0]["id"]
    first, second = sorted(jobs, key=lambda job: job["start_frame"])[:2]
    shared = max(first["start_frame"], second["start_frame"])
    assert first["start_frame"] <= shared <= first["stop_frame"]

    await draw(owner, first["id"], label, [(shared, [10, 10, 50, 50])])
    await draw(owner, second["id"], label, [(shared, [11, 10, 51, 50])])  # the same car

    export = await owner.post(f"/api/v1/projects/{project['id']}/export", json={"format": "coco"})
    assert export.status_code == 200, export.text

    on_shared = [
        annotation
        for annotation in coco_annotations(export.content)
        # COCO image ids are 1-based over the frames we emitted.
        if annotation["image_id"] == shared + 1
    ]
    assert len(on_shared) == 1, (
        f"the same object was exported {len(on_shared)} times; two annotators covering one "
        "frame is the point of overlap, not two objects"
    )
    assert task["overlap"] == 2


async def test_two_different_objects_on_a_shared_frame_both_survive(
    owner: ApiActor, project: dict[str, Any]
) -> None:
    """The other half. Deduplicating too eagerly loses real work.

    Two boxes far apart on the same frame are two objects, whichever job they came from.
    A merge that collapses them because they share a frame and a label is a data-loss bug
    wearing the costume of a fix.
    """
    _task, jobs = await overlapping_task(owner, project)
    label = project["labels"][0]["id"]
    first, second = sorted(jobs, key=lambda job: job["start_frame"])[:2]
    shared = max(first["start_frame"], second["start_frame"])

    await draw(owner, first["id"], label, [(shared, [0, 0, 20, 20])])
    await draw(owner, second["id"], label, [(shared, [200, 150, 240, 190])])

    export = await owner.post(f"/api/v1/projects/{project['id']}/export", json={"format": "coco"})
    on_shared = [
        annotation
        for annotation in coco_annotations(export.content)
        if annotation["image_id"] == shared + 1
    ]
    assert len(on_shared) == 2, "two objects in different places are two objects"


async def test_different_labels_on_a_shared_frame_are_not_merged(
    owner: ApiActor, project: dict[str, Any]
) -> None:
    """Two annotators disagreeing about the label is a disagreement, not a duplicate."""
    _task, jobs = await overlapping_task(owner, project)
    car = project["labels"][0]["id"]
    pedestrian = project["labels"][1]["id"]
    first, second = sorted(jobs, key=lambda job: job["start_frame"])[:2]
    shared = max(first["start_frame"], second["start_frame"])

    await draw(owner, first["id"], car, [(shared, [10, 10, 50, 50])])
    await draw(owner, second["id"], pedestrian, [(shared, [10, 10, 50, 50])])

    export = await owner.post(f"/api/v1/projects/{project['id']}/export", json={"format": "coco"})
    on_shared = [
        annotation
        for annotation in coco_annotations(export.content)
        if annotation["image_id"] == shared + 1
    ]
    assert len(on_shared) == 2, (
        "collapsing these would silently pick one annotator's label over the other's"
    )


async def track(
    actor: ApiActor, job_id: str, label_id: str, keyframes: list[tuple[int, list[float]]]
) -> dict[str, Any]:
    document = (await actor.get(f"/api/v1/jobs/{job_id}/annotations")).json()
    response = await actor.patch(
        f"/api/v1/jobs/{job_id}/annotations",
        json={
            "annotation_version": document["annotation_version"],
            "created_tracks": [
                {
                    "label_id": label_id,
                    "shape_type": "rectangle",
                    "shapes": [
                        {
                            "frame": frame,
                            "shape_type": "rectangle",
                            "points": points,
                            "rotation": 0.0,
                            "occluded": False,
                            "outside": False,
                            "keyframe": True,
                            "z_order": 0,
                            "attributes": {},
                        }
                        for frame, points in keyframes
                    ],
                }
            ],
        },
    )
    assert response.status_code == 200, response.text
    return dict(response.json())


def exported_document(archive: bytes) -> dict[str, Any]:
    with zipfile.ZipFile(io.BytesIO(archive)) as bundle:
        name = next(n for n in bundle.namelist() if n.endswith(".json"))
        return dict(json.loads(bundle.read(name)))


async def test_two_unrelated_tracks_in_different_jobs_get_different_ids(
    owner: ApiActor, project: dict[str, Any]
) -> None:
    """Track identity has to be unique across the task, not within a job.

    Numbering each job's tracks from zero makes job 1's first car and job 2's first
    pedestrian both `track_id: 0`. An export then claims one object teleported across the
    task, and any consumer that groups by track id silently welds two objects together.
    There is no overlap here at all: this is wrong on a plain segmented task.
    """
    _task, jobs = await overlapping_task(owner, project, frames=8, segment_size=4, overlap=0)
    label = project["labels"][0]["id"]
    first, second = sorted(jobs, key=lambda job: job["start_frame"])[:2]
    assert first["stop_frame"] < second["start_frame"], "no overlap in this fixture"

    await track(owner, first["id"], label, [(0, [0, 0, 20, 20]), (1, [5, 0, 25, 20])])
    await track(
        owner,
        second["id"],
        label,
        [
            (second["start_frame"], [200, 150, 220, 170]),
            (second["start_frame"] + 1, [205, 150, 225, 170]),
        ],
    )

    export = await owner.post(
        f"/api/v1/projects/{project['id']}/export", json={"format": "curvevision"}
    )
    assert export.status_code == 200, export.text
    document = exported_document(export.content)

    ids_by_frame = {
        frame["index"]: {shape.get("track_id") for shape in frame["shapes"]}
        for frame in document["frames"]
        if frame["shapes"]
    }
    early = ids_by_frame.get(0, set())
    late = ids_by_frame.get(second["start_frame"], set())
    assert early and late, ids_by_frame
    assert early.isdisjoint(late), (
        f"two unrelated objects share track ids {early & late}; they are in different jobs "
        "and were never the same object"
    )


async def test_a_track_crossing_the_seam_keeps_one_identity(
    owner: ApiActor, project: dict[str, Any]
) -> None:
    """What the overlap setting is *for*.

    A job seam exists so that a track can continue across it: the second annotator sees
    where the object was at the end of job 1 and carries on. If the export then emits the
    two halves under different track ids, the object appears to vanish and be replaced by a
    stranger — which is exactly the discontinuity the overlap was bought to prevent.
    """
    _task, jobs = await overlapping_task(owner, project, frames=8, segment_size=5, overlap=2)
    label = project["labels"][0]["id"]
    first, second = sorted(jobs, key=lambda job: job["start_frame"])[:2]
    seam = list(range(second["start_frame"], first["stop_frame"] + 1))
    assert len(seam) >= 2, seam

    # The same car, tracked by two people across the frames they share.
    await track(
        owner, first["id"], label, [(0, [0, 0, 40, 40]), (first["stop_frame"], [60, 0, 100, 40])]
    )
    await track(
        owner,
        second["id"],
        label,
        [(second["start_frame"], [40, 0, 80, 40]), (second["stop_frame"], [120, 0, 160, 40])],
    )

    export = await owner.post(
        f"/api/v1/projects/{project['id']}/export", json={"format": "curvevision"}
    )
    assert export.status_code == 200, export.text
    document = exported_document(export.content)

    # The property is about the *whole* car, not only the seam: the seam frames alone carry
    # one id trivially once the duplicate is dropped, so asserting on them would check
    # nothing. What matters is that the id before the seam is the id after it.
    ids_by_frame = {
        frame["index"]: [shape.get("track_id") for shape in frame["shapes"]]
        for frame in document["frames"]
        if frame["shapes"]
    }
    assert set(ids_by_frame) == set(range(8)), (
        f"the car should appear on every frame; it appears on {sorted(ids_by_frame)}"
    )

    every_id = {track_id for ids in ids_by_frame.values() for track_id in ids}
    assert len(every_id) == 1, (
        f"one car crossing a job boundary was exported under ids {every_id}; before the "
        f"seam {ids_by_frame[0]}, after it {ids_by_frame[7]} — the export says the car was "
        "replaced by a stranger, which is the discontinuity the overlap was bought to prevent"
    )
    assert all(len(ids) == 1 for ids in ids_by_frame.values()), (
        f"a frame carries the car twice: {ids_by_frame}"
    )


async def test_a_frame_covered_by_four_jobs_still_exports_one_object(
    owner: ApiActor, project: dict[str, Any]
) -> None:
    """More than two jobs can share a frame, and the merge must not stop at pairs.

    `segment_size=4, overlap=3` gives ranges 0-3, 1-4, 2-5, …, so frame 3 belongs to four
    jobs. Folding one job in at a time handles it by construction — but "by construction" is
    what people say right before shipping an off-by-one, so it is asserted.
    """
    _task, jobs = await overlapping_task(owner, project, frames=10, segment_size=4, overlap=3)
    label = project["labels"][0]["id"]
    covering = [job for job in jobs if job["start_frame"] <= 3 <= job["stop_frame"]]
    assert len(covering) == 4, [(j["start_frame"], j["stop_frame"]) for j in jobs]

    # Four annotators, the same car, drawn slightly differently by each.
    for offset, job in enumerate(covering):
        await draw(owner, job["id"], label, [(3, [10 + offset, 10, 50 + offset, 50])])

    export = await owner.post(f"/api/v1/projects/{project['id']}/export", json={"format": "coco"})
    assert export.status_code == 200, export.text

    on_shared = [a for a in coco_annotations(export.content) if a["image_id"] == 4]
    assert len(on_shared) == 1, (
        f"four annotators saw one car and the export has {len(on_shared)} of them"
    )


async def test_a_task_without_overlap_is_untouched(
    owner: ApiActor, project: dict[str, Any]
) -> None:
    """The common case must not pay for the rare one."""
    _task, jobs = await overlapping_task(owner, project, segment_size=0, overlap=0)
    label = project["labels"][0]["id"]
    assert len(jobs) == 1

    await draw(owner, jobs[0]["id"], label, [(0, [0, 0, 10, 10]), (0, [1, 1, 11, 11])])

    export = await owner.post(f"/api/v1/projects/{project['id']}/export", json={"format": "coco"})
    on_first = [a for a in coco_annotations(export.content) if a["image_id"] == 1]
    assert len(on_first) == 2, (
        "two nearly-identical boxes inside ONE job are the annotator's business; "
        "merging only ever reconciles the seam between jobs"
    )
