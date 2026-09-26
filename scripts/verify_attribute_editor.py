#!/usr/bin/env python3
"""A label's attributes can be edited, and no edit strands a value already recorded.

    python scripts/verify_attribute_editor.py

Annotation attribute values are stored keyed by attribute **name**, and the server rejects
a key its schema does not declare. So an edit that removes or renames an attribute used to
succeed — and then every annotation carrying the old value was refused on its next save,
which the annotator found out about from an autosave that failed on a box they had only
moved. The server now refuses those edits; this drives the form that makes the rest of
them, and then the editor that would have broken.

Every claim about what was saved is asserted against the **API**, not against the panel
that just claimed it; the two about what the form offers (1 and 8) read the form:

1. A saved attribute's type and "changes per frame" flag are shown fixed.
2. An attribute nothing has recorded a value under is renamed in place, keeping its id.
3. Removing an attribute an annotation records a value under is **refused**, the server's
   reason is on screen, and the attribute is still there.
4. **The annotator's box still saves** from the editor: dragged with the select tool and
   saved, it moves and keeps its recorded value. This is the check the whole change exists
   for, so it runs straight after the refusal — where a server that did not refuse would
   fail it — and again at the very end.
5. A select gains an option, after the ones it already had.
6. A new attribute is added, with its default.
7. An attribute nothing records is removed.
8. Two attributes of one name are caught before Save, with the reason shown, and nothing
   is sent — the server's answer to that is a 422 that would reach the screen only as
   "One or more fields are invalid".

`attributeSchema.test.ts` pins the pure parts without a DOM, and `TestEditingAttributes`
in `server/tests/api/test_label_schema.py` pins every server rule.

Needs the packaged server (`python desktop/sidecar/build.py`, itself needing
`npm --prefix web run build` first) and a Chromium Playwright can drive; set
`CURVEVISION_CHROMIUM` if the bundled one is not found.
"""

from __future__ import annotations

import json
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

from screenshot import api, canvas_point, find_chromium, start_server
from verify_label_schema import IMAGE_HEIGHT, IMAGE_WIDTH, png_bytes, text_of

#: Where the one annotation sits, in image pixels.
BOX = (40.0, 40.0, 160.0, 140.0)


def seed(base: str, token: str) -> tuple[dict, dict, dict]:
    """A project whose `car` has two attributes, and one box that records a value for one.

    Neither attribute has a default, on purpose: a default is recorded on every annotation
    saved with the label, so with one, `parked` would be recorded too and the rename and
    removal checks below would be refused for a reason that is not the one under test.
    """
    org = api(base, token, "/organizations")[0]
    project = api(base, token, "/projects", {
        "organization_id": org["id"], "slug": "attributes", "name": "Attributes",
        "labels": [
            {"name": "pedestrian", "color": "#22c55e"},
            {
                "name": "car", "color": "#ef4444",
                "attributes": [
                    {"name": "colour", "attribute_type": "select", "values": ["red", "blue"]},
                    {"name": "parked", "attribute_type": "checkbox", "mutable": True},
                ],
            },
        ],
    })
    car = next(label for label in project["labels"] if label["name"] == "car")
    task = api(base, token, "/tasks", {
        "project_id": project["id"], "name": "Kerbside", "media_kind": "image",
    })
    api(base, token, f"/tasks/{task['id']}/assets", files=png_bytes(),
        filename="0.png", content_type="image/png")
    job = api(base, token, f"/tasks/{task['id']}/jobs")[0]
    api(base, token, f"/jobs/{job['id']}/annotations", {
        "annotation_version": 0,
        "created_shapes": [{
            "label_id": car["id"], "frame": 0, "shape_type": "rectangle",
            "points": list(BOX), "attributes": {"colour": "red"},
        }],
    }, method="PATCH")
    return project, car, job


def label_by_id(base: str, token: str, project_id: str, label_id: str) -> dict:
    return next(label for label in api(base, token, f"/projects/{project_id}/labels")
                if label["id"] == label_id)


