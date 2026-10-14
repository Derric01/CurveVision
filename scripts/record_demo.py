#!/usr/bin/env python3
"""Record a demo video of the real application, from sign-in to an exported dataset.

    python scripts/record_demo.py                 # docs/demo/curvevision-demo.webm (+ .mp4)
    python scripts/record_demo.py --headed        # watch it happen

Like `scripts/screenshot.py`, and for the same reason: everything in the video is the
actual product doing the actual thing, on real photographs — the server shape with the
built web application, driven through real pointer and keyboard events in Chromium. The
captions are the only addition: a caption bar laid over the page, and a dot that shows
where the pointer is, because a recorded browser does not draw its own.

The project is created and the photographs uploaded through the API before recording —
that part is a progress bar, not a demo — and everything after the sign-in page is done
in the application. The MP4 needs an ``ffmpeg`` (``imageio-ffmpeg`` provides one); the
WebM is Playwright's own recording.

Needs the server package, Playwright with a Chromium, and the web application built
(``npm --prefix web run build``).
"""

from __future__ import annotations

import argparse
import json
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

from screenshot import (  # noqa: E402  (the shared harness helpers)
    BOXES,
    FRAMES,
    LABELS,
    PROJECT_NAME,
    api,
    canvas_point,
    find_chromium,
    frame_bytes,
    frame_size,
)
from verify_workspace import start_web_server  # noqa: E402

REPO = Path(__file__).resolve().parents[1]
OUT = REPO / "docs" / "demo"
WIDTH, HEIGHT = 1280, 800
USERNAME, PASSWORD = "maya", "demo-password-not-real-1"

#: The cat, traced round its head; the astronaut and the shuttle model beside her. In image
#: pixels, measured off the photographs as `screenshot.BOXES` are.
CAT = [(40, 12), (130, 2), (330, 2), (398, 22), (372, 120), (362, 212), (330, 284),
       (250, 298), (120, 298), (22, 280), (10, 120)]
ASTRONAUT = (("person", 20, 15, 365, 510), ("rocket", 352, 0, 470, 292))

#: The caption bar and the pointer dot, added to every page the recording visits.
OVERLAY = """
(() => {
  const install = () => {
    if (document.getElementById('demo-caption')) return;
    const bar = document.createElement('div');
    bar.id = 'demo-caption';
    Object.assign(bar.style, {
      position: 'fixed', left: '50%', bottom: '28px', transform: 'translateX(-50%)',
      maxWidth: '78%', padding: '12px 22px', borderRadius: '12px', zIndex: 2147483647,
      background: 'rgba(15, 23, 42, 0.88)', color: '#f8fafc', textAlign: 'center',
      font: '600 20px/1.35 system-ui, -apple-system, Segoe UI, sans-serif',
      boxShadow: '0 8px 30px rgba(0,0,0,0.35)', transition: 'opacity 250ms',
      opacity: '0', pointerEvents: 'none',
    });
    document.documentElement.appendChild(bar);
    const dot = document.createElement('div');
    dot.id = 'demo-pointer';
    Object.assign(dot.style, {
      position: 'fixed', width: '18px', height: '18px', marginLeft: '-9px',
      marginTop: '-9px', borderRadius: '50%', zIndex: 2147483646, left: '-40px',
      top: '-40px', pointerEvents: 'none', background: 'rgba(250, 204, 21, 0.85)',
      boxShadow: '0 0 0 3px rgba(15, 23, 42, 0.6)', transition: 'transform 80ms',
    });
    document.documentElement.appendChild(dot);
    addEventListener('mousemove', (e) => {
      dot.style.left = e.clientX + 'px'; dot.style.top = e.clientY + 'px';
    }, true);
    addEventListener('mousedown', () => { dot.style.transform = 'scale(0.7)'; }, true);
    addEventListener('mouseup', () => { dot.style.transform = 'scale(1)'; }, true);
  };
  window.__demoCaption = (text) => {
    install();
    const bar = document.getElementById('demo-caption');
    bar.textContent = text || '';
    bar.style.opacity = text ? '1' : '0';
  };
  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', install);
  } else { install(); }
})();
"""


