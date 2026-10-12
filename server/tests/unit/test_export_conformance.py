"""Exports held to what the tools that read them require.

`test_coco_reference.py` holds COCO to pycocotools. The other formats were checked in
iteration 66 against the readers people use — Datumaro (CVAT's export library) for Pascal
VOC, segmentation masks, MOTChallenge, KITTI and CVAT XML; CVAT's own XML reader
(`cvat/apps/dataset_manager/formats/cvat.py`) for CVAT XML — and each defect found has a
test here, written against the reader's rule rather than against this project's importer,
which could share the mistake.

Ultralytics could not be installed in CI (it needs torch), so its loader rules are
restated below from its source (`ultralytics/data/utils.py`: `img2label_paths`,
`verify_image_label`; `ultralytics/data/dataset.py`: `YOLODataset.get_labels`).
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
from curvevision.formats.base import rotated_corners

WIDTH, HEIGHT = 320, 240
ROTATED = [100.0, 100.0, 160.0, 120.0]
ROTATION = 30.0


def labels() -> list[LabelSpec]:
    return [
        LabelSpec(id="1", name="car", color="#ff8000"),
        LabelSpec(
            id="2",
            name="person",
            keypoints=("head", "hand", "foot"),
            skeleton_edges=((0, 1), (0, 2)),
        ),
        LabelSpec(id="3", name="road"),
    ]


def shapes(*, polygon: bool = True, tracked: bool = False) -> list[ShapeRecord]:
    found = [
        ShapeRecord(
            label="car",
            shape_type=ShapeType.RECTANGLE,
            points=[10.0, 20.0, 60.0, 50.0],
            track_id=7 if tracked else None,
        ),
        ShapeRecord(
            label="car",
            shape_type=ShapeType.ROTATED_RECTANGLE,
            points=list(ROTATED),
            rotation=ROTATION,
        ),
        ShapeRecord(
            label="person",
            shape_type=ShapeType.SKELETON,
            points=[],
            elements=[
                {"points": [50.0, 150.0]},
                {"points": [60.0, 170.0], "occluded": True},
                {"points": [0.0, 0.0], "outside": True},
            ],
        ),
    ]
    if polygon:
        found.append(
            ShapeRecord(
                label="road",
                shape_type=ShapeType.POLYGON,
                points=[150.0, 10.0, 200.0, 10.0, 200.0, 60.0],
            )
        )
    return found


def dataset(*, polygon: bool = True, tracked: bool = False, frames: int = 2) -> DatasetView:
    records = [
        FrameRecord(
            index=index,
            name=f"frame_{index:03d}.jpg",
            width=WIDTH,
            height=HEIGHT,
            shapes=shapes(polygon=polygon, tracked=tracked) if index == 0 else [],
            media=b"\xff\xd8 not really a jpeg",
        )
        for index in range(frames)
    ]
    return DatasetView(name="conformance", labels=labels(), frames=iter(records))


def export(format_id: str, view: DatasetView | None = None) -> dict[str, bytes]:
    sink = MemoryExportSink()
    get_format(format_id).export(view or dataset(), sink)
    return sink.files


def enclosing(points: list[float], rotation: float) -> list[float]:
    corners = rotated_corners(points, rotation)
    xs, ys = corners[0::2], corners[1::2]
    return [min(xs), min(ys), max(xs), max(ys)]


# ------------------------------------------------------------------------------ Pascal VOC


class TestPascalVoc:
    def test_the_labelmap_gives_colours_as_r_g_b(self) -> None:
        """Datumaro and CVAT parse `name:r,g,b:parts:actions`; a `#rrggbb` was refused."""
        labelmap = export("voc")["labelmap.txt"].decode().splitlines()
        assert labelmap[0].startswith("#")
        assert "car:255,128,0::" in labelmap

    def test_a_rotated_box_is_written_as_the_box_around_its_rotated_corners(self) -> None:
        document = ET.fromstring(export("voc")["Annotations/frame_000.xml"])
        boxes = [
            [float(box.findtext(key) or "nan") for key in ("xmin", "ymin", "xmax", "ymax")]
            for box in document.iter("bndbox")
        ]
        assert boxes[1] == pytest.approx(enclosing(ROTATED, ROTATION), abs=0.01)


class TestSegmentationMasks:
    def test_the_image_set_lists_every_frame_written(self) -> None:
        """Datumaro's VOC segmentation reader finds items through `ImageSets/Segmentation`."""
        files = export("segmentation_mask")
        listed = files["ImageSets/Segmentation/default.txt"].decode().split()
        assert listed == ["frame_000", "frame_001"]
        assert all(f"SegmentationClass/{stem}.png" in files for stem in listed)


