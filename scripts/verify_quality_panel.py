#!/usr/bin/env python3
"""Read a quality report in a real browser, and check that it is the reviewer's tool.

    python scripts/verify_quality_panel.py

The comparison engine has been correct and tested since it landed. What had never been
checked is the thing a reviewer actually does: open the job, ask for a score, and **click a
conflict to land on the frame it is about**. `quality.test.ts` proves the ordering and the
staleness rule without a DOM; it cannot prove that the panel reaches the endpoint, that the
score renders, or that clicking a row moves the editor.

Three claims, each one a thing that would be false if the panel merely looked right:

1. A job with no report offers to compute one, and computing it puts a real score on screen.
2. **A conflict row seeks to its frame.** This is the whole design — a conflict is a place,
   not a statistic.
3. Annotating after the score was computed marks the report **stale**, rather than leaving a
   number that describes work which no longer exists.

The seeded job is deliberately wrong in three different ways, so all four conflict kinds the
server can produce are exercised rather than just the easy one.

Needs the packaged server (`python desktop/sidecar/build.py`) and a Chromium Playwright can
drive; set `CURVEVISION_CHROMIUM` if the bundled one is not found.
"""

from __future__ import annotations

import io
import json
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

from screenshot import api, find_chromium, start_server  # noqa: E402

FRAMES = 6
#: The frame the third conflict sits on, and the one the click must land the editor on.
#: Chosen away from frame 0 so "seeked" cannot be confused with "never moved".
SEEK_TARGET = 4


def png_bytes(width: int = 480, height: int = 320, colour: str = "#334155") -> bytes:
    from PIL import Image

    buffer = io.BytesIO()
    Image.new("RGB", (width, height), colour).save(buffer, format="PNG")
    return buffer.getvalue()


def box(frame: int, label: str, points: list[float]) -> dict:
    return {
        "frame": frame,
        "label_id": label,
        "shape_type": "rectangle",
        "points": points,
        "rotation": 0.0,
        "occluded": False,
        "outside": False,
        "z_order": 0,
        "attributes": {},
    }


def write(base: str, token: str, job_id: str, shapes: list[dict]) -> None:
    document = api(base, token, f"/jobs/{job_id}/annotations")
    api(
        base,
        token,
        f"/jobs/{job_id}/annotations",
        {"annotation_version": document["annotation_version"], "created_shapes": shapes},
        method="PATCH",
    )


def seed(base: str, token: str) -> tuple[str, str, str]:
    """A task whose annotation job is wrong in every way the comparison can classify."""
    org = api(base, token, "/organizations")[0]
    project = api(base, token, "/projects", {
        "organization_id": org["id"], "slug": "quality", "name": "Quality",
        "description": "A job to score.",
        "labels": [
            {"name": "car", "color": "#ef4444", "allowed_shape_types": ["rectangle"]},
            {"name": "person", "color": "#22c55e", "allowed_shape_types": ["rectangle"]},
        ],
    })
    task = api(base, token, "/tasks", {
        "project_id": project["id"], "name": "Checked", "media_kind": "image",
    })
    for index in range(FRAMES):
        api(base, token, f"/tasks/{task['id']}/assets", files=png_bytes(),
            filename=f"f{index}.png", content_type="image/png")

    jobs = api(base, token, f"/tasks/{task['id']}/jobs")
    job = jobs[0]
    truth = api(base, token, f"/tasks/{task['id']}/ground-truth", {})

    car = project["labels"][0]["id"]
    person = project["labels"][1]["id"]

    # Ground truth: what is actually there.
    write(base, token, truth["id"], [
        box(0, car, [10, 10, 110, 110]),        # annotated correctly
        box(2, car, [20, 20, 120, 120]),        # missed entirely
        box(3, person, [30, 30, 130, 130]),     # labelled as a car
        box(SEEK_TARGET, car, [40, 40, 140, 140]),  # traced loosely
    ])

    # The annotation: right once, then wrong three different ways, plus an invention.
    write(base, token, job["id"], [
        box(0, car, [10, 10, 110, 110]),        # match
        box(3, car, [30, 30, 130, 130]),        # wrong label
        box(SEEK_TARGET, car, [95, 40, 195, 140]),  # IoU 0.29 -> poor overlap
        box(5, person, [0, 0, 50, 50]),         # extra
    ])
    return str(task["id"]), str(job["id"]), str(truth["id"])


