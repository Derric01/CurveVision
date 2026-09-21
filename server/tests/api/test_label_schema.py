"""Changing a project's label schema after the project exists.

`POST`, `PUT` and `DELETE /projects/{id}/labels` have existed since the initial schema and
**nothing tested any of them**, which is how the ordering defect below survived: a label
added later took position 0 and sorted into the middle of the list by name, so a schema
grew in an order nobody chose.

The property most worth pinning is the refusal: deleting a label that annotations still use
destroys annotation work, and the service refuses it rather than cascading. That is the
whole reason a delete control can be offered in the UI at all.
"""

from __future__ import annotations

from typing import Any

import pytest

from curvevision.domain.enums import Role
from tests.conftest import ApiActor, add_member, register

pytest.importorskip("PIL", reason="a job is needed to annotate against a label")

from tests.api.test_collaboration import make_task


def names(schema: list[dict[str, Any]]) -> list[str]:
    return [label["name"] for label in schema]


async def schema_of(actor: ApiActor, project_id: str) -> list[dict[str, Any]]:
    response = await actor.get(f"/api/v1/projects/{project_id}/labels")
    assert response.status_code == 200, response.text
    return list(response.json())


class TestAddingALabel:
    async def test_a_label_added_later_goes_to_the_end_of_the_schema(
        self, owner: ApiActor, project: dict[str, Any]
    ) -> None:
        """Not into the middle of it, sorted by name.

        The listing orders by `(position, name)`, and every label sent without a position
        took 0 — so `van` added to a `car`/`pedestrian` schema landed *first*. A schema is
        an ordered thing: it is the order the editor's label picker shows, and the order a
        person reads the keyboard shortcuts in.
        """
        before = names(await schema_of(owner, project["id"]))
        assert before, "the project fixture is expected to start with a label schema"

        added = await owner.post(
            f"/api/v1/projects/{project['id']}/labels",
            json={"name": "aardvark", "color": "#22c55e"},
        )
        assert added.status_code == 201, added.text
        assert names(await schema_of(owner, project["id"])) == [*before, "aardvark"]

        # And again, so "appends" means appends rather than "sorts after the first batch".
        await owner.post(f"/api/v1/projects/{project['id']}/labels", json={"name": "abacus"})
        assert names(await schema_of(owner, project["id"])) == [*before, "aardvark", "abacus"]

    async def test_an_explicit_position_is_still_honoured(
        self, owner: ApiActor, project: dict[str, Any]
    ) -> None:
        """Appending is the *default*, not a rule imposed on a caller who said where.

        The fixture's schema is `car` at position 0 and `pedestrian` at 1. A label asking
        for position 1 sorts between them by name, rather than going to the end where an
        unpositioned one would.
        """
        created = await owner.post(
            f"/api/v1/projects/{project['id']}/labels", json={"name": "bus", "position": 1}
        )
        assert created.status_code == 201, created.text
        assert names(await schema_of(owner, project["id"])) == ["car", "bus", "pedestrian"]

    async def test_a_duplicate_name_is_refused_rather_than_creating_a_second(
        self, owner: ApiActor, project: dict[str, Any]
    ) -> None:
        existing = names(await schema_of(owner, project["id"]))[0]
        refused = await owner.post(
            f"/api/v1/projects/{project['id']}/labels", json={"name": existing}
        )
        assert refused.status_code == 409
        assert existing in refused.text

    async def test_changing_the_schema_needs_the_rank_for_it(
        self, owner: ApiActor, project: dict[str, Any], organization: dict[str, Any]
    ) -> None:
        """An annotator draws with the schema; they do not get to change it."""
        annotator = await register(owner.client, "schemaless")
        await add_member(owner, organization["id"], annotator, Role.ANNOTATOR)

        refused = await annotator.post(
            f"/api/v1/projects/{project['id']}/labels", json={"name": "van"}
        )
        assert refused.status_code == 403
        # ...but they can read it, which is what the editor's label picker does.
        assert (await annotator.get(f"/api/v1/projects/{project['id']}/labels")).status_code == 200


