"""What an import keeps: attributes, and a track's identity across frames.

The readers produce both — `ShapeRecord.attributes`, and `track_id` on every per-frame
record of a track — and the import route threw both away: every imported shape was stored
with `attributes={}`, and a track became one unrelated box per frame. The README's
round-trip table said CVAT XML carries attributes and tracks, MOTChallenge carries object
identity, and the native format carries "everything, losslessly"; on the way in, none did.

A track is rebuilt with a keyframe on every frame the file lists it on. What a frame it does
not list means is the format's to say: in a per-frame format (MOTChallenge, the native
format) the object is gone, and an `outside` keyframe goes wherever it stops being listed;
in CVAT XML the object is interpolated across it, as CVAT's own reader has it, and leaves
only where the file says `outside="1"` (iteration 71).
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
        """Present on 0 and 1, gone on 2 and 3, back on 4 — and still there on 5.

        As CVAT reads it: the `keyframe="0"` box on frame 1 is dropped and interpolated from
        the keyframes either side (here: held, since the next one is a departure), and after
        its last keyframe a track nothing closes stays where it was to the end of the job.
        Until iteration 71 frame 1 became a keyframe and the track was ended after frame 4.
        """
        result, document = await import_into(
            owner, project, "cvat_xml", cvat_archive(self.XML), frames=6
        )

        [track] = document["tracks"]
        assert present_on(track) == [0, 1, 4, 5]
        assert track["attributes"] == {"colour": "black"}
        assert {s["frame"]: s["outside"] for s in track["shapes"]} == {
            0: False,
            2: True,
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


def cvat_track(*boxes: str, track_id: int = 3, label: str = "car") -> bytes:
    """A CVAT XML file holding one track of `boxes`, each a `<box …/>` element's attributes."""
    body = "\n".join(f'    <box occluded="0" {box}/>' for box in boxes)
    return cvat_archive(
        f"""<?xml version="1.0" encoding="utf-8"?>
<annotations>
  <version>1.1</version>
  <track id="{track_id}" label="{label}">
{body}
  </track>
</annotations>
"""
    )


def box(frame: int, x: float, *, keyframe: int = 1, outside: int = 0) -> str:
    return (
        f'frame="{frame}" keyframe="{keyframe}" outside="{outside}" '
        f'xtl="{x}" ytl="10" xbr="{x + 40}" ybr="50"'
    )


async def exported_boxes(actor: ApiActor, project: dict[str, Any]) -> dict[int, list[float]]:
    """Where the project's one track is on each frame, from a CVAT XML export of it.

    The export materialises the track frame by frame, so it is the server's own reading of
    the keyframes the import stored: the positions in between, and the frames it is on.
    """
    import xml.etree.ElementTree as ET

    export = await actor.post(
        f"/api/v1/projects/{project['id']}/export", json={"format": "cvat_xml"}
    )
    assert export.status_code == 200, export.text
    with zipfile.ZipFile(io.BytesIO(export.content)) as archive:
        root = ET.fromstring(archive.read("annotations.xml"))
    return {
        int(element.get("frame", "-1")): [
            float(element.get(key, "0")) for key in ("xtl", "ytl", "xbr", "ybr")
        ]
        for track in root.findall("track")
        for element in track
        if element.get("outside") != "1"
    }


