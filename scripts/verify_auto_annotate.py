#!/usr/bin/env python3
"""Auto-annotate a job from the editor, against a real model server.

    python scripts/verify_auto_annotate.py

`api.models`, `api.runInference` and `api.decideSuggestions` sat on the web client from the
first web iteration with nothing calling them: the editor had no AI affordance at all while
the server grew a whole inference path. This drives the panel that closes that.

**The model server here is real**, not a stub inside the app: a thread running an HTTP
server on localhost that speaks the documented contract. The packaged sidecar reaches it the
way it would reach anybody's Triton or FastAPI box, so this exercises the whole path —
browser, API, provider, HTTP, and back into the annotation tables — rather than a mock of
the middle of it.

`autoAnnotate.test.ts` and `suggestions.test.ts` prove the wording and the rules in a runtime
with no DOM. They cannot prove that the panel reaches the endpoint, that what the model
returns becomes an editable annotation, that **the classes the annotator typed are the
classes the model is asked for** — a panel that silently dropped them would look identical
and quietly run the wrong search — or that accepting a suggestion changes anything an
annotator can see.

Running a model:

1. A fixed-head model shows no class box and reports what it can find.
2. An open-vocabulary model shows one, and an **empty** box says it will use the project's
   own labels — the fallback an annotator cannot otherwise guess.
3. Typing classes sends exactly those to the model server.
4. What comes back is stored as an annotation with `source="model"`, editable like any
   other.
5. A class with no matching project label is called out before the run, not after.
6. Switching to a fixed-head model with classes still typed is not a dead end: the run goes
   ahead, says plainly that those classes are ignored, and sends no prompt a fixed head
   would be refused for.

Reviewing what it proposed:

7. The editor says how many suggestions are waiting, counted over the whole job rather than
   the frame on screen.
8. One awaiting review is drawn **dashed**, and flagged in the object list; one that has
   been accepted is drawn **solid** and flagged nowhere — the dash measured off the canvas,
   because this is the claim that makes accepting visible at all. The editor used to dash
   every `source="model"` annotation, which meant accepting one changed nothing on screen.
9. Accepting keeps the annotation and keeps `source="model"`, clearing only the confidence:
   the dataset still records that a machine drew it and a human agreed.
10. Rejecting deletes the suggestion and leaves accepted work alone.

Needs the packaged server (`python desktop/sidecar/build.py`) and a Chromium Playwright can
drive; set `CURVEVISION_CHROMIUM` if the bundled one is not found.
"""

from __future__ import annotations

import io
import json
import sys
import tempfile
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from typing import ClassVar

sys.path.insert(0, str(Path(__file__).parent))

from screenshot import api, find_chromium, start_server

IMAGE_WIDTH = 480
IMAGE_HEIGHT = 320

#: What the fake model "finds". Fixed geometry, so the assertion is about the loop rather
#: than about anything clever.
FOUND_BOX = [40.0, 60.0, 180.0, 220.0]


#: How much of a horizontal run of the shapes layer carries an **opaque** pixel.
#:
#: The distinction between a suggestion awaiting review and one that has been accepted is
#: that the first is stroked dashed, and a dash pattern is exactly a stroke with gaps in it.
#: So: walk the columns of a thin band across a shape's top edge and count the ones holding
#: a pixel the stroke could have put there. A `[6, 4]` dash lights about 60% of them; a
#: solid stroke lights all of them.
#:
#: The alpha threshold is what separates the stroke from the translucent fill beneath it:
#: the stroke is drawn fully opaque, the fill at 0.18, so anything above half is stroke.
STROKE_COVERAGE = """
([x0, x1, y, band]) => {
  const canvas = document.querySelectorAll('canvas')[1];
  const context = canvas.getContext('2d');
  const top = Math.max(0, Math.round(y - band));
  const height = Math.min(canvas.height - top, band * 2 + 1);
  const left = Math.max(0, Math.round(x0));
  const width = Math.min(canvas.width - left, Math.round(x1 - x0));
  if (width <= 0 || height <= 0) return { columns: 0, lit: 0 };
  const { data } = context.getImageData(left, top, width, height);
  let lit = 0;
  for (let col = 0; col < width; col += 1) {
    for (let row = 0; row < height; row += 1) {
      if (data[(row * width + col) * 4 + 3] > 128) { lit += 1; break; }
    }
  }
  return { columns: width, lit };
}
"""

