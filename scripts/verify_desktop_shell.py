#!/usr/bin/env python3
"""Drive the real desktop application — the Tauri shell and its webview — over WebDriver.

    python scripts/verify_desktop_shell.py [path/to/curvevision-desktop]

Every other harness drives Chromium against the server, standing in for the shell's
transport, because Playwright cannot drive the Tauri webview (ADR 0008). On Linux it can be
driven another way: `tauri-driver` speaks WebDriver and hands the session to WebKitGTK's
own `WebKitGTKDriver`, which controls the very webview the application ships. This runs the
built shell under Xvfb with an isolated home, and a team server from source, and proves
what was until now only claimed:

1. the application starts its server and opens it signed in — the injected connection is
   there, on a loopback origin;
2. the IPC bridge works: the page's `invoke('connection')` reaches the shell and returns
   that same connection;
3. *Connect to a team server* through the page: the shell opens the team server in a
   window of its own and closes this computer's — the page there is the team server's own
   sign-in, with **no** connection injected;
4. that window cannot use the shell: a command from it is refused by Tauri;
5. signing in there works;
6. the choice is remembered — written to the application's config folder, and the next
   launch opens the team server directly;
7. quitting the application stops the server it started;
8. the webview keeps what a page stores (IndexedDB) from one launch to the next — the
   property the editor's crash recovery rests on (`features/editor/drafts.ts`).

Not covered: the native menu (File ▸ Use This Computer), which WebDriver cannot click; its
handler shares `switch_to_local` with the code exercised here.

Needs: Linux, Xvfb, `WebKitWebDriver` (Debian/Ubuntu: `webkit2gtk-driver`), `tauri-driver`
(`cargo install tauri-driver --locked`), the shell built with `cargo build` and the packaged
server beside it (`desktop/sidecar/build.py`, then copy `curvevision-local` next to
`curvevision-desktop`), and the built web application.
"""

from __future__ import annotations

import contextlib
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any

from verify_workspace import ROOT, free_port, start_web_server

DEFAULT_APP = ROOT / "desktop" / "shell" / "src-tauri" / "target" / "debug" / "curvevision-desktop"
IDENTIFIER = "dev.curvevision.desktop"  # tauri.conf.json: where the config folder is named
PASSWORD = "desktop-shell-password-1"


class WebDriver:
    """The few WebDriver commands this needs, over plain HTTP."""

    def __init__(self, port: int) -> None:
        self.base = f"http://127.0.0.1:{port}"
        self.session = ""

    def call(self, method: str, path: str, body: object = None) -> Any:
        request = urllib.request.Request(
            f"{self.base}{path}", method=method,
            data=None if body is None else json.dumps(body).encode(),
            headers={"Content-Type": "application/json"},
        )
        try:
            with urllib.request.urlopen(request, timeout=180) as response:
                return json.loads(response.read())["value"]
        except urllib.error.HTTPError as exc:
            raise RuntimeError(f"{method} {path}: {exc.read().decode()[:400]}") from None

    def start(self, application: Path) -> None:
        value = self.call("POST", "/session", {"capabilities": {"alwaysMatch": {
            "tauri:options": {"application": str(application)}}}})
        self.session = value["sessionId"]

    def quit(self) -> None:
        if self.session:
            self.call("DELETE", f"/session/{self.session}")
            self.session = ""

    def s(self, path: str) -> str:
        return f"/session/{self.session}{path}"

    def run(self, script: str, *args: object) -> Any:
        return self.call("POST", self.s("/execute/sync"), {"script": script, "args": list(args)})

    def run_async(self, script: str, *args: object) -> Any:
        return self.call("POST", self.s("/execute/async"), {"script": script, "args": list(args)})

    def url(self) -> str:
        return str(self.call("GET", self.s("/url")))

    def handles(self) -> list[str]:
        return list(self.call("GET", self.s("/window/handles")))

    def switch_to(self, handle: str) -> None:
        self.call("POST", self.s("/window"), {"handle": handle})

    def find(self, css: str) -> str:
        element = self.call("POST", self.s("/element"), {"using": "css selector", "value": css})
        return str(next(iter(element.values())))

    def wait_for(self, css: str, timeout: float = 30) -> str:
        deadline = time.time() + timeout
        while True:
            try:
                return self.find(css)
            except RuntimeError:
                if time.time() > deadline:
                    raise
                time.sleep(0.25)

    def click(self, css: str) -> None:
        self.call("POST", self.s(f"/element/{self.wait_for(css)}/click"), {})

    def type(self, css: str, text: str) -> None:
        element = self.wait_for(css)
        self.call("POST", self.s(f"/element/{element}/clear"), {})
        self.call("POST", self.s(f"/element/{element}/value"), {"text": text})

    def text(self, css: str) -> str:
        return str(self.call("GET", self.s(f"/element/{self.wait_for(css)}/text")))


