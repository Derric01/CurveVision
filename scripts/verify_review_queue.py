#!/usr/bin/env python3
"""A reviewer can ask what is waiting for them, instead of opening every task.

    python scripts/verify_review_queue.py

`GET /jobs?mine=true` has filtered on `assignee_id` alone since early in the project, so
"what is waiting for *me* to review" could not be asked at all: a named reviewer found
submitted work by opening each task in turn and reading its job list. `reviewing=true` is
the other half of the question, and the My work page is where it is asked from.

What this drives, against the packaged application:

1. The queue holds the job that is submitted **and** named to this reviewer — not merely
   one they can see. A job they are annotating, with no reviewer named, is the control.
2. A job named to them that nobody has submitted yet is listed separately rather than
   counted as work, because a heading that says "3 waiting" when nothing can be reviewed
   sends somebody looking for work that does not exist.
3. Each row names its **task**. The queue spans every project, so "Job #2, frames 0-1" is
   an identifier for the server and nothing at all for a person.
4. A row opens the job. A queue that cannot be worked from is a report.
5. Accepting a job takes it out of the waiting half — the split is read from live state,
   not baked in at first render.

`myWork.test.ts` pins the partition and the wording without a DOM. What needs a browser is
that the page asks the server the *right question*: the partition is correct either way if
the query returns the wrong set of jobs.

Needs the packaged server (`python desktop/sidecar/build.py`, itself needing
`npm --prefix web run build` first) and a Chromium Playwright can drive; set
`CURVEVISION_CHROMIUM` if the bundled one is not found.

Note: a packaged desktop server provisions exactly one account, so the reviewer and the
annotator here are the same person. That is what makes the control job meaningful — the
queue must exclude a job this very caller is assigned to, because nobody named them to
review it.
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


def make_task(base: str, token: str, project_id: str, name: str) -> dict:
    """One task with one asset, and so exactly one job."""
    task = api(base, token, "/tasks", {
        "project_id": project_id, "name": name, "media_kind": "image",
    })
    api(base, token, f"/tasks/{task['id']}/assets", files=png_bytes(),
        filename="0.png", content_type="image/png")
    job = api(base, token, f"/tasks/{task['id']}/jobs")[0]
    return {"task": task, "job": job}


def seed(base: str, token: str, me: dict) -> dict[str, dict]:
    """Three jobs in three differently-named tasks, one for each case under test."""
    org = api(base, token, "/organizations")[0]
    project = api(base, token, "/projects", {
        "organization_id": org["id"], "slug": "queue", "name": "Queue",
        "description": "What is waiting on whom.",
        "labels": [{"name": "car", "color": "#ef4444", "allowed_shape_types": ["rectangle"]}],
    })

    ready = make_task(base, token, project["id"], "Warehouse aisle")
    drawing = make_task(base, token, project["id"], "Loading bay")
    mine = make_task(base, token, project["id"], "Street corner")
    also_mine = make_task(base, token, project["id"], "Depot ramp")

    # Named for review and submitted: the one thing the queue is for.
    api(base, token, f"/jobs/{ready['job']['id']}", {"reviewer_id": me["id"]}, method="PATCH")
    api(base, token, f"/jobs/{ready['job']['id']}", {"state": "submitted"}, method="PATCH")
    # Named for review, still being drawn.
    api(base, token, f"/jobs/{drawing['job']['id']}", {"reviewer_id": me["id"]}, method="PATCH")
    # Assigned to the same person, reviewer left empty: the control. Both are visible to
    # them, both are even theirs -- and neither is theirs to review. There are *two* of
    # them so that the summary line cannot read "1 job waiting on you" by coincidence if
    # the page asks the wrong question: a page filtering on `mine` would count two.
    for control in (mine, also_mine):
        api(base, token, f"/jobs/{control['job']['id']}", {"assignee_id": me["id"]},
            method="PATCH")
        api(base, token, f"/jobs/{control['job']['id']}", {"state": "submitted"},
            method="PATCH")

    return {"ready": ready, "drawing": drawing, "mine": mine, "also_mine": also_mine}


def row_ids(page, hook: str) -> list[str]:
    """The job ids a named list is showing, in order."""
    rows = page.locator(f"[data-job-list='{hook}'] [data-job-row]")
    return [rows.nth(i).get_attribute("data-job-row") for i in range(rows.count())]


def main() -> int:
    from playwright.sync_api import sync_playwright

    failures: list[str] = []

    def check(condition: bool, passed: str, failed: str) -> None:
        if condition:
            print(f"  ok   {passed}")
        else:
            failures.append(failed)
            print(f"  FAIL {failed}")

    with tempfile.TemporaryDirectory(prefix="curvevision-queue-") as workspace:
        process, handshake = start_server(Path(workspace) / "data")
        base, token = handshake["url"], handshake["token"]
        try:
            me = api(base, token, "/auth/me")
            seeded = seed(base, token, me)
            ready_id = str(seeded["ready"]["job"]["id"])
            drawing_id = str(seeded["drawing"]["job"]["id"])
            mine_id = str(seeded["mine"]["job"]["id"])
            print(f"four jobs, one account ({me['username']!r}): one to review, one not "
                  f"yet submitted, two only assigned\n")
            injection = json.dumps({
                "url": base, "token": token, "data_dir": handshake["data_dir"],
                "version": handshake["version"], "desktop": True,
            })

            with sync_playwright() as p:
                launch: dict[str, object] = {"headless": True}
                if (chromium := find_chromium()) is not None:
                    launch["executable_path"] = chromium
                browser = p.chromium.launch(**launch)
                page = browser.new_page(viewport={"width": 1400, "height": 1000})
                raised: list[str] = []
                page.on("pageerror", lambda exc: raised.append(str(exc)))
                page.add_init_script(f"window.__CURVEVISION__ = {injection};")
                page.goto(f"{base}/my-work", wait_until="networkidle")
                page.wait_for_selector("text=To review", timeout=30_000)
                check(not raised, "the My work page mounts without raising",
                      f"page error: {raised}")

                # ------------------------------------------------- 1 and 2: the partition
                waiting = row_ids(page, "review-waiting")
                check(waiting == [ready_id],
                      "the queue holds exactly the submitted job named to this reviewer",
                      f"the queue holds {waiting} rather than [{ready_id}]")
                later = row_ids(page, "review-later")
                check(later == [drawing_id],
                      "a job named to them but not submitted is listed apart from the work",
                      f"the not-yet-submitted list holds {later} rather than [{drawing_id}]")
                check(mine_id not in waiting and mine_id not in later,
                      "and a job they are only assigned to is in neither",
                      "the job with no reviewer named turned up in the review queue")
                assigned = row_ids(page, "assigned")
                check(mine_id in assigned,
                      "while still appearing under Assigned to me",
                      f"the assigned list holds {assigned}, without {mine_id}")

                summary = page.locator("[data-review-queue-summary]").inner_text()
                check(summary.strip() == "1 job waiting on you.",
                      "the summary counts only what can be reviewed today",
                      f"the summary reads {summary.strip()!r}")

                if waiting != [ready_id]:
                    # Everything below reads from a list that is already wrong; the click
                    # target does not exist, and a Playwright timeout would bury the four
                    # real failures above under a traceback.
                    browser.close()
                    print(f"\n{len(failures)} check(s) failed:")
                    for failure in failures:
                        print(f"  - {failure}")
                    return 1

                # ------------------------------------------------------- 3: it says where
                queue_text = page.locator("[data-job-list='review-waiting']").inner_text()
                check("Warehouse aisle" in queue_text,
                      "the row names the task rather than only the job number",
                      f"the queue row reads {queue_text.strip()!r}")

                shot = Path("/tmp/curvevision-review-queue.png")
                page.screenshot(path=str(shot))
                print(f"  (screenshot: {shot})")

                # ------------------------------------------------ 4: it can be worked from
                page.locator(
                    f"[data-job-list='review-waiting'] [data-job-row='{ready_id}']"
                ).click()
                page.wait_for_timeout(1500)
                check(page.url.endswith(f"/jobs/{ready_id}"),
                      "clicking a queued job opens it",
                      f"clicking the queue row went to {page.url}")

                # -------------------------------------------------- 5: the split is live
                api(base, token, f"/jobs/{ready_id}/review", {"accepted": True})
                page.goto(f"{base}/my-work", wait_until="networkidle")
                page.wait_for_selector("text=To review", timeout=30_000)
                check(row_ids(page, "review-waiting") == [],
                      "an accepted job leaves the waiting half",
                      f"the queue still holds {row_ids(page, 'review-waiting')} after accepting")
                still_listed = row_ids(page, "review-later")
                check(ready_id in still_listed,
                      "and is still listed, rather than vanishing from the reviewer's page",
                      f"the accepted job is in neither list ({still_listed})")
                check(page.locator("[data-review-queue-summary]").inner_text().strip()
                      == "Nothing to review yet — 2 jobs named to you when the annotator "
                         "submits.",
                      "and the summary says there is nothing to do without saying nothing is named",
                      "the summary after accepting reads "
                      f"{page.locator('[data-review-queue-summary]').inner_text().strip()!r}")

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
