"""KITTI, MOT, CVAT XML and segmentation masks.

Four formats added together because they answer one question — *can this dataset leave, and
can somebody else's arrive* — and because between them they cover the shapes of annotation
that the box-only formats cannot: object identity across frames (MOT), the full editor
vocabulary including tracks and attributes (CVAT XML), pixel-wise classes (segmentation) and
the convention robotics and driving work reaches for first (KITTI).

Every one is round-tripped through its own reader where it has one. An exporter that writes
something its own importer cannot read is the failure this file exists to catch, and it is
invisible from reading either half alone.
"""

from __future__ import annotations

import xml.etree.ElementTree as ET

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


def text(sink: MemoryExportSink, path: str) -> str:
    """The sink stores bytes, as a real archive does. Tests want text."""
    return sink.files[path].decode("utf-8")


def source(files: dict[str, str]) -> MemoryImportSource:
    """Hand-written fixtures, encoded — `ImportSource.read` returns bytes by contract."""
    return MemoryImportSource({name: body.encode("utf-8") for name, body in files.items()})


def dataset(frames: list[FrameRecord], labels: list[str] | None = None) -> DatasetView:
    names = labels or ["car", "pedestrian"]
    return DatasetView(
        name="Scene",
        labels=[LabelSpec(id=str(i), name=name) for i, name in enumerate(names, 1)],
        frames=iter(frames),
    )


def box(label: str, x1: float, y1: float, x2: float, y2: float, **kwargs: object) -> ShapeRecord:
    return ShapeRecord(
        label=label, shape_type=ShapeType.RECTANGLE, points=[x1, y1, x2, y2], **kwargs
    )


def context(count: int = 2, names: dict[str, int] | None = None) -> ImportContext:
    return ImportContext(
        known_labels={},
        frame_by_name=names or {"frame_000.jpg": 0, "frame_001.jpg": 1},
        frame_count=count,
        frame_sizes=dict.fromkeys(range(count), (800, 600)),
    )


def two_frames() -> list[FrameRecord]:
    return [
        FrameRecord(
            index=0,
            name="frame_000.jpg",
            width=800,
            height=600,
            shapes=[box("car", 100, 100, 300, 250)],
        ),
        FrameRecord(
            index=1,
            name="frame_001.jpg",
            width=800,
            height=600,
            shapes=[box("car", 120, 100, 320, 250), box("pedestrian", 500, 300, 540, 420)],
        ),
    ]


# --------------------------------------------------------------------------------- KITTI


