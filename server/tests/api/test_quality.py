"""Quality reports over HTTP.

`tests/services/test_comparison.py` proves the arithmetic. This proves the feature: that a
reviewer can ask for a score and get one, that the score describes the right jobs, that
tracks and shapes are compared on equal terms, and that the two ways a quality number can
be dishonest are both refused.

Three ways a quality number can be dishonest, all refused here:

* scoring frames the ground truth never covered, which inflates the number with unchecked
  work;
* letting an annotator score their own job, which is the same failure as letting them
  accept it;
* letting the annotator read the ground truth, after which the score measures copying.
"""

from __future__ import annotations

import io
from typing import Any

import pytest

from curvevision.domain.enums import Role
from tests.conftest import ApiActor, add_member, register


def png_bytes(width: int = 64, height: int = 48) -> bytes:
    from PIL import Image

    buffer = io.BytesIO()
    Image.new("RGB", (width, height), "#334155").save(buffer, format="PNG")
    return buffer.getvalue()


async def task_with_ground_truth(
    owner: ApiActor, project: dict[str, Any], frames: int = 4, **range_: Any
) -> tuple[dict[str, Any], dict[str, Any], dict[str, Any]]:
    """A task, its annotation job, and a ground-truth job over the same frames."""
    created = await owner.post(
        "/api/v1/tasks",
        json={"project_id": project["id"], "name": "Checked", "media_kind": "image"},
    )
    task = created.json()
    uploaded = await owner.post(
        f"/api/v1/tasks/{task['id']}/assets",
        files=[("files", (f"f{i}.png", png_bytes(), "image/png")) for i in range(frames)],
    )
    assert uploaded.status_code == 201, uploaded.text

    jobs = (await owner.get(f"/api/v1/tasks/{task['id']}/jobs")).json()
    annotation_job = jobs[0]

    truth = await owner.post(f"/api/v1/tasks/{task['id']}/ground-truth", json=range_)
    assert truth.status_code == 201, truth.text

    return task, annotation_job, truth.json()


async def draw(actor: ApiActor, job_id: str, label_id: str, boxes: list[tuple[int, list[float]]]):
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
    return response.json()


async def test_a_perfect_job_scores_one(owner: ApiActor, project: dict[str, Any]) -> None:
    _task, job, truth = await task_with_ground_truth(owner, project)
    label = project["labels"][0]["id"]

    await draw(owner, truth["id"], label, [(0, [0, 0, 10, 10]), (1, [5, 5, 20, 20])])
    await draw(owner, job["id"], label, [(0, [0, 0, 10, 10]), (1, [5, 5, 20, 20])])

    response = await owner.post(f"/api/v1/jobs/{job['id']}/quality", json={})
    assert response.status_code == 200, response.text

    report = response.json()
    assert report["precision"] == 1.0
    assert report["recall"] == 1.0
    assert report["f1"] == 1.0
    assert report["details"]["conflicts"] == []
    assert report["ground_truth_job_id"] == truth["id"]


async def test_a_missed_object_shows_up_as_a_named_conflict(
    owner: ApiActor, project: dict[str, Any]
) -> None:
    _task, job, truth = await task_with_ground_truth(owner, project)
    label = project["labels"][0]["id"]

    await draw(owner, truth["id"], label, [(0, [0, 0, 10, 10]), (0, [50, 50, 60, 60])])
    await draw(owner, job["id"], label, [(0, [0, 0, 10, 10])])

    report = (await owner.post(f"/api/v1/jobs/{job['id']}/quality", json={})).json()

    assert report["recall"] == 0.5
    assert report["precision"] == 1.0
    kinds = [conflict["kind"] for conflict in report["details"]["conflicts"]]
    assert kinds == ["missing"], "a reviewer needs to know it was missed, not just 'wrong'"


