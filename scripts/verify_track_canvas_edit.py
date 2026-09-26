#!/usr/bin/env python3
"""Moving or deleting a tracked object on the canvas saves — and does not break every save after it.

    python scripts/verify_track_canvas_edit.py

A tracked object is drawn on the canvas under its **track's** id. Autosave sent a drag of
one as an `updated_shapes` entry, which names a shape that does not exist: the server
answered 404, the whole batch failed, and autosave — which puts a failed batch back to retry
it — carried the bad entry into every later save. One touch of a tracked box, and nothing
else drawn in that session reached the server. Deleting one sent the track's id as a shape
to delete, which matched nothing, so the object was back on the next load.

Claims, each asserted against the **API**:

1. Dragging a tracked box on a frame that was only interpolated saves, and that frame
   becomes a keyframe carrying the new position — CVAT's behaviour (`Track.savePoints`).
2. The keyframes nobody touched are exactly where they were.
3. A rectangle drawn afterwards is saved too: the batch is not poisoned.
4. Undoing a drag before saving puts the car back, and — since an undo re-emits every
   object on the frame, moved or not — the track nobody moved is **not** given a keyframe.
5. **Deleting and then undoing before a save keeps the object**, for a saved shape, a
   shape never saved at all, and a tracked object. The undo reaches autosave as an update
   while the deletion is still queued; both were sent, the server applies deletions last,
   and the object the annotator had just restored was gone on the next save.
6. Deleting a tracked object deletes the track.

Needs the packaged server (`python desktop/sidecar/build.py`, itself needing
`npm --prefix web run build` first) and a Chromium Playwright can drive; set
`CURVEVISION_CHROMIUM` if the bundled one is not found.
"""

from __future__ import annotations

import json
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

from screenshot import api, canvas_point, find_chromium, start_server
from verify_label_schema import IMAGE_HEIGHT, IMAGE_WIDTH, png_bytes

FRAMES = 5
EDIT_FRAME = 2
#: The moving car: keyframes at 0 and 4, so frame 2 is interpolated halfway between them.
MOVING = [(0, [20.0, 20.0, 80.0, 70.0]), (4, [100.0, 20.0, 160.0, 70.0])]
#: A parked car, on screen on the same frame and never touched.
PARKED = [(0, [200.0, 150.0, 280.0, 210.0]), (4, [200.0, 150.0, 280.0, 210.0])]


def seed(base: str, token: str) -> tuple[str, str, str]:
    org = api(base, token, "/organizations")[0]
    project = api(base, token, "/projects", {
        "organization_id": org["id"], "slug": "tracked", "name": "Tracked",
        "labels": [{"name": "car", "color": "#ef4444"}],
    })
    label = project["labels"][0]["id"]
    task = api(base, token, "/tasks", {
        "project_id": project["id"], "name": "Street", "media_kind": "image",
    })
    for index in range(FRAMES):
        api(base, token, f"/tasks/{task['id']}/assets", files=png_bytes(),
            filename=f"{index}.png", content_type="image/png")
    job = api(base, token, f"/tasks/{task['id']}/jobs")[0]
    api(base, token, f"/jobs/{job['id']}/annotations", {
        "annotation_version": 0,
        "created_tracks": [
            {"label_id": label, "shape_type": "rectangle", "object_id": object_id,
             "shapes": [{"frame": frame, "shape_type": "rectangle", "points": points,
                         "keyframe": True} for frame, points in keyframes]}
            for object_id, keyframes in ((1, MOVING), (2, PARKED))
        ],
    }, method="PATCH")
    document = api(base, token, f"/jobs/{job['id']}/annotations")
    by_object = {track["object_id"]: track["id"] for track in document["tracks"]}
    return str(job["id"]), by_object[1], by_object[2]


