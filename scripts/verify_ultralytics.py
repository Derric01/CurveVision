#!/usr/bin/env python3
"""CurveVision's YOLO exports, read and trained on by Ultralytics itself.

    python scripts/verify_ultralytics.py            # read every export, then train one epoch each
    python scripts/verify_ultralytics.py --no-train # read only (seconds rather than minutes)

`tests/unit/test_export_conformance.py` restates Ultralytics' loader rules so the core suite
needs no PyTorch. A restatement can be wrong in the same way as the exporter, so this runs
the real thing, for each of the five Ultralytics tasks CurveVision exports:

1. `check_det_dataset` / `check_cls_dataset` accept the archive's layout and `data.yaml`.
2. Ultralytics' own dataset classes (`YOLODataset`, `ClassificationDataset`) load every
   image with its labels — nothing reported missing or corrupt — and what they hold is what
   was annotated: class, box, polygon, oriented corners, keypoints and their visibility,
   computed here from the source geometry rather than with the exporter's helpers.
3. One epoch of training, from a model definition (no weights to download), runs through
   to validation.

Detection is checked twice: a dataset of boxes only, and one with a polygon in it, which is
written as segments throughout and must still give a detector the original boxes.

Needs the server package and `ultralytics` in the same interpreter (CPU PyTorch is enough).
Run by `.github/workflows/ultralytics.yml`.
"""

from __future__ import annotations

import argparse
import io
import math
import sys
import tempfile
from pathlib import Path
from typing import Any

WIDTH, HEIGHT = 160, 120
FRAMES = [("default", "day"), ("default", "night")] * 3 + [("val", "day"), ("val", "night")]
JOINTS = ("nose", "left_eye", "right_eye", "left_hand", "right_hand")


def box(i: int) -> list[float]:
    return [10.0 + 3 * i, 12.0, 60.0 + 3 * i, 50.0]


def rotated(i: int) -> tuple[list[float], float]:
    return [80.0, 60.0, 130.0, 80.0], 15.0 + 5 * i


def polygon(i: int) -> list[float]:
    return [20.0, 70.0 + i, 70.0, 75.0, 45.0, 110.0]


def skeleton(i: int) -> list[dict[str, Any]]:
    return [
        {"points": [100.0, 20.0 + i]},
        {"points": [95.0, 15.0 + i], "occluded": True},
        {"points": [105.0, 15.0 + i]},
        {"points": [85.0, 40.0 + i]},
        {"points": [0.0, 0.0], "outside": True},  # absent, stale position
    ]


def image(i: int) -> bytes:
    from PIL import Image, ImageDraw

    canvas = Image.new("RGB", (WIDTH, HEIGHT), "#1e293b" if i % 2 else "#e2e8f0")
    draw = ImageDraw.Draw(canvas)
    draw.rectangle(box(i), outline="#ef4444", width=2)
    draw.polygon(polygon(i), fill="#22c55e")
    buffer = io.BytesIO()
    canvas.save(buffer, format="JPEG")
    return buffer.getvalue()


def dataset(*, with_polygon: bool) -> Any:
    from curvevision.domain.enums import ShapeType
    from curvevision.formats import DatasetView, FrameRecord, LabelSpec, ShapeRecord

    labels = [
        LabelSpec(id="1", name="car"),
        LabelSpec(id="2", name="road"),
        LabelSpec(id="3", name="person", keypoints=JOINTS),
    ]
    frames = []
    for i, (subset, tag) in enumerate(FRAMES):
        points, angle = rotated(i)
        shapes = [
            ShapeRecord(label="car", shape_type=ShapeType.RECTANGLE, points=box(i)),
            ShapeRecord(
                label="car", shape_type=ShapeType.ROTATED_RECTANGLE, points=points, rotation=angle
            ),
            ShapeRecord(label="person", shape_type=ShapeType.SKELETON, points=[],
                        elements=skeleton(i)),
        ]
        if with_polygon:
            shapes.append(
                ShapeRecord(label="road", shape_type=ShapeType.POLYGON, points=polygon(i))
            )
        frames.append(FrameRecord(index=i, name=f"frame_{i:02d}.jpg", width=WIDTH,
                                  height=HEIGHT, shapes=shapes, tags=[tag], media=image(i),
                                  subset=subset))
    return DatasetView(name="ultralytics-check", labels=labels, frames=iter(frames))