#: The fitted frame rectangle, measured off the **media** layer, which holds the frame image
#: and nothing else. Measuring it off the shapes layer instead includes the label chip drawn
#: above a shape's corner, which is a harness bug that reads exactly like a renderer one --
#: `verify_mask_rendering.py` has the long version of that story.
FRAME_BOUNDS = """
() => {
  const canvas = document.querySelectorAll('canvas')[0];
  const context = canvas.getContext('2d');
  const { data, width, height } = context.getImageData(0, 0, canvas.width, canvas.height);
  let minX = Infinity, minY = Infinity, maxX = -Infinity, maxY = -Infinity;
  for (let y = 0; y < height; y += 1) {
    for (let x = 0; x < width; x += 1) {
      if (data[(y * width + x) * 4 + 3] > 8) {
        if (x < minX) minX = x;
        if (y < minY) minY = y;
        if (x > maxX) maxX = x;
        if (y > maxY) maxY = y;
      }
    }
  }
  return { minX, minY, maxX, maxY };
}
"""


def top_edge_coverage(page, frame: dict) -> float:
    """What fraction of `FOUND_BOX`'s top edge is stroked, in the canvas' own pixels.

    Image coordinates are mapped through the frame rectangle rather than through the
    canvas' CSS box, so the answer does not depend on the device pixel ratio.
    """
    span_x = frame["maxX"] - frame["minX"] + 1
    span_y = frame["maxY"] - frame["minY"] + 1
    left, top, right, _bottom = FOUND_BOX

    def to_screen_x(x: float) -> float:
        return frame["minX"] + (x / IMAGE_WIDTH) * span_x

    # Inset the ends so the corners -- which both edges stroke, and which a dash pattern
    # always starts on -- cannot flatter a dashed edge.
    inset = (to_screen_x(right) - to_screen_x(left)) * 0.1
    measured = page.evaluate(
        STROKE_COVERAGE,
        [
            to_screen_x(left) + inset,
            to_screen_x(right) - inset,
            frame["minY"] + (top / IMAGE_HEIGHT) * span_y,
            3,
        ],
    )
    return measured["lit"] / measured["columns"] if measured["columns"] else 0.0

class ModelServer(BaseHTTPRequestHandler):
    """A model server in the documented contract, in about thirty lines.

    That it is this small is the point of ADR 0005: attaching a model is a URL, not a
    deployment project. It records every request so the harness can assert what the panel
    actually asked for.
    """

    #: Shared, because `HTTPServer` builds a fresh handler per request.
    received: ClassVar[list[dict]] = []

    def do_POST(self) -> None:
        length = int(self.headers.get("Content-Length", "0"))
        request = json.loads(self.rfile.read(length) or b"{}")
        ModelServer.received.append(request)

        # Return the first requested class, so the harness can tell a run that honoured the
        # prompt from one that ignored it.
        wanted = request.get("classes") or ["car"]
        shapes = [
            {
                "frame": frame["frame"],
                "label": wanted[0],
                "type": "rectangle",
                "points": FOUND_BOX,
                "confidence": 0.77,
            }
            for frame in request.get("frames", [])
        ]
        body = json.dumps({"shapes": shapes, "tags": [], "warnings": []}).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *_args) -> None:
        """Quiet: the harness's own output is the log."""


def png_bytes(width: int = IMAGE_WIDTH, height: int = IMAGE_HEIGHT) -> bytes:
    from PIL import Image

    buffer = io.BytesIO()
    Image.new("RGB", (width, height), "#1e293b").save(buffer, format="PNG")
    return buffer.getvalue()


