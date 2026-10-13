"""Open Images: the dataset's CSV files, boxes normalised to the image, masks as PNGs.

`scripts/verify_open_images.py`, run in CI, has Datumaro read this module's exports and
writes the files it imports with Datumaro's own exporter; these tests pin the rules that
check rests on, and the dataset's own conventions Datumaro does not write — MIDs with
display names, masks at another resolution, labels verified absent.

Written from the dataset's published file descriptions; no code from Datumaro is used.
"""

from __future__ import annotations

import csv
import io

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
from curvevision.formats.rle import decode as rle_decode
from curvevision.formats.rle import encode as rle_encode

Image = pytest.importorskip("PIL.Image")
ImageDraw = pytest.importorskip("PIL.ImageDraw")

FORMAT = get_format("open_images")
WIDTH, HEIGHT = 50, 40
LABELS = [
    LabelSpec("1", "Car", attributes=(AttributeSpec("truncated", AttributeType.CHECKBOX),)),
    LabelSpec("2", "Tree"),
    LabelSpec("3", "Daytime"),
]


def exported(*frames: FrameRecord) -> MemoryExportSink:
    sink = MemoryExportSink()
    FORMAT.export(DatasetView(name="Street", labels=LABELS, frames=iter(frames)), sink)
    return sink


def frame(*shapes: ShapeRecord, name: str = "a.png", **extra: object) -> FrameRecord:
    return FrameRecord(0, name, WIDTH, HEIGHT, shapes=list(shapes), **extra)  # type: ignore[arg-type]


def rows(sink: MemoryExportSink | dict[str, bytes], path: str) -> list[dict[str, str]]:
    files = sink.files if isinstance(sink, MemoryExportSink) else sink
    return list(csv.DictReader(io.StringIO(files[path].decode())))