async def test_the_threshold_is_honoured_and_recorded(
    owner: ApiActor, project: dict[str, Any]
) -> None:
    _task, job, truth = await task_with_ground_truth(owner, project)
    label = project["labels"][0]["id"]

    await draw(owner, truth["id"], label, [(0, [0, 0, 10, 10])])
    await draw(owner, job["id"], label, [(0, [2, 0, 12, 10])])  # IoU 8/12 = 0.667

    lenient = (
        await owner.post(f"/api/v1/jobs/{job['id']}/quality", json={"iou_threshold": 0.5})
    ).json()
    strict = (
        await owner.post(f"/api/v1/jobs/{job['id']}/quality", json={"iou_threshold": 0.9})
    ).json()

    assert lenient["f1"] == 1.0
    assert strict["f1"] == 0.0
    assert strict["iou_threshold"] == 0.9, "the report must say what it measured"


async def test_a_track_is_compared_against_a_shape_by_position(
    owner: ApiActor, project: dict[str, Any]
) -> None:
    """An annotator using tracks and a reviewer using shapes must score the same.

    Comparing by identity rather than by position would score this at zero, which would
    make the quality feature unusable on exactly the video work it exists for.
    """
    _task, job, truth = await task_with_ground_truth(owner, project)
    label = project["labels"][0]["id"]

    # Ground truth: plain shapes on frames 0 and 2.
    await draw(owner, truth["id"], label, [(0, [0, 0, 10, 10]), (2, [20, 0, 30, 10])])

    # The annotator drew one track with those two positions as keyframes.
    document = (await owner.get(f"/api/v1/jobs/{job['id']}/annotations")).json()
    written = await owner.patch(
        f"/api/v1/jobs/{job['id']}/annotations",
        json={
            "annotation_version": document["annotation_version"],
            "created_tracks": [
                {
                    "label_id": label,
                    "shape_type": "rectangle",
                    "shapes": [
                        {
                            "frame": 0,
                            "shape_type": "rectangle",
                            "points": [0, 0, 10, 10],
                            "rotation": 0.0,
                            "occluded": False,
                            "outside": False,
                            "keyframe": True,
                            "z_order": 0,
                            "attributes": {},
                        },
                        {
                            "frame": 2,
                            "shape_type": "rectangle",
                            "points": [20, 0, 30, 10],
                            "rotation": 0.0,
                            "occluded": False,
                            "outside": False,
                            "keyframe": True,
                            "z_order": 0,
                            "attributes": {},
                        },
                    ],
                }
            ],
        },
    )
    assert written.status_code == 200, written.text

    report = (await owner.post(f"/api/v1/jobs/{job['id']}/quality", json={})).json()

    assert report["details"]["matched"] >= 2, report["details"]
    assert report["recall"] == 1.0, "the track's keyframes match the ground-truth shapes"


async def test_frames_the_ground_truth_never_covered_are_not_scored(
    owner: ApiActor, project: dict[str, Any]
) -> None:
    """The flattering failure, refused.

    A ground-truth job over frames 0-1 is evidence about frames 0-1. Counting the
    annotator's frame-3 work as correct would inflate the score with data nobody checked.
    """
    # A ground truth over frames 0-1 only, which is how a reviewer checks a sample.
    _task, job, truth = await task_with_ground_truth(owner, project, stop_frame=1)
    label = project["labels"][0]["id"]
    assert truth["stop_frame"] == 1

    await draw(owner, truth["id"], label, [(0, [0, 0, 10, 10])])
    # Correct on frame 0; nonsense on frame 3, which the ground truth says nothing about.
    await draw(owner, job["id"], label, [(0, [0, 0, 10, 10]), (3, [0, 0, 5, 5])])

    report = (await owner.post(f"/api/v1/jobs/{job['id']}/quality", json={})).json()

    assert report["details"]["compared_frames"] == 2
    assert report["details"]["extra"] == 0, "frame 3 is outside the checked range"
    assert report["precision"] == 1.0


