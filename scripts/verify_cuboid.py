#!/usr/bin/env python3
"""Draw a cuboid in a real browser, and check it round-trips through real CVAT's own XML.

    python scripts/verify_cuboid.py

`ShapeType.CUBOID` has been in the domain model since early in this project's history --
`SHAPE_MIN_POINTS`, the IoU comparison used for merging and quality scoring, and track
interpolation all already knew about it -- but nothing let a human draw one, nothing painted
it, and no export format carried it. `cuboidTool.test.ts` and `geometry.test.ts` prove the
two-stage gesture and the point math without a DOM; `test_formats_robotics.py` proves the CVAT
XML encoding against hand-built `ShapeRecord`s. Neither can prove that a real drag on a real
canvas produces a box anyone can see, or that the box a person actually drew is the box that
lands in a file real CVAT would accept.

**The point order is not this project's invention.** `CVAT_ATTRS` below is CVAT's own on-disk
convention for a `<cuboid>` element -- `xtl1,ytl1,xbl1,ybl1,xtr1,ytr1,xbr1,ybr1,xtl2,...` --
independently confirmed from CVAT's `dataset_manager/formats/cvat.py`, not guessed at. This
harness draws a box with a real mouse gesture and checks the numbers that land in the exported
XML against exactly where the mouse went, which is the only way to know the two conventions
actually agree rather than merely both existing.

Four claims:

1. The tool is reachable, and its hint bar names which of its two stages is active.
2. Dragging the front face then clicking to set the depth paints something visible on the
   shapes layer -- not only an API record of one.
3. The stored shape has all 8 corners, at the pixels the front-face drag and the depth click
   actually specified.
4. Exporting to `cvat_xml` writes CVAT's own 16 named attributes, with the same numbers.

Needs the packaged server (`python desktop/sidecar/build.py`) and a Chromium Playwright can
drive; set `CURVEVISION_CHROMIUM` if the bundled one is not found.
"""

from __future__ import annotations

import io
import json
import sys
import tempfile
import urllib.request
import xml.etree.ElementTree as ET
import zipfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

from screenshot import api, find_chromium, start_server  # noqa: E402

IMAGE_WIDTH = 480
IMAGE_HEIGHT = 320

#: CVAT's own attribute names for a cuboid, in the order its `points` list carries them.
#: Independently confirmed from CVAT's `dataset_manager/formats/cvat.py`, not a guess.
CVAT_ATTRS = (
    "xtl1", "ytl1", "xbl1", "ybl1", "xtr1", "ytr1", "xbr1", "ybr1",
    "xtl2", "ytl2", "xbl2", "ybl2", "xtr2", "ytr2", "xbr2", "ybr2",
)

# The front face, and the depth drag from where that face's drag ends.
FRONT = (60, 60, 180, 140)  # x1, y1, x2, y2
DEPTH_TO = (220, 110)  # where the second click lands; (dx, dy) = (40, -30) from (180, 140)

#: The 8 corners this gesture should produce, in CurveVision's own point order (front
#: top-left, front bottom-left, front top-right, front bottom-right, then the back face the
#: same way) -- worked out by hand once here, so the harness is checking the tool against an
#: independent computation rather than against itself.
EXPECTED = [
    60, 60,     # front top-left
    60, 140,    # front bottom-left
    180, 60,    # front top-right
    180, 140,   # front bottom-right
    100, 30,    # back top-left   (60+40, 60-30)
    100, 110,   # back bottom-left (60+40, 140-30)
    220, 30,    # back top-right   (180+40, 60-30)
    220, 110,   # back bottom-right (180+40, 140-30)
]

#: `fitToImage`'s own margin (`viewport.ts`) -- the canvas is essentially never the same
#: pixel size as the image, so a click has to go through the same fit-and-centre transform
#: the editor itself uses, not straight through as if the two were 1:1.
FIT_MARGIN = 0.04


def image_to_screen(box: dict, x: float, y: float) -> tuple[float, float]:
    """An image-space point to a canvas-relative screen point, replicating `fitToImage`."""
    scale = min(box["width"] / IMAGE_WIDTH, box["height"] / IMAGE_HEIGHT) * (1 - FIT_MARGIN)
    offset_x = (IMAGE_WIDTH - box["width"] / scale) / 2
    offset_y = (IMAGE_HEIGHT - box["height"] / scale) / 2
    return (x - offset_x) * scale, (y - offset_y) * scale


#: `CuboidTool.DEPTH_STATUS` in `tools.ts`, mirrored here so this harness can tell the
#: depth-stage hint apart from the idle one -- which also mentions the word "depth" in its
#: own description of the gesture, so a substring check on that word alone cannot fail.
DEPTH_STATUS = "Move to set the depth, then click to place the back face"