class TestCvatKeyframes:
    """A CVAT track lists keyframes; the frames between them are interpolated.

    CVAT's reader (`load_anno` in `cvat/apps/dataset_manager/formats/cvat.py`) keeps only
    the shapes marked `keyframe="1"`, and its tracks interpolate between them and leave
    only at an `outside` keyframe. CVAT's own exports list every frame, which hid the
    difference; a file written by hand, by a script or by another tool lists only its
    keyframes, and every frame between two of them used to come back empty.
    """

    async def test_frames_between_two_keyframes_are_interpolated(
        self, owner: ApiActor, project: dict[str, Any]
    ) -> None:
        _result, document = await import_into(
            owner, project, "cvat_xml", cvat_track(box(0, 10), box(4, 50)), frames=6
        )

        [track] = document["tracks"]
        assert [(s["frame"], s["outside"]) for s in track["shapes"]] == [(0, False), (4, False)]
        assert present_on(track) == [0, 1, 2, 3, 4, 5]
        positions = await exported_boxes(owner, project)
        assert positions[2] == [30.0, 10.0, 70.0, 50.0]  # halfway
        assert positions[5] == [50.0, 10.0, 90.0, 50.0]  # held after the last keyframe

    async def test_an_interpolated_frame_in_the_file_is_not_made_a_keyframe(
        self, owner: ApiActor, project: dict[str, Any]
    ) -> None:
        """CVAT writes the frames between keyframes too, marked `keyframe="0"`."""
        archive = cvat_track(box(0, 10), box(1, 20, keyframe=0), box(2, 30))

        _result, document = await import_into(owner, project, "cvat_xml", archive, frames=6)

        [track] = document["tracks"]
        assert [s["frame"] for s in track["shapes"]] == [0, 2]

    async def test_an_outside_keyframe_ends_it_and_a_later_one_brings_it_back(
        self, owner: ApiActor, project: dict[str, Any]
    ) -> None:
        archive = cvat_track(box(0, 10), box(2, 30, outside=1), box(4, 50), box(5, 60, outside=1))

        _result, document = await import_into(owner, project, "cvat_xml", archive, frames=8)

        [track] = document["tracks"]
        assert present_on(track, stop=8) == [0, 1, 4]
        positions = await exported_boxes(owner, project)
        # Held up to the departure, not drawn towards the departure's stale coordinates.
        assert positions[1] == [10.0, 10.0, 50.0, 50.0]

    async def test_a_track_crossing_into_another_job_carries_on_moving_there(
        self, owner: ApiActor, project: dict[str, Any]
    ) -> None:
        """Split as CVAT's `_slice_track` splits it: each job's part gets the interpolated
        position on the boundary, so neither part stops early nor starts late."""
        task, jobs = await make_task(owner, project, frames=6, segment_size=3)
        imported = await owner.post(
            f"/api/v1/tasks/{task['id']}/import?format=cvat_xml",
            files={"file": ("import.zip", cvat_track(box(0, 10), box(4, 50)), "application/zip")},
        )
        assert imported.status_code == 200, imported.text
        assert imported.json()["tracks_imported"] == 2

        first, second = [
            (await owner.get(f"/api/v1/jobs/{job['id']}/annotations")).json()["tracks"]
            for job in jobs
        ]
        [early] = first
        [late] = second
        assert [(s["frame"], s["points"][0]) for s in early["shapes"]] == [(0, 10.0), (2, 30.0)]
        assert [(s["frame"], s["points"][0]) for s in late["shapes"]] == [(3, 40.0), (4, 50.0)]
        assert present_on(early, stop=3) == [0, 1, 2]
        assert present_on(late, stop=6) == [3, 4, 5]

    async def test_one_keyframe_closed_at_once_is_a_shape_and_one_left_open_a_track(
        self, owner: ApiActor, project: dict[str, Any]
    ) -> None:
        """A one-frame track closed on the next frame is how CVAT writes a plain shape in a
        tracked dump; one nothing closes is an object that stays to the end of the job."""
        closed = cvat_track(box(1, 10), box(2, 10, outside=1))
        _result, document = await import_into(owner, project, "cvat_xml", closed, frames=6)
        assert [s["frame"] for s in document["shapes"]] == [1]
        assert document["tracks"] == []

        open_ended = cvat_track(box(3, 10))
        _result, document = await import_into(owner, project, "cvat_xml", open_ended, frames=6)
        [track] = document["tracks"]
        assert present_on(track) == [3, 4, 5]
        assert document["shapes"] == []


class TestATrackWithAGap:
    """A track that leaves and comes back, through each format that carries tracks."""

    @pytest.mark.parametrize("format_id", ["curvevision", "cvat_xml", "mot"])
    async def test_it_comes_back_absent_where_it_was_absent(
        self, owner: ApiActor, organization: dict[str, Any], format_id: str
    ) -> None:
        """For CVAT XML this needs the export's own `outside` at the gap: CVAT reads a
        frame a track does not list as interpolated, so until iteration 71 — when only the
        end of a track was marked — the car came back gliding through frames 2 and 3."""
        source = await street_project(owner, organization, f"gap-source-{format_id}")
        _task, jobs = await make_task(owner, source, frames=6)
        car = label_of(source, "car")
        drawn = await owner.patch(
            f"/api/v1/jobs/{jobs[0]['id']}/annotations",
            json={
                "created_tracks": [
                    {
                        "label_id": car,
                        "shape_type": "rectangle",
                        "shapes": [
                            {"frame": 0, "shape_type": "rectangle", "points": BOX_AT_0},
                            {
                                "frame": 2,
                                "shape_type": "rectangle",
                                "points": BOX_AT_0,
                                "outside": True,
                            },
                            {"frame": 4, "shape_type": "rectangle", "points": BOX_AT_2},
                        ],
                    }
                ]
            },
        )
        assert drawn.status_code == 200, drawn.text
        export = await owner.post(
            f"/api/v1/projects/{source['id']}/export", json={"format": format_id}
        )
        assert export.status_code == 200, export.text
        target = await street_project(owner, organization, f"gap-target-{format_id}")

        _result, document = await import_into(owner, target, format_id, export.content, frames=6)

        [track] = document["tracks"]
        assert present_on(track) == [0, 1, 4, 5]
