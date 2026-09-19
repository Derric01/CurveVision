"""Asking a model to find things by name.

A detector with a fixed head can only find what it was trained on, and `output_labels` is
the whole of that. YOLO-World, Grounding DINO and OWL-ViT are not like that: they take the
class names as text at inference time. Until this landed, `ModelDescriptor.labels` was
documented as "the model's own label space" and `prompts` was specified for interactive
geometry, so there was no way at all to say *find forklifts* unless somebody had first
trained a forklift detector.

The two kinds fail in opposite directions, which is why the difference has to be declared
rather than guessed:

* Sending classes to a **fixed-head** model does nothing. The server drops the key, the
  caller gets an ordinary empty result, and nothing anywhere says why.
* Sending none to an **open-vocabulary** model returns nothing at all, which reads as a
  broken model rather than as an empty prompt.

So one is refused with a message naming what the model *can* find, and the other falls back
to the project's own label schema.
"""

from __future__ import annotations

import json
from typing import Any

import httpx
import pytest

from curvevision.core.errors import ValidationError
from curvevision.services.inference import resolve_classes
from tests.api.test_workflow import FakeInferenceApp, label_id, make_task
from tests.conftest import ApiActor


def resolve(
    requested: list[str],
    *,
    open_vocabulary: bool = True,
    project_labels: list[str] | None = None,
    model_labels: list[str] | None = None,
) -> list[str]:
    return resolve_classes(
        requested,
        open_vocabulary=open_vocabulary,
        project_labels=project_labels if project_labels is not None else ["car", "pedestrian"],
        model_labels=model_labels if model_labels is not None else ["car", "person"],
        model_name="Test Detector",
    )


# ------------------------------------------------------------------ choosing the classes


class TestResolveClasses:
    def test_a_fixed_head_model_asked_for_classes_is_refused_not_ignored(self) -> None:
        """The error has to name what the model *can* find, or it is a dead end."""
        with pytest.raises(ValidationError) as caught:
            resolve(["forklift"], open_vocabulary=False)

        message = str(caught.value)
        assert "forklift" in message, "the refusal must repeat what was asked for"
        assert "car" in message and "person" in message, "and say what is available instead"
        assert "open-vocabulary" in message, "and say what kind of model would work"

    def test_a_fixed_head_model_with_nothing_requested_sends_nothing(self) -> None:
        assert resolve([], open_vocabulary=False) == []

    def test_a_fixed_head_model_that_declared_no_labels_still_explains_itself(self) -> None:
        with pytest.raises(ValidationError, match="nothing it has declared"):
            resolve(["forklift"], open_vocabulary=False, model_labels=[])

    def test_requested_classes_are_taken_as_given(self) -> None:
        assert resolve(["forklift", "pallet"]) == ["forklift", "pallet"]

    # A project schema is a statement of what somebody is looking for. Making them retype
    # it would be friction for nothing.
    def test_an_open_model_with_nothing_requested_falls_back_to_the_project_schema(self) -> None:
        assert resolve([]) == ["car", "pedestrian"]

    # Returning [] here would send an empty prompt, and an open-vocabulary model asked for
    # nothing finds nothing -- which looks exactly like a model that is broken.
    def test_an_open_model_with_no_vocabulary_anywhere_says_so(self) -> None:
        with pytest.raises(ValidationError) as caught:
            resolve([], project_labels=[])
        assert "label schema" in str(caught.value)

    def test_blank_entries_are_dropped_and_names_are_trimmed(self) -> None:
        assert resolve(["  forklift ", "", "   ", "pallet"]) == ["forklift", "pallet"]

    def test_a_request_of_only_blanks_falls_back_rather_than_asking_for_nothing(self) -> None:
        assert resolve(["", "  "]) == ["car", "pedestrian"]

    # Duplicates cost the model work and come back as duplicate boxes over one object.
    def test_duplicates_are_dropped_case_insensitively_keeping_the_first_spelling(self) -> None:
        assert resolve(["Forklift", "forklift", "FORKLIFT", "pallet"]) == ["Forklift", "pallet"]

    # Some servers weight the first prompt more heavily, so the caller's order is theirs.
    def test_order_is_preserved(self) -> None:
        assert resolve(["pallet", "forklift", "crate"]) == ["pallet", "forklift", "crate"]


