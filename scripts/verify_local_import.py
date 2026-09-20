#!/usr/bin/env python3
"""Drive the annotate-a-folder-in-place flow in a real browser, against the packaged server.

    python scripts/verify_local_import.py

Local mode's whole claim is that a desktop user's photographs are annotated **where they
are**, with nothing copied. That claim spans a native dialog, an HTTP route that only exists
in local mode, and a page that must behave differently depending on whether a shell is
behind it — which is more seams than a unit test can hold, and none of them are exercised by
the API suite.

What this proves:

* the browser build is **not** offered a native folder picker (it has no way to open one);
* the desktop build is, and mounting the page with no shell behind it does not raise;
* pressing either button when the shell is missing shows an error rather than
  white-screening — the state this harness is in, and also what a broken shell looks like;
* a real folder is attached: the right number of files imported, a corrupt one reported in
  `skipped` rather than failing the import, jobs built, and the page showing the new count;
* a handful of **individually chosen files** are attached the same way — one call per
  file, since the endpoint takes a single path — with a file that cannot be read reported
  in `skipped` exactly as a corrupt file inside a folder is, rather than aborting the rest
  of the chosen files.

What it cannot prove, and does not pretend to: that `invoke()` reaches the shell. Playwright
drives Chromium, not the Tauri webview. ADR 0008 explains why the seam is kept to three
functions because of it, and `handoff.md` lists the gap under *Known issues*.

Needs the packaged server (`python desktop/sidecar/build.py`) and a Chromium Playwright can
drive; set `CURVEVISION_CHROMIUM` if the bundled one is not found.
"""

from __future__ import annotations

import json
import shutil
import sys
import tempfile
from pathlib import Path

from screenshot import FRAMES, LABELS, SAMPLES, api, find_chromium, start_server


def build_folder(root: Path) -> Path:
    """A folder shaped like a real one: some photographs, some things that are not."""
    folder = root / "photos"
    folder.mkdir(parents=True)
    for name in FRAMES:
        shutil.copy(SAMPLES / f"{name}.jpg", folder / f"{name}.jpg")
    (folder / "notes.txt").write_text("not media, and not a candidate for import")
    # A file that *looks* like media and is not. This is what `skipped` is for, and the
    # reason the summary has a warning tone at all.
    (folder / "truncated.jpg").write_bytes(b"\xff\xd8\xff\xe0 this is not a JPEG")
    return folder


def seed_empty_task(base: str, token: str, *, slug: str, name: str) -> str:
    org = api(base, token, "/organizations")[0]
    project = api(base, token, "/projects", {
        "organization_id": org["id"], "slug": slug, "name": name,
        "description": "Annotated in place, with nothing copied.", "labels": LABELS,
    })
    task = api(base, token, "/tasks", {
        "project_id": project["id"], "name": "On disk", "media_kind": "image",
    })
    return task["id"]


