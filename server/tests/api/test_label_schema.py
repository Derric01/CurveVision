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


#: Fields `LabelOut` reports that `LabelIn` refuses. `LabelIn` is a `StrictModel`, so a
#: label read from the API cannot simply be sent back to `PUT` — the round trip 422s on
#: `project_id` and `parent_id`, which are the server's to decide. `children` is dropped
#: too: `update_label` does not touch sub-labels, and `LabelOut`'s children carry the same
#: two forbidden fields.
SERVER_OWNED = ("project_id", "parent_id", "children")


def as_payload(label: dict[str, Any], **changes: Any) -> dict[str, Any]:
    """A label read from the API, ready to send back with some fields changed.

    `PUT` replaces the whole label, so everything not sent is destroyed: an omitted
    `position` becomes 0 and an omitted `attributes` deletes the attribute schema that
    validates values already stored on annotations. Building the payload *from the label*
    rather than from the edited fields is what makes an edit safe.
    """
    return {**{k: v for k, v in label.items() if k not in SERVER_OWNED}, **changes}


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


class TestEditingALabel:
    """Renaming and recolouring, which is a `PUT` that replaces the whole label.

    The danger is not the rename; it is everything the caller does *not* send. `PUT` takes a
    whole `LabelIn`, so a form that posts `{name, color}` and nothing else resets the
    label's position, empties its shape-type restriction, and deletes every attribute
    definition on it — along with the schema that validates the values already stored on
    annotations. These pin both halves: what a round-tripped payload preserves, and what a
    partial one destroys.
    """

    async def _label_named(self, actor: ApiActor, project_id: str, name: str) -> dict[str, Any]:
        return next(label for label in await schema_of(actor, project_id) if label["name"] == name)

    async def test_a_round_tripped_payload_keeps_the_attributes_and_their_ids(
        self, owner: ApiActor, project: dict[str, Any]
    ) -> None:
        """Sending the label back as the server reported it, with one field changed."""
        car = await self._label_named(owner, project["id"], "car")
        assert car["attributes"], "the project fixture is expected to give `car` attributes"

        renamed = await owner.put(
            f"/api/v1/projects/{project['id']}/labels/{car['id']}",
            json=as_payload(car, name="automobile"),
        )
        assert renamed.status_code == 200, renamed.text
        assert renamed.json()["name"] == "automobile"
        # Same attributes, same ids: `update_label` reuses a row whose id came back.
        assert [attribute["id"] for attribute in renamed.json()["attributes"]] == [
            attribute["id"] for attribute in car["attributes"]
        ]
        assert [attribute["name"] for attribute in renamed.json()["attributes"]] == [
            attribute["name"] for attribute in car["attributes"]
        ]

    async def test_a_partial_payload_destroys_everything_it_leaves_out(
        self, owner: ApiActor, project: dict[str, Any]
    ) -> None:
        """The trap, pinned so that nothing starts sending one by accident.

        This is not behaviour to rely on — it is behaviour to know about. `PUT` is a
        replace, and a client that treats it as a patch silently deletes an attribute
        schema. It is only *allowed* to here because nothing has recorded a value under
        those attributes yet; once something has, the same request is refused (see
        `TestEditingAttributes`).
        """
        car = await self._label_named(owner, project["id"], "car")
        stripped = await owner.put(
            f"/api/v1/projects/{project['id']}/labels/{car['id']}",
            json={"name": "automobile", "color": car["color"]},
        )
        assert stripped.status_code == 200
        reread = next(
            label for label in await schema_of(owner, project["id"]) if label["id"] == car["id"]
        )
        assert (stripped.json()["attributes"], reread["attributes"]) == ([], [])

    async def test_renaming_keeps_the_label_where_it_was_in_the_schema(
        self, owner: ApiActor, project: dict[str, Any]
    ) -> None:
        """`update_label` assigns `position` outright, so an omitted one means 0."""
        before = names(await schema_of(owner, project["id"]))
        pedestrian = await self._label_named(owner, project["id"], "pedestrian")

        await owner.put(
            f"/api/v1/projects/{project['id']}/labels/{pedestrian['id']}",
            json=as_payload(pedestrian, name="walker"),
        )
        after = names(await schema_of(owner, project["id"]))
        assert after == [name if name != "pedestrian" else "walker" for name in before]

    async def test_renaming_to_a_name_already_in_the_schema_is_refused(
        self, owner: ApiActor, project: dict[str, Any]
    ) -> None:
        """Two labels of one name is not a schema.

        `create_label` has always refused a duplicate; `update_label` did not check at all,
        so the rule could be walked straight around with a rename. Exports key classes by
        name — COCO categories, a YOLO class list, this project's own class distribution —
        so a duplicate does not stay a cosmetic problem.
        """
        pedestrian = await self._label_named(owner, project["id"], "pedestrian")
        refused = await owner.put(
            f"/api/v1/projects/{project['id']}/labels/{pedestrian['id']}",
            json=as_payload(pedestrian, name="car"),
        )
        assert refused.status_code == 409
        assert "car" in refused.text
        assert sorted(names(await schema_of(owner, project["id"]))) == ["car", "pedestrian"]

    async def test_renaming_a_label_to_itself_is_not_a_duplicate(
        self, owner: ApiActor, project: dict[str, Any]
    ) -> None:
        """Recolouring without touching the name must not trip the check above."""
        car = await self._label_named(owner, project["id"], "car")
        recoloured = await owner.put(
            f"/api/v1/projects/{project['id']}/labels/{car['id']}",
            json=as_payload(car, color="#123456"),
        )
        assert recoloured.status_code == 200, recoloured.text
        assert recoloured.json()["color"] == "#123456"

    async def test_the_annotations_on_a_renamed_label_are_untouched(
        self, owner: ApiActor, project: dict[str, Any]
    ) -> None:
        """A rename is not a relabel: shapes reference the label by id, not by name."""
        _task, jobs = await make_task(owner, project)
        car = await self._label_named(owner, project["id"], "car")
        written = await owner.patch(
            f"/api/v1/jobs/{jobs[0]['id']}/annotations",
            json={
                "annotation_version": 0,
                "created_shapes": [
                    {
                        "label_id": car["id"],
                        "frame": 0,
                        "shape_type": "rectangle",
                        "points": [0, 0, 10, 10],
                        "attributes": {"colour": "red"},
                    }
                ],
            },
        )
        assert written.status_code == 200, written.text

        await owner.put(
            f"/api/v1/projects/{project['id']}/labels/{car['id']}",
            json=as_payload(car, name="automobile"),
        )
        document = (await owner.get(f"/api/v1/jobs/{jobs[0]['id']}/annotations")).json()
        assert len(document["shapes"]) == 1
        assert document["shapes"][0]["label_id"] == car["id"]
        # The attribute value survives because its definition was reused rather than
        # recreated -- values are keyed by attribute name.
        assert document["shapes"][0]["attributes"] == {"colour": "red"}

    async def test_editing_a_label_needs_the_rank_for_it(
        self, owner: ApiActor, project: dict[str, Any], organization: dict[str, Any]
    ) -> None:
        annotator = await register(owner.client, "renamer")
        await add_member(owner, organization["id"], annotator, Role.ANNOTATOR)
        car = await self._label_named(owner, project["id"], "car")

        refused = await annotator.put(
            f"/api/v1/projects/{project['id']}/labels/{car['id']}",
            json=as_payload(car, name="automobile"),
        )
        assert refused.status_code == 403


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


