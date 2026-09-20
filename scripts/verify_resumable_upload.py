#!/usr/bin/env python3
"""Drive the resumable-upload panel in a real browser, against the packaged server.

    python scripts/verify_resumable_upload.py

The API and its tests prove the offset-based `PATCH` protocol is correct against an HTTP
client that behaves. They cannot prove what happens when a real browser tab drops a request
mid-upload, or that a person can even reach the panel at all outside the desktop build — the
"Add media from this computer" panel on this page has always been desktop-only, and until
this iteration a browser user had no way to add media to a task after creating it.

What this proves:

* the panel exists for a **plain browser session**, not only the desktop build — the gap
  this iteration closes;
* an ordinary small image still goes through the single-request batch path
  (`uploadAssets`), not the resumable one — no `PATCH .../uploads/...` call is made for it;
* a file at or over the resumable threshold is sent in **several** `PATCH` chunks, not one
  request holding the whole file;
* killing one chunk mid-upload (simulating a dropped connection) is reported to the user as
  a failure, not swallowed;
* re-selecting the *same* file resumes from the byte offset the server actually has, proven
  by reading the `Upload-Offset` header of the first retried chunk, rather than restarting
  the transfer from zero;
* exactly one asset lands from the two attempts combined, and the task's frame count and job
  list catch up once it completes.

Needs the packaged server (`python desktop/sidecar/build.py`, itself needing
`npm --prefix web run build` first) and a Chromium Playwright can drive; set
`CURVEVISION_CHROMIUM` if the bundled one is not found.
"""

from __future__ import annotations

import json
import os
import sys
import tempfile
from pathlib import Path

from screenshot import LABELS, api, find_chromium, start_server

#: Comfortably over the panel's 20 MB threshold. Random pixel noise, not a photograph:
#: PNG's lossless compression cannot shrink it, so the file on disk is close to the raw
#: byte count and the chunk math below is predictable rather than "however well a real
#: photo happened to compress".
LARGE_IMAGE_SIDE = 3000


def build_large_image(path: Path) -> int:
    from PIL import Image

    raw = os.urandom(LARGE_IMAGE_SIDE * LARGE_IMAGE_SIDE * 3)
    Image.frombuffer("RGB", (LARGE_IMAGE_SIDE, LARGE_IMAGE_SIDE), raw).save(
        path, format="PNG", compress_level=1
    )
    return path.stat().st_size


def build_small_image(path: Path) -> int:
    from PIL import Image

    Image.new("RGB", (64, 64), "#334155").save(path, format="PNG")
    return path.stat().st_size


def seed_task(base: str, token: str) -> tuple[str, str]:
    org = api(base, token, "/organizations")[0]
    project = api(
        base,
        token,
        "/projects",
        {
            "organization_id": org["id"],
            "slug": "resumable-upload",
            "name": "Resumable upload",
            "labels": LABELS,
        },
    )
    task = api(
        base,
        token,
        "/tasks",
        {"project_id": project["id"], "name": "Uploads", "media_kind": "image"},
    )
    return task["id"], project["id"]


