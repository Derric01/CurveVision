#!/usr/bin/env python3
"""Record the demo video: the real application, a real model, real photographs, in 1080p.

    python scripts/record_demo.py                # docs/demo/curvevision-demo.mp4

Everything on screen is the actual product doing the actual thing. The server shape runs
with the built web application; Chromium runs in a window on a virtual display; the
screen is captured with ffmpeg at 30 frames a second. The story it tells:

1. sign in, and open a project;
2. run a **real detector**, YOLO11n through `scripts/demo_detector.py`, over the whole
   job. Its boxes arrive as suggestions, and are accepted or rejected one by one;
3. draw what the model missed (it has no "saucer" class, and finds no rocket), trace a
   polygon, and tag a whole frame;
4. export the dataset as COCO, which is then opened and checked against what was drawn.

What the recording adds is only what a screen recording of a person would show: a pointer
(synthetic input moves no real one), the keys pressed, captions, and a title and closing
card drawn over the page.

Needs a display server (`Xvfb`) and `ffmpeg`, the web application built (`npm --prefix web
run build`), Playwright with a Chromium, and the detector's own needs: `pip install
onnxruntime` (the model downloads itself; see `demo_detector.py`).
"""

from __future__ import annotations

import argparse
import json
import math
import os
import shutil
import subprocess
import sys
import tempfile
import threading
import time
import zipfile
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).parent))

from screenshot import api, canvas_point, find_chromium, frame_bytes, frame_size
from verify_workspace import anonymous, start_web_server

REPO = Path(__file__).resolve().parents[1]
OUT = REPO / "docs" / "demo"
#: Captured pixels, and the page's own CSS pixels: the UI at 125%, as on a laptop.
SCREEN = (1920, 1080)
SCALE = 1.25
DISPLAY = ":97"
USERNAME, PASSWORD = "maya", "demo-password-not-real-1"
PROJECT = "Product photos — detector check"
FRAMES = ("coffee", "cat", "astronaut", "rocket")
LABELS = [
    {"name": "cup", "color": "#ef4444"},
    {"name": "saucer", "color": "#f59e0b"},
    {"name": "spoon", "color": "#38bdf8"},
    {"name": "cat", "color": "#a855f7"},
    {"name": "person", "color": "#22c55e"},
    {"name": "rocket", "color": "#ec4899"},
    {"name": "outdoor", "color": "#14b8a6"},
]
#: What the model misses, drawn by hand, in image pixels measured off the photographs.
SAUCER = (84, 99, 481, 376)
SPOON = (324, 57, 426, 325)
ROCKET = (305, 124, 340, 408)
CAT = [
    (40, 12),
    (130, 2),
    (330, 2),
    (398, 22),
    (372, 120),
    (362, 212),
    (330, 284),
    (250, 298),
    (120, 298),
    (22, 280),
    (10, 120),
]

