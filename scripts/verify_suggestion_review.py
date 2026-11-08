#!/usr/bin/env python3
"""Review a model's suggestions one at a time, in the editor.

    python scripts/verify_suggestion_review.py

Until iteration 99 suggestions could be accepted or rejected only all at once; one was
"accepted" by editing it and "rejected" by deleting it, which a reviewer had to know. This
drives the per-suggestion path in Chromium against the real server, with suggestions a real
model run wrote (a detector behind the inference contract, one box per frame):

1. *Next suggestion* selects the first one waiting, and the panel offers to decide it alone;
2. *Accept* keeps that one — `source="model"`, confidence cleared — and only that one;
3. *Next suggestion* moves to the next frame's and selects it there;
4. *Reject* deletes that one and nothing else;
5. an edit made to a suggestion and not yet saved is saved before the decision, so
   accepting it keeps the edit rather than the model's geometry;
6. *Least confident first* goes to the suggestion the model was least sure of, wherever it
   is in the job, and the panel says how sure (iteration 100).

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
from typing import Any

sys.path.insert(0, str(Path(__file__).parent))

from screenshot import api, find_chromium
from verify_workspace import PASSWORD, anonymous, start_web_server

FRAMES = 5
#: How sure the model is on each frame. Frame 4 is the least sure, and frame 3 comes first
#: in frame order: so the two orders disagree about what is next.
CONFIDENCE = [0.9, 0.8, 0.85, 0.6, 0.3]
BOX = [60.0, 50.0, 200.0, 170.0]


class Detector(BaseHTTPRequestHandler):
    def do_POST(self) -> None:
        request = json.loads(self.rfile.read(int(self.headers.get("Content-Length", 0))))
        shapes = [
            {
                "frame": frame["frame"],
                "label": "car",
                "type": "rectangle",
                "points": [v + 10 * frame["frame"] for v in BOX],
                "confidence": CONFIDENCE[frame["frame"]],
            }
            for frame in request.get("frames", [])
        ]
        body = json.dumps({"shapes": shapes, "tags": [], "warnings": []}).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *_: object) -> None:
        pass


def png_bytes(shade: int) -> bytes:
    from PIL import Image

    buffer = io.BytesIO()
    Image.new("RGB", (480, 320), (30 + shade * 40, 41, 59)).save(buffer, format="PNG")
    return buffer.getvalue()


#: The fitted frame on the media layer (see verify_interactor.py).
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

    model = ThreadingHTTPServer(("127.0.0.1", 0), Detector)
    threading.Thread(target=model.serve_forever, daemon=True).start()

    with tempfile.TemporaryDirectory(prefix="curvevision-suggestions-") as workdir:
        server, base = start_web_server(Path(workdir))
        try:
            anonymous(
                base,
                "/auth/register",
                {"username": "rhea", "email": "rhea@example.com", "password": PASSWORD},
            )
            session = anonymous(base, "/auth/login", {"identifier": "rhea", "password": PASSWORD})
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
                    "labels": [{"name": "car", "color": "#ef4444"}],
                },
            )
            task = api(
                base,
                token,
                "/tasks",
                {"project_id": project["id"], "name": "Clip", "media_kind": "image"},
            )
            for shade in range(FRAMES):
                api(
                    base,
                    token,
                    f"/tasks/{task['id']}/assets",
                    files=png_bytes(shade),
                    filename=f"{shade}.png",
                    content_type="image/png",
                )
            job = api(base, token, f"/tasks/{task['id']}/jobs")[0]
            detector = api(
                base,
                token,
                f"/models?organization_id={org['id']}",
                {
                    "slug": "det",
                    "name": "Detector",
                    "kind": "detector",
                    "provider": "http",
                    "config": {"endpoint": f"http://127.0.0.1:{model.server_port}/infer"},
                    "output_labels": ["car"],
                },
            )
            api(
                base,
                token,
                f"/jobs/{job['id']}/inference",
                {
                    "model_id": detector["id"],
                    "job_id": job["id"],
                    "label_mapping": {"car": project["labels"][0]["id"]},
                    # Low enough to keep the 30%-sure one: the run's default drops below 50%.
                    "confidence_threshold": 0.1,
                },
            )

            def shapes() -> dict[int, dict[str, Any]]:
                listed = api(base, token, f"/jobs/{job['id']}/annotations")["shapes"]
                return {shape["frame"]: shape for shape in listed}

            seeded = shapes()
            if len(seeded) != FRAMES or not all(s["confidence"] for s in seeded.values()):
                raise SystemExit(f"the model run did not leave one suggestion per frame: {seeded}")

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
                page.wait_for_timeout(1200)
                pending = page.locator("[data-suggestions-pending]")
                selected = page.locator("[data-suggestion-selected]")

                # ------------------------------------------------------------ 1
                page.click("[data-suggestion-next]")
                check(
                    appears(selected) and "5 suggestions" in pending.inner_text(),
                    "Next suggestion selects the first one waiting, and offers to decide it alone",
                    f"selected block shown: {selected.count()}; pending says "
                    f"{pending.inner_text()!r}",
                )

                # ------------------------------------------------------------ 2
                page.click("[data-suggestion-accept]")
                appears(pending.filter(has_text="4 suggestions"))
                after = shapes()
                check(
                    after[0]["confidence"] is None
                    and after[0]["source"] == "model"
                    and after[1]["confidence"] is not None
                    and after[2]["confidence"] is not None,
                    "Accept keeps that one, still a model's, and leaves the others waiting",
                    "after accepting: "
                    f"{[(f, x['source'], x['confidence']) for f, x in after.items()]}",
                )

                # ------------------------------------------------------------ 3
                page.click("[data-suggestion-next]")
                check(
                    appears(selected),
                    "Next suggestion moves to the next frame's, selected there",
                    "no suggestion selected after Next",
                )

                # ------------------------------------------------------------ 4
                page.click("[data-suggestion-reject]")
                appears(pending.filter(has_text="3 suggestions"))
                after = shapes()
                check(
                    sorted(after) == [0, 2, 3, 4] and after[2]["confidence"] is not None,
                    "Reject deletes that one and nothing else",
                    f"after rejecting, frames with shapes: {sorted(after)}",
                )

                # ------------------------------------------------------------ 5
                page.click("[data-suggestion-next]")
                appears(selected)
                frame = page.evaluate(FRAME_BOUNDS)

                def at(x: float, y: float) -> tuple[float, float]:
                    span_x = (frame["maxX"] - frame["minX"] + 1) / 480
                    span_y = (frame["maxY"] - frame["minY"] + 1) / 320
                    return (
                        frame["left"] + (frame["minX"] + x * span_x) * frame["scale"],
                        frame["top"] + (frame["minY"] + y * span_y) * frame["scale"],
                    )

                # Drag frame 2's box 30 px right; Accept comes before autosave would.
                page.mouse.move(*at(150, 130))
                page.mouse.down()
                page.mouse.move(*at(165, 130), steps=4)
                page.mouse.move(*at(180, 130), steps=4)
                page.mouse.up()
                page.click("[data-suggestion-accept]")
                appears(page.locator("[data-suggestions-outcome]"))
                page.wait_for_timeout(800)
                kept = shapes().get(2, {})
                moved = abs(kept.get("points", BOX)[0] - (BOX[0] + 20 + 30)) <= 3
                check(
                    kept.get("confidence") is None and moved,
                    "an unsaved edit is saved before the decision, and accepting keeps it",
                    f"frame 2 now {kept.get('source')} {kept.get('confidence')} "
                    f"{kept.get('points')}",
                )

                # ------------------------------------------------------------ 6
                # Frames 3 and 4 are left, 60% and 30% sure, and the reviewer is on frame 2: in
                # frame order 3 is next, least confident first goes to 4.
                page.check("[data-suggestion-order]")
                page.click("[data-suggestion-next]")
                appears(selected)
                sure = page.locator("[data-suggestion-confidence]")
                said = sure.inner_text() if appears(sure, 5_000) else ""
                page.click("[data-suggestion-accept]")
                appears(pending.filter(has_text="1 suggestion "))
                after = shapes()
                check(
                    "30% sure" in said
                    and after[4]["confidence"] is None
                    and after[3]["confidence"] is not None,
                    "least confident first reviews the least sure one first, and says how sure",
                    f"the panel said {said!r}; then frame 3 {after.get(3, {}).get('confidence')}, "
                    f"frame 4 {after.get(4, {}).get('confidence')}",
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