# ----------------------------------------------------------------------------- MOTChallenge


class TestMotChallenge:
    def test_the_label_list_is_where_readers_look_for_it(self) -> None:
        files = export("mot", dataset(tracked=True))
        assert "gt/labels.txt" in files
        assert "labels.txt" not in files
        assert files["gt/labels.txt"].decode().split()[0] == "car"


# ------------------------------------------------------------------------------- CVAT XML


class TestCvatXml:
    def root(self, view: DatasetView | None = None) -> ET.Element:
        return ET.fromstring(export("cvat_xml", view)["annotations.xml"])

    def test_the_meta_carries_the_frame_size(self) -> None:
        """CVAT and Datumaro size every track-mode item from `original_size`."""
        root = self.root(dataset(tracked=True))
        assert root.findtext("./meta/task/original_size/width") == str(WIDTH)
        assert root.findtext("./meta/task/original_size/height") == str(HEIGHT)

    def test_a_skeleton_s_joints_are_declared_as_labels_with_their_parent(self) -> None:
        """CVAT resolves each `<points label=…>` in a `<skeleton>` against the labels."""
        root = self.root()
        declared = {
            label.findtext("name"): (label.findtext("type"), label.findtext("parent"))
            for label in root.iterfind("./meta/task/labels/label")
        }
        assert declared["person"] == ("skeleton", None)
        for joint in ("head", "hand", "foot"):
            assert declared[joint] == ("points", "person")

    def test_a_skeleton_is_written_with_its_named_joints_and_their_state(self) -> None:
        [skeleton] = self.root().iter("skeleton")
        joints = [
            (point.get("label"), point.get("occluded"), point.get("outside"))
            for point in skeleton.iter("points")
        ]
        assert joints == [("head", "0", "0"), ("hand", "1", "0"), ("foot", "0", "1")]

    def test_a_rotated_box_is_a_box_with_a_rotation(self) -> None:
        boxes = list(self.root().iter("box"))
        rotated = [box for box in boxes if box.get("rotation")]
        assert len(rotated) == 1
        assert float(rotated[0].get("rotation") or 0) == pytest.approx(ROTATION)

    def test_skeletons_and_rotated_boxes_come_back(self) -> None:
        files = export("cvat_xml")
        context = ImportContext(
            known_labels={},
            frame_by_name={"frame_000.jpg": 0, "frame_001.jpg": 1},
            frame_count=2,
            frame_sizes={0: (WIDTH, HEIGHT), 1: (WIDTH, HEIGHT)},
        )

        result = get_format("cvat_xml").import_(MemoryImportSource(files), context)

        by_type = {shape.shape_type: shape for _frame, shape in result.shapes}
        rotated = by_type[ShapeType.ROTATED_RECTANGLE]
        assert rotated.points == pytest.approx(ROTATED, abs=0.01)
        assert rotated.rotation == pytest.approx(ROTATION)
        skeleton = by_type[ShapeType.SKELETON]
        assert [e.get("name") for e in skeleton.elements] == ["head", "hand", "foot"]
        assert [bool(e.get("outside")) for e in skeleton.elements] == [False, False, True]

    def test_no_track_is_empty_and_none_closes_past_the_last_frame(self) -> None:
        """An empty `<track>` broke Datumaro; a closing frame past the end is a frame
        that does not exist, and CVAT's own dumper never writes one."""
        root = self.root(dataset(tracked=True, frames=1))
        assert all(len(track) > 0 for track in root.iter("track"))
        frames = {int(child.get("frame") or -1) for track in root.iter("track") for child in track}
        assert frames == {0}


# ----------------------------------------------------------------------- Ultralytics YOLO


def data_yaml(files: dict[str, bytes]) -> dict[str, str]:
    """The flat keys of `data.yaml` (this test needs no YAML library for them)."""
    keys: dict[str, str] = {}
    for line in files["data.yaml"].decode().splitlines():
        if line and not line.startswith((" ", "#")) and ":" in line:
            key, _, value = line.partition(":")
            keys[key.strip()] = value.strip()
    return keys


def img2label_path(image: str) -> str:
    """Ultralytics' rule: the last `/images/` becomes `/labels/`, the suffix `.txt`."""
    head, _, tail = f"/{image}".rpartition("/images/")
    return f"{head}/labels/{tail}".lstrip("/").rsplit(".", 1)[0] + ".txt"


