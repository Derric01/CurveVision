"""COCO exports, checked by `pycocotools` — the reference implementation every COCO consumer
(Detectron2, MMDetection, torchvision's references, Ultralytics' converters) reads through.

The round trips in `test_formats.py` prove an export reads back through *this* project's
importer, which cannot catch a convention got wrong the same way in both directions. These
hold the export to the reader the rest of the industry uses:

* it loads, and every annotation rasterises (`annToMask`);
* each `bbox` is the tight box of what the annotation covers;
* a mask is the same pixels after the round trip through COCO's RLE (column-major — the
  classic way to scramble a mask is to write it row-major);
* keypoints follow the spec: `num_keypoints` counts every labelled joint (v > 0), an absent
  joint is `0,0,0`, and the box covers only the joints that are there;
* `COCOeval` scores the ground truth against itself at AP 1.0 for boxes, masks and
  keypoints — a document a framework cannot evaluate is one it cannot train on either.
"""

from __future__ import annotations

import io
import json
from contextlib import redirect_stdout
from pathlib import Path

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
from curvevision.formats.rle import encode, pixels

np = pytest.importorskip("numpy")
coco_api = pytest.importorskip("pycocotools.coco")
coco_eval = pytest.importorskip("pycocotools.cocoeval")
mask_utils = pytest.importorskip("pycocotools.mask")

WIDTH, HEIGHT = 640, 480
ROTATED = [200.0, 300.0, 300.0, 340.0]
ROTATION = 30.0


def road_mask() -> dict:
    """An L-shaped mask, so a transposed or row-major decode cannot pass by symmetry."""
    left, top, width, height = 400, 100, 60, 40
    flags = [
        (x < 20) or (y >= 30)  # a vertical bar down the left, a horizontal bar along the foot
        for y in range(height)
        for x in range(width)
    ]
    return {
        "rle": encode(flags, width, height),
        "left": left,
        "top": top,
        "width": width,
        "height": height,
    }


SKELETON = [
    {"label_id": "j1", "points": [100.0, 50.0], "occluded": False, "outside": False},
    {"label_id": "j2", "points": [110.0, 45.0], "occluded": True, "outside": False},
    # Absent, with stale coordinates left over from an earlier position.
    {"label_id": "j3", "points": [600.0, 470.0], "occluded": False, "outside": True},
]


def dataset() -> DatasetView:
    labels = [
        LabelSpec(id="1", name="car"),
        LabelSpec(id="2", name="pedestrian"),
        LabelSpec(id="3", name="road"),
        LabelSpec(
            id="4",
            name="person",
            keypoints=("nose", "left_eye", "right_eye"),
            skeleton_edges=((0, 1), (0, 2)),
        ),
    ]
    shapes = [
        ShapeRecord(label="car", shape_type=ShapeType.RECTANGLE, points=[10.0, 20.0, 110.0, 70.0]),
        ShapeRecord(
            label="car",
            shape_type=ShapeType.ROTATED_RECTANGLE,
            points=list(ROTATED),
            rotation=ROTATION,
        ),
        ShapeRecord(  # concave: an arrow head
            label="pedestrian",
            shape_type=ShapeType.POLYGON,
            points=[300.0, 50.0, 380.0, 90.0, 300.0, 130.0, 330.0, 90.0],
        ),
        ShapeRecord(label="road", shape_type=ShapeType.MASK, points=[], mask=road_mask()),
        ShapeRecord(label="person", shape_type=ShapeType.SKELETON, points=[], elements=SKELETON),
    ]
    frame = FrameRecord(index=0, name="street.jpg", width=WIDTH, height=HEIGHT, shapes=shapes)
    return DatasetView(name="Street", labels=labels, frames=iter([frame]))


@pytest.fixture
def exported(tmp_path: Path) -> tuple[dict, object]:
    sink = MemoryExportSink()
    get_format("coco").export(dataset(), sink)
    path = tmp_path / "instances.json"
    path.write_bytes(sink.files["annotations/instances_default.json"])
    with redirect_stdout(io.StringIO()):
        coco = coco_api.COCO(str(path))
    return json.loads(path.read_text()), coco


def annotation(document: dict, category: str, *, index: int = 0) -> dict:
    category_id = next(c["id"] for c in document["categories"] if c["name"] == category)
    return [a for a in document["annotations"] if a["category_id"] == category_id][index]


def tight_box(mask) -> list[float]:
    ys, xs = np.nonzero(mask)
    return [
        float(xs.min()),
        float(ys.min()),
        float(xs.max() + 1 - xs.min()),
        float(ys.max() + 1 - ys.min()),
    ]


