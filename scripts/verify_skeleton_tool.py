#!/usr/bin/env python3
"""Draw a skeleton in a real browser, and check the dataset it exports to.

    python scripts/verify_skeleton_tool.py

Skeletons round-tripped through the model, the API and the `yolo_pose` exporter long before
anything could draw one: the platform exported a dataset shape it could not produce. This
drives the half that was missing.

The claim that carries all the weight is **joint order**. `yolo_pose` writes `px py v`
triples positionally — the third triple *is* the third declared joint, and there is no name
in the file to correct a mis-ordering. A tool that omitted a joint nobody could see, rather
than recording it as invisible, would shorten the row and move every later joint one place
left. The result trains a model to put elbows where wrists are, and nothing about the file
looks wrong. So the second skeleton here is drawn with its **middle joint skipped**, and the
harness follows that skeleton all the way into the exported label file.

`skeleton.test.ts` and `skeletonTool.test.ts` prove the ordering rules and the state machine
without a DOM. Neither can prove that clicking the canvas reaches the tool, that the joints
land on the pixels that were clicked, that the elements survive the wire format, or that the
exporter reads back what was drawn.

Six claims:

1. The tool is reachable, and says which joint it wants before the first click.
2. Three clicks make one skeleton with three joints, at the pixels that were clicked.
3. A skipped joint is stored **in its own slot**, marked invisible, not dropped.
4. The joint after a skip is still the joint after the skip.
5. `yolo_pose` exports both skeletons with the same number of triples.
6. The skipped joint is visibility 0 in the middle of its row, and its neighbours are not.

Needs the packaged server (`python desktop/sidecar/build.py`) and a Chromium Playwright can
drive; set `CURVEVISION_CHROMIUM` if the bundled one is not found.
"""

from __future__ import annotations

import io
import json
import sys
import tempfile
import urllib.request
import zipfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

from screenshot import api, find_chromium, start_server  # noqa: E402

#: The joints, in the order the label declares them. The order is the contract.
JOINTS = ["shoulder", "elbow", "wrist"]
#: Bones, as index pairs: shoulder-elbow and elbow-wrist.
EDGES = [[0, 1], [1, 2]]

IMAGE_WIDTH = 480
IMAGE_HEIGHT = 320


def png_bytes(width: int = IMAGE_WIDTH, height: int = IMAGE_HEIGHT) -> bytes:
    from PIL import Image

    buffer = io.BytesIO()
    Image.new("RGB", (width, height), "#334155").save(buffer, format="PNG")
    return buffer.getvalue()


def download(base: str, token: str, path: str, payload: dict) -> bytes:
    """A POST whose response is bytes rather than JSON. `api` cannot do that."""
    request = urllib.request.Request(
        f"{base}/api/v1{path}", data=json.dumps(payload).encode(), method="POST"
    )
    request.add_header("Content-Type", "application/json")
    request.add_header("Authorization", f"Bearer {token}")
    with urllib.request.urlopen(request, timeout=60) as response:
        return bytes(response.read())


def seed(base: str, token: str) -> tuple[str, str, str, list[str]]:
    """A project whose label declares three joints, and a one-frame image task."""
    org = api(base, token, "/organizations")[0]
    project = api(base, token, "/projects", {
        "organization_id": org["id"],
        "slug": "poses",
        "name": "Poses",
        "description": "A label with joints, and one with none.",
        "labels": [
            {
                "name": "arm",
                "color": "#ef4444",
                "allowed_shape_types": ["skeleton"],
                "skeleton_edges": EDGES,
                "children": [{"name": name, "color": "#f59e0b"} for name in JOINTS],
            },
            # A second, jointless label, so the harness can also check that the tool says
            # why it is doing nothing rather than silently ignoring clicks.
            {"name": "car", "color": "#22c55e", "allowed_shape_types": ["rectangle"]},
        ],
    })
    arm = next(label for label in project["labels"] if label["name"] == "arm")
    child_ids = [child["id"] for child in arm["children"]]
    if [child["name"] for child in arm["children"]] != JOINTS:
        raise SystemExit(
            f"the project stored the joints as {[c['name'] for c in arm['children']]}, "
            f"not {JOINTS}; this harness is about order, so it cannot continue"
        )

    task = api(base, token, "/tasks", {
        "project_id": project["id"], "name": "Frames", "media_kind": "image",
    })
    api(base, token, f"/tasks/{task['id']}/assets", files=png_bytes(),
        filename="a.png", content_type="image/png")
    job = api(base, token, f"/tasks/{task['id']}/jobs")[0]
    return str(project["id"]), str(task["id"]), str(job["id"]), child_ids


