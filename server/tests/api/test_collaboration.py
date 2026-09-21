"""Collaboration: membership, assignment, review, issues, and dataset releases."""

from __future__ import annotations

import io
import zipfile
from typing import Any

import pytest

from curvevision.domain.enums import Role
from tests.conftest import ApiActor, add_member, register

pytest.importorskip("PIL", reason="image dimensions are needed to exercise the media path")


def png_bytes(width: int = 640, height: int = 480) -> bytes:
    from PIL import Image

    buffer = io.BytesIO()
    Image.new("RGB", (width, height), "#1e293b").save(buffer, format="PNG")
    return buffer.getvalue()


async def make_task(
    actor: ApiActor, project: dict[str, Any], *, frames: int = 2, segment_size: int = 0
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    task = (
        await actor.post(
            "/api/v1/tasks",
            json={
                "project_id": project["id"],
                "name": "Shared batch",
                "segment_size": segment_size,
            },
        )
    ).json()
    await actor.post(
        f"/api/v1/tasks/{task['id']}/assets",
        files=[("files", (f"f{i}.png", png_bytes(), "image/png")) for i in range(frames)],
    )
    jobs = (await actor.get(f"/api/v1/tasks/{task['id']}/jobs")).json()
    return task, list(jobs)


def label_id(project: dict[str, Any], name: str) -> str:
    return next(label["id"] for label in project["labels"] if label["name"] == name)


class TestMembership:
    async def test_roles_gate_project_creation(
        self, owner: ApiActor, organization: dict[str, Any]
    ) -> None:
        annotator = await register(owner.client, "annie")
        await add_member(owner, organization["id"], annotator, Role.ANNOTATOR)

        response = await annotator.post(
            "/api/v1/projects",
            json={
                "organization_id": organization["id"],
                "slug": "not-allowed",
                "name": "Nope",
            },
        )
        assert response.status_code == 403

        maintainer = await register(owner.client, "morgan")
        await add_member(owner, organization["id"], maintainer, Role.MAINTAINER)
        allowed = await maintainer.post(
            "/api/v1/projects",
            json={
                "organization_id": organization["id"],
                "slug": "allowed",
                "name": "Yes",
            },
        )
        assert allowed.status_code == 201

    async def test_non_members_cannot_learn_a_project_exists(
        self, owner: ApiActor, project: dict[str, Any]
    ) -> None:
        """Reporting 403 rather than 404 would make project existence observable."""
        outsider = await register(owner.client, "outsider")
        response = await outsider.get(f"/api/v1/projects/{project['id']}")
        assert response.status_code == 404

    async def test_project_listing_only_shows_the_callers_organizations(
        self, owner: ApiActor, project: dict[str, Any]
    ) -> None:
        outsider = await register(owner.client, "stranger")
        response = await outsider.get("/api/v1/projects")
        assert response.status_code == 200
        assert response.json()["count"] == 0
        assert response.json()["results"] == []

    async def test_the_last_owner_cannot_be_demoted(
        self, owner: ApiActor, organization: dict[str, Any]
    ) -> None:
        """An organization with no owner is unadministrable."""
        members = (await owner.get(f"/api/v1/organizations/{organization['id']}/members")).json()
        membership = next(m for m in members if m["role"] == "owner")

        response = await owner.patch(
            f"/api/v1/organizations/{organization['id']}/members/{membership['id']}",
            json={"role": "viewer"},
        )
        assert response.status_code == 409
        assert "only owner" in response.text

    async def test_a_second_owner_frees_the_first(
        self, owner: ApiActor, organization: dict[str, Any]
    ) -> None:
        successor = await register(owner.client, "successor")
        await add_member(owner, organization["id"], successor, Role.OWNER)

        members = (await owner.get(f"/api/v1/organizations/{organization['id']}/members")).json()
        first = next(m for m in members if m["user"]["username"] == "founder")

        response = await owner.patch(
            f"/api/v1/organizations/{organization['id']}/members/{first['id']}",
            json={"role": "admin"},
        )
        assert response.status_code == 200


class TestAnnotationPermissions:
    async def test_annotator_may_only_write_on_their_own_job(
        self, owner: ApiActor, project: dict[str, Any], organization: dict[str, Any]
    ) -> None:
        _task, jobs = await make_task(owner, project, frames=4, segment_size=2)
        mine, theirs = jobs[0], jobs[1]

        annotator = await register(owner.client, "worker")
        await add_member(owner, organization["id"], annotator, Role.ANNOTATOR)
        other = await register(owner.client, "otherworker")
        await add_member(owner, organization["id"], other, Role.ANNOTATOR)

        await owner.patch(f"/api/v1/jobs/{mine['id']}", json={"assignee_id": annotator.id})
        await owner.patch(f"/api/v1/jobs/{theirs['id']}", json={"assignee_id": other.id})

        shape = {
            "label_id": label_id(project, "car"),
            "frame": 0,
            "shape_type": "rectangle",
            "points": [0, 0, 10, 10],
        }
        allowed = await annotator.patch(
            f"/api/v1/jobs/{mine['id']}/annotations", json={"created_shapes": [shape]}
        )
        assert allowed.status_code == 200

        denied = await annotator.patch(
            f"/api/v1/jobs/{theirs['id']}/annotations",
            json={"created_shapes": [{**shape, "frame": 2}]},
        )
        assert denied.status_code == 403

    async def test_viewers_can_read_but_never_write(
        self, owner: ApiActor, project: dict[str, Any], organization: dict[str, Any]
    ) -> None:
        _task, jobs = await make_task(owner, project)
        viewer = await register(owner.client, "onlooker")
        await add_member(owner, organization["id"], viewer, Role.VIEWER)

        assert (await viewer.get(f"/api/v1/jobs/{jobs[0]['id']}/annotations")).status_code == 200
        write = await viewer.patch(
            f"/api/v1/jobs/{jobs[0]['id']}/annotations",
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
        assert write.status_code == 403

    async def test_a_locked_job_rejects_writes_from_everyone(
        self, owner: ApiActor, project: dict[str, Any]
    ) -> None:
        """A lock is state, not permission: it holds even for an administrator, and the
        error says so (409) rather than claiming a permission problem (403)."""
        _task, jobs = await make_task(owner, project)
        job_id = jobs[0]["id"]

        assert (
            await owner.patch(f"/api/v1/jobs/{job_id}", json={"locked": True})
        ).status_code == 200

        response = await owner.patch(
            f"/api/v1/jobs/{job_id}/annotations",
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
        assert response.status_code == 409
        assert "locked" in response.text

        cleared = await owner.delete(f"/api/v1/jobs/{job_id}/annotations")
        assert cleared.status_code == 409


class TestJobAssignment:
    """Handing a job to a person, and taking it back.

    Both halves matter to the screen that drives this: a picker that can assign but never
    unassign is a picker with no undo, and one whose response reports the previous holder
    shows the wrong name until something else refetches.
    """

    async def test_assigning_reports_the_new_holder_rather_than_the_previous_one(
        self, owner: ApiActor, project: dict[str, Any], organization: dict[str, Any]
    ) -> None:
        _task, jobs = await make_task(owner, project)
        job_id = jobs[0]["id"]
        first = await register(owner.client, "firsthand")
        second = await register(owner.client, "secondhand")
        for who in (first, second):
            await add_member(owner, organization["id"], who, Role.ANNOTATOR)

        assigned = await owner.patch(f"/api/v1/jobs/{job_id}", json={"assignee_id": first.id})
        assert assigned.status_code == 200
        assert assigned.json()["assignee"]["username"] == "firsthand"

        # The relationship was eagerly loaded when the job was fetched and the sessionmaker
        # is `expire_on_commit=False`, so a response built from the same instance can carry
        # the holder from before the write unless it is refreshed.
        reassigned = await owner.patch(f"/api/v1/jobs/{job_id}", json={"assignee_id": second.id})
        assert reassigned.status_code == 200
        assert reassigned.json()["assignee"]["username"] == "secondhand"
        reread = await owner.get(f"/api/v1/jobs/{job_id}")
        assert reread.json()["assignee"]["username"] == "secondhand"

    async def test_an_explicit_null_unassigns_while_an_omitted_field_leaves_it_alone(
        self, owner: ApiActor, project: dict[str, Any], organization: dict[str, Any]
    ) -> None:
        _task, jobs = await make_task(owner, project)
        job_id = jobs[0]["id"]
        annotator = await register(owner.client, "holder")
        await add_member(owner, organization["id"], annotator, Role.ANNOTATOR)
        await owner.patch(f"/api/v1/jobs/{job_id}", json={"assignee_id": annotator.id})

        # Locking the job says nothing about who holds it.
        untouched = await owner.patch(f"/api/v1/jobs/{job_id}", json={"locked": True})
        assert untouched.json()["assignee"]["username"] == "holder"

        cleared = await owner.patch(f"/api/v1/jobs/{job_id}", json={"assignee_id": None})
        assert cleared.status_code == 200
        assert cleared.json()["assignee"] is None
        assert (await owner.get(f"/api/v1/jobs/{job_id}")).json()["assignee"] is None

    async def test_the_reviewer_is_assigned_and_cleared_the_same_way(
        self, owner: ApiActor, project: dict[str, Any], organization: dict[str, Any]
    ) -> None:
        _task, jobs = await make_task(owner, project)
        job_id = jobs[0]["id"]
        reviewer = await register(owner.client, "checkerup")
        await add_member(owner, organization["id"], reviewer, Role.REVIEWER)

        assigned = await owner.patch(f"/api/v1/jobs/{job_id}", json={"reviewer_id": reviewer.id})
        assert assigned.json()["reviewer"]["username"] == "checkerup"

        cleared = await owner.patch(f"/api/v1/jobs/{job_id}", json={"reviewer_id": None})
        assert cleared.json()["reviewer"] is None

    async def test_assigning_needs_the_rank_for_it(
        self, owner: ApiActor, project: dict[str, Any], organization: dict[str, Any]
    ) -> None:
        """An annotator cannot hand work to somebody else, including to themselves."""
        _task, jobs = await make_task(owner, project)
        job_id = jobs[0]["id"]
        annotator = await register(owner.client, "notamanager")
        await add_member(owner, organization["id"], annotator, Role.ANNOTATOR)

        refused = await annotator.patch(
            f"/api/v1/jobs/{job_id}", json={"assignee_id": annotator.id}
        )
        assert refused.status_code == 403


class TestReviewWorkflow:
    async def test_rejection_sends_work_back_with_an_explanation(
        self, owner: ApiActor, project: dict[str, Any], organization: dict[str, Any]
    ) -> None:
        _task, jobs = await make_task(owner, project)
        job_id = jobs[0]["id"]

        annotator = await register(owner.client, "drafter")
        await add_member(owner, organization["id"], annotator, Role.ANNOTATOR)
        await owner.patch(f"/api/v1/jobs/{job_id}", json={"assignee_id": annotator.id})

        assert (
            await annotator.patch(f"/api/v1/jobs/{job_id}", json={"state": "submitted"})
        ).status_code == 200

        reviewer = await register(owner.client, "checker")
        await add_member(owner, organization["id"], reviewer, Role.REVIEWER)
        rejected = await reviewer.post(
            f"/api/v1/jobs/{job_id}/review",
            json={"accepted": False, "comment": "The second car is missing."},
        )
        assert rejected.status_code == 200
        assert rejected.json()["state"] == "rejected"

        issues = (await annotator.get(f"/api/v1/jobs/{job_id}/issues")).json()
        assert len(issues) == 1
        assert issues[0]["comments"][0]["body"] == "The second car is missing."

        # The annotator can pick the work back up.
        assert (
            await annotator.patch(f"/api/v1/jobs/{job_id}", json={"state": "in_progress"})
        ).status_code == 200

    async def test_an_annotator_cannot_accept_their_own_work(
        self, owner: ApiActor, project: dict[str, Any], organization: dict[str, Any]
    ) -> None:
        _task, jobs = await make_task(owner, project)
        job_id = jobs[0]["id"]

        annotator = await register(owner.client, "selfreviewer")
        await add_member(owner, organization["id"], annotator, Role.REVIEWER)
        await owner.patch(
            f"/api/v1/jobs/{job_id}",
            json={"assignee_id": annotator.id, "reviewer_id": annotator.id},
        )
        await annotator.patch(f"/api/v1/jobs/{job_id}", json={"state": "submitted"})

        response = await annotator.post(f"/api/v1/jobs/{job_id}/review", json={"accepted": True})
        assert response.status_code == 403

    async def test_only_submitted_jobs_can_be_reviewed(
        self, owner: ApiActor, project: dict[str, Any]
    ) -> None:
        _task, jobs = await make_task(owner, project)
        response = await owner.post(f"/api/v1/jobs/{jobs[0]['id']}/review", json={"accepted": True})
        assert response.status_code == 422

    async def test_invalid_state_transitions_are_refused(
        self, owner: ApiActor, project: dict[str, Any]
    ) -> None:
        _task, jobs = await make_task(owner, project)
        response = await owner.patch(f"/api/v1/jobs/{jobs[0]['id']}", json={"state": "accepted"})
        assert response.status_code == 409
        assert "cannot move to" in response.text

    async def test_issue_threads_carry_a_conversation(
        self, owner: ApiActor, project: dict[str, Any], organization: dict[str, Any]
    ) -> None:
        _task, jobs = await make_task(owner, project)
        job_id = jobs[0]["id"]

        annotator = await register(owner.client, "responder")
        await add_member(owner, organization["id"], annotator, Role.ANNOTATOR)

        issue = (
            await owner.post(
                f"/api/v1/jobs/{job_id}/issues",
                json={"frame": 0, "position": [120.0, 80.0], "body": "Box is too tight."},
            )
        ).json()
        assert issue["state"] == "open"
        assert issue["position"] == [120.0, 80.0]

        reply = await annotator.post(
            f"/api/v1/jobs/{job_id}/issues/{issue['id']}/comments",
            json={"body": "Widened it, thanks."},
        )
        assert reply.status_code == 201
        assert reply.json()["author"]["username"] == "responder"

        resolved = await owner.patch(
            f"/api/v1/jobs/{job_id}/issues/{issue['id']}", json={"state": "resolved"}
        )
        assert resolved.status_code == 200
        assert resolved.json()["state"] == "resolved"
        assert resolved.json()["resolved_at"] is not None
        assert len(resolved.json()["comments"]) == 2

        open_only = (
            await owner.get(f"/api/v1/jobs/{job_id}/issues", params={"state": "open"})
        ).json()
        assert open_only == []


class TestDatasetLifecycle:
    async def test_release_freezes_a_version_with_statistics(
        self, owner: ApiActor, project: dict[str, Any]
    ) -> None:
        """Dataset -> Version -> Annotation -> Review -> Release."""
        _task, jobs = await make_task(owner, project, frames=2)
        await owner.patch(
            f"/api/v1/jobs/{jobs[0]['id']}/annotations",
            json={
                "created_shapes": [
                    {
                        "label_id": label_id(project, "car"),
                        "frame": 0,
                        "shape_type": "rectangle",
                        "points": [0, 0, 40, 40],
                    },
                    {
                        "label_id": label_id(project, "pedestrian"),
                        "frame": 0,
                        "shape_type": "rectangle",
                        "points": [50, 50, 70, 90],
                    },
                ]
            },
        )

        version = (
            await owner.post(
                f"/api/v1/projects/{project['id']}/versions",
                json={"name": "v1.0", "description": "First release"},
            )
        ).json()
        assert version["state"] == "draft"

        released = (
            await owner.post(f"/api/v1/projects/{project['id']}/versions/{version['id']}/release")
        ).json()
        assert released["state"] == "released"
        assert released["content_hash"]
        assert released["statistics"]["shape_count"] == 2
        assert released["statistics"]["label_distribution"] == {"car": 1, "pedestrian": 1}
        # Frame 1 has nothing on it -- the most useful number to see before training.
        assert released["statistics"]["frames_without_annotations"] == 1

    async def test_a_released_version_is_immutable(
        self, owner: ApiActor, project: dict[str, Any]
    ) -> None:
        version = (
            await owner.post(f"/api/v1/projects/{project['id']}/versions", json={"name": "frozen"})
        ).json()
        await owner.post(f"/api/v1/projects/{project['id']}/versions/{version['id']}/release")

        deleted = await owner.delete(f"/api/v1/projects/{project['id']}/versions/{version['id']}")
        assert deleted.status_code == 422
        assert "immutable" in deleted.text

        rereleased = await owner.post(
            f"/api/v1/projects/{project['id']}/versions/{version['id']}/release"
        )
        assert rereleased.status_code == 409


class TestImportExport:
    async def test_coco_round_trip_through_the_api(
        self, owner: ApiActor, project: dict[str, Any]
    ) -> None:
        task, jobs = await make_task(owner, project, frames=2)
        await owner.patch(
            f"/api/v1/jobs/{jobs[0]['id']}/annotations",
            json={
                "created_shapes": [
                    {
                        "label_id": label_id(project, "car"),
                        "frame": 0,
                        "shape_type": "rectangle",
                        "points": [10, 20, 110, 220],
                    }
                ]
            },
        )

        export = await owner.post(
            f"/api/v1/projects/{project['id']}/export", json={"format": "coco"}
        )
        assert export.status_code == 200
        archive = export.content

        # Clear the job, then import the archive back.
        assert (await owner.delete(f"/api/v1/jobs/{jobs[0]['id']}/annotations")).status_code == 204

        imported = await owner.post(
            f"/api/v1/tasks/{task['id']}/import?format=coco",
            files={"file": ("export.zip", archive, "application/zip")},
        )
        assert imported.status_code == 200, imported.text
        assert imported.json()["shapes_imported"] == 1

        restored = (await owner.get(f"/api/v1/jobs/{jobs[0]['id']}/annotations")).json()
        assert len(restored["shapes"]) == 1
        assert restored["shapes"][0]["points"] == [10, 20, 110, 220]
        assert restored["shapes"][0]["source"] == "imported"

    async def test_export_warns_about_annotations_the_format_cannot_hold(
        self, owner: ApiActor, project: dict[str, Any]
    ) -> None:
        """A silent drop is how people discover a broken dataset during training."""
        _task, jobs = await make_task(owner, project, frames=1)
        await owner.patch(
            f"/api/v1/jobs/{jobs[0]['id']}/annotations",
            json={
                "created_shapes": [
                    {
                        "label_id": label_id(project, "car"),
                        "frame": 0,
                        "shape_type": "polyline",
                        "points": [0, 0, 10, 10, 20, 5],
                    }
                ]
            },
        )

        export = await owner.post(
            f"/api/v1/projects/{project['id']}/export", json={"format": "coco"}
        )
        assert export.status_code == 200
        assert "polyline" in export.headers["X-CurveVision-Warnings"]

        archive = zipfile.ZipFile(io.BytesIO(export.content))
        assert "curvevision_export_notes.txt" in archive.namelist()

    async def test_replace_policy_clears_existing_annotations(
        self, owner: ApiActor, project: dict[str, Any]
    ) -> None:
        task, jobs = await make_task(owner, project, frames=1)
        shape = {
            "label_id": label_id(project, "car"),
            "frame": 0,
            "shape_type": "rectangle",
            "points": [1, 1, 9, 9],
        }
        await owner.patch(
            f"/api/v1/jobs/{jobs[0]['id']}/annotations", json={"created_shapes": [shape]}
        )
        export = await owner.post(
            f"/api/v1/projects/{project['id']}/export", json={"format": "curvevision"}
        )

        # Add a second shape, then import with replace: only the archive's content survives.
        await owner.patch(
            f"/api/v1/jobs/{jobs[0]['id']}/annotations",
            json={"created_shapes": [{**shape, "points": [20, 20, 30, 30]}]},
        )
        assert (
            len((await owner.get(f"/api/v1/jobs/{jobs[0]['id']}/annotations")).json()["shapes"])
            == 2
        )

        imported = await owner.post(
            f"/api/v1/tasks/{task['id']}/import?format=curvevision&conflict_policy=replace",
            files={"file": ("export.zip", export.content, "application/zip")},
        )
        assert imported.status_code == 200
        restored = (await owner.get(f"/api/v1/jobs/{jobs[0]['id']}/annotations")).json()
        assert len(restored["shapes"]) == 1
        assert restored["shapes"][0]["points"] == [1, 1, 9, 9]

    async def test_unknown_format_lists_what_is_available(
        self, owner: ApiActor, project: dict[str, Any]
    ) -> None:
        response = await owner.post(
            f"/api/v1/projects/{project['id']}/export", json={"format": "parquet"}
        )
        assert response.status_code == 400
        assert "coco" in response.text

    async def test_format_catalogue_declares_capabilities(self, owner: ApiActor) -> None:
        response = await owner.get("/api/v1/formats")
        assert response.status_code == 200
        by_id = {fmt["id"]: fmt for fmt in response.json()}
        assert by_id["coco"]["supports_tracks"] is False
        assert by_id["curvevision"]["supports_tracks"] is True
        assert "rectangle" in by_id["voc"]["shape_types"]


class TestStatistics:
    async def test_project_statistics_report_class_distribution(
        self, owner: ApiActor, project: dict[str, Any]
    ) -> None:
        _task, jobs = await make_task(owner, project, frames=3)
        await owner.patch(
            f"/api/v1/jobs/{jobs[0]['id']}/annotations",
            json={
                "created_shapes": [
                    {
                        "label_id": label_id(project, "car"),
                        "frame": frame,
                        "shape_type": "rectangle",
                        "points": [0, 0, 10, 10],
                    }
                    for frame in (0, 1)
                ]
                + [
                    {
                        "label_id": label_id(project, "pedestrian"),
                        "frame": 0,
                        "shape_type": "rectangle",
                        "points": [20, 20, 30, 30],
                    }
                ]
            },
        )

        stats = (await owner.get(f"/api/v1/projects/{project['id']}/statistics")).json()
        assert stats["shape_count"] == 3
        assert stats["frame_count"] == 3
        assert stats["label_distribution"] == {"car": 2, "pedestrian": 1}
        assert stats["frames_without_annotations"] == 1
        assert stats["jobs_by_state"]["new"] == 1