class TestKitti:
    def test_a_label_file_per_frame_with_fifteen_columns(self) -> None:
        sink = MemoryExportSink()
        get_format("kitti").export(dataset(two_frames()), sink)

        assert "label_2/frame_000.txt" in sink.files
        assert "label_2/frame_001.txt" in sink.files
        first = text(sink, "label_2/frame_000.txt").strip().split("\n")
        assert len(first) == 1
        assert len(first[0].split()) == 15, "KITTI readers index by column position"
        assert first[0].split()[0] == "car"
        assert [float(v) for v in first[0].split()[4:8]] == [100.0, 100.0, 300.0, 250.0]

    def test_an_empty_frame_still_gets_a_file(self) -> None:
        """Absent and empty are different facts: unlabelled versus labelled-and-empty."""
        frames = [FrameRecord(index=0, name="a.jpg", width=800, height=600, shapes=[])]
        sink = MemoryExportSink()
        get_format("kitti").export(dataset(frames), sink)

        assert text(sink, "label_2/a.txt") == ""

    def test_the_3d_columns_are_marked_unknown_rather_than_invented(self) -> None:
        """The honesty that matters in this format.

        Writing zeros for dimensions and a plausible angle would let a consumer read them as
        measurements. -10 is outside the valid rotation range, so it cannot be mistaken for
        one, and the README says so in words.
        """
        sink = MemoryExportSink()
        get_format("kitti").export(dataset(two_frames()), sink)

        columns = text(sink, "label_2/frame_000.txt").strip().split()
        assert [float(v) for v in columns[8:14]] == [0.0] * 6
        assert float(columns[14]) == -10.0
        assert "NOT measurements" in text(sink, "README.txt")

    def test_a_box_running_off_the_frame_is_reported_as_truncated(self) -> None:
        """KITTI's truncation column exists for exactly this, and readers filter on it."""
        frames = [
            FrameRecord(
                index=0,
                name="a.jpg",
                width=800,
                height=600,
                shapes=[box("car", 700, 100, 900, 200)],
            )
        ]
        sink = MemoryExportSink()
        get_format("kitti").export(dataset(frames), sink)

        truncation = float(text(sink, "label_2/a.txt").split()[1])
        assert truncation == pytest.approx(0.5, abs=0.01), "half the box is outside"

    def test_a_fully_visible_box_is_not_truncated(self) -> None:
        sink = MemoryExportSink()
        get_format("kitti").export(dataset(two_frames()), sink)
        assert float(text(sink, "label_2/frame_000.txt").split()[1]) == 0.0

    def test_round_trip_preserves_boxes(self) -> None:
        kitti = get_format("kitti")
        sink = MemoryExportSink()
        kitti.export(dataset(two_frames()), sink)

        result = kitti.import_(MemoryImportSource(sink.files), context())

        assert result.frames_matched == 2
        assert len(result.shapes) == 3
        first = next(shape for frame, shape in result.shapes if frame == 0)
        assert first.points == [100.0, 100.0, 300.0, 250.0]
        assert first.label == "car"

    def test_a_label_with_a_space_is_written_as_one_column(self) -> None:
        """The format is space-delimited; a two-word label would shift every field after it."""
        frames = [
            FrameRecord(
                index=0,
                name="a.jpg",
                width=800,
                height=600,
                shapes=[box("traffic light", 10, 10, 50, 50)],
            )
        ]
        sink = MemoryExportSink()
        get_format("kitti").export(dataset(frames, ["traffic light"]), sink)

        columns = text(sink, "label_2/a.txt").split()
        assert len(columns) == 15
        assert columns[0] == "traffic_light"

    def test_dontcare_regions_are_not_imported_as_objects(self) -> None:
        """`DontCare` marks a region the benchmark ignores, not a thing somebody labelled."""
        files = {
            "label_2/frame_000.txt": "Car 0.00 0 -10.00 10.00 10.00 50.00 50.00 0 0 0 0 0 0 -10\n"
            "DontCare -1 -1 -10 200.00 200.00 260.00 260.00 -1 -1 -1 -1000 -1000 -1000 -10\n",
        }
        result = get_format("kitti").import_(source(files), context())

        assert len(result.shapes) == 1
        assert result.shapes[0][1].label == "Car"

    def test_a_short_line_is_reported_not_guessed(self) -> None:
        files = {"label_2/frame_000.txt": "Car 0.00 0\n"}
        result = get_format("kitti").import_(source(files), context())

        assert result.shapes == []
        assert any("8 columns" in warning for warning in result.warnings)


# ----------------------------------------------------------------------------------- MOT