# ------------------------------------------------------------------- over the wire


async def register(
    owner: ApiActor, organization: dict[str, Any], *, open_vocabulary: bool, slug: str
) -> dict[str, Any]:
    response = await owner.post(
        f"/api/v1/models?organization_id={organization['id']}",
        json={
            "slug": slug,
            "name": "Test Detector",
            "kind": "detector",
            "provider": "http",
            "config": {"endpoint": "http://model/infer"},
            "output_labels": ["car"],
            "open_vocabulary": open_vocabulary,
        },
    )
    assert response.status_code == 201, response.text
    return dict(response.json())


def attach(fake: FakeInferenceApp) -> None:
    from curvevision.ml import HttpModelProvider, register_provider

    register_provider(
        HttpModelProvider(
            client=httpx.AsyncClient(
                transport=httpx.ASGITransport(app=fake), base_url="http://model"
            )
        )
    )


class TestOpenVocabularyOverTheWire:
    async def test_the_flag_survives_registration(
        self, owner: ApiActor, organization: dict[str, Any]
    ) -> None:
        model = await register(owner, organization, open_vocabulary=True, slug="open-one")
        assert model["open_vocabulary"] is True

        listed = await owner.get(f"/api/v1/models?organization_id={organization['id']}")
        assert listed.status_code == 200, listed.text
        found = next(m for m in listed.json() if m["slug"] == "open-one")
        assert found["open_vocabulary"] is True

    async def test_a_model_is_closed_unless_it_says_otherwise(
        self, owner: ApiActor, organization: dict[str, Any]
    ) -> None:
        """The safe default: a prompt sent to a fixed-head server is silently dropped."""
        model = await register(owner, organization, open_vocabulary=False, slug="closed-one")
        assert model["open_vocabulary"] is False

    async def test_the_requested_classes_reach_the_model_server(
        self, owner: ApiActor, project: dict[str, Any], organization: dict[str, Any]
    ) -> None:
        _task, jobs = await make_task(owner, project, frames=1)
        fake = FakeInferenceApp(shapes=[])
        attach(fake)
        model = await register(owner, organization, open_vocabulary=True, slug="open-wire")

        run = await owner.post(
            f"/api/v1/jobs/{jobs[0]['id']}/inference",
            json={
                "model_id": model["id"],
                "job_id": jobs[0]["id"],
                "frames": [0],
                "classes": ["forklift", "hi-vis vest"],
                "persist": False,
            },
        )
        assert run.status_code == 200, run.text
        assert fake.received, "the model server was never called"
        assert fake.received[-1]["classes"] == ["forklift", "hi-vis vest"]

    async def test_an_open_run_with_no_classes_sends_the_project_s_labels(
        self, owner: ApiActor, project: dict[str, Any], organization: dict[str, Any]
    ) -> None:
        """The product decision, asserted on the wire rather than described."""
        _task, jobs = await make_task(owner, project, frames=1)
        fake = FakeInferenceApp(shapes=[])
        attach(fake)
        model = await register(owner, organization, open_vocabulary=True, slug="open-default")

        run = await owner.post(
            f"/api/v1/jobs/{jobs[0]['id']}/inference",
            json={
                "model_id": model["id"],
                "job_id": jobs[0]["id"],
                "frames": [0],
                "persist": False,
            },
        )
        assert run.status_code == 200, run.text
        sent = fake.received[-1]["classes"]
        # The `project` fixture declares `car` and `pedestrian`.
        assert sent == ["car", "pedestrian"], f"sent {sent}"

    async def test_a_fixed_head_model_asked_for_classes_is_refused(
        self, owner: ApiActor, project: dict[str, Any], organization: dict[str, Any]
    ) -> None:
        _task, jobs = await make_task(owner, project, frames=1)
        fake = FakeInferenceApp(shapes=[])
        attach(fake)
        model = await register(owner, organization, open_vocabulary=False, slug="closed-wire")

        run = await owner.post(
            f"/api/v1/jobs/{jobs[0]['id']}/inference",
            json={
                "model_id": model["id"],
                "job_id": jobs[0]["id"],
                "frames": [0],
                "classes": ["forklift"],
                "persist": False,
            },
        )
        assert run.status_code == 422, run.text
        assert "forklift" in run.text
        assert not fake.received, "the model must not be called with a prompt it will drop"

    async def test_a_fixed_head_run_sends_an_empty_class_list(
        self, owner: ApiActor, project: dict[str, Any], organization: dict[str, Any]
    ) -> None:
        """A closed server may ignore the key; it must never see a prompt it cannot honour."""
        _task, jobs = await make_task(owner, project, frames=1)
        fake = FakeInferenceApp(shapes=[])
        attach(fake)
        model = await register(owner, organization, open_vocabulary=False, slug="closed-empty")

        run = await owner.post(
            f"/api/v1/jobs/{jobs[0]['id']}/inference",
            json={
                "model_id": model["id"],
                "job_id": jobs[0]["id"],
                "frames": [0],
                "persist": False,
            },
        )
        assert run.status_code == 200, run.text
        assert fake.received[-1]["classes"] == []

    async def test_a_prediction_for_a_requested_class_lands_as_a_suggestion(
        self, owner: ApiActor, project: dict[str, Any], organization: dict[str, Any]
    ) -> None:
        """The whole loop: ask for a class by name, get an editable annotation back."""
        _task, jobs = await make_task(owner, project, frames=1)
        job_id = jobs[0]["id"]
        fake = FakeInferenceApp(
            shapes=[
                {
                    "frame": 0,
                    "label": "car",
                    "type": "rectangle",
                    "points": [10, 10, 110, 110],
                    "confidence": 0.88,
                }
            ]
        )
        attach(fake)
        model = await register(owner, organization, open_vocabulary=True, slug="open-loop")

        run = await owner.post(
            f"/api/v1/jobs/{job_id}/inference",
            json={
                "model_id": model["id"],
                "job_id": job_id,
                "frames": [0],
                "classes": ["car"],
                "label_mapping": {"car": label_id(project, "car")},
                "persist": True,
            },
        )
        assert run.status_code == 200, run.text
        assert run.json()["created_shapes"] == 1

        read = await owner.get(f"/api/v1/jobs/{job_id}/annotations")
        shapes = read.json()["shapes"]
        assert len(shapes) == 1
        assert shapes[0]["source"] == "model"
        assert shapes[0]["confidence"] == pytest.approx(0.88)

    async def test_the_wire_payload_is_json_serialisable_as_documented(
        self, owner: ApiActor, project: dict[str, Any], organization: dict[str, Any]
    ) -> None:
        """`classes` is a plain list of strings, next to the keys already documented."""
        _task, jobs = await make_task(owner, project, frames=1)
        fake = FakeInferenceApp(shapes=[])
        attach(fake)
        model = await register(owner, organization, open_vocabulary=True, slug="open-shape")

        await owner.post(
            f"/api/v1/jobs/{jobs[0]['id']}/inference",
            json={
                "model_id": model["id"],
                "job_id": jobs[0]["id"],
                "frames": [0],
                "classes": ["forklift"],
                "persist": False,
            },
        )
        body = fake.received[-1]
        assert set(body) >= {"model", "confidence_threshold", "classes", "prompts", "frames"}
        assert json.loads(json.dumps(body["classes"])) == ["forklift"]
