#!/usr/bin/env python3
"""Run a workspace from the application: members, API tokens, renaming and deleting, and
dataset versions.

    python scripts/verify_workspace.py

Each of these had an API and no way in through the app until iteration 66. This drives
the server shape as an admin, the role whose limits matter, then the desktop shape.

1. Members: the admin sees everyone, can change only the members below them, and is
   offered only roles below admin. Adding by email works; adding someone with no account
   says what to do. A role change and a removal reach the server.
2. API tokens: a new token is shown once and works; revoked, it stops working.
3. Dataset versions: a draft is created and released, and its download is the annotations
   as released — after the annotation has been moved.
4. Renaming a task and a project; deleting either takes typing its name, and lands on the
   page above it.
5. The desktop shape lists no members, and will not offer to revoke the token its own
   window runs on.

The server shape runs the real server (uvicorn on SQLite, with the built web application:
`npm --prefix web run build`), since the desktop build has one person and no registration.
The desktop shape needs the packaged server (`python desktop/sidecar/build.py`). Both need a
Chromium.
"""

from __future__ import annotations

import io
import json
import os
import socket
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request
import zipfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

from screenshot import api, find_chromium, start_server

PASSWORD = "correct-horse-battery"
ROOT = Path(__file__).resolve().parent.parent
# The project virtualenv where there is one (a contributor's machine), otherwise whatever
# runs this script — CI installs the server into its own interpreter and has no `.venv`.
_VENV = ROOT / "server" / ".venv" / "bin" / "python"
PYTHON = _VENV if _VENV.is_file() else Path(sys.executable)


def free_port() -> int:
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        return int(probe.getsockname()[1])


def start_web_server(
    workdir: Path, *, port: int | None = None, extra_env: dict[str, str] | None = None
) -> tuple[subprocess.Popen[str], str]:
    """The server shape: the API and the built web application, on SQLite."""
    port = port or free_port()
    env = {
        **os.environ,
        "CURVEVISION_ENVIRONMENT": "development",
        "CURVEVISION_SECRET_KEY": "workspace-harness-secret-key-not-real",
        "CURVEVISION_DATABASE_URL": f"sqlite+aiosqlite:///{workdir / 'db.sqlite'}",
        "CURVEVISION_STORAGE_BACKEND": "local",
        "CURVEVISION_STORAGE_LOCAL_ROOT": str(workdir / "storage"),
        "CURVEVISION_JOB_QUEUE_BACKEND": "inline",
        "CURVEVISION_WEB_ROOT": str(ROOT / "web" / "dist"),
        "CURVEVISION_RATE_LIMIT_PER_MINUTE": "0",
        "CURVEVISION_LOG_LEVEL": "ERROR",
        **(extra_env or {}),
    }
    subprocess.run(
        [str(PYTHON), "-c",
         "import asyncio\n"
         "import curvevision.main  # registers every model on Base\n"
         "from curvevision.core.config import get_settings\n"
         "from curvevision.core.db import Base, create_engine\n"
         "async def main():\n"
         "    engine = create_engine(get_settings())\n"
         "    async with engine.begin() as c:\n"
         "        await c.run_sync(Base.metadata.create_all)\n"
         "    await engine.dispose()\n"
         "asyncio.run(main())\n"],
        env=env, check=True, cwd=ROOT / "server",
    )
    process = subprocess.Popen(
        [str(PYTHON), "-m", "uvicorn", "curvevision.main:app", "--port", str(port),
         "--log-level", "warning"],
        env=env, cwd=ROOT / "server", text=True,
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
    )
    base = f"http://127.0.0.1:{port}"
    for _ in range(200):
        try:
            urllib.request.urlopen(f"{base}/api/v1/health", timeout=2)
            return process, base
        except OSError:
            time.sleep(0.1)
    process.terminate()
    raise SystemExit("the server did not start")


def anonymous(base: str, path: str, payload: object) -> dict:
    request = urllib.request.Request(
        f"{base}/api/v1{path}", data=json.dumps(payload).encode(), method="POST",
        headers={"Content-Type": "application/json"},
    )
    with urllib.request.urlopen(request, timeout=30) as response:
        return json.loads(response.read())