class TestMot:
    def tracked(self) -> list[FrameRecord]:
        """One car, tracked across both frames, plus an untracked pedestrian."""
        return [
            FrameRecord(
                index=0,
                name="frame_000.jpg",
                width=800,
                height=600,
                shapes=[box("car", 100, 100, 300, 250, track_id=7)],
            ),
            FrameRecord(
                index=1,
                name="frame_001.jpg",
                width=800,
                height=600,
                shapes=[
                    box("car", 120, 100, 320, 250, track_id=7),
                    box("pedestrian", 500, 300, 540, 420),
                ],
            ),
        ]

    def rows(self, sink: MemoryExportSink) -> list[list[str]]:
        return [line.split(",") for line in text(sink, "gt/gt.txt").strip().split("\n")]

    def test_object_identity_is_carried_across_frames(self) -> None:
        """The point of the format. Numbering rows instead would describe nothing."""
        sink = MemoryExportSink()
        get_format("mot").export(dataset(self.tracked()), sink)

        rows = self.rows(sink)
        car_rows = [row for row in rows if row[1] == "7"]
        assert len(car_rows) == 2, "the same car on both frames keeps one id"
        assert {row[0] for row in car_rows} == {"1", "2"}

    def test_frames_are_one_based(self) -> None:
        """MOT counts from 1 and CurveVision from 0; an off-by-one here is invisible."""
        sink = MemoryExportSink()
        get_format("mot").export(dataset(self.tracked()), sink)
        assert min(int(row[0]) for row in self.rows(sink)) == 1

    def test_an_untracked_shape_gets_an_id_that_cannot_be_mistaken_for_one(self) -> None:
        from curvevision.formats.mot import SYNTHETIC_ID_BASE

        sink = MemoryExportSink()
        get_format("mot").export(dataset(self.tracked()), sink)

        ids = {int(row[1]) for row in self.rows(sink)}
        assert 7 in ids
        assert any(value >= SYNTHETIC_ID_BASE for value in ids)

    def test_class_names_travel_in_a_sidecar(self) -> None:
        """MOT stores a numeric class only; without the names the export is not portable."""
        sink = MemoryExportSink()
        get_format("mot").export(dataset(self.tracked()), sink)
        assert text(sink, "labels.txt").split() == ["car", "pedestrian"]

    def test_round_trip_preserves_identity_and_geometry(self) -> None:
        mot = get_format("mot")
        sink = MemoryExportSink()
        mot.export(dataset(self.tracked()), sink)

        result = mot.import_(MemoryImportSource(sink.files), context())

        assert result.frames_matched == 2
        cars = [shape for _, shape in result.shapes if shape.label == "car"]
        assert len(cars) == 2
        assert {shape.track_id for shape in cars} == {7}
        assert cars[0].points == [100.0, 100.0, 300.0, 250.0]

    def test_synthetic_ids_do_not_come_back_as_tracks(self) -> None:
        """A round trip must not invent object identity that nobody annotated."""
        mot = get_format("mot")
        sink = MemoryExportSink()
        mot.export(dataset(self.tracked()), sink)

        result = mot.import_(MemoryImportSource(sink.files), context())
        pedestrians = [s for _, s in result.shapes if s.label == "pedestrian"]
        assert pedestrians and all(shape.track_id is None for shape in pedestrians)

    def test_a_frame_outside_the_task_is_reported_not_clamped(self) -> None:
        files = {"gt/gt.txt": "99,1,10,10,20,20,1,1,1.0\n", "labels.txt": "car\n"}
        result = get_format("mot").import_(source(files), context(count=2))

        assert result.shapes == []
        assert any("outside this task" in warning for warning in result.warnings)

    def test_an_archive_with_no_gt_says_so(self) -> None:
        result = get_format("mot").import_(source({"readme.txt": "hi"}), context())
        assert result.shapes == []
        assert any("no gt.txt" in warning for warning in result.warnings)


# ------------------------------------------------------------------------------ CVAT XML


