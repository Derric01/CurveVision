"""End-to-end tests of the two workflows CurveVision must support equally well.

    Manual-first  upload -> label schema -> draw -> review -> export
    AI-assisted   upload -> run model -> accept/correct -> review -> export

Neither may require the other, and this file asserts exactly that.
"""

from __future__ import annotations

import base64
import io
import json
import zipfile
from typing import Any

import httpx
import pytest

from curvevision.domain.enums import Role
from tests.conftest import ApiActor, add_member, register

pytest.importorskip("PIL", reason="image dimensions are needed to exercise the media path")


def png_bytes(width: int = 800, height: int = 600, colour: str = "#334155") -> bytes:
    from PIL import Image

    buffer = io.BytesIO()
    Image.new("RGB", (width, height), colour).save(buffer, format="PNG")
    return buffer.getvalue()


async def upload_frames(actor: ApiActor, task_id: str, count: int = 3) -> list[dict[str, Any]]:
    files = [("files", (f"frame_{i:03d}.png", png_bytes(), "image/png")) for i in range(count)]
    response = await actor.post(f"/api/v1/tasks/{task_id}/assets", files=files)
    assert response.status_code == 201, response.text
    return list(response.json())


async def make_task(
    actor: ApiActor, project: dict[str, Any], *, frames: int = 3, segment_size: int = 0
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    created = await actor.post(
        "/api/v1/tasks",
        json={
            "project_id": project["id"],
            "name": "Batch 1",
            "media_kind": "image",
            "segment_size": segment_size,
        },
    )
    assert created.status_code == 201, created.text
    task = created.json()
    await upload_frames(actor, task["id"], frames)

    jobs = await actor.get(f"/api/v1/tasks/{task['id']}/jobs")
    assert jobs.status_code == 200
    return task, list(jobs.json())


def label_id(project: dict[str, Any], name: str) -> str:
    return next(label["id"] for label in project["labels"] if label["name"] == name)


# ---------------------------------------------------------------- manual-first workflow


class TestManualWorkflow:
    async def test_upload_creates_frames_and_a_job(
        self, owner: ApiActor, project: dict[str, Any]
    ) -> None:
        task, jobs = await make_task(owner, project, frames=3)

        detail = (await owner.get(f"/api/v1/tasks/{task['id']}")).json()
        assert detail["frame_count"] == 3
        assert detail["status"] == "ready"
        assert len(jobs) == 1
        assert (jobs[0]["start_frame"], jobs[0]["stop_frame"]) == (0, 2)

    async def test_segment_size_splits_work_into_parallel_jobs(
        self, owner: ApiActor, project: dict[str, Any]
    ) -> None:
        """The job -- not the task -- is the unit of assignment and review."""
        _task, jobs = await make_task(owner, project, frames=5, segment_size=2)
        assert [(job["start_frame"], job["stop_frame"]) for job in jobs] == [
            (0, 1),
            (2, 3),
            (4, 4),
        ]

    async def test_identical_uploads_are_stored_once(
        self, owner: ApiActor, project: dict[str, Any]
    ) -> None:
        """Content addressing means re-uploading the same bytes is cheap."""
        from sqlalchemy import func, select

        from curvevision.domain.media import MediaBlob

        task, _ = await make_task(owner, project, frames=1)
        same = png_bytes()
        response = await owner.post(
            f"/api/v1/tasks/{task['id']}/assets",
            files=[("files", ("copy.png", same, "image/png"))],
        )
        assert response.status_code == 201

        # frame_000.png and copy.png have identical bytes, so one blob backs both assets.
        from curvevision.core.db import get_sessionmaker

        async with get_sessionmaker()() as session:
            blobs = (
                await session.execute(select(func.count()).select_from(MediaBlob))
            ).scalar_one()
        assert blobs == 1

    async def test_frame_metadata_and_media_are_served(
        self, owner: ApiActor, project: dict[str, Any]
    ) -> None:
        task, _ = await make_task(owner, project, frames=2)

        info = await owner.get(f"/api/v1/tasks/{task['id']}/frames/1")
        assert info.status_code == 200
        assert info.json()["width"] == 800
        assert info.json()["height"] == 600

        data = await owner.get(f"/api/v1/tasks/{task['id']}/frames/1/data")
        assert data.status_code == 200
        assert data.content[:4] == b"\x89PNG"

    async def test_out_of_range_frame_is_a_clean_404(
        self, owner: ApiActor, project: dict[str, Any]
    ) -> None:
        task, _ = await make_task(owner, project, frames=2)
        response = await owner.get(f"/api/v1/tasks/{task['id']}/frames/99")
        assert response.status_code == 404

    async def test_draw_review_and_export(self, owner: ApiActor, project: dict[str, Any]) -> None:
        """The complete manual path, with no model involved at any point."""
        task, jobs = await make_task(owner, project, frames=3)
        job = jobs[0]
        car = label_id(project, "car")

        write = await owner.patch(
            f"/api/v1/jobs/{job['id']}/annotations",
            json={
                "annotation_version": 0,
                "created_shapes": [
                    {
                        "client_id": "tmp-1",
                        "label_id": car,
                        "frame": 0,
                        "shape_type": "rectangle",
                        "points": [10, 20, 110, 220],
                        "attributes": {"colour": "red"},
                    },
                    {
                        "client_id": "tmp-2",
                        "label_id": label_id(project, "pedestrian"),
                        "frame": 1,
                        "shape_type": "polygon",
                        "points": [0, 0, 50, 0, 50, 50],
                    },
                ],
            },
        )
        assert write.status_code == 200, write.text
        result = write.json()
        assert result["created"] == {"shapes": 2}
        assert result["annotation_version"] == 1
        assert set(result["id_map"]) == {"tmp-1", "tmp-2"}

        read = await owner.get(f"/api/v1/jobs/{job['id']}/annotations")
        assert read.status_code == 200
        assert len(read.json()["shapes"]) == 2
        assert read.json()["shapes"][0]["attributes"]["colour"] == "red"

        submitted = await owner.patch(f"/api/v1/jobs/{job['id']}", json={"state": "submitted"})
        assert submitted.status_code == 200
        assert submitted.json()["state"] == "submitted"

        reviewer = await register(owner.client, "reviewer1")
        await add_member(owner, project["organization_id"], reviewer, Role.REVIEWER)
        reviewed = await reviewer.post(
            f"/api/v1/jobs/{job['id']}/review",
            json={"accepted": True, "comment": "Looks right."},
        )
        assert reviewed.status_code == 200
        assert reviewed.json()["state"] == "accepted"

        task_detail = (await owner.get(f"/api/v1/tasks/{task['id']}")).json()
        assert task_detail["status"] == "completed"
        assert task_detail["progress"]["completion"] == 1.0

        export = await owner.post(
            f"/api/v1/projects/{project['id']}/export", json={"format": "coco"}
        )
        assert export.status_code == 200
        archive = zipfile.ZipFile(io.BytesIO(export.content))
        document = json.loads(archive.read("annotations/instances_default.json"))
        assert len(document["annotations"]) == 2
        assert {c["name"] for c in document["categories"]} == {"car", "pedestrian"}


class TestAnnotationEngine:
    async def test_stale_version_is_rejected_rather_than_clobbering(
        self, owner: ApiActor, project: dict[str, Any]
    ) -> None:
        """Two annotators must never silently overwrite each other."""
        _task, jobs = await make_task(owner, project, frames=2)
        job_id = jobs[0]["id"]
        car = label_id(project, "car")

        shape = {
            "label_id": car,
            "frame": 0,
            "shape_type": "rectangle",
            "points": [0, 0, 10, 10],
        }
        first = await owner.patch(
            f"/api/v1/jobs/{job_id}/annotations",
            json={"annotation_version": 0, "created_shapes": [shape]},
        )
        assert first.status_code == 200

        stale = await owner.patch(
            f"/api/v1/jobs/{job_id}/annotations",
            json={"annotation_version": 0, "created_shapes": [shape]},
        )
        assert stale.status_code == 409
        body = stale.json()
        assert body["expected_version"] == 1
        assert body["submitted_version"] == 0

    async def test_tracks_interpolate_between_keyframes(
        self, owner: ApiActor, project: dict[str, Any]
    ) -> None:
        _task, jobs = await make_task(owner, project, frames=11)
        job_id = jobs[0]["id"]

        write = await owner.patch(
            f"/api/v1/jobs/{job_id}/annotations",
            json={
                "annotation_version": 0,
                "created_tracks": [
                    {
                        "label_id": label_id(project, "car"),
                        "shape_type": "rectangle",
                        "object_id": 7,
                        "shapes": [
                            {
                                "frame": 0,
                                "shape_type": "rectangle",
                                "points": [0, 0, 10, 10],
                            },
                            {
                                "frame": 10,
                                "shape_type": "rectangle",
                                "points": [100, 100, 110, 110],
                            },
                        ],
                    }
                ],
            },
        )
        assert write.status_code == 200, write.text

        frame5 = await owner.get(f"/api/v1/jobs/{job_id}/frames/5/annotations")
        assert frame5.status_code == 200
        shapes = frame5.json()["shapes"]
        assert len(shapes) == 1
        assert shapes[0]["points"] == [50, 50, 60, 60]
        assert shapes[0]["source"] == "interpolated"

        frame0 = await owner.get(f"/api/v1/jobs/{job_id}/frames/0/annotations")
        assert frame0.json()["shapes"][0]["source"] == "manual"

    async def test_geometry_is_validated_against_the_shape_type(
        self, owner: ApiActor, project: dict[str, Any]
    ) -> None:
        _task, jobs = await make_task(owner, project, frames=1)
        response = await owner.patch(
            f"/api/v1/jobs/{jobs[0]['id']}/annotations",
            json={
                "created_shapes": [
                    {
                        "label_id": label_id(project, "car"),
                        "frame": 0,
                        "shape_type": "polygon",
                        "points": [0, 0, 1, 1],
                    }
                ]
            },
        )
        assert response.status_code == 422
        assert "at least 3 points" in response.text

    async def test_frames_outside_the_job_are_rejected(
        self, owner: ApiActor, project: dict[str, Any]
    ) -> None:
        _task, jobs = await make_task(owner, project, frames=4, segment_size=2)
        second_job = jobs[1]
        response = await owner.patch(
            f"/api/v1/jobs/{second_job['id']}/annotations",
            json={
                "created_shapes": [
                    {
                        "label_id": label_id(project, "car"),
                        "frame": 0,
                        "shape_type": "rectangle",
                        "points": [0, 0, 1, 1],
                    }
                ]
            },
        )
        assert response.status_code == 422
        assert "outside this job" in response.text

    async def test_unknown_attribute_is_rejected_not_dropped(
        self, owner: ApiActor, project: dict[str, Any]
    ) -> None:
        """Silently discarding an annotator's work because of a typo is worse than an error."""
        _task, jobs = await make_task(owner, project, frames=1)
        response = await owner.patch(
            f"/api/v1/jobs/{jobs[0]['id']}/annotations",
            json={
                "created_shapes": [
                    {
                        "label_id": label_id(project, "car"),
                        "frame": 0,
                        "shape_type": "rectangle",
                        "points": [0, 0, 10, 10],
                        "attributes": {"colours": "red"},
                    }
                ]
            },
        )
        assert response.status_code == 422
        assert "colours" in response.text

    async def test_a_refusal_names_the_entry_it_came_from(
        self, owner: ApiActor, project: dict[str, Any]
    ) -> None:
        """A client must be able to set one bad object aside and save the rest.

        Refused whole, and naming nothing, a batch can only be resent whole — which is how
        one bad object failed every autosave after it.
        """
        _task, jobs = await make_task(owner, project, frames=1)
        job_id = jobs[0]["id"]
        box = {"frame": 0, "shape_type": "rectangle", "points": [0, 0, 10, 10]}
        response = await owner.patch(
            f"/api/v1/jobs/{job_id}/annotations",
            json={
                "annotation_version": 0,
                "created_shapes": [
                    {**box, "label_id": label_id(project, "pedestrian")},
                    {**box, "label_id": label_id(project, "car"), "attributes": {"colour": "x"}},
                ],
            },
        )
        assert response.status_code == 422
        body = response.json()
        assert body["errors"] == [
            {
                "location": ["body", "created_shapes", 1],
                "message": body["detail"],
                "type": "validation_error",
            }
        ]
        assert "colour" in body["detail"]

        # Still one transaction: the good entry before it was not written either.
        document = (await owner.get(f"/api/v1/jobs/{job_id}/annotations")).json()
        assert document["shapes"] == []
        assert document["annotation_version"] == 0

    async def test_a_refused_track_or_missing_shape_names_its_entry_too(
        self, owner: ApiActor, project: dict[str, Any]
    ) -> None:
        _task, jobs = await make_task(owner, project, frames=2)
        job_id = jobs[0]["id"]
        car = label_id(project, "car")
        keyframe = {"frame": 0, "shape_type": "rectangle", "points": [0, 0, 10, 10]}
        seeded = await owner.patch(
            f"/api/v1/jobs/{job_id}/annotations",
            json={
                "annotation_version": 0,
                "created_tracks": [
                    {"label_id": car, "shape_type": "rectangle", "shapes": [keyframe]},
                    {"label_id": car, "shape_type": "rectangle", "shapes": [keyframe]},
                ],
            },
        )
        assert seeded.status_code == 200, seeded.text
        tracks = (await owner.get(f"/api/v1/jobs/{job_id}/annotations")).json()["tracks"]

        def update(track: dict[str, Any], **changes: Any) -> dict[str, Any]:
            return {
                "id": track["id"],
                "label_id": car,
                "shape_type": "rectangle",
                "shapes": [keyframe],
                **changes,
            }

        refused_track = await owner.patch(
            f"/api/v1/jobs/{job_id}/annotations",
            json={
                "annotation_version": 1,
                "updated_tracks": [update(tracks[0]), update(tracks[1], attributes={"size": 1})],
            },
        )
        assert refused_track.status_code == 422
        assert refused_track.json()["errors"][0]["location"] == ["body", "updated_tracks", 1]

        missing = await owner.patch(
            f"/api/v1/jobs/{job_id}/annotations",
            json={
                "annotation_version": 1,
                "updated_shapes": [
                    {**keyframe, "id": tracks[0]["id"], "label_id": car},
                ],
            },
        )
        assert missing.status_code == 404
        assert missing.json()["errors"][0]["location"] == ["body", "updated_shapes", 0]

    async def test_attribute_value_must_be_in_the_allowed_set(
        self, owner: ApiActor, project: dict[str, Any]
    ) -> None:
        _task, jobs = await make_task(owner, project, frames=1)
        response = await owner.patch(
            f"/api/v1/jobs/{jobs[0]['id']}/annotations",
            json={
                "created_shapes": [
                    {
                        "label_id": label_id(project, "car"),
                        "frame": 0,
                        "shape_type": "rectangle",
                        "points": [0, 0, 10, 10],
                        "attributes": {"colour": "chartreuse"},
                    }
                ]
            },
        )
        assert response.status_code == 422

    async def test_update_and_delete_round_trip(
        self, owner: ApiActor, project: dict[str, Any]
    ) -> None:
        _task, jobs = await make_task(owner, project, frames=2)
        job_id = jobs[0]["id"]
        car = label_id(project, "car")

        created = await owner.patch(
            f"/api/v1/jobs/{job_id}/annotations",
            json={
                "created_shapes": [
                    {
                        "client_id": "a",
                        "label_id": car,
                        "frame": 0,
                        "shape_type": "rectangle",
                        "points": [0, 0, 10, 10],
                    }
                ]
            },
        )
        shape_id = created.json()["id_map"]["a"]
        version = created.json()["annotation_version"]

        updated = await owner.patch(
            f"/api/v1/jobs/{job_id}/annotations",
            json={
                "annotation_version": version,
                "updated_shapes": [
                    {
                        "id": shape_id,
                        "label_id": car,
                        "frame": 1,
                        "shape_type": "rectangle",
                        "points": [5, 5, 25, 25],
                        "occluded": True,
                    }
                ],
            },
        )
        assert updated.status_code == 200
        assert updated.json()["updated"] == {"shapes": 1}

        read = await owner.get(f"/api/v1/jobs/{job_id}/annotations")
        shape = read.json()["shapes"][0]
        assert shape["points"] == [5, 5, 25, 25]
        assert shape["frame"] == 1
        assert shape["occluded"] is True

        deleted = await owner.patch(
            f"/api/v1/jobs/{job_id}/annotations",
            json={
                "annotation_version": read.json()["annotation_version"],
                "deleted_shapes": [shape_id],
            },
        )
        assert deleted.json()["deleted"] == {"shapes": 1}
        assert (await owner.get(f"/api/v1/jobs/{job_id}/annotations")).json()["shapes"] == []

    async def test_history_records_every_batch(
        self, owner: ApiActor, project: dict[str, Any]
    ) -> None:
        _task, jobs = await make_task(owner, project, frames=1)
        job_id = jobs[0]["id"]
        for _ in range(3):
            await owner.patch(
                f"/api/v1/jobs/{job_id}/annotations",
                json={
                    "created_shapes": [
                        {
                            "label_id": label_id(project, "car"),
                            "frame": 0,
                            "shape_type": "rectangle",
                            "points": [0, 0, 10, 10],
                        }
                    ]
                },
            )
        history = await owner.get(f"/api/v1/jobs/{job_id}/history")
        assert history.status_code == 200
        assert history.json()["count"] == 3
        assert history.json()["results"][0]["summary"]["created"] == {"shapes": 1}


# ---------------------------------------------------------------- AI-assisted workflow


class FakeInferenceApp:
    """A minimal model server speaking the CurveVision inference contract.

    Its existence is the point of the contract: attaching a model is a URL, not a
    deployment project.
    """

    def __init__(self, shapes: list[dict[str, Any]] | None = None) -> None:
        self.shapes = shapes
        self.received: list[dict[str, Any]] = []

    async def __call__(self, scope: Any, receive: Any, send: Any) -> None:
        body = b""
        while True:
            message = await receive()
            body += message.get("body", b"")
            if not message.get("more_body"):
                break
        request = json.loads(body) if body else {}
        self.received.append(request)

        if self.shapes is not None:
            shapes = self.shapes
        else:
            shapes = [
                {
                    "frame": frame["frame"],
                    "label": "car",
                    "type": "rectangle",
                    "points": [10, 10, 110, 110],
                    "confidence": 0.91,
                }
                for frame in request.get("frames", [])
            ]

        payload = json.dumps({"shapes": shapes, "tags": [], "warnings": []}).encode()
        await send(
            {
                "type": "http.response.start",
                "status": 200,
                "headers": [(b"content-type", b"application/json")],
            }
        )
        await send({"type": "http.response.body", "body": payload})


class TestAiAssistedWorkflow:
    async def test_predictions_land_as_editable_suggestions(
        self,
        owner: ApiActor,
        project: dict[str, Any],
        organization: dict[str, Any],
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        task, jobs = await make_task(owner, project, frames=2)
        job_id = jobs[0]["id"]
        del task

        fake = FakeInferenceApp()
        model_client = httpx.AsyncClient(
            transport=httpx.ASGITransport(app=fake), base_url="http://model"
        )
        from curvevision.ml import HttpModelProvider, register_provider

        register_provider(HttpModelProvider(client=model_client))

        registered = await owner.post(
            f"/api/v1/models?organization_id={organization['id']}",
            json={
                "slug": "test-detector",
                "name": "Test Detector",
                "kind": "detector",
                "provider": "http",
                "config": {"endpoint": "http://model/infer"},
                "output_labels": ["car"],
            },
        )
        assert registered.status_code == 201, registered.text
        model = registered.json()

        run = await owner.post(
            f"/api/v1/jobs/{job_id}/inference",
            json={
                "model_id": model["id"],
                "job_id": job_id,
                "frames": [0, 1],
                "label_mapping": {"car": label_id(project, "car")},
                "confidence_threshold": 0.5,
                "persist": True,
            },
        )
        assert run.status_code == 200, run.text
        assert run.json()["created_shapes"] == 2

        read = await owner.get(f"/api/v1/jobs/{job_id}/annotations")
        shapes = read.json()["shapes"]
        assert len(shapes) == 2
        assert all(shape["source"] == "model" for shape in shapes)
        assert all(shape["confidence"] == pytest.approx(0.91) for shape in shapes)

        # A human editing a prediction changes its provenance.
        edited = await owner.patch(
            f"/api/v1/jobs/{job_id}/annotations",
            json={
                "annotation_version": read.json()["annotation_version"],
                "updated_shapes": [
                    {
                        "id": shapes[0]["id"],
                        "label_id": shapes[0]["label_id"],
                        "frame": shapes[0]["frame"],
                        "shape_type": "rectangle",
                        "points": [12, 12, 108, 108],
                    }
                ],
            },
        )
        assert edited.status_code == 200
        after = (await owner.get(f"/api/v1/jobs/{job_id}/annotations")).json()["shapes"]
        corrected = next(s for s in after if s["id"] == shapes[0]["id"])
        assert corrected["source"] == "model_corrected"

        await model_client.aclose()

    async def test_rejecting_a_suggestion_removes_it(
        self,
        owner: ApiActor,
        project: dict[str, Any],
        organization: dict[str, Any],
    ) -> None:
        _task, jobs = await make_task(owner, project, frames=1)
        job_id = jobs[0]["id"]

        fake = FakeInferenceApp()
        model_client = httpx.AsyncClient(
            transport=httpx.ASGITransport(app=fake), base_url="http://model"
        )
        from curvevision.ml import HttpModelProvider, register_provider

        register_provider(HttpModelProvider(client=model_client))

        model = (
            await owner.post(
                f"/api/v1/models?organization_id={organization['id']}",
                json={
                    "slug": "reject-detector",
                    "name": "Detector",
                    "kind": "detector",
                    "config": {"endpoint": "http://model/infer"},
                },
            )
        ).json()

        await owner.post(
            f"/api/v1/jobs/{job_id}/inference",
            json={
                "model_id": model["id"],
                "job_id": job_id,
                "label_mapping": {"car": label_id(project, "car")},
            },
        )
        shapes = (await owner.get(f"/api/v1/jobs/{job_id}/annotations")).json()["shapes"]
        assert len(shapes) == 1

        rejected = await owner.post(
            f"/api/v1/jobs/{job_id}/suggestions",
            json={"shape_ids": [shapes[0]["id"]], "accepted": False},
        )
        assert rejected.status_code == 200
        assert rejected.json()["shapes"] == 1
        assert (await owner.get(f"/api/v1/jobs/{job_id}/annotations")).json()["shapes"] == []

        await model_client.aclose()

    async def test_unmapped_model_labels_are_dropped_and_reported(
        self,
        owner: ApiActor,
        project: dict[str, Any],
        organization: dict[str, Any],
    ) -> None:
        """Inventing project labels from a model's vocabulary is how label schemas rot."""
        _task, jobs = await make_task(owner, project, frames=1)
        job_id = jobs[0]["id"]

        fake = FakeInferenceApp(
            shapes=[
                {
                    "frame": 0,
                    "label": "unicorn",
                    "type": "rectangle",
                    "points": [0, 0, 5, 5],
                    "confidence": 0.99,
                }
            ]
        )
        model_client = httpx.AsyncClient(
            transport=httpx.ASGITransport(app=fake), base_url="http://model"
        )
        from curvevision.ml import HttpModelProvider, register_provider

        register_provider(HttpModelProvider(client=model_client))

        model = (
            await owner.post(
                f"/api/v1/models?organization_id={organization['id']}",
                json={
                    "slug": "unicorn-detector",
                    "name": "Unicorns",
                    "kind": "detector",
                    "config": {"endpoint": "http://model/infer"},
                },
            )
        ).json()

        run = await owner.post(
            f"/api/v1/jobs/{job_id}/inference",
            json={"model_id": model["id"], "job_id": job_id, "label_mapping": {}},
        )
        assert run.status_code == 200
        assert run.json()["created_shapes"] == 0
        assert any("unicorn" in warning for warning in run.json()["warnings"])

        await model_client.aclose()

    async def test_low_confidence_predictions_are_filtered(
        self,
        owner: ApiActor,
        project: dict[str, Any],
        organization: dict[str, Any],
    ) -> None:
        _task, jobs = await make_task(owner, project, frames=1)
        job_id = jobs[0]["id"]

        fake = FakeInferenceApp(
            shapes=[
                {
                    "frame": 0,
                    "label": "car",
                    "type": "rectangle",
                    "points": [0, 0, 5, 5],
                    "confidence": 0.2,
                },
                {
                    "frame": 0,
                    "label": "car",
                    "type": "rectangle",
                    "points": [6, 6, 9, 9],
                    "confidence": 0.95,
                },
            ]
        )
        model_client = httpx.AsyncClient(
            transport=httpx.ASGITransport(app=fake), base_url="http://model"
        )
        from curvevision.ml import HttpModelProvider, register_provider

        register_provider(HttpModelProvider(client=model_client))

        model = (
            await owner.post(
                f"/api/v1/models?organization_id={organization['id']}",
                json={
                    "slug": "noisy-detector",
                    "name": "Noisy",
                    "kind": "detector",
                    "config": {"endpoint": "http://model/infer"},
                },
            )
        ).json()

        run = await owner.post(
            f"/api/v1/jobs/{job_id}/inference",
            json={
                "model_id": model["id"],
                "job_id": job_id,
                "label_mapping": {"car": label_id(project, "car")},
                "confidence_threshold": 0.5,
            },
        )
        assert run.json()["created_shapes"] == 1

        await model_client.aclose()

    async def test_preview_mode_does_not_write_anything(
        self,
        owner: ApiActor,
        project: dict[str, Any],
        organization: dict[str, Any],
    ) -> None:
        _task, jobs = await make_task(owner, project, frames=1)
        job_id = jobs[0]["id"]

        fake = FakeInferenceApp()
        model_client = httpx.AsyncClient(
            transport=httpx.ASGITransport(app=fake), base_url="http://model"
        )
        from curvevision.ml import HttpModelProvider, register_provider

        register_provider(HttpModelProvider(client=model_client))

        model = (
            await owner.post(
                f"/api/v1/models?organization_id={organization['id']}",
                json={
                    "slug": "preview-detector",
                    "name": "Preview",
                    "kind": "detector",
                    "config": {"endpoint": "http://model/infer"},
                },
            )
        ).json()

        run = await owner.post(
            f"/api/v1/jobs/{job_id}/inference",
            json={"model_id": model["id"], "job_id": job_id, "persist": False},
        )
        assert run.status_code == 200
        assert len(run.json()["shapes"]) == 1
        assert (await owner.get(f"/api/v1/jobs/{job_id}/annotations")).json()["shapes"] == []

        await model_client.aclose()

    async def test_the_model_receives_the_frame_image(
        self,
        owner: ApiActor,
        project: dict[str, Any],
        organization: dict[str, Any],
    ) -> None:
        _task, jobs = await make_task(owner, project, frames=1)
        job_id = jobs[0]["id"]

        fake = FakeInferenceApp()
        model_client = httpx.AsyncClient(
            transport=httpx.ASGITransport(app=fake), base_url="http://model"
        )
        from curvevision.ml import HttpModelProvider, register_provider

        register_provider(HttpModelProvider(client=model_client))

        model = (
            await owner.post(
                f"/api/v1/models?organization_id={organization['id']}",
                json={
                    "slug": "echo-detector",
                    "name": "Echo",
                    "kind": "detector",
                    "config": {"endpoint": "http://model/infer"},
                },
            )
        ).json()

        await owner.post(
            f"/api/v1/jobs/{job_id}/inference",
            json={"model_id": model["id"], "job_id": job_id, "persist": False},
        )

        sent = fake.received[-1]["frames"][0]
        assert sent["width"] == 800 and sent["height"] == 600
        assert base64.b64decode(sent["image"])[:4] == b"\x89PNG"

        await model_client.aclose()


class TestAResentCreate:
    """A create resent after its first attempt landed, without the version that would 409.

    The server keeps one client id per job; resending one was an unhandled IntegrityError.
    """

    async def test_is_answered_with_the_shape_it_already_made(
        self, owner: ApiActor, project: dict[str, Any]
    ) -> None:
        _task, jobs = await make_task(owner, project, frames=1)
        job_id = jobs[0]["id"]
        shape = {
            "client_id": "local-1",
            "label_id": label_id(project, "pedestrian"),
            "frame": 0,
            "shape_type": "rectangle",
            "points": [0, 0, 10, 10],
        }
        first = await owner.patch(
            f"/api/v1/jobs/{job_id}/annotations", json={"created_shapes": [shape]}
        )
        assert first.status_code == 200, first.text
        again = await owner.patch(
            f"/api/v1/jobs/{job_id}/annotations", json={"created_shapes": [shape]}
        )
        assert again.status_code == 200, again.text
        assert again.json()["id_map"] == first.json()["id_map"]
        document = (await owner.get(f"/api/v1/jobs/{job_id}/annotations")).json()
        assert len(document["shapes"]) == 1

    async def test_a_different_object_under_the_same_id_is_refused_and_named(
        self, owner: ApiActor, project: dict[str, Any]
    ) -> None:
        _task, jobs = await make_task(owner, project, frames=1)
        job_id = jobs[0]["id"]
        shape = {
            "client_id": "local-1",
            "label_id": label_id(project, "pedestrian"),
            "frame": 0,
            "shape_type": "rectangle",
            "points": [0, 0, 10, 10],
        }
        await owner.patch(f"/api/v1/jobs/{job_id}/annotations", json={"created_shapes": [shape]})
        other = await owner.patch(
            f"/api/v1/jobs/{job_id}/annotations",
            json={"created_shapes": [{**shape, "points": [5, 5, 20, 20]}]},
        )
        assert other.status_code == 422
        assert other.json()["errors"][0]["location"] == ["body", "created_shapes", 0]

    async def test_a_resent_track_is_answered_with_the_track_it_made(
        self, owner: ApiActor, project: dict[str, Any]
    ) -> None:
        _task, jobs = await make_task(owner, project, frames=1)
        job_id = jobs[0]["id"]
        track = {
            "client_id": "local-t",
            "label_id": label_id(project, "pedestrian"),
            "shape_type": "rectangle",
            "shapes": [{"frame": 0, "shape_type": "rectangle", "points": [0, 0, 10, 10]}],
        }
        first = await owner.patch(
            f"/api/v1/jobs/{job_id}/annotations", json={"created_tracks": [track]}
        )
        again = await owner.patch(
            f"/api/v1/jobs/{job_id}/annotations", json={"created_tracks": [track]}
        )
        assert again.status_code == 200, again.text
        assert again.json()["id_map"] == first.json()["id_map"]
        document = (await owner.get(f"/api/v1/jobs/{job_id}/annotations")).json()
        assert len(document["tracks"]) == 1
