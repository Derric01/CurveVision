#!/usr/bin/env python3
"""Edit a track's keyframes in a real browser and check what reached the database.

    python scripts/verify_keyframe_editing.py

`keyframes.test.ts` proves the arithmetic: adding a keyframe records the interpolated
position and moves the object on no other frame. What it cannot prove is that the editor
*reaches* that code — that selecting a track works, that the shortcut fires, that the write
carries a fresh `annotation_version`, and that the result is what the server stores. Every
defect this project has actually shipped lived in exactly that gap.

The check is deliberately end-to-end and asserts against the **API**, not the DOM: the
question is not "did the page look right" but "is the annotation now what the annotator
asked for".

Needs the packaged server (`python desktop/sidecar/build.py`) and a Chromium Playwright can
drive; set `CURVEVISION_CHROMIUM` if the bundled one is not found.
"""

from __future__ import annotations

import json
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

from screenshot import api, find_chromium, start_server  # noqa: E402
from verify_chunked_frames import make_video  # noqa: E402

FRAMES = 24
#: A car at x=20 on frame 0 and x=200 on frame 20. Frame 10 interpolates to x=110.
KEYFRAMES = [(0, [20, 40, 90, 110]), (20, [200, 40, 270, 110])]
EDIT_FRAME = 10
DEPART_FRAME = 15
#: Where the keyframe added at EDIT_FRAME is then dragged. Free of other keyframes, and to
#: the *left* so a drag that silently did nothing cannot pass by coincidence.
DRAG_TO = 5


def seed(base: str, token: str, clip: bytes) -> tuple[str, str]:
    org = api(base, token, "/organizations")[0]
    project = api(base, token, "/projects", {
        "organization_id": org["id"], "slug": "keyframes", "name": "Keyframes",
        "description": "A track to edit.",
        "labels": [{"name": "car", "color": "#ef4444", "allowed_shape_types": ["rectangle"]}],
    })
    task = api(base, token, "/tasks", {
        "project_id": project["id"], "name": "Clip", "media_kind": "video",
    })
    api(base, token, f"/tasks/{task['id']}/assets", files=clip, filename="clip.mp4",
        content_type="video/mp4")

    job = api(base, token, f"/tasks/{task['id']}/jobs")[0]
    label = project["labels"][0]["id"]
    document = api(base, token, f"/jobs/{job['id']}/annotations")
    api(base, token, f"/jobs/{job['id']}/annotations", {
        "annotation_version": document["annotation_version"],
        "created_tracks": [{
            "label_id": label,
            "shape_type": "rectangle",
            "shapes": [
                {"frame": frame, "shape_type": "rectangle", "points": points,
                 "rotation": 0.0, "occluded": False, "outside": False,
                 "keyframe": True, "z_order": 0, "attributes": {}}
                for frame, points in KEYFRAMES
            ],
        }],
    }, method="PATCH")
    return str(task["id"]), str(job["id"])