#: Whether any pixel within `radius` CSS pixels of `(sx, sy)` on the shapes layer is opaque.
#: A coarse "how many pixels are lit in total" count cannot tell "the wireframe is drawn
#: correctly" apart from "nothing paints, so the renderer fell through to the generic
#: point-by-point polyline every other shape type gets" -- that fallback still draws *some*
#: visible line (it just connects the wrong corners), so it lights plenty of pixels too. This
#: probes a specific point instead: read in backing-store pixels, since the canvas is scaled
#: by `devicePixelRatio` while every coordinate elsewhere in this harness is a CSS pixel.
PROBE_JS = """
([sx, sy, radius]) => {
  const canvas = document.querySelectorAll('canvas')[1];
  const context = canvas.getContext('2d');
  const dpr = window.devicePixelRatio || 1;
  const cx = Math.round(sx * dpr);
  const cy = Math.round(sy * dpr);
  const r = Math.ceil(radius * dpr);
  const left = Math.max(0, cx - r);
  const top = Math.max(0, cy - r);
  const w = Math.min(canvas.width - left, r * 2 + 1);
  const h = Math.min(canvas.height - top, r * 2 + 1);
  if (w <= 0 || h <= 0) return false;
  const { data } = context.getImageData(left, top, w, h);
  for (let i = 3; i < data.length; i += 4) if (data[i] > 8) return true;
  return false;
}
"""


def png_bytes(width: int = IMAGE_WIDTH, height: int = IMAGE_HEIGHT) -> bytes:
    from PIL import Image

    buffer = io.BytesIO()
    Image.new("RGB", (width, height), "#1e293b").save(buffer, format="PNG")
    return buffer.getvalue()


def download(base: str, token: str, path: str, payload: dict) -> bytes:
    """A POST whose response is bytes rather than JSON. `api` cannot do that."""
    request = urllib.request.Request(
        f"{base}/api/v1{path}", data=json.dumps(payload).encode(), method="POST"
    )
    request.add_header("Content-Type", "application/json")
    request.add_header("Authorization", f"Bearer {token}")
    with urllib.request.urlopen(request, timeout=60) as response:
        return bytes(response.read())


def seed(base: str, token: str) -> tuple[str, str]:
    org = api(base, token, "/organizations")[0]
    project = api(base, token, "/projects", {
        "organization_id": org["id"],
        "slug": "cuboids",
        "name": "Cuboids",
        "description": "The last shape type the platform could carry but not draw.",
        "labels": [{"name": "crate", "color": "#f59e0b"}],
    })
    task = api(base, token, "/tasks", {
        "project_id": project["id"], "name": "Frame", "media_kind": "image",
    })
    api(base, token, f"/tasks/{task['id']}/assets", files=png_bytes(),
        filename="a.png", content_type="image/png")
    job = api(base, token, f"/tasks/{task['id']}/jobs")[0]
    return str(project["id"]), str(job["id"])