async def test_a_task_with_no_ground_truth_says_so(
    owner: ApiActor, project: dict[str, Any]
) -> None:
    created = await owner.post(
        "/api/v1/tasks",
        json={"project_id": project["id"], "name": "Unchecked", "media_kind": "image"},
    )
    task = created.json()
    await owner.post(
        f"/api/v1/tasks/{task['id']}/assets",
        files=[("files", ("a.png", png_bytes(), "image/png"))],
    )
    job = (await owner.get(f"/api/v1/tasks/{task['id']}/jobs")).json()[0]

    response = await owner.post(f"/api/v1/jobs/{job['id']}/quality", json={})
    assert response.status_code == 404
    assert "ground-truth" in response.json()["detail"]


async def test_reading_a_report_before_one_exists_is_a_404(
    owner: ApiActor, project: dict[str, Any]
) -> None:
    _task, job, _truth = await task_with_ground_truth(owner, project)
    assert (await owner.get(f"/api/v1/jobs/{job['id']}/quality")).status_code == 404


async def test_recomputing_replaces_rather_than_accumulates(
    owner: ApiActor, project: dict[str, Any]
) -> None:
    """A job has one current score. Two rows invite reading the stale one."""
    task, job, truth = await task_with_ground_truth(owner, project)
    label = project["labels"][0]["id"]
    await draw(owner, truth["id"], label, [(0, [0, 0, 10, 10])])
    await draw(owner, job["id"], label, [(0, [0, 0, 10, 10])])

    await owner.post(f"/api/v1/jobs/{job['id']}/quality", json={})
    await owner.post(f"/api/v1/jobs/{job['id']}/quality", json={})

    listed = (await owner.get(f"/api/v1/tasks/{task['id']}/quality")).json()
    assert len(listed) == 1


async def test_a_ground_truth_job_cannot_be_scored_against_itself(
    owner: ApiActor, project: dict[str, Any]
) -> None:
    _task, _job, truth = await task_with_ground_truth(owner, project)
    response = await owner.post(f"/api/v1/jobs/{truth['id']}/quality", json={})
    assert response.status_code == 422


@pytest.mark.parametrize("threshold", [0.0, 1.5, -0.2])
async def test_a_nonsense_threshold_is_refused(
    owner: ApiActor, project: dict[str, Any], threshold: float
) -> None:
    _task, job, _truth = await task_with_ground_truth(owner, project)
    response = await owner.post(
        f"/api/v1/jobs/{job['id']}/quality", json={"iou_threshold": threshold}
    )
    assert response.status_code == 422


# ----------------------------------------------------- creating the ground truth itself


async def test_a_ground_truth_job_covers_the_whole_task_by_default(
    owner: ApiActor, project: dict[str, Any]
) -> None:
    task, job, truth = await task_with_ground_truth(owner, project, frames=4)

    assert truth["kind"] == "ground_truth"
    assert (truth["start_frame"], truth["stop_frame"]) == (0, 3)
    assert truth["id"] != job["id"]

    listed = (await owner.get(f"/api/v1/tasks/{task['id']}/jobs")).json()
    kinds = sorted(entry["kind"] for entry in listed)
    assert kinds == ["annotation", "ground_truth"]


async def test_a_second_ground_truth_job_is_refused(
    owner: ApiActor, project: dict[str, Any]
) -> None:
    """Two would mean the score silently depends on which one the query picked."""
    task, _job, _truth = await task_with_ground_truth(owner, project)
    again = await owner.post(f"/api/v1/tasks/{task['id']}/ground-truth", json={})
    assert again.status_code == 409


async def test_a_ground_truth_range_outside_the_task_is_refused(
    owner: ApiActor, project: dict[str, Any]
) -> None:
    created = await owner.post(
        "/api/v1/tasks",
        json={"project_id": project["id"], "name": "Short", "media_kind": "image"},
    )
    task = created.json()
    await owner.post(
        f"/api/v1/tasks/{task['id']}/assets",
        files=[("files", (f"f{i}.png", png_bytes(), "image/png")) for i in range(2)],
    )

    too_far = await owner.post(f"/api/v1/tasks/{task['id']}/ground-truth", json={"stop_frame": 99})
    assert too_far.status_code == 422

    backwards = await owner.post(
        f"/api/v1/tasks/{task['id']}/ground-truth", json={"start_frame": 1, "stop_frame": 0}
    )
    assert backwards.status_code == 422


