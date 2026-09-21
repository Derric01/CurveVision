"""What a `PATCH` means by an omitted field, and whether its response tells the truth.

Two defects have now been found three times between them, in four iterations, always the
same way — by building a screen that needed the behaviour:

* **An omitted field and an explicit `null` read the same.** `if payload.x is not None`
  cannot tell "leave it alone" from "clear it", so a nullable column has no route back to
  null. Every one of these is a thing that could be set and never unset.
* **The response reports the state from before the write.** The `owner`/`assignee`
  relationships are `lazy="selectin"` and the sessionmaker is `expire_on_commit=False`, so
  writing the *id* leaves the loaded relationship object untouched. A screen that renders
  what the write returned shows the previous holder, or `null` for something that just
  gained its first.

`update_job` was fixed in one iteration and `update_label` in another. This file is the
audit that followed: every route that writes a nullable field and returns an eagerly-loaded
relationship, tested for both, so the fourth instance is not found by a person either.
"""

from __future__ import annotations

from typing import Any

import pytest

from curvevision.domain.enums import Role
from tests.conftest import ApiActor, add_member, register

pytest.importorskip("PIL", reason="a task needs media before it has jobs")

from tests.api.test_collaboration import make_task


class TestUpdatingATask:
    async def test_an_explicit_null_unassigns_while_an_omitted_field_leaves_it_alone(
        self, owner: ApiActor, project: dict[str, Any], organization: dict[str, Any]
    ) -> None:
        task, _jobs = await make_task(owner, project)
        annotator = await register(owner.client, "taskholder")
        await add_member(owner, organization["id"], annotator, Role.ANNOTATOR)
        await owner.patch(f"/api/v1/tasks/{task['id']}", json={"assignee_id": annotator.id})

        renamed = await owner.patch(f"/api/v1/tasks/{task['id']}", json={"name": "Renamed"})
        assert renamed.json()["assignee"]["username"] == "taskholder"

        cleared = await owner.patch(f"/api/v1/tasks/{task['id']}", json={"assignee_id": None})
        assert cleared.status_code == 200, cleared.text
        assert cleared.json()["assignee"] is None
        assert (await owner.get(f"/api/v1/tasks/{task['id']}")).json()["assignee"] is None

    async def test_the_response_names_the_new_holder_rather_than_the_previous_one(
        self, owner: ApiActor, project: dict[str, Any], organization: dict[str, Any]
    ) -> None:
        task, _jobs = await make_task(owner, project)
        first = await register(owner.client, "taskfirst")
        second = await register(owner.client, "tasksecond")
        for who in (first, second):
            await add_member(owner, organization["id"], who, Role.ANNOTATOR)

        assigned = await owner.patch(f"/api/v1/tasks/{task['id']}", json={"assignee_id": first.id})
        assert assigned.json()["assignee"]["username"] == "taskfirst"
        reassigned = await owner.patch(
            f"/api/v1/tasks/{task['id']}", json={"assignee_id": second.id}
        )
        assert reassigned.json()["assignee"]["username"] == "tasksecond"

    async def test_a_description_can_be_cleared(
        self, owner: ApiActor, project: dict[str, Any]
    ) -> None:
        task, _jobs = await make_task(owner, project)
        await owner.patch(f"/api/v1/tasks/{task['id']}", json={"description": "Batch one"})
        cleared = await owner.patch(f"/api/v1/tasks/{task['id']}", json={"description": None})
        assert cleared.json()["description"] is None

    async def test_assigning_a_task_still_needs_the_rank_for_it(
        self, owner: ApiActor, project: dict[str, Any], organization: dict[str, Any]
    ) -> None:
        """Including the clearing half, which is the same decision in reverse."""
        task, _jobs = await make_task(owner, project)
        annotator = await register(owner.client, "tasknobody")
        await add_member(owner, organization["id"], annotator, Role.ANNOTATOR)

        refused = await annotator.patch(f"/api/v1/tasks/{task['id']}", json={"assignee_id": None})
        assert refused.status_code == 403