OVERLAY = """
(() => {
  const css = `
    #demo-caption { position: fixed; left: 50%; bottom: 30px; transform: translate(-50%, 12px);
      max-width: 74%; padding: 14px 26px; border-radius: 14px; z-index: 2147483600;
      background: rgba(2, 6, 23, 0.9); color: #f8fafc; text-align: center;
      font: 600 22px/1.35 Inter, system-ui, sans-serif; letter-spacing: -0.01em;
      box-shadow: 0 12px 40px rgba(0,0,0,0.45); border: 1px solid rgba(45, 212, 191, 0.35);
      opacity: 0; transition: opacity 280ms, transform 280ms; pointer-events: none; }
    #demo-caption.on { opacity: 1; transform: translate(-50%, 0); }
    #demo-caption small { display: block; margin-top: 4px; font-weight: 500;
      font-size: 15px; color: #94a3b8; }
    #demo-key { position: fixed; right: 28px; bottom: 32px; z-index: 2147483601;
      min-width: 54px; padding: 10px 16px; border-radius: 10px; text-align: center;
      background: #f8fafc; color: #0f172a; font: 700 22px/1 ui-monospace, monospace;
      box-shadow: 0 4px 0 #94a3b8, 0 10px 30px rgba(0,0,0,0.4);
      opacity: 0; transform: scale(0.8); transition: opacity 160ms, transform 160ms;
      pointer-events: none; }
    #demo-key.on { opacity: 1; transform: scale(1); }
    #demo-pointer { position: fixed; left: -60px; top: -60px; width: 26px; height: 26px;
      z-index: 2147483602; pointer-events: none; transition: transform 90ms; }
    #demo-pointer.down { transform: scale(0.86); }
    .demo-ripple { position: fixed; width: 44px; height: 44px; margin: -22px 0 0 -22px;
      border-radius: 50%; border: 3px solid rgba(45, 212, 191, 0.9); z-index: 2147483599;
      pointer-events: none; animation: demo-ripple 520ms ease-out forwards; }
    @keyframes demo-ripple { from { transform: scale(0.3); opacity: 1 }
                             to { transform: scale(1.4); opacity: 0 } }
    #demo-card { position: fixed; inset: 0; z-index: 2147483640; display: flex;
      flex-direction: column; align-items: center; justify-content: center; gap: 18px;
      background: radial-gradient(1200px 600px at 50% 30%, #134e4a 0%, #020617 70%);
      color: #f8fafc; font-family: Inter, system-ui, sans-serif; text-align: center;
      opacity: 0; transition: opacity 450ms; pointer-events: none; }
    #demo-card.on { opacity: 1; }
    #demo-card img { width: 92px; height: 92px; }
    #demo-card h1 { margin: 0; font-size: 68px; font-weight: 800; letter-spacing: -0.03em; }
    #demo-card p { margin: 0; max-width: 900px; font-size: 27px; line-height: 1.4;
      color: #cbd5e1; }
    #demo-card .chips { display: flex; flex-wrap: wrap; gap: 12px; justify-content: center;
      margin-top: 10px; max-width: 1100px; }
    #demo-card .chips span { padding: 10px 18px; border-radius: 999px; font-size: 19px;
      background: rgba(20, 184, 166, 0.14); border: 1px solid rgba(45, 212, 191, 0.4);
      color: #ccfbf1; }
    #demo-card .url { margin-top: 14px; font: 600 24px ui-monospace, monospace;
      color: #5eead4; }
  `;
  const install = () => {
    if (document.getElementById('demo-caption')) return;
    const style = document.createElement('style');
    style.textContent = css;
    document.documentElement.appendChild(style);
    for (const id of ['demo-caption', 'demo-key', 'demo-card']) {
      const node = document.createElement('div');
      node.id = id;
      document.documentElement.appendChild(node);
    }
    const pointer = document.createElement('div');
    pointer.id = 'demo-pointer';
    pointer.innerHTML = '<svg viewBox="0 0 24 24" width="26" height="26">' +
      '<path d="M3 2 L3 19 L8 14.5 L11.5 22 L14.5 20.6 L11 13.2 L17.5 13.2 Z" ' +
      'fill="#ffffff" stroke="#0f172a" stroke-width="1.6" stroke-linejoin="round"/></svg>';
    document.documentElement.appendChild(pointer);
    const at = window.__demoAt;
    if (at) { pointer.style.left = at[0] + 'px'; pointer.style.top = at[1] + 'px'; }
    addEventListener('mousemove', (e) => {
      pointer.style.left = e.clientX + 'px'; pointer.style.top = e.clientY + 'px';
      window.__demoAt = [e.clientX, e.clientY];
    }, true);
    addEventListener('mousedown', (e) => {
      pointer.classList.add('down');
      const ring = document.createElement('div');
      ring.className = 'demo-ripple';
      ring.style.left = e.clientX + 'px'; ring.style.top = e.clientY + 'px';
      document.documentElement.appendChild(ring);
      setTimeout(() => ring.remove(), 600);
    }, true);
    addEventListener('mouseup', () => pointer.classList.remove('down'), true);
    let keyTimer = 0;
    addEventListener('keydown', (e) => {
      if (!window.__demoShowKeys) return;
      const names = { ArrowRight: '→', ArrowLeft: '←', Enter: 'Enter ↵', Escape: 'Esc' };
      const key = document.getElementById('demo-key');
      key.textContent = names[e.key] || e.key.toUpperCase();
      key.classList.add('on');
      clearTimeout(keyTimer);
      keyTimer = setTimeout(() => key.classList.remove('on'), 1100);
    }, true);
  };
  window.__demoCaption = (text, note) => {
    install();
    const bar = document.getElementById('demo-caption');
    bar.innerHTML = '';
    if (text) {
      bar.append(text);
      if (note) {
        const small = document.createElement('small');
        small.append(note);
        bar.append(small);
      }
    }
    bar.classList.toggle('on', Boolean(text));
  };
  window.__demoCard = (card) => {
    install();
    const node = document.getElementById('demo-card');
    if (!card) { node.classList.remove('on'); return; }
    node.innerHTML = '';
    const logo = document.createElement('img'); logo.src = '/favicon.svg';
    const title = document.createElement('h1'); title.textContent = card.title;
    const line = document.createElement('p'); line.textContent = card.line;
    const chips = document.createElement('div'); chips.className = 'chips';
    for (const chip of card.chips || []) {
      const span = document.createElement('span'); span.textContent = chip; chips.append(span);
    }
    node.append(logo, title, line, chips);
    if (card.url) { const url = document.createElement('div'); url.className = 'url';
      url.textContent = card.url; node.append(url); }
    node.classList.add('on');
  };
  if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', install);
  else install();
})();
"""

