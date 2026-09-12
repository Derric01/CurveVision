#!/usr/bin/env python3
"""Seed a video task with tracks and photograph the editor's timeline.

    python scripts/verify_track_timeline.py

A track's presence along a job is not one span. A keyframe marked `outside` is the object
*leaving* -- behind a wall, out of shot -- and it may come back. The timeline has to draw
that as gaps, because a bar running from first keyframe to last claims the object is on
screen during its absences, and an annotator who trusts it looks for something that is not
there.

`timeline.test.ts` asserts that frame by frame against the interpolator. What it cannot
assert is that any of it reaches the screen: that the rows render, that the keyframe ticks
land where they should, and that `,`/`.` move between keyframes. That is what this does.

Needs the packaged server (`python desktop/sidecar/build.py`) and a Chromium Playwright can
drive; set `CURVEVISION_CHROMIUM` if the bundled one is not found.
"""

from __future__ import annotations

import json
import sys
import tempfile
from pathlib import Path

from screenshot import api, find_chromium, start_server
from verify_chunked_frames import make_video

FRAMES = 48

#: Three tracks whose lifetimes exercise the cases that differ: one present throughout, one
#: that leaves and comes back, one that starts late and never leaves.
TRACKS = [
    ("car", [(0, False), (20, False), (47, False)]),
    ("pedestrian", [(0, False), (8, True), (26, False), (40, False)]),
    ("sign", [(30, False), (44, False)]),
]


def seed(base: str, token: str, clip: bytes) -> tuple[str, str]:
    org = api(base, token, "/organizations")[0]
    project = api(base, token, "/projects", {
        "organization_id": org["id"], "slug": "tracked", "name": "Tracked",
        "description": "A clip with objects that come and go.",
        "labels": [
            {"name": "car", "color": "#ef4444", "allowed_shape_types": ["rectangle"]},
            {"name": "pedestrian", "color": "#22c55e", "allowed_shape_types": ["rectangle"]},
            {"name": "sign", "color": "#38bdf8", "allowed_shape_types": ["rectangle"]},
        ],
    })
    task = api(base, token, "/tasks", {
        "project_id": project["id"], "name": "Clip 01", "media_kind": "video",
    })
    api(base, token, f"/tasks/{task['id']}/assets", files=clip, filename="clip.mp4",
        content_type="video/mp4")

    job = api(base, token, f"/tasks/{task['id']}/jobs")[0]
    labels = {label["name"]: label["id"] for label in project["labels"]}
    document = api(base, token, f"/jobs/{job['id']}/annotations")

    created = []
    for index, (label, keyframes) in enumerate(TRACKS):
        created.append({
            "label_id": labels[label],
            "shape_type": "rectangle",
            "shapes": [
                {
                    "frame": frame,
                    "shape_type": "rectangle",
                    "points": [20 + index * 60, 20, 90 + index * 60, 110],
                    "rotation": 0.0,
                    "occluded": False,
                    "outside": outside,
                    "keyframe": True,
                    "z_order": 0,
                    "attributes": {},
                }
                for frame, outside in keyframes
            ],
        })

    written = api(base, token, f"/jobs/{job['id']}/annotations", {
        "annotation_version": document["annotation_version"],
        "created_tracks": created,
    }, method="PATCH")
    assert written, "the annotation write returned nothing"
    return str(task["id"]), str(job["id"])


def main() -> int:
    from playwright.sync_api import sync_playwright

    failures: list[str] = []

    def check(condition: bool, passed: str, failed: str) -> None:
        if condition:
            print(f"  ok   {passed}")
        else:
            failures.append(failed)
            print(f"  FAIL {failed}")

    clip = make_video(frames=FRAMES)
    print(f"clip: {FRAMES} frames; {len(TRACKS)} tracks\n")

    with tempfile.TemporaryDirectory(prefix="curvevision-timeline-") as workspace:
        process, handshake = start_server(Path(workspace) / "data")
        base, token = handshake["url"], handshake["token"]
        try:
            _task_id, job_id = seed(base, token, clip)
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
                page.wait_for_timeout(3000)

                check(not raised, "the editor mounts without raising", f"page error: {raised}")

                page.wait_for_selector("text=Tracks", timeout=20_000)
                lanes = page.get_by_role("button", name="keyframes. Click to seek.")
                check(lanes.count() == len(TRACKS),
                      f"one lane per track ({lanes.count()})",
                      f"expected {len(TRACKS)} lanes, found {lanes.count()}")

                # The pedestrian leaves at frame 8 and returns at 26, so its lane must be
                # drawn as two runs. One run would say it is on screen the whole time.
                runs = page.evaluate(
                    "() => [...document.querySelectorAll('button[aria-label*=\"keyframes\"]')]"
                    ".map(b => b.querySelectorAll('span[style*=\"opacity\"]').length)"
                )
                print(f"  presence runs per lane: {runs}")
                check(sorted(runs) == [1, 1, 2],
                      "the track that leaves and returns is drawn as two runs",
                      f"expected runs [1, 1, 2], got {sorted(runs)}")

                # `,` and `.` step between keyframes rather than frames.
                def counter() -> str:
                    return page.locator("text=/\\d+\\s*\\/\\s*\\d+/").first.inner_text().strip()

                page.keyboard.press(".")
                page.wait_for_timeout(500)
                after_first = counter()
                page.keyboard.press(".")
                page.wait_for_timeout(500)
                after_second = counter()
                print(f"  after two '.' presses the counter reads {after_second!r}")
                check(after_first != after_second,
                      "'.' moves between keyframes",
                      f"'.' did not advance: {after_first!r} then {after_second!r}")

                page.keyboard.press(",")
                page.wait_for_timeout(500)
                check(counter() == after_first,
                      "',' steps back to the previous keyframe",
                      f"',' did not return to {after_first!r}, got {counter()!r}")

                shot = Path("/tmp/curvevision-track-timeline.png")
                page.screenshot(path=str(shot))
                print(f"  (screenshot: {shot})")

                browser.close()
        finally:
            process.terminate()
            process.wait(timeout=20)

    if failures:
        print(f"\n{len(failures)} failure(s)")
        return 1
    print("\nall checks passed")
    return 0


if __name__ == "__main__":
    sys.exit(main())
