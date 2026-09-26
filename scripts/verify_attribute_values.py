#!/usr/bin/env python3
"""An annotator can set an object's attribute values from the editor.

    python scripts/verify_attribute_values.py

Until this, no screen could: the editor carried `attributes` through every save and never
let a person change one, so `occluded`, `parked` or a car's colour could be recorded only
from the SDK, the CLI, an import, or by a default.

Claims, each asserted against the **API**:

1. Selecting a box shows its label's attributes; a select, a checkbox and a text value set
   there are saved, as the types the server stores (a boolean, not the string "true").
2. A box drawn a moment ago, not yet saved, can be given a value straight away.
3. On a **tracked** object, a mutable value set on an interpolated frame lands on a new
   keyframe at that frame — not on the track, and not on the keyframes around it — while
   an immutable one lands on the track.
4. The editor then shows the mutable value as held on the frames after it, which it did
   not before: it showed a tracked object with the track's values only.

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


def seed(base: str, token: str) -> tuple[str, str, str]:
    org = api(base, token, "/organizations")[0]
    project = api(base, token, "/projects", {
        "organization_id": org["id"], "slug": "values", "name": "Values",
        "labels": [{
            "name": "car", "color": "#ef4444",
            "attributes": [
                {"name": "colour", "attribute_type": "select", "values": ["red", "blue"]},
                {"name": "parked", "attribute_type": "checkbox", "mutable": True},
                {"name": "note", "attribute_type": "text"},
            ],
        }],
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
        "created_shapes": [{"label_id": label, "frame": 0, "shape_type": "rectangle",
                            "points": [20, 150, 80, 210]}],
        "created_tracks": [{
            "label_id": label, "shape_type": "rectangle",
            "shapes": [
                {"frame": 0, "shape_type": "rectangle", "points": [20, 20, 80, 70],
                 "keyframe": True, "attributes": {"parked": False}},
                {"frame": 4, "shape_type": "rectangle", "points": [100, 20, 160, 70],
                 "keyframe": True, "attributes": {"parked": False}},
            ],
        }],
    }, method="PATCH")
    document = api(base, token, f"/jobs/{job['id']}/annotations")
    return str(job["id"]), document["shapes"][0]["id"], document["tracks"][0]["id"]


def main() -> int:
    from playwright.sync_api import sync_playwright

    failures: list[str] = []

    def check(condition: bool, passed: str, failed: str) -> None:
        if condition:
            print(f"  ok   {passed}")
        else:
            failures.append(failed)
            print(f"  FAIL {failed}")

    with tempfile.TemporaryDirectory(prefix="curvevision-values-") as workspace:
        process, handshake = start_server(Path(workspace) / "data")
        base, token = handshake["url"], handshake["token"]
        try:
            job_id, shape_id, track_id = seed(base, token)
            injection = json.dumps({
                "url": base, "token": token, "data_dir": handshake["data_dir"],
                "version": handshake["version"], "desktop": True,
            })

            def document() -> dict:
                return api(base, token, f"/jobs/{job_id}/annotations")

            with sync_playwright() as p:
                launch: dict[str, object] = {"headless": True}
                if (chromium := find_chromium()) is not None:
                    launch["executable_path"] = chromium
                browser = p.chromium.launch(**launch)
                page = browser.new_page(viewport={"width": 1280, "height": 1100})
                raised: list[str] = []
                page.on("pageerror", lambda exc: raised.append(str(exc)))
                page.add_init_script(f"window.__CURVEVISION__ = {injection};")
                page.goto(f"{base}/jobs/{job_id}", wait_until="networkidle")
                page.wait_for_selector("canvas", timeout=60_000)
                page.wait_for_timeout(1200)
                check(not raised, "the editor mounts without raising", f"page error: {raised}")

                def save() -> None:
                    button = page.get_by_text("Save", exact=True)
                    if button.is_enabled():
                        button.click()
                    page.wait_for_timeout(1500)

                def attribute(name: str):
                    return page.locator(f"[data-attribute='{name}']")

                # --------------------------------------------------- 1: a plain box
                page.locator(f"[data-object-id='{shape_id}']").click()
                page.wait_for_timeout(200)
                shown = page.locator(f"[data-attributes-panel='{shape_id}']").count() == 1
                check(shown, "selecting a box shows its attributes",
                      "no attribute panel appeared for the selected box")
                if shown:
                    attribute("colour").locator("select").select_option("blue")
                    attribute("parked").locator("input").check()
                    attribute("note").locator("input").fill("dented door")
                    save()
                saved = next((s for s in document()["shapes"] if s["id"] == shape_id), {})
                check(saved.get("attributes") == {"colour": "blue", "parked": True,
                                                  "note": "dented door"},
                      "a select, a checkbox and a text value are saved, as those types",
                      f"the box's attributes are {saved.get('attributes')}")

                # ------------------------------------------ 2: a box not yet saved
                box = page.locator("canvas").nth(1).bounding_box()
                assert box is not None

                def at(x: float, y: float) -> tuple[float, float]:
                    return canvas_point(box, (IMAGE_WIDTH, IMAGE_HEIGHT), x, y)

                page.get_by_title("Rectangle (R)").click()
                page.mouse.move(*at(200, 150))
                page.mouse.down()
                page.mouse.move(*at(280, 210), steps=6)
                page.mouse.up()
                page.wait_for_timeout(300)
                panel = page.locator("[data-attributes-panel]")
                if panel.count() == 1:
                    attribute("note").locator("input").fill("new")
                save()
                fresh = [s for s in document()["shapes"] if s["id"] != shape_id]
                check(len(fresh) == 1 and fresh[0]["attributes"].get("note") == "new",
                      "a box drawn a moment ago can be given a value before any save",
                      f"the new box saved as {[s['attributes'] for s in fresh]}")

                # --------------------------------------------- 3: a tracked object
                for _ in range(2):
                    page.locator("footer").get_by_role("button").nth(1).click()
                    page.wait_for_timeout(250)
                page.get_by_title("Select (V)").click()
                page.locator(f"[data-object-id='{track_id}']").click()
                page.wait_for_timeout(200)
                if page.locator(f"[data-attributes-panel='{track_id}']").count() == 1:
                    attribute("parked").locator("input").check()
                    attribute("colour").locator("select").select_option("red")
                save()
                track = next(t for t in document()["tracks"] if t["id"] == track_id)
                frames = {s["frame"]: s["attributes"] for s in track["shapes"]}
                check(frames.get(2) == {"parked": True}
                      and frames.get(0) == {"parked": False} and frames.get(4) == {"parked": False},
                      "a mutable value lands on a keyframe at this frame, and nowhere else",
                      f"the track's keyframe values are {frames}")
                check(track["attributes"] == {"colour": "red"},
                      "and an immutable one lands on the track",
                      f"the track's own attributes are {track['attributes']}")

                # ---------------------------- 4: the value is held on later frames
                page.locator("footer").get_by_role("button").nth(1).click()
                page.wait_for_timeout(400)
                page.locator(f"[data-object-id='{track_id}']").click()
                page.wait_for_timeout(200)
                held = attribute("parked").locator("input")
                check(held.count() == 1 and held.is_checked(),
                      "on the next frame the editor shows the value as held",
                      "the next frame does not show the value set on the frame before")

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
