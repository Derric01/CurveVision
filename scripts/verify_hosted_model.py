#!/usr/bin/env python3
"""Add a hosted AI model with an API key, and run it in the editor (ADR 0010).

    python scripts/verify_hosted_model.py

The Models page offers Claude, OpenAI, Gemini, xAI, OpenRouter and others by API key. No
real service is called here — that would need a key and a bill — so the service is a local
HTTP server speaking the OpenAI Chat Completions shape, added through *Other
OpenAI-compatible service*: the same provider, request and parsing every OpenAI-shaped
preset uses, reached over real HTTP by the packaged server. Claude's provider is pinned at
the SDK level by `tests/unit/test_hosted_vision.py`.

1. The form opens on hosted services, and says where frames go before anything is added.
2. A wrong key is refused at Connect, with the reason.
3. The right key lists the models that take images, and leaves out the ones that plainly do
   not (an embedding model).
4. Adding one lists it as a hosted, OpenAI-compatible detector, key not shown.
5. Running it from the editor sends the frame as an image, with the key, asking for the
   project's own labels by name; the box it answers in 0-1000 coordinates is stored as a
   suggestion in the frame's pixels.

Needs the packaged server (`python desktop/sidecar/build.py`) and a Chromium.
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

KEY = "sk-harness-31f9"
WIDTH, HEIGHT = 480, 320
#: What the fake model answers: a car over the middle of the frame, in 0-1000 coordinates.
ANSWER = {"objects": [{"label": "car", "box": [250, 250, 750, 500], "confidence": 0.9}]}
EXPECTED = [120.0, 80.0, 360.0, 160.0]


class Service(BaseHTTPRequestHandler):
    """An OpenAI-shaped service that wants a key."""

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
        Service.received.append((self.command, self.path, authorization, body))
        if authorization != f"Bearer {KEY}":
            self._answer(401, {"error": {"message": "Incorrect API key provided"}})
            return False
        return True

    def do_GET(self) -> None:
        if not self._authorised({}):
            return
        if self.path == "/v1/models":
            self._answer(200, {"object": "list", "data": [
                {"id": "vision-large", "object": "model"},
                {"id": "text-embedding-3-small", "object": "model"},
            ]})
        else:
            self._answer(404, {"error": {"message": "not found"}})

    def do_POST(self) -> None:
        length = int(self.headers.get("Content-Length", "0"))
        body = json.loads(self.rfile.read(length) or b"{}")
        if not self._authorised(body):
            return
        message = {"role": "assistant", "content": f"```json\n{json.dumps(ANSWER)}\n```"}
        self._answer(200, {"choices": [{"index": 0, "message": message}]})

    def log_message(self, *_args) -> None:
        """Quiet: the harness's own output is the log."""


def png_bytes() -> bytes:
    from PIL import Image

    buffer = io.BytesIO()
    Image.new("RGB", (WIDTH, HEIGHT), "#1e293b").save(buffer, format="PNG")
    return buffer.getvalue()


