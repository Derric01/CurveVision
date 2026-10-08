#!/usr/bin/env python3
"""Work that never reached the server is offered back when the job is opened again.

    python scripts/verify_crash_recovery.py

Autosave kept a copy of every unsaved change in the browser's IndexedDB and nothing ever
read it back, while the roadmap called crash recovery done. And in the desktop shape it
could not have been read back: the local server took a new port each launch, the page's
origin includes the port, and IndexedDB is per origin — so each launch started with an
empty one.

Claims, each about what was saved asserted against the **API**:

1. A box drawn while saves fail, and then the page gone without a word, is not on the
   server — and opening the job again offers it back, counted.
2. Until that is decided, Submit waits.
3. Restoring puts it on the canvas even while saves still fail, and it is saved once they
   do not.
4. Opening the job after that offers nothing: work the server has is not offered again.
5. Discarding does not save it, and it is not offered again.
6. A box whose save **landed**, though the browser never heard so, is not offered or saved
   a second time; a box drawn after it, which never landed, is — and the offer says the
   job has been saved since.
7. The desktop server comes back on the port it had, after being killed, so the page keeps
   its origin — and a box lost in the killed launch is offered in the next one and saved.

Needs the packaged server (`python desktop/sidecar/build.py`, itself needing
`npm --prefix web run build` first) and a Chromium Playwright can drive; set
`CURVEVISION_CHROMIUM` if the bundled one is not found.
"""

from __future__ import annotations

import json
import os
import signal
import subprocess
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

from screenshot import api, canvas_point, find_chromium, start_server
from verify_label_schema import IMAGE_HEIGHT, IMAGE_WIDTH, png_bytes, text_of


def crash(process: subprocess.Popen[str]) -> None:
    """Kill the server outright, as a crash would: nothing shut down, nothing flushed.

    The packaged server is a one-file binary whose bootloader runs the server as a child, and
    a SIGKILL cannot be passed on: killing the bootloader alone leaves the server running,
    holding the port. So the child is killed first.
    """
    children = subprocess.run(
        ["pgrep", "-P", str(process.pid)], capture_output=True, text=True, check=False
    ).stdout.split()
    for child in children:
        os.kill(int(child), signal.SIGKILL)
    process.kill()
    process.wait(timeout=20)


def seed(base: str, token: str) -> str:
    org = api(base, token, "/organizations")[0]
    project = api(base, token, "/projects", {
        "organization_id": org["id"], "slug": "recovery", "name": "Recovery",
        "labels": [{"name": "car", "color": "#ef4444"}],
    })
    task = api(base, token, "/tasks", {
        "project_id": project["id"], "name": "Kerbside", "media_kind": "image",
    })
    api(base, token, f"/tasks/{task['id']}/assets", files=png_bytes(),
        filename="0.png", content_type="image/png")
    return str(api(base, token, f"/tasks/{task['id']}/jobs")[0]["id"])


