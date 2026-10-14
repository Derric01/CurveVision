#!/usr/bin/env python3
"""Outline an object by clicking it, with an interactor model, in the editor.

    python scripts/verify_interactor.py

The inference contract carried `prompts` for `interactor` models from the start, and the
editor listed such models only to say it could not run them. Iteration 98 added the tool
(`canvas/interactor.ts`); this drives it in Chromium against the real server and a model
server of its own that answers clicks:

1. the tool says what to do, and that the workspace has a model to do it with;
2. a click on the object reaches the model as a positive prompt, in image pixels, for the
   frame on screen, and its outline is shown;
3. a right-click off the object reaches it as a negative prompt, and the outline changes;
4. Enter keeps the outline: a polygon in the job, `source="model"` with no confidence —
   a model drew it and a person accepted it — exactly where the model put it;
5. the auto-annotate panel points at the tool rather than saying it cannot run the model.

The model server outlines a square around the clicks on the object, shrinking it for each
click off it, so the stored shape shows that both kinds of click were heard.

Needs the built web application (`npm --prefix web run build`) and a Chromium.
"""

from __future__ import annotations

import io
import json
import sys
import tempfile
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any, ClassVar

sys.path.insert(0, str(Path(__file__).parent))

from screenshot import api, find_chromium
from verify_workspace import PASSWORD, anonymous, start_web_server

WIDTH, HEIGHT = 480, 320
HALF = 40  # the model's square, before clicks off the object shrink it
SHRINK = 15


def outline(positive: list[list[float]], negative: list[list[float]]) -> list[float]:
    cx = sum(x for x, _ in positive) / len(positive)
    cy = sum(y for _, y in positive) / len(positive)
    half = HALF - SHRINK * len(negative)
    return [cx - half, cy - half, cx + half, cy - half, cx + half, cy + half, cx - half, cy + half]


class Interactor(BaseHTTPRequestHandler):
    """A click-answering model in the documented contract."""

    received: ClassVar[list[dict[str, Any]]] = []

    def do_POST(self) -> None:
        request = json.loads(self.rfile.read(int(self.headers.get("Content-Length", 0))))
        Interactor.received.append(request)
        prompts = request.get("prompts") or {}
        positive = prompts.get("positive") or []
        shapes = (
            [
                {
                    "frame": frame["frame"],
                    "label": "object",
                    "type": "polygon",
                    "points": outline(positive, prompts.get("negative") or []),
                    "confidence": 0.93,
                }
                for frame in request.get("frames", [])
            ]
            if positive
            else []
        )
        body = json.dumps({"shapes": shapes, "tags": [], "warnings": []}).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *_: object) -> None:
        pass


def png_bytes() -> bytes:
    from PIL import Image

    buffer = io.BytesIO()
    Image.new("RGB", (WIDTH, HEIGHT), "#1e293b").save(buffer, format="PNG")
    return buffer.getvalue()


#: The fitted frame on the media layer, in canvas pixels (see verify_auto_annotate.py).
FRAME_BOUNDS = """
() => {
  const canvas = document.querySelectorAll('canvas')[0];
  const pixels = canvas.getContext('2d').getImageData(0, 0, canvas.width, canvas.height);
  const { data, width, height } = pixels;
  let minX = Infinity, minY = Infinity, maxX = -Infinity, maxY = -Infinity;
  for (let y = 0; y < height; y += 1) for (let x = 0; x < width; x += 1) {
    if (data[(y * width + x) * 4 + 3] > 8) {
      minX = Math.min(minX, x); minY = Math.min(minY, y);
      maxX = Math.max(maxX, x); maxY = Math.max(maxY, y);
    }
  }
  const box = canvas.getBoundingClientRect();
  return { minX, minY, maxX, maxY, left: box.left, top: box.top,
           scale: box.width / canvas.width };
}
"""


def appears(locator: Any, timeout: float = 20_000) -> bool:
    """Whether `locator` appears in time: a wait that runs out is a failed check, not a crash."""
    try:
        locator.wait_for(timeout=timeout)
        return True
    except Exception:
        return False


def near(a: list[float], b: list[float], tolerance: float = 2.5) -> bool:
    return len(a) == len(b) and all(abs(x - y) <= tolerance for x, y in zip(a, b, strict=True))