def seed(base: str, token: str) -> tuple[str, str]:
    """The project the demo works in: a schema and four photographs. `(job, project)`."""
    org = api(base, token, "/organizations", {"slug": "vision-lab", "name": "Vision Lab"})
    labels = [*LABELS[:4], {"name": "person", "color": "#22c55e"},
              {"name": "rocket", "color": "#ec4899"}]
    project = api(base, token, "/projects", {
        "organization_id": org["id"],
        "slug": "detector-smoke-test",
        "name": PROJECT_NAME,
        "description": "Reference photographs with unambiguous objects, "
                       "for checking a model end to end before it sees real data.",
        "labels": [{k: v for k, v in label.items() if k != "allowed_shape_types"}
                   for label in labels],
    })
    task = api(base, token, "/tasks", {
        "project_id": project["id"], "name": "Batch 01 — reference images",
        "media_kind": "image",
    })
    for name in FRAMES:
        api(base, token, f"/tasks/{task['id']}/assets", files=frame_bytes(name),
            filename=f"{name}.jpg")
    job = api(base, token, f"/tasks/{task['id']}/jobs")[0]
    return job["id"], project["id"]


def record(base: str, job_id: str, project_id: str, workdir: Path, headed: bool) -> Path:
    from playwright.sync_api import sync_playwright

    with sync_playwright() as p:
        launch: dict[str, object] = {"headless": not headed}
        if (chromium := find_chromium()) is not None:
            launch["executable_path"] = chromium
        browser = p.chromium.launch(**launch)
        context = browser.new_context(
            viewport={"width": WIDTH, "height": HEIGHT},
            record_video_dir=str(workdir / "video"),
            record_video_size={"width": WIDTH, "height": HEIGHT},
            accept_downloads=True,
        )
        context.add_init_script(OVERLAY)
        page = context.new_page()

        def say(text: str, hold_ms: int = 0) -> None:
            page.evaluate("(t) => window.__demoCaption && window.__demoCaption(t)", text)
            if hold_ms:
                page.wait_for_timeout(hold_ms)

        def glide(x: float, y: float, steps: int = 18) -> None:
            page.mouse.move(x, y, steps=steps)

        def click(locator) -> None:
            box = locator.bounding_box()
            if box:
                glide(box["x"] + box["width"] / 2, box["y"] + box["height"] / 2)
                page.wait_for_timeout(150)
            locator.click()

        # ---------------------------------------------------------------- sign in
        page.goto(f"{base}/login", wait_until="networkidle")
        page.wait_for_timeout(400)
        say("CurveVision — open-source annotation for computer vision", 2600)
        say("One codebase, two shapes: a desktop app, or a server your team shares", 2600)
        say("This is the server, in a browser. Sign in…", 600)
        click(page.locator("input[name=identifier]"))
        page.keyboard.type(USERNAME, delay=90)
        click(page.locator("input[name=password]"))
        page.keyboard.type(PASSWORD, delay=45)
        click(page.get_by_role("button", name="Sign in"))
        page.wait_for_load_state("networkidle")
        page.wait_for_timeout(1200)

        # ---------------------------------------------------------------- project
        say("A project: its label schema, its tasks, and its progress", 600)
        click(page.get_by_text(PROJECT_NAME, exact=True).first)
        page.wait_for_load_state("networkidle")
        page.wait_for_timeout(2600)

        # ---------------------------------------------------------------- editor
        page.goto(f"{base}/jobs/{job_id}", wait_until="networkidle")
        page.wait_for_selector("canvas", timeout=30_000)
        page.wait_for_timeout(1200)
        say("The editor. Real photographs, real tools", 2000)
        canvas = page.locator("canvas").first
        box = canvas.bounding_box()
        assert box is not None

        say("R for the box tool — pick a label, drag")
        page.keyboard.press("r")
        image = frame_size(FRAMES[0])
        for label, x0, y0, x1, y1 in BOXES:
            click(page.get_by_text(label, exact=True).first)
            glide(*canvas_point(box, image, x0, y0))
            page.mouse.down()
            page.mouse.move(*canvas_point(box, image, x1, y1), steps=26)
            page.mouse.up()
            page.wait_for_timeout(500)
        page.wait_for_timeout(1200)

        say("→ to the next frame. P for polygons: click round it, Enter to close")
        page.keyboard.press("ArrowRight")
        page.wait_for_timeout(1400)
        box = canvas.bounding_box() or box
        image = frame_size(FRAMES[1])
        click(page.get_by_text("cat", exact=True).first)
        page.keyboard.press("p")
        for x, y in CAT:
            glide(*canvas_point(box, image, x, y), steps=10)
            page.mouse.click(*canvas_point(box, image, x, y))
            page.wait_for_timeout(120)
        page.keyboard.press("Enter")
        page.wait_for_timeout(1600)

        say("Every frame keeps its own work; autosave keeps all of it")
        page.keyboard.press("ArrowRight")
        page.wait_for_timeout(1400)
        box = canvas.bounding_box() or box
        image = frame_size(FRAMES[2])
        page.keyboard.press("r")
        for label, x0, y0, x1, y1 in ASTRONAUT:
            click(page.get_by_text(label, exact=True).first)
            glide(*canvas_point(box, image, x0, y0))
            page.mouse.down()
            page.mouse.move(*canvas_point(box, image, x1, y1), steps=26)
            page.mouse.up()
            page.wait_for_timeout(500)
        page.wait_for_timeout(800)
        save = page.get_by_text("Save", exact=True)
        if save.is_enabled():
            click(save)
        page.wait_for_timeout(1800)

        # ---------------------------------------------------------------- export
        page.goto(f"{base}/projects/{project_id}", wait_until="networkidle")
        page.wait_for_timeout(800)
        say("Export to fifteen formats — each one checked in CI by the tool that reads it", 600)
        select = page.locator("#export-format")
        select.scroll_into_view_if_needed()
        page.wait_for_timeout(600)
        click(select)
        page.wait_for_timeout(300)
        options = select.locator("option")
        for index in range(options.count()):
            value = options.nth(index).get_attribute("value") or ""
            select.select_option(value)
            page.wait_for_timeout(260)
        select.select_option("coco")
        page.wait_for_timeout(700)
        say("COCO, YOLO, Pascal VOC, CVAT XML, LabelMe, Cityscapes, Open Images, TFRecord…", 400)
        with page.expect_download(timeout=60_000) as download:
            click(page.locator("[data-export-run]"))
        saved = workdir / "export.zip"
        download.value.save_as(saved)
        page.wait_for_timeout(1800)
        say("Open source, MIT. Your data stays where you put it.", 2800)
        say("CurveVision", 1800)

        video = page.video
        context.close()
        browser.close()
        assert video is not None
        recorded = Path(video.path())

    # The export the video shows is a real one: check it holds what was drawn.
    import zipfile

    with zipfile.ZipFile(saved) as archive:
        document = json.loads(archive.read(next(n for n in archive.namelist()
                                                if n.endswith(".json"))))
    drawn = len(BOXES) + 1 + len(ASTRONAUT)
    print(f"  the exported COCO file holds {len(document['annotations'])} annotations "
          f"({drawn} were drawn)")
    if len(document["annotations"]) != drawn:
        raise SystemExit("the export does not hold what the video drew")
    return recorded


