#!/usr/bin/env python3
"""Run a workspace's webhooks from the application, against a receiver that checks them.

    python scripts/verify_webhooks.py

Until iteration 96 no webhook had ever been sent anything, and until iteration 97 there was
no way to manage one but the API. This drives the Workspace page in Chromium against the
real server, with an HTTP receiver of its own that checks every delivery's signature:

1. adding a webhook shows its signing secret once;
2. *Send a test* reaches the receiver, signed with that secret, and the page says so;
3. a change made in the workspace reaches the receiver as the event subscribed to;
4. a receiver that refuses is reported as refusing, and not retried;
5. the recent deliveries list what happened;
6. a paused webhook is sent nothing;
7. an annotator is not offered webhooks at all;
8. deleting one removes it.

Needs the built web application (`npm --prefix web run build`) and a Chromium.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import sys
import tempfile
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).parent))

from screenshot import api, find_chromium
from verify_workspace import PASSWORD, anonymous, free_port, start_web_server


class Receiver:
    """An integrator's endpoint: keeps what arrives, answers with `status`."""

    def __init__(self) -> None:
        self.status = 200
        self.received: list[dict[str, Any]] = []
        receiver = self

        class Handler(BaseHTTPRequestHandler):
            def do_POST(self) -> None:
                body = self.rfile.read(int(self.headers.get("Content-Length", 0)))
                receiver.received.append(
                    {
                        "event": self.headers.get("X-CurveVision-Event"),
                        "signature": self.headers.get("X-CurveVision-Signature", ""),
                        "body": body,
                    }
                )
                self.send_response(receiver.status)
                self.end_headers()
                if receiver.status >= 400:
                    self.wfile.write(b"signature rejected by the receiver")

            def log_message(self, *_: object) -> None:
                pass

        self.server = ThreadingHTTPServer(("127.0.0.1", free_port()), Handler)
        self.url = f"http://127.0.0.1:{self.server.server_port}/curvevision"
        threading.Thread(target=self.server.serve_forever, daemon=True).start()

    def events(self) -> list[str]:
        return [str(entry["event"]) for entry in self.received]

    def wait_for(self, event: str, timeout: float = 20) -> dict[str, Any] | None:
        deadline = time.time() + timeout
        while time.time() < deadline:
            for entry in self.received:
                if entry["event"] == event:
                    return entry
            time.sleep(0.2)
        return None


def genuine(entry: dict[str, Any], secret: str) -> bool:
    expected = "sha256=" + hmac.new(secret.encode(), entry["body"], hashlib.sha256).hexdigest()
    return hmac.compare_digest(entry["signature"], expected)


