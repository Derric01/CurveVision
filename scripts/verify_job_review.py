#!/usr/bin/env python3
"""Accept and send back a job in a real browser, and check what reached the database.

    python scripts/verify_job_review.py

`POST /jobs/{id}/review`, the review state machine and the client's `reviewJob` have all
existed since early in the project, fully tested server-side — and **nothing in the
application called them**. An annotator could press Submit; no reviewer could do anything
about it from the editor. `docs/ROADMAP.md` called the review state machine Done, which was
true of the API and false of the product, the same way issues were Done with no panel and
model suggestions had a decision endpoint nothing reached.

`review.test.ts` pins the rules without a DOM: which states can be reviewed, which can be
submitted, that a rejection needs a reason. It cannot prove any of the loop — that the panel
reaches the endpoint, that a rejection's comment becomes an issue the annotator can actually
read, or that a job sent back can be picked up and submitted again.

Seven claims, which together are the loop the roadmap claims:

1. A job being annotated shows no review controls at all — they belong to a submitted job.
2. Submitting from the editor moves it to `submitted`, and the panel then appears.
3. Send back is refused while the reason box is empty. Work that returns unexplained is the
   failure the comment exists to prevent, so the button is disabled rather than optional.
4. Sending it back with a reason moves the job to `rejected` **and files that reason as an
   issue on the job** — asserted against the API, not against the panel that just said so.
5. The annotator sees it: the reason is in the issues panel on reload, not only in a table.
6. A rejected job can be submitted again, which is the point of sending one back.
7. Accepting moves it to `accepted` — and Submit is then **disabled**, because the server
   refuses `accepted -> submitted` with a 409 that the header used to swallow.

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

IMAGE_WIDTH = 480
IMAGE_HEIGHT = 320

#: What the reviewer types when sending the job back. Distinctive enough that finding it in
#: the issues list cannot be a coincidence.
REASON = "The boxes on the left edge are off by about a wheel."


def png_bytes(width: int = IMAGE_WIDTH, height: int = IMAGE_HEIGHT) -> bytes:
    from PIL import Image

    buffer = io.BytesIO()
    Image.new("RGB", (width, height), "#1e293b").save(buffer, format="PNG")
    return buffer.getvalue()


def seed(base: str, token: str) -> str:
    """A one-frame job with a box already drawn on it, in `new` — work to rule on."""
    org = api(base, token, "/organizations")[0]
    project = api(base, token, "/projects", {
        "organization_id": org["id"], "slug": "review", "name": "Review",
        "description": "Accepting work, and sending it back with a reason.",
        "labels": [{"name": "car", "color": "#ef4444", "allowed_shape_types": ["rectangle"]}],
    })
    task = api(base, token, "/tasks", {
        "project_id": project["id"], "name": "Batch", "media_kind": "image",
    })
    api(base, token, f"/tasks/{task['id']}/assets", files=png_bytes(),
        filename="a.png", content_type="image/png")

    job = api(base, token, f"/tasks/{task['id']}/jobs")[0]
    document = api(base, token, f"/jobs/{job['id']}/annotations")
    api(base, token, f"/jobs/{job['id']}/annotations", {
        "annotation_version": document["annotation_version"],
        "created_shapes": [{
            "label_id": project["labels"][0]["id"],
            "frame": 0,
            "shape_type": "rectangle",
            "points": [40, 60, 180, 220],
        }],
    }, method="PATCH")
    return str(job["id"])


def state_of(base: str, token: str, job_id: str) -> str:
    return str(api(base, token, f"/jobs/{job_id}")["state"])


def main() -> int:
    from playwright.sync_api import sync_playwright

    failures: list[str] = []

    def check(condition: bool, passed: str, failed: str) -> None:
        if condition:
            print(f"  ok   {passed}")
        else:
            failures.append(failed)
            print(f"  FAIL {failed}")

    with tempfile.TemporaryDirectory(prefix="curvevision-review-") as workspace:
        process, handshake = start_server(Path(workspace) / "data")
        base, token = handshake["url"], handshake["token"]
        try:
            job_id = seed(base, token)
            print("a one-frame job with a box on it, state 'new'\n")
            injection = json.dumps({
                "url": base, "token": token, "data_dir": handshake["data_dir"],
                "version": handshake["version"], "desktop": True,
            })

            with sync_playwright() as p:
                launch: dict[str, object] = {"headless": True}
                if (chromium := find_chromium()) is not None:
                    launch["executable_path"] = chromium
                browser = p.chromium.launch(**launch)
                page = browser.new_page(viewport={"width": 1500, "height": 1000})
                raised: list[str] = []
                page.on("pageerror", lambda exc: raised.append(str(exc)))
                page.add_init_script(f"window.__CURVEVISION__ = {injection};")
                page.goto(f"{base}/jobs/{job_id}", wait_until="networkidle")
                page.wait_for_selector("canvas", timeout=60_000)
                page.wait_for_timeout(1800)
                check(not raised, "the editor mounts without raising", f"page error: {raised}")

                review = page.locator("[data-review]")
                submit = page.get_by_role("button", name="Submit")

                # ------------------------------------------------- 1. nothing to review yet
                check(review.count() == 0,
                      "a job being annotated shows no review controls",
                      "the review panel rendered on a job nobody has submitted")

                # ------------------------------------------------------- 2. submit, then it
                submit.click()
                page.wait_for_timeout(1200)
                check(state_of(base, token, job_id) == "submitted",
                      "pressing Submit moves the job to 'submitted'",
                      f"after Submit the job is {state_of(base, token, job_id)!r}")
                page.wait_for_selector("[data-review]", timeout=20_000)
                check(page.locator("[data-review-prompt]").count() == 1,
                      "the review panel appears once the job is submitted",
                      "no review panel on a submitted job")

                # ------------------------------------- 3. no reason, no sending it back
                send_back = page.locator("[data-review-reject]")
                check(send_back.is_disabled(),
                      "send back is refused while no reason has been given",
                      "send back was offered with an empty reason box")

                page.locator("[data-review-comment]").fill("   ")
                page.wait_for_timeout(200)
                check(send_back.is_disabled(),
                      "and whitespace is not a reason either",
                      "whitespace was accepted as a reason")

                # ------------------------------------------ 4. send it back, with a reason
                page.locator("[data-review-comment]").fill(REASON)
                page.wait_for_timeout(200)
                check(not send_back.is_disabled(),
                      "typing a reason enables it",
                      "send back stayed disabled with a real reason typed")

                send_back.click()
                page.wait_for_selector("[data-review-outcome]", timeout=20_000)
                outcome = page.locator("[data-review-outcome]").inner_text()
                print(f"\n  the panel reports: {outcome!r}")
                check(state_of(base, token, job_id) == "rejected",
                      "sending it back moves the job to 'rejected'",
                      f"the job is {state_of(base, token, job_id)!r}, not rejected")

                issues = api(base, token, f"/jobs/{job_id}/issues")
                bodies = [
                    comment["body"] for issue in issues for comment in issue["comments"]
                ]
                check(any(REASON in body for body in bodies),
                      "the reason was filed as an issue on the job, verbatim",
                      f"no issue carries the reason; found {bodies}")

                # --------------------------------- 5. the annotator can read it on the page
                page.reload(wait_until="networkidle")
                page.wait_for_selector("canvas", timeout=60_000)
                page.wait_for_timeout(1500)
                check(page.get_by_text(REASON, exact=False).count() > 0,
                      "and the annotator meets it in the issues panel, not only in a table",
                      "the reason is nowhere on the page the annotator opens")
                check(page.locator("[data-review]").count() == 0,
                      "a rejected job is no longer offered the review controls",
                      "the review panel is still offered on a rejected job")

                shot = Path("/tmp/curvevision-job-review.png")
                page.screenshot(path=str(shot))
                print(f"  (screenshot: {shot})")

                # ------------------------------------------------- 6. pick it up, submit it
                submit = page.get_by_role("button", name="Submit")
                check(not submit.is_disabled(),
                      "a rejected job can be submitted again, which is the point of "
                      "sending it back",
                      "Submit is disabled on a rejected job, so it can never come back")
                submit.click()
                page.wait_for_timeout(1200)
                check(state_of(base, token, job_id) == "submitted",
                      "resubmitting works",
                      f"after resubmit the job is {state_of(base, token, job_id)!r}")

                # ------------------------------------------------------------- 7. accept it
                page.wait_for_selector("[data-review-accept]", timeout=20_000)
                page.locator("[data-review-accept]").click()
                page.wait_for_selector("[data-review-outcome]", timeout=20_000)
                accepted_text = page.locator("[data-review-outcome]").inner_text()
                print(f"  the panel reports: {accepted_text!r}")
                check(state_of(base, token, job_id) == "accepted",
                      "accepting moves the job to 'accepted'",
                      f"the job is {state_of(base, token, job_id)!r}, not accepted")

                page.reload(wait_until="networkidle")
                page.wait_for_selector("canvas", timeout=60_000)
                page.wait_for_timeout(1500)
                submit = page.get_by_role("button", name="Submit")
                check(submit.is_disabled(),
                      "Submit is disabled on an accepted job, rather than sending a request "
                      "the server refuses and nothing reports",
                      "Submit is still offered on an accepted job")
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
