#!/usr/bin/env python3
"""Drive the intelligent scissors in a real browser and check the boundary it produces.

    python scripts/verify_scissors.py

`scissors.test.ts` proves the algorithm against synthetic images and `scissorsTool.test.ts`
proves the state machine. Neither can tell you whether the tool *reaches* the pixels once it
is inside the editor — whether the frame rasterises, whether `getImageData` is allowed, and
whether real pointer events land where the maths expects. Two of the three defects this
project has shipped were exactly that kind: everything green, nothing working.

The image is a disc on a plain background, seeded through the API. A disc is chosen because
the right answer is known everywhere and is emphatically not a straight line: clicking two
points a quarter-turn apart and getting back a chord means the tool ignored the image.

Needs the packaged server (`python desktop/sidecar/build.py`) and a Chromium Playwright can
drive; set `CURVEVISION_CHROMIUM` if the bundled one is not found.
"""

from __future__ import annotations

import io
import json
import math
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

from screenshot import api, canvas_point, find_chromium, start_server  # noqa: E402

WIDTH = 640
HEIGHT = 480
CENTRE = (320, 240)
RADIUS = 150


def disc_png() -> bytes:
    """A dark disc on a light field, with a little texture so the edge is the only edge."""
    from PIL import Image, ImageDraw

    image = Image.new("RGB", (WIDTH, HEIGHT), "#e8e8e8")
    draw = ImageDraw.Draw(image)
    # Faint texture: an untextured background makes every path equally cheap, which is not
    # what a photograph looks like and would flatter the tool.
    for y in range(0, HEIGHT, 7):
        draw.line([(0, y), (WIDTH, y)], fill="#dedede")
    draw.ellipse(
        [CENTRE[0] - RADIUS, CENTRE[1] - RADIUS, CENTRE[0] + RADIUS, CENTRE[1] + RADIUS],
        fill="#1e293b",
    )
    buffer = io.BytesIO()
    image.save(buffer, format="PNG")
    return buffer.getvalue()


def seed(base: str, token: str) -> str:
    org = api(base, token, "/organizations")[0]
    project = api(base, token, "/projects", {
        "organization_id": org["id"], "slug": "scissors", "name": "Scissors",
        "description": "A disc, for checking that the wire follows a curve.",
        "labels": [{"name": "disc", "color": "#f97316", "allowed_shape_types": ["polygon"]}],
    })
    task = api(base, token, "/tasks", {
        "project_id": project["id"], "name": "Disc", "media_kind": "image",
    })
    api(base, token, f"/tasks/{task['id']}/assets", files=disc_png(), filename="disc.png",
        content_type="image/png")
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

    print(f"image: {WIDTH}x{HEIGHT}, disc radius {RADIUS} at {CENTRE}\n")

    with tempfile.TemporaryDirectory(prefix="curvevision-scissors-") as workspace:
        process, handshake = start_server(Path(workspace) / "data")
        base, token = handshake["url"], handshake["token"]
        try:
            job_id = seed(base, token)
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

                # Pick the label first. Every drawing tool declines silently without one —
                # `activeLabelId()` returns null and `onPointerDown` returns nothing — and the
                # first run of this harness spent a while looking like a scissors bug when it
                # was a missing click.
                page.get_by_text("disc", exact=True).first.click()
                page.wait_for_timeout(300)

                # The tool must be offered, and reachable by its shortcut like every other.
                button = page.get_by_role("button", name="Scissors (snaps to edges)")
                check(button.count() == 1, "the scissors tool is in the toolbar",
                      "no scissors button in the toolbar")
                button.click()
                page.wait_for_timeout(300)

                box = page.locator("canvas").first.bounding_box()
                assert box is not None, "the canvas has no bounding box"

                def click_image(x: float, y: float) -> None:
                    sx, sy = canvas_point(box, (WIDTH, HEIGHT), x, y)
                    page.mouse.move(sx, sy)
                    page.wait_for_timeout(120)
                    page.mouse.click(sx, sy)
                    page.wait_for_timeout(400)

                # Two points a quarter-turn apart on the disc: top, then right.
                top = (CENTRE[0], CENTRE[1] - RADIUS)
                right = (CENTRE[0] + RADIUS, CENTRE[1])
                click_image(*top)
                click_image(*right)
                page.keyboard.press("Enter")

                # Poll rather than sleep: the write goes through the autosave buffer, and a
                # fixed wait is the classic way to get a harness that passes on a fast machine
                # and fails in CI.
                shapes: list[dict] = []
                for _ in range(40):
                    page.wait_for_timeout(500)
                    document = api(base, token, f"/jobs/{job_id}/annotations")
                    shapes = document.get("shapes", [])
                    if shapes:
                        break
                check(len(shapes) == 1, f"one polygon was written ({len(shapes)})",
                      f"expected 1 shape, got {len(shapes)}")
                if not shapes:
                    browser.close()
                    raise SystemExit(1)

                points = shapes[0]["points"]
                vertices = [(points[i], points[i + 1]) for i in range(0, len(points), 2)]
                check(shapes[0]["shape_type"] == "polygon", "it is a polygon",
                      f"shape type is {shapes[0]['shape_type']}")

                # The measurement that matters: every vertex should sit on the disc's rim.
                radii = [math.dist(v, CENTRE) for v in vertices]
                worst = max(abs(r - RADIUS) for r in radii)
                print(f"  vertices: {len(vertices)}; radius {min(radii):.1f}-{max(radii):.1f} "
                      f"(disc is {RADIUS}); worst deviation {worst:.1f}px")
                check(worst <= 12,
                      f"every vertex lies on the rim (worst {worst:.1f}px off)",
                      f"a vertex is {worst:.1f}px off the rim; the wire did not follow the edge")

                # A chord between the two clicks would be ~212px long and pass ~44px inside
                # the rim at its midpoint. Distinguishing "followed the arc" from "drew a
                # line" is the whole point, so it is asserted directly rather than implied.
                midpoint = ((top[0] + right[0]) / 2, (top[1] + right[1]) / 2)
                chord_radius = math.dist(midpoint, CENTRE)
                closest_to_midpoint = min(math.dist(v, midpoint) for v in vertices)
                print(f"  a chord's midpoint sits {chord_radius:.0f}px from the centre "
                      f"({RADIUS - chord_radius:.0f}px inside the rim); "
                      f"nearest vertex to it is {closest_to_midpoint:.0f}px away")
                check(closest_to_midpoint > 20,
                      "the boundary bulges out to the arc rather than cutting the chord",
                      "a vertex sits on the chord: the tool drew a straight line")

                check(4 <= len(vertices) <= 60,
                      f"the polygon is editable, not pixel-dense ({len(vertices)} vertices)",
                      f"{len(vertices)} vertices: simplification did not run")

                shot = Path("/tmp/curvevision-scissors.png")
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