def main() -> int:
    from playwright.sync_api import sync_playwright

    failures: list[str] = []

    def check(condition: bool, passed: str, failed: str) -> None:
        if condition:
            print(f"  ok   {passed}")
        else:
            failures.append(failed)
            print(f"  FAIL {failed}")

    with tempfile.TemporaryDirectory(
        prefix="curvevision-resumable-upload-"
    ) as workspace:
        root = Path(workspace)
        large_path = root / "large.png"
        large_size = build_large_image(large_path)
        small_path = root / "small.png"
        build_small_image(small_path)
        chunk_size = 8 * 1024 * 1024
        expected_chunks = -(-large_size // chunk_size)  # ceil
        print(
            f"a {large_size:,}-byte synthetic image, expected in {expected_chunks} chunks"
        )

        process, handshake = start_server(root / "data")
        base, token = handshake["url"], handshake["token"]
        try:
            task_id, _project_id = seed_task(base, token)

            with sync_playwright() as p:
                launch: dict[str, object] = {"headless": True}
                if (chromium := find_chromium()) is not None:
                    launch["executable_path"] = chromium
                browser = p.chromium.launch(**launch)
                viewport = {"width": 1400, "height": 900}

                # -------------------------------------------------- reachable in a browser
                plain = browser.new_page(viewport=viewport)
                plain.add_init_script(
                    f"localStorage.setItem('curvevision.access', {json.dumps(token)});"
                )
                plain.goto(f"{base}/tasks/{task_id}", wait_until="networkidle")
                plain.wait_for_selector("text=Uploads", timeout=30_000)
                check(
                    plain.get_by_text("Upload media").count() == 1,
                    "a plain browser session is offered the upload panel",
                    "the upload panel is missing for a browser session",
                )
                check(
                    plain.get_by_text("Add media from this computer").count() == 0,
                    "the desktop-only local-import panel is correctly absent",
                    "a browser session was offered the desktop-only panel",
                )

                # ------------------------------------------------------ the small-file path
                patch_urls: list[str] = []

                def record_patch(route, request):
                    if request.method == "PATCH":
                        patch_urls.append(request.url)
                    route.continue_()

                plain.route(f"**/api/v1/tasks/{task_id}/uploads/**", record_patch)
                plain.set_input_files('input[type="file"]', str(small_path))
                plain.wait_for_selector("text=/Uploaded 1 file/", timeout=30_000)
                check(
                    len(patch_urls) == 0,
                    "an ordinary small image is sent as one request, not chunked",
                    f"a small image triggered {len(patch_urls)} PATCH chunk(s)",
                )
                plain.unroute(f"**/api/v1/tasks/{task_id}/uploads/**", record_patch)
                plain.close()

                # -------------------------------------- a dropped chunk, then a real resume
                offsets_sent: list[int] = []
                attempt = {"patches_seen": 0}

                def intercept(route, request):
                    if request.method != "PATCH":
                        route.continue_()
                        return
                    attempt["patches_seen"] += 1
                    offsets_sent.append(int(request.headers.get("upload-offset", "-1")))
                    if attempt["patches_seen"] == 2:
                        # Stands in for a connection dropping mid-chunk: the browser never
                        # gets a response, so the client's fetch rejects exactly as it
                        # would for a real dropped socket.
                        route.abort("connectionreset")
                    else:
                        route.continue_()

                page = browser.new_page(viewport=viewport)
                page.add_init_script(
                    f"localStorage.setItem('curvevision.access', {json.dumps(token)});"
                )
                page.route(f"**/api/v1/tasks/{task_id}/uploads/**", intercept)
                page.goto(f"{base}/tasks/{task_id}", wait_until="networkidle")

                page.set_input_files('input[type="file"]', str(large_path))
                page.wait_for_selector("text=/1 file failed/", timeout=60_000)
                print(f"\n  first attempt sent offsets {offsets_sent} before the drop")
                check(
                    offsets_sent[:2] == [0, chunk_size],
                    "the first two chunks were sent at offsets 0 and the chunk size",
                    f"unexpected offsets before the drop: {offsets_sent}",
                )
                check(
                    len(offsets_sent) == 2,
                    "the upload stopped at the dropped chunk rather than continuing past it",
                    f"expected exactly 2 attempted chunks before failure, got {len(offsets_sent)}",
                )

                # Re-selecting the same file is how a person actually retries this: the
                # input's value was reset after the first attempt specifically so the
                # browser fires `change` again for an identical selection.
                offsets_sent.clear()
                page.set_input_files('input[type="file"]', str(large_path))
                page.wait_for_selector("text=/Uploaded 1 file/", timeout=60_000)
                print(f"  retry sent offsets {offsets_sent}")
                check(
                    bool(offsets_sent) and offsets_sent[0] == chunk_size,
                    "the retry resumed from the server's real offset, not from zero",
                    f"the retry's first chunk was not at the expected offset: {offsets_sent}",
                )
                check(
                    len(offsets_sent) == expected_chunks - 1,
                    "the retry sent only the remaining chunks, not the whole file again",
                    f"expected {expected_chunks - 1} chunk(s) on retry, sent {len(offsets_sent)}",
                )

                assets = api(base, token, f"/tasks/{task_id}/assets")
                check(
                    len(assets) == 2,
                    "exactly the small image and the large image are attached — no duplicate",
                    f"unexpected asset count: {len(assets)}",
                )

                page.reload(wait_until="networkidle")
                page.wait_for_selector("text=2 frames", timeout=30_000)
                check(
                    bool(api(base, token, f"/tasks/{task_id}/jobs")),
                    "a job exists once the upload has landed",
                    "no job was built after the resumable upload completed",
                )

                shot = Path("/tmp/curvevision-resumable-upload.png")
                page.screenshot(path=str(shot))
                print(f"  (screenshot: {shot})")
                page.close()

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