def ultralytics_rows(
    files: dict[str, bytes], *, columns: int | None = None, pose: bool = False
) -> dict[str, list[list[float]]]:
    """Every label file a split of `data.yaml` points Ultralytics at, read its way.

    Coordinates must lie in [0, 1]; a pose row's visibility flags (0, 1, 2) are not
    coordinates, and Ultralytics range-checks only the joints' x and y.
    """
    keys = data_yaml(files)
    nc = int(keys["nc"])
    rows: dict[str, list[list[float]]] = {}
    for split in ("train", "val"):
        directory = keys[split].strip("/") + "/"
        images = [name for name in files if name.startswith(directory)]
        assert images, f"{split}: {directory} holds no images"
        for image in images:
            label = img2label_path(image)
            assert label in files, f"{image} has no label file at {label}"
            parsed = [
                [float(value) for value in line.split()]
                for line in files[label].decode().splitlines()
                if line.strip()
            ]
            for row in parsed:
                assert 0 <= row[0] < nc and row[0] == int(row[0])
                coordinates = (
                    row[1:5] + [v for i, v in enumerate(row[5:]) if i % 3 != 2] if pose else row[1:]
                )
                assert all(0.0 <= value <= 1.0 for value in coordinates), row
                if columns is not None:
                    assert len(row) == columns, row
            rows[label] = parsed
    return rows