def main() -> int:
    from playwright.sync_api import sync_playwright

    failures: list[str] = []

    def check(condition: bool, passed: str, failed: str) -> None:
        if condition:
            print(f"  ok   {passed}")
        else:
            failures.append(failed)
            print(f"  FAIL {failed}")

    launch: dict[str, object] = {"headless": True}
    if (chromium := find_chromium()) is not None:
        launch["executable_path"] = chromium

    with tempfile.TemporaryDirectory(prefix="curvevision-recovery-") as workspace, \
            sync_playwright() as p:
        data_dir = Path(workspace) / "data"

        def lefts(base: str, token: str, job_id: str) -> list[int]:
            shapes = api(base, token, f"/jobs/{job_id}/annotations")["shapes"]
            return sorted(round(shape["points"][0]) for shape in shapes)

        def opener(context, handshake: dict, job_id: str):
            injection = json.dumps({
                "url": handshake["url"], "token": handshake["token"],
                "data_dir": handshake["data_dir"], "version": handshake["version"],
                "desktop": True,
            })

            def open_editor():
                page = context.new_page()
                page.add_init_script(f"window.__CURVEVISION__ = {injection};")
                page.goto(f"{handshake['url']}/jobs/{job_id}", wait_until="networkidle")
                page.wait_for_selector("canvas", timeout=60_000)
                page.wait_for_timeout(1500)  # the lookup for a copy is two round trips
                return page

            return open_editor

        def draw(page, x: float, y: float) -> None:
            page.get_by_title("Fit to frame").click()
            box = page.locator("canvas").nth(1).bounding_box()
            assert box is not None

            def at(px: float, py: float) -> tuple[float, float]:
                return canvas_point(box, (IMAGE_WIDTH, IMAGE_HEIGHT), px, py)

            page.get_by_title("Rectangle (R)").click()
            page.mouse.move(*at(x, y))
            page.mouse.down()
            page.mouse.move(*at(x + 40, y + 30), steps=6)
            page.mouse.up()
            page.wait_for_timeout(300)

        def refuse_saves(page) -> None:
            """The network is gone: every save fails, as a dropped connection's would."""
            page.route("**/annotations", lambda route: route.abort()
                       if route.request.method == "PATCH" else route.continue_())

        def save(page) -> None:
            button = page.get_by_text("Save", exact=True)
            if button.is_enabled():
                button.click()
            page.wait_for_timeout(1500)

        def notice(page) -> str:
            return text_of(page, "[data-recovered-notice]").strip()

        # ============================================= the browser shape: one origin
        process, handshake = start_server(data_dir)
        base, token = handshake["url"], handshake["token"]
        try:
            job_id = seed(base, token)
            browser = p.chromium.launch(**launch)
            context = browser.new_context(viewport={"width": 1280, "height": 1000})
            raised: list[str] = []
            context.on("weberror", lambda error: raised.append(str(error.error)))
            open_editor = opener(context, handshake, job_id)

            # ----------------------------------------------- 1 and 2: offered back
            page = open_editor()
            refuse_saves(page)
            draw(page, 20, 20)
            page.wait_for_timeout(800)
            page.close()  # without beforeunload: the tab, or the machine, simply went
            unsaved = lefts(base, token, job_id)
            page = open_editor()
            offered = notice(page)
            check(unsaved == [] and "One change" in offered,
                  "a box lost with its page is offered back when the job is opened again",
                  f"server holds boxes at {unsaved}; the editor offers {offered!r}")
            submit = page.get_by_role("button", name="Submit")
            submit_held = submit.is_disabled()

            # -------------------------------- 3: on the canvas, then saved
            refuse_saves(page)
            if page.locator("[data-recovered-restore]").count() == 1:
                page.locator("[data-recovered-restore]").click()
            page.wait_for_timeout(800)
            shown = page.locator("[data-object-id]").count()
            check(shown == 1 and lefts(base, token, job_id) == [],
                  "restoring puts it on the canvas, even while saves still fail",
                  f"{shown} object(s) on the canvas; server holds "
                  f"{lefts(base, token, job_id)}")
            page.unroute("**/annotations")
            save(page)
            check(lefts(base, token, job_id) == [20],
                  "and it is saved once saves go through",
                  f"server holds boxes at {lefts(base, token, job_id)}")
            check(submit_held and submit.is_enabled(),
                  "Submit waits until the offer is decided",
                  f"Submit disabled while offered: {submit_held}; "
                  f"enabled after: {submit.is_enabled()}")
            page.close()

            # --------------------------------------- 4: saved work is not offered
            page = open_editor()
            check(page.locator("[data-recovered-notice]").count() == 0,
                  "opening it again offers nothing: the server has that work",
                  f"the editor offers {notice(page)!r} again")

            # ------------------------------------------------------- 5: discard
            refuse_saves(page)
            draw(page, 150, 20)
            page.wait_for_timeout(800)
            page.close()
            page = open_editor()
            offered_again = page.locator("[data-recovered-discard]").count() == 1
            if offered_again:
                page.locator("[data-recovered-discard]").click()
            page.wait_for_timeout(800)
            page.close()
            page = open_editor()
            check(offered_again and lefts(base, token, job_id) == [20]
                  and page.locator("[data-recovered-notice]").count() == 0,
                  "discarding does not save it, and it is not offered again",
                  f"offered: {offered_again}; server holds {lefts(base, token, job_id)}; "
                  f"offered after discarding: {notice(page)!r}")

            # --------------------------- 6: landed, though the browser never heard
            answered: list[bool] = []

            def lose_the_answer(route) -> None:
                if route.request.method != "PATCH":
                    route.continue_()
                elif not answered:
                    answered.append(True)
                    route.fetch()  # the server applies it...
                    route.abort()  # ...and the answer never arrives
                else:
                    route.abort()

            page.route("**/annotations", lose_the_answer)
            draw(page, 20, 150)
            save(page)
            draw(page, 150, 150)
            page.wait_for_timeout(800)
            page.close()
            landed = lefts(base, token, job_id)
            page = open_editor()
            offered = notice(page)
            if page.locator("[data-recovered-restore]").count() == 1:
                page.locator("[data-recovered-restore]").click()
            page.wait_for_timeout(800)
            save(page)
            check(landed == [20, 20] and "One change" in offered and "saved since" in offered
                  and lefts(base, token, job_id) == [20, 20, 150],
                  "a box whose save landed unheard is not saved twice; the one after it is",
                  f"server held {landed} before; offer read {offered!r}; server holds "
                  f"{lefts(base, token, job_id)} after")
            page.close()
            check(not raised, "the browser shape raises nothing", f"page errors: {raised}")
            browser.close()
        finally:
            process.terminate()
            process.wait(timeout=20)

        # ============================== the desktop shape: killed and launched again
        desktop_dir = Path(workspace) / "desktop"
        profile = Path(workspace) / "profile"
        process, first = start_server(desktop_dir)
        try:
            job_id = seed(first["url"], first["token"])
            context = p.chromium.launch_persistent_context(
                str(profile), viewport={"width": 1280, "height": 1000}, **launch)
            page = opener(context, first, job_id)()
            refuse_saves(page)
            draw(page, 90, 90)
            page.wait_for_timeout(800)
            context.close()
        finally:
            crash(process)

        process, second = start_server(desktop_dir, fresh=False)
        try:
            check(second["url"] == first["url"],
                  "launched again, the desktop server comes back on the port it had",
                  f"it was at {first['url']} and is now at {second['url']}: a new origin, "
                  "with nothing the last launch kept")
            context = p.chromium.launch_persistent_context(
                str(profile), viewport={"width": 1280, "height": 1000}, **launch)
            page = opener(context, second, job_id)()
            offered = notice(page)
            if page.locator("[data-recovered-restore]").count() == 1:
                page.locator("[data-recovered-restore]").click()
            page.wait_for_timeout(800)
            save(page)
            saved = lefts(second["url"], second["token"], job_id)
            check("One change" in offered and saved == [90],
                  "and a box lost when the last launch was killed is offered and saved",
                  f"the editor offered {offered!r}; server holds boxes at {saved}")
            context.close()
        finally:
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
    sys.exit(main())
