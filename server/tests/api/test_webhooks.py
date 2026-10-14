"""Webhooks through the API: what is sent, to whom, and what an integrator can see of it.

Until iteration 96 nothing emitted an event. Signing, retries and backoff were built and
tested (`tests/services/test_webhooks.py`), the `webhook.dispatch` job existed, and no code
queued it — so a webhook registered through the API received nothing, ever. These drive the
real routes and read what a receiver gets, signature included.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import uuid
from typing import Any

import httpx
import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from curvevision.domain.enums import Role
from curvevision.domain.system import BackgroundTask, Webhook
from curvevision.services import webhooks as webhook_service
from tests.api.test_workflow import label_id, make_task
from tests.conftest import ApiActor, add_member, register

EVERY_EVENT = [
    "project.created",
    "project.updated",
    "task.created",
    "task.updated",
    "task.completed",
    "job.updated",
    "job.submitted",
    "job.accepted",
    "job.rejected",
    "annotations.updated",
    "issue.created",
    "dataset.released",
]


class Receiver:
    """An integrator's endpoint: records what arrives, and answers with `status`."""

    def __init__(self, status: int = 200) -> None:
        self.status = status
        self.received: list[httpx.Request] = []

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.received.append(request)
        return httpx.Response(self.status, text="" if self.status < 400 else "receiver says no")

    def events(self) -> list[str]:
        return [request.headers["x-curvevision-event"] for request in self.received]

    def bodies(self, event: str) -> list[dict[str, Any]]:
        return [
            json.loads(request.content)
            for request in self.received
            if request.headers["x-curvevision-event"] == event
        ]


@pytest.fixture
def receiver(monkeypatch: pytest.MonkeyPatch) -> Receiver:
    hook = Receiver()
    monkeypatch.setattr(webhook_service, "transport", httpx.MockTransport(hook))
    return hook


async def subscribe(
    actor: ApiActor,
    organization_id: str,
    events: list[str],
    *,
    project_id: str | None = None,
    url: str = "https://hooks.example.com/curvevision",
) -> dict[str, Any]:
    response = await actor.post(
        f"/api/v1/webhooks?organization_id={organization_id}",
        json={"target_url": url, "events": events, "project_id": project_id},
    )
    assert response.status_code == 201, response.text
    return dict(response.json())


def signed_by(request: httpx.Request, secret: str) -> bool:
    expected = "sha256=" + hmac.new(secret.encode(), request.content, hashlib.sha256).hexdigest()
    return hmac.compare_digest(request.headers["x-curvevision-signature"], expected)


