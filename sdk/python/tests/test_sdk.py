"""SDK tests, run against a live CurveVision server over real HTTP."""

from __future__ import annotations

import io
import json
import zipfile
from pathlib import Path
from typing import Any

import pytest

from curvevision_sdk import CurveVision, CurveVisionError

pytest.importorskip("PIL")


def write_png(path: Path, width: int = 320, height: int = 240) -> Path:
    from PIL import Image

    Image.new("RGB", (width, height), "#0f172a").save(path, format="PNG")
    return path


class TestConnection:
    def test_health_reports_a_working_server(self, client: CurveVision) -> None:
        health = client.health()
        assert health["status"] == "ok"
        assert health["database"] == "ok"

    def test_whoami_identifies_the_token_holder(self, client: CurveVision) -> None:
        assert client.whoami()["username"].startswith("sdk")

    def test_password_login_yields_a_usable_client(self, server: str) -> None:
        import uuid

        import httpx

        username = f"login{uuid.uuid4().hex[:6]}"
        httpx.post(
            f"{server}/api/v1/auth/register",
            json={
                "email": f"{username}@example.com",
                "username": username,
                "password": "correct-horse-42",
            },
            timeout=30.0,
        ).raise_for_status()

        with CurveVision.login(server, username, "correct-horse-42") as session:
            assert session.whoami()["username"] == username

    def test_api_errors_carry_the_problem_document(self, client: CurveVision) -> None:
        with pytest.raises(CurveVisionError) as excinfo:
            client.create_organization("Not A Slug", "Bad")
        assert excinfo.value.status_code == 422
        assert excinfo.value.errors, "field-level errors should be exposed"

    def test_unauthenticated_client_is_rejected_clearly(self, server: str) -> None:
        with CurveVision(server) as anonymous, pytest.raises(CurveVisionError) as excinfo:
            anonymous.whoami()
        assert excinfo.value.status_code == 401


class TestProjectWorkflow:
    def test_full_manual_workflow(
        self, client: CurveVision, organization: Any, tmp_path: Path
    ) -> None:
        """Create → upload → annotate → export, entirely through the SDK."""
        project = client.create_project(
            organization.id,
            slug="sdk-street",
            name="SDK Street",
            labels=[
                {"name": "car", "color": "#ef4444"},
                {"name": "pedestrian", "color": "#22c55e"},
            ],
        )
        assert project.label_id("car")

        task = client.create_task(project.id, name="SDK batch")
        paths = [write_png(tmp_path / f"frame_{i}.png") for i in range(3)]
        assets = client.upload(task.id, paths)
        assert len(assets) == 3

        refreshed = client.task(task.id)
        assert refreshed.frame_count == 3
        assert refreshed.status == "ready"

        job = client.jobs(task_id=task.id)[0]
        assert (job.start_frame, job.stop_frame) == (0, 2)

        result = client.create_shapes(
            job.id,
            [
                {
                    "label_id": str(project.label_id("car")),
                    "frame": 0,
                    "shape_type": "rectangle",
                    "points": [10, 20, 110, 120],
                },
                {
                    "label_id": str(project.label_id("pedestrian")),
                    "frame": 1,
                    "shape_type": "polygon",
                    "points": [5, 5, 60, 5, 60, 80],
                },
            ],
            annotation_version=job.annotation_version,
        )
        assert result["created"] == {"shapes": 2}

        annotations = client.annotations(job.id)
        assert len(annotations["shapes"]) == 2

        stats = client.project_statistics(project.id)
        assert stats["label_distribution"] == {"car": 1, "pedestrian": 1}

        archive_path = tmp_path / "dataset.zip"
        data = client.export(project.id, format="coco", destination=archive_path)
        assert archive_path.exists()

        with zipfile.ZipFile(io.BytesIO(data)) as archive:
            document = json.loads(archive.read("annotations/instances_default.json"))
        assert len(document["annotations"]) == 2

    def test_label_lookup_reports_what_exists(self, client: CurveVision, organization: Any) -> None:
        project = client.create_project(
            organization.id, slug="sdk-labels", name="Labels", labels=[{"name": "car"}]
        )
        with pytest.raises(KeyError) as excinfo:
            project.label_id("bicycle")
        assert "car" in str(excinfo.value)

    def test_export_round_trips_back_through_import(
        self, client: CurveVision, organization: Any, tmp_path: Path
    ) -> None:
        project = client.create_project(
            organization.id, slug="sdk-round", name="Round", labels=[{"name": "car"}]
        )
        task = client.create_task(project.id, name="Round batch")
        client.upload(task.id, [write_png(tmp_path / "a.png")])
        job = client.jobs(task_id=task.id)[0]

        client.create_shapes(
            job.id,
            [
                {
                    "label_id": str(project.label_id("car")),
                    "frame": 0,
                    "shape_type": "rectangle",
                    "points": [1, 2, 30, 40],
                }
            ],
        )

        archive = client.export(project.id, format="curvevision")
        client.clear_annotations(job.id)
        assert client.annotations(job.id)["shapes"] == []

        result = client.import_annotations(task.id, archive, format="curvevision")
        assert result["shapes_imported"] == 1
        restored = client.annotations(job.id)["shapes"]
        assert restored[0]["points"] == [1, 2, 30, 40]

    def test_export_warning_is_surfaced_not_swallowed(
        self, client: CurveVision, organization: Any, tmp_path: Path
    ) -> None:
        """A silently dropped annotation is discovered during training, which is too late."""
        project = client.create_project(
            organization.id, slug="sdk-warn", name="Warn", labels=[{"name": "lane"}]
        )
        task = client.create_task(project.id, name="Warn batch")
        client.upload(task.id, [write_png(tmp_path / "a.png")])
        job = client.jobs(task_id=task.id)[0]
        client.create_shapes(
            job.id,
            [
                {
                    "label_id": str(project.label_id("lane")),
                    "frame": 0,
                    "shape_type": "polyline",
                    "points": [0, 0, 10, 10, 20, 0],
                }
            ],
        )

        with pytest.warns(UserWarning, match="polyline"):
            client.export(project.id, format="coco")


class TestReviewAndPagination:
    def test_review_transitions_a_job(
        self, client: CurveVision, organization: Any, tmp_path: Path
    ) -> None:
        project = client.create_project(
            organization.id, slug="sdk-review", name="Review", labels=[{"name": "car"}]
        )
        task = client.create_task(project.id, name="Review batch")
        client.upload(task.id, [write_png(tmp_path / "a.png")])
        job = client.jobs(task_id=task.id)[0]

        client.update_job(job.id, state="submitted")
        reviewed = client.review_job(job.id, accepted=True, comment="Good.")
        assert reviewed.state == "accepted"

        progress = client.task_progress(task.id)
        assert progress["completion"] == 1.0

    def test_iter_pages_walks_every_result(self, client: CurveVision, organization: Any) -> None:
        project = client.create_project(organization.id, slug="sdk-pages", name="Pages", labels=[])
        for index in range(7):
            client.create_task(project.id, name=f"Task {index}")

        seen = list(client.iter_pages("/tasks", project_id=str(project.id), limit=2))
        assert len(seen) == 7
        assert len({item["id"] for item in seen}) == 7

    def test_formats_declare_capabilities(self, client: CurveVision) -> None:
        by_id = {fmt["id"]: fmt for fmt in client.formats()}
        assert {"coco", "yolo", "voc", "curvevision"} <= set(by_id)
        assert by_id["curvevision"]["supports_tracks"] is True
