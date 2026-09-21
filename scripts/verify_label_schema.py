#!/usr/bin/env python3
"""A project's label schema can be changed after the project exists.

    python scripts/verify_label_schema.py

`POST` and `DELETE /projects/{id}/labels` have existed since the initial schema, the policy
engine gates them, and `api.createLabel` sat in the web client **with no caller** — so a
project that turned out to need a `van` class alongside `car` could only get one from the
SDK, the CLI or curl. Label schemas are not knowable in advance; that is the whole reason
those endpoints exist.

Six claims, each asserted against the **API** rather than the panel that just claimed it:

1. A label typed into the box is created, with the colour shown beside it.
2. It lands at the **end** of the schema. It used to take position 0 and sort into the
   middle by name, because the listing orders by `(position, name)` — `van` added to a
   `car`/`pedestrian` project appeared first.
3. It can be drawn with straight away: the editor's label picker offers it.
4. An unused label is removed by its delete control.
5. A label annotations still use is **refused**, the message says so, and both the label
   and the annotation are still there afterwards. This is what makes the delete control
   safe to offer at all — the server will not cascade.
6. A duplicate name is refused by the server and reported, rather than pre-empted in the
   browser: the name comparison lives in one place.

`labelSchema.test.ts` pins the pure parts without a DOM — what is sent for a typed name,
and which colour is proposed next.

Needs the packaged server (`python desktop/sidecar/build.py`, itself needing
`npm --prefix web run build` first) and a Chromium Playwright can drive; set
`CURVEVISION_CHROMIUM` if the bundled one is not found.
"""

from __future__ import annotations

import io
import json
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

from screenshot import api, find_chromium, start_server

IMAGE_WIDTH = 320
IMAGE_HEIGHT = 240


def png_bytes() -> bytes:
    from PIL import Image

    buffer = io.BytesIO()
    Image.new("RGB", (IMAGE_WIDTH, IMAGE_HEIGHT), "#1e293b").save(buffer, format="PNG")
    return buffer.getvalue()


def seed(base: str, token: str) -> tuple[dict, dict]:
    """A project whose schema is deliberately incomplete, and a task to draw in."""
    org = api(base, token, "/organizations")[0]
    project = api(base, token, "/projects", {
        "organization_id": org["id"], "slug": "schema", "name": "Schema",
        "description": "A label schema that turned out to need another class.",
        "labels": [
            {"name": "car", "color": "#ef4444", "allowed_shape_types": ["rectangle"]},
            {"name": "pedestrian", "color": "#22c55e"},
        ],
    })
    task = api(base, token, "/tasks", {
        "project_id": project["id"], "name": "Kerbside", "media_kind": "image",
    })
    api(base, token, f"/tasks/{task['id']}/assets", files=png_bytes(),
        filename="0.png", content_type="image/png")
    job = api(base, token, f"/tasks/{task['id']}/jobs")[0]
    return project, job


def schema(base: str, token: str, project_id: str) -> list[dict]:
    return list(api(base, token, f"/projects/{project_id}/labels"))


def text_of(page, selector: str) -> str:
    """An element's text, or `""` when it is not on the page at all.

    "Nothing rendered the server's refusal" is precisely what two of these checks are
    looking for, and `inner_text` on a locator that never appears raises a timeout — which
    buries the failure under a traceback instead of reporting it. Confirmed by deleting the
    error banner deliberately: the run died rather than printing a FAIL line.
    """
    locator = page.locator(selector)
    return locator.inner_text() if locator.count() else ""


