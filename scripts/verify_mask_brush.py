#!/usr/bin/env python3
"""Paint a mask with the brush tool in a real browser, and check what was stored.

    python scripts/verify_mask_brush.py

Masks could be stored, exported and drawn before this — the encoding is stated once in
`mask.ts`, two export formats carry the pixels, and the renderer paints and picks a mask by
them, all proven by earlier harnesses. What was missing was a way to make one: masks were the
last shape type the platform could carry but not create. `brushTool.test.ts` proves the state
machine — which stroke starts a new mask and which edits the selected one, that erasing every
pixel deletes the object rather than leaving an invalid empty mask behind — in a runtime with
no DOM. It cannot prove that a real pointer drag on the real canvas reaches that code, that
what lands is picked up by `AnnotationEngine.applyResult` the same way every other tool's
strokes are, or that the pixels a person actually sees painted are the pixels that get saved.

Five claims:

1. A stroke over the canvas creates a mask covering where the pointer moved, and something
   visibly painted appears on the shapes layer — not only an API record of one.
2. Selecting that mask and painting elsewhere with the brush *grows* it — an edit, not a
   second shape.
3. Alt-dragging over covered pixels *shrinks* it, while the object survives.
4. Alt-dragging over the *whole* of what remains deletes the object outright, because a mask
   with no pixels is not a state the server will store.
5. The brush's own size control (`]` / `[`) actually changes what a stroke paints — the
   claim a status line reporting a new number without an effect behind it would fail.

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

#: How much of the shapes layer is lit, at all -- a coarse but sufficient stand-in for "did
#: anything visibly get painted", which an API-only check cannot tell apart from a request
#: that silently failed to reach the canvas at all.
LIT_PIXELS = """
() => {
  const canvas = document.querySelectorAll('canvas')[1];
  const context = canvas.getContext('2d');
  const { data } = context.getImageData(0, 0, canvas.width, canvas.height);
  let lit = 0;
  for (let i = 3; i < data.length; i += 4) if (data[i] > 8) lit += 1;
  return lit;
}
"""


def png_bytes(width: int = IMAGE_WIDTH, height: int = IMAGE_HEIGHT) -> bytes:
    from PIL import Image

    buffer = io.BytesIO()
    Image.new("RGB", (width, height), "#1e293b").save(buffer, format="PNG")
    return buffer.getvalue()


def seed(base: str, token: str) -> str:
    org = api(base, token, "/organizations")[0]
    project = api(base, token, "/projects", {
        "organization_id": org["id"],
        "slug": "brush",
        "name": "Brush",
        "description": "Painting a mask instead of placing its vertices.",
        "labels": [{"name": "car", "color": "#ef4444"}],
    })
    task = api(base, token, "/tasks", {
        "project_id": project["id"], "name": "Frame", "media_kind": "image",
    })
    api(base, token, f"/tasks/{task['id']}/assets", files=png_bytes(),
        filename="a.png", content_type="image/png")
    job = api(base, token, f"/tasks/{task['id']}/jobs")[0]
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

    with tempfile.TemporaryDirectory(prefix="curvevision-brush-") as workspace:
        process, handshake = start_server(Path(workspace) / "data")
        base, token = handshake["url"], handshake["token"]
        try:
            job_id = seed(base, token)
            print("a one-frame job labelled car\n")
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
                page.wait_for_timeout(1200)

                canvas = page.locator("canvas").nth(1)  # the shapes layer

                def draw(x1: float, y1: float, x2: float, y2: float, *, erase: bool = False) -> None:
                    box = canvas.bounding_box()
                    assert box is not None
                    if erase:
                        page.keyboard.down("Alt")
                    page.mouse.move(box["x"] + x1, box["y"] + y1)
                    page.mouse.down()
                    page.mouse.move(box["x"] + x2, box["y"] + y2, steps=8)
                    page.mouse.up()
                    if erase:
                        page.keyboard.up("Alt")
                    page.wait_for_timeout(200)

                def save() -> None:
                    page.get_by_text("Save", exact=True).click()
                    page.wait_for_timeout(700)

                def stored() -> list[dict]:
                    return api(base, token, f"/jobs/{job_id}/annotations")["shapes"]

                page.locator('[data-label-name="car"]').click()
                page.wait_for_timeout(150)
                page.get_by_title("Brush (paint a mask)").click()
                page.wait_for_timeout(150)

                before = page.evaluate(LIT_PIXELS)

                # ------------------------------------------------------- a fresh stroke
                draw(200, 150, 260, 200)
                after_stroke = page.evaluate(LIT_PIXELS)
                print(f"  lit pixels: {before} before, {after_stroke} right after the stroke")
                check(after_stroke > before,
                      "the stroke visibly paints something on the shapes layer",
                      f"lit pixel count did not grow ({before} -> {after_stroke})")

                save()
                shapes = stored()
                check(len(shapes) == 1, "the stroke reached the annotation tables as one shape",
                      f"expected 1 stored shape, found {len(shapes)}")
                if len(shapes) != 1:
                    browser.close()
                    raise SystemExit(1)
                shape = shapes[0]
                mask = shape["mask"]
                print(f"  stored: type={shape['shape_type']!r}, frame={shape['frame']}, "
                      f"box=({mask['left']},{mask['top']},{mask['width']}x{mask['height']})")
                check(shape["shape_type"] == "mask", "it is stored as a mask, not a rectangle",
                      f"the stored shape type is {shape['shape_type']!r}")
                check(shape["label_id"] is not None and mask["width"] > 0 and mask["height"] > 0,
                      "with a real box, not an empty placeholder",
                      f"the stored mask box is {mask['width']}x{mask['height']}")
                first_area = sum(run for i, run in enumerate(mask["rle"]) if i % 2 == 1)
                check(first_area > 0, "and covers a real number of pixels",
                      f"the stored mask covers {first_area} pixels")

                # Re-selecting is a plain canvas click through the Select tool, not the
                # object list: `focusAnnotation` (what a row click does) re-centres the
                # viewport on the object, which would silently invalidate every fixed
                # screen coordinate the rest of this harness relies on.
                def select_at(x: float, y: float) -> None:
                    page.get_by_title("Select (V)").click()
                    page.wait_for_timeout(100)
                    box = canvas.bounding_box()
                    assert box is not None
                    page.mouse.click(box["x"] + x, box["y"] + y)
                    page.wait_for_timeout(100)
                    page.get_by_title("Brush (paint a mask)").click()
                    page.wait_for_timeout(100)

                # ------------------------------------------------------------ growing it
                select_at(230, 175)  # the midpoint of the first stroke, guaranteed painted
                draw(320, 150, 340, 170)  # away from the first stroke entirely
                save()
                shapes = stored()
                check(len(shapes) == 1,
                      "painting elsewhere while it is selected edits it rather than adding a shape",
                      f"expected 1 shape after growing, found {len(shapes)}")
                grown_area = sum(run for i, run in enumerate(shapes[0]["mask"]["rle"]) if i % 2 == 1)
                print(f"  area: {first_area} -> {grown_area} after growing")
                check(grown_area > first_area, "and the mask grew",
                      f"the mask did not grow ({first_area} -> {grown_area})")

                # ------------------------------------------------------------- shrinking it
                select_at(330, 160)  # the midpoint of the growing stroke, well clear of
                                     # where the shrink below is about to erase
                draw(240, 195, 260, 200, erase=True)  # the tail end of the first stroke
                save()
                shapes = stored()
                check(len(shapes) == 1, "erasing part of it leaves the object in place",
                      f"expected 1 shape after erasing part of it, found {len(shapes)}")
                shrunk_area = sum(run for i, run in enumerate(shapes[0]["mask"]["rle"]) if i % 2 == 1)
                print(f"  area: {grown_area} -> {shrunk_area} after erasing part of it")
                check(shrunk_area < grown_area, "and the mask shrank",
                      f"the mask did not shrink ({grown_area} -> {shrunk_area})")

                # ---------------------------------------------- the brush's own size control
                select_at(330, 160)  # still inside the growing stroke, untouched above
                for _ in range(40):
                    page.keyboard.press("]")
                status = page.locator("[data-brush-status]")
                said = status.inner_text().strip() if status.count() else ""
                print(f"  after 40x ']': {said!r}")
                check("94px" in said,
                      "the status line reports the size the [ / ] keys actually set",
                      f"the status line says {said!r}, not the expected size")

                # ---------------------------------------------------- erasing all of it
                # One erase, at the size just set, centred between the two remaining
                # strokes -- the claim that ']' is not merely cosmetic text, and the closing
                # claim of the whole harness: nothing survives it.
                box = canvas.bounding_box()
                assert box is not None
                cx, cy = 270, 165
                page.keyboard.down("Alt")
                page.mouse.move(box["x"] + cx, box["y"] + cy)
                page.mouse.down()
                page.mouse.move(box["x"] + cx + 5, box["y"] + cy + 5, steps=4)
                page.mouse.up()
                page.keyboard.up("Alt")
                page.wait_for_timeout(200)
                save()
                shapes = stored()
                print(f"  shapes after erasing all of it: {len(shapes)}")
                check(len(shapes) == 0,
                      "erasing every pixel deletes the object, leaving no empty mask behind",
                      f"expected 0 shapes after erasing everything, found {len(shapes)}")

                shot = Path("/tmp/curvevision-mask-brush.png")
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