class TestEventsAreSent:
    async def test_every_event_reaches_a_subscriber_signed(
        self,
        owner: ApiActor,
        organization: dict[str, Any],
        receiver: Receiver,
    ) -> None:
        hook = await subscribe(owner, organization["id"], EVERY_EVENT)

        project = (
            await owner.post(
                "/api/v1/projects",
                json={
                    "organization_id": organization["id"],
                    "slug": "hooks",
                    "name": "Hooks",
                    "labels": [{"name": "car"}],
                },
            )
        ).json()
        await owner.patch(f"/api/v1/projects/{project['id']}", json={"name": "Hooks, renamed"})
        task, jobs = await make_task(owner, project, frames=2)
        await owner.patch(f"/api/v1/tasks/{task['id']}", json={"description": "Two frames"})
        job = jobs[0]
        await owner.patch(f"/api/v1/jobs/{job['id']}", json={"assignee_id": owner.id})
        written = await owner.patch(
            f"/api/v1/jobs/{job['id']}/annotations",
            json={
                "annotation_version": 0,
                "created_shapes": [
                    {
                        "label_id": label_id(project, "car"),
                        "frame": 0,
                        "shape_type": "rectangle",
                        "points": [0, 0, 40, 40],
                    }
                ],
            },
        )
        assert written.status_code == 200, written.text
        issue = await owner.post(f"/api/v1/jobs/{job['id']}/issues", json={"body": "Check this"})
        assert issue.status_code == 201, issue.text
        await owner.patch(f"/api/v1/jobs/{job['id']}", json={"state": "submitted"})
        rejected = await owner.post(f"/api/v1/jobs/{job['id']}/review", json={"accepted": False})
        assert rejected.json()["state"] == "rejected"
        await owner.patch(f"/api/v1/jobs/{job['id']}", json={"state": "submitted"})
        await owner.post(f"/api/v1/jobs/{job['id']}/review", json={"accepted": True})
        version = (
            await owner.post(f"/api/v1/projects/{project['id']}/versions", json={"name": "v1"})
        ).json()
        await owner.post(f"/api/v1/projects/{project['id']}/versions/{version['id']}/release")

        assert set(receiver.events()) == set(EVERY_EVENT)
        assert all(signed_by(request, hook["secret"]) for request in receiver.received)

        # Each carries the resource as the API returns it, and a delivery id to deduplicate on.
        (created,) = receiver.bodies("project.created")
        assert created["event"] == "project.created"
        assert created["data"]["project"]["id"] == project["id"]
        assert created["delivery_id"]
        (renamed,) = receiver.bodies("project.updated")
        assert renamed["data"]["project"]["name"] == "Hooks, renamed"
        (completed,) = receiver.bodies("task.completed")
        assert completed["data"]["task"]["id"] == task["id"]
        assert completed["data"]["task"]["status"] == "completed"
        assert [body["data"]["job"]["state"] for body in receiver.bodies("job.accepted")] == [
            "accepted"
        ]
        (annotated,) = receiver.bodies("annotations.updated")
        assert annotated["data"]["job"]["id"] == job["id"]
        assert annotated["data"]["annotation_version"] == 1
        (opened,) = receiver.bodies("issue.created")
        assert opened["data"]["issue"]["job_id"] == job["id"]
        (released,) = receiver.bodies("dataset.released")
        assert released["data"]["version"]["state"] == "released"

    async def test_only_the_events_subscribed_to(
        self,
        owner: ApiActor,
        organization: dict[str, Any],
        project: dict[str, Any],
        receiver: Receiver,
    ) -> None:
        await subscribe(owner, organization["id"], ["task.created"])
        await make_task(owner, project, frames=1)
        await owner.patch(f"/api/v1/projects/{project['id']}", json={"name": "Renamed"})
        assert receiver.events() == ["task.created"]

    async def test_a_project_s_webhook_hears_only_that_project(
        self,
        owner: ApiActor,
        organization: dict[str, Any],
        project: dict[str, Any],
        receiver: Receiver,
    ) -> None:
        other = (
            await owner.post(
                "/api/v1/projects",
                json={"organization_id": organization["id"], "slug": "other", "name": "Other"},
            )
        ).json()
        await subscribe(owner, organization["id"], ["project.updated"], project_id=project["id"])
        await owner.patch(f"/api/v1/projects/{other['id']}", json={"name": "Other, renamed"})
        await owner.patch(f"/api/v1/projects/{project['id']}", json={"name": "This one"})
        assert [body["data"]["project"]["id"] for body in receiver.bodies("project.updated")] == [
            project["id"]
        ]

    async def test_a_paused_webhook_hears_nothing(
        self,
        owner: ApiActor,
        organization: dict[str, Any],
        project: dict[str, Any],
        receiver: Receiver,
    ) -> None:
        hook = await subscribe(owner, organization["id"], ["project.updated"])
        paused = await owner.patch(f"/api/v1/webhooks/{hook['id']}", json={"is_active": False})
        assert paused.json()["is_active"] is False
        await owner.patch(f"/api/v1/projects/{project['id']}", json={"name": "Quietly"})
        assert receiver.received == []

    async def test_nothing_is_queued_when_nobody_listens(
        self,
        owner: ApiActor,
        organization: dict[str, Any],
        project: dict[str, Any],
        receiver: Receiver,
        session: AsyncSession,
    ) -> None:
        """Every write would otherwise queue a job that finds no webhook and does nothing."""

        async def dispatches() -> int:
            rows = await session.execute(
                select(BackgroundTask).where(BackgroundTask.kind == "webhook.dispatch")
            )
            return len(rows.scalars().all())

        await owner.patch(f"/api/v1/projects/{project['id']}", json={"name": "Unheard"})
        assert await dispatches() == 0
        await subscribe(owner, organization["id"], ["task.created"])  # not this event
        await owner.patch(f"/api/v1/projects/{project['id']}", json={"name": "Still unheard"})
        assert await dispatches() == 0
        assert receiver.received == []

    async def test_a_receiver_that_fails_does_not_fail_the_change(
        self,
        owner: ApiActor,
        organization: dict[str, Any],
        project: dict[str, Any],
        receiver: Receiver,
    ) -> None:
        receiver.status = 500
        await subscribe(owner, organization["id"], ["project.updated"])
        renamed = await owner.patch(f"/api/v1/projects/{project['id']}", json={"name": "Still"})
        assert renamed.status_code == 200
        assert renamed.json()["name"] == "Still"


