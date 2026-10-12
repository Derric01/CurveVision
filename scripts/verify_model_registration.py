#!/usr/bin/env python3
"""Add a model from the Models page, with an endpoint and a key, and run it in the editor.

    python scripts/verify_model_registration.py

Until iteration 61 the editor's Auto-annotate panel said "register an inference endpoint
and it appears here" and nothing in the application could: a model was registered with a
JSON body to `POST /models`, typing its kind, its labels and the name its server knows it
by. And `GET <endpoint>/models`, which `docs/API.md` told model authors to implement for
discovery, was called by nothing.

**The model server here is real**: an HTTP server on localhost that wants a key and lists
two models, reached by the packaged server the way it would reach anybody's. It records
every request, so each check below is about what actually arrived, not what the page says.

1. With no model, the editor's empty panel links to the Models page.
2. A wrong key is refused before anything is saved, and the page says to check the key.
3. The right key connects; the endpoint's two models are offered, and nothing can be added
   until one is picked.
4. Picking one fills in its name, kind and labels from what the endpoint said.
5. Editing the key after connecting needs connecting again: no key nobody has seen work is
   saved.
6. Adding it lists it, and the key is not in anything the page can read back.
7. The editor offers it, and not a model from another workspace the same person is in,
   which it used to list and the server then refused for this job.
8. The editor runs it: the model server receives the key, and the model's own id.
9. Removing it removes it.

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

KEY = "harness-key-7f3a"
FOUND_BOX = [40.0, 60.0, 180.0, 220.0]
MODELS = [
    {"id": "street-v1", "name": "Street Detector", "kind": "detector",
     "labels": ["car", "person"]},
    {"id": "world-v2", "name": "World Finder", "kind": "detector",
     "labels": [], "open_vocabulary": True},
]


class ModelServer(BaseHTTPRequestHandler):
    """A contract model server that wants a key and implements discovery."""

    #: `(method, path, authorization, body)` of every request, shared across handlers.
    received: ClassVar[list[tuple[str, str, str | None, dict]]] = []

    def _answer(self, status: int, payload: object) -> None:
        body = json.dumps(payload).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _authorised(self, body: dict) -> bool:
        authorization = self.headers.get("Authorization")
        ModelServer.received.append((self.command, self.path, authorization, body))
        if authorization != f"Bearer {KEY}":
            self._answer(401, {"detail": "unknown key"})
            return False
        return True

    def do_GET(self) -> None:
        if not self._authorised({}):
            return
        if self.path == "/infer/models":
            self._answer(200, {"models": MODELS})
        else:
            self._answer(404, {"detail": "not found"})

    def do_POST(self) -> None:
        length = int(self.headers.get("Content-Length", "0"))
        body = json.loads(self.rfile.read(length) or b"{}")
        if not self._authorised(body):
            return
        shapes = [
            {"frame": frame["frame"], "label": "car", "type": "rectangle",
             "points": FOUND_BOX, "confidence": 0.8}
            for frame in body.get("frames", [])
        ]
        self._answer(200, {"shapes": shapes, "tags": [], "warnings": []})

    def log_message(self, *_args) -> None:
        """Quiet: the harness's own output is the log."""


def png_bytes(width: int = 480, height: int = 320) -> bytes:
    from PIL import Image

    buffer = io.BytesIO()
    Image.new("RGB", (width, height), "#1e293b").save(buffer, format="PNG")
    return buffer.getvalue()


