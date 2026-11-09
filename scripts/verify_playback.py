#!/usr/bin/env python3
"""Play a job's frames in the real editor.

    python scripts/verify_playback.py

The editor could only step a frame at a time. This drives Chromium against the real
server, on a task of 12 frames, each a different shade so a frame is recognisable:

1. Play moves through the frames on its own;
2. every frame it passes through was drawn: playback waits for each image, never skipping
   one that had not loaded;
3. it stops at the last frame and shows Play again;
4. from the end, Play starts over at the first frame;
5. an arrow key pauses it, where it was.

Needs the built web application (`npm --prefix web run build`) and a Chromium.
"""

from __future__ import annotations

import io
import json
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

from screenshot import api, find_chromium
from verify_workspace import PASSWORD, anonymous, start_web_server

FRAMES = 12


def png_bytes(index: int) -> bytes:
    from PIL import Image

    buffer = io.BytesIO()
    Image.new("RGB", (320, 200), (20 * index, 60, 120)).save(buffer, format="PNG")
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

    with tempfile.TemporaryDirectory(prefix="curvevision-playback-") as workdir:
        server, base = start_web_server(Path(workdir))
        try:
            anonymous(
                base,
                "/auth/register",
                {"username": "paz", "email": "paz@example.com", "password": PASSWORD},
            )
            session = anonymous(base, "/auth/login", {"identifier": "paz", "password": PASSWORD})
            token = session["access_token"]
            org = api(base, token, "/organizations", {"slug": "lab", "name": "Lab"})
            project = api(
                base,
                token,
                "/projects",
                {
                    "organization_id": org["id"],
                    "slug": "clip",
                    "name": "Clip",
                    "labels": [{"name": "car", "color": "#ef4444"}],
                },
            )
            task = api(
                base,
                token,
                "/tasks",
                {"project_id": project["id"], "name": "Clip", "media_kind": "image"},
            )
            for index in range(FRAMES):
                api(
                    base,
                    token,
                    f"/tasks/{task['id']}/assets",
                    files=png_bytes(index),
                    filename=f"{index:02d}.png",
                    content_type="image/png",
                )
            job = api(base, token, f"/tasks/{task['id']}/jobs")[0]

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

                button = page.locator("[data-playback]")
                slider = page.get_by_role("slider", name="Frame")

                def frame() -> int:
                    return int(slider.input_value())

                def drawn_shade() -> int:
                    # The red channel of the media layer's centre pixel identifies the frame.
                    return int(
                        page.evaluate(
                            """() => {
                              const canvas = document.querySelector('canvas');
                              const ctx = canvas.getContext('2d');
                              const x = canvas.width / 2, y = canvas.height / 2;
                              return ctx.getImageData(x, y, 1, 1).data[0];
                            }"""
                        )
                    )

                page.locator("[data-playback-rate]").select_option("5")
                seen: list[tuple[int, int]] = []
                button.click()
                for _ in range(60):
                    seen.append((frame(), drawn_shade()))
                    if button.get_attribute("data-playback") == "paused":
                        break
                    page.wait_for_timeout(100)

                frames_seen = sorted({f for f, _ in seen})
                check(
                    frames_seen[-1] == FRAMES - 1 and len(frames_seen) >= FRAMES - 1,
                    "Play moves through the frames on its own",
                    f"frames seen {frames_seen}",
                )
                # The frame number moves a moment before its picture: this frame's shade, or
                # the one before, is fine; anything else is a frame skipped unseen.
                mismatched = [
                    (f, shade) for f, shade in seen if shade not in (20 * f, 20 * (f - 1))
                ]
                check(
                    not mismatched,
                    "every frame shown was drawn before playback moved on",
                    f"frame / drawn shade mismatches {mismatched[:5]}",
                )
                check(
                    button.get_attribute("data-playback") == "paused" and frame() == FRAMES - 1,
                    "it stops at the last frame and shows Play again",
                    f"state {button.get_attribute('data-playback')}, frame {frame()}",
                )

                button.click()
                page.wait_for_timeout(300)
                check(
                    frame() <= 2,
                    "from the end, Play starts over at the first frame",
                    f"frame {frame()}",
                )
                page.wait_for_timeout(600)
                page.keyboard.press("ArrowLeft")
                held = frame()
                page.wait_for_timeout(800)
                check(
                    button.get_attribute("data-playback") == "paused" and frame() == held,
                    "an arrow key pauses it where it is",
                    f"state {button.get_attribute('data-playback')}, frame {held} -> {frame()}",
                )

                check(not raised, "no page errors", f"page errors: {raised}")
                browser.close()
        finally:
            server.terminate()

    print("PASS" if not failures else f"FAIL ({len(failures)})")
    return 0 if not failures else 1


if __name__ == "__main__":
    raise SystemExit(main())
