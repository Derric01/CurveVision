"""Cityscapes: the ``gtFine`` polygons and label images, and the image beside them.

The polygons file is the vector truth and the PNGs are drawn from it, as the dataset's own
were. `scripts/verify_cityscapes.py`, run in CI, holds every PNG here to cityscapesScripts'
own drawing of the polygons file beside it and has Datumaro read the export; these tests pin
what that check cannot see — which shape wins a pixel, which classes are written how — and
the import.

Written from the dataset's published layout and label table; no code from cityscapesScripts
or Datumaro is used.
"""

from __future__ import annotations

import io
import json

import pytest

from curvevision.domain.enums import ShapeType
from curvevision.formats import (
    DatasetView,
    FrameRecord,
    ImportContext,
    LabelSpec,
    MemoryExportSink,
    MemoryImportSource,
    ShapeRecord,
    get_format,
)
from curvevision.formats.cityscapes import CITYSCAPES_CLASSES
from curvevision.formats.rle import decode as rle_decode
from curvevision.formats.rle import encode as rle_encode

Image = pytest.importorskip("PIL.Image")

FORMAT = get_format("cityscapes")
WIDTH, HEIGHT = 40, 30
ID = {entry.name: entry for entry in CITYSCAPES_CLASSES}


def labels(*names: str) -> list[LabelSpec]:
    return [LabelSpec(id=str(i), name=name) for i, name in enumerate(names, 1)]


def exported(
    frames: list[FrameRecord], label_specs: list[LabelSpec] | None = None
) -> MemoryExportSink:
    sink = MemoryExportSink()
    specs = label_specs or labels("road", "car", "person", "cargroup", "license plate")
    FORMAT.export(DatasetView(name="Street", labels=specs, frames=iter(frames)), sink)
    return sink


def frame(*shapes: ShapeRecord, name: str = "aachen/a.png", media: bytes | None = None):
    return FrameRecord(0, name, WIDTH, HEIGHT, shapes=list(shapes), media=media)


def png(sink: MemoryExportSink, path: str):
    image = Image.open(io.BytesIO(sink.files[path]))
    return image


def value(sink: MemoryExportSink, path: str, x: int, y: int) -> int | tuple[int, ...]:
    image = png(sink, path)
    return (
        image.convert("I").getpixel((x, y))
        if image.mode.startswith("I")
        else image.getpixel((x, y))
    )


def polygons(sink: MemoryExportSink, path: str = "gtFine/default/aachen/a_gtFine_polygons.json"):
    return json.loads(sink.files[path])


def gt(kind: str, stem: str = "aachen/a") -> str:
    return f"gtFine/default/{stem}_gtFine_{kind}.png"