def seed(base: str, token: str) -> tuple[str, str]:
    """A one-frame job in a project labelled car + pedestrian, and no model."""
    org = api(base, token, "/organizations")[0]
    project = api(base, token, "/projects", {
        "organization_id": org["id"],
        "slug": "models",
        "name": "Models",
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
    return str(job["id"]), str(org["id"])


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
    endpoint = f"http://127.0.0.1:{httpd.server_port}/infer"
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    print(f"a model server on {endpoint} that wants a key and lists two models\n")

    with tempfile.TemporaryDirectory(prefix="curvevision-models-") as workspace:
        process, handshake = start_server(Path(workspace) / "data")
        base, token = handshake["url"], handshake["token"]
        try:
            job_id, org_id = seed(base, token)
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

                # ---------------------------------------- 1. from the editor's empty panel
                page.goto(f"{base}/jobs/{job_id}", wait_until="networkidle")
                page.wait_for_selector("[data-auto-annotate]", timeout=60_000)
                link = page.locator("[data-auto-annotate-add]")
                check(link.count() == 1,
                      "with no model, the editor's panel links to where one is added",
                      "the empty Auto-annotate panel offers nowhere to add a model")
                if link.count() == 1:
                    link.click()
                else:
                    page.goto(f"{base}/models", wait_until="networkidle")
                page.wait_for_selector("[data-models-page]", timeout=30_000)
                check(page.locator("[data-models-empty]").count() == 1,
                      "the Models page says none is registered",
                      "the Models page does not say none is registered")

                endpoint_box = page.locator("[data-model-endpoint]")
                key_box = page.locator("[data-model-key]")
                connect = page.locator("[data-model-connect]")
                details = page.locator("[data-model-details]")
                add = page.locator("[data-model-register]")

                # ---------------------------------------- 2. a wrong key
                endpoint_box.fill(endpoint)
                key_box.fill("not-the-key")
                connect.click()
                page.wait_for_timeout(1500)
                notice = page.locator("[data-add-model]").inner_text()
                print(f"  wrong key: {notice.splitlines()[-1]!r}")
                check("API key" in notice and "401" in notice,
                      "a wrong key is refused before anything is saved, and says to check it",
                      f"a wrong key did not say so: {notice!r}")
                check(details.count() == 0 and add.count() == 0,
                      "and nothing can be added with it",
                      "the form offered to add a model whose key was refused")

                # ---------------------------------------- 3. the right key
                key_box.fill(KEY)
                connect.click()
                page.wait_for_selector("[data-model-details]", timeout=15_000)
                said = page.locator("[data-model-discovery]").inner_text().strip()
                print(f"  discovery: {said!r}")
                check("2 models" in said,
                      "the right key connects, and the endpoint's two models are offered",
                      f"after connecting the page says {said!r}")
                offered = page.locator("[data-model-found]")
                check(offered.count() == 2,
                      "one choice per model the endpoint serves",
                      f"{offered.count()} choices for two models")
                check(add.is_disabled(),
                      "nothing can be added before one is picked",
                      "Add model is enabled with no model picked")

                # ---------------------------------------- 4. pick one
                page.locator("[data-model-found='street-v1']").click()
                page.wait_for_timeout(200)
                name = page.locator("[data-model-name]").input_value()
                labels = page.locator("[data-model-labels-input]").input_value()
                kind = page.locator("[data-model-kind]").input_value()
                print(f"  filled in: {name!r}, {kind!r}, {labels!r}")
                check((name, kind, labels) == ("Street Detector", "detector", "car, person"),
                      "picking one fills in its name, kind and labels from the endpoint",
                      f"picking street-v1 filled in {(name, kind, labels)!r}")
                check(not add.is_disabled(),
                      "and it can be added",
                      "Add model stays disabled for a filled-in model")

                # ---------------------------------------- 5. the key edited after connecting
                key_box.fill(KEY + "x")
                page.wait_for_timeout(200)
                check(details.count() == 0,
                      "editing the key after connecting needs connecting again",
                      "a key nobody has seen work could still be saved")
                key_box.fill(KEY)
                page.wait_for_timeout(200)

                # ---------------------------------------- 6. add it
                check(details.count() == 1,
                      "the key it connected with brings the details back",
                      "restoring the connected key did not restore the form")
                add.click()
                page.wait_for_selector("[data-model-registered]", timeout=15_000)
                card = page.locator("[data-model-card='street-detector']")
                page.wait_for_timeout(500)
                check(card.count() == 1,
                      "the model is listed once added",
                      "no card for the added model")
                if card.count() == 1:
                    shown = card.locator("[data-model-labels]").inner_text().split()
                    check(shown == ["car", "person"],
                          "with the labels it returns",
                          f"the card shows labels {shown!r}")
                check(KEY not in page.content(),
                      "the key is not in the page",
                      "the API key is rendered in the Models page")
                listed = api(base, token, f"/models?organization_id={org_id}")
                check(KEY not in json.dumps(listed),
                      "nor in anything the API reads back",
                      "the API key is returned by GET /models")
                [model] = listed if len(listed) == 1 else [None]
                check(model is not None and model["output_labels"] == ["car", "person"],
                      "the registration holds what discovery found",
                      f"GET /models returned {listed!r}")

                # ---------------------------------------- 7. offered in the editor
                other = api(base, token, "/organizations",
                            {"slug": "elsewhere", "name": "Elsewhere"})
                api(base, token, f"/models?organization_id={other['id']}", {
                    "slug": "elsewhere-model", "name": "Elsewhere Model", "kind": "detector",
                    "provider": "http", "config": {"endpoint": endpoint},
                    "output_labels": ["car"],
                })
                page.goto(f"{base}/jobs/{job_id}", wait_until="networkidle")
                page.wait_for_selector("[data-auto-annotate-model]", timeout=60_000)
                picker = page.locator("[data-auto-annotate-model]")
                options = picker.locator("option").all_inner_texts()
                check(any("Street Detector" in option for option in options),
                      "the editor offers the model just added",
                      f"the editor's model picker offers {options!r}")
                check(not any("Elsewhere" in option for option in options),
                      "and not one from another workspace, which this job cannot run",
                      f"the editor offers another workspace's model: {options!r}")

                # ---------------------------------------- 8. run it
                picker.select_option(label="Street Detector")
                before = len(ModelServer.received)
                page.locator("[data-auto-annotate-run]").click()
                page.wait_for_timeout(3000)
                runs = [r for r in ModelServer.received[before:] if r[0] == "POST"]
                check(len(runs) == 1,
                      "running it reaches the model server",
                      f"the model server saw {len(runs)} inference requests")
                if runs:
                    _method, _path, authorization, body = runs[0]
                    check(authorization == f"Bearer {KEY}",
                          "with the key",
                          f"the inference request carried {authorization!r}")
                    check(body.get("model") == "street-v1",
                          "asking for the model by the id the endpoint gave it",
                          f"the inference request asked for model {body.get('model')!r}")
                stored = api(base, token, f"/jobs/{job_id}/annotations")["shapes"]
                check(len(stored) == 1 and stored[0]["source"] == "model",
                      "what it found is stored as a suggestion",
                      f"stored after the run: {stored!r}")

                # ---------------------------------------- 9. remove it
                page.goto(f"{base}/models", wait_until="networkidle")
                page.wait_for_selector("[data-models-page]", timeout=30_000)
                # Two workspaces by now, so the page asks which one.
                page.locator("select[aria-label='Workspace']").select_option(value=org_id)
                street = page.locator("[data-model-card='street-detector']")
                street.wait_for(timeout=15_000)
                street.locator("[data-model-remove]").click()
                street.locator("[data-model-remove-confirm]").click()
                page.wait_for_selector("[data-models-empty]", timeout=15_000)
                check(api(base, token, f"/models?organization_id={org_id}") == [],
                      "removing it removes it",
                      "the model is still registered after removing it")

                check(not raised, "nothing raised in the page", f"page errors: {raised}")
                shot = Path("/tmp/curvevision-models.png")
                page.screenshot(path=str(shot), full_page=True)
                print(f"  (screenshot: {shot})")
                browser.close()
        finally:
            process.terminate()
            process.wait(timeout=30)
            httpd.shutdown()

    print()
    if failures:
        print(f"{len(failures)} check(s) failed")
        return 1
    print("all checks passed")
    return 0


if __name__ == "__main__":
    sys.exit(main())
