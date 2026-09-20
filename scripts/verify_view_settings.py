#!/usr/bin/env python3
"""Toggle the editor's view settings, and check the canvas actually changes.

    python scripts/verify_view_settings.py

`Scene.showLabels`, `Scene.showSuggestions` and `Scene.fillOpacity` are as old as the
renderer: `isVisible` and the fill colour have always read them. Nothing before this
iteration ever called their setters, because no UI control existed to call them from --
they were plain public fields a caller could read, and could write to without effect, since
nothing called `invalidate()` for a raw field assignment. `scene.test.ts` proves the setters
themselves repaint-on-change and clamp correctly, in a runtime with no DOM. It cannot prove
that a click in the Labels panel actually reaches them, or that flipping one visibly changes
what is painted rather than a value nobody reads.

Three claims, each read straight off the shapes-layer canvas rather than asserted only
against component state:

1. Hiding label chips hides the little name/confidence badge drawn above each shape,
   without touching the shapes themselves -- confirmed by checking *both* things, since a
   toggle that hid the whole shape rather than just its chip would still pass a check that
   only looked for something changing near the corner.
2. Hiding suggestions hides only the *unreviewed* one -- a manually drawn shape stays lit
   the whole time, which is the distinction `isUnreviewed` exists to draw.
3. Raising the fill opacity slider visibly darkens (raises the alpha of) a shape's interior.

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

IMAGE_WIDTH = 480
IMAGE_HEIGHT = 320

#: Two shapes, far enough apart that a probe point deep inside one never lands anywhere near
#: the other -- the whole harness depends on being able to tell them apart on the canvas.
MANUAL_BOX = [40.0, 40.0, 140.0, 140.0]
SUGGESTION_BOX = [300.0, 180.0, 400.0, 280.0]


def png_bytes(width: int = IMAGE_WIDTH, height: int = IMAGE_HEIGHT) -> bytes:
    from PIL import Image

    buffer = io.BytesIO()
    Image.new("RGB", (width, height), "#1e293b").save(buffer, format="PNG")
    return buffer.getvalue()


def seed(base: str, token: str) -> str:
    org = api(base, token, "/organizations")[0]
    project = api(base, token, "/projects", {
        "organization_id": org["id"],
        "slug": "viewsettings",
        "name": "View settings",
        "description": "Global visibility toggles and fill opacity.",
        "labels": [{"name": "car", "color": "#ef4444"}],
    })
    task = api(base, token, "/tasks", {
        "project_id": project["id"], "name": "Frame", "media_kind": "image",
    })
    api(base, token, f"/tasks/{task['id']}/assets", files=png_bytes(),
        filename="a.png", content_type="image/png")
    job = api(base, token, f"/tasks/{task['id']}/jobs")[0]
    car = project["labels"][0]["id"]

    document = api(base, token, f"/jobs/{job['id']}/annotations")
    api(base, token, f"/jobs/{job['id']}/annotations", {
        "annotation_version": document["annotation_version"],
        "created_shapes": [
            {
                "frame": 0, "label_id": car, "shape_type": "rectangle",
                "points": MANUAL_BOX, "rotation": 0.0, "occluded": False,
                "outside": False, "z_order": 0, "attributes": {},
            },
            {
                # Unreviewed: a confidence is what `isUnreviewed` keys on, matching a real
                # prediction rather than something already accepted.
                "frame": 0, "label_id": car, "shape_type": "rectangle",
                "points": SUGGESTION_BOX, "rotation": 0.0, "occluded": False,
                "outside": False, "z_order": 0, "attributes": {},
                "source": "model", "confidence": 0.8,
            },
        ],
    }, method="PATCH")
    return str(job["id"])


def main() -> int:
    from playwright.sync_api import sync_playwright

    failures: list[str] = []

    def check(condition: bool, passed: str, failed: str) -> None:
        if condition:
            print(f"  ok   {passed}")
        else:
            failures.append(failed)
            print(f"  FAIL {failed}")

    with tempfile.TemporaryDirectory(prefix="curvevision-view-") as workspace:
        process, handshake = start_server(Path(workspace) / "data")
        base, token = handshake["url"], handshake["token"]
        try:
            job_id = seed(base, token)
            print("a manual rectangle and an unreviewed suggestion, far apart on one frame\n")
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
                page.goto(f"{base}/jobs/{job_id}", wait_until="networkidle")
                page.wait_for_selector("canvas", timeout=60_000)
                page.wait_for_timeout(1500)

                # Where the two boxes' *centres* land on the canvas, and the frame's own
                # rectangle -- measured off the media layer, which holds only the frame
                # image, so a label chip or a stroke width cannot bias the mapping.
                frame = page.evaluate("""
