"""What an import keeps: attributes, and a track's identity across frames.

The readers produce both — `ShapeRecord.attributes`, and `track_id` on every per-frame
record of a track — and the import route threw both away: every imported shape was stored
with `attributes={}`, and a track became one unrelated box per frame. The README's
round-trip table said CVAT XML carries attributes and tracks, MOTChallenge carries object
identity, and the native format carries "everything, losslessly"; on the way in, none did.

A track is rebuilt with a keyframe on every frame the file lists it on, and an `outside`
keyframe wherever it stops being listed — between two runs, and after the last — because the
server holds a track's last position to the end of the job otherwise.
"""

from __future__ import annotations

import io
import zipfile
from typing import Any

import pytest

from tests.api.test_collaboration import make_task
from tests.conftest import ApiActor

BOX_AT_0 = [10.0, 10.0, 50.0, 50.0]
BOX_AT_2 = [30.0, 10.0, 70.0, 50.0]


async def street_project(actor: ApiActor, organization: dict[str, Any], slug: str):
    """The `project` fixture's schema, again: `car` with `colour` and a mutable `parked`."""
    slug = slug.replace("_", "-")
    created = await actor.post(
        "/api/v1/projects",
        json={
            "organization_id": organization["id"],
            "slug": slug,
            "name": slug,
            "labels": [
                {
                    "name": "car",
                    "attributes": [
                        {
                            "name": "colour",
                            "attribute_type": "select",
                            "values": ["red", "blue", "black"],
                        },
                        {"name": "parked", "attribute_type": "checkbox", "mutable": True},
                    ],
                },
                {"name": "pedestrian"},
            ],
        },
    )
    assert created.status_code == 201, created.text
    return created.json()


def label_of(project: dict[str, Any], name: str) -> str:
    return next(label["id"] for label in project["labels"] if label["name"] == name)


async def import_into(
    actor: ApiActor, project: dict[str, Any], format_id: str, archive: bytes, *, frames: int
) -> tuple[dict[str, Any], dict[str, Any]]:
    task, jobs = await make_task(actor, project, frames=frames)
    imported = await actor.post(
        f"/api/v1/tasks/{task['id']}/import?format={format_id}",
        files={"file": ("import.zip", archive, "application/zip")},
    )
    assert imported.status_code == 200, imported.text
    document = (await actor.get(f"/api/v1/jobs/{jobs[0]['id']}/annotations")).json()
    return imported.json(), document


def present_on(track: dict[str, Any], *, stop: int = 6) -> list[int]:
    """The frames a track is on, from its keyframes, as the server interpolates them.

    `stop` is one past the job's last frame: past its last keyframe a track holds there.
    """
    keyframes = sorted(track["shapes"], key=lambda shape: shape["frame"])
    frames: list[int] = []
    for keyframe, following in zip(keyframes, [*keyframes[1:], None], strict=True):
        if keyframe["outside"]:
            continue
        end = following["frame"] if following else stop
        frames.extend(range(keyframe["frame"], end))
    return frames