class TestUpdatingAProject:
    async def test_an_explicit_null_clears_the_owner(
        self, owner: ApiActor, project: dict[str, Any], organization: dict[str, Any]
    ) -> None:
        lead = await register(owner.client, "projectlead")
        await add_member(owner, organization["id"], lead, Role.MAINTAINER)
        await owner.patch(f"/api/v1/projects/{project['id']}", json={"owner_id": lead.id})

        untouched = await owner.patch(f"/api/v1/projects/{project['id']}", json={"name": "Renamed"})
        assert untouched.json()["owner"]["username"] == "projectlead"

        cleared = await owner.patch(f"/api/v1/projects/{project['id']}", json={"owner_id": None})
        assert cleared.status_code == 200, cleared.text
        assert cleared.json()["owner"] is None
        assert (await owner.get(f"/api/v1/projects/{project['id']}")).json()["owner"] is None

    async def test_the_response_names_the_new_owner_rather_than_the_previous_one(
        self, owner: ApiActor, project: dict[str, Any], organization: dict[str, Any]
    ) -> None:
        first = await register(owner.client, "leadfirst")
        second = await register(owner.client, "leadsecond")
        for who in (first, second):
            await add_member(owner, organization["id"], who, Role.MAINTAINER)

        assigned = await owner.patch(
            f"/api/v1/projects/{project['id']}", json={"owner_id": first.id}
        )
        assert assigned.json()["owner"]["username"] == "leadfirst"
        reassigned = await owner.patch(
            f"/api/v1/projects/{project['id']}", json={"owner_id": second.id}
        )
        assert reassigned.json()["owner"]["username"] == "leadsecond"

    async def test_a_description_can_be_cleared(
        self, owner: ApiActor, project: dict[str, Any]
    ) -> None:
        await owner.patch(
            f"/api/v1/projects/{project['id']}", json={"description": "Street scenes"}
        )
        cleared = await owner.patch(f"/api/v1/projects/{project['id']}", json={"description": None})
        assert cleared.json()["description"] is None


#: `updated_at` and `created_at` are excluded from the whole-body comparisons below, and
#: **not** because they are expected to differ: they hold the same instant either way. They
#: are *serialised* differently — `...Z` from the instance that was just written, and with
#: no suffix at all once the row has been read back from SQLite, which has no time-zone
#: type. That is its own defect, and a nastier one than it looks, because a browser reads a
#: suffix-less timestamp as **local** time; it is recorded in `handoff.md` as the next piece
#: of work. These tests are about staleness, so they compare everything else and leave that
#: one to the change that fixes it.
TIMESTAMPS = ("created_at", "updated_at")


def without_timestamps(body: dict[str, Any]) -> dict[str, Any]:
    return {key: value for key, value in body.items() if key not in TIMESTAMPS}


class TestAWriteAgreesWithTheNextRead:
    """The general property behind both defects: a response is a read like any other.

    Asserted over the whole body rather than field by field, because the point is that
    nothing in the response is stale — the next screen to render one of these should not
    have to know which fields can be trusted.
    """

    async def test_for_a_task(
        self, owner: ApiActor, project: dict[str, Any], organization: dict[str, Any]
    ) -> None:
        task, _jobs = await make_task(owner, project)
        annotator = await register(owner.client, "agreeable")
        await add_member(owner, organization["id"], annotator, Role.ANNOTATOR)

        written = await owner.patch(
            f"/api/v1/tasks/{task['id']}",
            json={"assignee_id": annotator.id, "description": "Now with a holder"},
        )
        read = await owner.get(f"/api/v1/tasks/{task['id']}")
        assert without_timestamps(written.json()) == without_timestamps(read.json())

    async def test_for_a_project(
        self, owner: ApiActor, project: dict[str, Any], organization: dict[str, Any]
    ) -> None:
        lead = await register(owner.client, "agreeablelead")
        await add_member(owner, organization["id"], lead, Role.MAINTAINER)

        written = await owner.patch(
            f"/api/v1/projects/{project['id']}",
            json={"owner_id": lead.id, "description": "Now with an owner"},
        )
        read = await owner.get(f"/api/v1/projects/{project['id']}")
        assert without_timestamps(written.json()) == without_timestamps(read.json())

    async def test_for_a_job(
        self, owner: ApiActor, project: dict[str, Any], organization: dict[str, Any]
    ) -> None:
        """Already fixed, and kept here so the property is stated in one place."""
        _task, jobs = await make_task(owner, project)
        reviewer = await register(owner.client, "agreeableeye")
        await add_member(owner, organization["id"], reviewer, Role.REVIEWER)

        written = await owner.patch(
            f"/api/v1/jobs/{jobs[0]['id']}", json={"reviewer_id": reviewer.id}
        )
        read = await owner.get(f"/api/v1/jobs/{jobs[0]['id']}")
        assert without_timestamps(written.json()) == without_timestamps(read.json())