class TestRemovingALabel:
    async def test_an_unused_label_is_removed(
        self, owner: ApiActor, project: dict[str, Any]
    ) -> None:
        created = (
            await owner.post(f"/api/v1/projects/{project['id']}/labels", json={"name": "unused"})
        ).json()
        removed = await owner.delete(f"/api/v1/projects/{project['id']}/labels/{created['id']}")
        assert removed.status_code == 204
        assert "unused" not in names(await schema_of(owner, project["id"]))

    async def test_a_label_annotations_still_use_is_refused_and_the_work_survives(
        self, owner: ApiActor, project: dict[str, Any]
    ) -> None:
        """The reason a delete button can be offered at all: the server will not cascade."""
        _task, jobs = await make_task(owner, project)
        job_id = jobs[0]["id"]
        label = (
            await owner.post(f"/api/v1/projects/{project['id']}/labels", json={"name": "in-use"})
        ).json()

        written = await owner.patch(
            f"/api/v1/jobs/{job_id}/annotations",
            json={
                "annotation_version": 0,
                "created_shapes": [
                    {
                        "label_id": label["id"],
                        "frame": 0,
                        "shape_type": "rectangle",
                        "points": [1, 1, 20, 20],
                    }
                ],
            },
        )
        assert written.status_code == 200, written.text

        refused = await owner.delete(f"/api/v1/projects/{project['id']}/labels/{label['id']}")
        assert refused.status_code == 409
        assert "relabel" in refused.text.lower()

        # Both the label and the annotation are still there: a refusal that half-applied
        # would be worse than a cascade, because nothing would say so.
        assert "in-use" in names(await schema_of(owner, project["id"]))
        document = (await owner.get(f"/api/v1/jobs/{job_id}/annotations")).json()
        assert len(document["shapes"]) == 1

    async def test_a_label_from_another_project_is_not_found_rather_than_deleted(
        self, owner: ApiActor, project: dict[str, Any], organization: dict[str, Any]
    ) -> None:
        other = (
            await owner.post(
                "/api/v1/projects",
                json={
                    "organization_id": organization["id"],
                    "slug": "elsewhere",
                    "name": "Elsewhere",
                    "labels": [{"name": "borrowed"}],
                },
            )
        ).json()
        borrowed = other["labels"][0]["id"]

        missing = await owner.delete(f"/api/v1/projects/{project['id']}/labels/{borrowed}")
        assert missing.status_code == 404
        assert names(await schema_of(owner, other["id"])) == ["borrowed"]


class TestUsingANewLabel:
    async def test_a_label_added_today_can_be_drawn_with_immediately(
        self, owner: ApiActor, project: dict[str, Any]
    ) -> None:
        """The point of the whole feature: a schema that turned out to be incomplete."""
        _task, jobs = await make_task(owner, project)
        added = (
            await owner.post(
                f"/api/v1/projects/{project['id']}/labels",
                json={"name": "van", "allowed_shape_types": ["rectangle"]},
            )
        ).json()

        written = await owner.patch(
            f"/api/v1/jobs/{jobs[0]['id']}/annotations",
            json={
                "annotation_version": 0,
                "created_shapes": [
                    {
                        "label_id": added["id"],
                        "frame": 0,
                        "shape_type": "rectangle",
                        "points": [0, 0, 10, 10],
                    }
                ],
            },
        )
        assert written.status_code == 200, written.text

        refused = await owner.patch(
            f"/api/v1/jobs/{jobs[0]['id']}/annotations",
            json={
                "annotation_version": written.json()["annotation_version"],
                "created_shapes": [
                    {
                        "label_id": added["id"],
                        "frame": 0,
                        "shape_type": "polygon",
                        "points": [0, 0, 10, 0, 10, 10],
                    }
                ],
            },
        )
        # The shape-type restriction it was created with is enforced like any other.
        assert refused.status_code == 422