# A database of the harness's own, beside the editor's (`curvevision-drafts`): the property
# under test is the origin's storage surviving a relaunch, not the editor's use of it.
MARK_WRITE = """
const [value, done] = [arguments[0], arguments[arguments.length - 1]];
const open = indexedDB.open('curvevision-harness', 1);
open.onupgradeneeded = () => open.result.createObjectStore('marks');
open.onerror = () => done('could not open: ' + open.error);
open.onsuccess = () => {
  const tx = open.result.transaction('marks', 'readwrite');
  tx.objectStore('marks').put(value, 'mark');
  tx.oncomplete = () => done('stored');
  tx.onerror = () => done('could not write: ' + tx.error);
};
"""
MARK_READ = """
const done = arguments[arguments.length - 1];
const open = indexedDB.open('curvevision-harness', 1);
open.onupgradeneeded = () => open.result.createObjectStore('marks');
open.onerror = () => done('could not open: ' + open.error);
open.onsuccess = () => {
  const get = open.result.transaction('marks').objectStore('marks').get('mark');
  get.onsuccess = () => done(get.result ?? null);
  get.onerror = () => done('could not read: ' + get.error);
};
"""


def until(condition: Any, timeout: float = 30) -> Any:
    deadline = time.time() + timeout
    while time.time() < deadline:
        if result := condition():
            return result
        time.sleep(0.25)
    return None


def servers_for(data_dir: Path) -> list[str]:
    """Process ids of packaged servers using `data_dir`'s installation."""
    found = subprocess.run(["pgrep", "-f", "curvevision-local"], capture_output=True, text=True)
    pids = []
    for pid in found.stdout.split():
        try:
            environ = Path(f"/proc/{pid}/environ").read_bytes()
        except OSError:
            continue
        if str(data_dir).encode() in environ:
            pids.append(pid)
    return pids


