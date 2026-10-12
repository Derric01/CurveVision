#!/usr/bin/env python3
"""Import annotations made elsewhere, from the task page.

    python scripts/verify_annotation_import.py

`POST /tasks/{id}/import` had no caller in the application: a dataset in COCO, CVAT XML or
any other importable format came in only through the API, the SDK or the CLI. This drives
the task page's *Import annotations* panel with an archive written the way CVAT writes one —
a box with attributes on one image, and a track across two — and checks what arrives.

1. The panel offers the importable formats, and nothing can be imported without a file.
2. An append import stores the box with its attribute values and the track as one track,
   and says what it did — including the attribute value the project would refuse.
3. Replace cannot be sent until its warning is ticked; then it replaces rather than
   doubles.
4. A COCO archive naming files this task does not hold says why nothing came in. (COCO,
   because image formats match by file name; CVAT tracks and MOTChallenge match by frame
   number and would land — the first run of this harness used CVAT XML and found exactly
   that.)

Needs the packaged server (`python desktop/sidecar/build.py`) and a Chromium.
"""

from __future__ import annotations

import io
import json
import sys
import tempfile
import zipfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

from screenshot import api, find_chromium, start_server

NAMES = ["a.png", "b.png", "c.png"]

CVAT_XML = """<?xml version="1.0" encoding="utf-8"?>
<annotations>
  <version>1.1</version>
  <image id="2" name="c.png">
    <box label="car" occluded="0" xtl="10" ytl="10" xbr="90" ybr="60" z_order="0">
      <attribute name="colour">blue</attribute>
      <attribute name="parked">true</attribute>
      <attribute name="speed">fast</attribute>
    </box>
  </image>
  <track id="4" label="car">
    <box frame="0" keyframe="1" outside="0" occluded="0" xtl="20" ytl="20" xbr="80" ybr="70">
      <attribute name="colour">red</attribute>
    </box>
    <box frame="1" keyframe="1" outside="0" occluded="0" xtl="40" ytl="20" xbr="100" ybr="70">
      <attribute name="colour">red</attribute>
    </box>
    <box frame="2" keyframe="1" outside="1" occluded="0" xtl="40" ytl="20" xbr="100" ybr="70"/>
  </track>
</annotations>
"""


def png_bytes() -> bytes:
    from PIL import Image

    buffer = io.BytesIO()
    Image.new("RGB", (160, 120), "#1e293b").save(buffer, format="PNG")
    return buffer.getvalue()


def archive(xml: str) -> bytes:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as handle:
        handle.writestr("annotations.xml", xml)
    return buffer.getvalue()


def coco_archive() -> bytes:
    """One box on `a.png`, which the second task does not hold."""
    document = {
        "images": [{"id": 1, "file_name": "a.png", "width": 160, "height": 120}],
        "annotations": [{"id": 1, "image_id": 1, "category_id": 1, "bbox": [1, 1, 10, 10],
                         "area": 100, "iscrowd": 0}],
        "categories": [{"id": 1, "name": "car"}],
    }
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as handle:
        handle.writestr("annotations/instances_default.json", json.dumps(document))
    return buffer.getvalue()


def task_with(base: str, token: str, project_id: str, names: list[str]) -> tuple[str, str]:
    task = api(base, token, "/tasks", {
        "project_id": project_id, "name": "Frames", "media_kind": "image",
    })
    for name in names:
        api(base, token, f"/tasks/{task['id']}/assets", files=png_bytes(),
            filename=name, content_type="image/png")
    job = api(base, token, f"/tasks/{task['id']}/jobs")[0]
    return str(task["id"]), str(job["id"])


