#!/usr/bin/env python3
"""Open, answer and resolve a review issue in a real browser.

    python scripts/verify_issues_panel.py

Issues are how a reviewer sends work back. The model, the API and the permissions have
existed since the first iteration and **nothing called them** — so the feature was complete
and unreachable, and receiving review feedback meant reading it out of the database.

`issues.test.ts` proves the ordering and the anchoring arithmetic without a DOM. What it
cannot prove is the loop: that the panel reaches the endpoint, that an issue opened on a
selected object actually carries that object's id, that a reply lands on the right thread,
and that resolving one moves it out of the open list without losing it.

Two checks carry the most weight. The **anchor**: A track materialised onto a frame is not a
shape: it has no row in the shapes table, and the editor gives it the *track's* id. Sending
that as `shape_id` points a foreign key at nothing — the issue still saves, still lists, and
silently stops pointing at the object it was about. Nothing in the UI would show it, so this
asserts against the API which column was actually filled in.

And the **pin's coordinate space**. The canvas scales and centres the frame, so a screen
point sent straight through lands somewhere else entirely. An issue pinned to the wrong pixel
is worse than one not pinned at all, because it reads as deliberate — so the harness clicks
the centre of the canvas and asserts the stored point is near the centre of the *image*.

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

FRAMES = 12
#: A car crossing the frame, as a track — so the anchor under test is a track, not a shape.
KEYFRAMES = [(0, [20, 40, 90, 110]), (10, [200, 40, 270, 110])]


def seed(base: str, token: str, clip: bytes) -> str:
    org = api(base, token, "/organizations")[0]
    project = api(base, token, "/projects", {
        "organization_id": org["id"], "slug": "issues", "name": "Issues",
        "description": "Sending work back.",
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
    return str(job["id"])


def issues_of(base: str, token: str, job_id: str) -> list[dict]:
    return list(api(base, token, f"/jobs/{job_id}/issues"))


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
    print(f"clip: {FRAMES} frames, one track, no issues yet\n")

    with tempfile.TemporaryDirectory(prefix="curvevision-issues-") as workspace:
        process, handshake = start_server(Path(workspace) / "data")
        base, token = handshake["url"], handshake["token"]
        try:
            job_id = seed(base, token, clip)
            injection = json.dumps({
                "url": base, "token": token, "data_dir": handshake["data_dir"],
                "version": handshake["version"], "desktop": True,
            })

            with sync_playwright() as p:
                launch: dict[str, object] = {"headless": True}
                if (chromium := find_chromium()) is not None:
                    launch["executable_path"] = chromium
                browser = p.chromium.launch(**launch)
                page = browser.new_page(viewport={"width": 1500, "height": 980})
                raised: list[str] = []
                page.on("pageerror", lambda exc: raised.append(str(exc)))
                page.add_init_script(f"window.__CURVEVISION__ = {injection};")
                page.goto(f"{base}/jobs/{job_id}", wait_until="networkidle")
                page.wait_for_selector("canvas", timeout=60_000)
                page.wait_for_timeout(2200)
                check(not raised, "the editor mounts without raising", f"page error: {raised}")

                page.wait_for_selector("text=Issues", timeout=20_000)
                check(page.get_by_text("Nothing open on this job.").count() > 0,
                      "a job with no issues says so",
                      "the empty state did not render")

                body = page.get_by_label("Describe the problem")
                check(body.count() == 1, "the panel offers a place to describe a problem",
                      "no issue composer in the panel")

                # Select the track's object first: the issue must anchor to it.
                #
                # `aside li button` is not enough: the label list and the object list are
                # both lists of buttons carrying the label's name, and clicking a label row
                # selects the active *label* while leaving the selection empty — which looks
                # exactly like a broken anchor. `data-object-id` exists to tell them apart.
                objects = page.locator("aside li button[data-object-id]")
                check(objects.count() > 0, "the object list shows the track on this frame",
                      "no object to select")

                # Clicking an object row goes through the canvas' imperative handle, which is
                # how the parent reaches the engine at all — the same path as undo, redo,
                # delete, fit-to-frame and the label toggles. The handle was built from a
                # ref that was still null when it was built, and never updated, so every one
                # of those controls silently did nothing. Focusing zooms to the object, so
                # the zoom readout moving is the proof that the handle is live.
                zoom_before = page.locator("span", has_text="%").first.inner_text()
                objects.first.click()
                page.wait_for_timeout(800)
                zoom_after = page.locator("span", has_text="%").first.inner_text()
                print(f"  zoom: {zoom_before} -> {zoom_after}")
                check(zoom_before != zoom_after,
                      "clicking an object reaches the engine and focuses it",
                      f"the canvas handle is not live: zoom stayed at {zoom_after}")

                check(page.get_by_text("on the selected object").count() > 0,
                      "the panel says it will attach the issue to the selected object",
                      "selecting an object did not reach the issues panel")

                body.fill("this box is too loose on the left")
                page.get_by_role("button", name="Open an issue").click()
                page.wait_for_timeout(2000)
                check(not raised, "opening an issue raises nothing", f"page error: {raised}")

                issues = issues_of(base, token, job_id)
                check(len(issues) == 1, "the issue reached the server",
                      f"expected 1 issue, found {len(issues)}")
                if not issues:
                    browser.close()
                    raise SystemExit(1)
                first = issues[0]

                print(f"  anchor: shape_id={first['shape_id']} track_id={first['track_id']}")
                check(first["track_id"] is not None and first["shape_id"] is None,
                      "an issue on a track anchors by track_id, not as a phantom shape",
                      f"the anchor is wrong: shape_id={first['shape_id']} "
                      f"track_id={first['track_id']}")
                check(first["frame"] == 0 and first["state"] == "open",
                      "it is open, on the frame it was written from",
                      f"frame={first['frame']} state={first['state']}")
                check([c["body"] for c in first["comments"]]
                      == ["this box is too loose on the left"],
                      "the description became the thread's first comment",
                      f"comments are {[c['body'] for c in first['comments']]}")

                # Reply on the thread, then resolve it.
                page.get_by_text("this box is too loose on the left").first.click()
                page.wait_for_timeout(400)
                reply = page.get_by_label("Reply to this issue")
                check(reply.count() >= 1, "opening a row shows its thread and a reply box",
                      "the thread did not expand")
                reply.first.fill("tightened it")
                page.get_by_role("button", name="Reply").first.click()
                page.wait_for_timeout(1800)

                after = issues_of(base, token, job_id)[0]
                bodies = [c["body"] for c in after["comments"]]
                print(f"  thread: {bodies}")
                check(bodies == ["this box is too loose on the left", "tightened it"],
                      "the reply landed on the same thread, after the first comment",
                      f"the thread is {bodies}")

                page.get_by_role("button", name="Resolve").first.click()
                page.wait_for_timeout(1800)
                resolved = issues_of(base, token, job_id)[0]
                check(resolved["state"] == "resolved" and resolved["resolved_at"] is not None,
                      "resolving records who closed it and when",
                      f"state={resolved['state']} resolved_at={resolved['resolved_at']}")
                check(page.get_by_text("Nothing open on this job.").count() > 0,
                      "a resolved issue leaves the open list",
                      "the resolved issue is still listed as open")
                check(page.get_by_text("1 resolved").count() > 0,
                      "but is still reachable, rather than hidden",
                      "the resolved issue vanished from the panel entirely")

                # Pinning: arm the pin, click the image, and check the point that reached
                # the server is the pixel that was clicked rather than a screen coordinate.
                # The canvas is scaled and centred, so a screen point sent straight through
                # lands somewhere else entirely — and an issue pinned to the wrong pixel is
                # worse than one not pinned at all, because it reads as deliberate.
                canvas = page.locator("canvas").first
                box = canvas.bounding_box()
                assert box is not None, "the canvas has no bounding box"

                page.get_by_label("Describe the problem").fill("the wheel is outside the box")
                page.get_by_role("button", name="Pin").click()
                page.wait_for_timeout(300)

                target = (box["x"] + box["width"] * 0.5, box["y"] + box["height"] * 0.5)
                page.mouse.click(*target)
                page.wait_for_timeout(600)

                pinned_label = page.get_by_text("pinned at", exact=False)
                check(pinned_label.count() > 0,
                      "clicking the image places a pin and the panel says where",
                      "the click did not place a pin")
                placed = pinned_label.first.inner_text() if pinned_label.count() else ""
                print(f"  {placed}")

                page.get_by_role("button", name="Open an issue").click()
                page.wait_for_timeout(2000)

                pinned = next((i for i in issues_of(base, token, job_id)
                               if i["state"] == "open"), None)
                check(pinned is not None, "the pinned issue reached the server",
                      "no open issue after pinning")
                if pinned is not None:
                    position = pinned["position"]
                    print(f"  position stored: {position}")
                    check(len(position) == 2 and all(isinstance(v, (int, float)) for v in position),
                          "the issue carries a two-number point",
                          f"position is {position!r}")
                    # Where the click should land, exactly.
                    #
                    # Selecting the object earlier focused the view on it, so the viewport is
                    # centred on that box at ~479% rather than fitted to the frame. The
                    # centre of the canvas is therefore the centre of the *box*, which the
                    # seeded keyframe pins precisely: [20, 40, 90, 110] -> (55, 75).
                    #
                    # That makes this an exact expectation rather than a tolerance around a
                    # guess, and it fails for either mistake worth catching: a screen
                    # coordinate sent straight through, and a conversion that ignores the
                    # viewport's pan or scale.
                    x1, y1, x2, y2 = KEYFRAMES[0][1]
                    expected = ((x1 + x2) / 2, (y1 + y2) / 2)
                    if len(position) == 2:
                        off = (abs(position[0] - expected[0]), abs(position[1] - expected[1]))
                        print(f"  expected the focused box's centre {expected}; "
                              f"off by {off[0]:.1f}, {off[1]:.1f} px")
                        check(off[0] <= 3 and off[1] <= 3,
                              "the stored point is in image space, through the live viewport",
                              f"the point is {position}, not the focused box's centre "
                              f"{expected}")

                check(not raised, "the whole loop raises nothing", f"page error: {raised}")
                shot = Path("/tmp/curvevision-issues.png")
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
