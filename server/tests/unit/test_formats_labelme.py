"""LabelMe JSON: the per-image files of the `labelme` annotation tool.

One JSON file beside each image, with its shapes in pixel coordinates. It is what a great
many individual annotators already have on disk, which makes import the half that matters
most: their work arriving intact. Export is held to the same standard by reading it back
here, and — in `scripts/verify_labelme.py`, run in CI — with labelme's own loader.

Written from the file format; no code from labelme (GPL-3.0) is used.
"""

from __future__ import annotations

import base64
import io
import json

import pytest

from curvevision.domain.enums import AttributeType, ShapeType
from curvevision.formats import (
    AttributeSpec,
    DatasetView,
    FrameRecord,
    ImportContext,
    LabelSpec,
    MemoryExportSink,
    MemoryImportSource,
    ShapeRecord,
    TagRecord,
    get_format,
)
from curvevision.formats.rle import encode as rle_encode

FORMAT = get_format("labelme")

LABELS = [
    LabelSpec(
        id="1",
        name="car",
        attributes=(
            AttributeSpec(name="parked", attribute_type=AttributeType.CHECKBOX),
            AttributeSpec(name="description", attribute_type=AttributeType.TEXT),
            AttributeSpec(
                name="colour", attribute_type=AttributeType.SELECT, values=("red", "blue")
            ),
        ),
    ),
    LabelSpec(id="2", name="road"),
    LabelSpec(id="3", name="sign"),
    LabelSpec(id="4", name="night"),
]


def exported(frames: list[FrameRecord]) -> MemoryExportSink:
    sink = MemoryExportSink()
    FORMAT.export(DatasetView(name="Street", labels=LABELS, frames=iter(frames)), sink)
    return sink


def document(sink: MemoryExportSink, path: str) -> dict:
    return json.loads(sink.files[path].decode("utf-8"))


def context(
    names: dict[str, int] | None = None, known: dict[str, str] | None = None
) -> ImportContext:
    names = names or {"a.jpg": 0, "b.png": 1}
    return ImportContext(
        known_labels=known or {},
        frame_by_name=names,
        frame_count=len(names),
        frame_sizes=dict.fromkeys(range(len(names)), (640, 480)),
    )


def frame(
    index: int, name: str, *shapes: ShapeRecord, tags: list[str] | None = None
) -> FrameRecord:
    return FrameRecord(
        index=index,
        name=name,
        width=640,
        height=480,
        shapes=list(shapes),
        tags=list(tags or []),
        tag_records=[TagRecord(label=tag) for tag in tags or []],
    )


def shape(
    kind: ShapeType, points: list[float], label: str = "car", **kwargs: object
) -> ShapeRecord:
    return ShapeRecord(label=label, shape_type=kind, points=points, **kwargs)


def round_trip(frames: list[FrameRecord]) -> list[tuple[int, ShapeRecord]]:
    sink = exported(frames)
    result = FORMAT.import_(MemoryImportSource(dict(sink.files)), context())
    return sorted(result.shapes, key=lambda pair: (pair[0], pair[1].label, pair[1].points))