def main() -> int:
    from playwright.sync_api import sync_playwright

    failures: list[str] = []

    def check(condition: bool, passed: str, failed: str) -> None:
        if condition:
            print(f"  ok   {passed}")
        else:
            failures.append(failed)
            print(f"  FAIL {failed}")

    with tempfile.TemporaryDirectory(prefix="curvevision-attributes-") as workspace:
        process, handshake = start_server(Path(workspace) / "data")
        base, token = handshake["url"], handshake["token"]
        try:
            project, car, job = seed(base, token)
            project_id = str(project["id"])
            saved = {a["name"]: a for a in car["attributes"]}
            print("car has attributes colour (a box records 'red') and parked (nothing)\n")
            injection = json.dumps({
                "url": base, "token": token, "data_dir": handshake["data_dir"],
                "version": handshake["version"], "desktop": True,
            })

            def attributes() -> dict[str, dict]:
                return {a["name"]: a
                        for a in label_by_id(base, token, project_id, car["id"])["attributes"]}

            with sync_playwright() as p:
                launch: dict[str, object] = {"headless": True}
                if (chromium := find_chromium()) is not None:
                    launch["executable_path"] = chromium
                browser = p.chromium.launch(**launch)
                page = browser.new_page(viewport={"width": 1400, "height": 1100})
                raised: list[str] = []
                page.on("pageerror", lambda exc: raised.append(str(exc)))
                page.add_init_script(f"window.__CURVEVISION__ = {injection};")

                def open_editor() -> None:
                    page.goto(f"{base}/projects/{project_id}", wait_until="networkidle")
                    page.wait_for_selector(f"[data-label-edit='{car['id']}']", timeout=30_000)
                    page.locator(f"[data-label-edit='{car['id']}']").click()
                    page.wait_for_selector("[data-attribute-row]", timeout=10_000)

                def row(attribute_id: str):
                    return page.locator(f"[data-attribute-row='{attribute_id}']")

                def save() -> None:
                    page.locator("[data-label-edit-save]").click()
                    page.wait_for_timeout(1500)

                def move_box_in_editor(when: str) -> None:
                    """Drag the box with the select tool and save, as an annotator would.

                    The editor sends the shape back with its attributes exactly as it read
                    them, which is where a value the schema no longer accepts shows up.
                    """
                    before = api(base, token, f"/jobs/{job['id']}/annotations")["shapes"][0]
                    page.goto(f"{base}/jobs/{job['id']}", wait_until="networkidle")
                    page.wait_for_selector("canvas", timeout=60_000)
                    page.wait_for_timeout(1200)
                    canvas = page.locator("canvas").nth(1)  # the shapes layer
                    box = canvas.bounding_box()
                    assert box is not None
                    page.get_by_title("Select (V)").click()
                    x1, y1, x2, y2 = before["points"]
                    start = canvas_point(box, (IMAGE_WIDTH, IMAGE_HEIGHT),
                                         (x1 + x2) / 2, (y1 + y2) / 2)
                    page.mouse.click(*start)
                    page.mouse.move(*start)
                    page.mouse.down()
                    page.mouse.move(start[0] + 30, start[1] + 20, steps=8)
                    page.mouse.up()
                    page.wait_for_timeout(300)
                    page.get_by_text("Save", exact=True).click()
                    page.wait_for_timeout(1500)

                    after = api(base, token, f"/jobs/{job['id']}/annotations")["shapes"][0]
                    moved = after["points"][0] > before["points"][0] + 5
                    check(moved,
                          f"{when}, the box is moved in the editor and the save lands",
                          f"{when}, the box is still at {after['points']}: the editor's "
                          "save was refused")
                    # Only means something if the save landed: a refused save leaves the
                    # stored value untouched, which would pass this on its own.
                    check(moved and after["attributes"].get("colour") == "red",
                          "and it keeps the value it recorded",
                          f"the box's attributes are now {after['attributes']}"
                          + ("" if moved else ", because nothing was saved"))

                # ------------------------------------------------- 1: what is fixed
                open_editor()
                check(not raised, "the project page mounts without raising",
                      f"page error: {raised}")
                colour_row = row(saved["colour"]["id"])
                check(colour_row.locator("[data-attribute-type]").is_disabled()
                      and colour_row.locator("[data-attribute-mutable]").is_disabled(),
                      "a saved attribute's type and per-frame flag are shown fixed",
                      "a saved attribute's type or per-frame flag can be changed in the form")

                # ------------------------------------------ 2: rename, nothing recorded
                row(saved["parked"]["id"]).locator("[data-attribute-name]").fill("stationary")
                save()
                now = attributes()
                check("stationary" in now and now["stationary"]["id"] == saved["parked"]["id"]
                      and "parked" not in now,
                      "an attribute nothing records is renamed in place, keeping its id",
                      f"after the rename the attributes are {sorted(now)}")

                # ---------------------------------------- 3: remove, something recorded
                open_editor()
                row(saved["colour"]["id"]).locator("[data-attribute-remove]").click()
                save()
                check("colour" in attributes(),
                      "removing an attribute a box records a value for is refused",
                      "the recorded attribute was removed; the box's next save will fail")
                refusal = text_of(page, "[data-label-error]")
                check("record a value" in refusal,
                      "with the server's reason on screen",
                      f"the panel reported {refusal.strip()!r}")

                # ------------------------------ 4: the annotator's box still saves
                move_box_in_editor("after a rename and a refused removal")

                # --------------------------------------------- 5: a select gains options
                open_editor()
                if row(saved["colour"]["id"]).count() == 0:
                    # Only reachable when step 3 failed; reported rather than left to die
                    # on a missing element, which would bury the real failure.
                    check(False, "", "colour is gone, so it cannot gain an option")
                else:
                    row(saved["colour"]["id"]).locator("[data-attribute-options]").fill("silver")
                    save()
                    options = attributes().get("colour", {}).get("values")
                    check(options == ["red", "blue", "silver"],
                          "a select gains an option, after the ones it already had",
                          f"the options are now {options}")

                # ------------------------------------------------ 6: a new attribute
                open_editor()
                page.locator("[data-attribute-add]").click()
                added = row("new")
                added.locator("[data-attribute-name]").fill("occluded")
                added.locator("[data-attribute-default]").select_option("false")
                save()
                now = attributes()
                check(list(now) == ["colour", "stationary", "occluded"]
                      and now["occluded"]["attribute_type"] == "checkbox"
                      and now["occluded"]["default_value"] == "false",
                      "a new attribute is added, with its default, after the others",
                      f"the attributes are now {[(n, a['default_value']) for n, a in now.items()]}")
                check(now.get("colour", {}).get("id") == saved["colour"]["id"],
                      "and the attributes already there keep their ids",
                      "saving replaced an existing attribute instead of keeping it")

                # ---------------------------------------- 7: remove, nothing recorded
                open_editor()
                row(saved["parked"]["id"]).locator("[data-attribute-remove]").click()
                save()
                # By id, not by name: asserting "stationary" is absent passes just as well
                # when the rename in step 2 never happened -- which is exactly what a
                # sabotaged save produced, and how this check was caught unable to fail.
                check(saved["parked"]["id"] not in {a["id"] for a in attributes().values()},
                      "an attribute nothing records is removed",
                      f"it is still there: {sorted(attributes())}")

                # --------------------------------------- 8: caught before it is sent
                open_editor()
                before = attributes()
                page.locator("[data-attribute-add]").click()
                row("new").locator("[data-attribute-name]").fill("colour")
                problem = text_of(page, "[data-attribute-problem]")
                check(page.locator("[data-label-edit-save]").is_disabled()
                      and "colour" in problem,
                      "two attributes of one name are caught before Save, and it says why",
                      f"Save is enabled={page.locator('[data-label-edit-save]').is_enabled()}"
                      f", problem shown: {problem!r}")
                page.locator("[data-label-edit-cancel]").click()
                check(attributes() == before, "and nothing was sent",
                      "the schema changed although Save was never pressed")

                shot = Path("/tmp/curvevision-attribute-editor.png")
                open_editor()
                page.screenshot(path=str(shot))
                print(f"  (screenshot: {shot})")

                # ---------------------- 4 again: after every edit, the box still saves
                move_box_in_editor("after every edit")

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