def to_mp4(webm: Path, mp4: Path) -> bool:
    ffmpeg = shutil.which("ffmpeg")
    if ffmpeg is None:
        try:
            import imageio_ffmpeg

            ffmpeg = imageio_ffmpeg.get_ffmpeg_exe()
        except ImportError:
            return False
    subprocess.run(
        [ffmpeg, "-y", "-loglevel", "error", "-i", str(webm), "-c:v", "libx264",
         "-pix_fmt", "yuv420p", "-crf", "20", "-movflags", "+faststart", str(mp4)],
        check=True,
    )
    return True


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--headed", action="store_true")
    args = parser.parse_args()

    OUT.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="curvevision-demo-") as scratch:
        workdir = Path(scratch)
        server, base = start_web_server(workdir)
        try:
            from verify_workspace import anonymous

            anonymous(base, "/auth/register", {
                "username": USERNAME, "email": "maya@example.com", "password": PASSWORD,
                "full_name": "Maya Okafor",
            })
            token = anonymous(base, "/auth/login",
                              {"identifier": USERNAME, "password": PASSWORD})["access_token"]
            job_id, project_id = seed(base, token)
            recorded = record(base, job_id, project_id, workdir, args.headed)
            webm = OUT / "curvevision-demo.webm"
            shutil.copyfile(recorded, webm)
            print(f"  {webm.relative_to(REPO)}")
            mp4 = OUT / "curvevision-demo.mp4"
            if to_mp4(webm, mp4):
                print(f"  {mp4.relative_to(REPO)}")
            else:
                print("  (no ffmpeg: only the WebM was written)")
        finally:
            server.terminate()
            server.wait(timeout=20)
    return 0


if __name__ == "__main__":
    sys.exit(main())