def main() -> int:
    from playwright.sync_api import sync_playwright

    failures: list[str] = []

    def check(condition: bool, passed: str, failed: str) -> None:
        if condition:
            print(f"  ok   {passed}")
        else:
            failures.append(failed)
            print(f"  FAIL {failed}")

    with tempfile.TemporaryDirectory(prefix="curvevision-local-import-") as workspace:
        root = Path(workspace)
        folder = build_folder(root)
        print(f"a folder on disk: {folder}")
        print(f"  {len(FRAMES)} photographs, one non-media file, one corrupt image\n")

        process, handshake = start_server(root / "data")
        base, token = handshake["url"], handshake["token"]
        try:
            task_id = seed_empty_task(base, token, slug="local-folder", name="Local folder")
            files_task_id = seed_empty_task(base, token, slug="local-files", name="Local files")
            injection = json.dumps({
                "url": base, "token": token, "data_dir": handshake["data_dir"],
                "version": handshake["version"], "desktop": True,
            })

            with sync_playwright() as p:
                launch: dict[str, object] = {"headless": True}
                if (chromium := find_chromium()) is not None:
                    launch["executable_path"] = chromium
                browser = p.chromium.launch(**launch)
                viewport = {"width": 1400, "height": 900}

                # ------------------------------------------------- the browser build
                plain = browser.new_page(viewport=viewport)
                # An ordinary signed-in session: a stored token, and no shell global.
                plain.add_init_script(
                    f"localStorage.setItem('curvevision.access', {json.dumps(token)});"
                )
                plain.goto(f"{base}/tasks/{task_id}", wait_until="networkidle")
                plain.wait_for_selector("text=On disk", timeout=30_000)
                check(
                    plain.get_by_text("Add media from this computer").count() == 0,
                    "a browser is not offered a native folder picker",
                    "a browser was offered a folder picker it cannot open",
                )
                plain.close()

                # ------------------------------------------------- the desktop build
                page = browser.new_page(viewport=viewport)
                raised: list[str] = []
                page.on("pageerror", lambda exc: raised.append(str(exc)))
                page.add_init_script(f"window.__CURVEVISION__ = {injection};")
                page.goto(f"{base}/tasks/{task_id}", wait_until="networkidle")
                page.wait_for_selector("text=Add media from this computer", timeout=30_000)
                print("  ok   the desktop build offers the folder picker")

                # Mounting subscribes to the shell's menu event; in Chromium that import
                # fails, and the page has to survive it.
                page.wait_for_timeout(1200)
                check(not raised, "mounting with no shell behind it raises nothing",
                      f"mounting raised: {raised}")

                page.get_by_role("button", name="Choose folder").click()
                page.wait_for_timeout(2500)
                intact = page.get_by_text("Add media from this computer").count() == 1
                reported = page.locator("text=/error|failed|not available|undefined/i").count()
                check(intact, "the page survives a missing shell",
                      "the page fell over when the shell was missing")
                check(reported > 0, "a failed shell call is reported to the user",
                      "a failed shell call was silent")

                # The files picker is the same missing-shell story: a different button, a
                # different Tauri command, the identical failure mode to survive.
                page.get_by_role("button", name="Choose files").click()
                page.wait_for_timeout(2500)
                check(page.get_by_text("Add media from this computer").count() == 1,
                      "the page survives the files picker with no shell either",
                      "the page fell over when clicking Choose files with no shell")
                page.close()

                # ------------------------------------------- the whole flow, clicked
                # `invoke` from @tauri-apps/api calls `window.__TAURI_INTERNALS__.invoke`.
                # Standing in for *that* — the transport, and only the transport — makes
                # everything above it real: the click, the dynamic import, the API call,
                # the cache invalidation and what the user is told. It is not a claim that
                # IPC works; it is how much can be tested without it.
                clicked = browser.new_page(viewport=viewport)
                clicked.add_init_script(f"window.__CURVEVISION__ = {injection};")
                clicked.add_init_script(
                    "window.__TAURI_INTERNALS__ = { invoke: (cmd) => "
                    f"cmd === 'choose_folder' ? Promise.resolve({json.dumps(str(folder))})"
                    " : Promise.reject(new Error('unexpected command: ' + cmd)) };"
                )
                clicked.goto(f"{base}/tasks/{task_id}", wait_until="networkidle")
                clicked.get_by_role("button", name="Choose folder").click()
                clicked.wait_for_selector("text=/Imported \\d/", timeout=60_000)
                headline = clicked.locator("text=/Imported \\d/").first.inner_text()
                print(f"\n  the page reports: {headline!r}")
                check(f"Imported {len(FRAMES)} files" in headline,
                      f"all {len(FRAMES)} photographs were attached",
                      f"unexpected headline: {headline!r}")
                check("skipped 1 file" in headline,
                      "the corrupt file is reported rather than swallowed",
                      f"the skipped file was not mentioned: {headline!r}")
                check(f"{len(FRAMES)} frames" in headline,
                      "the new frame count is shown",
                      f"no frame count in: {headline!r}")

                clicked.get_by_text("could not be read").click()  # open the <details>
                clicked.wait_for_timeout(300)
                check(clicked.get_by_text("truncated.jpg", exact=False).count() > 0,
                      "the skipped file is named, with the server's reason",
                      "the skipped list did not name the file")

                shot = Path("/tmp/curvevision-local-import.png")
                clicked.screenshot(path=str(shot))
                print(f"  (screenshot: {shot})")

                # And the rest of the page has caught up, not just the panel.
                clicked.reload(wait_until="networkidle")
                clicked.wait_for_selector(f"text={len(FRAMES)} frames", timeout=30_000)
                print("  ok   the task header shows the new frame count")
                check(bool(api(base, token, f"/tasks/{task_id}/jobs")),
                      "a job was built from the folder",
                      "no job was built for the imported folder")
                clicked.close()

                # ------------------------------------------- individually chosen files
                # `choose_files` returns several paths at once; the endpoint takes one. One
                # of the three does not exist on disk, standing in for a file that could
                # not be read -- the same case a folder import reports in `skipped`, this
                # time surfacing as a whole failed request for that one path rather than
                # one entry the server found while walking a directory.
                good_a = SAMPLES / f"{FRAMES[0]}.jpg"
                good_b = SAMPLES / f"{FRAMES[1]}.jpg"
                missing = root / "does-not-exist.jpg"
                chosen_paths = [str(good_a), str(missing), str(good_b)]

                files_clicked = browser.new_page(viewport=viewport)
                files_raised: list[str] = []
                files_clicked.on("pageerror", lambda exc: files_raised.append(str(exc)))
                files_clicked.add_init_script(f"window.__CURVEVISION__ = {injection};")
                files_clicked.add_init_script(
                    "window.__TAURI_INTERNALS__ = { invoke: (cmd) => "
                    f"cmd === 'choose_files' ? Promise.resolve({json.dumps(chosen_paths)})"
                    " : Promise.reject(new Error('unexpected command: ' + cmd)) };"
                )
                files_clicked.goto(f"{base}/tasks/{files_task_id}", wait_until="networkidle")
                files_clicked.get_by_role("button", name="Choose files").click()
                files_clicked.wait_for_selector("text=/Imported \\d/", timeout=60_000)
                files_headline = files_clicked.locator("text=/Imported \\d/").first.inner_text()
                print(f"\n  choosing 3 individual files, one missing: {files_headline!r}")
                check("Imported 2 files" in files_headline,
                      "both readable files are attached, one call per file",
                      f"unexpected headline: {files_headline!r}")
                check("skipped 1 file" in files_headline,
                      "the missing file is reported rather than aborting the other two",
                      f"the missing file was not mentioned: {files_headline!r}")
                check("2 frames" in files_headline,
                      "the new frame count reflects only what was actually attached",
                      f"unexpected frame count in: {files_headline!r}")

                files_clicked.get_by_text("could not be read").click()
                files_clicked.wait_for_timeout(300)
                check(files_clicked.get_by_text("does-not-exist.jpg", exact=False).count() > 0,
                      "the missing file is named by its own filename, not its full path or the other two",
                      "the skipped list did not name the missing file")
                check(not files_raised, "choosing individual files raises nothing",
                      f"page error: {files_raised}")

                check(bool(api(base, token, f"/tasks/{files_task_id}/jobs")),
                      "a job was built from the individually chosen files",
                      "no job was built for the individually imported files")
                files_clicked.close()

                browser.close()
        finally:
            process.terminate()
            process.wait(timeout=20)

    if failures:
        print(f"\n{len(failures)} failure(s)")
        return 1
    print("\nall checks passed")
    return 0


if __name__ == "__main__":
    sys.exit(main())