async def draw(
    actor: ApiActor, job_id: str, label_id: str, attributes: dict[str, Any]
) -> dict[str, Any]:
    """Draw one rectangle and return it as the server stored it."""
    document = (await actor.get(f"/api/v1/jobs/{job_id}/annotations")).json()
    written = await actor.patch(
        f"/api/v1/jobs/{job_id}/annotations",
        json={
            "annotation_version": document["annotation_version"],
            "created_shapes": [
                {
                    "label_id": label_id,
                    "frame": 0,
                    "shape_type": "rectangle",
                    "points": [0, 0, 10, 10],
                    "attributes": attributes,
                }
            ],
        },
    )
    assert written.status_code == 200, written.text
    shapes = (await actor.get(f"/api/v1/jobs/{job_id}/annotations")).json()["shapes"]
    return dict(shapes[-1])


async def resave(actor: ApiActor, job_id: str, shape: dict[str, Any]) -> int:
    """Save a shape back with its attributes untouched, the way the editor does.

    Moving a box in the editor sends the whole shape, attributes included, exactly as it was
    read. So whatever a schema edit does to the values already recorded, *this* is where it
    shows: a value the schema no longer accepts is a 422 on the annotator's next autosave,
    not on the schema edit that caused it.
    """
    document = (await actor.get(f"/api/v1/jobs/{job_id}/annotations")).json()
    response = await actor.patch(
        f"/api/v1/jobs/{job_id}/annotations",
        json={
            "annotation_version": document["annotation_version"],
            "updated_shapes": [
                {
                    key: shape[key]
                    for key in ("id", "label_id", "frame", "shape_type", "points", "attributes")
                }
            ],
        },
    )
    return response.status_code


