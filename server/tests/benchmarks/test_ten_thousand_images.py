"""A 10,000-image task, through the real API, the way the web app drives it.

`pytest -m benchmark -s tests/benchmarks/test_ten_thousand_images.py`

`CURVEVISION_BENCH_IMAGES=100000` runs it at a lakh. `CURVEVISION_BENCH_SEGMENT=0` makes the
whole task one job, which is what the web app creates.

Every step an annotation project at that size takes, timed: upload in the batches the web
app sends, open the task and its media, open a frame at the far end, save annotations on
every frame the way autosave does, read a job's annotations back, and export as COCO and
YOLO. The ceilings are loose -- an order of magnitude over what was measured -- so that a
slow runner stays green and an accidental O(n^2) does not.
"""

from __future__ import annotations

import io
import json
import os
import resource
import time
import zipfile
from typing import Any

import pytest

from tests.api.test_collaboration import label_id
from tests.conftest import ApiActor

pytestmark = pytest.mark.benchmark

IMAGES = int(os.environ.get("CURVEVISION_BENCH_IMAGES", "10000"))
#: Frames per job; 0 is one job for the whole task.
SEGMENT = int(os.environ.get("CURVEVISION_BENCH_SEGMENT", "500"))
#: What the web app sends per upload request (`web/src/features/tasks/uploadBatches.ts`).
BATCH = 100


def _png(index: int) -> bytes:
    from PIL import Image

    # Unique per image, or content addressing would store one blob for all of them.
    image = Image.new("RGB", (64, 48), (index % 256, (index // 256) % 256, 90))
    buffer = io.BytesIO()
    image.save(buffer, format="PNG")
    return buffer.getvalue()


class Clock:
    def __init__(self) -> None:
        self.rows: list[tuple[str, float]] = []

    def lap(self, name: str, started: float) -> float:
        elapsed = time.perf_counter() - started
        self.rows.append((name, elapsed))
        print(f"  {name:<52} {elapsed:8.2f} s")
        return elapsed


async def test_a_ten_thousand_image_task_end_to_end(
    owner: ApiActor, project: dict[str, Any]
) -> None:
    clock = Clock()
    print(f"\n{IMAGES:,} images")

    task = (
        await owner.post(
            "/api/v1/tasks",
            json={"project_id": project["id"], "name": "Ten thousand", "segment_size": SEGMENT},
        )
    ).json()
    images = [_png(i) for i in range(IMAGES)]

    started = time.perf_counter()
    batch_times: list[float] = []
    for first in range(0, IMAGES, BATCH):
        batch_started = time.perf_counter()
        response = await owner.post(
            f"/api/v1/tasks/{task['id']}/assets",
            files=[
                ("files", (f"img_{i:05d}.png", images[i], "image/png"))
                for i in range(first, min(first + BATCH, IMAGES))
            ],
        )
        assert response.is_success, response.text[:300]
        batch_times.append(time.perf_counter() - batch_started)
    upload = clock.lap(f"upload, {IMAGES // BATCH} requests of {BATCH}", started)
    print(f"    first batch {batch_times[0]:.2f} s, last batch {batch_times[-1]:.2f} s")

    started = time.perf_counter()
    detail = (await owner.get(f"/api/v1/tasks/{task['id']}")).json()
    clock.lap("open the task", started)
    assert detail["frame_count"] == IMAGES

    started = time.perf_counter()
    jobs = (await owner.get(f"/api/v1/tasks/{task['id']}/jobs")).json()
    clock.lap("list its jobs", started)
    assert len(jobs) == (IMAGES // SEGMENT if SEGMENT else 1)

    started = time.perf_counter()
    media = await owner.get(f"/api/v1/tasks/{task['id']}/media")
    clock.lap("the editor's media metadata", started)
    assert media.is_success, media.text[:300]

    started = time.perf_counter()
    last = await owner.get(f"/api/v1/tasks/{task['id']}/frames/{IMAGES - 1}/data")
    clock.lap("open the last frame", started)
    assert last.content == images[-1]

    # Two boxes on every frame, saved per job the way autosave sends them.
    car, person = label_id(project, "car"), label_id(project, "pedestrian")
    started = time.perf_counter()
    for job in jobs:
        # In windows of 500 frames, as an annotator's autosaves arrive, not one request
        # carrying a whole 100,000-frame job.
        for window in range(job["start_frame"], job["stop_frame"] + 1, 500):
            shapes = [
                {
                    "label_id": label,
                    "frame": frame,
                    "shape_type": "rectangle",
                    "points": [4.5 + offset, 6.25, 30.75 + offset, 40.0],
                }
                for frame in range(window, min(window + 500, job["stop_frame"] + 1))
                for label, offset in ((car, 0.0), (person, 10.0))
            ]
            saved = await owner.patch(
                f"/api/v1/jobs/{job['id']}/annotations", json={"created_shapes": shapes}
            )
            assert saved.is_success, saved.text[:300]
    annotate = clock.lap(f"save {2 * IMAGES:,} boxes, 500 frames a request", started)

    started = time.perf_counter()
    response = await owner.get(f"/api/v1/jobs/{jobs[-1]['id']}/annotations")
    loaded = response.json()
    expected = 2 * (SEGMENT or IMAGES)
    clock.lap(f"load one job's annotations ({expected:,} boxes)", started)
    print(f"    {len(response.content) / 1e6:.1f} MB of JSON")
    assert len(loaded["shapes"]) == expected

    exports: dict[str, float] = {}
    rss_before = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / 1024
    for fmt in ("coco", "yolo"):
        started = time.perf_counter()
        export = await owner.post(
            f"/api/v1/projects/{project['id']}/export",
            json={"format": fmt, "include_images": True},
        )
        exports[fmt] = clock.lap(f"export {fmt} with images", started)
        assert export.is_success, export.text[:300]
        with zipfile.ZipFile(io.BytesIO(export.content)) as archive:
            names = archive.namelist()
            if fmt == "coco":
                document = json.loads(archive.read("annotations/instances_default.json"))
                assert len(document["images"]) == IMAGES
                assert len(document["annotations"]) == 2 * IMAGES
            assert sum(1 for name in names if name.endswith(".png")) == IMAGES

    rss_after = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / 1024
    print(f"    peak memory before exports {rss_before:.0f} MB, after {rss_after:.0f} MB")

    # Ceilings an order of magnitude over what was measured on a laptop-class runner.
    scale = IMAGES / 10_000
    assert upload < 600 * scale
    assert batch_times[-1] < 3 * max(batch_times[0], 0.5), "upload slows as the task grows"
    assert annotate < 300 * scale
    assert max(exports.values()) < 300 * scale