class TestRoundTrips:
    async def draw_and_export(
        self, actor: ApiActor, organization: dict[str, Any], format_id: str
    ) -> bytes:
        """A parked-then-moving red car tracked over frames 0 to 3 of six, and a blue box."""
        source = await street_project(actor, organization, f"source-{format_id}")
        _task, jobs = await make_task(actor, source, frames=6)
        car = label_of(source, "car")
        drawn = await actor.patch(
            f"/api/v1/jobs/{jobs[0]['id']}/annotations",
            json={
                "created_shapes": [
                    {
                        "label_id": car,
                        "frame": 5,
                        "shape_type": "rectangle",
                        "points": [100, 100, 140, 140],
                        "attributes": {"colour": "blue", "parked": True},
                    }
                ],
                "created_tracks": [
                    {
                        "label_id": car,
                        "shape_type": "rectangle",
                        "attributes": {"colour": "red"},
                        "shapes": [
                            {
                                "frame": 0,
                                "shape_type": "rectangle",
                                "points": BOX_AT_0,
                                "attributes": {"parked": True},
                            },
                            {
                                "frame": 2,
                                "shape_type": "rectangle",
                                "points": BOX_AT_2,
                                "attributes": {"parked": False},
                            },
                            {
                                "frame": 4,
                                "shape_type": "rectangle",
                                "points": BOX_AT_2,
                                "outside": True,
                            },
                        ],
                    }
                ],
            },
        )
        assert drawn.status_code == 200, drawn.text
        export = await actor.post(
            f"/api/v1/projects/{source['id']}/export", json={"format": format_id}
        )
        assert export.status_code == 200, export.text
        return export.content

    @pytest.mark.parametrize("format_id", ["curvevision", "cvat_xml", "mot"])
    async def test_a_track_comes_back_as_one_track_on_the_frames_it_was_on(
        self, owner: ApiActor, organization: dict[str, Any], format_id: str
    ) -> None:
        archive = await self.draw_and_export(owner, organization, format_id)
        target = await street_project(owner, organization, f"target-{format_id}")

        result, document = await import_into(owner, target, format_id, archive, frames=6)

        [track] = document["tracks"]
        assert track["label_id"] == label_of(target, "car")
        assert present_on(track) == [0, 1, 2, 3]
        at = {shape["frame"]: shape for shape in track["shapes"]}
        assert at[0]["points"] == BOX_AT_0
        assert at[2]["points"] == BOX_AT_2
        assert result["tracks_imported"] == 1

    async def test_a_track_crossing_into_another_job_is_split_there(
        self, owner: ApiActor, organization: dict[str, Any]
    ) -> None:
        """A track belongs to one job; one that spans two becomes one in each."""
        archive = await self.draw_and_export(owner, organization, "curvevision")
        target = await street_project(owner, organization, "split-target")
        task, jobs = await make_task(owner, target, frames=6, segment_size=3)
        assert [(job["start_frame"], job["stop_frame"]) for job in jobs] == [(0, 2), (3, 5)]

        imported = await owner.post(
            f"/api/v1/tasks/{task['id']}/import?format=curvevision",
            files={"file": ("import.zip", archive, "application/zip")},
        )

        assert imported.status_code == 200, imported.text
        assert imported.json()["tracks_imported"] == 2
        first, second = [
            (await owner.get(f"/api/v1/jobs/{job['id']}/annotations")).json()["tracks"]
            for job in jobs
        ]
        assert [present_on(track, stop=3) for track in first] == [[0, 1, 2]]
        assert [present_on(track, stop=6) for track in second] == [[3]]

    @pytest.mark.parametrize("format_id", ["curvevision", "cvat_xml"])
    async def test_attributes_come_back_on_shapes_tracks_and_keyframes(
        self, owner: ApiActor, organization: dict[str, Any], format_id: str
    ) -> None:
        archive = await self.draw_and_export(owner, organization, format_id)
        target = await street_project(owner, organization, f"attrs-{format_id}")

        _result, document = await import_into(owner, target, format_id, archive, frames=6)

        [shape] = document["shapes"]
        assert shape["attributes"] == {"colour": "blue", "parked": True}
        [track] = document["tracks"]
        # Immutable on the track, mutable on each keyframe: as the editor writes them.
        assert track["attributes"] == {"colour": "red"}
        parked = {
            keyframe["frame"]: keyframe["attributes"].get("parked")
            for keyframe in track["shapes"]
            if not keyframe["outside"]
        }
        assert parked == {0: True, 1: True, 2: False, 3: False}


def cvat_archive(xml: str) -> bytes:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        archive.writestr("annotations.xml", xml)
    return buffer.getvalue()


class TestACvatFile:
    """A file as CVAT writes one, rather than one this project exported."""

    XML = """<?xml version="1.0" encoding="utf-8"?>
<annotations>
  <version>1.1</version>
  <image id="0" name="f0.png">
    <box label="car" occluded="0" xtl="1" ytl="1" xbr="9" ybr="9" z_order="0">
      <attribute name="colour">purple</attribute>
      <attribute name="speed">fast</attribute>
      <attribute name="parked">true</attribute>
    </box>
  </image>
  <track id="7" label="car">
    <box frame="0" keyframe="1" outside="0" occluded="0" xtl="10" ytl="10" xbr="50" ybr="50">
      <attribute name="colour">black</attribute>
    </box>
    <box frame="1" keyframe="0" outside="0" occluded="1" xtl="20" ytl="10" xbr="60" ybr="50">
      <attribute name="colour">black</attribute>
    </box>
    <box frame="2" keyframe="1" outside="1" occluded="0" xtl="20" ytl="10" xbr="60" ybr="50"/>
    <box frame="4" keyframe="1" outside="0" occluded="0" xtl="40" ytl="10" xbr="80" ybr="50">
      <attribute name="colour">black</attribute>
    </box>
  </track>
</annotations>
"""

    async def test_a_track_that_leaves_and_comes_back_is_absent_in_between(
        self, owner: ApiActor, project: dict[str, Any]
    ) -> None:
        """Present on 0 and 1, gone on 2 and 3, back on 4, and gone after its last listed frame."""
        result, document = await import_into(
            owner, project, "cvat_xml", cvat_archive(self.XML), frames=6
        )

        [track] = document["tracks"]
        assert present_on(track) == [0, 1, 4]
        assert track["attributes"] == {"colour": "black"}
        assert {s["frame"]: s["occluded"] for s in track["shapes"] if not s["outside"]} == {
            0: False,
            1: True,
            4: False,
        }
        assert result["tracks_imported"] == 1

    async def test_values_the_project_would_refuse_are_dropped_and_named(
        self, owner: ApiActor, project: dict[str, Any]
    ) -> None:
        """Kept: a value the label declares and accepts. Dropped, and said: the rest.

        CVAT's own importer keeps only the attributes a label declares, unless
        `soft_attribute_import` is switched on. Storing one here would make the annotation's
        next save fail, because every save validates attributes against the label.
        """
        result, document = await import_into(
            owner, project, "cvat_xml", cvat_archive(self.XML), frames=6
        )

        [shape] = document["shapes"]
        assert shape["attributes"] == {"parked": True}
        warnings = " ".join(result["warnings"])
        assert "speed" in warnings
        assert "colour" in warnings and "purple" in warnings
