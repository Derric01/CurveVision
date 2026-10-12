"""A COCO export through the real route, read by pycocotools: masks and rotated boxes included.

`tests/unit/test_coco_reference.py` holds the exporter to the reference reader on in-memory
data; this checks the service hands it what it needs — a mask's frame size above all, since
COCO's RLE spans the whole image.
"""

from __future__ import annotations

import io
import json
import zipfile
from contextlib import redirect_stdout
from pathlib import Path
from typing import Any

import pytest

from curvevision.formats.rle import encode
from tests.api.test_collaboration import label_id, make_task
from tests.conftest import ApiActor

np = pytest.importorskip("numpy")
coco_api = pytest.importorskip("pycocotools.coco")


async def test_a_drawn_mask_and_rotated_box_export_as_coco_reads_them(
    owner: ApiActor, project: dict[str, Any], tmp_path: Path
) -> None:
    _task, jobs = await make_task(owner, project, frames=1)  # 640 x 480 frames
    width, height = 30, 20
    flags = [x < 10 or y >= 15 for y in range(height) for x in range(width)]
    mask = {
        "rle": encode(flags, width, height),
        "left": 50,
        "top": 60,
        "width": width,
        "height": height,
    }
    drawn = await owner.patch(
        f"/api/v1/jobs/{jobs[0]['id']}/annotations",
        json={
            "created_shapes": [
                {
                    "label_id": label_id(project, "car"),
                    "frame": 0,
                    "shape_type": "mask",
                    "points": [50, 60, 79, 79],
                    "mask": mask,
                },
                {
                    "label_id": label_id(project, "pedestrian"),
                    "frame": 0,
                    "shape_type": "rotated_rectangle",
                    "points": [200, 200, 300, 240],
                    "rotation": 90,
                },
            ]
        },
    )
    assert drawn.status_code == 200, drawn.text

    export = await owner.post(f"/api/v1/projects/{project['id']}/export", json={"format": "coco"})

    assert export.status_code == 200, export.text
    assert "X-CurveVision-Warnings" not in export.headers  # nothing was dropped
    with zipfile.ZipFile(io.BytesIO(export.content)) as archive:
        document = archive.read("annotations/instances_default.json")
    path = tmp_path / "instances.json"
    path.write_bytes(document)
    with redirect_stdout(io.StringIO()):
        coco = coco_api.COCO(str(path))
    annotations = json.loads(document)["annotations"]
    [mask_ann] = [a for a in annotations if isinstance(a["segmentation"], dict)]
    assert int(coco.annToMask(mask_ann).sum()) == sum(flags)
    assert mask_ann["bbox"] == [50.0, 60.0, 30.0, 20.0]
    [rotated] = [a for a in annotations if a is not mask_ann]
    # 100 x 40 turned a quarter: 40 wide and 100 tall about the same centre (250, 220).
    assert rotated["bbox"] == pytest.approx([230.0, 170.0, 40.0, 100.0], abs=0.01)