def main() -> int:
    application = Path(sys.argv[1]) if len(sys.argv) > 1 else DEFAULT_APP
    for tool in ("Xvfb", "tauri-driver", "WebKitWebDriver"):
        if shutil.which(tool) is None:
            raise SystemExit(f"{tool} is not installed; see this script's docstring")
    if not application.is_file():
        raise SystemExit(f"no shell at {application}: cargo build in desktop/shell/src-tauri")

    failures: list[str] = []

    def check(condition: bool, passed: str, failed: str) -> None:
        if condition:
            print(f"  ok   {passed}")
        else:
            failures.append(failed)
            print(f"  FAIL {failed}")

    with tempfile.TemporaryDirectory(prefix="curvevision-shell-") as workdir:
        home = Path(workdir)
        (home / "team").mkdir()
        team, team_base = start_web_server(home / "team")
        request = urllib.request.Request(
            f"{team_base}/api/v1/auth/register", method="POST",
            data=json.dumps({"email": "kim@example.com", "username": "kim",
                             "password": PASSWORD}).encode(),
            headers={"Content-Type": "application/json"},
        )
        urllib.request.urlopen(request, timeout=30).close()

        display = f":{free_port() % 400 + 100}"
        env = {
            **os.environ, "DISPLAY": display, "HOME": str(home),
            "XDG_DATA_HOME": str(home / "data"), "XDG_CONFIG_HOME": str(home / "config"),
        }
        remembered = home / "config" / IDENTIFIER / "connection.json"
        xvfb = subprocess.Popen(["Xvfb", display, "-screen", "0", "1600x1000x24"],
                                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        port = free_port()
        driver_log = (home / "driver.log").open("w")
        driver = subprocess.Popen(["tauri-driver", "--port", str(port)], env=env,
                                  stdout=driver_log, stderr=subprocess.STDOUT)
        wd = WebDriver(port)
        crashed = False
        try:
            until(lambda: _answers(port), timeout=30)

            # ------------------------------------------------------------ 1
            wd.start(application)
            injected = until(lambda: wd.run("return window.__CURVEVISION__ || null"), 60) or {}
            local = str(injected.get("url", ""))
            check(injected.get("desktop") is True and local.startswith("http://127.0.0.1:")
                  and wd.url().startswith(local),
                  "the application starts its server and opens it, signed in",
                  f"injected {injected} at {wd.url()}")

            # ------------------------------------------------------------ 2
            answered = wd.run_async(
                "const done = arguments[arguments.length - 1];"
                "window.__TAURI_INTERNALS__.invoke('connection')"
                "  .then(c => done({ok: c}), e => done({error: String(e)}));")
            check(answered.get("ok", {}).get("token") == injected.get("token"),
                  "invoke() crosses the IPC bridge and the shell answers",
                  f"invoke('connection') gave {answered}")

            # Kept for check 8: written now, read back two launches later.
            stored_mark = wd.run_async(MARK_WRITE, "launch one")

            # ------------------------------------------------------------ 3
            wd.click("a[href='/team-server']")
            wd.type("input[name=team-server-address]", team_base)
            wd.click("button[type=submit]")
            team_handle = until(
                lambda: next((h for h in wd.handles() if _shows(wd, h, team_base)), None), 60)
            if team_handle is None:
                check(False, "", f"no window showed {team_base}; windows: {wd.handles()}")
                return _finish(failures)
            wd.switch_to(team_handle)
            closed = until(lambda: len(wd.handles()) == 1, 20)
            check(bool(closed), "the team server opens in a window of its own, replacing this one",
                  "the local window stayed open beside the team server's")
            wd.wait_for("input[name=identifier]")
            leaked = wd.run("return window.__CURVEVISION__ || null")
            check(leaked is None, "the team server's page is its own sign-in, given nothing",
                  f"the team server's page received {leaked}")

            # ------------------------------------------------------------ 4
            refused = wd.run_async(
                "const done = arguments[arguments.length - 1];"
                "const ipc = window.__TAURI_INTERNALS__;"
                "if (!ipc) { done({error: 'no IPC at all'}); return; }"
                "ipc.invoke('connection').then(c => done({ok: c}), e => done({error: String(e)}));")
            check("ok" not in refused,
                  "a team server's page cannot use the shell: its command is refused",
                  f"the shell answered the team server's page: {refused}")

            # ------------------------------------------------------------ 5
            wd.type("input[name=identifier]", "kim")
            wd.type("input[name=password]", PASSWORD)
            wd.click("button[type=submit]")
            signed_in = until(lambda: wd.url().rstrip("/").endswith("/projects"), 30)
            check(bool(signed_in), "signing in to the team server works there",
                  f"after signing in the window showed {wd.url()}")

            # ------------------------------------------------------------ 6
            stored = json.loads(remembered.read_text()) if remembered.is_file() else None
            check(stored == {"team_server": team_base},
                  "the choice is written to the application's config folder",
                  f"{remembered} holds {stored}")
            wd.quit()
            stopped = until(lambda: not servers_for(home / "data"), 30)
            check(bool(stopped), "quitting the application stops the server it started",
                  f"still running: {servers_for(home / 'data')}")

            wd.start(application)
            reopened = until(lambda: wd.url().startswith(team_base), 60)
            check(bool(reopened), "the next launch opens the team server directly",
                  f"the next launch opened {wd.url()}")
            wd.quit()

            # ------------------------------------------------------------ 8
            # Back to this computer for the next launch, as Use This Computer leaves it.
            remembered.write_text(json.dumps({"team_server": None}))
            wd.start(application)
            until(lambda: wd.run("return window.__CURVEVISION__ || null"), 60)
            read_back = wd.run_async(MARK_READ)
            check(stored_mark == "stored" and read_back == "launch one",
                  "the webview keeps IndexedDB from one launch to the next",
                  f"wrote {stored_mark!r}; two launches later read {read_back!r}")
            wd.quit()
        except Exception:
            crashed = True
            raise
        finally:
            with contextlib.suppress(Exception):
                wd.quit()
            driver.terminate()
            driver.wait(timeout=20)
            xvfb.terminate()
            team.terminate()
            team.wait(timeout=20)
            if failures or crashed:
                print("\n--- tauri-driver and application output (tail) ---")
                lines = (home / "driver.log").read_text(errors="replace").splitlines()
                # The server's request log would push everything else out of the tail.
                print("\n".join([line for line in lines if "curvevision.access" not in line][-60:]))
    return _finish(failures)


def _answers(port: int) -> bool:
    try:
        urllib.request.urlopen(f"http://127.0.0.1:{port}/status", timeout=2).close()
        return True
    except OSError:
        return False


def _shows(wd: WebDriver, handle: str, base: str) -> bool:
    try:
        wd.switch_to(handle)
        return wd.url().startswith(base)
    except RuntimeError:
        return False


def _finish(failures: list[str]) -> int:
    print()
    if failures:
        print(f"{len(failures)} check(s) failed")
        return 1
    print("every check passed")
    return 0


if __name__ == "__main__":
    sys.exit(main())
