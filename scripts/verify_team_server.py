#!/usr/bin/env python3
"""Drive *Connect to a team server* in a real browser, against two real servers.

    python scripts/verify_team_server.py

The desktop application can show a team's shared server instead of this computer's
(iteration 85). The page asks this computer's server to check the address, then hands it to
the shell, which opens the team server's pages in a window of their own. This runs this
computer's packaged server and a team server (the server shape, from source), and proves:

1. the desktop build offers *Team server*; a browser on a shared server does not, and has no
   such page;
2. an address that is not one, an address where nothing listens, and another desktop
   installation are each refused with their own sentence, and the shell is never asked;
3. the team server is recognised, with its version, and exactly its origin is handed to the
   shell (`connect_to_team_server`);
4. the shell's initialization script hands this computer's credential to this computer's
   server **only**: on the team server's origin the same script leaves nothing behind, and
   the team server's own sign-in page is what appears — and works.

What it cannot prove: that `invoke()` reaches the shell, or that the shell opens and closes
the windows. Playwright drives Chromium, not the Tauri webview; the shell's side is its own
Rust tests (`desktop/shell/src-tauri/src/team.rs`). Here the transport is stood in for and
everything above it is real.

Needs the packaged server (`python desktop/sidecar/build.py`), the built web application
and a Chromium Playwright can drive.
"""

from __future__ import annotations

import json
import sys
import tempfile
import urllib.request
from pathlib import Path

from screenshot import find_chromium, start_server
from verify_workspace import free_port, start_web_server

PASSWORD = "team-server-password-1"


def shell_script(local_origin: str, connection: str) -> str:
    """The local window's initialization script, exactly as `team::initialization_script`
    writes it."""
    return (
        f"if (window.location.origin === {json.dumps(local_origin)}) "
        f"{{ window.__CURVEVISION__ = {connection}; }}"
    )


