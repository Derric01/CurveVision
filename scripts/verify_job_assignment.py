#!/usr/bin/env python3
"""Hand a job to a person from the task page, and take it back.

    python scripts/verify_job_assignment.py

`Job.assignee_id` and `Job.reviewer_id` have been enforced by the policy engine and settable
through `PATCH /jobs/{id}` since the first iterations, and **no screen set either** — the web
client did not even have a `members` method to ask who the candidates were, so dividing a
task between three annotators meant three API calls. This drives the pickers that close that.

Two server bugs this exercises from the outside, both fixed alongside the pickers and both
invisible to anything that only reads the database:

* **A job could be assigned and never unassigned.** `update_job` read an omitted field and
  an explicit `null` the same way, so the nullable column had no route back to null.
* **The response named the previous holder.** `assignee`/`reviewer` are eagerly loaded and
  the sessionmaker is `expire_on_commit=False`, so writing the *id* left the loaded
  relationship stale — a job gaining its first assignee came back as `assignee: null`, which
  a picker would render straight back at the person who had just assigned it.

`assignment.test.ts` pins the pure parts without a DOM: who is offered for which field, the
ordering, and that the empty option becomes an explicit `null` rather than a dropped key.

Five claims:

1. Both pickers render on each job row, starting at Unassigned.
2. Choosing somebody assigns them — asserted against the API, not the select.
3. **And does not navigate**: the selects are siblings of the row's link, not children, so
   using one does not open the editor. The trap the timeline's keyframe markers hit.
4. The choice survives a reload, which is what the stale-relationship fix is for.
5. Choosing Unassigned clears it, and one row's pickers leave the other row alone.

Needs the packaged server (`python desktop/sidecar/build.py`, itself needing
`npm --prefix web run build` first) and a Chromium Playwright can drive; set
`CURVEVISION_CHROMIUM` if the bundled one is not found.

Note: a packaged desktop server provisions exactly one account and refuses registration
(`allow_registration=False`), so there is one candidate to pick from here. Which people are
offered for which field is a pure rule, and `assignment.test.ts` is where it is pinned.
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
#: Two frames with `segment_size=1`, so the task has two jobs and the harness can check that
#: one row's pickers do not reach into the other's.
FRAMES = 2


def png_bytes() -> bytes:
    from PIL import Image

    buffer = io.BytesIO()
    Image.new("RGB", (IMAGE_WIDTH, IMAGE_HEIGHT), "#1e293b").save(buffer, format="PNG")
    return buffer.getvalue()


def seed(base: str, token: str) -> tuple[str, list[dict]]:
    org = api(base, token, "/organizations")[0]
    project = api(base, token, "/projects", {
        "organization_id": org["id"], "slug": "assignment", "name": "Assignment",
        "description": "Handing jobs to people.",
        "labels": [{"name": "car", "color": "#ef4444", "allowed_shape_types": ["rectangle"]}],
    })
    task = api(base, token, "/tasks", {
        "project_id": project["id"], "name": "Split", "media_kind": "image",
        "segment_size": 1,
    })
    for index in range(FRAMES):
        api(base, token, f"/tasks/{task['id']}/assets", files=png_bytes(),
            filename=f"{index}.png", content_type="image/png")
    jobs = api(base, token, f"/tasks/{task['id']}/jobs")
    return str(task["id"]), list(jobs)


def job_holders(base: str, token: str, job_id: str) -> tuple[str | None, str | None]:
    """`(assignee username, reviewer username)` as the server reports them."""
    job = api(base, token, f"/jobs/{job_id}")
    assignee = job["assignee"]["username"] if job.get("assignee") else None
    reviewer = job["reviewer"]["username"] if job.get("reviewer") else None
    return assignee, reviewer


def main() -> int:
    from playwright.sync_api import sync_playwright

    failures: list[str] = []

    def check(condition: bool, passed: str, failed: str) -> None:
        if condition:
            print(f"  ok   {passed}")
        else:
            failures.append(failed)
            print(f"  FAIL {failed}")

    with tempfile.TemporaryDirectory(prefix="curvevision-assign-") as workspace:
        process, handshake = start_server(Path(workspace) / "data")
        base, token = handshake["url"], handshake["token"]
        try:
            task_id, jobs = seed(base, token)
            me = api(base, token, "/auth/me")
            print(f"a task split into {len(jobs)} jobs; one account, {me['username']!r}\n")
            first, second = str(jobs[0]["id"]), str(jobs[1]["id"])
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
                page.goto(f"{base}/tasks/{task_id}", wait_until="networkidle")
                page.wait_for_selector("text=Split", timeout=30_000)
                page.wait_for_selector("[data-assign='assignee_id']", timeout=30_000)
                check(not raised, "the task page mounts without raising", f"page error: {raised}")

                annotators = page.locator("[data-assign='assignee_id']")
                reviewers = page.locator("[data-assign='reviewer_id']")
                check(annotators.count() == len(jobs) and reviewers.count() == len(jobs),
                      f"both pickers render on each of the {len(jobs)} job rows",
                      f"found {annotators.count()} annotator and {reviewers.count()} "
                      f"reviewer pickers for {len(jobs)} jobs")
                check(annotators.first.input_value() == "",
                      "an unassigned job starts at Unassigned",
                      f"the picker started at {annotators.first.input_value()!r}")

                # ------------------------------------------------- 2 and 3: pick somebody
                # A real click first, and only then the selection. `select_option` alone
                # dispatches change without a click, so it cannot bubble to an ancestor
                # anchor -- which makes it useless for the claim below: nesting the pickers
                # inside the row's link was tried deliberately and this check still passed.
                # A check that cannot fail is worse than none.
                annotators.first.click()
                page.wait_for_timeout(400)
                navigated = not page.url.endswith(f"/tasks/{task_id}")
                check(not navigated,
                      "clicking a picker does not navigate into the editor",
                      f"clicking the picker navigated away to {page.url}")
                if navigated:
                    # Nothing after this means anything: the pickers are not on screen.
                    browser.close()
                    print(f"\n{len(failures)} check(s) failed:")
                    for failure in failures:
                        print(f"  - {failure}")
                    return 1

                # By value, not by label: what the option *reads* as is `describePerson`'s
                # rule and `assignment.test.ts` owns it. What matters here is that picking a
                # person reaches the server as that person.
                annotators.first.select_option(value=str(me["id"]))
                page.wait_for_timeout(1200)
                check(page.url.endswith(f"/tasks/{task_id}"),
                      "and neither does choosing somebody in it",
                      f"the page navigated away to {page.url}")

                assignee, _ = job_holders(base, token, first)
                check(assignee == me["username"],
                      "the first job is now held by the person picked",
                      f"the server reports the assignee as {assignee!r}")
                other_assignee, _ = job_holders(base, token, second)
                check(other_assignee is None,
                      "and the other job is untouched",
                      f"the second job was assigned to {other_assignee!r} as well")

                # ------------------------------------------------------- 4: it sticks
                page.reload(wait_until="networkidle")
                page.wait_for_selector("[data-assign='assignee_id']", timeout=30_000)
                reloaded = page.locator("[data-assign='assignee_id']").first
                check(reloaded.input_value() != "",
                      "the assignment is still shown after a reload",
                      "the picker came back empty after a reload")

                # Scrolled to the job rows: the pickers are below the fold on this page, and
                # a screenshot of the part that is not under test helps nobody debug it.
                reloaded.scroll_into_view_if_needed()
                page.wait_for_timeout(200)
                shot = Path("/tmp/curvevision-job-assignment.png")
                page.screenshot(path=str(shot))
                print(f"  (screenshot: {shot})")

                # ------------------------------------------- the reviewer, the same way
                page.locator("[data-assign='reviewer_id']").first.select_option(
                    value=str(me["id"])
                )
                page.wait_for_timeout(1200)
                _, reviewer = job_holders(base, token, first)
                check(reviewer == me["username"],
                      "the reviewer picker assigns the same way",
                      f"the server reports the reviewer as {reviewer!r}")

                # ------------------------------------------------------ 5: take it back
                page.locator("[data-assign='assignee_id']").first.select_option(value="")
                page.wait_for_timeout(1200)
                assignee, reviewer = job_holders(base, token, first)
                check(assignee is None,
                      "choosing Unassigned clears the assignment, which the API had no route to",
                      f"the job is still held by {assignee!r}")
                check(reviewer == me["username"],
                      "and clearing one field leaves the other alone",
                      f"clearing the assignee also cleared the reviewer ({reviewer!r})")

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