def track_of(base: str, token: str, job_id: str) -> dict:
    document = api(base, token, f"/jobs/{job_id}/annotations")
    assert document["tracks"], "the seeded track vanished"
    return document["tracks"][0]


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
    print(f"clip: {FRAMES} frames; one track with keyframes at "
          f"{[frame for frame, _ in KEYFRAMES]}\n")

    with tempfile.TemporaryDirectory(prefix="curvevision-keyframes-") as workspace:
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
                page.wait_for_timeout(2500)
                check(not raised, "the editor mounts without raising", f"page error: {raised}")

                page.wait_for_selector("text=Tracks", timeout=20_000)

                # Before selecting anything, the shortcuts must do nothing: a keyframe edit
                # names one object and a job can hold dozens.
                page.keyboard.press("k")
                page.wait_for_timeout(600)
                unchanged = track_of(base, token, job_id)
                check(len(unchanged["shapes"]) == len(KEYFRAMES),
                      "`k` with no track selected changes nothing",
                      f"`k` edited an unselected track: {len(unchanged['shapes'])} keyframes")

                # The select control is the track's name button; its accessible name is the
                # label, so locate it by the attribute that exists for exactly this purpose.
                # (It was `button[aria-pressed]` until the quality panel's filter chips made
                # that ambiguous — "the first toggle in the editor" is not a track selector.)
                selector = page.locator('button[data-track-selector]')
                check(selector.count() >= 1, "a track can be selected from the timeline",
                      "no track selector in the timeline")
                selector.first.click()
                page.wait_for_timeout(400)
                check(page.get_by_text("leaves here").count() > 0,
                      "the shortcuts are shown once a track is selected",
                      "selecting a track did not reveal the keyframe shortcuts")

                # Seek to the edit frame, then add a keyframe.
                for _ in range(EDIT_FRAME):
                    page.keyboard.press("ArrowRight")
                    page.wait_for_timeout(40)
                page.wait_for_timeout(500)
                page.keyboard.press("k")
                page.wait_for_timeout(1500)

                track = track_of(base, token, job_id)
                frames = sorted(shape["frame"] for shape in track["shapes"])
                print(f"  keyframes after `k` at frame {EDIT_FRAME}: {frames}")
                check(EDIT_FRAME in frames,
                      f"`k` added a keyframe at frame {EDIT_FRAME}",
                      f"no keyframe at frame {EDIT_FRAME}; got {frames}")

                added = next((s for s in track["shapes"] if s["frame"] == EDIT_FRAME), None)
                if added:
                    # Frame 10 is halfway between x=20 and x=200, so the interpolated box is
                    # at x=110. A keyframe carrying the *previous* position would be at 20
                    # and would drag frames 1-9 backwards with it.
                    x = added["points"][0]
                    print(f"  its x is {x:.1f} (interpolated position is 110)")
                    check(abs(x - 110) <= 1.0,
                          "the new keyframe carries the interpolated position",
                          f"the new keyframe is at x={x:.1f}, not the interpolated 110 — "
                          "adding it moved the object")

                # Now mark a departure further along.
                for _ in range(DEPART_FRAME - EDIT_FRAME):
                    page.keyboard.press("ArrowRight")
                    page.wait_for_timeout(40)
                page.wait_for_timeout(500)
                page.keyboard.press("o")
                page.wait_for_timeout(1500)

                track = track_of(base, token, job_id)
                by_frame = {shape["frame"]: shape for shape in track["shapes"]}
                print(f"  keyframes after `o` at frame {DEPART_FRAME}: "
                      f"{sorted(by_frame)}")
                check(by_frame.get(DEPART_FRAME, {}).get("outside") is True,
                      f"`o` marked the object as leaving at frame {DEPART_FRAME}",
                      f"frame {DEPART_FRAME} is not an outside keyframe")
                check(DEPART_FRAME - 1 in by_frame,
                      "the frame before the departure was pinned, so nothing froze",
                      f"frame {DEPART_FRAME - 1} was not pinned; frames leading up to the "
                      "departure will have frozen")

                # Dragging a marker along its lane. `moveKeyframe` is unit-tested; what
                # cannot be unit-tested is that a pointer lands on the marker at all, that
                # the drag survives leaving an 18px lane, and that releasing it does not
                # *also* seek — the lane under the marker is a click-to-seek button, so the
                # obvious implementation moves the keyframe and then jumps the playhead to
                # wherever it was dropped.
                lane = page.locator('button[aria-label*="Click to seek"]').first
                box = lane.bounding_box()
                assert box is not None, "the lane has no bounding box"

                def lane_x(frame: int) -> float:
                    return box["x"] + (frame / (FRAMES - 1)) * box["width"]

                marker = page.locator(f'[data-keyframe="{EDIT_FRAME}"]').first
                check(marker.count() > 0,
                      f"the keyframe at frame {EDIT_FRAME} is a grabbable marker",
                      f"no draggable marker at frame {EDIT_FRAME}")

                before_readout = page.locator("footer span.font-mono").inner_text()
                middle = box["y"] + box["height"] / 2
                page.mouse.move(lane_x(EDIT_FRAME), middle)
                page.mouse.down()
                # Several steps, and deliberately off the lane vertically partway through:
                # a drag without pointer capture dies the moment it leaves those 18 pixels.
                page.mouse.move(lane_x(8), middle - 30, steps=5)
                page.mouse.move(lane_x(DRAG_TO), middle, steps=5)
                page.mouse.up()
                page.wait_for_timeout(1800)

                track = track_of(base, token, job_id)
                moved = sorted(shape["frame"] for shape in track["shapes"])
                print(f"  keyframes after dragging {EDIT_FRAME} -> {DRAG_TO}: {moved}")
                check(DRAG_TO in moved and EDIT_FRAME not in moved,
                      f"dragging moved the keyframe from {EDIT_FRAME} to {DRAG_TO}",
                      f"the keyframe did not move; frames are {moved}")

                landed = next((s for s in track["shapes"] if s["frame"] == DRAG_TO), None)
                if landed:
                    # It carried its own geometry rather than being re-interpolated at the
                    # new frame, which would make a drag silently a redraw.
                    x = landed["points"][0]
                    print(f"  its x is {x:.1f} (the dragged keyframe's own x was 110)")
                    check(abs(x - 110) <= 1.0,
                          "the keyframe kept its geometry across the move",
                          f"the moved keyframe is at x={x:.1f}, not the 110 it carried")

                after_readout = page.locator("footer span.font-mono").inner_text()
                print(f"  frame readout: {before_readout!r} -> {after_readout!r}")
                check(before_readout == after_readout,
                      "releasing a dragged keyframe does not also seek the editor",
                      f"the drop seeked the playhead ({before_readout!r} -> {after_readout!r})")

                check(not raised, "dragging raises nothing", f"page error: {raised}")

                shot = Path("/tmp/curvevision-keyframes.png")
                page.screenshot(path=str(shot))
                print(f"  (screenshot: {shot})")
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