() => {
  const canvas = document.querySelectorAll('canvas')[0];
  const context = canvas.getContext('2d');
  const { data, width, height } = context.getImageData(0, 0, canvas.width, canvas.height);
  let minX = Infinity, minY = Infinity, maxX = -Infinity, maxY = -Infinity;
  for (let y = 0; y < height; y++) for (let x = 0; x < width; x++) {
    if (data[(y * width + x) * 4 + 3] > 8) {
      if (x < minX) minX = x; if (x > maxX) maxX = x;
      if (y < minY) minY = y; if (y > maxY) maxY = y;
    }
  }
  return { minX, minY, maxX, maxY };
}
""")
                span_x = frame["maxX"] - frame["minX"] + 1
                span_y = frame["maxY"] - frame["minY"] + 1

                def to_screen(x: float, y: float) -> tuple[float, float]:
                    return (
                        frame["minX"] + (x / IMAGE_WIDTH) * span_x,
                        frame["minY"] + (y / IMAGE_HEIGHT) * span_y,
                    )

                def centre(box: list[float]) -> tuple[float, float]:
                    return to_screen((box[0] + box[2]) / 2, (box[1] + box[3]) / 2)

                manual_x, manual_y = centre(MANUAL_BOX)
                suggestion_x, suggestion_y = centre(SUGGESTION_BOX)

                def alpha_at(x: float, y: float) -> int:
                    return page.evaluate(
                        """([x, y]) => {
  const canvas = document.querySelectorAll('canvas')[1];
  const context = canvas.getContext('2d');
  return context.getImageData(Math.round(x), Math.round(y), 1, 1).data[3];
}""",
                        [x, y],
                    )

                lit_before = (alpha_at(manual_x, manual_y), alpha_at(suggestion_x, suggestion_y))
                print(f"  before anything is toggled: manual={lit_before[0]}, "
                      f"suggestion={lit_before[1]} (0 = nothing painted there)")
                check(lit_before[0] > 0 and lit_before[1] > 0,
                      "both shapes are painted before any toggle",
                      f"one of the seeded shapes did not render: {lit_before}")

                # ------------------------------------------------- hiding the label chips
                # `showLabels` gates only the little name/confidence chip `paintLabel` draws
                # above a shape's corner -- not the shape itself, which stays governed by
                # each label's own visibility toggle. So the probe here is the chip's own
                # band (`anchor.y - 15` to `anchor.y`, drawn fully opaque), not the shape's
                # fill; reading the shape's centre would never move on this toggle at all.
                chip_x, chip_y = to_screen(MANUAL_BOX[0] + 5, MANUAL_BOX[1] - 8)
                chip_before = alpha_at(chip_x, chip_y)
                check(chip_before > 0, "the label chip is painted before any toggle",
                      f"nothing is painted where the chip should be: alpha={chip_before}")

                page.locator("[data-view-show-labels]").click()
                page.wait_for_timeout(200)
                chip_hidden = alpha_at(chip_x, chip_y)
                shape_still_there = alpha_at(manual_x, manual_y)
                print(f"  chips hidden: chip={chip_hidden}, shape centre={shape_still_there}")
                check(chip_hidden == 0, "hiding label chips hides the chip",
                      f"the chip is still painted: alpha={chip_hidden}")
                check(shape_still_there > 0,
                      "and leaves the shape itself alone -- this hides names, not objects",
                      f"the shape vanished along with its chip: alpha={shape_still_there}")

                page.locator("[data-view-show-labels]").click()
                page.wait_for_timeout(200)
                chip_restored = alpha_at(chip_x, chip_y)
                check(chip_restored > 0, "showing chips again restores it",
                      f"the chip did not come back: alpha={chip_restored}")

                # -------------------------------------------------- hiding suggestions
                page.locator("[data-view-show-suggestions]").click()
                page.wait_for_timeout(200)
                after_hide_suggestions = (
                    alpha_at(manual_x, manual_y), alpha_at(suggestion_x, suggestion_y),
                )
                print(f"  suggestions hidden: manual={after_hide_suggestions[0]}, "
                      f"suggestion={after_hide_suggestions[1]}")
                check(after_hide_suggestions[0] > 0,
                      "the manually drawn shape stays visible",
                      f"the manual shape vanished when hiding suggestions: {after_hide_suggestions}")
                check(after_hide_suggestions[1] == 0,
                      "and only the unreviewed suggestion disappears",
                      f"the suggestion is still painted: {after_hide_suggestions}")

                page.locator("[data-view-show-suggestions]").click()
                page.wait_for_timeout(200)
                restored_suggestion = alpha_at(suggestion_x, suggestion_y)
                check(restored_suggestion > 0,
                      "showing suggestions again restores it",
                      f"the suggestion did not come back: alpha={restored_suggestion}")

                # ------------------------------------------------------ fill opacity
                # The box's own centre -- already used above, and far enough from the
                # stroke (which is drawn fully opaque regardless of `fillOpacity`) that the
                # reading here is the *fill* alpha, not the outline's.
                low_alpha = alpha_at(manual_x, manual_y)
                slider = page.locator("[data-view-fill-opacity]")
                # A plain `el.value = ...` does not reach React's controlled-input tracking
                # -- React patches the DOM property descriptor to notice a change, and an
                # assignment through that same patched setter looks like "no change" to it.
                # Going through the *native* setter first, the way a real drag does, is the
                # standard way around that.
                slider.evaluate(
                    "el => {"
                    " const setter = Object.getOwnPropertyDescriptor("
                    "   window.HTMLInputElement.prototype, 'value').set;"
                    " setter.call(el, '1');"
                    " el.dispatchEvent(new Event('input', { bubbles: true }));"
                    "}"
                )
                page.wait_for_timeout(200)
                high_alpha = alpha_at(manual_x, manual_y)
                print(f"  fill alpha: {low_alpha} at 18%, {high_alpha} at 100%")
                check(high_alpha > low_alpha,
                      "raising the fill opacity slider visibly darkens a shape's interior",
                      f"the fill alpha did not increase ({low_alpha} -> {high_alpha})")

                shot = Path("/tmp/curvevision-view-settings.png")
                page.screenshot(path=str(shot), full_page=False)
                print(f"  (screenshot: {shot})")

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
    raise SystemExit(main())