def main() -> int:
    from playwright.sync_api import sync_playwright

    failures: list[str] = []

    def check(condition: bool, passed: str, failed: str) -> None:
        if condition:
            print(f"  ok   {passed}")
        else:
            failures.append(failed)
            print(f"  FAIL {failed}")

    with tempfile.TemporaryDirectory(prefix="curvevision-labels-") as workspace:
        process, handshake = start_server(Path(workspace) / "data")
        base, token = handshake["url"], handshake["token"]
        try:
            project, job = seed(base, token)
            project_id = str(project["id"])
            before = [label["name"] for label in schema(base, token, project_id)]
            print(f"a project whose schema is {before}\n")
            injection = json.dumps({
                "url": base, "token": token, "data_dir": handshake["data_dir"],
                "version": handshake["version"], "desktop": True,
            })

            with sync_playwright() as p:
                launch: dict[str, object] = {"headless": True}
                if (chromium := find_chromium()) is not None:
                    launch["executable_path"] = chromium
                browser = p.chromium.launch(**launch)
                page = browser.new_page(viewport={"width": 1400, "height": 1100})
                raised: list[str] = []
                page.on("pageerror", lambda exc: raised.append(str(exc)))
                page.add_init_script(f"window.__CURVEVISION__ = {injection};")
                page.goto(f"{base}/projects/{project_id}", wait_until="networkidle")
                page.wait_for_selector("[data-new-label]", timeout=30_000)
                check(not raised, "the project page mounts without raising",
                      f"page error: {raised}")

                # ------------------------------------------------------- 1 and 2: adding
                proposed = page.locator("[data-new-label-color]").input_value()
                check(proposed not in {"#ef4444", "#22c55e"},
                      f"the proposed colour ({proposed}) is not one the schema already uses",
                      f"the picker proposed {proposed}, which is already in the schema")

                page.locator("[data-new-label]").fill("  van  ")
                page.locator("[data-new-label-add]").click()
                page.wait_for_timeout(1500)

                after = schema(base, token, project_id)
                names = [label["name"] for label in after]
                check(names == [*before, "van"],
                      "the label is created, and lands at the end of the schema",
                      f"the schema is now {names} rather than {[*before, 'van']}")
                created = next((label for label in after if label["name"] == "van"), None)
                check(created is not None and created["color"] == proposed,
                      "with the colour that was shown beside the box",
                      f"the label was stored as {created and created['color']!r}, "
                      f"not {proposed!r}")

                shot = Path("/tmp/curvevision-label-schema.png")
                page.screenshot(path=str(shot))
                print(f"  (screenshot: {shot})")

                if created is None:
                    browser.close()
                    print(f"\n{len(failures)} check(s) failed:")
                    for failure in failures:
                        print(f"  - {failure}")
                    return 1

                # --------------------------------------------- 3: it is usable right away
                # Waited on the label list as a whole and asserted on *this* label, not the
                # other way round: waiting for the thing under test and then checking it is
                # there is a check that can only time out, never fail.
                page.goto(f"{base}/jobs/{job['id']}", wait_until="networkidle")
                page.wait_for_selector("[data-label-id]", timeout=30_000)
                check(page.locator(f"[data-label-id='{created['id']}']").count() == 1,
                      "the new label is offered in the editor straight away",
                      "the editor's label list does not show the new label")
                api(base, token, f"/jobs/{job['id']}/annotations", {
                    "annotation_version": 0,
                    "created_shapes": [{
                        "label_id": created["id"], "frame": 0,
                        "shape_type": "rectangle", "points": [10, 10, 60, 60],
                    }],
                }, method="PATCH")

                # ------------------------------------------- 4 and 5: removing, and not
                page.goto(f"{base}/projects/{project_id}", wait_until="networkidle")
                page.wait_for_selector("[data-new-label]", timeout=30_000)
                spare = api(base, token, f"/projects/{project_id}/labels",
                            {"name": "scooter", "color": "#a855f7"})
                page.reload(wait_until="networkidle")
                page.wait_for_selector(f"[data-label-delete='{spare['id']}']", timeout=30_000)
                page.locator(f"[data-label-delete='{spare['id']}']").click()
                page.wait_for_timeout(1500)
                check("scooter" not in [label["name"] for label in schema(base, token, project_id)],
                      "an unused label is removed by its delete control",
                      "the unused label is still in the schema")

                page.locator(f"[data-label-delete='{created['id']}']").click()
                page.wait_for_timeout(1500)
                still_there = [label["name"] for label in schema(base, token, project_id)]
                check("van" in still_there,
                      "a label annotations still use is not deleted",
                      f"the in-use label was deleted; the schema is now {still_there}")
                drawn = api(base, token, f"/jobs/{job['id']}/annotations")
                check(len(drawn["shapes"]) == 1,
                      "and the annotation that uses it survives",
                      f"the job now has {len(drawn['shapes'])} shapes")
                error_text = text_of(page, "[data-label-error]")
                check("relabel" in error_text.lower() or "annotation" in error_text.lower(),
                      "with the server's own reason on screen",
                      f"the panel reported {error_text.strip()!r}")

                # ------------------------------------------------------ 6: the duplicate
                page.locator("[data-new-label]").fill("car")
                page.locator("[data-new-label-add]").click()
                page.wait_for_timeout(1500)
                duplicate_text = text_of(page, "[data-label-error]")
                check("already exists" in duplicate_text.lower(),
                      "a duplicate name is refused by the server and reported here",
                      f"adding a duplicate reported {duplicate_text.strip()!r}")
                check(len([label for label in schema(base, token, project_id)
                           if label["name"] == "car"]) == 1,
                      "and no second label by that name exists",
                      "a duplicate label was created")

                check(not raised, "the whole loop raises nothing", f"page error: {raised}")
                browser.close()
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
