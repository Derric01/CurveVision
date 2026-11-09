#!/usr/bin/env python3
"""One whole project, through the real interface: draw, review, export, import, compare.

    python scripts/verify_end_to_end.py

Every other harness proves one part. This walks the path a team takes, in Chromium against
the real server, and compares what comes out at the end with what went in:

1. an annotator draws a box on frame 1, a polygon on frame 2 and a box on frame 3, and
   relabels that last box from the object panel; all of it is saved;
2. they submit the job, and a reviewer accepts it;
3. on the project page, COCO with its images and *accepted only* is exported, and the
   browser downloads it (an export with images runs in the background and is fetched by
   a signed link, which the browser's own download manager follows);
4. the archive holds every image, byte for byte;
5. imported into a fresh task over the same images, it gives back every shape: the same
   labels, the same geometry, on the same frames.

Needs the built web application (`npm --prefix web run build`) and a Chromium.
"""

from __future__ import annotations

import io
import json
import sys
import tempfile
import urllib.request
import uuid
import zipfile
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).parent))

from screenshot import api, find_chromium
from verify_workspace import PASSWORD, anonymous, start_web_server

WIDTH, HEIGHT = 480, 320


def png_bytes(index: int) -> bytes:
    from PIL import Image

    buffer = io.BytesIO()
    Image.new("RGB", (WIDTH, HEIGHT), (40 + 50 * index, 50, 70)).save(buffer, format="PNG")
    return buffer.getvalue()


def import_archive(base: str, token: str, task_id: str, archive: bytes) -> dict[str, Any]:
    boundary = uuid.uuid4().hex
    body = (
        f"--{boundary}\r\nContent-Disposition: form-data; name=\"file\"; "
        f'filename="export.zip"\r\nContent-Type: application/zip\r\n\r\n'
    ).encode() + archive + f"\r\n--{boundary}--\r\n".encode()
    request = urllib.request.Request(
        f"{base}/api/v1/tasks/{task_id}/import?format=coco&conflict_policy=replace",
        data=body,
        method="POST",
        headers={
            "Authorization": f"Bearer {token}",
            "Content-Type": f"multipart/form-data; boundary={boundary}",
        },
    )
    with urllib.request.urlopen(request, timeout=60) as response:
        return dict(json.loads(response.read()))