class TestExport:
    def test_writes_each_file_of_the_layout_and_the_image_where_the_dataset_keeps_it(self) -> None:
        sink = exported([frame(media=b"png bytes")])
        assert sorted(sink.files) == sorted(
            [
                "gtFine/default/aachen/a_gtFine_polygons.json",
                gt("labelIds"),
                gt("instanceIds"),
                gt("color"),
                gt("labelTrainIds"),
                "leftImg8bit/default/aachen/a_leftImg8bit.png",
            ]
        )
        assert polygons(sink) == {"imgHeight": HEIGHT, "imgWidth": WIDTH, "objects": []}

    def test_cityscapes_classes_keep_their_own_ids_colours_and_training_ids(self) -> None:
        sink = exported([frame(ShapeRecord("road", ShapeType.RECTANGLE, [0, 0, 39, 29]))])
        assert value(sink, gt("labelIds"), 5, 5) == ID["road"].id == 7
        assert value(sink, gt("labelTrainIds"), 5, 5) == ID["road"].train_id == 0
        assert value(sink, gt("color"), 5, 5) == (128, 64, 128)
        assert value(sink, gt("instanceIds"), 5, 5) == 7  # the road has no instances
        assert "label_colors.txt" not in sink.files  # the dataset has no class list

    def test_unlabelled_pixels_are_zero_and_ignored_in_training(self) -> None:
        sink = exported([frame()])
        assert value(sink, gt("labelIds"), 1, 1) == 0
        assert value(sink, gt("labelTrainIds"), 1, 1) == 255

    def test_the_shape_in_front_wins_the_pixel(self) -> None:
        road = ShapeRecord("road", ShapeType.RECTANGLE, [0, 0, 39, 29], z_order=0)
        car = ShapeRecord("car", ShapeType.RECTANGLE, [10, 10, 20, 20], z_order=1)
        assert value(exported([frame(car, road)]), gt("labelIds"), 15, 15) == ID["car"].id
        road.z_order, car.z_order = 1, 0
        sink = exported([frame(car, road)])
        assert value(sink, gt("labelIds"), 15, 15) == ID["road"].id
        # ...and the polygons file lists them in the order they are drawn.
        assert [o["label"] for o in polygons(sink)["objects"]] == ["car", "road"]

    def test_instances_are_numbered_per_class_from_zero_and_a_crowd_has_no_number(self) -> None:
        sink = exported(
            [
                frame(
                    ShapeRecord("car", ShapeType.RECTANGLE, [0, 0, 5, 5]),
                    ShapeRecord("person", ShapeType.RECTANGLE, [20, 0, 25, 5]),
                    ShapeRecord("car", ShapeType.RECTANGLE, [10, 0, 15, 5]),
                    ShapeRecord("cargroup", ShapeType.RECTANGLE, [0, 20, 5, 25]),
                )
            ]
        )
        assert value(sink, gt("instanceIds"), 2, 2) == 26000
        assert value(sink, gt("instanceIds"), 12, 2) == 26001
        assert value(sink, gt("instanceIds"), 22, 2) == 24000
        assert value(sink, gt("instanceIds"), 2, 22) == 26  # a crowd of cars: the class only
        assert value(sink, gt("labelIds"), 2, 22) == ID["car"].id
        assert png(sink, gt("instanceIds")).mode == "I;16"  # as the dataset's own

    def test_a_license_plate_is_listed_but_never_drawn(self) -> None:
        sink = exported([frame(ShapeRecord("license plate", ShapeType.RECTANGLE, [0, 0, 9, 9]))])
        assert [o["label"] for o in polygons(sink)["objects"]] == ["license plate"]
        assert value(sink, gt("labelIds"), 4, 4) == 0

    def test_boxes_rotated_boxes_and_ellipses_are_written_as_the_polygons_they_are(self) -> None:
        sink = exported(
            [
                frame(
                    ShapeRecord("car", ShapeType.RECTANGLE, [12, 4, 2, 8]),
                    ShapeRecord("car", ShapeType.ROTATED_RECTANGLE, [10, 10, 20, 20], rotation=90),
                    ShapeRecord("person", ShapeType.ELLIPSE, [30, 15, 5, 3]),
                )
            ]
        )
        box, turned, ellipse = (o["polygon"] for o in polygons(sink)["objects"])
        assert box == [[2, 4], [12, 4], [12, 8], [2, 8]]
        assert sorted(map(tuple, turned)) == [(10, 10), (10, 20), (20, 10), (20, 20)]
        assert len(ellipse) == 36
        assert {round(x) for x, _ in ellipse} >= {25, 35}  # spans rx either side
        assert {round(y) for _, y in ellipse} >= {12, 18}  # and ry

    def test_lines_and_points_cover_no_area_and_are_left_out(self) -> None:
        sink = exported(
            [
                frame(
                    ShapeRecord("road", ShapeType.POLYLINE, [0, 0, 39, 29]),
                    ShapeRecord("car", ShapeType.POINTS, [5, 5]),
                )
            ]
        )
        assert polygons(sink)["objects"] == []
        assert value(sink, gt("labelIds"), 5, 5) == 0

    def test_a_mask_is_painted_pixel_for_pixel_and_has_no_polygon(self) -> None:
        flags = [True, False, True, True, True, False]  # 3x2, asymmetric
        mask = {"rle": rle_encode(flags, 3, 2), "left": 5, "top": 7, "width": 3, "height": 2}
        sink = exported([frame(ShapeRecord("car", ShapeType.MASK, [5, 7, 7, 8], mask=mask))])
        assert polygons(sink)["objects"] == []
        drawn = [value(sink, gt("labelIds"), 5 + i % 3, 7 + i // 3) for i in range(6)]
        assert drawn == [ID["car"].id if flag else 0 for flag in flags]
        assert value(sink, gt("instanceIds"), 5, 7) == 26000

    def test_a_projects_own_labels_are_numbered_after_a_background(self) -> None:
        specs = [LabelSpec("1", "crate", color="#ff8800"), LabelSpec("2", "pallet")]
        sink = exported(
            [
                frame(
                    ShapeRecord("pallet", ShapeType.RECTANGLE, [0, 0, 5, 5]),
                    ShapeRecord("pallet", ShapeType.RECTANGLE, [10, 0, 15, 5]),
                    ShapeRecord("crate", ShapeType.RECTANGLE, [20, 0, 25, 5]),
                )
            ],
            specs,
        )
        assert (
            sink.files["label_colors.txt"].decode()
            == "0 0 0 background\n255 136 0 crate\n56 189 248 pallet\n"
        )
        assert value(sink, gt("labelIds"), 2, 2) == 2
        assert value(sink, gt("instanceIds"), 2, 2) == 2000
        assert value(sink, gt("instanceIds"), 12, 2) == 2001
        assert value(sink, gt("instanceIds"), 22, 2) == 1000
        assert value(sink, gt("labelTrainIds"), 22, 2) == 1  # the ids, background 0
        assert value(sink, gt("labelTrainIds"), 30, 20) == 0
        assert value(sink, gt("color"), 22, 2) == (255, 136, 0)

    def test_one_label_outside_cityscapes_makes_the_whole_project_its_own(self) -> None:
        sink = exported(
            [frame(ShapeRecord("car", ShapeType.RECTANGLE, [0, 0, 5, 5]))],
            labels("car", "forklift"),
        )
        assert sink.files["label_colors.txt"].decode().splitlines()[1].endswith(" car")
        assert value(sink, gt("labelIds"), 2, 2) == 1

    def test_a_frame_of_unknown_size_is_skipped(self) -> None:
        sink = exported([FrameRecord(0, "a.png", None, None)])
        assert sink.files == {}


def source(files: dict[str, bytes | str]) -> MemoryImportSource:
    return MemoryImportSource(
        {k: v.encode() if isinstance(v, str) else v for k, v in files.items()}
    )


def context(*names: str, create: bool = True) -> ImportContext:
    return ImportContext(
        known_labels={},
        frame_by_name={name: index for index, name in enumerate(names)},
        frame_count=len(names),
        create_missing_labels=create,
    )


def ids_png(rows: list[list[int]], mode: str = "I;16") -> bytes:
    image = Image.new("I", (len(rows[0]), len(rows)), 0)
    for y, row in enumerate(rows):
        for x, v in enumerate(row):
            image.putpixel((x, y), v)
    buffer = io.BytesIO()
    (image.convert(mode) if mode != "I" else image).save(buffer, format="PNG")
    return buffer.getvalue()


def region(shape: ShapeRecord) -> set[tuple[int, int]]:
    m = shape.mask or {}
    flags = rle_decode(m["rle"], m["width"], m["height"])
    return {
        (m["left"] + i % m["width"], m["top"] + i // m["width"])
        for i, flag in enumerate(flags)
        if flag
    }


class TestImport:
    def test_an_export_comes_back_as_its_polygons_in_their_order(self) -> None:
        sink = exported(
            [
                frame(
                    ShapeRecord("road", ShapeType.POLYGON, [0, 20, 39, 20, 39, 29], z_order=0),
                    ShapeRecord("car", ShapeType.RECTANGLE, [2, 4, 12, 8], z_order=3),
                )
            ]
        )
        result = FORMAT.import_(MemoryImportSource(sink.files), context("aachen/a.png"))
        assert result.warnings == []
        assert result.frames_matched == 1
        assert [(s.label, s.shape_type, s.points, s.z_order) for _, s in result.shapes] == [
            ("road", ShapeType.POLYGON, [0, 20, 39, 20, 39, 29], 0),
            ("car", ShapeType.POLYGON, [2, 4, 12, 4, 12, 8, 2, 8], 1),
        ]
        # New labels take Cityscapes' colours.
        assert {spec.name: spec.color for spec in result.new_labels} == {
            "road": "#804080",
            "car": "#00008e",
        }

    def test_the_datasets_own_names_match_images_named_with_leftimg8bit(self) -> None:
        document = {
            "imgHeight": 10,
            "imgWidth": 10,
            "objects": [
                {"label": "car", "polygon": [[1, 1], [5, 1], [5, 5]]},
                {"label": "car", "polygon": [[1, 1], [5, 1], [5, 5]], "deleted": 1},
            ],
        }
        result = FORMAT.import_(
            source({"gtFine/val/frankfurt/f_000_gtFine_polygons.json": json.dumps(document)}),
            context("f_000_leftImg8bit.png"),
        )
        assert result.warnings == []
        assert len(result.shapes) == 1  # the deleted one is not

    def test_without_polygons_each_instance_is_the_mask_of_its_pixels(self) -> None:
        rows = [
            [26000, 26000, 0, 26001],
            [0, 26000, 0, 26001],
            [7, 7, 7, 7],
        ]
        result = FORMAT.import_(
            source({"gtFine/train/x/a_gtFine_instanceIds.png": ids_png(rows)}), context("a.png")
        )
        assert result.warnings == []
        shapes = [s for _, s in result.shapes]
        assert [(s.label, s.shape_type) for s in shapes] == [
            ("road", ShapeType.MASK),
            ("car", ShapeType.MASK),
            ("car", ShapeType.MASK),
        ]
        assert region(shapes[0]) == {(x, 2) for x in range(4)}
        assert region(shapes[1]) == {(0, 0), (1, 0), (1, 1)}
        assert region(shapes[2]) == {(3, 0), (3, 1)}
        assert shapes[1].points == [0, 0, 1, 1]

    def test_a_label_colors_file_names_the_classes(self) -> None:
        rows = [[2000, 1000, 0]]
        result = FORMAT.import_(
            source(
                {
                    "label_colors.txt": "0 0 0 background\n255 136 0 wooden crate\n1 2 3 pallet\n",
                    "gtFine/default/a_gtFine_instanceIds.png": ids_png(rows),
                }
            ),
            context("a.jpg"),
        )
        assert result.warnings == []
        assert [s.label for _, s in result.shapes] == ["wooden crate", "pallet"]
        assert {spec.name: spec.color for spec in result.new_labels} == {
            "wooden crate": "#ff8800",
            "pallet": "#010203",
        }

    def test_a_label_id_map_alone_is_a_mask_per_class(self) -> None:
        image = Image.new("L", (3, 1), 0)
        image.putpixel((1, 0), 26)
        image.putpixel((2, 0), 26)
        buffer = io.BytesIO()
        image.save(buffer, format="PNG")
        result = FORMAT.import_(
            source({"gtFine/default/a_gtFine_labelIds.png": buffer.getvalue()}), context("a.png")
        )
        assert [(s.label, region(s)) for _, s in result.shapes] == [("car", {(1, 0), (2, 0)})]

    def test_what_cannot_be_read_is_reported_and_the_rest_imports(self) -> None:
        good = {
            "imgHeight": 5,
            "imgWidth": 5,
            "objects": [
                {"label": "car", "polygon": [[0, 0], [4, 0], [4, 4]]},
                {"label": "car", "polygon": [[0, 0], [4, 0]]},
                {"polygon": [[0, 0], [4, 0], [4, 4]]},
                {"label": "car", "polygon": "nope"},
            ],
        }
        result = FORMAT.import_(
            source(
                {
                    "gtFine/default/a_gtFine_polygons.json": json.dumps(good),
                    "gtFine/default/b_gtFine_polygons.json": "{not json",
                    "gtFine/default/c_gtFine_polygons.json": json.dumps(good),
                    "gtFine/default/d_gtFine_instanceIds.png": ids_png([[40000]]),
                }
            ),
            context("a.png", "b.png", "d.png"),
        )
        assert len(result.shapes) == 1
        joined = "\n".join(result.warnings)
        assert "object 2: a polygon needs 3 points, has 2" in joined
        assert "object 3: no label" in joined
        assert "object 4: polygon is not a list" in joined
        assert "b_gtFine_polygons.json: not valid JSON" in joined
        assert "no image 'c' in this task" in joined
        assert "class ids [40] are in no label map" in joined

    def test_unknown_labels_are_refused_once_when_labels_may_not_be_created(self) -> None:
        document = {
            "imgHeight": 5,
            "imgWidth": 5,
            "objects": [
                {"label": "car", "polygon": [[0, 0], [4, 0], [4, 4]]},
                {"label": "car", "polygon": [[0, 0], [3, 0], [3, 3]]},
            ],
        }
        result = FORMAT.import_(
            source({"gtFine/default/a_gtFine_polygons.json": json.dumps(document)}),
            context("a.png", create=False),
        )
        assert result.shapes == []
        assert len(result.warnings) == 1 and "unknown label 'car'" in result.warnings[0]

    def test_a_malformed_label_colors_file_falls_back_to_the_cityscapes_classes(self) -> None:
        result = FORMAT.import_(
            source(
                {
                    "label_colors.txt": "red car\n",
                    "gtFine/default/a_gtFine_instanceIds.png": ids_png([[26000]]),
                }
            ),
            context("a.png"),
        )
        assert [s.label for _, s in result.shapes] == ["car"]
        assert "label_colors.txt" in result.warnings[0]
