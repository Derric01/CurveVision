#!/usr/bin/env python3
"""Drive the real application in a real browser and photograph it.

Every image in the README is produced by this script, from the actual product, with
sample data generated here. Nothing is mocked up, drawn by hand, or touched afterwards —
a screenshot that flatters software into looking like something it is not is a lie with a
long tail, and this one has to survive somebody downloading the app.

    python scripts/screenshot.py                    # writes docs/images/*.png
    python scripts/screenshot.py --headed           # watch it happen

It needs the packaged server (`python desktop/sidecar/build.py`) and a Chromium that
Playwright can drive. Set `CURVEVISION_CHROMIUM` if the bundled one is not found.
"""

from __future__ import annotations

import argparse
import io
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


def sample_frame(index: int, width: int = 1280, height: int = 800) -> bytes:
    """A synthetic street-ish scene: enough structure that boxes on it read as annotation.

    Synthetic on purpose. Shipping photographs in a repository means shipping somebody's
    copyright and, often, somebody's face.
    """
    from PIL import Image, ImageDraw

    image = Image.new("RGB", (width, height), (0, 0, 0))
    draw = ImageDraw.Draw(image)

    horizon = int(height * 0.52)
    for y in range(horizon):
        t = y / horizon
        draw.line([(0, y), (width, y)], fill=(int(38 + 90 * t), int(52 + 96 * t), int(78 + 92 * t)))
    for y in range(horizon, height):
        t = (y - horizon) / (height - horizon)
        draw.line([(0, y), (width, y)], fill=(int(52 - 14 * t), int(54 - 14 * t), int(60 - 16 * t)))

    # Lane markings, receding.
    for step in range(7):
        t = step / 7
        y = horizon + int((height - horizon) * (t**1.7)) + 30
        half = int(14 + 90 * t)
        draw.rectangle([width // 2 - half // 8, y, width // 2 + half // 8, y + int(8 + 26 * t)],
                       fill=(196, 190, 170))

    drift = index * 26
    # Vehicles.
    for x, y, w, h, colour in (
        (180 + drift, horizon - 26, 250, 150, (176, 74, 68)),
        (700 - drift // 2, horizon - 10, 190, 112, (66, 96, 150)),
        (1010, horizon - 46, 120, 74, (188, 162, 92)),
    ):
        draw.rounded_rectangle([x, y, x + w, y + h], radius=14, fill=colour)
        draw.rounded_rectangle([x + w * 0.16, y + 8, x + w * 0.84, y + h * 0.46], radius=8,
                               fill=(28, 34, 44))
        draw.ellipse([x + w * 0.1, y + h * 0.78, x + w * 0.3, y + h * 1.02], fill=(24, 24, 28))
        draw.ellipse([x + w * 0.7, y + h * 0.78, x + w * 0.9, y + h * 1.02], fill=(24, 24, 28))

    # People.
    for x, y, scale in ((520 + drift // 3, horizon - 6, 1.0), (930, horizon + 40, 1.35)):
        head, body = int(16 * scale), int(70 * scale)
        draw.ellipse([x, y, x + head * 2, y + head * 2], fill=(224, 196, 168))
        draw.rounded_rectangle([x - 4, y + head * 2, x + head * 2 + 4, y + head * 2 + body],
                               radius=10, fill=(72, 112, 104))

    buffer = io.BytesIO()
    image.save(buffer, format="JPEG", quality=90)
    return buffer.getvalue()


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


def seed(base: str, token: str) -> str:
    """A project with a real label schema and a few annotated frames. Returns the job id."""
    org = api(base, token, "/organizations")[0]
    project = api(base, token, "/projects", {
        "organization_id": org["id"],
        "slug": "street-scenes",
        "name": "Street Scenes",
        "description": "Urban footage for a detection model.",
        "labels": [
            {"name": "car", "color": "#ef4444", "allowed_shape_types": ["rectangle"]},
            {"name": "pedestrian", "color": "#22c55e", "allowed_shape_types": ["rectangle"]},
            {"name": "lane", "color": "#f59e0b", "allowed_shape_types": ["polyline"]},
            {"name": "sign", "color": "#38bdf8", "allowed_shape_types": ["rectangle"]},
        ],
    })
    task = api(base, token, "/tasks", {
        "project_id": project["id"], "name": "Batch 01 — daylight", "media_kind": "image",
    })
    for index in range(6):
        api(base, token, f"/tasks/{task['id']}/assets", files=sample_frame(index))

    job = api(base, token, f"/tasks/{task['id']}/jobs")[0]
    labels = {label["name"]: label["id"] for label in project["labels"]}

    # Pre-draw a couple of frames so the editor is photographed doing its job, not empty.
    api(base, token, f"/jobs/{job['id']}/annotations", None)  # warm the version
    return job["id"], project["id"], task["id"], labels


# -------------------------------------------------------------------------- screenshots


def find_chromium() -> str | None:
    for candidate in CHROMIUM_CANDIDATES:
        if candidate and Path(candidate).is_file():
            return candidate
    return None


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
        page = browser.new_page(viewport={"width": 1600, "height": 1000}, device_scale_factor=2)
        page.add_init_script(f"window.__CURVEVISION__ = {injection};")

        def shot(name: str, height: int | None = None) -> None:
            page.wait_for_timeout(900)
            # Trim the viewport to the content rather than shipping a screenshot that is
            # mostly empty background: a picture should show the product, not the padding.
            clip = {"x": 0, "y": 0, "width": 1600, "height": height} if height else None
            page.screenshot(path=str(OUT / f"{name}.png"), clip=clip)
            print(f"  docs/images/{name}.png")

        page.goto(f"{base}/projects/{project_id}", wait_until="networkidle")
        page.wait_for_selector("text=Street Scenes", timeout=30_000)
        shot("project", height=770)

        page.goto(f"{base}/jobs/{job_id}", wait_until="networkidle")
        page.wait_for_selector("canvas", timeout=30_000)
        page.wait_for_timeout(1500)

        # Draw with the real tools, through real pointer events, switching label as a
        # person would. Nothing here is composited afterwards.
        canvas = page.locator("canvas").first
        box = canvas.bounding_box()
        if box:
            page.keyboard.press("r")  # rectangle tool

            def draw(label: str, x0: float, y0: float, x1: float, y1: float) -> None:
                page.get_by_text(label, exact=True).first.click()
                page.mouse.move(box["x"] + box["width"] * x0, box["y"] + box["height"] * y0)
                page.mouse.down()
                page.mouse.move(
                    box["x"] + box["width"] * x1, box["y"] + box["height"] * y1, steps=14
                )
                page.mouse.up()
                page.wait_for_timeout(220)

            draw("car", 0.165, 0.455, 0.355, 0.685)
            draw("car", 0.555, 0.475, 0.700, 0.640)
            draw("car", 0.790, 0.435, 0.885, 0.545)
            draw("pedestrian", 0.400, 0.500, 0.445, 0.625)
            draw("pedestrian", 0.665, 0.545, 0.715, 0.700)

            # Let autosave settle, so the header reads as saved rather than mid-flight.
            page.get_by_role("button", name="Save").click()
            page.wait_for_timeout(1200)
        shot("editor")

        browser.close()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--headed", action="store_true", help="Show the browser window.")
    args = parser.parse_args()

    data_dir = Path("/tmp/curvevision-screenshots")
    process, handshake = start_server(data_dir)
    print(f"server at {handshake['url']}")
    try:
        job_id, project_id, _task_id, _labels = seed(handshake["url"], handshake["token"])
        print("seeded a project, a task and six frames")
        capture(handshake, job_id, project_id, args.headed)
    finally:
        process.terminate()
        process.wait(timeout=20)
    print("done")
    return 0


if __name__ == "__main__":
    sys.exit(main())