def skeletons(base: str, token: str, job_id: str) -> list[dict]:
    document = api(base, token, f"/jobs/{job_id}/annotations")
    return [s for s in document["shapes"] if s["shape_type"] == "skeleton"]


def main() -> int:
    from playwright.sync_api import sync_playwright

    failures: list[str] = []

    def check(condition: bool, passed: str, failed: str) -> None:
        if condition:
            print(f"  ok   {passed}")
        else:
            failures.append(failed)
            print(f"  FAIL {failed}")

    print(f"a label whose joints are {' -> '.join(JOINTS)}, on a {IMAGE_WIDTH}x{IMAGE_HEIGHT} frame\n")

    with tempfile.TemporaryDirectory(prefix="curvevision-pose-") as workspace:
        process, handshake = start_server(Path(workspace) / "data")
        base, token = handshake["url"], handshake["token"]
        try:
            project_id, _task_id, job_id, child_ids = seed(base, token)
            injection = json.dumps({
                "url": base, "token": token, "data_dir": handshake["data_dir"],
                "version": handshake["version"], "desktop": True,
            })

            with sync_playwright() as p:
                launch: dict[str, object] = {"headless": True}
                if (chromium := find_chromium()) is not None:
                    launch["executable_path"] = chromium
                browser = p.chromium.launch(**launch)
                page = browser.new_page(viewport={"width": 1280, "height": 900})
                raised: list[str] = []
                page.on("pageerror", lambda exc: raised.append(str(exc)))
                page.add_init_script(f"window.__CURVEVISION__ = {injection};")
                page.goto(f"{base}/jobs/{job_id}", wait_until="networkidle")
                page.wait_for_selector("canvas", timeout=60_000)
                page.wait_for_timeout(1500)
                check(not raised, "the editor mounts without raising", f"page error: {raised}")

                page.get_by_title("Skeleton (joints, in order)").click()
                page.wait_for_timeout(300)
                hint = page.locator("[data-skeleton-status]")
                check(hint.count() == 1,
                      "picking the skeleton tool says what it is waiting for",
                      "the skeleton tool shows no hint at all")

                # The jointless label first: a canvas that ignores clicks and says nothing
                # is indistinguishable from a broken one.
                page.locator('[data-label-name="car"]').first.click()
                page.wait_for_timeout(300)
                jointless = hint.inner_text().strip() if hint.count() else ""
                print(f"  with a jointless label: {jointless!r}")
                check("declares no keypoints" in jointless and "arm" in jointless,
                      "a label with no joints says so, and names one that would work",
                      f"the hint for a jointless label reads {jointless!r}")

                page.locator('[data-label-name="arm"]').first.click()
                page.wait_for_timeout(300)
                opening = hint.inner_text().strip() if hint.count() else ""
                print(f"  ready: {opening!r}")
                check(JOINTS[0] in opening,
                      f"it asks for {JOINTS[0]} first, by name",
                      f"the opening hint does not name {JOINTS[0]}: {opening!r}")

                names = page.locator("[data-label-name]").evaluate_all(
                    "nodes => nodes.map(node => node.dataset.labelName)"
                )
                print(f"  the label panel offers: {names}")
                check(names == ["arm", "car"],
                      "the panel offers the two labels, not the joints of one of them",
                      f"the label panel lists {names}; joints are not drawn with directly")

                canvas = page.locator("canvas").first
                box = canvas.bounding_box()
                assert box is not None, "the canvas has no bounding box"

                def click_at(fraction_x: float, fraction_y: float = 0.5) -> None:
                    page.mouse.click(
                        box["x"] + box["width"] * fraction_x,
                        box["y"] + box["height"] * fraction_y,
                    )
                    page.wait_for_timeout(180)

                # ---------------------------------------- a complete skeleton
                #
                # All three on the canvas' horizontal midline, so every joint should land on
                # the same image row, and the middle one exactly at the image centre. That
                # makes "the click reached the right pixel" an exact expectation rather than
                # a tolerance around a guess, without having to reimplement the fit here.
                click_at(0.35)
                mid = hint.inner_text().strip() if hint.count() else ""
                print(f"  after one click: {mid!r}")
                check(JOINTS[1] in mid,
                      "it moves on to the next joint, by name",
                      f"after one click the hint reads {mid!r}")
                click_at(0.50)
                click_at(0.65)
                page.wait_for_timeout(2500)

                drawn = skeletons(base, token, job_id)
                check(len(drawn) == 1,
                      "three clicks made exactly one skeleton",
                      f"three clicks produced {len(drawn)} skeletons")
                if len(drawn) != 1:
                    browser.close()
                    raise SystemExit(1)

                whole = drawn[0]
                elements = whole["elements"]
                print(f"  elements: {[e['points'] for e in elements]}")
                check(len(elements) == len(JOINTS),
                      f"it carries one element per declared joint ({len(JOINTS)})",
                      f"the skeleton has {len(elements)} elements, not {len(JOINTS)}")
                check([e["label_id"] for e in elements] == child_ids,
                      "the elements are in the label's declared joint order",
                      f"element labels are {[e['label_id'] for e in elements]}, "
                      f"not {child_ids}")

                xs = [e["points"][0] for e in elements]
                ys = [e["points"][1] for e in elements]
                print(f"  joints landed at x={[round(x, 1) for x in xs]} y={[round(y, 1) for y in ys]}")
                check(xs[0] < xs[1] < xs[2],
                      "the joints keep the left-to-right order they were clicked in",
                      f"the joints came back out of order: {xs}")
                check(abs(xs[1] - IMAGE_WIDTH / 2) < 1.0,
                      "the middle joint is on the image's centre line, so the click was "
                      "converted to image space rather than passed through as a screen point",
                      f"the centre click landed at x={xs[1]}, not {IMAGE_WIDTH / 2}")
                check(max(ys) - min(ys) < 1.0 and abs(ys[1] - IMAGE_HEIGHT / 2) < 1.0,
                      "three clicks on one canvas row land on one image row",
                      f"the joints landed on different rows: {ys}")
                check(all(not e["outside"] for e in elements),
                      "every joint of a complete skeleton is marked visible",
                      f"a joint of a complete skeleton is marked invisible: {elements}")

                shot = Path("/tmp/curvevision-skeleton.png")
                page.screenshot(path=str(shot), full_page=False)
                print(f"  (screenshot: {shot})")

                # ---------------------------------------- one with a joint skipped
                #
                # This is the case the whole design exists for. The elbow is not visible;
                # the wrist still has to be the third element.
                click_at(0.30, 0.70)
                page.keyboard.press("x")
                page.wait_for_timeout(200)
                after_skip = hint.inner_text().strip() if hint.count() else ""
                print(f"  after skipping: {after_skip!r}")
                check(JOINTS[2] in after_skip,
                      f"skipping {JOINTS[1]} moves on to {JOINTS[2]} rather than ending",
                      f"after a skip the hint reads {after_skip!r}")
                click_at(0.60, 0.70)
                page.wait_for_timeout(2500)

                partial = [s for s in skeletons(base, token, job_id) if s["id"] != whole["id"]]
                check(len(partial) == 1,
                      "the skeleton with a skipped joint reached the server",
                      f"expected one more skeleton, found {len(partial)}")
                if len(partial) != 1:
                    browser.close()
                    raise SystemExit(1)

                gapped = partial[0]["elements"]
                print(f"  with a gap: {[(e['points'], e['outside']) for e in gapped]}")
                check(len(gapped) == len(JOINTS),
                      "a skipped joint keeps its slot rather than shortening the row",
                      f"the gapped skeleton has {len(gapped)} elements, not {len(JOINTS)}")
                check([e["label_id"] for e in gapped] == child_ids,
                      "and the slots still line up with the declared joints",
                      f"element labels are {[e['label_id'] for e in gapped]}")
                check(gapped[1]["outside"] is True,
                      f"the skipped {JOINTS[1]} is recorded as not visible",
                      f"the skipped joint reads {gapped[1]}")
                check(gapped[0]["outside"] is False and gapped[2]["outside"] is False,
                      "its neighbours are untouched",
                      f"a neighbour of the skipped joint is wrong: {gapped}")
                check(gapped[2]["points"][0] > gapped[0]["points"][0],
                      f"the {JOINTS[2]} is still the third element, to the right of the first",
                      f"the joint after the skip landed at {gapped[2]['points']}")

                browser.close()

            # ---------------------------------------- what the exporter reads back
            archive = download(base, token, f"/projects/{project_id}/export",
                               {"format": "yolo_pose"})
            with zipfile.ZipFile(io.BytesIO(archive)) as bundle:
                names = bundle.namelist()
                label_files = [n for n in names if n.startswith("labels/") and n.endswith(".txt")]
                check(len(label_files) == 1,
                      "yolo_pose wrote one label file for the one frame",
                      f"expected one label file, found {label_files}")
                if not label_files:
                    raise SystemExit(1)
                text = bundle.read(label_files[0]).decode().strip()
                data_yaml = bundle.read("data.yaml").decode()

            lines = [line for line in text.splitlines() if line.strip()]
            print(f"\n  data.yaml declares: "
                  f"{[l for l in data_yaml.splitlines() if 'kpt_shape' in l]}")
            for line in lines:
                print(f"  {line}")

            check(f"kpt_shape: [{len(JOINTS)}, 3]" in data_yaml,
                  f"the dataset declares {len(JOINTS)} keypoints per object",
                  f"data.yaml does not declare kpt_shape [{len(JOINTS)}, 3]")
            check(len(lines) == 2,
                  "both skeletons are in the label file",
                  f"the label file has {len(lines)} lines, not 2")
            if len(lines) != 2:
                raise SystemExit(1)

            # `class cx cy w h` then three `px py v` triples.
            widths = {len(line.split()) for line in lines}
            check(widths == {5 + 3 * len(JOINTS)},
                  "every row is the same width, whatever the annotator could see -- "
                  "which is the whole reason a skipped joint is padded rather than dropped",
                  f"the rows have different widths: {widths}")

            visibilities = [
                [int(float(value)) for value in line.split()[5 + 2 :: 3]] for line in lines
            ]
            print(f"  visibility per row: {visibilities}")
            complete, gapped_row = visibilities
            check(all(v > 0 for v in complete),
                  "the complete skeleton exports with every joint visible",
                  f"the complete skeleton exported visibilities {complete}")
            check(gapped_row[1] == 0,
                  f"the skipped {JOINTS[1]} exports as visibility 0, in its own slot",
                  f"the gapped skeleton exported visibilities {gapped_row}")
            check(gapped_row[0] > 0 and gapped_row[2] > 0,
                  "and the joints either side of it are still visible, so nothing shifted",
                  f"the gapped skeleton exported visibilities {gapped_row}")
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
    raise SystemExit(main())