def main() -> int:
    from playwright.sync_api import sync_playwright

    failures: list[str] = []

    def check(condition: bool, passed: str, failed: str) -> None:
        if condition:
            print(f"  ok   {passed}")
        else:
            failures.append(failed)
            print(f"  FAIL {failed}")

    with tempfile.TemporaryDirectory(prefix="curvevision-e2e-") as workdir:
        server, base = start_web_server(Path(workdir))
        try:
            anonymous(
                base,
                "/auth/register",
                {"username": "noor", "email": "noor@example.com", "password": PASSWORD},
            )
            session = anonymous(base, "/auth/login", {"identifier": "noor", "password": PASSWORD})
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
            images = [png_bytes(index) for index in range(3)]

            def new_task(name: str) -> dict[str, Any]:
                task = api(
                    base,
                    token,
                    "/tasks",
                    {"project_id": project["id"], "name": name, "media_kind": "image"},
                )
                for index, data in enumerate(images):
                    api(
                        base,
                        token,
                        f"/tasks/{task['id']}/assets",
                        files=data,
                        filename=f"frame_{index}.png",
                        content_type="image/png",
                    )
                return dict(task)

            task = new_task("Morning")
            job = api(base, token, f"/tasks/{task['id']}/jobs")[0]

            def shapes_of(job_id: str) -> list[tuple[int, str, str, list[float]]]:
                stored = api(base, token, f"/jobs/{job_id}/annotations")["shapes"]
                return sorted(
                    (
                        s["frame"],
                        label_name[s["label_id"]],
                        s["shape_type"],
                        [round(v, 2) for v in s["points"]],
                    )
                    for s in stored
                )

            with sync_playwright() as p:
                launch: dict[str, object] = {"headless": True}
                if (chromium := find_chromium()) is not None:
                    launch["executable_path"] = chromium
                browser = p.chromium.launch(**launch)
                context = browser.new_context(
                    viewport={"width": 1280, "height": 900}, accept_downloads=True
                )
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

                def at(x: float, y: float) -> tuple[float, float]:
                    scale = min(canvas["width"] / WIDTH, canvas["height"] / HEIGHT)
                    left = canvas["x"] + (canvas["width"] - WIDTH * scale) / 2
                    top = canvas["y"] + (canvas["height"] - HEIGHT * scale) / 2
                    return left + x * scale, top + y * scale

                def box(x1: float, y1: float, x2: float, y2: float) -> None:
                    page.get_by_title("Rectangle (R)").click()
                    page.mouse.move(*at(x1, y1))
                    page.mouse.down()
                    page.mouse.move(*at(x2, y2), steps=8)
                    page.mouse.up()

                def save() -> None:
                    button = page.locator("button", has_text="Save").first
                    if button.is_enabled():
                        button.click()
                    page.wait_for_timeout(1500)

                # ------------------------------------------------------------ 1
                box(60, 60, 200, 160)
                save()
                page.keyboard.press("ArrowRight")
                page.wait_for_timeout(800)
                page.get_by_title("Polygon (P)").click()
                for x, y in ((100, 100), (300, 90), (280, 240), (120, 250)):
                    page.mouse.click(*at(x, y))
                page.keyboard.press("Enter")
                save()
                page.keyboard.press("ArrowRight")
                page.wait_for_timeout(800)
                box(250, 150, 420, 300)
                page.get_by_title("Select (V)").click()
                page.mouse.click(*at(330, 220))
                page.locator("[data-relabel]").select_option(label="bus")
                save()
                drawn = shapes_of(job["id"])
                check(
                    [(f, label, kind) for f, label, kind, _ in drawn]
                    == [(0, "car", "rectangle"), (1, "car", "polygon"), (2, "bus", "rectangle")],
                    "a box, a polygon and a relabelled box are saved on frames 1, 2 and 3",
                    f"saved {[(f, label, kind) for f, label, kind, _ in drawn]}",
                )

                # ------------------------------------------------------------ 2
                page.get_by_role("button", name="Submit").click()
                page.wait_for_selector("[data-review-accept]", timeout=20_000)
                page.locator("[data-review-accept]").click()
                page.wait_for_selector("[data-review-outcome]", timeout=20_000)
                state = api(base, token, f"/jobs/{job['id']}")["state"]
                check(state == "accepted", "submitted and accepted", f"the job is {state!r}")

                # ------------------------------------------------------------ 3
                page.goto(f"{base}/projects/{project['id']}", wait_until="networkidle")
                page.wait_for_selector("[data-export-run]")
                page.locator("#export-format").select_option("coco")
                page.locator("[data-export-images]").check()
                page.locator("[data-export-accepted]").check()
                with page.expect_download(timeout=60_000) as pending:
                    page.locator("[data-export-run]").click()
                download = pending.value
                archive_path = Path(workdir) / "export.zip"
                download.save_as(archive_path)
                archive = archive_path.read_bytes()
                check(
                    zipfile.is_zipfile(archive_path)
                    and download.suggested_filename == "street-coco.zip",
                    "the browser downloads the export, under the project's name",
                    f"downloaded {download.suggested_filename!r}, "
                    f"a zip: {zipfile.is_zipfile(archive_path)}",
                )

                # ------------------------------------------------------------ 4
                with zipfile.ZipFile(archive_path) as opened:
                    stored = sorted(
                        opened.read(name) for name in opened.namelist() if name.endswith(".png")
                    )
                check(
                    stored == sorted(images),
                    "the archive holds every image, byte for byte",
                    f"{len(stored)} images in the archive, of {len(images)}",
                )

                # ------------------------------------------------------------ 5
                fresh = new_task("Re-imported")
                result = import_archive(base, token, fresh["id"], archive)
                fresh_job = api(base, token, f"/tasks/{fresh['id']}/jobs")[0]
                came_back = shapes_of(fresh_job["id"])
                check(
                    came_back == drawn,
                    "imported into a fresh task, every shape comes back as it was drawn",
                    f"drew {drawn}; got back {came_back}; import said {result}",
                )

                check(not raised, "no page errors", f"page errors: {raised}")
                browser.close()
        finally:
            server.terminate()

    print("PASS" if not failures else f"FAIL ({len(failures)})")
    return 0 if not failures else 1


if __name__ == "__main__":
    raise SystemExit(main())