INTRO = {
    "title": "CurveVision",
    "line": "Label images and video for computer vision. "
    "Let a model take the first pass, and keep every decision yours.",
    "chips": [
        "Free and open source (MIT)",
        "Desktop app or team server",
        "Your images stay on your machines",
    ],
}
OUTRO = {
    "title": "Try it free",
    "line": "Windows · macOS (Apple Silicon and Intel) · Linux. "
    "Or run it for your whole team with Docker or Kubernetes.",
    "chips": [
        "15+ export formats",
        "Boxes, polygons, masks, skeletons, tracks",
        "Bring any model: YOLO, SAM, or an API key",
        "Review and quality reports",
    ],
    "url": "github.com/Derric01/CurveVision/releases/latest",
}


def seed(base: str, token: str, detector_url: str, classes: list[str]) -> tuple[str, str]:
    org = api(base, token, "/organizations", {"slug": "vision-lab", "name": "Vision Lab"})
    project = api(
        base,
        token,
        "/projects",
        {
            "organization_id": org["id"],
            "slug": "product-photos",
            "name": PROJECT,
            "description": "Photographs a new detector is checked against before it sees "
            "customer data.",
            "labels": LABELS,
        },
    )
    task = api(
        base,
        token,
        "/tasks",
        {
            "project_id": project["id"],
            "name": "Batch 01",
            "media_kind": "image",
        },
    )
    for name in FRAMES:
        api(
            base,
            token,
            f"/tasks/{task['id']}/assets",
            files=frame_bytes(name),
            filename=f"{name}.jpg",
        )
    api(
        base,
        token,
        f"/models?organization_id={org['id']}",
        {
            "slug": "yolo11n",
            "name": "YOLO11n — 80 everyday objects",
            "kind": "detector",
            "provider": "http",
            "config": {"endpoint": detector_url},
            "output_labels": classes,
        },
    )
    job = api(base, token, f"/tasks/{task['id']}/jobs")[0]
    return job["id"], project["id"]