def export(format_id: str, view: Any, root: Path) -> Path:
    from curvevision.formats import MemoryExportSink, get_format

    sink = MemoryExportSink()
    get_format(format_id).export(view, sink)
    for name, data in sink.files.items():
        path = root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)
    return root


# ---------------------------------------------------------------- expected, from the source


def corners_of(points: list[float], angle: float) -> list[tuple[float, float]]:
    x1, y1, x2, y2 = points
    cx, cy, hw, hh = (x1 + x2) / 2, (y1 + y2) / 2, (x2 - x1) / 2, (y2 - y1) / 2
    cos, sin = math.cos(math.radians(angle)), math.sin(math.radians(angle))
    return [(cx + dx * cos - dy * sin, cy + dx * sin + dy * cos)
            for dx, dy in ((-hw, -hh), (hw, -hh), (hw, hh), (-hw, hh))]


def xywhn(xs: list[float], ys: list[float]) -> list[float]:
    x1, x2 = max(0.0, min(xs)), min(float(WIDTH), max(xs))
    y1, y2 = max(0.0, min(ys)), min(float(HEIGHT), max(ys))
    return [(x1 + x2) / 2 / WIDTH, (y1 + y2) / 2 / HEIGHT, (x2 - x1) / WIDTH, (y2 - y1) / HEIGHT]


def expected_boxes(i: int, *, with_polygon: bool) -> list[tuple[int, list[float]]]:
    """(class, normalised xywh) for each object a detector should learn from frame `i`."""
    x1, y1, x2, y2 = box(i)
    corners = corners_of(*rotated(i))
    found = [
        (0, xywhn([x1, x2], [y1, y2])),
        (0, xywhn([x for x, _ in corners], [y for _, y in corners])),
    ]
    if with_polygon:
        p = polygon(i)
        found.append((1, xywhn(p[0::2], p[1::2])))
    return found


# --------------------------------------------------------------------------- the checks


class Checks:
    def __init__(self) -> None:
        self.failures: list[str] = []

    def __call__(self, condition: bool, passed: str, failed: str) -> None:
        if condition:
            print(f"  ok   {passed}")
        else:
            self.failures.append(failed)
            print(f"  FAIL {failed}")


def close(a: Any, b: Any, tolerance: float = 2e-5) -> bool:
    import numpy as np

    a, b = np.asarray(a, dtype=float), np.asarray(b, dtype=float)
    return a.shape == b.shape and bool(np.allclose(a, b, atol=tolerance))


def frame_of(label: dict[str, Any]) -> int:
    return int(Path(label["im_file"]).stem.split("_")[1])


def load(data_yaml: Path, task: str, split: str = "train") -> tuple[dict[str, Any], Any]:
    from ultralytics.cfg import get_cfg
    from ultralytics.data import YOLODataset
    from ultralytics.data.utils import check_det_dataset

    data = check_det_dataset(str(data_yaml))
    cfg = get_cfg()
    dataset = YOLODataset(img_path=data[split], imgsz=64, augment=False, hyp=cfg,
                          data=data, task=task, prefix=f"{task} {split}: ")
    return data, dataset


def sorted_rows(classes: Any, rows: Any) -> list[tuple[int, list[float]]]:
    pairs = [
        (int(c), [round(float(v), 5) for v in row])
        for c, row in zip(classes, rows, strict=True)
    ]
    return sorted(pairs)