class TestCvatXml:
    def rich_frames(self) -> list[FrameRecord]:
        return [
            FrameRecord(
                index=0,
                name="frame_000.jpg",
                width=800,
                height=600,
                shapes=[
                    box("car", 100, 100, 300, 250, occluded=True, attributes={"colour": "red"}),
                    ShapeRecord(
                        label="pedestrian",
                        shape_type=ShapeType.POLYGON,
                        points=[400, 200, 460, 200, 460, 400],
                    ),
                    ShapeRecord(
                        label="car", shape_type=ShapeType.POLYLINE, points=[10, 10, 60, 60, 110, 10]
                    ),
                ],
                tags=["daytime"],
            ),
        ]

    def test_it_carries_the_whole_editor_vocabulary(self) -> None:
        """The reason this format is the migration bridge rather than COCO."""
        sink = MemoryExportSink()
        get_format("cvat_xml").export(dataset(self.rich_frames()), sink)
        root = ET.fromstring(text(sink, "annotations.xml"))

        image = root.find("image")
        assert image is not None
        assert {child.tag for child in image} == {"box", "polygon", "polyline", "tag"}

    def test_attributes_and_occlusion_survive_a_round_trip(self) -> None:
        cvat = get_format("cvat_xml")
        sink = MemoryExportSink()
        cvat.export(dataset(self.rich_frames()), sink)

        result = cvat.import_(
            MemoryImportSource(sink.files), context(count=1, names={"frame_000.jpg": 0})
        )

        car = next(s for _, s in result.shapes if s.shape_type is ShapeType.RECTANGLE)
        assert car.points == [100.0, 100.0, 300.0, 250.0]
        assert car.occluded is True
        assert car.attributes == {"colour": "red"}

    def test_tags_survive_a_round_trip(self) -> None:
        cvat = get_format("cvat_xml")
        sink = MemoryExportSink()
        cvat.export(dataset(self.rich_frames()), sink)

        result = cvat.import_(
            MemoryImportSource(sink.files), context(count=1, names={"frame_000.jpg": 0})
        )
        assert result.tags == [(0, "daytime")]

    def test_a_cuboid_writes_cvats_own_named_attributes(self) -> None:
        """Real CVAT does not understand `<cuboid points="...">` -- it reads 16 named
        attributes (`xtl1,ytl1,xbl1,ybl1,xtr1,ytr1,xbr1,ybr1,xtl2,...`), independently
        confirmed from CVAT's own `dataset_manager/formats/cvat.py`. Writing a generic
        `points=` list here would produce a file this format's own capabilities claimed
        round-tripped, but that real CVAT would silently fail to read."""
        points = [10, 20, 10, 60, 30, 20, 30, 60, 15, 25, 15, 65, 35, 25, 35, 65]
        frames = [
            FrameRecord(
                index=0,
                name="a.jpg",
                width=800,
                height=600,
                shapes=[ShapeRecord(label="car", shape_type=ShapeType.CUBOID, points=points)],
            )
        ]
        sink = MemoryExportSink()
        get_format("cvat_xml").export(dataset(frames), sink)
        root = ET.fromstring(text(sink, "annotations.xml"))

        cuboid = root.find("./image/cuboid")
        assert cuboid is not None
        assert (cuboid.get("xtl1"), cuboid.get("ytl1")) == ("10.00", "20.00")
        assert (cuboid.get("xbr2"), cuboid.get("ybr2")) == ("35.00", "65.00")

    def test_a_cuboid_round_trips_all_16_coordinates(self) -> None:
        cvat = get_format("cvat_xml")
        points = [10, 20, 10, 60, 30, 20, 30, 60, 15, 25, 15, 65, 35, 25, 35, 65]
        frames = [
            FrameRecord(
                index=0,
                name="a.jpg",
                width=800,
                height=600,
                shapes=[ShapeRecord(label="car", shape_type=ShapeType.CUBOID, points=points)],
            )
        ]
        sink = MemoryExportSink()
        cvat.export(dataset(frames), sink)

        result = cvat.import_(MemoryImportSource(sink.files), context(count=1, names={"a.jpg": 0}))
        cuboid = next(s for _, s in result.shapes if s.shape_type is ShapeType.CUBOID)
        assert cuboid.points == [float(p) for p in points]

    def test_a_tracked_dataset_is_written_as_tracks(self) -> None:
        """Exporting tracked work as loose per-frame shapes throws away the identity."""
        frames = [
            FrameRecord(
                index=0,
                name="a.jpg",
                width=800,
                height=600,
                shapes=[box("car", 10, 10, 50, 50, track_id=3)],
            ),
            FrameRecord(
                index=1,
                name="b.jpg",
                width=800,
                height=600,
                shapes=[box("car", 20, 10, 60, 50, track_id=3)],
            ),
        ]
        sink = MemoryExportSink()
        get_format("cvat_xml").export(dataset(frames), sink)
        root = ET.fromstring(text(sink, "annotations.xml"))

        tracks = root.findall("track")
        assert len(tracks) == 1
        assert tracks[0].get("id") == "3"
        assert root.find("./meta/task/mode").text == "interpolation"

    def test_a_track_is_closed_with_an_outside_shape(self) -> None:
        """Without it a consumer interpolates the object past the end of its life."""
        frames = [
            FrameRecord(
                index=0,
                name="a.jpg",
                width=800,
                height=600,
                shapes=[box("car", 10, 10, 50, 50, track_id=3)],
            )
        ]
        sink = MemoryExportSink()
        get_format("cvat_xml").export(dataset(frames), sink)
        root = ET.fromstring(text(sink, "annotations.xml"))

        boxes = root.findall("./track/box")
        assert [b.get("outside") for b in boxes] == ["0", "1"]
        assert boxes[1].get("frame") == "1"

    def test_a_track_round_trips_with_its_identity(self) -> None:
        cvat = get_format("cvat_xml")
        frames = [
            FrameRecord(
                index=0,
                name="a.jpg",
                width=800,
                height=600,
                shapes=[box("car", 10, 10, 50, 50, track_id=3)],
            ),
            FrameRecord(
                index=1,
                name="b.jpg",
                width=800,
                height=600,
                shapes=[box("car", 20, 10, 60, 50, track_id=3)],
            ),
        ]
        sink = MemoryExportSink()
        cvat.export(dataset(frames), sink)

        result = cvat.import_(
            MemoryImportSource(sink.files), context(count=2, names={"a.jpg": 0, "b.jpg": 1})
        )

        assert len(result.shapes) == 2, "the closing outside shape is not a position"
        assert {shape.track_id for _, shape in result.shapes} == {3}

    def test_an_import_matches_frames_by_filename_not_order(self) -> None:
        """Media re-uploaded in a different order is the normal case, not the exception."""
        cvat = get_format("cvat_xml")
        sink = MemoryExportSink()
        cvat.export(dataset(self.rich_frames()), sink)

        # The same file is frame 5 in this task.
        result = cvat.import_(
            MemoryImportSource(sink.files),
            ImportContext(known_labels={}, frame_by_name={"frame_000.jpg": 5}, frame_count=6),
        )
        assert {frame for frame, _ in result.shapes} == {5}

    def test_malformed_xml_is_reported_not_raised(self) -> None:
        result = get_format("cvat_xml").import_(
            source({"annotations.xml": "<annotations><oops>"}), context()
        )
        assert result.shapes == []
        assert any("not valid XML" in warning for warning in result.warnings)

    def test_an_archive_with_no_xml_says_so(self) -> None:
        result = get_format("cvat_xml").import_(source({"readme.txt": "hi"}), context())
        assert any("no .xml" in warning for warning in result.warnings)


