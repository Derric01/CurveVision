#!/usr/bin/env python3
"""Skeletons in a real browser: a tracked one drawn and edited, an untracked one moved.

    python scripts/verify_tracked_skeleton.py

A skeleton's geometry is its joints; its `points` are only their flattened copy, for
picking. Until iteration 68 the editor's track path and its select tool knew only `points`:

* a tracked skeleton (CVAT tracks them; a CVAT XML import brings them) was drawn on no
  frame of the editor;
* saving any edit to such a track sent its keyframes without joints, and since a save
  replaces a track's keyframes, that deleted every joint it had;
* moving a skeleton, or dragging one of its joints, moved `points` and nothing that was
  drawn or saved.

Four claims, each checked against the API rather than the screen where it can be:

1. On a frame between two keyframes, the tracked skeleton is drawn where its joints
   interpolate to — not at the earlier keyframe, where the server used to hold it.
2. Dragging one of its joints there saves a keyframe on that frame with that joint moved
   and the other where it was.
3. The keyframes nobody touched keep their joints.
4. Dragging an untracked skeleton moves all its joints, and the move is saved.

Needs the packaged server (`python desktop/sidecar/build.py`) and a Chromium.
"""

from __future__ import annotations

import json
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

from screenshot import api, canvas_point, find_chromium, start_server
from verify_skeleton_tool import IMAGE_HEIGHT, IMAGE_WIDTH, png_bytes

FRAMES = 5
EDIT_FRAME = 2
#: The tracked skeleton: keyframes on 0 and 4, so frame 2 is halfway.
NOSE = [(0, [100.0, 100.0]), (4, [300.0, 100.0])]
TAIL = [(0, [100.0, 260.0]), (4, [100.0, 140.0])]
HALFWAY_NOSE, HALFWAY_TAIL = [200.0, 100.0], [100.0, 200.0]
#: An untracked skeleton on the edit frame.
STILL = {"nose": [380.0, 250.0], "tail": [420.0, 280.0]}


def seed(base: str, token: str) -> tuple[str, dict[str, str]]:
    org = api(base, token, "/organizations")[0]
    project = api(base, token, "/projects", {
        "organization_id": org["id"], "slug": "poses", "name": "Poses",
        "labels": [{"name": "person", "color": "#22c55e", "skeleton_edges": [[0, 1]],
                    "children": [{"name": "nose"}, {"name": "tail"}]}],
    })
    person = project["labels"][0]
    ids = {"person": person["id"],
           **{child["name"]: child["id"] for child in person["children"]}}
    task = api(base, token, "/tasks", {
        "project_id": project["id"], "name": "Clip", "media_kind": "image",
    })
    for index in range(FRAMES):
        api(base, token, f"/tasks/{task['id']}/assets", files=png_bytes(),
            filename=f"{index}.png", content_type="image/png")
    job = api(base, token, f"/tasks/{task['id']}/jobs")[0]

    def elements(nose: list[float], tail: list[float]) -> list[dict]:
        return [{"label_id": ids["nose"], "points": nose},
                {"label_id": ids["tail"], "points": tail}]

    api(base, token, f"/jobs/{job['id']}/annotations", {
        "annotation_version": 0,
        "created_tracks": [{
            "label_id": ids["person"], "shape_type": "skeleton", "object_id": 1,
            "shapes": [{"frame": frame, "shape_type": "skeleton", "points": [],
                        "keyframe": True, "elements": elements(nose, tail)}
                       for (frame, nose), (_, tail) in zip(NOSE, TAIL, strict=True)],
        }],
        "created_shapes": [{
            "label_id": ids["person"], "frame": EDIT_FRAME, "shape_type": "skeleton",
            "points": [*STILL["nose"], *STILL["tail"]],
            "elements": elements(STILL["nose"], STILL["tail"]),
        }],
    }, method="PATCH")
    return str(job["id"]), ids