class TestExport:
    def test_one_file_beside_each_image_naming_it(self) -> None:
        sink = exported([frame(0, "a.jpg"), frame(1, "nested/b.png")])

        first = document(sink, "a.json")
        assert first["imagePath"] == "a.jpg"
        assert (first["imageWidth"], first["imageHeight"]) == (640, 480)
        assert first["shapes"] == [] and first["flags"] == {} and first["imageData"] is None
        assert document(sink, "nested/b.json")["imagePath"] == "b.png"

    def test_two_images_with_one_stem_do_not_share_a_file(self) -> None:
        sink = exported([frame(0, "a.jpg"), frame(1, "a.png")])

        paths = sorted(p for p in sink.files if p.endswith(".json"))
        assert len(paths) == 2
        assert {document(sink, p)["imagePath"] for p in paths} == {"a.jpg", "a.png"}

    def test_each_geometry_in_labelmes_own_terms(self) -> None:
        sink = exported(
            [
                frame(
                    0,
                    "a.jpg",
                    shape(ShapeType.RECTANGLE, [30, 40, 10, 20]),
                    shape(ShapeType.POLYGON, [0, 0, 10, 0, 10, 10], label="road"),
                    shape(ShapeType.POLYLINE, [0, 0, 5, 5, 9, 2], label="road"),
                    shape(ShapeType.ELLIPSE, [50, 60, 7, 7], label="sign"),
                    shape(ShapeType.POINTS, [1, 2], label="sign"),
                )
            ]
        )

        shapes = document(sink, "a.json")["shapes"]
        kinds = {(s["label"], s["shape_type"]): s["points"] for s in shapes}
        # Corners normalised: labelme draws from the first point to the second.
        assert kinds[("car", "rectangle")] == [[10, 20], [30, 40]]
        assert kinds[("road", "polygon")] == [[0, 0], [10, 0], [10, 10]]
        assert kinds[("road", "linestrip")] == [[0, 0], [5, 5], [9, 2]]
        # A circle is its centre and a point on its edge.
        assert kinds[("sign", "circle")] == [[50, 60], [57, 60]]
        assert kinds[("sign", "point")] == [[1, 2]]
        for written in shapes:
            assert set(written) >= {
                "label",
                "points",
                "group_id",
                "shape_type",
                "flags",
                "description",
            }

    def test_a_rotated_box_is_an_oriented_rectangle_of_its_real_corners(self) -> None:
        sink = exported(
            [frame(0, "a.jpg", shape(ShapeType.ROTATED_RECTANGLE, [0, 40, 100, 60], rotation=90))]
        )

        [written] = document(sink, "a.json")["shapes"]
        assert written["shape_type"] == "oriented_rectangle"
        xs = [x for x, _ in written["points"]]
        ys = [y for _, y in written["points"]]
        # 100 wide and 20 tall, turned upright about its centre (50, 50).
        assert max(xs) - min(xs) == pytest.approx(20)
        assert max(ys) - min(ys) == pytest.approx(100)

    def test_an_ellipse_that_is_not_a_circle_becomes_a_polygon_not_a_circle(self) -> None:
        sink = exported([frame(0, "a.jpg", shape(ShapeType.ELLIPSE, [50, 50, 20, 10]))])

        [written] = document(sink, "a.json")["shapes"]
        assert written["shape_type"] == "polygon"
        xs = [x for x, _ in written["points"]]
        ys = [y for _, y in written["points"]]
        assert (min(xs), max(xs)) == (pytest.approx(30), pytest.approx(70))
        assert (min(ys), max(ys)) == (pytest.approx(40), pytest.approx(60))

    def test_several_points_stay_one_object_through_a_group(self) -> None:
        sink = exported(
            [
                frame(
                    0,
                    "a.jpg",
                    shape(ShapeType.POINTS, [1, 2, 3, 4], label="sign"),
                    shape(ShapeType.RECTANGLE, [0, 0, 5, 5], group=1),
                )
            ]
        )

        shapes = document(sink, "a.json")["shapes"]
        points = [s for s in shapes if s["shape_type"] == "point"]
        assert [p["points"] for p in points] == [[[1, 2]], [[3, 4]]]
        # Their own group, not one an annotator already used for something else.
        assert points[0]["group_id"] == points[1]["group_id"] != 1

    def test_checkboxes_are_flags_the_description_is_the_description_tags_are_image_flags(
        self,
    ) -> None:
        sink = exported(
            [
                frame(
                    0,
                    "a.jpg",
                    shape(
                        ShapeType.RECTANGLE,
                        [0, 0, 5, 5],
                        group=3,
                        attributes={
                            "parked": True,
                            "description": "behind the van",
                            "colour": "red",
                        },
                    ),
                    tags=["night"],
                )
            ]
        )

        written = document(sink, "a.json")
        [box] = written["shapes"]
        assert box["flags"] == {"parked": True}
        assert box["description"] == "behind the van"
        assert box["group_id"] == 3
        assert written["flags"] == {"night": True}

    def test_skeletons_and_cuboids_are_left_out_rather_than_misdrawn(self) -> None:
        sink = exported(
            [frame(0, "a.jpg", shape(ShapeType.CUBOID, [0.0] * 16), shape(ShapeType.SKELETON, []))]
        )

        assert document(sink, "a.json")["shapes"] == []

    def test_the_images_travel_with_their_files(self) -> None:
        image = FrameRecord(index=0, name="a.jpg", width=640, height=480, media=b"jpeg bytes")
        sink = exported([image])

        assert sink.files["a.jpg"] == b"jpeg bytes"


