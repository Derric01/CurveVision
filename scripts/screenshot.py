#!/usr/bin/env python3
"""Drive the real application in a real browser and photograph it.

Every image in the README is produced by this script, from the actual product, on real
photographs. Nothing is mocked up, drawn by hand, or touched afterwards — a screenshot
that flatters software into looking like something it is not is a lie with a long tail,
and this one has to survive somebody downloading the app.

The frames come from `docs/images/samples/`, which is public-domain and CC0 photography
with its provenance recorded alongside it. They are photographs rather than renders on
purpose: boxes drawn over flat vector shapes tell a reader nothing about whether the
editor copes with the images they actually have.

    python scripts/screenshot.py                    # writes docs/images/*.png
    python scripts/screenshot.py --headed           # watch it happen

It needs the packaged server (`python desktop/sidecar/build.py`) and a Chromium that
Playwright can drive. Set `CURVEVISION_CHROMIUM` if the bundled one is not found.
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
SIDECAR = REPO / "desktop" / "sidecar" / "dist" / "curvevision-local"
OUT = REPO / "docs" / "images"
SAMPLES = OUT / "samples"
HANDSHAKE_PREFIX = "CURVEVISION_READY "

#: Where the pre-installed browser tends to live. Playwright's own copy is used when its
#: version matches; otherwise an explicit path avoids a download this script should not do.
CHROMIUM_CANDIDATES = (
    os.environ.get("CURVEVISION_CHROMIUM", ""),
    "/opt/pw-browsers/chromium-1194/chrome-linux/chrome",
    "/usr/bin/chromium",
    "/usr/bin/google-chrome",
)


# --------------------------------------------------------------------------- sample data

#: The frames the demo project is seeded with, in the order they appear in the filmstrip.
#: Real photographs; see `docs/images/samples/CREDITS.md` for licence and attribution, and
#: `scripts/extract_sample_images.py` for where they came from.
FRAMES = ("coffee", "cat", "astronaut", "rocket")

#: A schema that covers the whole set, because that is what a real one has to do — the
#: first frame only exercises three of these, and a sidebar that listed only those three
#: would be a schema invented to flatter a screenshot.
LABELS = [
    {"name": "cup", "color": "#ef4444", "allowed_shape_types": ["rectangle"]},
    {"name": "saucer", "color": "#f59e0b", "allowed_shape_types": ["rectangle"]},
    {"name": "spoon", "color": "#38bdf8", "allowed_shape_types": ["rectangle"]},
    {"name": "cat", "color": "#a855f7", "allowed_shape_types": ["rectangle"]},
    {"name": "person", "color": "#22c55e", "allowed_shape_types": ["rectangle"]},
    {"name": "rocket", "color": "#ec4899", "allowed_shape_types": ["rectangle"]},
]

#: Boxes drawn on the first frame, in **image pixels** — measured off `coffee.jpg`, not
#: guessed as fractions of the browser window. They land on the objects at any viewport
#: size because `canvas_point` puts them through the same fit the editor uses.
BOXES = (
    ("saucer", 84, 99, 481, 376),
    ("cup", 178, 30, 406, 310),
    ("spoon", 324, 57, 426, 325),
)


def frame_bytes(name: str) -> bytes:
    path = SAMPLES / f"{name}.jpg"
    if not path.is_file():
        raise SystemExit(
            f"missing sample photograph: {path}\n"
            "regenerate them: pip install scikit-image && "
            "python scripts/extract_sample_images.py"
        )
    return path.read_bytes()


def frame_size(name: str) -> tuple[int, int]:
    from PIL import Image

    with Image.open(SAMPLES / f"{name}.jpg") as image:
        return image.size


# ------------------------------------------------------------------------------- server


def start_server(data_dir: Path) -> tuple[subprocess.Popen[str], dict[str, str]]:
    if not SIDECAR.is_file():
        raise SystemExit(
            f"the packaged server is missing at {SIDECAR}\n"
            "build it first: python desktop/sidecar/build.py"
        )
    if data_dir.exists():
        shutil.rmtree(data_dir)

    process = subprocess.Popen(
        [str(SIDECAR), "--data-dir", str(data_dir)],
        stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, bufsize=1,
    )
    assert process.stdout is not None
    deadline = time.time() + 120
    while time.time() < deadline:
        line = process.stdout.readline()
        if not line:
            break
        if line.startswith(HANDSHAKE_PREFIX):
            return process, json.loads(line[len(HANDSHAKE_PREFIX) :])
    process.kill()
    raise SystemExit("the server never announced itself")


def api(base: str, token: str, path: str, payload: object = None, files: bytes | None = None):
    url = f"{base}/api/v1{path}"
    if files is not None:
        boundary = "----curvevision-screenshot"
        body = b"".join([
            f"--{boundary}\r\n".encode(),
            b'Content-Disposition: form-data; name="files"; filename="frame.jpg"\r\n',
            b"Content-Type: image/jpeg\r\n\r\n",
            files,
            f"\r\n--{boundary}--\r\n".encode(),
        ])
        request = urllib.request.Request(url, data=body, method="POST")
        request.add_header("Content-Type", f"multipart/form-data; boundary={boundary}")
    else:
        data = json.dumps(payload).encode() if payload is not None else None
        request = urllib.request.Request(url, data=data, method="POST" if data else "GET")
        if data:
            request.add_header("Content-Type", "application/json")
    request.add_header("Authorization", f"Bearer {token}")

    for _ in range(60):
        try:
            with urllib.request.urlopen(request, timeout=30) as response:
                raw = response.read()
                return json.loads(raw) if raw else None
        except urllib.error.HTTPError as exc:
            raise SystemExit(f"{path} -> {exc.code}: {exc.read()[:400]!r}") from exc
        except urllib.error.URLError:
            time.sleep(0.2)
    raise SystemExit(f"no response from {path}")


PROJECT_NAME = "Detector smoke test"


def seed(base: str, token: str) -> tuple[str, str]:
    """A project with a real schema and real frames. Returns `(job id, project id)`."""
    org = api(base, token, "/organizations")[0]
    project = api(base, token, "/projects", {
        "organization_id": org["id"],
        "slug": "detector-smoke-test",
        "name": PROJECT_NAME,
        "description": "Reference photographs with unambiguous objects, "
                       "for checking a model end to end before it sees real data.",
        "labels": LABELS,
    })
    task = api(base, token, "/tasks", {
        "project_id": project["id"], "name": "Batch 01 — reference images",
        "media_kind": "image",
    })
    for name in FRAMES:
        api(base, token, f"/tasks/{task['id']}/assets", files=frame_bytes(name))

    job = api(base, token, f"/tasks/{task['id']}/jobs")[0]
    api(base, token, f"/jobs/{job['id']}/annotations", None)  # warm the version
    return job["id"], project["id"]


# -------------------------------------------------------------------------- screenshots


def find_chromium() -> str | None:
    for candidate in CHROMIUM_CANDIDATES:
        if candidate and Path(candidate).is_file():
            return candidate
    return None


def canvas_point(box: dict, image: tuple[int, int], x: float, y: float) -> tuple[float, float]:
    """Where image pixel `(x, y)` lands on screen, given the canvas' bounding box.

    This mirrors `fitToImage` in `web/src/canvas/viewport.ts` — the editor scales the image
    to fit with a 4% margin and centres it. Duplicating six lines of arithmetic here is
    what lets the boxes below be measured off the photograph once and stay correct whatever
    the window size is; the alternative is fractions of the viewport that quietly slide off
    the objects the moment anything about the layout changes.
    """
    image_width, image_height = image
    scale = min(box["width"] / image_width, box["height"] / image_height) * 0.96
    offset_x = (image_width - box["width"] / scale) / 2
    offset_y = (image_height - box["height"] / scale) / 2
    return box["x"] + (x - offset_x) * scale, box["y"] + (y - offset_y) * scale


def capture(handshake: dict[str, str], job_id: str, project_id: str, headed: bool) -> None:
    from playwright.sync_api import sync_playwright

    OUT.mkdir(parents=True, exist_ok=True)
    base = handshake["url"]
    # Exactly what the desktop shell injects, so these are pictures of the desktop app.
    injection = json.dumps({
        "url": base, "token": handshake["token"], "data_dir": handshake["data_dir"],
        "version": handshake["version"], "desktop": True,
    })

    with sync_playwright() as p:
        launch: dict[str, object] = {"headless": not headed}
        if (chromium := find_chromium()) is not None:
            launch["executable_path"] = chromium
        browser = p.chromium.launch(**launch)
        # Device scale 1, not 2. A 2x capture is sharper for the chrome and *softer* for
        # the photograph: the editor already fits a 600x400 frame to a ~1200px canvas, and
        # doubling that magnifies it to 4x native, which is visibly mushy at full size.
        # 1600x1000 is close to 1:1 for a README on a high-density display anyway.
        page = browser.new_page(viewport={"width": 1600, "height": 1000}, device_scale_factor=1)
        page.add_init_script(f"window.__CURVEVISION__ = {injection};")

        def shot(name: str, trim: bool = False) -> None:
            page.wait_for_timeout(900)
            clip = None
            if trim:
                # Trim to where the content actually ends rather than shipping a picture
                # that is half empty background — measured, not a magic number, because a
                # hard-coded height cuts a card in half the moment the page grows a row.
                bottom = page.evaluate(
                    "() => Math.max(...[...document.querySelectorAll('main *')]"
                    ".map((n) => n.getBoundingClientRect().bottom).filter(Number.isFinite))"
                )
                clip = {"x": 0, "y": 0, "width": 1600,
                        "height": min(1000, max(400, int(bottom) + 24))}
            page.screenshot(path=str(OUT / f"{name}.png"), clip=clip)
            print(f"  docs/images/{name}.png")

        page.goto(f"{base}/jobs/{job_id}", wait_until="networkidle")
        page.wait_for_selector("canvas", timeout=30_000)
        page.wait_for_timeout(1500)

        # Draw with the real tools, through real pointer events, switching label as a
        # person would. Nothing here is composited afterwards.
        canvas = page.locator("canvas").first
        box = canvas.bounding_box()
        if box:
            image = frame_size(FRAMES[0])
            page.keyboard.press("r")  # rectangle tool

            def draw(label: str, x0: float, y0: float, x1: float, y1: float) -> None:
                page.get_by_text(label, exact=True).first.click()
                page.mouse.move(*canvas_point(box, image, x0, y0))
                page.mouse.down()
                page.mouse.move(*canvas_point(box, image, x1, y1), steps=14)
                page.mouse.up()
                page.wait_for_timeout(220)

            for label, x0, y0, x1, y1 in BOXES:
                draw(label, x0, y0, x1, y1)

            # Let autosave settle, so the header reads as saved rather than mid-flight.
            page.get_by_role("button", name="Save").click()
            page.wait_for_timeout(1200)
        shot("editor")

        # The project page last, so its counters describe the work that was just done. The
        # other order photographs an empty project and quietly says the opposite of the
        # editor shot sitting next to it.
        page.goto(f"{base}/projects/{project_id}", wait_until="networkidle")
        page.wait_for_selector(f"text={PROJECT_NAME}", timeout=30_000)
        shot("project", trim=True)

        browser.close()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--headed", action="store_true", help="Show the browser window.")
    args = parser.parse_args()

    data_dir = Path("/tmp/curvevision-screenshots")
    process, handshake = start_server(data_dir)
    print(f"server at {handshake['url']}")
    try:
        job_id, project_id = seed(handshake["url"], handshake["token"])
        print(f"seeded a project, a task and {len(FRAMES)} photographs")
        capture(handshake, job_id, project_id, args.headed)
    finally:
        process.terminate()
        process.wait(timeout=20)
    print("done")
    return 0


if __name__ == "__main__":
    sys.exit(main())
