#!/usr/bin/env python3
"""Declare a task's ground truth from the browser, then score a job against it.

    python scripts/verify_ground_truth_setup.py

This closes the last stretch of the quality feature that only the CLI could reach. Until now
`POST /tasks/{id}/ground-truth` was the one decision you could not make from the
application — and it is the decision that gives every score on the task its meaning.

The check is the whole loop, in one browser session, because that is the thing no unit test
can assert: **create the answer key on the task page, annotate it, then open an annotation
job and get a real score out of the panel.** Each half was verified separately; this is the
first time they are driven end to end as one workflow.

The claim that carries the most weight is the blank-field one. Both frame bounds are optional
and default to the whole task, so a form that read an empty field as `0` would produce a
**one-frame answer key** while looking like it had done what was asked — and every score
afterwards would be a real-looking number computed from one frame. The harness submits the
form untouched and asserts the job it created covers every frame.

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


def png_bytes(width: int = 480, height: int = 320) -> bytes:
    from PIL import Image

    buffer = io.BytesIO()
    Image.new("RGB", (width, height), "#334155").save(buffer, format="PNG")
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
    """A task with media and one annotated job, and deliberately no ground truth."""
    org = api(base, token, "/organizations")[0]
    project = api(base, token, "/projects", {
        "organization_id": org["id"], "slug": "gt-setup", "name": "Ground truth",
        "description": "Declaring what correct means.",
        "labels": [{"name": "car", "color": "#ef4444", "allowed_shape_types": ["rectangle"]}],
    })
    task = api(base, token, "/tasks", {
        "project_id": project["id"], "name": "Checked", "media_kind": "image",
    })
    for index in range(FRAMES):
        api(base, token, f"/tasks/{task['id']}/assets", files=png_bytes(),
            filename=f"f{index}.png", content_type="image/png")

    job = api(base, token, f"/tasks/{task['id']}/jobs")[0]
    car = project["labels"][0]["id"]
    # Right on frame 0, missing on frame 2 — so a score against a proper answer key is
    # neither 0 nor 1, and a panel showing a hardcoded number would be caught.
    write(base, token, job["id"], [box(0, car, [10, 10, 110, 110])])
    return str(task["id"]), str(job["id"]), str(car)


def main() -> int:
    from playwright.sync_api import sync_playwright

    failures: list[str] = []

    def check(condition: bool, passed: str, failed: str) -> None:
        if condition:
            print(f"  ok   {passed}")
        else:
            failures.append(failed)
            print(f"  FAIL {failed}")

    print(f"a {FRAMES}-frame task with one annotated job and no ground truth\n")

    with tempfile.TemporaryDirectory(prefix="curvevision-gt-") as workspace:
        process, handshake = start_server(Path(workspace) / "data")
        base, token = handshake["url"], handshake["token"]
        try:
            task_id, job_id, car = seed(base, token)
            injection = json.dumps({
                "url": base, "token": token, "data_dir": handshake["data_dir"],
                "version": handshake["version"], "desktop": True,
            })

            with sync_playwright() as p:
                launch: dict[str, object] = {"headless": True}
                if (chromium := find_chromium()) is not None:
                    launch["executable_path"] = chromium
                browser = p.chromium.launch(**launch)
                page = browser.new_page(viewport={"width": 1280, "height": 1000})
                raised: list[str] = []
                page.on("pageerror", lambda exc: raised.append(str(exc)))
                page.add_init_script(f"window.__CURVEVISION__ = {injection};")
                page.goto(f"{base}/tasks/{task_id}", wait_until="networkidle")
                page.wait_for_selector("text=Ground truth", timeout=60_000)
                page.wait_for_timeout(800)
                check(not raised, "the task page mounts without raising", f"page error: {raised}")

                create = page.get_by_role("button", name="Create the ground-truth job")
                check(create.count() == 1,
                      "a task with no ground truth offers to create one",
                      "no create button on the task page")

                # Submitted untouched: blank bounds must mean the whole task.
                create.first.click()
                page.wait_for_timeout(2500)
                check(not raised, "creating raises nothing", f"page error: {raised}")

                jobs = api(base, token, f"/tasks/{task_id}/jobs")
                truth = next((j for j in jobs if j["kind"] == "ground_truth"), None)
                check(truth is not None, "a ground-truth job now exists on the task",
                      "no ground-truth job was created")
                if truth is None:
                    browser.close()
                    raise SystemExit(1)

                print(f"  it covers frames {truth['start_frame']}-{truth['stop_frame']} "
                      f"of 0-{FRAMES - 1}")
                check(truth["start_frame"] == 0 and truth["stop_frame"] == FRAMES - 1,
                      "leaving both frame fields blank means the whole task, not frame 0",
                      f"blank fields produced frames {truth['start_frame']}-"
                      f"{truth['stop_frame']}, not the whole task")

                # The panel must now describe it rather than offer a second one, because the
                # API refuses a second and a button that always 409s is a lie.
                page.reload(wait_until="networkidle")
                page.wait_for_selector("text=Ground truth", timeout=30_000)
                page.wait_for_timeout(800)
                check(page.get_by_role("button", name="Create the ground-truth job").count() == 0,
                      "the form is replaced by a description once one exists",
                      "the page still offers to create a second ground-truth job")
                check(page.get_by_text("the answer key").count() > 0,
                      "the ground-truth job is marked as the answer key",
                      "nothing marks the ground-truth job on the task page")
                check(page.get_by_text("nothing annotated yet").count() > 0,
                      "an empty answer key says so, rather than looking ready to score",
                      "an unannotated ground truth is presented as usable")

                shot = Path("/tmp/curvevision-ground-truth.png")
                page.screenshot(path=str(shot), full_page=True)
                print(f"  (screenshot: {shot})")

                # Annotate the answer key, then score the annotation job from the editor —
                # the two halves of the feature, driven as one workflow for the first time.
                write(base, token, truth["id"], [
                    box(0, car, [10, 10, 110, 110]),
                    box(2, car, [20, 20, 120, 120]),
                ])
                page.goto(f"{base}/jobs/{job_id}", wait_until="networkidle")
                page.wait_for_selector("canvas", timeout=60_000)
                page.wait_for_timeout(1800)
                scored = page.get_by_role("button", name="Check against ground truth")
                check(scored.count() == 1,
                      "the editor offers to score the job against the new answer key",
                      "the editor does not offer to score against the ground truth")
                scored.first.click()
                page.wait_for_timeout(3000)

                report = api(base, token, f"/jobs/{job_id}/quality")
                print(f"  score: P={report['precision']:.2f} R={report['recall']:.2f} "
                      f"F1={report['f1']:.2f} over "
                      f"{report['details']['compared_frames']} frames")
                check(report["recall"] == 0.5 and report["precision"] == 1.0,
                      "the score reflects the ground truth just declared "
                      "(one of two objects found)",
                      f"expected P=1.0 R=0.5, got P={report['precision']} "
                      f"R={report['recall']}")
                check(report["details"]["compared_frames"] == FRAMES,
                      f"all {FRAMES} frames were compared, because the answer key covers them",
                      f"only {report['details']['compared_frames']} frames were compared")
                check(not raised, "the whole loop raises nothing", f"page error: {raised}")
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