class TestImport:
    def test_labelmes_own_shapes_arrive_as_ours(self) -> None:
        files = {
            "a.json": {
                "version": "5.4.1",
                "flags": {"night": True, "rain": False},
                "imagePath": "..\\images\\a.jpg",
                "imageData": None,
                "imageHeight": 480,
                "imageWidth": 640,
                "shapes": [
                    {
                        "label": "car",
                        "points": [[30, 40], [10, 20]],
                        "shape_type": "rectangle",
                        "group_id": 2,
                        "flags": {"parked": True},
                        "description": "rear",
                    },
                    {
                        "label": "road",
                        "points": [[0, 0], [9, 0], [9, 9]],
                        "shape_type": "polygon",
                        "group_id": None,
                        "flags": {},
                    },
                    {"label": "road", "points": [[0, 0], [4, 4]], "shape_type": "line"},
                    {
                        "label": "road",
                        "points": [[0, 0], [4, 4], [8, 0]],
                        "shape_type": "linestrip",
                    },
                    {"label": "sign", "points": [[50, 60], [50, 67]], "shape_type": "circle"},
                    {"label": "sign", "points": [[5, 6]], "shape_type": "point"},
                ],
            }
        }
        result = FORMAT.import_(
            MemoryImportSource({k: json.dumps(v).encode() for k, v in files.items()}), context()
        )

        assert result.frames_matched == 1
        by_kind = {(s.label, s.shape_type): s for _, s in result.shapes}
        car = by_kind[("car", ShapeType.RECTANGLE)]
        assert car.points == [10, 20, 30, 40]
        assert car.group == 2
        assert car.attributes == {"parked": True, "description": "rear"}
        assert by_kind[("road", ShapeType.POLYGON)].points == [0, 0, 9, 0, 9, 9]
        polylines = [s for _, s in result.shapes if s.shape_type is ShapeType.POLYLINE]
        assert sorted(len(s.points) for s in polylines) == [4, 6]  # a line is a short polyline
        assert by_kind[("sign", ShapeType.ELLIPSE)].points == [50, 60, 7, 7]
        assert by_kind[("sign", ShapeType.POINTS)].points == [5, 6]
        # A flag that is set is a tag on the image; one that is not set says nothing.
        assert [tag for _, tag in result.tags] == ["night"]
        made = {label.name: label for label in result.new_labels}
        assert set(made) == {"car", "road", "sign", "night"}
        assert {(a.name, a.attribute_type) for a in made["car"].attributes} == {
            ("parked", AttributeType.CHECKBOX),
            ("description", AttributeType.TEXT),
        }

    def test_an_oriented_rectangle_comes_back_as_a_rotated_box(self) -> None:
        [(_, back)] = round_trip(
            [frame(0, "a.jpg", shape(ShapeType.ROTATED_RECTANGLE, [0, 40, 100, 60], rotation=30))]
        )

        assert back.shape_type is ShapeType.ROTATED_RECTANGLE
        assert back.rotation == pytest.approx(30, abs=0.01)
        assert back.points == pytest.approx([0, 40, 100, 60], abs=0.01)

    def test_what_it_writes_it_reads(self) -> None:
        shapes = [
            shape(ShapeType.RECTANGLE, [10, 20, 30, 40], attributes={"parked": True}),
            shape(ShapeType.POLYGON, [0, 0, 10, 0, 10, 10], label="road", group=4),
            shape(ShapeType.POLYLINE, [0, 0, 5, 5, 9, 2], label="road"),
            shape(ShapeType.ELLIPSE, [50, 60, 7, 7], label="sign"),
        ]
        back = round_trip([frame(0, "a.jpg", *shapes)])

        assert [(i, s.label, s.shape_type, s.points, s.group) for i, s in back] == sorted(
            [(0, s.label, s.shape_type, s.points, s.group) for s in shapes],
            key=lambda row: (row[0], row[1], row[3]),
        )
        assert next(s for _, s in back if s.shape_type is ShapeType.RECTANGLE).attributes == {
            "parked": True
        }

    def test_a_file_is_matched_to_its_frame_by_the_image_it_names(self) -> None:
        body = {
            "imagePath": "photos/b.png",
            "imageHeight": 480,
            "imageWidth": 640,
            "shapes": [{"label": "car", "points": [[0, 0], [5, 5]], "shape_type": "rectangle"}],
        }
        result = FORMAT.import_(
            MemoryImportSource({"labels/whatever.json": json.dumps(body).encode()}), context()
        )

        assert [index for index, _ in result.shapes] == [1]

    def test_what_cannot_be_read_is_reported_and_the_rest_imported(self) -> None:
        files = {
            "a.json": json.dumps(
                {
                    "imagePath": "a.jpg",
                    "shapes": [
                        {"label": "car", "points": [[0, 0], [5, 5]], "shape_type": "rectangle"},
                        {"label": "car", "points": [[0, 0]], "shape_type": "hexagon"},
                        {"label": "car", "points": "nonsense", "shape_type": "polygon"},
                        {"label": "car", "points": [[0, 0], [1, 1]], "shape_type": "polygon"},
                    ],
                }
            ).encode(),
            "nobody.json": json.dumps({"imagePath": "nobody.jpg", "shapes": []}).encode(),
            "notes.json": b"not json at all",
            "config.json": json.dumps({"something": "else"}).encode(),
        }
        result = FORMAT.import_(MemoryImportSource(files), context())

        assert len(result.shapes) == 1
        said = "\n".join(result.warnings)
        assert "hexagon" in said
        assert "nobody.jpg" in said
        assert "notes.json" in said
        assert said.count("a.json") == 3  # the bad shape type, the bad points, the 2-point polygon
        assert "config.json" not in said  # not a labelme file: not this importer's business

    def test_labels_the_project_has_are_used_and_missing_ones_skipped_when_asked(self) -> None:
        body = {
            "imagePath": "a.jpg",
            "shapes": [
                {"label": "car", "points": [[0, 0], [5, 5]], "shape_type": "rectangle"},
                {"label": "tree", "points": [[0, 0], [5, 5]], "shape_type": "rectangle"},
            ],
        }
        ctx = context(known={"car": "1"})
        ctx.create_missing_labels = False
        result = FORMAT.import_(MemoryImportSource({"a.json": json.dumps(body).encode()}), ctx)

        assert [s.label for _, s in result.shapes] == ["car"]
        assert result.new_labels == []
        assert any("tree" in warning for warning in result.warnings)


