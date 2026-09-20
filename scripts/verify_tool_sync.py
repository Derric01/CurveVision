#!/usr/bin/env python3
"""Press a tool's keyboard shortcut, and check the toolbar agrees with the engine.

    python scripts/verify_tool_sync.py

A toolbar click sets React's own `tool` state, which an effect in `AnnotationCanvas`
propagates down to `AnnotationEngine.setTool`. A keyboard shortcut instead calls
`engine.handleKey` directly, which switches the engine's tool with no path back to React at
all — there was no `EngineEvents.toolChanged`. Found while wiring the brush tool's status
hint, and confirmed with a live probe before touching anything: press `r`, and the engine
was genuinely on `RectangleTool` — a drag right after it produced a rectangle — while the
toolbar stayed highlighted on Select and any `tool === 'x'` hint stayed on whatever was
active before. Not a drawing bug — the wrong tool never actually ran — but a real one:
nothing on screen told the annotator which tool a key had just switched to.

`engine.test.ts` proves `toolChanged` fires from the right places, in a runtime with no DOM.
It cannot prove the wiring: that `AnnotationCanvas` forwards the event, that `EditorPage`
uses it rather than only its own `tool` state, and that the toolbar and the tool-specific
hint bar actually repaint from it. This is that proof.

Two claims:

1. Pressing a tool's shortcut key highlights that tool's button and un-highlights the one
   that was active before, exactly as clicking it would.
2. It also updates a tool-specific hint bar (the skeleton tool's), which a stale `tool`
   state would leave showing the previous tool's text -- or none at all.

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


def png_bytes(width: int = IMAGE_WIDTH, height: int = IMAGE_HEIGHT) -> bytes:
    from PIL import Image

    buffer = io.BytesIO()
    Image.new("RGB", (width, height), "#1e293b").save(buffer, format="PNG")
    return buffer.getvalue()


def seed(base: str, token: str) -> str:
    org = api(base, token, "/organizations")[0]
    project = api(base, token, "/projects", {
        "organization_id": org["id"],
        "slug": "toolsync",
        "name": "Tool sync",
        "description": "The toolbar agreeing with the engine after a keyboard shortcut.",
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

    with tempfile.TemporaryDirectory(prefix="curvevision-toolsync-") as workspace:
        process, handshake = start_server(Path(workspace) / "data")
        base, token = handshake["url"], handshake["token"]
        try:
            job_id = seed(base, token)
            print("a one-frame job, labelled car\n")
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

                def highlighted(title: str) -> bool:
                    classes = page.get_by_title(title).get_attribute("class") or ""
                    return "bg-curve-500" in classes

                check(highlighted("Select (V)"),
                      "the editor opens on the Select tool, highlighted",
                      "Select is not highlighted on open")
                check(not highlighted("Rectangle (R)"),
                      "and Rectangle is not",
                      "Rectangle is highlighted before anything switched to it")

                # The click on the canvas gives keyboard events somewhere real to land, the
                # way a focused body would in an actual browser session. The overlay layer
                # (last of the three stacked canvases) is the one that actually receives
                # pointer events; the other two would have the click intercepted by it.
                page.locator("canvas").last.click(position={"x": 5, "y": 5})
                page.keyboard.press("r")
                page.wait_for_timeout(300)

                check(highlighted("Rectangle (R)"),
                      "pressing 'r' highlights the Rectangle button",
                      "Rectangle is still not highlighted after pressing 'r'")
                check(not highlighted("Select (V)"),
                      "and un-highlights Select",
                      "Select is still highlighted after switching away from it")

                shapes_before = len(api(base, token, f"/jobs/{job_id}/annotations")["shapes"])
                box = page.locator("canvas").nth(1).bounding_box()
                assert box is not None
                page.mouse.move(box["x"] + 100, box["y"] + 100)
                page.mouse.down()
                page.mouse.move(box["x"] + 200, box["y"] + 200, steps=5)
                page.mouse.up()
                page.get_by_text("Save", exact=True).click()
                page.wait_for_timeout(700)
                shapes = api(base, token, f"/jobs/{job_id}/annotations")["shapes"]
                check(len(shapes) == shapes_before + 1 and shapes[-1]["shape_type"] == "rectangle",
                      "and the engine really is on Rectangle, not just the button",
                      f"drawing after 'r' produced {[s['shape_type'] for s in shapes]}, "
                      "not a new rectangle")

                # A tool-specific hint bar is the other thing a stale `tool` state breaks.
                page.keyboard.press("j")  # skeleton's shortcut
                page.wait_for_timeout(300)
                check(highlighted("Skeleton (joints, in order)"),
                      "pressing 'j' highlights the Skeleton button",
                      "Skeleton is not highlighted after pressing 'j'")
                hint = page.locator("[data-skeleton-status]")
                check(hint.count() == 1,
                      "and its hint bar appears, which only renders while tool === 'skeleton'",
                      "no skeleton hint bar appeared after switching to it by keyboard")

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