def seed(base: str, token: str, model_url: str) -> tuple[str, str]:
    """A one-frame job, and two models: one fixed-head, one open-vocabulary."""
    org = api(base, token, "/organizations")[0]
    project = api(base, token, "/projects", {
        "organization_id": org["id"],
        "slug": "auto",
        "name": "Auto",
        "description": "Finding things by name.",
        "labels": [
            {"name": "car", "color": "#ef4444"},
            {"name": "pedestrian", "color": "#22c55e"},
        ],
    })
    task = api(base, token, "/tasks", {
        "project_id": project["id"], "name": "Frames", "media_kind": "image",
    })
    api(base, token, f"/tasks/{task['id']}/assets", files=png_bytes(),
        filename="a.png", content_type="image/png")
    job = api(base, token, f"/tasks/{task['id']}/jobs")[0]

    for slug, name, open_vocab in (
        ("fixed-head", "Fixed Detector", False),
        ("open-vocab", "Open Detector", True),
    ):
        api(base, token, f"/models?organization_id={org['id']}", {
            "slug": slug,
            "name": name,
            "kind": "detector",
            "provider": "http",
            "config": {"endpoint": model_url},
            "output_labels": ["car", "person"],
            "open_vocabulary": open_vocab,
        })
    return str(job["id"]), str(project["id"])