def seed(base: str, token: str) -> tuple[str, str]:
    org = api(base, token, "/organizations")[0]
    project = api(base, token, "/projects", {
        "organization_id": org["id"], "slug": "hosted", "name": "Hosted",
        "labels": [{"name": "car", "color": "#ef4444"}, {"name": "pedestrian"}],
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

    httpd = HTTPServer(("127.0.0.1", 0), Service)
    base_url = f"http://127.0.0.1:{httpd.server_port}/v1"
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    print(f"an OpenAI-shaped service on {base_url} that wants a key\n")

    with tempfile.TemporaryDirectory(prefix="curvevision-hosted-") as workspace:
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

                # ------------------------------------------------ 1. hosted by default
                page.goto(f"{base}/models", wait_until="networkidle")
                page.wait_for_selector("[data-hosted-form]", timeout=30_000)
                services = page.locator("[data-hosted-service] option").all_inner_texts()
                print(f"  services: {services}")
                check(all(any(name in s for s in services)
                          for name in ("Claude", "OpenAI", "Gemini", "xAI", "OpenRouter")),
                      "the form opens on hosted services, Claude, OpenAI, Gemini, xAI and "
                      "OpenRouter among them",
                      f"the hosted services offered are {services}")
                page.locator("[data-hosted-service]").select_option("other")
                page.locator("[data-hosted-base-url]").fill(base_url)

                # ------------------------------------------------ 2. a wrong key
                page.locator("[data-hosted-key]").fill("sk-wrong")
                page.locator("[data-hosted-connect]").click()
                page.wait_for_timeout(1500)
                said = page.locator("[data-hosted-form]").inner_text()
                check("API key" in said and "401" in said,
                      "a wrong key is refused at Connect, and says so",
                      f"a wrong key read: {said[-200:]!r}")
                check(page.locator("[data-hosted-details]").count() == 0,
                      "and nothing can be added with it",
                      "the form offered to add a model whose key was refused")

                # ------------------------------------------------ 3. the right key
                page.locator("[data-hosted-key]").fill(KEY)
                page.locator("[data-hosted-connect]").click()
                page.wait_for_selector("[data-hosted-details]", timeout=15_000)
                listed = page.locator("[data-hosted-model] option").all_inner_texts()
                print(f"  models offered: {listed}")
                check(any("vision-large" in o for o in listed)
                      and not any("embedding" in o for o in listed),
                      "the right key lists the model that takes images, not the embedding one",
                      f"the models offered are {listed}")
                privacy = page.locator("[data-hosted-privacy]")
                check(privacy.count() == 1 and "sent to" in privacy.inner_text(),
                      "where the frames go is said before the model is added",
                      "nothing says the frames leave this computer")

                # ------------------------------------------------ 4. add it
                page.locator("[data-hosted-model]").select_option("vision-large")
                page.locator("[data-hosted-add]").click()
                page.wait_for_selector("[data-model-registered]", timeout=15_000)
                page.wait_for_timeout(400)
                cards = page.locator("[data-model-card]")
                card_text = cards.first.inner_text() if cards.count() else ""
                print(f"  card: {card_text!r}")
                check("OpenAI-compatible" in card_text,
                      "it is listed as a hosted, OpenAI-compatible model",
                      f"the card reads {card_text!r}")
                registered = api(base, token, f"/models?organization_id={org_id}")
                check(KEY not in page.content() and KEY not in json.dumps(registered),
                      "and the key is in neither the page nor what the API reads back",
                      "the API key is shown or returned")

                # ------------------------------------------------ 5. run it
                page.goto(f"{base}/jobs/{job_id}", wait_until="networkidle")
                page.wait_for_selector("[data-auto-annotate-run]", timeout=60_000)
                before = len(Service.received)
                page.locator("[data-auto-annotate-run]").click()
                page.wait_for_timeout(4000)
                asked = [r for r in Service.received[before:] if r[0] == "POST"]
                check(len(asked) == 1, "running it reaches the service once, for one frame",
                      f"the service saw {len(asked)} requests")
                if asked:
                    _m, path, authorization, body = asked[0]
                    content = body["messages"][1]["content"]
                    prompt = next(part["text"] for part in content if part["type"] == "text")
                    image = next(part for part in content if part["type"] == "image_url")
                    check(path == "/v1/chat/completions" and authorization == f"Bearer {KEY}",
                          "at /chat/completions, with the key",
                          f"the request went to {path} with {authorization!r}")
                    check(image["image_url"]["url"].startswith("data:image/jpeg;base64,"),
                          "the frame goes as an image",
                          "the frame was not sent as an image")
                    check('"car"' in prompt and '"pedestrian"' in prompt,
                          "and the model is asked for the project's own labels, by name",
                          f"the prompt was {prompt[:200]!r}")
                stored = api(base, token, f"/jobs/{job_id}/annotations")["shapes"]
                print(f"  stored: {[(s['source'], s['points']) for s in stored]}")
                check(len(stored) == 1 and stored[0]["source"] == "model"
                      and stored[0]["points"] == EXPECTED,
                      "the box it answered is stored as a suggestion, in the frame's pixels",
                      f"stored after the run: {stored!r}")
                check(not raised, "nothing raised in the page", f"page errors: {raised}")
                shot = Path("/tmp/curvevision-hosted-model.png")
                page.screenshot(path=str(shot))
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