class TestUltralyticsYolo:
    def test_data_yaml_points_at_the_images_written(self) -> None:
        """Until iteration 66 `data.yaml` named `images/train` and the images went to
        `images/default`: Ultralytics refused every export with "images not found"."""
        files = export("yolo")
        keys = data_yaml(files)
        assert keys["train"] == "images/train"
        assert keys["val"] == "images/train"  # no validation split: it trains, not fails
        assert ultralytics_rows(files)

    @pytest.mark.parametrize("format_id", ["yolo", "yolo_obb", "yolo_pose"])
    def test_data_yaml_resolves_from_its_own_directory(self, format_id: str) -> None:
        """Ultralytics resolves `path:` against the working directory, not the yaml's
        (`check_det_dataset`: `data.get("path") or Path(yaml_file).parent`). With
        `path: .`, as written until iteration 67, training from anywhere but inside the
        export failed with "images not found". Without the key, the yaml's directory is
        the root — found by running Ultralytics itself (`scripts/verify_ultralytics.py`)."""
        assert "path" not in data_yaml(export(format_id))

    def test_boxes_only_is_written_as_boxes(self) -> None:
        rows = ultralytics_rows(export("yolo", dataset(polygon=False)), columns=5)
        car, rotated = rows["labels/train/frame_000.txt"]
        assert car[1:] == pytest.approx(
            [35 / WIDTH, 35 / HEIGHT, 50 / WIDTH, 30 / HEIGHT], abs=1e-6
        )
        x1, y1, x2, y2 = enclosing(ROTATED, ROTATION)
        assert rotated[3] == pytest.approx((x2 - x1) / WIDTH, abs=1e-5)
        assert rotated[4] == pytest.approx((y2 - y1) / HEIGHT, abs=1e-5)

    def test_with_any_polygon_every_row_is_a_segment(self) -> None:
        """Ultralytics reads a file with any row longer than six values as segments
        throughout, so a box row beside a polygon became a two-point polygon."""
        rows = ultralytics_rows(export("yolo"))
        written = rows["labels/train/frame_000.txt"]
        assert all(len(row) > 6 and len(row) % 2 == 1 for row in written)
        # The box Ultralytics derives from the box's corners is the box.
        car = written[0][1:]
        xs, ys = car[0::2], car[1::2]
        assert [min(xs), min(ys), max(xs), max(ys)] == pytest.approx(
            [10 / WIDTH, 20 / HEIGHT, 60 / WIDTH, 50 / HEIGHT], abs=1e-6
        )

    def test_a_box_hanging_off_the_image_is_clipped_not_shifted(self) -> None:
        view = DatasetView(
            name="edge",
            labels=labels(),
            frames=iter(
                [
                    FrameRecord(
                        index=0,
                        name="a.jpg",
                        width=WIDTH,
                        height=HEIGHT,
                        shapes=[
                            ShapeRecord(
                                label="car",
                                shape_type=ShapeType.RECTANGLE,
                                points=[-40.0, 10.0, 60.0, 50.0],
                            )
                        ],
                        media=b"x",
                    )
                ]
            ),
        )
        [[_cls, cx, _cy, w, _h]] = ultralytics_rows(export("yolo", view))["labels/train/a.txt"]
        assert (cx - w / 2, cx + w / 2) == pytest.approx((0.0, 60 / WIDTH), abs=1e-6)

    def test_oriented_boxes_are_nine_values(self) -> None:
        rows = ultralytics_rows(export("yolo_obb"), columns=9)
        assert rows["labels/train/frame_000.txt"]

    def test_pose_rows_match_kpt_shape(self) -> None:
        files = export("yolo_pose")
        assert data_yaml(files)["kpt_shape"] == "[3, 3]"
        [person] = ultralytics_rows(files, columns=5 + 3 * 3, pose=True)[
            "labels/train/frame_000.txt"
        ]
        # Visible, occluded, absent — and an absent joint is all zeros.
        assert person[7::3] == [2.0, 1.0, 0.0]
        assert person[11:14] == [0.0, 0.0, 0.0]

    def pose_view(self, *keypoints: tuple[str, ...]) -> DatasetView:
        labels_ = [
            LabelSpec(id=str(i), name=f"body{i}", keypoints=names)
            for i, names in enumerate(keypoints)
        ]
        frame = FrameRecord(
            index=0,
            name="a.jpg",
            width=WIDTH,
            height=HEIGHT,
            shapes=[
                ShapeRecord(
                    label="body0",
                    shape_type=ShapeType.SKELETON,
                    points=[],
                    elements=[{"points": [10.0 + i, 20.0 + i]} for i in range(len(keypoints[0]))],
                )
            ],
            media=b"x",
        )
        return DatasetView(name="pose", labels=labels_, frames=iter([frame]))

    def test_pose_names_its_mirror_joints(self) -> None:
        """Without `flip_idx` Ultralytics turns horizontal-flip augmentation off; with a
        wrong one it teaches a model that a left eye is a right eye. Derived from names."""
        files = export(
            "yolo_pose",
            self.pose_view(
                ("nose", "left_eye", "right_eye", "l_wrist", "r_wrist", "Tail_L", "tail_r")
            ),
        )
        assert data_yaml(files)["flip_idx"] == "[0, 2, 1, 4, 3, 6, 5]"

    def test_pose_writes_no_flip_idx_it_cannot_derive(self) -> None:
        # Nothing names a side: whether "hand" has a mirror is unknowable, so no claim.
        assert "flip_idx" not in data_yaml(export("yolo_pose", self.pose_view(("head", "hand"))))
        # A side with no counterpart.
        assert "flip_idx" not in data_yaml(
            export("yolo_pose", self.pose_view(("nose", "left_eye")))
        )
        # Two skeleton labels whose joints mirror differently by position.
        assert "flip_idx" not in data_yaml(
            export(
                "yolo_pose",
                self.pose_view(("left_eye", "right_eye"), ("nose", "chin")),
            )
        )

    @pytest.mark.parametrize("format_id", ["yolo", "yolo_obb", "yolo_pose"])
    def test_a_val_subset_is_the_val_split(self, format_id: str) -> None:
        records = [
            FrameRecord(
                index=index,
                name=f"f{index}.jpg",
                width=WIDTH,
                height=HEIGHT,
                shapes=shapes(polygon=False),
                media=b"x",
                subset=subset,
            )
            for index, subset in enumerate(["default", "val"])
        ]
        files = export(format_id, DatasetView(name="split", labels=labels(), frames=iter(records)))
        assert data_yaml(files)["val"] == "images/val"
        assert "images/val/f1.jpg" in files and "labels/val/f1.txt" in files
        assert ultralytics_rows(files, pose=format_id == "yolo_pose")

    @pytest.mark.parametrize("subsets", [("default", "default"), ("default", "val")])
    def test_classification_asks_for_a_val_split_only_without_one(
        self, subsets: tuple[str, str]
    ) -> None:
        records = [
            FrameRecord(
                index=i, name=f"{i}.jpg", width=8, height=8, tags=["cat"], media=b"x", subset=subset
            )
            for i, subset in enumerate(subsets)
        ]
        files = export(
            "yolo_classification",
            DatasetView(name="cls", labels=[LabelSpec(id="1", name="cat")], frames=iter(records)),
        )
        asks = "val/" in files["README.txt"].decode()
        assert asks == ("val" not in subsets)
        assert ("val/cat/1.jpg" in files) == ("val" in subsets)

    def test_classification_is_split_then_class(self) -> None:
        view = DatasetView(
            name="cls",
            labels=[LabelSpec(id="1", name="cat"), LabelSpec(id="2", name="dog")],
            frames=iter(
                [
                    FrameRecord(index=0, name="a.jpg", width=8, height=8, tags=["cat"], media=b"x"),
                    FrameRecord(index=1, name="b.jpg", width=8, height=8, tags=["dog"], media=b"y"),
                ]
            ),
        )
        files = export("yolo_classification", view)
        assert "train/cat/a.jpg" in files
        assert "train/dog/b.jpg" in files
