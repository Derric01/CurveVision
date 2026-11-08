#!/usr/bin/env python3
"""Tag a whole frame in the editor, and take a tag off again.

    python scripts/verify_frame_tags.py

The server stored frame tags from the start, but until iteration 102 the editor could
neither show one nor make one; CVAT offers both beside its drawing tools. This drives
Chromium against the real server:

1. a frame with no tags says so, and offers every project label;
2. choosing a label tags the frame: the chip shows, the canvas shows it, the server has it
   on that frame, and that label is no longer offered for this frame;
3. a second tag sits beside the first;
4. another frame has tags of its own, and the first frame's are kept;
5. a box drawn after the tags saves, and the tags stay: a tag write and a save share the
   job (this does not isolate the version hand-off in `useAutosave.writeTags`, which the
   re-read after every write would mask);
6. removing a tag deletes that one and nothing else.

Needs the built web application (`npm --prefix web run build`) and a Chromium.
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

LABELS = [
    {"name": "car", "color": "#ef4444"},
    {"name": "night", "color": "#6366f1"},
    {"name": "blurred", "color": "#f59e0b"},
]


def png_bytes(shade: int) -> bytes:
    from PIL import Image

    buffer = io.BytesIO()
    Image.new("RGB", (480, 320), (30 + shade * 40, 41, 59)).save(buffer, format="PNG")
    return buffer.getvalue()


def appears(locator: Any, timeout: float = 15_000) -> bool:
    try:
        locator.wait_for(timeout=timeout)
        return True
    except Exception:
        return False


def main() -> int:
    from playwright.sync_api import sync_playwright

    failures: list[str] = []

    def check(condition: bool, passed: str, failed: str) -> None:
        if condition:
            print(f"  ok   {passed}")
        else:
            failures.append(failed)
            print(f"  FAIL {failed}")

    with tempfile.TemporaryDirectory(prefix="curvevision-tags-") as workdir:
        server, base = start_web_server(Path(workdir))
        try:
            anonymous(
                base,
                "/auth/register",
                {"username": "tove", "email": "tove@example.com", "password": PASSWORD},
            )
            session = anonymous(base, "/auth/login", {"identifier": "tove", "password": PASSWORD})
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
                    "labels": LABELS,
                },
            )
            label_id = {label["name"]: label["id"] for label in project["labels"]}
            task = api(
                base,
                token,
                "/tasks",
                {"project_id": project["id"], "name": "Clip", "media_kind": "image"},
            )
            for shade in range(2):
                api(
                    base,
                    token,
                    f"/tasks/{task['id']}/assets",
                    files=png_bytes(shade),
                    filename=f"{shade}.png",
                    content_type="image/png",
                )
            job = api(base, token, f"/tasks/{task['id']}/jobs")[0]

            def document() -> dict[str, Any]:
                return api(base, token, f"/jobs/{job['id']}/annotations")

            def tags_on(frame: int) -> list[str]:
                names = {v: k for k, v in label_id.items()}
                return sorted(
                    names[tag["label_id"]] for tag in document()["tags"] if tag["frame"] == frame
                )

            with sync_playwright() as p:
                launch: dict[str, object] = {"headless": True}
                if (chromium := find_chromium()) is not None:
                    launch["executable_path"] = chromium
                browser = p.chromium.launch(**launch)
                context = browser.new_context(viewport={"width": 1280, "height": 1000})
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
                add = page.locator("[data-frame-tag-add]")
                chip = page.locator("[data-frame-tag]")
                overlay = page.locator("[data-frame-tags-overlay]")

                def offered() -> list[str]:
                    return [
                        text
                        for text in add.locator("option").all_inner_texts()
                        if not text.endswith("…")
                    ]

                # ------------------------------------------------------------ 1
                check(
                    appears(page.locator("[data-frame-tags-empty]"))
                    and offered() == ["car", "night", "blurred"],
                    "an untagged frame says so, and offers every label",
                    f"empty notice {page.locator('[data-frame-tags-empty]').count()}, "
                    f"offered {offered()}",
                )

                # ------------------------------------------------------------ 2
                add.select_option(label="night")
                appears(page.locator("[data-frame-tag='night']"))
                check(
                    tags_on(0) == ["night"]
                    and appears(overlay.filter(has_text="night"), 5_000)
                    and "night" not in offered(),
                    "tagging shows the tag, on the canvas too, stores it, and stops offering it",
                    f"server has {tags_on(0)}, overlay {overlay.count()}, offered {offered()}",
                )

                # ------------------------------------------------------------ 3
                add.select_option(label="blurred")
                appears(page.locator("[data-frame-tag='blurred']"))
                check(
                    tags_on(0) == ["blurred", "night"] and chip.count() == 2,
                    "a second tag sits beside the first",
                    f"server has {tags_on(0)}, chips {chip.count()}",
                )

                # ------------------------------------------------------------ 4
                page.keyboard.press("d")
                appears(page.locator("[data-frame-tags-empty]"))
                add.select_option(label="car")
                appears(page.locator("[data-frame-tag='car']"))
                check(
                    tags_on(1) == ["car"] and tags_on(0) == ["blurred", "night"],
                    "another frame has tags of its own, and the first keeps its",
                    f"frame 1 {tags_on(1)}, frame 0 {tags_on(0)}",
                )

                # ------------------------------------------------------------ 5
                page.get_by_title("Rectangle (R)").click()
                box = page.locator("canvas").first.bounding_box()
                assert box is not None
                x, y = box["x"] + box["width"] / 2, box["y"] + box["height"] / 2
                page.mouse.move(x - 60, y - 40)
                page.mouse.down()
                page.mouse.move(x + 60, y + 40, steps=8)
                page.mouse.up()
                page.locator("button", has_text="Save").first.click()
                page.wait_for_timeout(1500)
                saved = [s for s in document()["shapes"] if s["frame"] == 1]
                conflict = page.get_by_text("changed", exact=False).count()
                check(
                    len(saved) == 1 and tags_on(1) == ["car"],
                    "a box drawn after tagging saves, and the tags stay",
                    f"frame 1 shapes {len(saved)}, tags {tags_on(1)}, conflict notices {conflict}",
                )

                # ------------------------------------------------------------ 6
                page.keyboard.press("Escape")
                page.keyboard.press("a")
                appears(page.locator("[data-frame-tag='night']"))
                page.click("[data-frame-tag-remove='night']")
                page.locator("[data-frame-tag='night']").wait_for(state="detached", timeout=10_000)
                check(
                    tags_on(0) == ["blurred"] and tags_on(1) == ["car"],
                    "removing a tag deletes that one and nothing else",
                    f"frame 0 {tags_on(0)}, frame 1 {tags_on(1)}",
                )

                check(not raised, "no page errors", f"page errors: {raised}")
                browser.close()
        finally:
            server.terminate()

    print()
    if failures:
        print(f"{len(failures)} check(s) failed")
        return 1
    print("every check passed")
    return 0


if __name__ == "__main__":
    sys.exit(main())