def pixels(png: bytes) -> set[tuple[int, int]]:
    image = Image.open(io.BytesIO(png)).convert("L")
    width = image.size[0]
    return {(i % width, i // width) for i, v in enumerate(image.tobytes()) if v}


def region(shape: ShapeRecord) -> set[tuple[int, int]]:
    m = shape.mask or {}
    flags = rle_decode(m["rle"], m["width"], m["height"])
    return {
        (m["left"] + i % m["width"], m["top"] + i // m["width"]) for i, f in enumerate(flags) if f
    }


def png_of(covered: set[tuple[int, int]], size: tuple[int, int] = (WIDTH, HEIGHT)) -> bytes:
    image = Image.new("L", size, 0)
    for point in covered:
        image.putpixel(point, 255)
    buffer = io.BytesIO()
    image.save(buffer, format="PNG")
    return buffer.getvalue()


def context(*names: str, create: bool = True, sizes: bool = True) -> ImportContext:
    return ImportContext(
        known_labels={},
        frame_by_name={name: i for i, name in enumerate(names)},
        frame_count=len(names),
        frame_sizes=dict.fromkeys(range(len(names)), (WIDTH, HEIGHT)) if sizes else {},
        create_missing_labels=create,
    )


class TestExport:
    def test_writes_the_datasets_files(self) -> None:
        sink = exported(
            frame(ShapeRecord("Car", ShapeType.RECTANGLE, [5, 4, 25, 20]), media=b"jpeg"),
        )
        assert sorted(sink.files) == [
            "annotations/class-descriptions.csv",
            "annotations/default-annotations-bbox.csv",
            "annotations/default-images-with-rotation.csv",
            "annotations/images.meta",
            "images/default/a.png",
        ]
        # V5's class list has no header row.
        assert (
            sink.files["annotations/class-descriptions.csv"]
            == b"Car,Car\nTree,Tree\nDaytime,Daytime\n"
        )
        assert rows(sink, "annotations/default-images-with-rotation.csv")[0]["ImageID"] == "a"
        assert sink.files["annotations/images.meta"].decode().splitlines()[-1] == "a 40 50"

    def test_a_box_is_normalised_to_six_places_with_its_flags(self) -> None:
        sink = exported(
            frame(
                ShapeRecord(
                    "Car",
                    ShapeType.RECTANGLE,
                    [25, 20, 5, 4],
                    occluded=True,
                    attributes={"truncated": True, "colour": "red"},
                )
            )
        )
        (box,) = rows(sink, "annotations/default-annotations-bbox.csv")
        assert (box["XMin"], box["XMax"], box["YMin"], box["YMax"]) == (
            "0.100000",
            "0.500000",
            "0.100000",
            "0.500000",
        )
        assert (box["IsOccluded"], box["IsTruncated"], box["IsGroupOf"]) == ("1", "1", "-1")
        assert (box["LabelName"], box["Source"], box["Confidence"]) == ("Car", "freeform", "1")

    def test_a_models_confidence_is_kept(self) -> None:
        sink = exported(
            frame(ShapeRecord("Car", ShapeType.RECTANGLE, [5, 4, 25, 20], confidence=0.875))
        )
        assert rows(sink, "annotations/default-annotations-bbox.csv")[0]["Confidence"] == "0.875"

    def test_tags_are_image_level_labels_a_models_marked_as_machine(self) -> None:
        sink = exported(
            frame(
                tags=["Daytime", "Tree"],
                tag_records=[
                    TagRecord("Daytime"),
                    TagRecord("Tree", source="model", confidence=0.5),
                ],
            )
        )
        labels = rows(sink, "annotations/default-annotations-human-imagelabels.csv")
        assert [(r["LabelName"], r["Source"], r["Confidence"]) for r in labels] == [
            ("Daytime", "verification", "1"),
            ("Tree", "machine", "0.5"),
        ]

    def test_a_polygon_is_the_mask_it_covers_and_the_box_around_it(self) -> None:
        polygon = [10, 5, 30, 8, 20, 25]
        sink = exported(frame(ShapeRecord("Tree", ShapeType.POLYGON, polygon)))
        (row,) = rows(sink, "annotations/default-annotations-object-segmentation.csv")
        assert row["MaskPath"] == "a_Tree_00000000.png"
        assert row["BoxID"] == "00000000"
        expected = Image.new("L", (WIDTH, HEIGHT), 0)
        ImageDraw.Draw(expected).polygon(
            list(zip(polygon[0::2], polygon[1::2], strict=True)), fill=255
        )
        covered = pixels(sink.files["masks/default/a_Tree_00000000.png"])
        assert covered == pixels(png_of_image(expected))
        (box,) = rows(sink, "annotations/default-annotations-bbox.csv")
        xs = [x for x, _ in covered]
        assert float(box["XMin"]) == pytest.approx(min(xs) / WIDTH, abs=1e-6)
        assert float(box["XMax"]) == pytest.approx((max(xs) + 1) / WIDTH, abs=1e-6)
        assert (row["BoxXMin"], row["BoxYMax"]) == (box["XMin"], box["YMax"])

    def test_a_mask_round_trips_pixel_for_pixel_and_arrives_once(self) -> None:
        flags = [True, False, True, True, True, False]
        mask = {"rle": rle_encode(flags, 3, 2), "left": 40, "top": 30, "width": 3, "height": 2}
        sink = exported(
            frame(
                ShapeRecord(
                    "Car",
                    ShapeType.MASK,
                    [40, 30, 42, 31],
                    mask=mask,
                    occluded=True,
                    attributes={"truncated": False},
                )
            )
        )
        result = FORMAT.import_(MemoryImportSource(sink.files), context("a.png"))
        assert result.warnings == []
        ((_, shape),) = result.shapes
        assert shape.shape_type is ShapeType.MASK
        assert region(shape) == {(40, 30), (42, 30), (40, 31), (41, 31)}
        assert shape.occluded and shape.attributes == {"truncated": False}

    def test_names_that_are_not_file_names_make_safe_mask_names(self) -> None:
        sink = exported(
            frame(
                ShapeRecord("Tree", ShapeType.ELLIPSE, [20, 20, 5, 3]),
                name="street/left cam/a.png",
            )
        )
        (row,) = rows(sink, "annotations/default-annotations-object-segmentation.csv")
        assert row["ImageID"] == "street/left cam/a"
        assert row["MaskPath"] == "street-left-cam-a_Tree_00000000.png"
        assert "masks/default/street-left-cam-a_Tree_00000000.png" in sink.files

    def test_what_the_format_cannot_hold_is_left_out(self) -> None:
        sink = exported(
            frame(
                ShapeRecord("Car", ShapeType.ROTATED_RECTANGLE, [5, 5, 15, 15], rotation=30),
                ShapeRecord("Car", ShapeType.POLYLINE, [0, 0, 10, 10]),
                ShapeRecord("Car", ShapeType.POINTS, [3, 3]),
            )
        )
        assert "annotations/default-annotations-bbox.csv" not in sink.files

    def test_a_frame_of_unknown_size_is_listed_without_its_shapes(self) -> None:
        sink = exported(
            FrameRecord(
                0,
                "a.png",
                None,
                None,
                shapes=[ShapeRecord("Car", ShapeType.RECTANGLE, [1, 1, 5, 5])],
            )
        )
        assert rows(sink, "annotations/default-images-with-rotation.csv")[0]["ImageID"] == "a"
        assert "annotations/default-annotations-bbox.csv" not in sink.files
        assert "annotations/images.meta" not in sink.files


def png_of_image(image: object) -> bytes:
    buffer = io.BytesIO()
    image.save(buffer, format="PNG")  # type: ignore[attr-defined]
    return buffer.getvalue()


BOX_HEADER = (
    "ImageID,Source,LabelName,Confidence,XMin,XMax,YMin,YMax,"
    "IsOccluded,IsTruncated,IsGroupOf,IsDepiction,IsInside\n"
)
MASK_HEADER = (
    "MaskPath,ImageID,LabelName,BoxID,BoxXMin,BoxXMax,BoxYMin,BoxYMax,PredictedIoU,Clicks\n"
)


def source(files: dict[str, str | bytes]) -> MemoryImportSource:
    return MemoryImportSource(
        {k: v.encode() if isinstance(v, str) else v for k, v in files.items()}
    )


class TestImport:
    def test_the_datasets_own_files_mids_display_names_and_low_resolution_masks(self) -> None:
        # As Open Images V6 ships them: a class list with a header and MIDs, masks at a
        # resolution other than the image's, each naming its box.
        small = png_of({(x, y) for x in range(5, 10) for y in range(4, 8)}, size=(25, 20))
        result = FORMAT.import_(
            source(
                {
                    "oidv6-class-descriptions.csv": (
                        "LabelName,DisplayName\n/m/0k4j,Car\n/m/07j7r,Tree\n"
                    ),
                    "train-annotations-bbox.csv": BOX_HEADER
                    + "f00d,xclick,/m/0k4j,1,0.1,0.5,0.1,0.5,0,1,0,0,0\n"
                    + "f00d,xclick,/m/07j7r,1,0.2,0.4,0.2,0.4,1,0,0,0,0\n",
                    "train-annotations-object-segmentation.csv": MASK_HEADER
                    + "f00d_m07j7r_b1.png,f00d,/m/07j7r,b1,"
                    + "0.200000,0.400000,0.200000,0.400000,0.9,\n",
                    "train-masks-f/f00d_m07j7r_b1.png": small,
                }
            ),
            context("f00d.jpg"),
        )
        assert result.warnings == []
        by_label = {s.label: s for _, s in result.shapes}
        assert sorted(by_label) == ["Car", "Tree"]
        car, tree = by_label["Car"], by_label["Tree"]
        assert car.points == [5, 4, 25, 20]
        assert not car.occluded and car.attributes == {
            "truncated": True,
            "is_group_of": False,
            "is_depiction": False,
            "is_inside": False,
        }
        # Scaled up by two to the image, to the nearest pixel; its box's flags kept.
        assert tree.shape_type is ShapeType.MASK
        assert region(tree) == {(x, y) for x in range(10, 20) for y in range(8, 16)}
        assert tree.occluded
        created = {spec.name: {a.name for a in spec.attributes} for spec in result.new_labels}
        assert created["Car"] == {"truncated", "is_group_of", "is_depiction", "is_inside"}

    def test_a_label_verified_absent_is_not_a_tag_and_machine_labels_are_not_read(self) -> None:
        header = "ImageID,Source,LabelName,Confidence\n"
        result = FORMAT.import_(
            source(
                {
                    "class-descriptions.csv": "/m/01,Daytime\n/m/02,Night\n",
                    "train-annotations-human-imagelabels.csv": header
                    + "a,verification,/m/01,1\na,verification,/m/02,0\n",
                    "train-annotations-machine-imagelabels.csv": header + "a,machine,/m/02,0.7\n",
                }
            ),
            context("a.jpg"),
        )
        assert [tag for _, tag in result.tags] == ["Daytime"]
        assert len(result.warnings) == 1 and "machine-generated" in result.warnings[0]

    def test_images_not_in_the_task_are_one_warning_not_one_per_row(self) -> None:
        body = "".join(f"img{i},xclick,Car,1,0.1,0.2,0.1,0.2,0,0,0,0,0\n" for i in range(40))
        result = FORMAT.import_(
            source({"train-annotations-bbox.csv": BOX_HEADER + body}), context("img3.jpg")
        )
        assert len(result.shapes) == 1
        assert result.warnings == [
            "annotations for 39 image(s) not in this task were skipped: img0, img1, img10, "
            "img11, img12 and 34 more"
        ]

    def test_what_cannot_be_read_is_reported_and_the_rest_imports(self) -> None:
        result = FORMAT.import_(
            source(
                {
                    "train-annotations-bbox.csv": BOX_HEADER
                    + "a,xclick,Car,1,0.1,0.2,0.1,0.2,0,0,0,0,0\n"
                    + "a,xclick,Car,1,left,0.2,0.1,0.2,0,0,0,0,0\n",
                    "train-annotations-object-segmentation.csv": MASK_HEADER
                    + "gone.png,a,Car,b,,,,,,\n"
                    + "empty.png,a,Car,c,,,,,,\n",
                    "masks/train/empty.png": png_of(set()),
                }
            ),
            context("a.jpg"),
        )
        assert len(result.shapes) == 1
        joined = "\n".join(result.warnings)
        assert "line 3: box coordinates are not numbers" in joined
        assert "mask 'gone.png' is not in the archive" in joined
        assert "empty.png covers no pixels" in joined

    def test_without_the_images_size_nothing_is_guessed(self) -> None:
        result = FORMAT.import_(
            source(
                {
                    "train-annotations-bbox.csv": BOX_HEADER
                    + "a,xclick,Car,1,0.1,0.2,0.1,0.2,0,0,0,0,0\n"
                }
            ),
            context("a.jpg", sizes=False),
        )
        assert result.shapes == []
        assert "of unknown size were skipped: a" in result.warnings[0]

    def test_unknown_labels_are_refused_once_when_labels_may_not_be_created(self) -> None:
        body = "a,xclick,Car,1,0.1,0.2,0.1,0.2,0,0,0,0,0\n" * 2
        result = FORMAT.import_(
            source({"train-annotations-bbox.csv": BOX_HEADER + body}),
            context("a.jpg", create=False),
        )
        assert result.shapes == []
        assert len(result.warnings) == 1 and "unknown label 'Car'" in result.warnings[0]