def main() -> int:
    from playwright.sync_api import sync_playwright

    failures: list[str] = []

    def check(condition: bool, passed: str, failed: str) -> None:
        if condition:
            print(f"  ok   {passed}")
        else:
            failures.append(failed)
            print(f"  FAIL {failed}")

    with tempfile.TemporaryDirectory(prefix="curvevision-tracked-skeleton-") as workspace:
        process, handshake = start_server(Path(workspace) / "data")
        base, token = handshake["url"], handshake["token"]
        try:
            job_id, ids = seed(base, token)
            injection = json.dumps({
                "url": base, "token": token, "data_dir": handshake["data_dir"],
                "version": handshake["version"], "desktop": True,
            })

            def document() -> dict:
                return api(base, token, f"/jobs/{job_id}/annotations")

            def joints(elements: list[dict]) -> dict[str, list[float]]:
                names = {ids["nose"]: "nose", ids["tail"]: "tail"}
                return {names[e["label_id"]]: [round(v, 1) for v in e["points"]]
                        for e in elements}

            with sync_playwright() as p:
                launch: dict[str, object] = {"headless": True}
                if (chromium := find_chromium()) is not None:
                    launch["executable_path"] = chromium
                browser = p.chromium.launch(**launch)
                page = browser.new_page(viewport={"width": 1280, "height": 1000})
                raised: list[str] = []
                page.on("pageerror", lambda exc: raised.append(str(exc)))
                refused: list[int] = []
                page.on("response", lambda response: refused.append(response.status)
                        if "/annotations" in response.url and response.request.method == "PATCH"
                        and response.status >= 400 else None)
                page.add_init_script(f"window.__CURVEVISION__ = {injection};")
                page.goto(f"{base}/jobs/{job_id}", wait_until="networkidle")
                page.wait_for_selector("canvas", timeout=60_000)
                page.wait_for_timeout(1200)

                shapes_layer = page.locator("canvas").nth(1)
                box = shapes_layer.bounding_box()
                assert box is not None

                def at(point: list[float]) -> tuple[float, float]:
                    return canvas_point(box, (IMAGE_WIDTH, IMAGE_HEIGHT), point[0], point[1])

                def drawn_near(point: list[float]) -> bool:
                    """Whether the shapes layer — annotations only — has paint within a few
                    pixels of `point`."""
                    x, y = at(point)
                    return bool(shapes_layer.evaluate(
                        """(canvas, [x, y]) => {
                            const rect = canvas.getBoundingClientRect();
                            const sx = canvas.width / rect.width, sy = canvas.height / rect.height;
                            const cx = Math.round((x - rect.left) * sx);
                            const cy = Math.round((y - rect.top) * sy);
                            const r = Math.round(4 * sx);
                            const data = canvas.getContext('2d')
                              .getImageData(cx - r, cy - r, 2 * r + 1, 2 * r + 1).data;
                            for (let i = 3; i < data.length; i += 4) if (data[i] > 0) return true;
                            return false;
                        }""",
                        [x, y],
                    ))

                def save() -> None:
                    button = page.get_by_text("Save", exact=True)
                    if button.is_enabled():
                        button.click()
                    page.wait_for_timeout(1500)

                def drag(start: list[float], end: list[float]) -> None:
                    page.mouse.move(*at(start))
                    page.mouse.down()
                    page.mouse.move(*at(end), steps=8)
                    page.mouse.up()
                    page.wait_for_timeout(300)

                for _ in range(EDIT_FRAME):
                    page.locator("footer").get_by_role("button").nth(1).click()
                    page.wait_for_timeout(250)

                # ------------------------------------------------ 1. drawn, interpolated
                check(drawn_near(HALFWAY_NOSE) and drawn_near(HALFWAY_TAIL),
                      f"on frame {EDIT_FRAME} the tracked skeleton is drawn halfway between "
                      "its keyframes",
                      f"nothing is drawn at the interpolated joints {HALFWAY_NOSE}, "
                      f"{HALFWAY_TAIL}")
                # The frame-0 *tail*: the frame-0 nose is the corner of this frame's
                # bounding box, where the label chip is drawn.
                check(not drawn_near(TAIL[0][1]),
                      "and not held at the earlier keyframe's position",
                      f"something is drawn at the frame-0 tail {TAIL[0][1]}")

                # ------------------------------------------ 2 and 3: drag a tracked joint
                page.get_by_title("Select (V)").click()
                page.mouse.click(*at(HALFWAY_NOSE))  # select it
                page.wait_for_timeout(250)
                moved_nose = [HALFWAY_NOSE[0] + 20, HALFWAY_NOSE[1] + 40]
                drag(HALFWAY_NOSE, moved_nose)  # then drag the joint
                save()

                [track] = document()["tracks"]
                by_frame = {s["frame"]: joints(s.get("elements") or []) for s in track["shapes"]}
                print(f"  keyframes after the drag: {by_frame}")
                check(not refused, "the save is accepted", f"saves refused: {refused}")
                edited = by_frame.get(EDIT_FRAME, {})
                check(edited.get("nose") == [round(v, 1) for v in moved_nose]
                      and edited.get("tail") == HALFWAY_TAIL,
                      f"frame {EDIT_FRAME} becomes a keyframe with the nose where it was "
                      "dragged and the tail where it was shown",
                      f"frame {EDIT_FRAME}'s joints are {edited}")
                check(EDIT_FRAME in by_frame
                      and by_frame.get(0) == {"nose": NOSE[0][1], "tail": TAIL[0][1]}
                      and by_frame.get(4) == {"nose": NOSE[1][1], "tail": TAIL[1][1]},
                      "the keyframes nobody touched keep their joints",
                      f"the untouched keyframes now hold {by_frame.get(0)}, {by_frame.get(4)}")

                # ------------------------------------------------- 4. move a skeleton
                page.keyboard.press("Escape")
                page.mouse.click(*at([470.0, 10.0]))  # clear the selection on empty space
                page.wait_for_timeout(250)
                drag(STILL["nose"], [STILL["nose"][0] - 30, STILL["nose"][1] - 50])
                save()
                [still] = document()["shapes"]
                after = joints(still.get("elements") or [])
                expected = {"nose": [STILL["nose"][0] - 30, STILL["nose"][1] - 50],
                            "tail": [STILL["tail"][0] - 30, STILL["tail"][1] - 50]}
                print(f"  the untracked skeleton after the drag: {after}")
                check(after == expected,
                      "dragging an untracked skeleton moves all its joints, and saves them",
                      f"its joints are {after}; expected {expected}")

                check(not raised, "nothing raised in the page", f"page errors: {raised}")
                page.screenshot(path="/tmp/curvevision-tracked-skeleton.png")
                browser.close()
        finally:
            process.terminate()
            process.wait(timeout=30)

    print()
    if failures:
        print(f"{len(failures)} check(s) failed")
        return 1
    print("all checks passed")
    return 0


if __name__ == "__main__":
    sys.exit(main())
