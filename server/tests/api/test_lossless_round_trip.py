"""What a backup restores, and which frames of a track come back as keyframes.

The native format's own description is "lossless", and until iteration 72 three things it
wrote were not read back. A restore stored everything as `imported` with no confidence,
so a model's **pending suggestion** (`source: model` with a confidence; accepting one
clears it) came back as finished work, and an accepted model annotation lost the record
that a model made it. A tag was written as its label alone, losing its attributes. And
every frame of a track came back a **keyframe**, because no export said which frames were
authored: right positions, but a timeline with a keyframe on every frame, where moving the
object means moving each one.

Each test here exports through the API and imports the archive into a second project, and
compares what the API then serves with what was drawn.
"""

from __future__ import annotations

from typing import Any

import pytest

from tests.api.test_collaboration import make_task
from tests.api.test_import_fidelity import (
    box,
    cvat_track,
    import_into,
    label_of,
    present_on,
    street_project,
)
from tests.conftest import ApiActor

CAR_AT_0 = [10.0, 10.0, 50.0, 50.0]
CAR_AT_3 = [40.0, 10.0, 80.0, 50.0]
#: A polygon that gains a vertex between its keyframes: interpolated by resampling, which no
#: other tool does the same way, so the frames between are where a careless import diverges.
TRIANGLE = [100.0, 100.0, 140.0, 100.0, 120.0, 140.0]
SQUARE = [110.0, 100.0, 150.0, 100.0, 150.0, 140.0, 110.0, 140.0]


async def annotated_project(actor: ApiActor, organization: dict[str, Any], slug: str):
    """A project with one of each kind of provenance, a sparse track and a tagged frame."""
    project = await street_project(actor, organization, slug)
    added = await actor.post(
        f"/api/v1/projects/{project['id']}/labels",
        json={
            "name": "scene",
            "attributes": [
                {"name": "lighting", "attribute_type": "select", "values": ["day", "night"]}
            ],
        },
    )
    assert added.status_code == 201, added.text
    project = (await actor.get(f"/api/v1/projects/{project['id']}")).json()
    _task, jobs = await make_task(actor, project, frames=6)
    car, scene = label_of(project, "car"), label_of(project, "scene")

    def shape(frame: int, x: float, **provenance: Any) -> dict[str, Any]:
        return {
            "label_id": car,
            "frame": frame,
            "shape_type": "rectangle",
            "points": [x, 200.0, x + 30, 230.0],
            "attributes": {"colour": "blue"},
            **provenance,
        }

    drawn = await actor.patch(
        f"/api/v1/jobs/{jobs[0]['id']}/annotations",
        json={
            "created_shapes": [
                shape(1, 10.0),
                shape(1, 60.0, source="model", confidence=0.7),  # pending suggestion
                shape(1, 110.0, source="model"),  # a model's, accepted
            ],
            "created_tracks": [
                {
                    "label_id": car,
                    "shape_type": "rectangle",
                    "source": "model_corrected",
                    "attributes": {"colour": "red"},
                    "shapes": [
                        {"frame": 0, "shape_type": "rectangle", "points": CAR_AT_0},
                        {"frame": 3, "shape_type": "rectangle", "points": CAR_AT_3},
                        {
                            "frame": 5,
                            "shape_type": "rectangle",
                            "points": CAR_AT_3,
                            "outside": True,
                        },
                    ],
                },
                {
                    "label_id": car,
                    "shape_type": "polygon",
                    "shapes": [
                        {"frame": 0, "shape_type": "polygon", "points": TRIANGLE},
                        {"frame": 4, "shape_type": "polygon", "points": SQUARE},
                    ],
                },
            ],
            "created_tags": [
                {
                    "label_id": scene,
                    "frame": 2,
                    "source": "model",
                    "confidence": 0.9,
                    "attributes": {"lighting": "night"},
                }
            ],
        },
    )
    assert drawn.status_code == 200, drawn.text
    return project


async def exported(actor: ApiActor, project: dict[str, Any], format_id: str) -> bytes:
    export = await actor.post(
        f"/api/v1/projects/{project['id']}/export", json={"format": format_id}
    )
    assert export.status_code == 200, export.text
    return export.content


async def restored(
    actor: ApiActor, organization: dict[str, Any], format_id: str, slug: str
) -> dict[str, Any]:
    source = await annotated_project(actor, organization, f"{slug}-source")
    archive = await exported(actor, source, format_id)
    target = await street_project(actor, organization, f"{slug}-target")
    await actor.post(
        f"/api/v1/projects/{target['id']}/labels",
        json={
            "name": "scene",
            "attributes": [
                {"name": "lighting", "attribute_type": "select", "values": ["day", "night"]}
            ],
        },
    )
    target = (await actor.get(f"/api/v1/projects/{target['id']}")).json()
    _result, document = await import_into(actor, target, format_id, archive, frames=6)
    return document


