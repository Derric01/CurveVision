"""Registering a model from an endpoint and a key: `POST /models/discover`.

Discovery was in the inference contract from the start — `GET <endpoint>/models`, written
up in `docs/API.md` for model authors to implement — and nothing called it: a model server
that implemented it got nothing for the effort, and registering one meant typing its labels,
kind and model name by hand into a JSON body. The Models page asks the endpoint instead, so
what these pin is what that page shows a person before anything is saved: what the endpoint
serves, or exactly why it could not be asked.
"""

from __future__ import annotations

import json
from typing import Any

import httpx
import pytest

from curvevision.domain.enums import Role
from curvevision.ml import HttpModelProvider, register_provider
from tests.api.test_workflow import label_id, make_task
from tests.conftest import ApiActor, add_member, register

KEY = "s3cret"

POSE = {
    "id": "pose-v2",
    "name": "Pose v2",
    "kind": "detector",
    "labels": ["person"],
    "shape_types": ["skeleton"],
}
DETECTOR = {"id": "yolo-v8n", "name": "YOLOv8n", "labels": ["car", "truck"]}


class ModelServer:
    """A model server that wants a key, and may or may not implement discovery."""

    def __init__(self, *, discovery: Any = None, status: int = 200) -> None:
        self.discovery = {"models": [DETECTOR, POSE]} if discovery is None else discovery
        self.status = status
        self.seen: list[httpx.Request] = []

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.seen.append(request)
        if request.headers.get("authorization") != f"Bearer {KEY}":
            return httpx.Response(401, json={"detail": "bad key"})
        if request.method == "GET" and request.url.path == "/infer/models":
            return httpx.Response(self.status, json=self.discovery)
        if request.method == "POST" and request.url.path == "/infer":
            frames = json.loads(request.content)["frames"]
            shapes = [
                {"frame": f["frame"], "label": "car", "type": "rectangle", "points": [1, 2, 30, 40]}
                for f in frames
            ]
            return httpx.Response(200, json={"shapes": shapes})
        return httpx.Response(404)


def serve(handler: Any) -> None:
    client = httpx.AsyncClient(transport=httpx.MockTransport(handler), base_url="http://model")
    register_provider(HttpModelProvider(client=client))


async def discover(actor: ApiActor, organization: dict[str, Any], **config: Any) -> httpx.Response:
    return await actor.post(
        f"/api/v1/models/discover?organization_id={organization['id']}",
        json={"config": {"endpoint": "http://model/infer", **config}},
    )


class TestDiscovery:
    async def test_an_endpoint_says_what_it_serves(
        self, owner: ApiActor, organization: dict[str, Any]
    ) -> None:
        server = ModelServer()
        serve(server)

        found = await discover(owner, organization, api_key=KEY)

        assert found.status_code == 200, found.text
        assert found.json()["models"] == [
            {
                "id": "yolo-v8n",
                "name": "YOLOv8n",
                "kind": "detector",
                "labels": ["car", "truck"],
                "shape_types": ["rectangle"],
                "open_vocabulary": False,
                "description": None,
            },
            {
                "id": "pose-v2",
                "name": "Pose v2",
                "kind": "detector",
                "labels": ["person"],
                "shape_types": ["skeleton"],
                "open_vocabulary": False,
                "description": None,
            },
        ]
        assert [request.url.path for request in server.seen] == ["/infer/models"]

    async def test_a_refused_key_says_to_check_the_key(
        self, owner: ApiActor, organization: dict[str, Any]
    ) -> None:
        serve(ModelServer())

        refused = await discover(owner, organization, api_key="wrong")

        assert refused.status_code == 502
        assert "401" in refused.text and "API key" in refused.text

    async def test_an_endpoint_without_discovery_is_up_and_lists_nothing(
        self, owner: ApiActor, organization: dict[str, Any]
    ) -> None:
        """Discovery is optional in the contract; its absence is not a broken endpoint."""
        serve(ModelServer(status=404))

        found = await discover(owner, organization, api_key=KEY)

        assert (found.status_code, found.json()) == (200, {"models": []})

    async def test_nothing_listening_is_said_so(
        self, owner: ApiActor, organization: dict[str, Any]
    ) -> None:
        def refuse(request: httpx.Request) -> httpx.Response:
            raise httpx.ConnectError("Connection refused", request=request)

        serve(refuse)

        unreachable = await discover(owner, organization)

        assert unreachable.status_code == 502
        assert "Could not reach" in unreachable.text

    @pytest.mark.parametrize("discovery", [{"model": []}, ["pose"], {"models": "pose"}])
    async def test_an_answer_that_is_not_a_model_list_is_said_so(
        self, owner: ApiActor, organization: dict[str, Any], discovery: Any
    ) -> None:
        serve(ModelServer(discovery=discovery))

        malformed = await discover(owner, organization, api_key=KEY)

        assert malformed.status_code == 502
        assert "model list" in malformed.text

    async def test_an_endpoint_is_required(
        self, owner: ApiActor, organization: dict[str, Any]
    ) -> None:
        missing = await owner.post(
            f"/api/v1/models/discover?organization_id={organization['id']}", json={"config": {}}
        )
        assert missing.status_code == 502
        assert "endpoint" in missing.text

    async def test_asking_needs_the_rank_to_register(
        self, owner: ApiActor, organization: dict[str, Any]
    ) -> None:
        """The same request a registration makes the server send, to an address of the
        caller's choosing: an annotator runs models, and does not point the server at hosts."""
        server = ModelServer()
        serve(server)
        annotator = await register(owner.client, "no-models")
        await add_member(owner, organization["id"], annotator, Role.ANNOTATOR)

        refused = await discover(annotator, organization, api_key=KEY)

        assert refused.status_code == 403
        assert server.seen == []


class TestTheKeyReachesTheModel:
    async def test_a_registered_key_is_sent_with_every_inference_request(
        self, owner: ApiActor, organization: dict[str, Any], project: dict[str, Any]
    ) -> None:
        """Registering with a key and running the model, as the Models page and the editor do.

        The key was already sent — on the client the provider builds — but nothing had
        checked it, and the tests' injected client never carried it at all.
        """
        server = ModelServer()
        serve(server)
        [detector, _pose] = (await discover(owner, organization, api_key=KEY)).json()["models"]
        registered = await owner.post(
            f"/api/v1/models?organization_id={organization['id']}",
            json={
                "slug": "yolo-v8n",
                "name": detector["name"],
                "kind": detector["kind"],
                "config": {"endpoint": "http://model/infer", "api_key": KEY, "model": "yolo-v8n"},
                "output_labels": detector["labels"],
            },
        )
        assert registered.status_code == 201, registered.text
        # The key is the registration's to use, not anybody's to read back.
        assert KEY not in registered.text
        listed = await owner.get(f"/api/v1/models?organization_id={organization['id']}")
        assert KEY not in listed.text

        _task, jobs = await make_task(owner, project, frames=1)
        run = await owner.post(
            f"/api/v1/jobs/{jobs[0]['id']}/inference",
            json={
                "model_id": registered.json()["id"],
                "job_id": jobs[0]["id"],
                "label_mapping": {"car": label_id(project, "car")},
                "persist": True,
            },
        )

        assert run.status_code == 200, run.text
        assert run.json()["created_shapes"] == 1
        [inference] = [request for request in server.seen if request.method == "POST"]
        assert json.loads(inference.content)["model"] == "yolo-v8n"