class Recorder:
    """ffmpeg capturing the virtual screen, until told to stop."""

    def __init__(self, path: Path) -> None:
        self.process = subprocess.Popen(
            [
                "ffmpeg",
                "-y",
                "-loglevel",
                "error",
                "-f",
                "x11grab",
                "-draw_mouse",
                "0",
                "-framerate",
                "30",
                "-video_size",
                f"{SCREEN[0]}x{SCREEN[1]}",
                "-i",
                DISPLAY,
                "-c:v",
                "libx264",
                "-preset",
                "ultrafast",
                "-qp",
                "0",
                str(path),
            ],
            stdin=subprocess.PIPE,
        )
        self.started = time.monotonic()
        #: Seconds into the recording when Chrome's own download animation begins.
        self.download_at: float | None = None

    def stop(self) -> None:
        assert self.process.stdin is not None
        self.process.stdin.write(b"q")
        self.process.stdin.flush()
        self.process.wait(timeout=120)


def record(base: str, job_id: str, project_id: str, raw: Path, workdir: Path) -> tuple[Path, float]:
    from playwright.sync_api import sync_playwright

    width, height = round(SCREEN[0] / SCALE), round(SCREEN[1] / SCALE)
    # A fresh profile with the password manager off: its "Save password?" bubble is
    # Chrome's, not the application's, and would sit over the editor for the whole video.
    preferences = workdir / "profile" / "Default" / "Preferences"
    preferences.parent.mkdir(parents=True, exist_ok=True)
    preferences.write_text(
        json.dumps(
            {"credentials_enable_service": False, "profile": {"password_manager_enabled": False}}
        )
    )
    with sync_playwright() as p:
        launch: dict[str, Any] = {}
        if (chromium := find_chromium()) is not None:
            launch["executable_path"] = chromium
        context = p.chromium.launch_persistent_context(
            str(workdir / "profile"),
            headless=False,
            no_viewport=True,
            accept_downloads=True,
            env={**os.environ, "DISPLAY": DISPLAY},
            args=[
                "--app=data:text/html,<body style='background:%23020617'>",
                "--window-position=0,0",
                f"--window-size={width},{height}",
                f"--force-device-scale-factor={SCALE}",
                "--disable-infobars",
                "--no-first-run",
                "--disable-features=DownloadBubble,DownloadBubbleV2",
                "--hide-scrollbars",
            ],
            ignore_default_args=["--enable-automation"],
            **launch,
        )
        context.add_init_script(OVERLAY)
        page = context.pages[0]
        at = [width / 2, height / 2]

        def say(text: str, note: str = "", hold: int = 0) -> None:
            page.evaluate("([t, n]) => window.__demoCaption(t, n)", [text, note])
            if hold:
                page.wait_for_timeout(hold)

        def card(content: dict | None, hold: int = 0) -> None:
            page.evaluate("(c) => window.__demoCard(c)", content)
            if hold:
                page.wait_for_timeout(hold)

        def glide(x: float, y: float, ms: int | None = None) -> None:
            """Move the way a hand does: eased, and slower over longer distances."""
            sx, sy = at
            distance = math.hypot(x - sx, y - sy)
            duration = ms if ms is not None else int(min(900, 220 + distance * 0.9))
            steps = max(6, duration // 16)
            for step in range(1, steps + 1):
                t = step / steps
                eased = t * t * (3 - 2 * t)
                page.mouse.move(sx + (x - sx) * eased, sy + (y - sy) * eased)
                time.sleep(duration / steps / 1000)
            at[:] = [x, y]

        def click(locator: Any, pause: int = 180) -> None:
            locator.scroll_into_view_if_needed()
            box = locator.bounding_box()
            if box:
                glide(box["x"] + box["width"] / 2, box["y"] + box["height"] / 2)
                page.wait_for_timeout(pause)
            locator.click()

        def drag(start: tuple[float, float], end: tuple[float, float], ms: int = 650) -> None:
            glide(*start)
            page.mouse.down()
            glide(*end, ms=ms)
            page.mouse.up()

        def key(name: str, hold: int = 350) -> None:
            page.keyboard.press(name)
            page.wait_for_timeout(hold)

        def fit() -> dict:
            """Fit the frame, as a person does before drawing: reviewing a suggestion
            zooms onto it, and that zoom carries over to the next frame."""
            click(page.get_by_title("Fit to frame"))
            page.wait_for_timeout(450)
            fitted = page.locator("canvas").first.bounding_box()
            assert fitted is not None
            return fitted

        def show_keys(on: bool) -> None:
            page.evaluate("(v) => { window.__demoShowKeys = v; }", on)

        page.goto(f"{base}/login", wait_until="networkidle")
        card(INTRO)
        page.wait_for_timeout(600)
        recorder = Recorder(raw)
        page.wait_for_timeout(4200)
        card(None, 600)

        # ------------------------------------------------------------- sign in
        say("Sign in to your team's server", "or install the desktop app: no account needed")
        click(page.locator("input[name=identifier]"))
        page.keyboard.type(USERNAME, delay=70)
        click(page.locator("input[name=password]"))
        page.keyboard.type(PASSWORD, delay=28)
        click(page.get_by_role("button", name="Sign in"))
        page.wait_for_load_state("networkidle")
        page.wait_for_timeout(900)
        say("Projects hold a label schema, tasks, and everyone's progress")
        click(page.get_by_text(PROJECT, exact=True).first)
        page.wait_for_load_state("networkidle")
        page.wait_for_timeout(2400)

        # ------------------------------------------------------------- the model
        page.goto(f"{base}/jobs/{job_id}", wait_until="networkidle")
        page.wait_for_selector("canvas", timeout=30_000)
        page.wait_for_timeout(900)
        say("The editor: real photographs, nothing labelled yet", hold=2200)
        say(
            "Let a real model take the first pass", "YOLO11n, running locally on this machine's CPU"
        )
        panel = page.locator("[data-auto-annotate]")
        panel.scroll_into_view_if_needed()
        page.wait_for_timeout(900)
        click(page.locator("[data-auto-annotate-run]"))
        page.locator("[data-auto-annotate-result]").wait_for(timeout=120_000)
        page.wait_for_timeout(1500)
        say(
            "Its boxes arrive as suggestions, never as finished work",
            "dashed until someone accepts them",
            hold=2600,
        )

        say("Review them one at a time: the panel says how sure the model was")
        click(page.locator("[data-suggestion-next]"))
        page.locator("[data-suggestion-selected]").wait_for(timeout=10_000)
        page.wait_for_timeout(1700)
        click(page.locator("[data-suggestion-accept]"))
        page.wait_for_timeout(1300)

        # ------------------------------------------------------------- by hand
        show_keys(True)
        box = fit()
        image = frame_size(FRAMES[0])
        say("The model has no 'saucer' class, and missed the spoon. Draw them: R, then drag")
        key("r")
        for label, (x0, y0, x1, y1) in (("saucer", SAUCER), ("spoon", SPOON)):
            click(page.locator(f"[data-label-name='{label}']"))
            drag(canvas_point(box, image, x0, y0), canvas_point(box, image, x1, y1))
            page.wait_for_timeout(450)
        page.wait_for_timeout(900)

        say("Next frame. A box is the wrong shape for this one: reject it")
        key("ArrowRight", 1100)
        click(page.locator("[data-suggestion-next]"))
        page.locator("[data-suggestion-selected]").wait_for(timeout=10_000)
        page.wait_for_timeout(1100)
        click(page.locator("[data-suggestion-reject]"))
        page.wait_for_timeout(900)

        say("…and trace the outline instead: P, click around it, Enter")
        box = fit()
        image = frame_size(FRAMES[1])
        click(page.locator("[data-label-name='cat']"))
        key("p")
        for x, y in CAT:
            target = canvas_point(box, image, x, y)
            glide(*target, ms=170)
            page.mouse.click(*target)
            page.wait_for_timeout(70)
        key("Enter", 1300)

        say("Happy with what's left? Accept every remaining suggestion at once")
        key("ArrowRight", 900)
        click(page.locator("[data-suggestions-accept]"))
        page.wait_for_timeout(1700)

        say("The model found nothing here. Box the rocket, and tag the whole frame")
        key("ArrowRight", 900)
        box = fit()
        image = frame_size(FRAMES[3])
        key("r")
        click(page.locator("[data-label-name='rocket']"))
        x0, y0, x1, y1 = ROCKET
        drag(canvas_point(box, image, x0, y0), canvas_point(box, image, x1, y1), ms=800)
        page.wait_for_timeout(600)
        add = page.locator("[data-frame-tag-add]")
        add.scroll_into_view_if_needed()
        click(add)
        add.select_option(label="outdoor")
        page.keyboard.press("Escape")
        page.wait_for_timeout(1500)
        show_keys(False)
        say("Everything saves as you go, and survives a crash", hold=600)
        save = page.get_by_text("Save", exact=True)
        if save.is_enabled():
            click(save)
        page.wait_for_timeout(1800)

        # ------------------------------------------------------------- export
        page.goto(f"{base}/projects/{project_id}", wait_until="networkidle")
        page.wait_for_timeout(700)
        say(
            "Export in the format your training code reads",
            "COCO, YOLO, Pascal VOC, CVAT, LabelMe, Cityscapes, TFRecord and more",
        )
        select = page.locator("#export-format")
        select.scroll_into_view_if_needed()
        page.wait_for_timeout(500)
        # Hovered and changed in place: opening it would show Chrome's own popup, which a
        # screen capture of a virtual display renders as stray white slivers.
        box = select.bounding_box()
        assert box is not None
        glide(box["x"] + box["width"] / 2, box["y"] + box["height"] / 2)
        options = select.locator("option")
        for index in range(options.count()):
            select.select_option(options.nth(index).get_attribute("value") or "")
            page.wait_for_timeout(170)
        select.select_option("coco")
        page.wait_for_timeout(700)
        recorder.download_at = time.monotonic() - recorder.started
        with page.expect_download(timeout=60_000) as download:
            click(page.locator("[data-export-run]"))
        saved = workdir / "export.zip"
        download.value.save_as(saved)
        say("A real COCO file: three boxes from the model, the rest by hand", hold=2600)
        say("")
        card(OUTRO, 5600)
        recorder.stop()
        context.close()
    return saved, recorder.download_at or 0.0


def check_export(saved: Path) -> None:
    """The export the video shows is a real one: it holds what the video made."""
    with zipfile.ZipFile(saved) as archive:
        document = json.loads(
            archive.read(next(n for n in archive.namelist() if n.endswith(".json")))
        )
    categories = {c["id"]: c["name"] for c in document["categories"]}
    names = sorted(categories[a["category_id"]] for a in document["annotations"])
    print(f"  the exported COCO file holds: {', '.join(names)}")
    expected = ["cat", "cup", "person", "rocket", "saucer", "spoon"]
    if names != expected:
        raise SystemExit(f"the export does not hold what the video made: expected {expected}")


def check_geometry(base: str, token: str, job_id: str, label_ids: dict[str, str]) -> None:
    """What was drawn by hand landed where it was meant to: a count alone passed a run
    whose saucer covered the whole photograph."""
    shapes = api(base, token, f"/jobs/{job_id}/annotations")["shapes"]
    names = {v: k for k, v in label_ids.items()}

    def bounds(points: list[float]) -> tuple[float, float, float, float]:
        xs, ys = points[0::2], points[1::2]
        return min(xs), min(ys), max(xs), max(ys)

    def iou(a: tuple[float, ...], b: tuple[float, ...]) -> float:
        ix = max(0.0, min(a[2], b[2]) - max(a[0], b[0]))
        iy = max(0.0, min(a[3], b[3]) - max(a[1], b[1]))
        union = (a[2] - a[0]) * (a[3] - a[1]) + (b[2] - b[0]) * (b[3] - b[1]) - ix * iy
        return ix * iy / union if union else 0.0

    cat_xs, cat_ys = [x for x, _ in CAT], [y for _, y in CAT]
    meant = {
        "saucer": SAUCER,
        "spoon": SPOON,
        "rocket": ROCKET,
        "cat": (min(cat_xs), min(cat_ys), max(cat_xs), max(cat_ys)),
    }
    for shape in shapes:
        name = names.get(shape["label_id"], "")
        if name in meant:
            overlap = iou(bounds(shape["points"]), meant[name])
            print(f"  {name}: drawn where it was meant to go, IoU {overlap:.2f}")
            if overlap < 0.8:
                raise SystemExit(f"the {name} was not drawn where it was meant to be")


def finish(raw: Path, mp4: Path, download_at: float) -> None:
    """Trim the blank first moment, and encode for the web.

    From the export on, Chrome draws its own download animation at the window's left edge,
    in a native window over the page: browser chrome, not CurveVision, and no flag turns it
    off in an app window. That strip holds nothing of the application on the project page,
    so it is painted over in the page's own background from the moment Export is clicked.
    """
    trim = 0.3
    cover = (
        f"drawbox=x=0:y=40:w=64:h=640:color=0x060912@1:t=fill"
        f":enable='gte(t,{max(0.0, download_at - trim):.2f})'"
    )
    subprocess.run(
        [
            "ffmpeg",
            "-y",
            "-loglevel",
            "error",
            "-ss",
            str(trim),
            "-i",
            str(raw),
            "-vf",
            f"fade=t=in:st=0:d=0.5,{cover}",
            "-c:v",
            "libx264",
            "-preset",
            "slow",
            "-crf",
            "20",
            "-pix_fmt",
            "yuv420p",
            "-movflags",
            "+faststart",
            str(mp4),
        ],
        check=True,
    )


def main() -> int:
    argparse.ArgumentParser(description=__doc__).parse_args()
    for tool in ("Xvfb", "ffmpeg"):
        if shutil.which(tool) is None:
            raise SystemExit(f"needs {tool}")
    import demo_detector

    detector = demo_detector.serve(0)
    threading.Thread(target=detector.serve_forever, daemon=True).start()
    detector_url = f"http://127.0.0.1:{detector.server_port}/infer"
    classes = demo_detector.labels()

    OUT.mkdir(parents=True, exist_ok=True)
    xvfb = subprocess.Popen(
        ["Xvfb", DISPLAY, "-screen", "0", f"{SCREEN[0]}x{SCREEN[1]}x24", "-nolisten", "tcp"],
        stderr=subprocess.DEVNULL,
    )
    try:
        with tempfile.TemporaryDirectory(prefix="curvevision-demo-") as scratch:
            workdir = Path(scratch)
            server, base = start_web_server(workdir)
            try:
                anonymous(
                    base,
                    "/auth/register",
                    {
                        "username": USERNAME,
                        "email": "maya@example.com",
                        "password": PASSWORD,
                        "full_name": "Maya Okafor",
                    },
                )
                token = anonymous(
                    base, "/auth/login", {"identifier": USERNAME, "password": PASSWORD}
                )
                job_id, project_id = seed(base, token["access_token"], detector_url, classes)
                project = api(base, token["access_token"], f"/projects/{project_id}")
                label_ids = {label["name"]: label["id"] for label in project["labels"]}
                raw = workdir / "raw.mkv"
                saved, download_at = record(base, job_id, project_id, raw, workdir)
                mp4 = OUT / "curvevision-demo.mp4"
                finish(raw, mp4, download_at)
                print(f"  {mp4.relative_to(REPO)}")
                check_export(saved)
                check_geometry(base, token["access_token"], job_id, label_ids)
            finally:
                server.terminate()
                server.wait(timeout=20)
    finally:
        xvfb.terminate()
        detector.shutdown()
    return 0


if __name__ == "__main__":
    sys.exit(main())
