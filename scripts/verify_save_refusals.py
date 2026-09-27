#!/usr/bin/env python3
"""One object the server refuses no longer stops everything after it from saving.

    python scripts/verify_save_refusals.py

A save is one batch, refused whole. Autosave put a refused batch back and sent it again, and
a batch holding an object the server will never accept is refused every time — so a box of
a label with a **required** attribute nobody had filled in failed its save, and **every**
save after it in that session failed with it, whatever was drawn. The server now names the
entry it refused; autosave sets that one object aside and saves the rest.

Claims, each about what was saved asserted against the **API**:

1. A required value nobody has set is marked on its control before the save.
2. That box's save is refused, and the editor names the object and the reason.
3. A box drawn afterwards **is** saved: the refusal does not poison the session.
4. The refused box is still on the canvas after that save reloaded the frame, and marked.
5. Filling the value in saves it, and the notice goes. Until then the job cannot be
   submitted, which would hand a reviewer a job without it.
6. A new box starts with its label's defaults — typed — and a required checkbox unticked,
   so it saves first time.
7. A **tracked** object whose required value is cleared is refused the same way, without
   stopping the rest, and saves once the value is back.
8. A box deleted after its save but before the reload that follows it is deleted, and does
   not poison the session either. Until the reload swaps the server's id in, the canvas
   knows the box by a local id the server cannot parse; the deletion went out under it, as
   a 422 naming no entry, and every save after it failed the same way. The reload is held
   back here so that window is wide rather than a few milliseconds a timer has to hit.

Needs the packaged server (`python desktop/sidecar/build.py`, itself needing
`npm --prefix web run build` first) and a Chromium Playwright can drive; set
`CURVEVISION_CHROMIUM` if the bundled one is not found.
"""

from __future__ import annotations

import json
import sys
import tempfile
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

from screenshot import api, canvas_point, find_chromium, start_server
from verify_label_schema import IMAGE_HEIGHT, IMAGE_WIDTH, png_bytes, text_of

FRAMES = 2


def seed(base: str, token: str) -> tuple[str, dict[str, str], str]:
    org = api(base, token, "/organizations")[0]
    project = api(base, token, "/projects", {
        "organization_id": org["id"], "slug": "refusals", "name": "Refusals",
        "labels": [
            {"name": "car", "color": "#ef4444", "attributes": [
                {"name": "plate", "attribute_type": "text", "required": True}]},
            {"name": "sign", "color": "#22c55e"},
            {"name": "van", "color": "#3b82f6", "attributes": [
                {"name": "parked", "attribute_type": "checkbox", "required": True},
                {"name": "colour", "attribute_type": "select", "values": ["red", "blue"],
                 "default_value": "blue"},
                {"name": "lit", "attribute_type": "checkbox", "default_value": "true"}]},
            {"name": "bus", "color": "#eab308", "attributes": [
                {"name": "route", "attribute_type": "text", "required": True}]},
        ],
    })
    labels = {label["name"]: label["id"] for label in project["labels"]}
    task = api(base, token, "/tasks", {
        "project_id": project["id"], "name": "Kerbside", "media_kind": "image",
    })
    for index in range(FRAMES):
        api(base, token, f"/tasks/{task['id']}/assets", files=png_bytes(),
            filename=f"{index}.png", content_type="image/png")
    job = api(base, token, f"/tasks/{task['id']}/jobs")[0]
    api(base, token, f"/jobs/{job['id']}/annotations", {
        "annotation_version": 0,
        "created_tracks": [{
            "label_id": labels["bus"], "shape_type": "rectangle",
            "attributes": {"route": "12"},
            "shapes": [
                {"frame": frame, "shape_type": "rectangle", "points": [20, 150, 80, 210],
                 "keyframe": True}
                for frame in range(FRAMES)
            ],
        }],
    }, method="PATCH")
    track_id = api(base, token, f"/jobs/{job['id']}/annotations")["tracks"][0]["id"]
    return str(job["id"]), labels, track_id