def main() -> int:
    from playwright.sync_api import sync_playwright

    failures: list[str] = []

    def check(condition: bool, passed: str, failed: str) -> None:
        if condition:
            print(f"  ok   {passed}")
        else:
            failures.append(failed)
            print(f"  FAIL {failed}")

    model = ThreadingHTTPServer(("127.0.0.1", 0), Interactor)
    threading.Thread(target=model.serve_forever, daemon=True).start()
    model_url = f"http://127.0.0.1:{model.server_port}/infer"

    with tempfile.TemporaryDirectory(prefix="curvevision-interactor-") as workdir:
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
                    "slug": "parts",
                    "name": "Parts",
                    "labels": [{"name": "part", "color": "#22c55e"}],
                },
            )
            task = api(
                base,
                token,
                "/tasks",
                {"project_id": project["id"], "name": "Bench", "media_kind": "image"},
            )
            api(
                base,
                token,
                f"/tasks/{task['id']}/assets",
                files=png_bytes(),
                filename="a.png",
                content_type="image/png",
            )
            job = api(base, token, f"/tasks/{task['id']}/jobs")[0]
            api(
                base,
                token,
                f"/models?organization_id={org['id']}",
                {
                    "slug": "clicker",
                    "name": "Clicker",
                    "kind": "interactor",
                    "provider": "http",
                    "config": {"endpoint": model_url},
                    "output_labels": ["object"],
                },
            )

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
                page.wait_for_timeout(1500)
                frame = page.evaluate(FRAME_BOUNDS)

                def at(x: float, y: float) -> tuple[float, float]:
                    span_x = (frame["maxX"] - frame["minX"] + 1) / WIDTH
                    span_y = (frame["maxY"] - frame["minY"] + 1) / HEIGHT
                    return (
                        frame["left"] + (frame["minX"] + x * span_x) * frame["scale"],
                        frame["top"] + (frame["minY"] + y * span_y) * frame["scale"],
                    )

                status = page.locator("[data-interactor-status]")

                # ------------------------------------------------------------ 1
                page.keyboard.press("i")
                status.wait_for(timeout=10_000)
                check(
                    "Click the object" in status.inner_text(),
                    "the tool says what to do, with the workspace's model",
                    f"the tool said {status.inner_text()!r}",
                )

                # ------------------------------------------------------------ 2
                page.mouse.click(*at(200, 150))
                outlined = appears(status.filter(has_text="Enter keeps"))
                first = Interactor.received[-1] if Interactor.received else {}
                prompts = first.get("prompts", {})
                check(
                    outlined
                    and len(prompts.get("positive", [])) == 1
                    and near(prompts["positive"][0], [200, 150])
                    and prompts.get("negative") == []
                    and [f["frame"] for f in first.get("frames", [])] == [job["start_frame"]],
                    "a click on the object reaches the model, in image pixels, for this frame",
                    f"the model was sent {prompts} for frames "
                    f"{[f.get('frame') for f in first.get('frames', [])]}",
                )

                # ------------------------------------------------------------ 3
                before = len(Interactor.received)
                page.mouse.click(*at(300, 150), button="right")
                refined = appears(status.filter(has_text="1 off it").filter(has_text="Enter keeps"))
                second = Interactor.received[-1].get("prompts", {})
                check(
                    refined
                    and len(Interactor.received) == before + 1
                    and len(second.get("negative", [])) == 1
                    and near(second["negative"][0], [300, 150])
                    and near(second.get("positive", [[0, 0]])[0], [200, 150]),
                    "a right-click off the object reaches it as a negative click",
                    f"the model was sent {second}",
                )

                # ------------------------------------------------------------ 4
                page.keyboard.press("Enter")
                expected = outline([[200, 150]], [[300, 150]])
                shapes: list[dict[str, Any]] = []
                for _ in range(40):
                    page.wait_for_timeout(500)
                    shapes = api(base, token, f"/jobs/{job['id']}/annotations")["shapes"]
                    if shapes:
                        break
                kept = shapes[0] if len(shapes) == 1 else {}
                check(
                    kept.get("shape_type") == "polygon"
                    and kept.get("source") == "model"
                    and kept.get("confidence") is None
                    and near(kept.get("points", []), expected),
                    "Enter keeps the outline where the model put it, as a model-drawn shape",
                    f"the job holds {shapes}",
                )

                # ------------------------------------------------------------ 5
                reason = page.locator("[data-auto-annotate]").inner_text()
                check(
                    "Outline tool" in reason,
                    "the auto-annotate panel points at the tool for an interactor",
                    f"the panel says {reason[:300]!r}",
                )

                check(not raised, "no page errors", f"page errors: {raised}")
                browser.close()
        finally:
            server.terminate()
            model.shutdown()

    print()
    if failures:
        print(f"{len(failures)} check(s) failed")
        return 1
    print("every check passed")
    return 0


if __name__ == "__main__":
    sys.exit(main())