def check_detect(check: Checks, root: Path, *, with_polygon: bool) -> None:
    what = "with a polygon (segment rows)" if with_polygon else "boxes only"
    data, dataset = load(root / "data.yaml", "detect")
    check(len(dataset.labels) == 6 and Path(data["val"]).name == "val",
          f"detect, {what}: all 6 training images load, and val is the val split",
          f"detect, {what}: {len(dataset.labels)} images loaded, val={data['val']}")
    wrong = []
    for label in dataset.labels:
        i = frame_of(label)
        got = sorted_rows(label["cls"].ravel(), label["bboxes"])
        want = sorted((c, [round(v, 5) for v in b]) for c, b in
                      expected_boxes(i, with_polygon=with_polygon))
        if not all(g[0] == w[0] and close(g[1], w[1]) for g, w in zip(got, want, strict=False)) \
                or len(got) != len(want):
            wrong.append((i, got, want))
    check(not wrong, f"detect, {what}: every box is the annotated box",
          f"detect, {what}: boxes differ, first: {wrong[:1]}")


def check_segment(check: Checks, root: Path) -> None:
    _data, dataset = load(root / "data.yaml", "segment")
    wrong = []
    for label in dataset.labels:
        i = frame_of(label)
        segments = label["segments"]
        road = [s for c, s in zip(label["cls"].ravel(), segments, strict=True) if int(c) == 1]
        p = polygon(i)
        want = [[p[k] / WIDTH, p[k + 1] / HEIGHT] for k in range(0, len(p), 2)]
        # Ultralytics resamples every segment to a fixed point count; the original vertices
        # must all be on it.
        ok = len(segments) == 3 and len(road) == 1 and all(
            min(math.dist(v, s) for s in road[0]) < 1e-4 for v in want)
        if not ok:
            wrong.append(i)
    check(not wrong, "segment: every object has its polygon, the road's vertices intact",
          f"segment: frames with wrong segments: {wrong}")


def check_obb(check: Checks, root: Path) -> None:
    data, dataset = load(root / "data.yaml", "obb")
    check(Path(data["val"]).name == "val", "obb: val is the val split",
          f"obb: val is {data['val']}")
    wrong = []
    for label in dataset.labels:
        i = frame_of(label)
        corners = corners_of(*rotated(i))
        want = [[x / WIDTH, y / HEIGHT] for x, y in corners]
        found = [
            s for c, s in zip(label["cls"].ravel(), label["segments"], strict=True) if int(c) == 0
        ]
        if not any(close(sorted(map(tuple, s.tolist())), sorted(map(tuple, want)), 1e-5)
                   for s in found):
            wrong.append(i)
    check(not wrong, "obb: the rotated box's four corners arrive as drawn",
          f"obb: frames where the rotated box's corners differ: {wrong}")


def check_pose(check: Checks, root: Path) -> None:
    data, dataset = load(root / "data.yaml", "pose")
    check(list(data["kpt_shape"]) == [5, 3]
          and list(data.get("flip_idx", [])) == [0, 2, 1, 4, 3],
          "pose: kpt_shape, and flip_idx pairing each left joint with its right",
          f"pose: kpt_shape {data.get('kpt_shape')}, flip_idx {data.get('flip_idx')}")
    wrong = []
    for label in dataset.labels:
        i = frame_of(label)
        keypoints = label["keypoints"]
        if keypoints is None or keypoints.shape != (1, 5, 3):
            wrong.append((i, None if keypoints is None else keypoints.shape))
            continue
        joints = skeleton(i)
        visibility = [0 if j.get("outside") else 1 if j.get("occluded") else 2 for j in joints]
        xy = [[0.0, 0.0] if v == 0 else [j["points"][0] / WIDTH, j["points"][1] / HEIGHT]
              for j, v in zip(joints, visibility, strict=True)]
        if not (close(keypoints[0, :, :2], xy, 1e-5) and close(keypoints[0, :, 2], visibility)):
            wrong.append((i, keypoints[0].tolist()))
    check(not wrong, "pose: every joint where it was put, visible/occluded/absent as marked",
          f"pose: joints differ, first: {wrong[:1]}")