def png_bytes() -> bytes:
    from PIL import Image

    buffer = io.BytesIO()
    Image.new("RGB", (160, 120), "#1e293b").save(buffer, format="PNG")
    return buffer.getvalue()


def status_with(base: str, token: str, path: str) -> int:
    request = urllib.request.Request(f"{base}/api/v1{path}")
    request.add_header("Authorization", f"Bearer {token}")
    try:
        with urllib.request.urlopen(request, timeout=30) as response:
            return int(response.status)
    except urllib.error.HTTPError as exc:
        return int(exc.code)


def main() -> int:
    from playwright.sync_api import sync_playwright

    failures: list[str] = []

    def check(condition: bool, passed: str, failed: str) -> None:
        if condition:
            print(f"  ok   {passed}")
        else:
            failures.append(failed)
            print(f"  FAIL {failed}")

    with tempfile.TemporaryDirectory(prefix="curvevision-workspace-") as workspace:
        server, base = start_web_server(Path(workspace))
        process, handshake = start_server(Path(workspace) / "data")
        try:
            for name in ("founder", "dana", "eli", "finn"):
                anonymous(base, "/auth/register", {
                    "username": name, "email": f"{name}@example.com", "password": PASSWORD,
                })
            token = anonymous(base, "/auth/login",
                              {"identifier": "founder", "password": PASSWORD})["access_token"]
            org = api(base, token, "/organizations", {"slug": "acme", "name": "Acme"})
            for name, role in (("dana", "admin"), ("finn", "annotator")):
                api(base, token, f"/organizations/{org['id']}/members",
                    {"identifier": name, "role": role})
            session = anonymous(base, "/auth/login", {"identifier": "dana", "password": PASSWORD})

            project = api(base, token, "/projects", {
                "organization_id": org["id"], "slug": "street", "name": "Street",
                "labels": [{"name": "car"}],
            })
            task = api(base, token, "/tasks", {
                "project_id": project["id"], "name": "Batch 1", "media_kind": "image",
            })
            api(base, token, f"/tasks/{task['id']}/assets", files=png_bytes(),
                filename="a.png", content_type="image/png")
            job = api(base, token, f"/tasks/{task['id']}/jobs")[0]
            car = project["labels"][0]["id"]
            api(base, token, f"/jobs/{job['id']}/annotations", {"created_shapes": [{
                "label_id": car, "frame": 0, "shape_type": "rectangle",
                "points": [10, 10, 50, 40],
            }]}, method="PATCH")

            with sync_playwright() as p:
                launch: dict[str, object] = {"headless": True}
                if (chromium := find_chromium()) is not None:
                    launch["executable_path"] = chromium
                browser = p.chromium.launch(**launch)
                context = browser.new_context(
                    viewport={"width": 1280, "height": 1400}, accept_downloads=True
                )
                context.add_init_script(
                    "localStorage.setItem('curvevision.access', "
                    f"{json.dumps(session['access_token'])});"
                    "localStorage.setItem('curvevision.refresh', "
                    f"{json.dumps(session['refresh_token'])});"
                )
                page = context.new_page()
                raised: list[str] = []
                page.on("pageerror", lambda exc: raised.append(str(exc)))

                # ------------------------------------------------------ 1. members
                page.goto(f"{base}/workspace", wait_until="networkidle")
                page.locator("[data-members-list]").wait_for(timeout=30_000)
                rows = page.locator("[data-member]")
                listed = [rows.nth(i).get_attribute("data-member") for i in range(rows.count())]
                print(f"  members: {listed}")
                check({"dana", "finn"} <= set(listed) and len(listed) == 3,
                      "the admin sees everyone in the workspace",
                      f"members listed: {listed}")
                owner_row = page.locator("[data-member]").first
                check(owner_row.locator("[data-member-role]").count() == 0
                      and page.locator("[data-member='dana'] [data-member-role]").count() == 0,
                      "the owner and the admin themselves cannot be changed by the admin",
                      "the admin was offered a role control on the owner or on themselves")
                finn_roles = page.locator(
                    "[data-member='finn'] [data-member-role] option").all_inner_texts()
                offered = page.locator("[data-member-new-role] option").all_inner_texts()
                check("admin" not in finn_roles and "owner" not in offered
                      and "maintainer" in offered,
                      "only roles below admin are offered",
                      f"offered {finn_roles} and {offered}")

                page.locator("[data-member-identifier]").fill("eli@example.com")
                page.locator("[data-member-new-role]").select_option("reviewer")
                page.locator("[data-member-add-submit]").click()
                page.locator("[data-member='eli']").wait_for(timeout=15_000)
                check(True, "someone is added by email", "")
                page.locator("[data-member-identifier]").fill("nobody@example.com")
                page.locator("[data-member-add-submit]").click()
                page.locator("[data-member-add-failure]").wait_for(timeout=15_000)
                failure = page.locator("[data-member-add-failure]").inner_text()
                check("sign up" in failure, "an unknown email says what to do",
                      f"the panel said {failure!r}")

                page.locator("[data-member='finn'] [data-member-role]").select_option(
                    "maintainer")
                page.wait_for_timeout(800)
                roles = {m["user"]["username"]: m["role"] for m in
                         api(base, token, f"/organizations/{org['id']}/members")}
                check(roles.get("finn") == "maintainer", "a role change reaches the server",
                      f"finn is {roles.get('finn')}")
                page.locator("[data-member='finn'] [data-member-remove]").click()
                page.locator("[data-member='finn'] [data-member-remove-confirm]").click()
                page.locator("[data-member='finn']").wait_for(state="detached", timeout=15_000)
                roles = {m["user"]["username"] for m in
                         api(base, token, f"/organizations/{org['id']}/members")}
                check("finn" not in roles, "a removal reaches the server",
                      f"members after removal: {roles}")

                # ------------------------------------------------------ 2. API tokens
                page.locator("[data-api-token-name]").fill("ci pipeline")
                page.locator("[data-api-token-submit]").click()
                page.locator("[data-api-token-secret]").wait_for(timeout=15_000)
                secret = page.locator("[data-api-token-secret]").inner_text().strip()
                check(secret.startswith("cv_") and status_with(base, secret, "/auth/me") == 200,
                      "a new token is shown once, and works",
                      f"token {secret[:12]!r}… answered {status_with(base, secret, '/auth/me')}")
                row = page.locator("[data-api-token='ci pipeline']")
                row.locator("[data-api-token-revoke]").click()
                page.wait_for_timeout(800)
                check(status_with(base, secret, "/auth/me") == 401,
                      "revoked, it stops working",
                      "a revoked token still authenticates")

                # ------------------------------------------------------ 3. versions
                page.goto(f"{base}/projects/{project['id']}", wait_until="networkidle")
                page.locator("[data-versions-panel]").wait_for(timeout=30_000)
                page.locator("[data-version-name]").fill("v1.0")
                page.locator("[data-version-create-submit]").click()
                version = page.locator("[data-version='v1.0']")
                version.wait_for(timeout=15_000)
                version.locator("[data-version-release]").click()
                version.locator("[data-version-release-confirm]").click()
                version.locator("[data-version-hash]").wait_for(timeout=15_000)
                summary = version.locator("[data-version-summary]").inner_text()
                check(summary.startswith("1 frame · 1 annotation"),
                      f"the release says what it holds ({summary!r})",
                      f"the release summary was {summary!r}")
                document = api(base, token, f"/jobs/{job['id']}/annotations")
                moved = {**document["shapes"][0], "points": [80, 60, 120, 100]}
                api(base, token, f"/jobs/{job['id']}/annotations",
                    {"updated_shapes": [moved]}, method="PATCH")
                version.locator("[data-version-format]").select_option("coco")
                with page.expect_download(timeout=30_000) as download:
                    version.locator("[data-version-download]").click()
                path = download.value.path()
                with zipfile.ZipFile(path) as archive:
                    coco = json.loads(archive.read("annotations/instances_default.json"))
                boxes = [a["bbox"] for a in coco["annotations"]]
                page.screenshot(path="/tmp/curvevision-versions.png", full_page=True)
                check(boxes == [[10.0, 10.0, 40.0, 30.0]],
                      "the release downloads as released, after the box was moved",
                      f"the release downloaded {boxes}")

                # ------------------------------------------------------ 4. rename, delete
                page.goto(f"{base}/tasks/{task['id']}", wait_until="networkidle")
                page.locator("[data-manage-rename-open]").click()
                page.locator("[data-manage-rename-input]").fill("Batch 1 — checked")
                page.locator("[data-manage-rename-save]").click()
                page.wait_for_timeout(800)
                renamed = api(base, token, f"/tasks/{task['id']}")["name"]
                check(renamed == "Batch 1 — checked", "a task is renamed",
                      f"the task is named {renamed!r}")
                page.locator("[data-manage-delete-open]").click()
                confirm = page.locator("[data-manage-delete-confirm]")
                page.locator("[data-manage-delete-input]").fill("Batch 1")
                held = confirm.is_disabled()
                page.locator("[data-manage-delete-input]").fill("Batch 1 — checked")
                page.screenshot(path="/tmp/curvevision-delete-task.png")
                check(held and confirm.is_enabled(),
                      "deleting waits for the whole name to be typed",
                      "delete was enabled before the name was typed")
                confirm.click()
                page.wait_for_url(f"**/projects/{project['id']}", timeout=15_000)
                tasks = api(base, token, f"/tasks?project_id={project['id']}")["results"]
                check(tasks == [], "the task is deleted and the page returns to its project",
                      f"tasks left: {tasks}")

                page.locator("[data-manage-rename-open]").click()
                page.locator("[data-manage-rename-input]").fill("Street scenes")
                page.locator("[data-manage-rename-save]").click()
                page.wait_for_timeout(800)
                check(page.locator("h1", has_text="Street scenes").count() == 1,
                      "a project is renamed", "the project header did not change")
                page.locator("[data-manage-delete-open]").click()
                page.locator("[data-manage-delete-input]").fill("Street scenes")
                page.locator("[data-manage-delete-confirm]").click()
                page.wait_for_url("**/projects", timeout=15_000)
                left = api(base, token, "/projects")["results"]
                check(all(p["id"] != project["id"] for p in left),
                      "the project is deleted and the page returns to the list",
                      "the project still exists")

                check(not raised, "nothing raised in the page", f"page errors: {raised}")
                shot = Path("/tmp/curvevision-workspace.png")
                page.goto(f"{base}/workspace", wait_until="networkidle")
                page.screenshot(path=str(shot), full_page=True)
                print(f"  (screenshot: {shot})")
                context.close()

                # ------------------------------------------------------ 5. desktop
                local, token = handshake["url"], handshake["token"]
                injection = json.dumps({
                    "url": local, "token": token, "data_dir": handshake["data_dir"],
                    "version": handshake["version"], "desktop": True,
                })
                desktop = browser.new_page(viewport={"width": 1280, "height": 1000})
                desktop.add_init_script(f"window.__CURVEVISION__ = {injection};")
                desktop.goto(f"{local}/workspace", wait_until="networkidle")
                desktop.locator("[data-api-tokens]").wait_for(timeout=30_000)
                desktop.wait_for_timeout(500)
                check(desktop.locator("[data-members-panel]").count() == 0,
                      "the desktop shape lists no members",
                      "the desktop shape shows a members panel")
                mine = desktop.locator("[data-api-token]", has_text="this window")
                check(mine.count() == 1 and mine.locator("[data-api-token-revoke]").count() == 0,
                      "and will not offer to revoke the token its window runs on",
                      f"{mine.count()} row(s) marked as this window, revocable")
                desktop.screenshot(path="/tmp/curvevision-workspace-desktop.png", full_page=True)
                browser.close()
        finally:
            process.terminate()
            process.wait(timeout=30)
            server.terminate()
            server.wait(timeout=30)

    print()
    if failures:
        print(f"{len(failures)} check(s) failed")
        return 1
    print("all checks passed")
    return 0


if __name__ == "__main__":
    sys.exit(main())