def register(base: str) -> None:
    request = urllib.request.Request(
        f"{base}/api/v1/auth/register",
        data=json.dumps(
            {"email": "pat@example.com", "username": "pat", "password": PASSWORD}
        ).encode(),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    urllib.request.urlopen(request, timeout=30).close()


def main() -> int:
    from playwright.sync_api import sync_playwright

    failures: list[str] = []

    def check(condition: bool, passed: str, failed: str) -> None:
        if condition:
            print(f"  ok   {passed}")
        else:
            failures.append(failed)
            print(f"  FAIL {failed}")

    with tempfile.TemporaryDirectory(prefix="curvevision-team-server-") as workdir:
        root = Path(workdir)
        local, handshake = start_server(root / "desktop")
        (root / "team").mkdir()
        team, team_base = start_web_server(root / "team")
        register(team_base)
        nobody = f"http://127.0.0.1:{free_port()}"
        connection = json.dumps({
            "url": handshake["url"], "token": handshake["token"],
            "data_dir": handshake["data_dir"], "version": handshake["version"],
            "desktop": True,
        })
        try:
            with sync_playwright() as p:
                launch: dict[str, object] = {"headless": True}
                if (chromium := find_chromium()) is not None:
                    launch["executable_path"] = chromium
                browser = p.chromium.launch(**launch)
                raised: list[str] = []

                # ------------------------------------------------------------ 1
                plain = browser.new_page()
                plain.on("pageerror", lambda exc: raised.append(str(exc)))
                plain.goto(f"{team_base}/login", wait_until="networkidle")
                plain.locator("input[name=identifier]").fill("pat")
                plain.locator("input[name=password]").fill(PASSWORD)
                plain.locator("button[type=submit]").click()
                plain.wait_for_url(f"{team_base}/projects", timeout=30_000)
                offered = plain.get_by_role("link", name="Team server").count()
                plain.goto(f"{team_base}/team-server", wait_until="networkidle")
                check(offered == 0 and plain.url == f"{team_base}/projects",
                      "a browser on a shared server is not offered Team server, nor the page",
                      f"offered {offered} link(s); /team-server ended at {plain.url}")
                plain.close()

                page = browser.new_page()
                page.on("pageerror", lambda exc: raised.append(str(exc)))
                page.add_init_script(shell_script(handshake["url"], connection))
                # The transport, and only the transport: everything above it is real.
                page.add_init_script(
                    # Kept across page loads, so a call from any attempt is seen.
                    "window.__invoked = JSON.parse(sessionStorage.getItem('invoked') || '[]');"
                    "window.__TAURI_INTERNALS__ = { invoke: (cmd, args) => {"
                    "  window.__invoked.push([cmd, args]);"
                    "  sessionStorage.setItem('invoked', JSON.stringify(window.__invoked));"
                    "  return cmd === 'connect_to_team_server' ? Promise.resolve(null)"
                    "    : Promise.reject(new Error('unexpected command: ' + cmd)); } };"
                )
                page.goto(f"{handshake['url']}/projects", wait_until="networkidle")
                link = page.get_by_role("link", name="Team server")
                check(link.count() == 1, "the desktop build offers Team server",
                      "no Team server link in the desktop build")
                link.click()
                page.wait_for_selector("[data-team-server]", timeout=30_000)

                # ------------------------------------------------------------ 2
                def refused_for(address: str) -> str:
                    """What the page says about `address`, from a fresh page each time."""
                    page.goto(f"{handshake['url']}/team-server", wait_until="networkidle")
                    page.locator("input[name=team-server-address]").fill(address)
                    page.get_by_role("button", name="Connect").click()
                    outcome = page.locator(
                        "[data-team-server-refused], [data-team-server-switching]"
                    )
                    outcome.first.wait_for(timeout=30_000)
                    return outcome.first.inner_text()

                said = refused_for("ftp://files.example.com")
                check("not a web address" in said, "an address that is not one is refused",
                      f"for ftp://… the page said {said!r}")
                said = refused_for(nobody)
                check(f"Could not reach {nobody}" in said,
                      "an address where nothing listens is refused, plainly",
                      f"for {nobody} the page said {said!r}")
                said = refused_for(handshake["url"])
                check("desktop installation, not a team server" in said,
                      "another desktop installation is not mistaken for a team server",
                      f"for the desktop server the page said {said!r}")
                invoked = page.evaluate("() => window.__invoked")
                check(invoked == [], "the shell was not asked to open any of them",
                      f"the shell was asked {invoked}")

                # ------------------------------------------------------------ 3
                said = refused_for(f"{team_base}/projects")
                invoked = page.evaluate("() => window.__invoked")
                check(team_base in said and handshake["version"] in said,
                      "the team server is recognised, with its version",
                      f"the page said {said!r}")
                check(invoked == [["connect_to_team_server", {"url": team_base}]],
                      "exactly the team server's origin is handed to the shell",
                      f"the shell was asked {invoked}")
                page.close()

                # ------------------------------------------------------------ 4
                remote = browser.new_page()
                remote.on("pageerror", lambda exc: raised.append(str(exc)))
                remote.add_init_script(shell_script(handshake["url"], connection))
                remote.goto(team_base, wait_until="networkidle")
                leaked = remote.evaluate("() => window.__CURVEVISION__ ?? null")
                check(leaked is None and handshake["token"] not in remote.content(),
                      "on the team server's origin, the shell's script hands over nothing",
                      f"the team server's page received {leaked}")
                remote.locator("input[name=identifier]").fill("pat")
                remote.locator("input[name=password]").fill(PASSWORD)
                remote.locator("button[type=submit]").click()
                remote.wait_for_url(f"{team_base}/projects", timeout=30_000)
                check(remote.get_by_role("link", name="Team server").count() == 0,
                      "the team server's pages are its own: signed in there, no desktop chrome",
                      "the team server's page offered the desktop's Team server link")

                check(not raised, "nothing raised in any page", f"page errors: {raised}")
                browser.close()
        finally:
            team.terminate()
            team.wait(timeout=20)
            local.terminate()
            local.wait(timeout=20)

    print()
    if failures:
        print(f"{len(failures)} check(s) failed")
        return 1
    print("every check passed")
    return 0


if __name__ == "__main__":
    sys.exit(main())