# -------------------------------------------------------------------- segmentation masks

pytest.importorskip("PIL", reason="mask rasterisation needs Pillow")


class TestSegmentationMask:
    def read_mask(self, sink: MemoryExportSink, path: str):
        from io import BytesIO

        from PIL import Image

        return Image.open(BytesIO(sink.files[path]))

    def test_one_indexed_png_per_frame(self) -> None:
        sink = MemoryExportSink()
        get_format("segmentation_mask").export(dataset(two_frames()), sink)

        mask = self.read_mask(sink, "SegmentationClass/frame_000.png")
        assert mask.mode == "P"
        assert mask.size == (800, 600)

    def test_pixels_carry_the_class_index(self) -> None:
        sink = MemoryExportSink()
        get_format("segmentation_mask").export(dataset(two_frames()), sink)
        mask = self.read_mask(sink, "SegmentationClass/frame_000.png")

        assert mask.getpixel((200, 175)) == 1, "inside the car box"
        assert mask.getpixel((10, 10)) == 0, "background"

    def test_class_indices_are_stable_across_frames(self) -> None:
        """A frame with no cars must agree with one that has them about what index 'car' is.

        Numbering from what happens to appear would give two masks of the same dataset
        different meanings, and they could not be trained on together.
        """
        frames = [
            FrameRecord(
                index=0,
                name="a.jpg",
                width=100,
                height=100,
                shapes=[box("pedestrian", 10, 10, 50, 50)],
            ),
            FrameRecord(
                index=1, name="b.jpg", width=100, height=100, shapes=[box("car", 10, 10, 50, 50)]
            ),
        ]
        sink = MemoryExportSink()
        get_format("segmentation_mask").export(dataset(frames), sink)

        assert self.read_mask(sink, "SegmentationClass/a.png").getpixel((20, 20)) == 2
        assert self.read_mask(sink, "SegmentationClass/b.png").getpixel((20, 20)) == 1

    def test_z_order_decides_an_overlap(self) -> None:
        """A mask has room for one answer per pixel; the shape in front should win."""
        frames = [
            FrameRecord(
                index=0,
                name="a.jpg",
                width=100,
                height=100,
                shapes=[
                    box("car", 0, 0, 100, 100, z_order=0),
                    box("pedestrian", 20, 20, 60, 60, z_order=5),
                ],
            ),
        ]
        sink = MemoryExportSink()
        get_format("segmentation_mask").export(dataset(frames), sink)
        mask = self.read_mask(sink, "SegmentationClass/a.png")

        assert mask.getpixel((40, 40)) == 2, "the pedestrian is in front"
        assert mask.getpixel((90, 90)) == 1, "the car elsewhere"

    def test_a_polygon_is_rasterised_not_dropped(self) -> None:
        frames = [
            FrameRecord(
                index=0,
                name="a.jpg",
                width=100,
                height=100,
                shapes=[
                    ShapeRecord(
                        label="car",
                        shape_type=ShapeType.POLYGON,
                        points=[10, 10, 90, 10, 90, 90, 10, 90],
                    ),
                ],
            )
        ]
        sink = MemoryExportSink()
        get_format("segmentation_mask").export(dataset(frames), sink)

        assert self.read_mask(sink, "SegmentationClass/a.png").getpixel((50, 50)) == 1

    def test_a_polyline_covers_nothing(self) -> None:
        """It encloses no area; painting one would invent a region nobody drew."""
        frames = [
            FrameRecord(
                index=0,
                name="a.jpg",
                width=100,
                height=100,
                shapes=[
                    ShapeRecord(
                        label="car", shape_type=ShapeType.POLYLINE, points=[10, 50, 90, 50]
                    ),
                ],
            )
        ]
        sink = MemoryExportSink()
        get_format("segmentation_mask").export(dataset(frames), sink)
        mask = self.read_mask(sink, "SegmentationClass/a.png")

        assert set(mask.getdata()) == {0}

    def test_a_labelmap_travels_with_the_masks(self) -> None:
        sink = MemoryExportSink()
        get_format("segmentation_mask").export(dataset(two_frames()), sink)

        labelmap = text(sink, "labelmap.txt")
        assert "background:0,0,0::" in labelmap
        assert labelmap.count("\n") == 4  # header + background + two classes

    def test_import_is_refused_with_a_reason(self) -> None:
        """A mask does not record the polygons it was painted from."""
        result = get_format("segmentation_mask").import_(MemoryImportSource({}), context())

        assert result.shapes == []
        assert any("cannot be imported" in warning for warning in result.warnings)
        assert get_format("segmentation_mask").capabilities.supports_import is False