class TestTheReferenceReader:
    def test_every_shape_coco_can_carry_is_exported_and_rasterises(self, exported) -> None:
        document, coco = exported
        assert len(document["annotations"]) == 5  # the mask included
        # Rasterised where there is something to rasterise: a box is written with
        # `segmentation: []`, as CVAT and Datumaro write one, and pycocotools cannot rasterise
        # an empty segmentation.
        for ann in document["annotations"]:
            if ann.get("segmentation"):
                assert coco.annToMask(ann).sum() > 0, ann

    def test_a_mask_is_the_same_pixels_after_coco_s_rle(self, exported) -> None:
        document, coco = exported
        road = annotation(document, "road")

        decoded = coco.annToMask(road)

        expected = np.zeros((HEIGHT, WIDTH), dtype=np.uint8)
        for x, y in pixels(road_mask()):
            expected[y, x] = 1
        assert np.array_equal(decoded, expected)
        assert road["iscrowd"] == 0  # an instance to train on, not a region to ignore
        assert road["bbox"] == tight_box(expected)
        assert road["area"] == pytest.approx(float(expected.sum()))

    def test_a_rotated_box_s_bbox_encloses_the_rotated_box(self, exported) -> None:
        document, _coco = exported
        rotated = annotation(document, "car", index=1)

        corners = rotated_corners(ROTATED, ROTATION)
        xs, ys = corners[0::2], corners[1::2]
        expected = [min(xs), min(ys), max(xs) - min(xs), max(ys) - min(ys)]
        assert rotated["bbox"] == pytest.approx(expected, abs=0.01)
        # And its exact shape travels as a polygon, for anything that reads segmentation.
        assert rotated["segmentation"][0] == pytest.approx(corners, abs=0.01)

    def test_keypoints_follow_the_spec(self, exported) -> None:
        document, _coco = exported
        person = annotation(document, "person")

        assert person["keypoints"] == [100.0, 50.0, 2, 110.0, 45.0, 1, 0, 0, 0]
        assert person["num_keypoints"] == 2  # labelled: visible *and* occluded
        # The box covers the joints that are there, not the absent one's stale position.
        assert person["bbox"] == [100.0, 45.0, 10.0, 5.0]

    @pytest.mark.parametrize("iou_type", ["bbox", "segm", "keypoints"])
    def test_cocoeval_scores_the_ground_truth_against_itself_at_one(
        self, exported, iou_type: str
    ) -> None:
        document, coco = exported

        # Each kind of evaluation over the annotations it applies to: boxes for all, masks
        # for those with a segmentation, keypoints for the skeleton.
        def applies(ann: dict) -> bool:
            if iou_type == "keypoints":
                return bool(ann.get("keypoints"))
            if iou_type == "segm":
                return bool(ann.get("segmentation"))
            return True

        scored = [ann for ann in document["annotations"] if applies(ann)]
        results = []
        for ann in scored:
            result = {
                "image_id": ann["image_id"],
                "category_id": ann["category_id"],
                "score": 1.0,
                "bbox": ann["bbox"],
            }
            if iou_type == "segm":
                rle = coco.annToRLE(ann)
                rle["counts"] = rle["counts"].decode("ascii")
                result["segmentation"] = rle
            if iou_type == "keypoints":
                result["keypoints"] = ann["keypoints"]
            results.append(result)

        with redirect_stdout(io.StringIO()):
            evaluator = coco_eval.COCOeval(coco, coco.loadRes(results), iou_type)
            # Categories every annotation of which the evaluation applies to: pycocotools
            # cannot segm-evaluate a category that also holds a box with no segmentation.
            evaluator.params.catIds = sorted(
                {ann["category_id"] for ann in scored}
                - {ann["category_id"] for ann in document["annotations"] if not applies(ann)}
            )
            if iou_type == "keypoints":
                # Per-joint tolerances for a three-joint skeleton (COCO's defaults are for
                # its own 17 person joints).
                evaluator.params.kpt_oks_sigmas = np.full(3, 0.05)
            evaluator.evaluate()
            evaluator.accumulate()
            evaluator.summarize()

        assert evaluator.stats[0] == pytest.approx(1.0)  # AP @ IoU .50:.95


class TestReadingBack:
    def test_a_mask_and_a_rotated_box_come_back_with_their_pixels(self) -> None:
        sink = MemoryExportSink()
        get_format("coco").export(dataset(), sink)
        context = ImportContext(
            known_labels={},
            frame_by_name={"street.jpg": 0},
            frame_count=1,
            frame_sizes={0: (WIDTH, HEIGHT)},
        )

        result = get_format("coco").import_(MemoryImportSource(sink.files), context)

        records = [record for _frame, record in result.shapes]
        [road] = [r for r in records if r.label == "road"]
        assert road.shape_type is ShapeType.MASK
        assert set(pixels(road.mask)) == set(pixels(road_mask()))
        rotated = [r for r in records if r.label == "car" and r.shape_type is ShapeType.POLYGON]
        assert len(rotated) == 1
        assert rotated[0].points == pytest.approx(rotated_corners(ROTATED, ROTATION), abs=0.01)

    def test_a_compressed_rle_made_by_pycocotools_reads_as_the_same_mask(self) -> None:
        """Detectron2 and pycocotools-made files compress the counts into a string."""
        from curvevision.formats.rle import from_coco_rle

        expected = np.zeros((HEIGHT, WIDTH), dtype=np.uint8, order="F")
        for x, y in pixels(road_mask()):
            expected[y, x] = 1
        compressed = mask_utils.encode(expected)
        assert isinstance(compressed["counts"], bytes)

        mask = from_coco_rle(
            {"size": compressed["size"], "counts": compressed["counts"].decode("ascii")}
        )

        assert mask is not None
        assert set(pixels(mask)) == set(pixels(road_mask()))