def main() -> int:
    from playwright.sync_api import sync_playwright

    failures: list[str] = []

    def check(condition: bool, passed: str, failed: str) -> None:
        if condition:
            print(f"  ok   {passed}")
        else:
            failures.append(failed)
            print(f"  FAIL {failed}")

    httpd = HTTPServer(("127.0.0.1", 0), ModelServer)
    model_url = f"http://127.0.0.1:{httpd.server_port}/infer"
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    print(f"a model server on {model_url}, and a project labelled car + pedestrian\n")

    with tempfile.TemporaryDirectory(prefix="curvevision-auto-") as workspace:
        process, handshake = start_server(Path(workspace) / "data")
        base, token = handshake["url"], handshake["token"]
        try:
            job_id, _project_id = seed(base, token, model_url)
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
                page.wait_for_timeout(1800)
                check(not raised, "the editor mounts without raising", f"page error: {raised}")

                panel = page.locator("[data-auto-annotate]")
                check(panel.count() == 1,
                      "the editor offers to auto-annotate",
                      "no auto-annotate panel in the editor")
                if panel.count() != 1:
                    browser.close()
                    raise SystemExit(1)

                picker = page.locator("[data-auto-annotate-model]")
                classes = page.locator("[data-auto-annotate-classes]")
                plan = page.locator("[data-auto-annotate-plan]")
                run = page.locator("[data-auto-annotate-run]")

                # ------------------------------------------- the fixed-head model
                picker.select_option(label="Fixed Detector")
                page.wait_for_timeout(300)
                fixed_plan = plan.inner_text().strip()
                print(f"  fixed head: {fixed_plan!r}")
                check(classes.count() == 0,
                      "a fixed-head model offers no class box, because it has no use for one",
                      "a class box is shown for a model with a fixed label space")
                check("car" in fixed_plan and "person" in fixed_plan,
                      "and says what it can find instead",
                      f"the fixed-head model says {fixed_plan!r}")

                # ------------------------------------------- the open-vocabulary one
                picker.select_option(label="Open Detector")
                page.wait_for_timeout(300)
                check(classes.count() == 1,
                      "an open-vocabulary model offers a class box",
                      "no class box for an open-vocabulary model")
                empty_plan = plan.inner_text().strip()
                print(f"  open, empty box: {empty_plan!r}")
                # The behaviour an annotator cannot guess: empty is not "find nothing".
                check("car" in empty_plan and "pedestrian" in empty_plan,
                      "an empty box says it will use the project's own labels",
                      f"an empty class box says {empty_plan!r}, which does not name the "
                      "project's labels")

                # ------------------------------------------- a class with nowhere to land
                classes.fill("forklift")
                page.wait_for_timeout(300)
                unmatched = page.locator("[data-auto-annotate-unmatched]")
                warned = unmatched.inner_text().strip() if unmatched.count() else ""
                print(f"  unmatched warning: {warned!r}")
                check("forklift" in warned and "discard" in warned.lower(),
                      "a class with no project label is called out before the run, not after",
                      f"nothing warned that forklift has nowhere to land: {warned!r}")

                # ------------------------------------------- the real run
                classes.fill("car")
                page.wait_for_timeout(300)
                before = len(ModelServer.received)
                run.click()
                page.wait_for_timeout(3000)
                check(not raised, "running raises nothing", f"page error: {raised}")

                check(len(ModelServer.received) == before + 1,
                      "the panel reached the model server",
                      f"the model server saw {len(ModelServer.received) - before} requests")
                if len(ModelServer.received) <= before:
                    browser.close()
                    raise SystemExit(1)

                sent = ModelServer.received[-1]
                print(f"  the model was asked for: {sent.get('classes')}")
                # The claim a silent drop would look identical to.
                check(sent.get("classes") == ["car"],
                      "the classes typed are the classes the model was asked for",
                      f"the model was asked for {sent.get('classes')}, not ['car']")

                result = page.locator("[data-auto-annotate-result]")
                summary = result.inner_text().strip() if result.count() else ""
                print(f"  panel says: {summary!r}")
                check("1 object" in summary,
                      "the panel says what it added",
                      f"the panel reported {summary!r}")

                stored = api(base, token, f"/jobs/{job_id}/annotations")["shapes"]
                print(f"  stored: {len(stored)} shape(s), "
                      f"source={[s['source'] for s in stored]}")
                check(len(stored) == 1,
                      "the prediction reached the annotation tables",
                      f"expected one stored shape, found {len(stored)}")
                if len(stored) == 1:
                    check(stored[0]["source"] == "model",
                          "it is marked as a model suggestion, not as somebody's own work",
                          f"the stored shape's source is {stored[0]['source']!r}")
                    check(stored[0]["points"] == FOUND_BOX,
                          "with the geometry the model returned",
                          f"the stored geometry is {stored[0]['points']}")

                shot = Path("/tmp/curvevision-auto-annotate.png")
                page.screenshot(path=str(shot), full_page=False)
                print(f"  (screenshot: {shot})")

                # ---------------------------- a fixed-head model, with classes still typed
                # The state an annotator lands in by switching models after typing: the
                # class box unmounts with the text still in it. Refusing the run here would
                # be an error quoting text they can no longer see or clear.
                picker.select_option(label="Open Detector")
                page.wait_for_timeout(250)
                classes.fill("forklift")
                page.wait_for_timeout(250)
                picker.select_option(label="Fixed Detector")
                page.wait_for_timeout(400)
                switched = plan.inner_text().strip()
                ignored = page.locator("[data-auto-annotate-ignored]")
                note = ignored.inner_text().strip() if ignored.count() else ""
                print(f"  after switching with 'forklift' typed: {switched!r} / {note!r}")
                check(not run.is_disabled(),
                      "switching to a fixed-head model is not a dead end",
                      "the run button is dead over text with no box left to clear it in")
                check("forklift" in note,
                      "and the typed classes are said to be ignored, not silently dropped",
                      f"nothing said forklift would be ignored: {switched!r} / {note!r}")

                before = len(ModelServer.received)
                run.click()
                page.wait_for_timeout(3000)
                check(len(ModelServer.received) == before + 1,
                      "a fixed-head run goes ahead",
                      "the fixed-head run never reached the model server")
                if len(ModelServer.received) > before:
                    sent = ModelServer.received[-1]
                    print(f"  the fixed-head model was asked for: {sent.get('classes')}")
                    # The server 422s on this; the panel must not get that far.
                    check(not sent.get("classes"),
                          "and carries no class prompt a fixed head would be refused for",
                          f"a fixed-head model was prompted with {sent.get('classes')}")
                # ------------------------------------------- reviewing what it proposed
                # Two suggestions are stored by now, at the same geometry: one from the
                # open-vocabulary run and one from the fixed-head one.
                review_panel = page.locator("[data-suggestions]")
                pending = page.locator("[data-suggestions-pending]")
                waiting = pending.inner_text().strip() if pending.count() else ""
                print(f"  review panel: {waiting!r}")
                check("2 suggestions" in waiting and "this job" in waiting,
                      "the editor says how many suggestions are waiting, job-wide",
                      f"the review panel says {waiting!r}")

                flagged = page.locator("[data-unreviewed]")
                check(flagged.count() == 2,
                      "and flags both of them in the object list",
                      f"{flagged.count()} objects are flagged as awaiting review, not 2")

                frame = page.evaluate(FRAME_BOUNDS)
                dashed = top_edge_coverage(page, frame)
                print(f"  top edge stroked over {dashed:.0%} of its length (awaiting review)")
                # A `[6, 4]` dash leaves gaps; a solid stroke does not.
                check(dashed < 0.85,
                      "an unreviewed suggestion is drawn dashed",
                      f"a suggestion's edge is stroked over {dashed:.0%} of its length, "
                      "which is not a dash pattern")

                page.locator("[data-suggestions-accept]").click()
                page.wait_for_timeout(2500)
                outcome = page.locator("[data-suggestions-outcome]")
                said = outcome.inner_text().strip() if outcome.count() else ""
                print(f"  after accepting: {said!r}")
                check("2" in said,
                      "accepting says what it kept",
                      f"accepting reported {said!r}")

                kept = api(base, token, f"/jobs/{job_id}/annotations")["shapes"]
                print(f"  stored: {len(kept)} shape(s), "
                      f"source={[s['source'] for s in kept]}, "
                      f"confidence={[s['confidence'] for s in kept]}")
                check(len(kept) == 2,
                      "accepting keeps the annotations",
                      f"{len(kept)} shapes survived accepting two")
                # The provenance decision: a dataset still records that a machine drew this
                # and a human agreed, which is not the same as a human having drawn it.
                check(all(s["source"] == "model" for s in kept),
                      "and still records that a model produced them",
                      f"the accepted sources are {[s['source'] for s in kept]}")
                check(all(s["confidence"] is None for s in kept),
                      "with the confidence cleared, which is what marks them reviewed",
                      f"the accepted confidences are {[s['confidence'] for s in kept]}")

                # The claim the whole renderer change exists for: accepting has to be
                # visible, or the button appears to do nothing.
                solid = top_edge_coverage(page, frame)
                print(f"  top edge stroked over {solid:.0%} of its length (accepted)")
                check(solid > 0.95,
                      "an accepted suggestion is drawn solid, like any other annotation",
                      f"an accepted suggestion is still stroked over only {solid:.0%} of "
                      "its edge, so accepting changed nothing on screen")

                check(page.locator("[data-suggestions-pending]").count() == 0,
                      "and nothing is left waiting",
                      "the panel still lists suggestions to review after accepting them")
                check(flagged.count() == 0,
                      "nor flagged in the object list",
                      f"{flagged.count()} objects are still flagged after being accepted")

                # ------------------------------------------- rejecting deletes
                before = len(ModelServer.received)
                run.click()
                page.wait_for_timeout(3000)
                check(len(ModelServer.received) == before + 1,
                      "a further run adds another suggestion",
                      "the second run never reached the model server")
                page.wait_for_timeout(500)
                waiting = pending.inner_text().strip() if pending.count() else ""
                check("1 suggestion" in waiting,
                      "which is the only thing waiting -- the accepted ones are done",
                      f"the panel says {waiting!r} after one new suggestion")

                page.locator("[data-suggestions-reject]").click()
                page.wait_for_timeout(2500)
                left = api(base, token, f"/jobs/{job_id}/annotations")["shapes"]
                print(f"  after rejecting: {len(left)} shape(s) left")
                check(len(left) == 2,
                      "rejecting deletes the suggestion and leaves the accepted work alone",
                      f"{len(left)} shapes are left, not the two that were accepted")
                check(review_panel.count() == 1,
                      "the review panel stays to report what it did",
                      "the review panel vanished before saying what it rejected")

                shot = Path("/tmp/curvevision-suggestions.png")
                page.screenshot(path=str(shot), full_page=False)
                print(f"  (screenshot: {shot})")
                check(not raised, "the whole loop raises nothing", f"page error: {raised}")

                browser.close()
        finally:
            httpd.shutdown()
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