class TestEditingAttributes:
    """Changing a label's attribute definitions without stranding values already recorded.

    Values are stored on each annotation as a JSON object **keyed by attribute name**, and
    `validate_attributes` rejects a key the schema does not declare rather than dropping it.
    So an edit that removes or renames an attribute, changes its type, or narrows a
    select's options does not fail itself — it leaves every annotation carrying the old
    value unable to be saved again, and the annotator is the one who finds out, from an
    autosave that is refused.

    The rules follow CVAT's for an attribute that already exists (`cvat-ai/cvat`,
    `LabelSerializer._update_attribute`): its type and its `mutable` flag are fixed, and a
    select's options may be added to but not taken away. CVAT allows a rename and a delete
    because it stores values against the attribute's id; this codebase stores them against
    its name, so here those two are refused while any annotation of the label records a
    value under it — and allowed when none does, which is what keeps a schema correctable
    before it is used.
    """

    async def _car(self, actor: ApiActor, project_id: str) -> dict[str, Any]:
        return next(label for label in await schema_of(actor, project_id) if label["name"] == "car")

    def _attribute(self, label: dict[str, Any], name: str) -> dict[str, Any]:
        return dict(next(a for a in label["attributes"] if a["name"] == name))

    def _without(self, label: dict[str, Any], name: str) -> list[dict[str, Any]]:
        return [a for a in label["attributes"] if a["name"] != name]

    def _replacing(
        self, label: dict[str, Any], which: str, /, **changes: Any
    ) -> list[dict[str, Any]]:
        return [{**a, **changes} if a["name"] == which else a for a in label["attributes"]]

    async def _put(
        self, actor: ApiActor, project_id: str, label: dict[str, Any], attributes: list[Any]
    ) -> Any:
        return await actor.put(
            f"/api/v1/projects/{project_id}/labels/{label['id']}",
            json=as_payload(label, attributes=attributes),
        )

    async def test_removing_an_attribute_with_recorded_values_is_refused(
        self, owner: ApiActor, project: dict[str, Any]
    ) -> None:
        """The defect itself: the edit succeeded, and the next save of the shape did not."""
        _task, jobs = await make_task(owner, project)
        car = await self._car(owner, project["id"])
        shape = await draw(owner, jobs[0]["id"], car["id"], {"colour": "red"})

        refused = await self._put(owner, project["id"], car, self._without(car, "colour"))
        assert refused.status_code == 409, refused.text
        assert "colour" in refused.text
        assert "1 annotation" in refused.text

        # Nothing half-applied, and the annotator's next autosave still goes through.
        assert [a["name"] for a in (await self._car(owner, project["id"]))["attributes"]] == [
            a["name"] for a in car["attributes"]
        ]
        assert await resave(owner, jobs[0]["id"], shape) == 200

    async def test_an_attribute_nobody_recorded_a_value_for_can_be_removed(
        self, owner: ApiActor, project: dict[str, Any]
    ) -> None:
        """Refusing *every* removal once a label is in use would make a typo permanent.

        `parked` has no default, so a shape drawn without it records nothing under it.
        """
        _task, jobs = await make_task(owner, project)
        car = await self._car(owner, project["id"])
        shape = await draw(owner, jobs[0]["id"], car["id"], {"colour": "red"})
        assert "parked" not in shape["attributes"]

        removed = await self._put(owner, project["id"], car, self._without(car, "parked"))
        assert removed.status_code == 200, removed.text
        assert [a["name"] for a in removed.json()["attributes"]] == ["colour"]
        assert await resave(owner, jobs[0]["id"], shape) == 200

    async def test_a_value_recorded_on_a_track_keyframe_counts(
        self, owner: ApiActor, project: dict[str, Any]
    ) -> None:
        """Mutable attributes live per keyframe, in a table that has no `label_id` of its own."""
        _task, jobs = await make_task(owner, project)
        car = await self._car(owner, project["id"])
        written = await owner.patch(
            f"/api/v1/jobs/{jobs[0]['id']}/annotations",
            json={
                "annotation_version": 0,
                "created_tracks": [
                    {
                        "label_id": car["id"],
                        "shape_type": "rectangle",
                        "attributes": {"colour": "red"},
                        "shapes": [
                            {
                                "frame": 0,
                                "shape_type": "rectangle",
                                "points": [0, 0, 10, 10],
                                "attributes": {"parked": True},
                            }
                        ],
                    }
                ],
            },
        )
        assert written.status_code == 200, written.text
        # Recorded on the keyframe only: the track-level `attributes` above has no `parked`.
        refused = await self._put(owner, project["id"], car, self._without(car, "parked"))
        assert refused.status_code == 409, refused.text
        assert "parked" in refused.text

    async def test_a_value_recorded_on_a_tag_counts(
        self, owner: ApiActor, project: dict[str, Any]
    ) -> None:
        _task, jobs = await make_task(owner, project)
        car = await self._car(owner, project["id"])
        written = await owner.patch(
            f"/api/v1/jobs/{jobs[0]['id']}/annotations",
            json={
                "annotation_version": 0,
                "created_tags": [
                    {"label_id": car["id"], "frame": 0, "attributes": {"colour": "blue"}}
                ],
            },
        )
        assert written.status_code == 200, written.text
        refused = await self._put(owner, project["id"], car, self._without(car, "colour"))
        assert refused.status_code == 409, refused.text

    async def test_renaming_an_attribute_with_recorded_values_is_refused(
        self, owner: ApiActor, project: dict[str, Any]
    ) -> None:
        """A rename is a removal as far as the stored values can tell: they are keyed by name."""
        _task, jobs = await make_task(owner, project)
        car = await self._car(owner, project["id"])
        shape = await draw(owner, jobs[0]["id"], car["id"], {"colour": "red"})

        refused = await self._put(
            owner, project["id"], car, self._replacing(car, "colour", name="color")
        )
        assert refused.status_code == 409, refused.text
        assert "colour" in refused.text
        assert await resave(owner, jobs[0]["id"], shape) == 200

    async def test_renaming_an_attribute_nobody_recorded_is_allowed_and_keeps_its_id(
        self, owner: ApiActor, project: dict[str, Any]
    ) -> None:
        car = await self._car(owner, project["id"])
        parked = self._attribute(car, "parked")

        renamed = await self._put(
            owner, project["id"], car, self._replacing(car, "parked", name="stationary")
        )
        assert renamed.status_code == 200, renamed.text
        assert self._attribute(renamed.json(), "stationary")["id"] == parked["id"]

    async def test_the_type_of_an_existing_attribute_is_fixed(
        self, owner: ApiActor, project: dict[str, Any]
    ) -> None:
        """CVAT's rule, and for the same reason: `red` is not a number or a checkbox.

        Refused even with nothing recorded, so the rule a person meets does not depend on
        data they cannot see. Removing the attribute and adding a new one is the way to
        change it, and that works whenever nothing is recorded under it.
        """
        car = await self._car(owner, project["id"])
        refused = await self._put(
            owner, project["id"], car, self._replacing(car, "colour", attribute_type="text")
        )
        assert refused.status_code == 409, refused.text
        assert "type" in refused.text

    async def test_whether_an_existing_attribute_is_mutable_is_fixed(
        self, owner: ApiActor, project: dict[str, Any]
    ) -> None:
        """Turning `mutable` off would silently drop every per-keyframe value on next save."""
        car = await self._car(owner, project["id"])
        refused = await self._put(
            owner, project["id"], car, self._replacing(car, "parked", mutable=False)
        )
        assert refused.status_code == 409, refused.text
        assert "frame to frame" in refused.text

    async def test_a_select_can_gain_options_but_not_lose_them(
        self, owner: ApiActor, project: dict[str, Any]
    ) -> None:
        _task, jobs = await make_task(owner, project)
        car = await self._car(owner, project["id"])
        shape = await draw(owner, jobs[0]["id"], car["id"], {"colour": "black"})

        refused = await self._put(
            owner, project["id"], car, self._replacing(car, "colour", values=["red", "blue"])
        )
        assert refused.status_code == 409, refused.text
        assert "black" in refused.text

        grown = await self._put(
            owner,
            project["id"],
            car,
            self._replacing(car, "colour", values=["red", "blue", "black", "silver"]),
        )
        assert grown.status_code == 200, grown.text
        assert self._attribute(grown.json(), "colour")["values"][-1] == "silver"
        assert await resave(owner, jobs[0]["id"], shape) == 200

    async def test_an_attribute_can_be_added_to_a_label_already_in_use(
        self, owner: ApiActor, project: dict[str, Any]
    ) -> None:
        """The ordinary case: the schema turned out to need `occluded`."""
        _task, jobs = await make_task(owner, project)
        car = await self._car(owner, project["id"])
        shape = await draw(owner, jobs[0]["id"], car["id"], {"colour": "red"})

        added = await self._put(
            owner,
            project["id"],
            car,
            [
                *car["attributes"],
                {"name": "occluded", "attribute_type": "checkbox", "default_value": "false"},
            ],
        )
        assert added.status_code == 200, added.text
        assert [a["name"] for a in added.json()["attributes"]] == ["colour", "parked", "occluded"]
        assert await resave(owner, jobs[0]["id"], shape) == 200

    async def test_a_new_required_attribute_needs_a_default_once_annotations_exist(
        self, owner: ApiActor, project: dict[str, Any]
    ) -> None:
        """Every annotation drawn before it has no value, and `required` refuses the next save."""
        _task, jobs = await make_task(owner, project)
        car = await self._car(owner, project["id"])
        shape = await draw(owner, jobs[0]["id"], car["id"], {"colour": "red"})
        required = {"name": "make", "attribute_type": "text", "required": True}

        refused = await self._put(owner, project["id"], car, [*car["attributes"], required])
        assert refused.status_code == 409, refused.text
        assert "make" in refused.text
        assert await resave(owner, jobs[0]["id"], shape) == 200

        # With a default, the annotations drawn before it take the default on their next save.
        defaulted = await self._put(
            owner, project["id"], car, [*car["attributes"], {**required, "default_value": "?"}]
        )
        assert defaulted.status_code == 200, defaulted.text
        assert await resave(owner, jobs[0]["id"], shape) == 200

    async def test_an_existing_attribute_cannot_become_required_without_a_default_either(
        self, owner: ApiActor, project: dict[str, Any]
    ) -> None:
        _task, jobs = await make_task(owner, project)
        car = await self._car(owner, project["id"])
        await draw(owner, jobs[0]["id"], car["id"], {"colour": "red"})

        refused = await self._put(
            owner, project["id"], car, self._replacing(car, "parked", required=True)
        )
        assert refused.status_code == 409, refused.text
        assert "parked" in refused.text

    async def test_an_attribute_can_be_replaced_by_a_new_one_of_the_same_name(
        self, owner: ApiActor, project: dict[str, Any]
    ) -> None:
        """How a type is changed: remove the old definition and add a new one, in one `PUT`.

        Both rows share a name under a unique constraint, so the old one has to be gone
        before the new one is written — the order a flush would not otherwise choose.
        """
        car = await self._car(owner, project["id"])
        parked = self._attribute(car, "parked")
        replacement = {"name": "parked", "attribute_type": "text", "mutable": False}

        replaced = await self._put(
            owner, project["id"], car, [*self._without(car, "parked"), replacement]
        )
        assert replaced.status_code == 200, replaced.text
        now = self._attribute(replaced.json(), "parked")
        assert (now["attribute_type"], now["id"] != parked["id"]) == ("text", True)

    async def test_two_attributes_can_trade_names(
        self, owner: ApiActor, project: dict[str, Any]
    ) -> None:
        """Legal, if odd — and a 500 if the renames are written in the order they arrive."""
        car = await self._car(owner, project["id"])
        colour, parked = self._attribute(car, "colour"), self._attribute(car, "parked")

        traded = await self._put(
            owner,
            project["id"],
            car,
            [{**colour, "name": "parked"}, {**parked, "name": "colour"}],
        )
        assert traded.status_code == 200, traded.text
        by_id = {a["id"]: a["name"] for a in traded.json()["attributes"]}
        assert (by_id[colour["id"]], by_id[parked["id"]]) == ("parked", "colour")

    async def test_a_refused_edit_changes_nothing_else_about_the_label(
        self, owner: ApiActor, project: dict[str, Any]
    ) -> None:
        """The label's own fields travel in the same `PUT`, and must not half-apply."""
        car = await self._car(owner, project["id"])
        refused = await owner.put(
            f"/api/v1/projects/{project['id']}/labels/{car['id']}",
            json=as_payload(
                car,
                name="automobile",
                attributes=self._replacing(car, "colour", attribute_type="text"),
            ),
        )
        assert refused.status_code == 409, refused.text
        assert (await self._car(owner, project["id"]))["name"] == "car"

    async def test_two_attributes_of_one_name_are_refused(
        self, owner: ApiActor, project: dict[str, Any]
    ) -> None:
        """Values are keyed by name, so a second `colour` would share the first one's values."""
        car = await self._car(owner, project["id"])
        twin = {"name": "colour", "attribute_type": "text"}

        on_update = await self._put(owner, project["id"], car, [*car["attributes"], twin])
        assert on_update.status_code == 422, on_update.text
        on_create = await owner.post(
            f"/api/v1/projects/{project['id']}/labels",
            json={"name": "twins", "attributes": [twin, twin]},
        )
        assert on_create.status_code == 422, on_create.text

    @pytest.mark.parametrize(
        ("attribute", "problem"),
        [
            ({"attribute_type": "select", "values": ["a", "b"], "default_value": "c"}, "one of"),
            ({"attribute_type": "radio", "values": ["a", "b"], "default_value": "c"}, "one of"),
            ({"attribute_type": "checkbox", "default_value": "yes"}, "true"),
            ({"attribute_type": "number", "default_value": "many"}, "number"),
        ],
    )
    async def test_a_default_the_attribute_itself_would_refuse_is_refused(
        self, owner: ApiActor, project: dict[str, Any], attribute: dict[str, Any], problem: str
    ) -> None:
        """A default is written into every annotation drawn without a value.

        `validate_attributes` fills it in without checking it, so an invalid one was
        recorded silently — and then refused on that annotation's next save, which is the
        same stranding as every other case here, only arriving from the other side.
        """
        refused = await owner.post(
            f"/api/v1/projects/{project['id']}/labels",
            json={"name": "defaulted", "attributes": [{"name": "a", **attribute}]},
        )
        assert refused.status_code == 422, refused.text
        assert problem in refused.text