class TestMasks:
    PIL = pytest.importorskip("PIL", reason="masks are PNGs")

    def test_a_mask_is_its_box_and_a_png_of_exactly_that_box(self) -> None:
        from PIL import Image

        # A 3x2 box at (10, 20) with an L of foreground.
        flags = [True, False, False, True, True, True]
        mask = {"rle": rle_encode(flags, 3, 2), "left": 10, "top": 20, "width": 3, "height": 2}
        sink = exported(
            [frame(0, "a.jpg", shape(ShapeType.MASK, [10, 20, 12, 21], mask=mask, label="road"))]
        )

        [written] = document(sink, "a.json")["shapes"]
        assert written["shape_type"] == "mask"
        assert written["points"] == [[10, 20], [12, 21]]
        png = Image.open(io.BytesIO(base64.b64decode(written["mask"])))
        assert png.size == (3, 2)
        assert [bool(png.getpixel((x, y))) for y in range(2) for x in range(3)] == flags

    def test_masks_come_back_pixel_for_pixel(self) -> None:
        flags = [False, True, True, False, True, False, False, False, True]
        mask = {"rle": rle_encode(flags, 3, 3), "left": 4, "top": 5, "width": 3, "height": 3}
        [(_, back)] = round_trip(
            [frame(0, "a.jpg", shape(ShapeType.MASK, [4, 5, 6, 7], mask=mask, label="road"))]
        )

        assert back.shape_type is ShapeType.MASK
        assert back.mask == mask
        assert back.points == [4, 5, 6, 7]

    def test_a_png_that_does_not_fit_its_box_is_refused(self) -> None:
        from PIL import Image

        buffer = io.BytesIO()
        Image.new("1", (4, 4), 1).save(buffer, format="PNG")
        body = {
            "imagePath": "a.jpg",
            "shapes": [
                {
                    "label": "road",
                    "points": [[0, 0], [1, 1]],
                    "shape_type": "mask",
                    "mask": base64.b64encode(buffer.getvalue()).decode(),
                }
            ],
        }
        result = FORMAT.import_(
            MemoryImportSource({"a.json": json.dumps(body).encode()}), context()
        )

        assert result.shapes == []
        assert any("4x4" in warning and "2x2" in warning for warning in result.warnings)