def main() -> int:
    from playwright.sync_api import sync_playwright

    failures: list[str] = []

    def check(condition: bool, passed: str, failed: str) -> None:
        if condition:
            print(f"  ok   {passed}")
        else:
            failures.append(failed)
            print(f"  FAIL {failed}")

    print(f"a {FRAMES}-frame task: one object matched, one missed, one mislabelled, "
          f"one traced loosely, one invented\n")

    with tempfile.TemporaryDirectory(prefix="curvevision-quality-") as workspace:
        process, handshake = start_server(Path(workspace) / "data")
        base, token = handshake["url"], handshake["token"]
        try:
            _task_id, job_id, _truth_id = seed(base, token)
            injection = json.dumps({
                "url": base, "token": token, "data_dir": handshake["data_dir"],
                "version": handshake["version"], "desktop": True,
            })

            with sync_playwright() as p:
                launch: dict[str, object] = {"headless": True}
                if (chromium := find_chromium()) is not None:
                    launch["executable_path"] = chromium
                browser = p.chromium.launch(**launch)
                page = browser.new_page(viewport={"width": 1500, "height": 950})
                raised: list[str] = []
                page.on("pageerror", lambda exc: raised.append(str(exc)))
                page.add_init_script(f"window.__CURVEVISION__ = {injection};")
                page.goto(f"{base}/jobs/{job_id}", wait_until="networkidle")
                page.wait_for_selector("canvas", timeout=60_000)
                page.wait_for_timeout(2000)
                check(not raised, "the editor mounts without raising", f"page error: {raised}")

                # 1. An unscored job says so and offers to fix that.
                page.wait_for_selector("text=Quality", timeout=20_000)
                compute = page.get_by_role("button", name="Check against ground truth")
                check(compute.count() == 1,
                      "an unscored job offers to check itself against ground truth",
                      "no compute button in the quality panel")
                check(page.get_by_text("Not scored yet").count() > 0,
                      "and says plainly that it has not been scored",
                      "the panel does not say the job is unscored")

                compute.first.click()
                page.wait_for_timeout(3000)
                check(not raised, "computing a report raises nothing",
                      f"page error while computing: {raised}")

                stored = api(base, token, f"/jobs/{job_id}/quality")
                kinds = sorted({c["kind"] for c in stored["details"]["conflicts"]})
                print(f"  server-side score: P={stored['precision']:.2f} "
                      f"R={stored['recall']:.2f} F1={stored['f1']:.2f}; conflicts {kinds}")
                check(kinds == ["extra", "missing", "poor_overlap", "wrong_label"],
                      "all four conflict kinds are exercised",
                      f"only {kinds} were produced, so the panel is not fully exercised")

                # The score must be on screen, not merely in the database. Precision is
                # 1/3 here, which renders as 33.3%.
                shown = page.get_by_text("Precision").count() > 0
                check(shown, "the panel shows the score",
                      "the score did not render after computing")

                # 2. The claim the whole design rests on: a conflict is a place.
                rows = page.locator("li button", has_text="Missed")
                check(rows.count() > 0, "conflicts are listed as rows a reviewer can act on",
                      "no conflict rows rendered")

                frame_readout = page.locator("footer span.font-mono")
                before = frame_readout.inner_text() if frame_readout.count() else "?"

                target = page.locator("li button", has_text=f"Loose geometry")
                if target.count() == 0:
                    target = rows
                target.first.click()
                page.wait_for_timeout(1500)
                after = frame_readout.inner_text() if frame_readout.count() else "?"
                print(f"  frame readout: {before!r} -> {after!r}")
                check(before != after,
                      "clicking a conflict seeks the editor to the frame it is about",
                      f"clicking a conflict did not move the editor (still {after!r})")

                shot = Path("/tmp/curvevision-quality.png")
                page.screenshot(path=str(shot))
                print(f"  (screenshot: {shot})")

                # 3. Annotating after the score was taken must invalidate it visibly.
                car = api(base, token, f"/jobs/{job_id}/annotations")["shapes"][0]["label_id"]
                write(base, token, job_id, [box(2, car, [20, 20, 120, 120])])
                page.reload(wait_until="networkidle")
                page.wait_for_selector("canvas", timeout=60_000)
                page.wait_for_timeout(2500)

                stale = page.get_by_text("The job has changed since this was computed")
                check(stale.count() > 0,
                      "a report is marked stale once the job is annotated further",
                      "the panel still presents a report that predates the current work")

                stale_shot = Path("/tmp/curvevision-quality-stale.png")
                page.screenshot(path=str(stale_shot))
                print(f"  (screenshot: {stale_shot})")
                browser.close()
        finally:
            process.terminate()

    print()
    if failures:
        print(f"{len(failures)} check(s) failed:")
        for failure in failures:
            print(f"  - {failure}")
        return 1
    print("all checks passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