def main() -> int:
    from playwright.sync_api import sync_playwright

    failures: list[str] = []

    def check(condition: bool, passed: str, failed: str) -> None:
        if condition:
            print(f"  ok   {passed}")
        else:
            failures.append(failed)
            print(f"  FAIL {failed}")

    receiver = Receiver()
    with tempfile.TemporaryDirectory(prefix="curvevision-webhooks-") as workdir:
        server, base = start_web_server(Path(workdir))
        try:
            for name in ("founder", "finn"):
                anonymous(
                    base,
                    "/auth/register",
                    {"username": name, "email": f"{name}@example.com", "password": PASSWORD},
                )
            sessions = {
                name: anonymous(base, "/auth/login", {"identifier": name, "password": PASSWORD})
                for name in ("founder", "finn")
            }
            token = sessions["founder"]["access_token"]
            org = api(base, token, "/organizations", {"slug": "acme", "name": "Acme"})
            api(
                base,
                token,
                f"/organizations/{org['id']}/members",
                {"identifier": "finn", "role": "annotator"},
            )
            project = api(
                base,
                token,
                "/projects",
                {"organization_id": org["id"], "slug": "street", "name": "Street"},
            )

            with sync_playwright() as p:
                launch: dict[str, object] = {"headless": True}
                if (chromium := find_chromium()) is not None:
                    launch["executable_path"] = chromium
                browser = p.chromium.launch(**launch)

                def page_for(name: str) -> Any:
                    context = browser.new_context(viewport={"width": 1280, "height": 1600})
                    context.add_init_script(
                        "localStorage.setItem('curvevision.access', "
                        f"{json.dumps(sessions[name]['access_token'])});"
                        "localStorage.setItem('curvevision.refresh', "
                        f"{json.dumps(sessions[name]['refresh_token'])});"
                    )
                    return context.new_page()

                page = page_for("founder")
                raised: list[str] = []
                page.on("pageerror", lambda exc: raised.append(str(exc)))

                # ------------------------------------------------------------ 1
                page.goto(f"{base}/workspace", wait_until="networkidle")
                panel = page.locator("[data-webhooks]")
                panel.wait_for(timeout=30_000)
                page.fill("[data-webhook-url]", receiver.url)
                page.fill("[data-webhook-description]", "harness")
                page.check("[data-webhook-event='task.created']")
                page.click("[data-webhook-submit]")
                secret = page.locator("[data-webhook-secret]").inner_text(timeout=15_000).strip()
                row = page.locator(f"[data-webhook='{receiver.url}']")
                row.wait_for(timeout=15_000)
                page.reload(wait_until="networkidle")
                row.wait_for(timeout=15_000)
                check(
                    len(secret) >= 32 and page.locator("[data-webhook-secret]").count() == 0,
                    "a new webhook's signing secret is shown once",
                    f"secret {secret!r}; after a reload the secret shown "
                    f"{page.locator('[data-webhook-secret]').count()} time(s)",
                )

                # ------------------------------------------------------------ 2
                row.locator("[data-webhook-ping]").click()
                result = row.locator("[data-webhook-ping-result]")
                result.wait_for(timeout=20_000)
                ping = receiver.wait_for("ping", 5)
                check(
                    ping is not None
                    and genuine(ping, secret)
                    and result.get_attribute("data-webhook-ping-result") == "success",
                    "Send a test reaches the receiver, signed with that secret, and says so",
                    f"receiver got {receiver.events()}; the page said {result.inner_text()!r}",
                )

                # ------------------------------------------------------------ 3
                api(base, token, "/tasks", {"project_id": project["id"], "name": "Batch 7"})
                created = receiver.wait_for("task.created")
                data = json.loads(created["body"])["data"] if created else {}
                check(
                    created is not None
                    and genuine(created, secret)
                    and data.get("task", {}).get("name") == "Batch 7",
                    "a task created in the workspace reaches the receiver as task.created",
                    f"receiver got {receiver.events()}",
                )

                # ------------------------------------------------------------ 4
                receiver.status = 401
                before = len(receiver.received)
                row.locator("[data-webhook-ping]").click()
                # A locator, not wait_for_function: the page's CSP forbids evaluated strings.
                row.locator("[data-webhook-ping-result='danger']").wait_for(timeout=20_000)
                said = result.inner_text()
                time.sleep(2)
                check(
                    "401" in said
                    and "Not retried" in said
                    and len(receiver.received) == before + 1,
                    "a receiver that refuses is reported as refusing, and not retried",
                    f"the page said {said!r}; {len(receiver.received) - before} attempt(s)",
                )
                receiver.status = 200

                # ------------------------------------------------------------ 5
                row.locator("[data-webhook-deliveries-toggle]").click()
                listed = row.locator("[data-webhook-delivery]")
                listed.first.wait_for(timeout=15_000)
                rows = [
                    (
                        item.get_attribute("data-webhook-delivery"),
                        item.get_attribute("data-webhook-delivery-tone"),
                    )
                    for item in listed.all()
                ]
                check(
                    rows[:1] == [("ping", "danger")]
                    and ("task.created", "success") in rows
                    and ("ping", "success") in rows,
                    "the recent deliveries list what each receiver answered, newest first",
                    f"listed {rows}",
                )

                # ------------------------------------------------------------ 6
                row.locator("[data-webhook-toggle]").click()
                row.locator("[data-webhook-paused]").wait_for(timeout=15_000)
                before = len(receiver.received)
                api(base, token, "/tasks", {"project_id": project["id"], "name": "Batch 8"})
                time.sleep(3)
                check(
                    len(receiver.received) == before,
                    "a paused webhook is sent nothing",
                    f"while paused the receiver got {receiver.events()[before:]}",
                )

                # ------------------------------------------------------------ 7
                annotator = page_for("finn")
                annotator.goto(f"{base}/workspace", wait_until="networkidle")
                annotator.locator("[data-workspace-page]").wait_for(timeout=30_000)
                annotator.wait_for_timeout(1000)
                check(
                    annotator.locator("[data-webhooks]").count() == 0,
                    "an annotator is not offered webhooks",
                    "the annotator's Workspace page shows the webhooks panel",
                )

                # ------------------------------------------------------------ 8
                row.locator("[data-webhook-delete]").click()
                row.locator("[data-webhook-delete-confirm]").click()
                row.wait_for(state="detached", timeout=15_000)
                remaining = api(base, token, f"/webhooks?organization_id={org['id']}")
                check(
                    remaining == [],
                    "deleting a webhook removes it",
                    f"the server still has {remaining}",
                )

                check(not raised, "no page errors", f"page errors: {raised}")
                browser.close()
        finally:
            server.terminate()
            receiver.server.shutdown()

    print()
    if failures:
        print(f"{len(failures)} check(s) failed")
        return 1
    print("every check passed")
    return 0


if __name__ == "__main__":
    sys.exit(main())