async def test_a_task_with_no_frames_has_nothing_to_check(
    owner: ApiActor, project: dict[str, Any]
) -> None:
    created = await owner.post(
        "/api/v1/tasks",
        json={"project_id": project["id"], "name": "Empty", "media_kind": "image"},
    )
    response = await owner.post(f"/api/v1/tasks/{created.json()['id']}/ground-truth", json={})
    assert response.status_code == 422


async def test_an_annotator_cannot_declare_the_ground_truth(
    owner: ApiActor, organization: dict[str, Any], project: dict[str, Any]
) -> None:
    """What counts as correct is not the annotator's call on their own work."""
    annotator = await register(owner.client, "annie")
    await add_member(owner, organization["id"], annotator, Role.ANNOTATOR)

    created = await owner.post(
        "/api/v1/tasks",
        json={"project_id": project["id"], "name": "Guarded", "media_kind": "image"},
    )
    task = created.json()
    await owner.post(
        f"/api/v1/tasks/{task['id']}/assets",
        files=[("files", ("a.png", png_bytes(), "image/png"))],
    )

    visible = await annotator.get(f"/api/v1/tasks/{task['id']}")
    assert visible.status_code == 200, "the refusal below must be about the action, not access"

    response = await annotator.post(f"/api/v1/tasks/{task['id']}/ground-truth", json={})
    assert response.status_code == 403


async def test_an_annotator_cannot_read_the_answer_key(
    owner: ApiActor, organization: dict[str, Any], project: dict[str, Any]
) -> None:
    """A score is only evidence if the annotator could not read the ground truth.

    They can see that a ground-truth job exists — its frame range is not a secret — but its
    annotations take reviewer rank. Otherwise the number measures copying.
    """
    annotator = await register(owner.client, "annie")
    await add_member(owner, organization["id"], annotator, Role.ANNOTATOR)
    _task, job, truth = await task_with_ground_truth(owner, project)
    label = project["labels"][0]["id"]
    await draw(owner, truth["id"], label, [(0, [0, 0, 10, 10])])

    assert (await annotator.get(f"/api/v1/jobs/{truth['id']}")).status_code == 200

    hidden = await annotator.get(f"/api/v1/jobs/{truth['id']}/annotations")
    assert hidden.status_code == 403

    written = await annotator.patch(
        f"/api/v1/jobs/{truth['id']}/annotations",
        json={"annotation_version": 1, "created_shapes": []},
    )
    assert written.status_code == 403

    # Their own job is unaffected.
    assert (await annotator.get(f"/api/v1/jobs/{job['id']}/annotations")).status_code == 200


async def test_the_ground_truth_annotator_can_see_their_own_job(
    owner: ApiActor, organization: dict[str, Any], project: dict[str, Any]
) -> None:
    """Restricting the answer key must not make it unannotatable."""
    annotator = await register(owner.client, "gt-annie")
    await add_member(owner, organization["id"], annotator, Role.ANNOTATOR)
    task, _job, _truth = await task_with_ground_truth(owner, project)

    # Reuse the task by assigning its ground truth to the annotator.
    listed = (await owner.get(f"/api/v1/tasks/{task['id']}/jobs")).json()
    truth_id = next(entry["id"] for entry in listed if entry["kind"] == "ground_truth")
    assigned = await owner.patch(f"/api/v1/jobs/{truth_id}", json={"assignee_id": annotator.id})
    assert assigned.status_code == 200, assigned.text

    assert (await annotator.get(f"/api/v1/jobs/{truth_id}/annotations")).status_code == 200
    label = project["labels"][0]["id"]
    await draw(annotator, truth_id, label, [(0, [0, 0, 10, 10])])
