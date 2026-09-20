#!/usr/bin/env python3
"""Draw on a frame that is not a job's first, and check what frame it was stored on.

    python scripts/verify_shape_frame.py

`AnnotationEngine` had no notion of "current frame" at all. Every tool builds its draft
through `draftAnnotation`, which sets `frame: 0` as a placeholder, and nothing corrected it
before this. Drawing a shape on any frame but a job's first silently saved it to frame 0
instead — no error, no warning, and the shape was simply not there the next time that frame
was opened. Every browser harness before this one used a single-frame task, which is the one
case where frame 0 is also the *only* frame, so nothing could have caught it.

`engine.test.ts` pins the fix at the unit level -- `setFrame` plus a draw, asserted on the
`created` listener -- which is the right place for the logic. It cannot prove the wiring: that
`AnnotationCanvas` actually calls `setFrame` when the job's frame slider moves, that the value
survives a save and a reload, and that it is not simply a constant that happens to match one
test's frame number. This is that proof, end to end, on the packaged application.

Two tools, not one: the fix lives in `AnnotationEngine.applyResult`, the one place every
tool's `created` result passes through, specifically so it would not need to be added to each
tool individually. Drawing with both a rectangle and an ellipse is what that claim is worth
checking against; a fix that only patched `RectangleTool` would pass a single-tool harness.

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
FRAME_COUNT = 4
#: Frames a shape is drawn on. Neither is the job's first, which is the one frame this bug
#: could not hide on, and they are different from each other so a fix that stamped every new
#: shape with the *same* wrong frame (rather than reading the actual current one) still fails.
RECTANGLE_FRAME = 2
ELLIPSE_FRAME = 3


def png_bytes(width: int = IMAGE_WIDTH, height: int = IMAGE_HEIGHT) -> bytes:
    from PIL import Image

    buffer = io.BytesIO()
    Image.new("RGB", (width, height), "#1e293b").save(buffer, format="PNG")
    return buffer.getvalue()


def seed(base: str, token: str) -> tuple[str, str]:
    """A job spanning `FRAME_COUNT` frames, labelled `car`."""
    org = api(base, token, "/organizations")[0]
    project = api(base, token, "/projects", {
        "organization_id": org["id"],
        "slug": "frames",
        "name": "Frames",
        "description": "Which frame a new shape lands on.",
        "labels": [{"name": "car", "color": "#ef4444"}],
    })
    task = api(base, token, "/tasks", {
        "project_id": project["id"], "name": "Sequence", "media_kind": "image",
    })
    for i in range(FRAME_COUNT):
        api(base, token, f"/tasks/{task['id']}/assets", files=png_bytes(),
            filename=f"{i}.png", content_type="image/png")
    job = api(base, token, f"/tasks/{task['id']}/jobs")[0]
    return str(job["id"]), str(task["id"])


def main() -> int:
    from playwright.sync_api import sync_playwright

    failures: list[str] = []

    def check(condition: bool, passed: str, failed: str) -> None:
        if condition:
            print(f"  ok   {passed}")
        else:
            failures.append(failed)
            print(f"  FAIL {failed}")

    with tempfile.TemporaryDirectory(prefix="curvevision-frame-") as workspace:
        process, handshake = start_server(Path(workspace) / "data")
        base, token = handshake["url"], handshake["token"]
        try:
            job_id, _task_id = seed(base, token)
            print(f"a {FRAME_COUNT}-frame job, drawing on frames "
                  f"{RECTANGLE_FRAME} and {ELLIPSE_FRAME}\n")
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

                next_button = page.locator("footer").get_by_role("button").nth(1)
                canvas = page.locator("canvas").nth(1)  # the shapes layer, not the media one

                prev_button = page.locator("footer").get_by_role("button").nth(0)

                def current_frame() -> int:
                    counter = page.locator("footer span.font-mono")
                    return int(counter.inner_text().split("/")[0].strip()) - 1

                def goto_frame(target: int) -> None:
                    button = next_button if target > current_frame() else prev_button
                    while current_frame() != target:
                        button.click()
                        page.wait_for_timeout(200)

                def draw(x1: float, y1: float, x2: float, y2: float) -> None:
                    box = canvas.bounding_box()
                    assert box is not None
                    page.mouse.move(box["x"] + x1, box["y"] + y1)
                    page.mouse.down()
                    page.mouse.move(box["x"] + x2, box["y"] + y2, steps=6)
                    page.mouse.up()
                    page.wait_for_timeout(300)

                def save() -> None:
                    # The object list is deliberately not optimistic -- it reflects
                    # `annotations.data`, the react-query cache, which only changes once a
                    # flush's write round-trips and the query is invalidated. Forcing a save
                    # here makes the harness deterministic instead of racing the 4-second
                    # periodic autosave.
                    button = page.get_by_text("Save", exact=True)
                    button.click()
                    page.wait_for_timeout(700)

                page.locator('[data-label-name="car"]').click()
                page.wait_for_timeout(150)

                def object_count() -> int:
                    # `uppercase` is a CSS transform on this heading, and Playwright's
                    # `inner_text` reports rendered text -- it reads "OBJECTS (n)" in the
                    # browser even though the DOM text is "Objects (n)". Pulling the number
                    # out of parentheses sidesteps the case question entirely.
                    text = page.get_by_text("bjects (", exact=False).inner_text()
                    return int(text.split("(")[1].split(")")[0])

                # --------------------------------------------------- frame 0: nothing yet
                check(object_count() == 0,
                      "a fresh job starts with nothing on its first frame",
                      f"frame 0 already shows {object_count()} object(s)")

                # --------------------------------------------------- rectangle, off frame 0
                goto_frame(RECTANGLE_FRAME)
                page.get_by_title("Rectangle (R)").click()
                page.wait_for_timeout(150)
                draw(120, 90, 260, 210)
                save()
                check(object_count() == 1,
                      f"the rectangle appears in the object list on frame {RECTANGLE_FRAME} once saved",
                      f"frame {RECTANGLE_FRAME} shows {object_count()} object(s) after saving")

                # --------------------------------------------------- ellipse, a different frame
                goto_frame(ELLIPSE_FRAME)
                check(object_count() == 0,
                      f"the rectangle did not follow onto frame {ELLIPSE_FRAME}",
                      f"frame {ELLIPSE_FRAME} already shows {object_count()} object(s) "
                      "before anything was drawn there")
                page.get_by_title("Ellipse (E)").click()
                page.wait_for_timeout(150)
                draw(140, 100, 240, 180)
                save()
                check(object_count() == 1,
                      f"the ellipse appears in the object list on frame {ELLIPSE_FRAME} once saved",
                      f"frame {ELLIPSE_FRAME} shows {object_count()} object(s) after saving")

                # --------------------------------------------------- the API, before reload
                stored = api(base, token, f"/jobs/{job_id}/annotations")["shapes"]
                by_type = {s["shape_type"]: s["frame"] for s in stored}
                print(f"  stored before reload: {[(s['shape_type'], s['frame']) for s in stored]}")
                check(len(stored) == 2, "both shapes reached the annotation tables",
                      f"expected 2 stored shapes, found {len(stored)}")
                check(by_type.get("rectangle") == RECTANGLE_FRAME,
                      f"the rectangle is stored on frame {RECTANGLE_FRAME}, where it was drawn",
                      f"the rectangle is stored on frame {by_type.get('rectangle')}, "
                      f"not {RECTANGLE_FRAME}")
                check(by_type.get("ellipse") == ELLIPSE_FRAME,
                      f"the ellipse is stored on frame {ELLIPSE_FRAME}, where it was drawn",
                      f"the ellipse is stored on frame {by_type.get('ellipse')}, "
                      f"not {ELLIPSE_FRAME}")

                # --------------------------------------------------- survives a reload
                # The failure mode this whole harness exists for: a shape that looked fine
                # in the live session because it was still sitting in the in-memory scene,
                # and was simply not there once the document was re-fetched from the server.
                page.reload(wait_until="networkidle")
                page.wait_for_selector("canvas", timeout=60_000)
                page.wait_for_timeout(1200)
                goto_frame(RECTANGLE_FRAME)
                check(object_count() == 1,
                      f"the rectangle is still on frame {RECTANGLE_FRAME} after a reload",
                      f"after reloading, frame {RECTANGLE_FRAME} shows {object_count()} object(s)")
                goto_frame(0)
                check(object_count() == 0,
                      "and frame 0 is still empty -- nothing collapsed onto it",
                      f"after reloading, frame 0 shows {object_count()} object(s)")

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