def main() -> int:
    from playwright.sync_api import sync_playwright

    failures: list[str] = []

    def check(condition: bool, passed: str, failed: str) -> None:
        if condition:
            print(f"  ok   {passed}")
        else:
            failures.append(failed)
            print(f"  FAIL {failed}")

    with tempfile.TemporaryDirectory(prefix="curvevision-import-") as workspace:
        file = Path(workspace) / "cvat-export.zip"
        file.write_bytes(archive(CVAT_XML))
        coco = Path(workspace) / "coco-export.zip"
        coco.write_bytes(coco_archive())
        process, handshake = start_server(Path(workspace) / "data")
        base, token = handshake["url"], handshake["token"]
        try:
            org = api(base, token, "/organizations")[0]
            project = api(base, token, "/projects", {
                "organization_id": org["id"], "slug": "imported", "name": "Imported",
                "labels": [{
                    "name": "car",
                    "attributes": [
                        {"name": "colour", "attribute_type": "select",
                         "values": ["red", "blue"]},
                        {"name": "parked", "attribute_type": "checkbox", "mutable": True},
                    ],
                }],
            })
            task_id, job_id = task_with(base, token, project["id"], NAMES)
            other_task, _other_job = task_with(base, token, project["id"], ["x.png", "y.png"])
            injection = json.dumps({
                "url": base, "token": token, "data_dir": handshake["data_dir"],
                "version": handshake["version"], "desktop": True,
            })
            with sync_playwright() as p:
                launch: dict[str, object] = {"headless": True}
                if (chromium := find_chromium()) is not None:
                    launch["executable_path"] = chromium
                browser = p.chromium.launch(**launch)
                page = browser.new_page(viewport={"width": 1280, "height": 1400})
                raised: list[str] = []
                page.on("pageerror", lambda exc: raised.append(str(exc)))
                page.add_init_script(f"window.__CURVEVISION__ = {injection};")

                # -------------------------------------------- 1. the panel
                page.goto(f"{base}/tasks/{task_id}", wait_until="networkidle")
                panel = page.locator("[data-import-panel]")
                panel.wait_for(timeout=30_000)
                offered = page.locator("[data-import-format] option").all_inner_texts()
                print(f"  formats: {offered}")
                check(any("CVAT" in name for name in offered)
                      and any("COCO" in name for name in offered)
                      and not any("Pose" in name for name in offered),
                      "the panel offers the formats that import, and not the export-only ones",
                      f"the panel offers {offered}")
                run = page.locator("[data-import-run]")
                check(run.is_disabled(), "nothing can be imported without a file",
                      "Import is enabled with no file chosen")

                # -------------------------------------------- 2. append
                page.locator("[data-import-format]").select_option("cvat_xml")
                page.locator("[data-import-file]").set_input_files(str(file))
                run.click()
                page.wait_for_selector("[data-import-summary]", timeout=30_000)
                summary = page.locator("[data-import-summary]").inner_text()
                warnings = page.locator("[data-import-result]").inner_text()
                print(f"  summary: {summary!r}")
                check("1 shape" in summary and "1 track" in summary,
                      "it says what it stored: one shape and one track",
                      f"the panel said {summary!r}")
                check("speed" in warnings,
                      "and names the attribute the project does not declare",
                      f"nothing named the dropped attribute: {warnings!r}")
                document = api(base, token, f"/jobs/{job_id}/annotations")
                shapes, tracks = document["shapes"], document["tracks"]
                check(len(shapes) == 1
                      and shapes[0]["attributes"] == {"colour": "blue", "parked": True},
                      "the box arrived with its attribute values",
                      f"shapes after import: {shapes!r}")
                check(len(tracks) == 1 and tracks[0]["attributes"] == {"colour": "red"},
                      "the track arrived as one track, with its attribute",
                      f"tracks after import: {tracks!r}")

                # -------------------------------------------- 3. replace
                page.locator("[data-import-replace]").check()
                page.locator("[data-import-file]").set_input_files(str(file))
                page.wait_for_timeout(200)
                check(page.locator("[data-import-replace-warning]").count() == 1
                      and run.is_disabled(),
                      "replace says what it deletes, and waits for a yes",
                      "replace could be sent without its warning being ticked")
                page.locator("[data-import-replace-confirm]").check()
                run.click()
                page.wait_for_timeout(2500)
                again = api(base, token, f"/jobs/{job_id}/annotations")
                check((len(again["shapes"]), len(again["tracks"])) == (1, 1),
                      "and it replaces rather than doubles",
                      f"after replace: {len(again['shapes'])} shapes, "
                      f"{len(again['tracks'])} tracks")

                # -------------------------------------------- 4. no frame matches
                page.goto(f"{base}/tasks/{other_task}", wait_until="networkidle")
                page.locator("[data-import-panel]").wait_for(timeout=30_000)
                page.locator("[data-import-format]").select_option("coco")
                page.locator("[data-import-file]").set_input_files(str(coco))
                page.locator("[data-import-run]").click()
                page.wait_for_selector("[data-import-summary]", timeout=30_000)
                nothing = page.locator("[data-import-summary]").inner_text()
                print(f"  unmatched: {nothing!r}")
                check("file name" in nothing,
                      "an archive naming other files says why nothing came in",
                      f"the panel said {nothing!r}")

                check(not raised, "nothing raised in the page", f"page errors: {raised}")
                shot = Path("/tmp/curvevision-annotation-import.png")
                page.screenshot(path=str(shot), full_page=True)
                print(f"  (screenshot: {shot})")
                browser.close()
        finally:
            process.terminate()
            process.wait(timeout=30)

    print()
    if failures:
        print(f"{len(failures)} check(s) failed")
        return 1
    print("all checks passed")
    return 0


if __name__ == "__main__":
    sys.exit(main())
