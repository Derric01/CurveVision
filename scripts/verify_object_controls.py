#!/usr/bin/env python3
"""The selected object's controls, and moving a box against the edge, in the real editor.

    python scripts/verify_object_controls.py

Drives Chromium against the real server and checks what the *server* stored:

1. relabelling from the object panel saves the new label;
2. Q marks the object occluded, and that is saved;
3. "Front" puts an object above an overlapping one (its saved z-order is higher);
4. hiding an object takes it off the canvas and out of reach of a click, saving nothing;
5. a box dragged hard against the right edge of the image keeps its width (it used to be
   squashed, saved with no width at all);
6. a locked object does not move when dragged.

Needs the built web application (`npm --prefix web run build`) and a Chromium. Writes a
screenshot of the panel to `docs/images/object-controls.png` with `--screenshot`.
"""

from __future__ import annotations

import io
import json
import sys
import tempfile
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).parent))

from screenshot import api, find_chromium
from verify_workspace import PASSWORD, anonymous, start_web_server

WIDTH, HEIGHT = 480, 320


def png_bytes() -> bytes:
    from PIL import Image

    buffer = io.BytesIO()
    Image.new("RGB", (WIDTH, HEIGHT), (30, 41, 59)).save(buffer, format="PNG")
    return buffer.getvalue()