def main() -> int:
    from playwright.sync_api import sync_playwright

    failures: list[str] = []

    def check(condition: bool, passed: str, failed: str) -> None:
        if condition:
            print(f"  ok   {passed}")
        else:
            failures.append(failed)
            print(f"  FAIL {failed}")

    with tempfile.TemporaryDirectory(prefix="curvevision-cuboid-") as workspace:
        process, handshake = start_server(Path(workspace) / "data")
        base, token = handshake["url"], handshake["token"]
        try:
            project_id, job_id = seed(base, token)
            print("a one-frame job labelled crate\n")
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
                check(not raised, "the editor mounts without raising", f"page error: {raised}")

                # The label chip drawn above a shape (`Renderer.paintLabel`) is unconditional
                # once `showLabels` is on, and lights far more of the shapes layer than a
                # 1.75px wireframe does -- enough that the "something was painted" check below
                # would pass even with the wireframe itself entirely broken. Hiding it first
                # makes that check actually about the cuboid's own edges.
                page.get_by_title("Hide label names on shapes").click()
                page.wait_for_timeout(150)

                page.get_by_title("Cuboid (front face, then depth)").click()
                page.wait_for_timeout(200)
                hint = page.locator("[data-cuboid-status]")
                check(hint.count() == 1,
                      "picking the cuboid tool shows a hint bar",
                      "the cuboid tool shows no hint at all")
                opening = hint.inner_text().strip() if hint.count() else ""
                print(f"  before drawing: {opening!r}")
                check("front face" in opening,
                      "it explains the first stage before anything is drawn",
                      f"the opening hint does not mention the front face: {opening!r}")

                canvas = page.locator("canvas").nth(1)  # the shapes layer
                box = canvas.bounding_box()
                assert box is not None, "the canvas has no bounding box"

                x1, y1, x2, y2 = FRONT
                sx1, sy1 = image_to_screen(box, x1, y1)
                sx2, sy2 = image_to_screen(box, x2, y2)
                page.mouse.move(box["x"] + sx1, box["y"] + sy1)
                page.mouse.down()
                page.mouse.move(box["x"] + sx2, box["y"] + sy2, steps=8)
                page.mouse.up()
                page.wait_for_timeout(200)

                mid = hint.inner_text().strip() if hint.count() else ""
                print(f"  after the front-face drag: {mid!r}")
                # An exact match, not a substring: the idle hint *also* mentions "depth" in
                # its own description of the gesture, so a loose check here would pass
                # whether or not the tool actually advanced -- exactly the vacuous-check
                # trap this project's own harnesses have hit before.
                check(mid == DEPTH_STATUS,
                      "releasing the front-face drag moves on to the depth stage",
                      f"the hint after the drag reads {mid!r}, not the depth-stage line")

                depth_x, depth_y = DEPTH_TO  # an absolute image position, not an offset
                sdx, sdy = image_to_screen(box, depth_x, depth_y)
                page.mouse.move(box["x"] + sdx, box["y"] + sdy, steps=6)
                page.mouse.down()
                page.mouse.up()
                page.wait_for_timeout(300)

                def probe(image_x: float, image_y: float) -> bool:
                    sx, sy = image_to_screen(box, image_x, image_y)
                    return bool(page.evaluate(PROBE_JS, [sx, sy, 4]))

                # The front-top edge (60,60)-(180,60) and the back-top edge (100,30)-(220,30)
                # are both real edges of `CUBOID_EDGES`, and neither is a segment the generic
                # point-by-point fallback every other shape falls back to would draw -- that
                # fallback connects consecutive *indices* (front top-left straight to front
                # bottom-left, not to front top-right), so it cannot land on either midpoint.
                # Together they rule out both "nothing was painted" and "something was
                # painted, but it is not this wireframe".
                front_top_lit = probe(120, 60)
                back_top_lit = probe(160, 30)
                interior_lit = probe(140, 90)  # inside both faces, on no edge at all
                print(f"  front-top edge lit: {front_top_lit}, back-top edge lit: "
                      f"{back_top_lit}, interior (should be empty): {interior_lit}")
                check(front_top_lit and back_top_lit,
                      "both the front and back face are actually painted, at the edges "
                      "the drag and the depth click specified",
                      f"expected both edges lit; front={front_top_lit} back={back_top_lit}")
                check(not interior_lit,
                      "a cuboid is a wireframe, not filled -- its own interior stays empty",
                      "the cuboid's interior is painted, so it is being filled like a box")

                after = hint.inner_text().strip() if hint.count() else ""
                check("front face" in after,
                      "committing resets the hint to the first stage, ready for another box",
                      f"the hint after committing reads {after!r}")

                page.get_by_text("Save", exact=True).click()
                page.wait_for_timeout(700)

                shapes = api(base, token, f"/jobs/{job_id}/annotations")["shapes"]
                cuboids = [s for s in shapes if s["shape_type"] == "cuboid"]
                check(len(cuboids) == 1,
                      "exactly one cuboid reached the server",
                      f"expected one cuboid, found {len(cuboids)}")
                if len(cuboids) != 1:
                    browser.close()
                    raise SystemExit(1)

                points = cuboids[0]["points"]
                print(f"  stored points: {points}")
                check(len(points) == 16,
                      "it carries all 8 corners",
                      f"the stored cuboid has {len(points)} coordinates, not 16")
                deltas = [abs(a - b) for a, b in zip(points, EXPECTED, strict=True)]
                check(max(deltas) < 1.5,
                      "the corners land where the front-face drag and the depth click "
                      "actually specified, in CurveVision's front-then-back point order",
                      f"stored {points} differs from the expected {EXPECTED} by up to "
                      f"{max(deltas):.1f}px")

                shot = Path("/tmp/curvevision-cuboid.png")
                page.screenshot(path=str(shot), full_page=False)
                print(f"  (screenshot: {shot})")

                browser.close()

            # ---------------------------------------- what real CVAT's own file format reads
            archive = download(base, token, f"/projects/{project_id}/export",
                                {"format": "cvat_xml"})
            with zipfile.ZipFile(io.BytesIO(archive)) as bundle:
                xml_bytes = bundle.read("annotations.xml")
            root = ET.fromstring(xml_bytes)
            cuboid = root.find("./image/cuboid")
            check(cuboid is not None,
                  "the export writes a <cuboid> element, not a generic points shape",
                  "no <cuboid> element in the exported XML")
            if cuboid is None:
                raise SystemExit(1)

            print(f"\n  exported attributes: { {k: cuboid.get(k) for k in CVAT_ATTRS} }")
            exported = [float(cuboid.get(name, "nan")) for name in CVAT_ATTRS]
            deltas = [abs(a - b) for a, b in zip(exported, EXPECTED, strict=True)]
            check(max(deltas) < 0.5,
                  "the exported xtl1/ytl1/.../ybr2 attributes carry the same box the browser "
                  "drew, in the same order real CVAT itself writes and reads them",
                  f"exported {exported} differs from {EXPECTED} by up to {max(deltas):.2f}")
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