def check_classify(check: Checks, root: Path) -> None:
    from ultralytics.cfg import get_cfg
    from ultralytics.data import ClassificationDataset
    from ultralytics.data.utils import check_cls_dataset

    data = check_cls_dataset(str(root))
    names = list(data["names"].values())
    dataset = ClassificationDataset(root=str(root / "train"), args=get_cfg(), augment=False)
    filed = {Path(path).stem: names[index] for path, index, *_ in dataset.samples}
    want = {f"frame_{i:02d}": tag for i, (subset, tag) in enumerate(FRAMES)
            if subset == "default"}
    check(sorted(names) == ["day", "night"] and filed == want and data.get("val") is not None,
          "classify: every image under its tag, with a val split",
          f"classify: names {names}, filed {filed}, val {data.get('val')}")


def train(check: Checks, task: str, model: str, data: Path, workdir: Path) -> None:
    from ultralytics import YOLO

    try:
        result = YOLO(model).train(
            data=str(data), epochs=1, imgsz=64, batch=4, device="cpu", workers=0,
            plots=False, amp=False, verbose=False, project=str(workdir), name=task,
            exist_ok=True,
        )
        check(result is not None, f"train {task}: one epoch on the export, through validation",
              f"train {task}: no result")
    except Exception as exc:  # report every task, not the first failure
        check(False, "", f"train {task}: {type(exc).__name__}: {exc}")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n", 1)[0])
    parser.add_argument("--no-train", action="store_true", help="read the exports only")
    args = parser.parse_args()

    import ultralytics

    print(f"ultralytics {ultralytics.__version__}")
    check = Checks()
    with tempfile.TemporaryDirectory(prefix="curvevision-ultralytics-") as workspace:
        work = Path(workspace)
        boxes = export("yolo", dataset(with_polygon=False), work / "detect")
        mixed = export("yolo", dataset(with_polygon=True), work / "segment")
        obb = export("yolo_obb", dataset(with_polygon=False), work / "obb")
        pose = export("yolo_pose", dataset(with_polygon=False), work / "pose")
        classes = export("yolo_classification", dataset(with_polygon=False), work / "classify")

        readers: list[tuple[str, Any]] = [
            ("detect, boxes only", lambda: check_detect(check, boxes, with_polygon=False)),
            ("detect, with a polygon", lambda: check_detect(check, mixed, with_polygon=True)),
            ("segment", lambda: check_segment(check, mixed)),
            ("obb", lambda: check_obb(check, obb)),
            ("pose", lambda: check_pose(check, pose)),
            ("classify", lambda: check_classify(check, classes)),
        ]
        for name, read in readers:
            # Ultralytics refuses a dataset by raising — "No valid images found" when every
            # label file is corrupt to it. That is a failed check, not the end of the run.
            try:
                read()
            except Exception as exc:
                check(False, "", f"{name}: Ultralytics refused the export: "
                                 f"{type(exc).__name__}: {str(exc).splitlines()[0]}")

        if not args.no_train:
            runs = work / "runs"
            train(check, "detect", "yolo11n.yaml", boxes / "data.yaml", runs)
            train(check, "segment", "yolo11n-seg.yaml", mixed / "data.yaml", runs)
            train(check, "obb", "yolo11n-obb.yaml", obb / "data.yaml", runs)
            train(check, "pose", "yolo11n-pose.yaml", pose / "data.yaml", runs)
            train(check, "classify", "yolo11n-cls.yaml", classes, runs)

    print()
    if check.failures:
        print(f"{len(check.failures)} check(s) failed")
        return 1
    print("all checks passed")
    return 0


if __name__ == "__main__":
    sys.exit(main())