def by_x(document: dict[str, Any]) -> dict[float, dict[str, Any]]:
    return {shape["points"][0]: shape for shape in document["shapes"]}


def tracks_by_type(document: dict[str, Any]) -> dict[str, dict[str, Any]]:
    return {track["shape_type"]: track for track in document["tracks"]}


class TestANativeBackup:
    async def test_restores_each_annotation_s_provenance(
        self, owner: ApiActor, organization: dict[str, Any]
    ) -> None:
        document = await restored(owner, organization, "curvevision", "provenance")

        shapes = by_x(document)
        assert (shapes[10.0]["source"], shapes[10.0]["confidence"]) == ("manual", None)
        # Still a suggestion awaiting review, not something somebody already accepted.
        assert (shapes[60.0]["source"], shapes[60.0]["confidence"]) == ("model", 0.7)
        assert (shapes[110.0]["source"], shapes[110.0]["confidence"]) == ("model", None)
        assert tracks_by_type(document)["rectangle"]["source"] == "model_corrected"

    async def test_restores_a_tag_with_its_attributes_and_provenance(
        self, owner: ApiActor, organization: dict[str, Any]
    ) -> None:
        document = await restored(owner, organization, "curvevision", "tags")

        [tag] = document["tags"]
        assert tag["frame"] == 2
        assert tag["attributes"] == {"lighting": "night"}
        assert (tag["source"], tag["confidence"]) == ("model", 0.9)

    async def test_an_import_from_another_format_claims_no_provenance(
        self, owner: ApiActor, organization: dict[str, Any]
    ) -> None:
        """Only the native format wrote its `source` from an annotation's own; anything
        another tool wrote is stored as `imported`."""
        document = await restored(owner, organization, "cvat_xml", "foreign")

        assert {shape["source"] for shape in document["shapes"]} == {"imported"}
        assert {shape["confidence"] for shape in document["shapes"]} == {None}
        assert {track["source"] for track in document["tracks"]} == {"imported"}


class TestKeyframes:
    @pytest.mark.parametrize("format_id", ["curvevision", "cvat_xml"])
    async def test_a_track_comes_back_with_the_keyframes_it_was_drawn_with(
        self, owner: ApiActor, organization: dict[str, Any], format_id: str
    ) -> None:
        document = await restored(owner, organization, format_id, f"keys-{format_id}")

        tracks = tracks_by_type(document)
        car = tracks["rectangle"]
        assert [(s["frame"], s["outside"]) for s in car["shapes"]] == [
            (0, False),
            (3, False),
            (5, True),
        ]
        at = {s["frame"]: s["points"] for s in car["shapes"]}
        assert at[0] == CAR_AT_0
        assert at[3] == CAR_AT_3
        assert present_on(car) == [0, 1, 2, 3, 4]

    @pytest.mark.parametrize("format_id", ["curvevision", "cvat_xml"])
    async def test_a_polygon_that_gains_a_vertex_keeps_two_keyframes(
        self, owner: ApiActor, organization: dict[str, Any], format_id: str
    ) -> None:
        """Its frames between are resampled; CVAT XML writes them to two decimals, and the
        import still recognises them as what its own interpolation produces."""
        document = await restored(owner, organization, format_id, f"poly-{format_id}")

        polygon = tracks_by_type(document)["polygon"]
        assert [s["frame"] for s in polygon["shapes"]] == [0, 4]
        assert [s["points"] for s in polygon["shapes"]] == [TRIANGLE, SQUARE]


class TestAFrameMarkedInterpolated:
    async def test_is_kept_where_interpolation_would_put_the_object_elsewhere(
        self, owner: ApiActor, project: dict[str, Any]
    ) -> None:
        """`keyframe="0"` is a claim about how the frame was made, not licence to move the
        object: one interpolation here does not reproduce is stored as a keyframe."""
        archive = cvat_track(box(0, 10), box(1, 50, keyframe=0), box(2, 30))

        _result, document = await import_into(owner, project, "cvat_xml", archive, frames=6)

        [track] = document["tracks"]
        assert [(s["frame"], s["points"][0]) for s in track["shapes"]] == [
            (0, 10.0),
            (1, 50.0),
            (2, 30.0),
        ]