def main() -> int:
    from playwright.sync_api import sync_playwright

    failures: list[str] = []

    def check(condition: bool, passed: str, failed: str) -> None:
        if condition:
            print(f"  ok   {passed}")
        else:
            failures.append(failed)
            print(f"  FAIL {failed}")

    with tempfile.TemporaryDirectory(prefix="curvevision-refusals-") as workspace:
        process, handshake = start_server(Path(workspace) / "data")
        base, token = handshake["url"], handshake["token"]
        try:
            job_id, labels, track_id = seed(base, token)
            names = {label_id: name for name, label_id in labels.items()}
            injection = json.dumps({
                "url": base, "token": token, "data_dir": handshake["data_dir"],
                "version": handshake["version"], "desktop": True,
            })

            def document() -> dict:
                return api(base, token, f"/jobs/{job_id}/annotations")

            def saved() -> list[tuple[str, dict]]:
                return sorted((names[s["label_id"]], s["attributes"]) for s in document()["shapes"])

            def route() -> dict:
                return next(t for t in document()["tracks"] if t["id"] == track_id)["attributes"]

            with sync_playwright() as p:
                launch: dict[str, object] = {"headless": True}
                if (chromium := find_chromium()) is not None:
                    launch["executable_path"] = chromium
                browser = p.chromium.launch(**launch)
                page = browser.new_page(viewport={"width": 1280, "height": 1100})
                raised: list[str] = []
                page.on("pageerror", lambda exc: raised.append(str(exc)))
                page.add_init_script(f"window.__CURVEVISION__ = {injection};")
                page.goto(f"{base}/jobs/{job_id}", wait_until="networkidle")
                page.wait_for_selector("canvas", timeout=60_000)
                page.wait_for_timeout(1200)
                check(not raised, "the editor mounts without raising", f"page error: {raised}")

                box = page.locator("canvas").nth(1).bounding_box()
                assert box is not None

                def at(x: float, y: float) -> tuple[float, float]:
                    return canvas_point(box, (IMAGE_WIDTH, IMAGE_HEIGHT), x, y)

                def draw(label: str, x: float, y: float) -> None:
                    # Selecting an object from the list zooms onto it, and `at` assumes the
                    # whole frame is in view.
                    page.get_by_title("Fit to frame").click()
                    page.locator(f"[data-label-id='{labels[label]}']").click()
                    page.get_by_title("Rectangle (R)").click()
                    page.mouse.move(*at(x, y))
                    page.mouse.down()
                    page.mouse.move(*at(x + 50, y + 40), steps=6)
                    page.mouse.up()
                    page.wait_for_timeout(300)

                def save() -> None:
                    button = page.get_by_text("Save", exact=True)
                    if button.is_enabled():
                        button.click()
                    page.wait_for_timeout(1500)

                def attribute(name: str):
                    return page.locator(f"[data-attribute='{name}']")

                refused_rows = page.locator("[data-object-id]:has([data-refused])")

                # ------------------------------------ 1 and 2: refused, and said so
                draw("car", 20, 20)
                check(attribute("plate").locator("xpath=self::*[@data-attribute-missing]")
                      .count() == 1,
                      "a required value nobody has set is marked on its control",
                      "the panel does not mark the required plate as missing")
                save()
                notice = text_of(page, "[data-refused-notice]")
                check(saved() == [] and "car" in notice and "plate" in notice,
                      "the car's save is refused, and the editor names the object and why",
                      f"server holds {saved()}; notice reads {notice.strip()!r}")
                submit = page.get_by_role("button", name="Submit")
                submit_held = submit.is_disabled()

                # ------------------------------------------- 3: not poisoned
                draw("sign", 150, 150)
                save()
                check(saved() == [("sign", {})],
                      "a box drawn after the refused one is saved: the session is not poisoned",
                      f"after drawing a sign and saving, the server holds {saved()}")

                # ------------------------------ 4: still there after the reload
                check(refused_rows.count() == 1,
                      "the refused car is still on the canvas after that save, marked not saved",
                      f"{refused_rows.count()} object row(s) are marked refused; the car "
                      "vanished with the reload or was never marked")

                # --------------------------------------------- 5: fix it, and it saves
                if refused_rows.count() == 1:
                    refused_rows.first.click()
                    page.wait_for_timeout(200)
                    attribute("plate").locator("input").fill("AB12")
                save()
                check(("car", {"plate": "AB12"}) in saved()
                      and page.locator("[data-refused-notice]").count() == 0,
                      "filling in the value saves the car, and the notice goes",
                      f"server holds {saved()}; notice shown: "
                      f"{page.locator('[data-refused-notice]').count() == 1}")
                # Both halves, so that a Submit disabled for some other reason cannot pass it.
                check(submit_held and submit.is_enabled(),
                      "Submit waits while an object is refused, and is offered once it is saved",
                      f"Submit was disabled while refused: {submit_held}; "
                      f"enabled after the fix: {submit.is_enabled()}")

                # -------------------------- 6: starts with what its label demands
                draw("van", 220, 20)
                save()
                vans = [attrs for name, attrs in saved() if name == "van"]
                check(vans == [{"parked": False, "colour": "blue", "lit": True}],
                      "a new van starts unticked and with its defaults, typed, and saves at once",
                      f"the vans saved are {vans}")

                # ---------------------------------------- 7: a tracked object too
                page.get_by_title("Select (V)").click()
                page.locator(f"[data-object-id='{track_id}']").click()
                page.wait_for_timeout(200)
                if attribute("route").count() == 1:
                    attribute("route").locator("input").fill("")
                save()
                notice = text_of(page, "[data-refused-notice]")
                check(route() == {"route": "12"} and "bus" in notice and "route" in notice,
                      "clearing a tracked bus's required route is refused, and said so",
                      f"the bus's route is {route()}; notice reads {notice.strip()!r}")
                draw("sign", 150, 20)
                save()
                signs = [name for name, _ in saved() if name == "sign"]
                check(len(signs) == 2,
                      "and a box drawn afterwards is still saved",
                      f"the server holds {len(signs)} sign(s) after drawing a second")
                page.get_by_title("Select (V)").click()
                page.locator(f"[data-object-id='{track_id}']").click()
                page.wait_for_timeout(200)
                missing = attribute("route").locator("xpath=self::*[@data-attribute-missing]")
                check(missing.count() == 1,
                      "after that save the bus still shows the route cleared, not the saved one",
                      "the bus shows its saved route again, so the refused edit was lost")
                if attribute("route").count() == 1:
                    attribute("route").locator("input").fill("7")
                save()
                check(route() == {"route": "7"}
                      and page.locator("[data-refused-notice]").count() == 0,
                      "typing a route back in saves the bus, and the notice goes",
                      f"the bus's route is {route()}; notice reads "
                      f"{text_of(page, '[data-refused-notice]').strip()!r}")

                # ------------------- 8: deleted between its save and the reload
                def sign_lefts() -> list[int]:
                    return sorted(round(s["points"][0]) for s in document()["shapes"]
                                  if s["label_id"] == labels["sign"])

                before = sign_lefts()

                def slow_reads(route) -> None:
                    if route.request.method == "GET":
                        time.sleep(2)
                    route.continue_()

                page.route(f"**/jobs/{job_id}/annotations", slow_reads)
                draw("sign", 250, 150)
                page.get_by_text("Save", exact=True).click()
                page.wait_for_timeout(500)  # saved; the reload is still two seconds out
                page.keyboard.press("Delete")
                page.wait_for_timeout(3500)
                page.unroute(f"**/jobs/{job_id}/annotations")
                save()
                draw("sign", 100, 90)
                save()
                # By position, not by count: on the bug the deleted box stays and the next is
                # never saved, which is one more sign exactly as the fix is.
                check(sign_lefts() == sorted([*before, 100]),
                      "a box deleted before its save's reload is deleted, and the next one saves",
                      f"signs' left edges went from {before} to {sign_lefts()}; expected the one "
                      "at 250 gone and one at 100 added")

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
