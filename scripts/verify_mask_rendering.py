#!/usr/bin/env python3
"""Draw a mask's pixels in a real browser, and read the canvas back to prove it.

    python scripts/verify_mask_rendering.py

The editor drew a mask through the same branch as a rectangle: an empty box where the pixels
were. An annotator could not tell a mask covering a whole car from one covering its wing
mirror, and could not review an imported mask at all.

`mask.test.ts` proves the encoding and the hit-testing in a runtime with no DOM. It cannot
prove the one thing that matters here -- **that the right pixels are lit on screen**. So this
reads the shape canvas back with `getImageData` and checks the picture, rather than taking a
screenshot and hoping.

The seeded mask is a **plus with unequal arms**, which catches the two mistakes worth
catching. A renderer that fell back to the bounding box lights the whole frame. A renderer
that swapped rows for columns is indistinguishable from a correct one on any symmetric blob
-- so the arms reach different distances, which makes the transposed picture a different
picture, and the harness probes the pixel only a transposed mask would cover.

Six claims:

1. The crossing of the two bars is painted, and so is each arm.
2. An empty part of the frame is **not** -- the bounding box is not the mask.
3. The rows and columns are not swapped.
4. The frame is drawn at its own aspect ratio, which is what makes 1-3 measurable.
5. Clicking a covered pixel selects the mask.
6. Clicking an uncovered part of its bounding box does not.

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

from screenshot import api, find_chromium, start_server

IMAGE_WIDTH = 480
IMAGE_HEIGHT = 320

#: The mask's box is the whole frame, on purpose.
#:
#: It makes the harness self-locating. The shape's outline traces its bounding box, so with
#: the box equal to the frame the outline is drawn exactly around the fitted image -- and the
#: bounding box of everything lit on the shapes layer *is* the frame's rectangle on the
#: canvas. Every frame coordinate can then be converted without reimplementing the editor's
#: fit, which the first version of this harness tried to reverse-engineer from the painted
#: extent and got wrong.
#:
#: It also sharpens the claim: a renderer that filled the bounding box would now fill the
#: entire frame, which is unmistakable.
MASK_LEFT = 0
MASK_TOP = 0
MASK_WIDTH = IMAGE_WIDTH
MASK_HEIGHT = IMAGE_HEIGHT

#: A plus with unequal arms that reach neither edge, so transposing rows and columns changes
#: the picture and the shape is plainly not the box.
BAR_X = (200, 211)  # the vertical bar's columns
BAR_Y = (150, 161)  # the horizontal bar's rows
ARM_X = (120, 361)  # how far the horizontal bar reaches
ARM_Y = (60, 261)  # how far the vertical bar reaches


def covers(x: int, y: int) -> bool:
    vertical = BAR_X[0] <= x < BAR_X[1] and ARM_Y[0] <= y < ARM_Y[1]
    horizontal = BAR_Y[0] <= y < BAR_Y[1] and ARM_X[0] <= x < ARM_X[1]
    return vertical or horizontal


def plus_flags() -> list[bool]:
    return [covers(x, y) for y in range(MASK_HEIGHT) for x in range(MASK_WIDTH)]


def encode(flags: list[bool]) -> list[int]:
    """The same convention as `formats/rle.py`: alternating runs, starting with background."""
    runs: list[int] = []
    value = False
    run = 0
    for flag in flags:
        if flag == value:
            run += 1
            continue
        runs.append(run)
        value = flag
        run = 1
    if value:
        runs.append(run)
    return runs


def png_bytes(width: int = IMAGE_WIDTH, height: int = IMAGE_HEIGHT) -> bytes:
    from PIL import Image

    buffer = io.BytesIO()
    # Deliberately dark and flat: a lit mask pixel has to be distinguishable from the frame,
    # and a busy photograph would make "is this pixel painted" a judgement call.
    Image.new("RGB", (width, height), "#0b1220").save(buffer, format="PNG")
    return buffer.getvalue()


def seed(base: str, token: str) -> tuple[str, dict]:
    org = api(base, token, "/organizations")[0]
    project = api(base, token, "/projects", {
        "organization_id": org["id"],
        "slug": "masks",
        "name": "Masks",
        "description": "One mask, drawn as its pixels.",
        "labels": [{"name": "car", "color": "#ef4444", "allowed_shape_types": ["mask"]}],
    })
    car = project["labels"][0]["id"]
    task = api(base, token, "/tasks", {
        "project_id": project["id"], "name": "Frames", "media_kind": "image",
    })
    api(base, token, f"/tasks/{task['id']}/assets", files=png_bytes(),
        filename="a.png", content_type="image/png")
    job = api(base, token, f"/tasks/{task['id']}/jobs")[0]

    mask = {
        "rle": encode(plus_flags()),
        "left": MASK_LEFT,
        "top": MASK_TOP,
        "width": MASK_WIDTH,
        "height": MASK_HEIGHT,
    }
    document = api(base, token, f"/jobs/{job['id']}/annotations")
    api(base, token, f"/jobs/{job['id']}/annotations", {
        "annotation_version": document["annotation_version"],
        "created_shapes": [{
            "frame": 0,
            "label_id": car,
            "shape_type": "mask",
            "points": [
                float(MASK_LEFT), float(MASK_TOP),
                float(MASK_LEFT + MASK_WIDTH - 1), float(MASK_TOP + MASK_HEIGHT - 1),
            ],
            "rotation": 0.0,
            "occluded": False,
            "outside": False,
            "z_order": 0,
            "attributes": {},
            "mask": mask,
        }],
    }, method="PATCH")
    return str(job["id"]), mask


#: Reads one pixel of the shape canvas. The editor stacks three canvases; the shapes layer is
#: the second, and reading it alone keeps the frame image out of the answer.
READ_PIXEL = """
([x, y]) => {
  const canvas = document.querySelectorAll('canvas')[1];
  const context = canvas.getContext('2d');
  const data = context.getImageData(Math.round(x), Math.round(y), 1, 1).data;
  return [data[0], data[1], data[2], data[3]];
}
"""

#: Where the frame itself sits on the canvas, measured off the **media** layer.
#:
#: The media layer holds the frame image and nothing else, so its lit extent is exactly the
#: fitted frame rectangle. Measuring it off the *shapes* layer instead -- which the first
#: version of this did -- silently includes the label chip drawn above the shape's top-left
#: corner, which stretched the rectangle 15px upwards and put every probe below on the wrong
#: pixel. The failure looked like a renderer bug and was a harness bug.
FRAME_BOUNDS = """
() => {
  const canvas = document.querySelectorAll('canvas')[0];
  const context = canvas.getContext('2d');
  const { data, width, height } = context.getImageData(0, 0, canvas.width, canvas.height);
  let minX = Infinity, minY = Infinity, maxX = -Infinity, maxY = -Infinity, lit = 0;
  for (let y = 0; y < height; y += 1) {
    for (let x = 0; x < width; x += 1) {
      if (data[(y * width + x) * 4 + 3] > 8) {
        lit += 1;
        if (x < minX) minX = x;
        if (y < minY) minY = y;
        if (x > maxX) maxX = x;
        if (y > maxY) maxY = y;
      }
    }
  }
  return { minX, minY, maxX, maxY, lit, width, height };
}
"""

#: The same, for the shapes layer: how much of it is lit, and where.
PAINTED_BOUNDS = """
() => {
  const canvas = document.querySelectorAll('canvas')[1];
  const context = canvas.getContext('2d');
  const { data, width, height } = context.getImageData(0, 0, canvas.width, canvas.height);
  let minX = Infinity, minY = Infinity, maxX = -Infinity, maxY = -Infinity, lit = 0;
  for (let y = 0; y < height; y += 1) {
    for (let x = 0; x < width; x += 1) {
      if (data[(y * width + x) * 4 + 3] > 8) {
        lit += 1;
        if (x < minX) minX = x;
        if (y < minY) minY = y;
        if (x > maxX) maxX = x;
        if (y > maxY) maxY = y;
      }
    }
  }
  return { minX, minY, maxX, maxY, lit, width, height };
}
"""


def main() -> int:
    from playwright.sync_api import sync_playwright

    failures: list[str] = []

    def check(condition: bool, passed: str, failed: str) -> None:
        if condition:
            print(f"  ok   {passed}")
        else:
            failures.append(failed)
            print(f"  FAIL {failed}")

    flags = plus_flags()
    covered = sum(flags)
    print(f"a {MASK_WIDTH}x{MASK_HEIGHT} plus at ({MASK_LEFT}, {MASK_TOP}): "
          f"{covered} of {len(flags)} pixels covered, encoded as {len(encode(flags))} runs\n")

    with tempfile.TemporaryDirectory(prefix="curvevision-mask-") as workspace:
        process, handshake = start_server(Path(workspace) / "data")
        base, token = handshake["url"], handshake["token"]
        try:
            job_id, mask = seed(base, token)
            stored = api(base, token, f"/jobs/{job_id}/annotations")["shapes"]
            check(len(stored) == 1 and stored[0]["mask"] == mask,
                  "the mask reached the server intact",
                  f"the server stored {stored[0]['mask'] if stored else None}")

            injection = json.dumps({
                "url": base, "token": token, "data_dir": handshake["data_dir"],
                "version": handshake["version"], "desktop": True,
            })

            with sync_playwright() as p:
                launch: dict[str, object] = {"headless": True}
                if (chromium := find_chromium()) is not None:
                    launch["executable_path"] = chromium
                browser = p.chromium.launch(**launch)
                page = browser.new_page(viewport={"width": 1280, "height": 900})
                raised: list[str] = []
                page.on("pageerror", lambda exc: raised.append(str(exc)))
                page.add_init_script(f"window.__CURVEVISION__ = {injection};")
                page.goto(f"{base}/jobs/{job_id}", wait_until="networkidle")
                page.wait_for_selector("canvas", timeout=60_000)
                page.wait_for_timeout(2000)
                check(not raised, "the editor mounts without raising", f"page error: {raised}")

                painted = page.evaluate(PAINTED_BOUNDS)
                print(f"  shapes layer: {painted['lit']} lit pixels, "
                      f"box ({painted['minX']}, {painted['minY']})-"
                      f"({painted['maxX']}, {painted['maxY']}) "
                      f"on a {painted['width']}x{painted['height']} canvas")
                check(painted["lit"] > 0,
                      "the mask puts something on the shapes layer",
                      "nothing at all was painted on the shapes layer")
                if not painted["lit"]:
                    browser.close()
                    raise SystemExit(1)

                # Where the frame is, measured off the layer that holds only the frame.
                frame = page.evaluate(FRAME_BOUNDS)
                span_x = frame["maxX"] - frame["minX"] + 1
                span_y = frame["maxY"] - frame["minY"] + 1
                scale = span_x / IMAGE_WIDTH
                aspect = span_x / span_y
                print(f"  the frame occupies {span_x}x{span_y} canvas pixels at "
                      f"({frame['minX']}, {frame['minY']}) "
                      f"(scale {scale:.3f}, aspect {aspect:.3f} against "
                      f"{IMAGE_WIDTH / IMAGE_HEIGHT:.3f})")
                check(abs(aspect - IMAGE_WIDTH / IMAGE_HEIGHT) < 0.02,
                      "the frame is drawn at its own aspect ratio, so the conversion below "
                      "is sound",
                      f"the frame's aspect is {aspect:.3f}, not {IMAGE_WIDTH / IMAGE_HEIGHT:.3f}")

                # The claim. A renderer filling the bounding box would light the whole frame;
                # the plus covers a known fraction of it, and the outline adds a perimeter.
                coverage = painted["lit"] / (span_x * span_y)
                expected = sum(flags) / (IMAGE_WIDTH * IMAGE_HEIGHT)
                print(f"  {coverage:.1%} of the frame is lit; the mask covers {expected:.1%}")
                check(coverage < 0.5,
                      "the mask does not fill its bounding box",
                      f"{coverage:.1%} of the frame is lit, so the renderer is still "
                      "filling the bounding box rather than the pixels")

                def at_image(x: float, y: float) -> list[int]:
                    """One frame pixel, read off the shapes layer."""
                    return page.evaluate(READ_PIXEL, [
                        frame["minX"] + (x + 0.5) * scale,
                        frame["minY"] + (y + 0.5) * scale,
                    ])

                crossing = at_image((BAR_X[0] + BAR_X[1]) / 2, (BAR_Y[0] + BAR_Y[1]) / 2)
                arm_top = at_image((BAR_X[0] + BAR_X[1]) / 2, ARM_Y[0] + 4)
                arm_left = at_image(ARM_X[0] + 4, (BAR_Y[0] + BAR_Y[1]) / 2)
                # Inside the frame, far from both bars: the pixel a box-filling renderer
                # would paint and a correct one leaves alone.
                empty = at_image(60, 40)
                # Asymmetric on purpose: transposing rows and columns puts the vertical bar
                # here, so a transposed renderer lights it.
                transposed = at_image((BAR_Y[0] + BAR_Y[1]) / 2, (BAR_X[0] + BAR_X[1]) / 2)

                print(f"  crossing of the bars: rgba{tuple(crossing)}")
                print(f"  top of the vertical arm: rgba{tuple(arm_top)}")
                print(f"  left of the horizontal arm: rgba{tuple(arm_left)}")
                print(f"  empty corner of the frame: rgba{tuple(empty)}")
                print(f"  where a transposed mask would put a bar: rgba{tuple(transposed)}")

                check(crossing[3] > 8, "the crossing of the two bars is painted",
                      f"the centre of the mask is transparent: rgba{tuple(crossing)}")
                check(arm_top[3] > 8 and arm_left[3] > 8,
                      "so are both arms, which reach different distances",
                      f"an arm is transparent: top rgba{tuple(arm_top)}, "
                      f"left rgba{tuple(arm_left)}")
                check(empty[3] <= 8,
                      "an empty part of the frame is NOT painted -- the box is not the mask",
                      f"empty frame is painted rgba{tuple(empty)}, so the renderer is still "
                      "filling the bounding box rather than the pixels")
                check(transposed[3] <= 8,
                      "and the rows and columns are not swapped",
                      f"a pixel only a transposed mask would cover is painted "
                      f"rgba{tuple(transposed)}")

                shot = Path("/tmp/curvevision-mask.png")
                page.screenshot(path=str(shot), full_page=False)
                print(f"  (screenshot: {shot})")

                # ---------------------------------------- picking follows the pixels
                canvas = page.locator("canvas").first
                box = canvas.bounding_box()
                assert box is not None
                # The canvas backing store may be denser than its CSS box; clicks are in CSS
                # pixels, so everything measured above is divided back down by that ratio.
                dpr = frame["width"] / box["width"]

                def click_image(x: float, y: float) -> None:
                    page.mouse.click(
                        box["x"] + (frame["minX"] + (x + 0.5) * scale) / dpr,
                        box["y"] + (frame["minY"] + (y + 0.5) * scale) / dpr,
                    )
                    page.wait_for_timeout(400)

                def selected() -> int:
                    return page.locator("[data-object-id][data-selected='true']").count()

                click_image((BAR_X[0] + BAR_X[1]) / 2, (BAR_Y[0] + BAR_Y[1]) / 2)
                on_pixel = selected()
                click_image(60, 40)
                off_pixel = selected()
                print(f"  selected after clicking the crossing: {on_pixel}; "
                      f"after clicking empty frame: {off_pixel}")

                check(on_pixel == 1,
                      "clicking a covered pixel selects the mask",
                      "clicking the middle of the mask selected nothing")
                check(off_pixel == 0,
                      "clicking an uncovered part of its bounding box does not",
                      "an empty part of the bounding box still picks the mask")
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