class TestAnotherWorkspace:
    async def test_a_row_stored_before_the_check_hears_nothing_either(
        self,
        owner: ApiActor,
        client: httpx.AsyncClient,
        project: dict[str, Any],
        receiver: Receiver,
        session: AsyncSession,
    ) -> None:
        """A webhook written before iteration 96 may already name another workspace's
        project. Deliveries are matched on the workspace too, so it is inert."""
        outsider = await register(client, "earlier")
        theirs = (
            await outsider.post("/api/v1/organizations", json={"slug": "earlier", "name": "E"})
        ).json()
        session.add(
            Webhook(
                organization_id=uuid.UUID(theirs["id"]),
                project_id=uuid.UUID(project["id"]),
                target_url="https://attacker.example.com/",
                secret="s",
                events=["project.updated"],
            )
        )
        await session.commit()
        await owner.patch(f"/api/v1/projects/{project['id']}", json={"name": "Private"})
        assert receiver.received == []

    async def test_a_webhook_cannot_name_another_workspace_s_project(
        self,
        owner: ApiActor,
        client: httpx.AsyncClient,
        project: dict[str, Any],
        receiver: Receiver,
    ) -> None:
        """Before iteration 96 it could, and heard everything that project did."""
        outsider = await register(client, "outsider")
        theirs = (
            await outsider.post("/api/v1/organizations", json={"slug": "theirs", "name": "Theirs"})
        ).json()
        response = await outsider.post(
            f"/api/v1/webhooks?organization_id={theirs['id']}",
            json={
                "target_url": "https://attacker.example.com/",
                "events": ["project.updated"],
                "project_id": project["id"],
            },
        )
        assert response.status_code == 422, response.text
        await owner.patch(f"/api/v1/projects/{project['id']}", json={"name": "Private"})
        assert receiver.received == []

    async def test_another_workspace_s_deliveries_are_not_visible(
        self,
        owner: ApiActor,
        client: httpx.AsyncClient,
        organization: dict[str, Any],
        receiver: Receiver,
    ) -> None:
        hook = await subscribe(owner, organization["id"], ["project.updated"])
        outsider = await register(client, "nosy")
        for request in (
            outsider.get(f"/api/v1/webhooks/{hook['id']}/deliveries"),
            outsider.post(f"/api/v1/webhooks/{hook['id']}/ping"),
        ):
            assert (await request).status_code in (403, 404)

    async def test_an_annotator_cannot_manage_webhooks(
        self,
        owner: ApiActor,
        client: httpx.AsyncClient,
        organization: dict[str, Any],
        receiver: Receiver,
    ) -> None:
        hook = await subscribe(owner, organization["id"], ["project.updated"])
        annotator = await register(client, "annotator9")
        await add_member(owner, organization["id"], annotator, Role.ANNOTATOR)
        assert (await annotator.get(f"/api/v1/webhooks/{hook['id']}/deliveries")).status_code == 403
        assert (await annotator.post(f"/api/v1/webhooks/{hook['id']}/ping")).status_code == 403


class TestWhatAnIntegratorSees:
    async def test_a_ping_is_sent_now_and_reported(
        self, owner: ApiActor, organization: dict[str, Any], receiver: Receiver
    ) -> None:
        hook = await subscribe(owner, organization["id"], ["task.completed"])
        response = await owner.post(f"/api/v1/webhooks/{hook['id']}/ping")
        assert response.status_code == 200, response.text
        delivery = response.json()
        assert delivery["event"] == "ping"
        assert delivery["status_code"] == 200
        assert delivery["delivered_at"] is not None
        (request,) = receiver.received
        assert request.headers["x-curvevision-event"] == "ping"
        assert signed_by(request, hook["secret"])
        assert json.loads(request.content)["data"]["webhook"]["id"] == hook["id"]

    async def test_a_failed_ping_says_what_the_receiver_answered(
        self, owner: ApiActor, organization: dict[str, Any], receiver: Receiver
    ) -> None:
        receiver.status = 401
        hook = await subscribe(owner, organization["id"], ["task.completed"])
        delivery = (await owner.post(f"/api/v1/webhooks/{hook['id']}/ping")).json()
        assert delivery["status_code"] == 401
        assert delivery["delivered_at"] is None
        assert "receiver says no" in delivery["error"]
        assert delivery["retrying"] is False

    async def test_recent_deliveries_newest_first(
        self,
        owner: ApiActor,
        organization: dict[str, Any],
        project: dict[str, Any],
        receiver: Receiver,
    ) -> None:
        hook = await subscribe(owner, organization["id"], ["project.updated"])
        await owner.patch(f"/api/v1/projects/{project['id']}", json={"name": "First"})
        receiver.status = 503
        await owner.patch(f"/api/v1/projects/{project['id']}", json={"name": "Second"})

        listed = await owner.get(f"/api/v1/webhooks/{hook['id']}/deliveries")
        assert listed.status_code == 200, listed.text
        rows = listed.json()
        assert rows[0]["status_code"] == 503
        assert rows[0]["delivered_at"] is None
        assert rows[-1]["status_code"] == 200
        assert rows[-1]["delivered_at"] is not None
        assert all(row["event"] == "project.updated" for row in rows)
        assert "secret" not in json.dumps(rows)