def main() -> int:
    from playwright.sync_api import sync_playwright

    failures: list[str] = []

    def check(condition: bool, passed: str, failed: str) -> None:
        if condition:
            print(f"  ok   {passed}")
        else:
            failures.append(failed)
            print(f"  FAIL {failed}")

    with tempfile.TemporaryDirectory(prefix="curvevision-tracked-") as workspace:
        process, handshake = start_server(Path(workspace) / "data")
        base, token = handshake["url"], handshake["token"]
        try:
            job_id, moving_id, parked_id = seed(base, token)
            print(f"two tracks on a {FRAMES}-frame job, editing on frame {EDIT_FRAME}\n")
            injection = json.dumps({
                "url": base, "token": token, "data_dir": handshake["data_dir"],
                "version": handshake["version"], "desktop": True,
            })

            def tracks() -> dict[str, dict]:
                document = api(base, token, f"/jobs/{job_id}/annotations")
                return {track["id"]: track for track in document["tracks"]}

            def keyframes(track: dict) -> dict[int, list[float]]:
                return {shape["frame"]: shape["points"] for shape in track["shapes"]}

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
                check(not raised, "the editor mounts without raising", f"page error: {raised}")

                canvas = page.locator("canvas").nth(1)  # the shapes layer
                box = canvas.bounding_box()
                assert box is not None

                def at(x: float, y: float) -> tuple[float, float]:
                    return canvas_point(box, (IMAGE_WIDTH, IMAGE_HEIGHT), x, y)

                def save() -> None:
                    # Save is disabled when autosave holds nothing, which is itself one of
                    # the failures below (an undo dropped on the floor); clicking it anyway
                    # would bury that under a Playwright timeout instead of a FAIL line.
                    button = page.get_by_text("Save", exact=True)
                    if button.is_enabled():
                        button.click()
                    page.wait_for_timeout(1500)

                for _ in range(EDIT_FRAME):
                    page.locator("footer").get_by_role("button").nth(1).click()
                    page.wait_for_timeout(250)

                # ------------------------------------------ 1 and 2: drag, then save
                # Halfway between x=20 and x=100 at frame 2, so the box is at 60..120.
                page.get_by_title("Select (V)").click()
                start = at(90, 45)
                page.mouse.click(*start)
                page.mouse.move(*start)
                page.mouse.down()
                page.mouse.move(start[0], start[1] + 60, steps=8)
                page.mouse.up()
                page.wait_for_timeout(300)
                save()

                moving = keyframes(tracks()[moving_id])
                check(not refused, "the save of a dragged tracked box is accepted",
                      f"the editor's save was refused: {refused}")
                check(EDIT_FRAME in moving and moving[EDIT_FRAME][1] > 40,
                      f"frame {EDIT_FRAME} becomes a keyframe at the new position",
                      f"the track's keyframes are {moving}")
                # Gated on the drag having landed: with nothing saved, the untouched
                # keyframes are trivially where they were, and this would pass on the bug.
                check(EDIT_FRAME in moving
                      and moving.get(0) == MOVING[0][1] and moving.get(4) == MOVING[1][1],
                      "and the keyframes nobody touched are exactly where they were",
                      f"the keyframes are now {moving}")

                # ------------------------------------- 3: the batch is not poisoned
                page.get_by_title("Rectangle (R)").click()
                page.mouse.move(*at(200, 20))
                page.mouse.down()
                page.mouse.move(*at(260, 60), steps=6)
                page.mouse.up()
                page.wait_for_timeout(300)
                save()
                shapes = api(base, token, f"/jobs/{job_id}/annotations")["shapes"]
                check(len(shapes) == 1 and not refused,
                      "a rectangle drawn afterwards is saved too",
                      f"the job holds {len(shapes)} shape(s); refused saves: {refused}")

                # --------------------------- 4: an undo does not litter other tracks
                # A real undo: drag the moving car again and take it back before saving.
                # (A save reloads the frame, which clears the undo stack, so this has to
                # happen between two saves.) Undoing hands autosave every object on the
                # frame, the parked car included, moved or not.
                placed = tracks()[moving_id]
                x1, y1, x2, y2 = keyframes(placed)[EDIT_FRAME]
                page.get_by_title("Select (V)").click()
                again = at((x1 + x2) / 2, (y1 + y2) / 2)
                page.mouse.click(*again)
                page.mouse.move(*again)
                page.mouse.down()
                page.mouse.move(again[0] + 50, again[1], steps=8)
                page.mouse.up()
                page.wait_for_timeout(300)
                page.keyboard.press("Control+z")
                page.wait_for_timeout(300)
                save()
                after_undo = tracks()
                check(keyframes(after_undo[moving_id]) == keyframes(placed) and not refused,
                      "undoing a drag before saving leaves the moved car where it was",
                      f"the moved car's keyframes went from {keyframes(placed)} to "
                      f"{keyframes(after_undo[moving_id])}; refused: {refused}")
                parked = keyframes(after_undo[parked_id])
                check(sorted(parked) == [0, 4],
                      "and gives the track nobody moved no keyframe",
                      f"the parked car's keyframes are now {sorted(parked)}")

                # ------------------------------- 5: delete, undo, and it survives
                def delete_then_undo(x: float, y: float) -> None:
                    page.get_by_title("Select (V)").click()
                    page.mouse.click(*at(x, y))
                    page.wait_for_timeout(200)
                    page.keyboard.press("Delete")
                    page.wait_for_timeout(200)
                    page.keyboard.press("Control+z")
                    page.wait_for_timeout(300)

                def shape_count() -> int:
                    return len(api(base, token, f"/jobs/{job_id}/annotations")["shapes"])

                # Each compared with the count just before its own step, so that one of
                # these failing cannot make the next one fail, or pass, for its reason.
                before = shape_count()
                delete_then_undo(230, 40)  # the rectangle saved in step 3
                save()
                check(before == 1 and shape_count() == before,
                      "a saved shape deleted and undone before saving is still there",
                      "the shape was deleted although the deletion was undone")

                before = shape_count()
                page.get_by_title("Rectangle (R)").click()
                page.mouse.move(*at(20, 170))
                page.mouse.down()
                page.mouse.move(*at(80, 220), steps=6)
                page.mouse.up()
                page.wait_for_timeout(300)
                page.keyboard.press("Delete")
                page.wait_for_timeout(200)
                page.keyboard.press("Control+z")
                page.wait_for_timeout(300)
                save()
                check(shape_count() == before + 1,
                      "so is a shape drawn, deleted and undone before it was ever saved",
                      "the never-saved shape was lost although its deletion was undone")

                delete_then_undo(240, 180)  # the parked car
                save()
                check(parked_id in tracks() and not refused,
                      "and so is a tracked object",
                      f"the track was deleted although the deletion was undone; "
                      f"refused: {refused}")

                # --------------------------------------------- 6: delete the track
                page.mouse.click(*at(240, 180))
                page.wait_for_timeout(200)
                page.keyboard.press("Delete")
                page.wait_for_timeout(300)
                save()
                remaining = tracks()
                check(parked_id not in remaining and moving_id in remaining and not refused,
                      "deleting a tracked object deletes its track, and only that one",
                      f"the job's tracks are now {sorted(remaining)}; refused: {refused}")

                check(not raised, "the whole loop raises nothing", f"page error: {raised}")
                browser.close()
        finally:
            process.terminate()
            process.wait(timeout=20)

    print("")
    if failures:
        print(f"{len(failures)} check(s) failed:")
        for failure in failures:
            print(f"  - {failure}")
        return 1
    print("every check passed")
    return 0


if __name__ == "__main__":
    sys.exit(main())