def main() -> int:
    from playwright.sync_api import sync_playwright

    failures: list[str] = []

    def check(condition: bool, passed: str, failed: str) -> None:
        if condition:
            print(f"  ok   {passed}")
        else:
            failures.append(failed)
            print(f"  FAIL {failed}")

    with tempfile.TemporaryDirectory(prefix="curvevision-objects-") as workdir:
        server, base = start_web_server(Path(workdir))
        try:
            anonymous(
                base,
                "/auth/register",
                {"username": "ines", "email": "ines@example.com", "password": PASSWORD},
            )
            session = anonymous(base, "/auth/login", {"identifier": "ines", "password": PASSWORD})
            token = session["access_token"]
            org = api(base, token, "/organizations", {"slug": "lab", "name": "Lab"})
            project = api(
                base,
                token,
                "/projects",
                {
                    "organization_id": org["id"],
                    "slug": "street",
                    "name": "Street",
                    "labels": [
                        {"name": "car", "color": "#ef4444"},
                        {"name": "bus", "color": "#22c55e"},
                    ],
                },
            )
            label_name = {label["id"]: label["name"] for label in project["labels"]}
            task = api(
                base,
                token,
                "/tasks",
                {"project_id": project["id"], "name": "Street", "media_kind": "image"},
            )
            api(
                base,
                token,
                f"/tasks/{task['id']}/assets",
                files=png_bytes(),
                filename="street.png",
                content_type="image/png",
            )
            job = api(base, token, f"/tasks/{task['id']}/jobs")[0]

            def shapes() -> list[dict[str, Any]]:
                stored = api(base, token, f"/jobs/{job['id']}/annotations")["shapes"]
                return sorted(stored, key=lambda shape: shape["points"][0])

            with sync_playwright() as p:
                launch: dict[str, object] = {"headless": True}
                if (chromium := find_chromium()) is not None:
                    launch["executable_path"] = chromium
                browser = p.chromium.launch(**launch)
                context = browser.new_context(viewport={"width": 1280, "height": 900})
                context.add_init_script(
                    f"localStorage.setItem('curvevision.access', {json.dumps(token)});"
                    "localStorage.setItem('curvevision.refresh', "
                    f"{json.dumps(session['refresh_token'])});"
                )
                page = context.new_page()
                raised: list[str] = []
                page.on("pageerror", lambda exc: raised.append(str(exc)))
                page.goto(f"{base}/jobs/{job['id']}", wait_until="networkidle")
                page.wait_for_selector("canvas", timeout=60_000)
                page.wait_for_timeout(800)

                canvas = page.locator("canvas").first.bounding_box()
                assert canvas is not None

                def to_screen(x: float, y: float) -> tuple[float, float]:
                    # The editor fits the frame to the canvas: centre it, scale by the
                    # smaller ratio. Read back rather than assumed elsewhere.
                    scale = min(canvas["width"] / WIDTH, canvas["height"] / HEIGHT)
                    left = canvas["x"] + (canvas["width"] - WIDTH * scale) / 2
                    top = canvas["y"] + (canvas["height"] - HEIGHT * scale) / 2
                    return left + x * scale, top + y * scale

                def drag(x1: float, y1: float, x2: float, y2: float) -> None:
                    page.mouse.move(*to_screen(x1, y1))
                    page.mouse.down()
                    page.mouse.move(*to_screen(x2, y2), steps=10)
                    page.mouse.up()

                def save() -> None:
                    # Autosave may already have sent it; the button is enabled only while
                    # something is unsaved.
                    button = page.locator("button", has_text="Save").first
                    if button.is_enabled():
                        button.click()
                    page.wait_for_timeout(1500)

                page.get_by_title("Rectangle (R)").click()
                drag(100, 100, 180, 160)
                drag(140, 130, 220, 190)
                page.get_by_title("Select (V)").click()
                save()
                first, second = shapes()

                # ------------------------------------------------------------ 1
                page.locator(f"[data-object-id='{first['id']}']").click()
                page.locator("[data-relabel]").select_option(label="bus")
                save()
                check(
                    label_name[shapes()[0]["label_id"]] == "bus",
                    "relabelling from the panel saves the new label",
                    f"label is {label_name[shapes()[0]['label_id']]}",
                )

                # ------------------------------------------------------------ 2
                page.keyboard.press("q")
                save()
                check(
                    shapes()[0]["occluded"] is True,
                    "Q marks the object occluded, and it is saved",
                    f"occluded is {shapes()[0]['occluded']}",
                )

                # ------------------------------------------------------------ 3
                page.locator("[data-restack='front']").click()
                save()
                stored = shapes()
                check(
                    stored[0]["z_order"] > stored[1]["z_order"],
                    "Front puts the object above the one it overlaps, and saves the order",
                    f"z-orders {stored[0]['z_order']} and {stored[1]['z_order']}",
                )
                if "--screenshot" in sys.argv:
                    page.screenshot(path=str(Path("docs/images/object-controls.png")))

                # ------------------------------------------------------------ 4
                # Choosing an object in the list zooms to it; back to the whole frame, so
                # image coordinates map to the screen the way `to_screen` assumes.
                page.get_by_title("Fit to frame").click()
                page.keyboard.press("Escape")
                before = shapes()
                page.locator(f"[data-object-hide='{second['id']}']").click()
                page.mouse.click(*to_screen(210, 180))  # only the hidden box is here
                check(
                    page.locator("[data-object-panel]").count() == 0 and shapes() == before,
                    "a hidden object cannot be clicked, and hiding saves nothing",
                    f"panel shown {page.locator('[data-object-panel]').count()}",
                )
                page.locator(f"[data-object-hide='{second['id']}']").click()

                # ------------------------------------------------------------ 5
                page.keyboard.press("Escape")
                start = next(s for s in shapes() if s["id"] == first["id"])["points"]
                width = start[2] - start[0]
                drag(110, 110, 470, 110)  # a press only the first box covers, far past the edge
                save()
                x1, _, x2, _ = next(s for s in shapes() if s["id"] == first["id"])["points"]
                check(
                    abs((x2 - x1) - width) < 0.01 and abs(x2 - WIDTH) < 0.01,
                    "a box dragged past the edge stops there and keeps its width",
                    f"box is now x {x1}..{x2}",
                )

                # ------------------------------------------------------------ 6
                page.locator(f"[data-object-lock='{second['id']}']").click()
                locked_before = next(s for s in shapes() if s["id"] == second["id"])["points"]
                page.keyboard.press("Escape")
                drag(210, 180, 50, 250)  # only the locked box is here
                save()
                locked_after = next(s for s in shapes() if s["id"] == second["id"])["points"]
                check(
                    locked_after == locked_before,
                    "a locked object does not move when dragged",
                    f"moved from {locked_before} to {locked_after}",
                )

                check(not raised, "no page errors", f"page errors: {raised}")
                browser.close()
        finally:
            server.terminate()

    print("PASS" if not failures else f"FAIL ({len(failures)})")
    return 0 if not failures else 1


if __name__ == "__main__":
    raise SystemExit(main())
