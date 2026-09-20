#!/usr/bin/env python3
"""Show a provisional frame count in a real browser, and clear it from there.

    python scripts/verify_frame_count_warning.py

A video task is created with an *estimated* frame count, because counting means decoding the
whole file and that cannot happen inside an upload request. A background job normally
replaces the estimate within seconds. Sometimes it cannot: the file is truncated, or moved,
or encoded with something this build has no decoder for. `correct_frame_counts` then leaves
the estimate in place -- deliberately, because replacing it with zero would be worse -- and
the task goes on offering frames the media does not contain. An annotator who reaches one
sees what looks like missing media.

Until now that outcome was reported into a background-task row nobody reads.

**The state is manufactured the way a user would actually reach it**, through the public API
and with no reaching into the database to fake a flag: the second upload is a clip truncated
mid-stream, exactly what an interrupted transfer produces. Its header still probes -- 64x48,
2.333 seconds at 3 fps, so the task takes the estimate of six frames -- and decoding it
yields none of them. Six frames, none of which exist.

The harness asserts that premise before it asserts anything else. If a future build of PyAV
decodes the truncated file differently, this fails on the premise and says so, rather than
passing while testing nothing.

`frameCount.test.ts` proves the wording and the edge cases without a DOM. It cannot prove
that the page fetches the metadata, that the notice renders, that the button reaches
`POST /tasks/{id}/media/recount`, or -- the one that matters most -- that the notice **goes
away** once the count is trustworthy. A warning that never clears trains people to ignore
warnings.

Five claims:

1. A task whose clip was counted shows no warning, so the notice is not simply always on.
2. A task holding a file nothing could count warns, and **names the file**.
3. The recount button queues a real `media.probe_task` and disables itself rather than
   queueing a second decode of the same file.
4. The recount is honest: the file still cannot be counted, so the warning stays.
5. Removing the uncountable asset clears both the warning and the frames it invented.

Needs the packaged server (`python desktop/sidecar/build.py`) and a Chromium Playwright can
drive; set `CURVEVISION_CHROMIUM` if the bundled one is not found.
"""

from __future__ import annotations

import io
import json
import sys
import tempfile
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

from screenshot import api, find_chromium, start_server

#: 7 frames at 3 fps gives a duration of 2.333s, so the estimate `int(duration * rate)` is
#: 6. Matroska declares no frame count, which is what forces an estimate at all.
FRAMES = 7
RATE = 3
ESTIMATE = 6


def make_matroska(frames: int = FRAMES, rate: int = RATE, width: int = 64, height: int = 48):
    import av
    from PIL import Image

    buffer = io.BytesIO()
    with av.open(buffer, mode="w", format="matroska") as container:
        stream = container.add_stream("mpeg4", rate=rate)
        stream.width, stream.height, stream.pix_fmt = width, height, "yuv420p"
        for index in range(frames):
            shade = (index * 31) % 256
            picture = Image.new("RGB", (width, height), (shade, 255 - shade, 90))
            for packet in stream.encode(av.VideoFrame.from_image(picture)):
                container.mux(packet)
        for packet in stream.encode():
            container.mux(packet)
    return buffer.getvalue()


def find_truncation(whole: bytes) -> tuple[bytes, int]:
    """The shortest prefix whose header still probes but whose frames will not decode.

    Searched rather than hardcoded. The exact offset depends on how PyAV lays out the
    container, and a number that silently stops meaning what it meant would leave a harness
    that passes while exercising a perfectly good file.
    """
    from curvevision.core.errors import ValidationError
    from curvevision.media.video import VideoReader

    for cut in range(len(whole) // 4, len(whole), 8):
        prefix = whole[:cut]
        try:
            meta = VideoReader(prefix).metadata(count_frames=False)
            if not meta.duration_seconds or not meta.frame_rate:
                continue
            if VideoReader(prefix).frame_count() == 0:
                return prefix, cut
        except ValidationError:
            continue
    raise SystemExit(
        "could not build a clip whose header probes but whose frames do not decode; "
        "this harness has nothing to assert -- check whether PyAV's behaviour changed"
    )


def probes_done(base: str, token: str, task_id: str) -> int:
    """How many probe jobs for this task have reached a terminal state."""
    rows = api(base, token, f"/background-tasks?resource_id={task_id}&limit=100")["results"]
    return sum(
        1
        for row in rows
        if row["kind"] == "media.probe_task" and row["state"] in {"succeeded", "failed"}
    )


def wait_for_probe(base: str, token: str, task_id: str, after: int, seconds: float = 60.0) -> None:
    """Block until one more probe than ``after`` has finished.

    The packaged server dispatches jobs with `asyncio.create_task` rather than running them
    inside the request, so every upload here races a decode. Polling the job rows is the
    honest way to wait: a fixed sleep is either flaky or slow, and both make a failure hard
    to read.
    """
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        if probes_done(base, token, task_id) > after:
            return
        time.sleep(0.3)
    raise SystemExit(f"no probe finished for task {task_id} within {seconds:.0f}s")


def main() -> int:
    from playwright.sync_api import sync_playwright

    failures: list[str] = []

    def check(condition: bool, passed: str, failed: str) -> None:
        if condition:
            print(f"  ok   {passed}")
        else:
            failures.append(failed)
            print(f"  FAIL {failed}")

    whole = make_matroska()
    truncated, cut = find_truncation(whole)
    print(f"a {FRAMES}-frame clip ({len(whole)} bytes), and the same clip cut at {cut} bytes")

    # The premise, asserted rather than described. Everything below is about a task that
    # offers six frames none of which exist; if the truncated clip decodes, there is no
    # such task and the rest of this run means nothing.
    from curvevision.media.probe import _estimate_frame_count
    from curvevision.media.video import VideoReader

    meta = VideoReader(truncated).metadata(count_frames=False)
    estimate = _estimate_frame_count(truncated, meta.duration_seconds, meta.frame_rate)
    print(f"  its header still claims {meta.duration_seconds:.3f}s at {meta.frame_rate} fps "
          f"-> an estimate of {estimate}; decoding it yields "
          f"{VideoReader(truncated).frame_count()}\n")
    if estimate != ESTIMATE or VideoReader(truncated).frame_count() != 0:
        print("the truncated clip no longer has the shape this harness relies on")
        return 1

    with tempfile.TemporaryDirectory(prefix="curvevision-frames-") as workspace:
        process, handshake = start_server(Path(workspace) / "data")
        base, token = handshake["url"], handshake["token"]
        try:
            org = api(base, token, "/organizations")[0]
            project = api(base, token, "/projects", {
                "organization_id": org["id"],
                "slug": "frame-counts",
                "name": "Frame counts",
                "description": "Where the count is a guess, and where it is not.",
                "labels": [{"name": "car", "color": "#ef4444"}],
            })
            task = api(base, token, "/tasks", {
                "project_id": project["id"], "name": "Footage", "media_kind": "video",
            })
            task_id = str(task["id"])

            done = probes_done(base, token, task_id)
            api(base, token, f"/tasks/{task_id}/assets", files=whole,
                filename="whole.mkv", content_type="video/x-matroska")
            wait_for_probe(base, token, task_id, done)

            first = api(base, token, f"/tasks/{task_id}/media")
            print(f"  one counted clip: {first['frame_count']} frames, "
                  f"exact={first['frame_count_exact']}")
            check(first["frame_count"] == FRAMES and first["frame_count_exact"] is True,
                  "a clip the probe counted reports an exact frame count",
                  f"the counted clip reports {first['frame_count']} frames, "
                  f"exact={first['frame_count_exact']}")

            injection = json.dumps({
                "url": base, "token": token, "data_dir": handshake["data_dir"],
                "version": handshake["version"], "desktop": True,
            })

            with sync_playwright() as p:
                launch: dict[str, object] = {"headless": True}
                if (chromium := find_chromium()) is not None:
                    launch["executable_path"] = chromium
                browser = p.chromium.launch(**launch)
                page = browser.new_page(viewport={"width": 1280, "height": 1000})
                raised: list[str] = []
                page.on("pageerror", lambda exc: raised.append(str(exc)))
                page.add_init_script(f"window.__CURVEVISION__ = {injection};")

                # ------------------------------------- an exact task says nothing
                page.goto(f"{base}/tasks/{task_id}", wait_until="networkidle")
                page.wait_for_selector("text=frames", timeout=60_000)
                page.wait_for_timeout(1200)
                check(not raised, "the task page mounts without raising", f"page error: {raised}")
                check(page.locator("[data-frame-count-warning]").count() == 0,
                      "a task whose count was counted shows no warning",
                      "the warning is on a task whose frame count is exact")

                # ------------------------------------- add six frames that do not exist
                done = probes_done(base, token, task_id)
                api(base, token, f"/tasks/{task_id}/assets", files=truncated,
                    filename="truncated.mkv", content_type="video/x-matroska")
                wait_for_probe(base, token, task_id, done)

                meta = api(base, token, f"/tasks/{task_id}/media")
                print(f"  after the truncated clip: {meta['frame_count']} frames, "
                      f"exact={meta['frame_count_exact']}, "
                      f"estimated={meta['estimated_assets']}")
                check(meta["frame_count_exact"] is False,
                      "a file nothing could count leaves the task marked as an estimate",
                      f"the count is still reported exact: {meta}")
                check(meta["estimated_assets"] == ["truncated.mkv"],
                      "the server names the file whose count is a guess",
                      f"expected truncated.mkv to be named, got {meta['estimated_assets']}")
                check(meta["frame_count"] == FRAMES + ESTIMATE,
                      f"the task offers {FRAMES} real frames plus {ESTIMATE} that are not there",
                      f"the task reports {meta['frame_count']} frames, "
                      f"not the {FRAMES + ESTIMATE} the estimate gives")

                # ------------------------------------- the page says so, and names it
                page.reload(wait_until="networkidle")
                page.wait_for_selector("[data-frame-count-warning]", timeout=60_000)
                page.wait_for_timeout(600)
                check(page.locator("[data-frame-count-warning]").count() == 1,
                      "the task page warns that the frame count is an estimate",
                      "no warning on a task whose frame count is provisional")
                named = page.locator("[data-estimated-files]").inner_text().strip()
                print(f"  the warning names: {named!r}")
                check(named == "truncated.mkv",
                      "the warning names the file, not just 'something'",
                      f"the warning says {named!r} rather than naming truncated.mkv")

                shot = Path("/tmp/curvevision-frame-count.png")
                page.screenshot(path=str(shot), full_page=True)
                print(f"  (screenshot: {shot})")

                # ------------------------------------- the button reaches the server
                done = probes_done(base, token, task_id)
                recount = page.locator("[data-recount-frames]")
                check(recount.count() == 1,
                      "the warning offers a way to act on it",
                      "the warning has no recount button")
                recount.first.click()
                page.wait_for_timeout(1200)
                check(not raised, "asking for a recount raises nothing", f"page error: {raised}")
                check(recount.first.is_disabled(),
                      "the button disables itself rather than queueing a second decode",
                      "the recount button is still pressable while a decode is running")

                wait_for_probe(base, token, task_id, done)
                check(probes_done(base, token, task_id) > done,
                      "the button queued a real media.probe_task, which ran",
                      "no probe job ran after the recount button was pressed")

                # Still uncountable, so still a warning. The button does not pretend.
                after = api(base, token, f"/tasks/{task_id}/media")
                check(after["frame_count_exact"] is False,
                      "a file that still cannot be decoded still warns, rather than "
                      "the button clearing it for looking busy",
                      f"the recount silently marked an uncountable file exact: {after}")

                # ------------------------------------- removing it clears the warning
                assets = api(base, token, f"/tasks/{task_id}/assets")
                bad = next(a for a in assets if a["name"] == "truncated.mkv")
                check(bad["frame_count_exact"] is False and
                      all(a["frame_count_exact"] for a in assets if a["name"] != "truncated.mkv"),
                      "the asset list marks exactly the file that could not be counted",
                      f"asset flags are wrong: "
                      f"{[(a['name'], a['frame_count_exact']) for a in assets]}")
                api(base, token, f"/tasks/{task_id}/assets/{bad['id']}", method="DELETE")

                cleared = api(base, token, f"/tasks/{task_id}/media")
                print(f"  after removing it: {cleared['frame_count']} frames, "
                      f"exact={cleared['frame_count_exact']}")
                check(cleared["frame_count_exact"] is True,
                      "removing the uncountable file leaves a task that is exact again",
                      f"the count is still provisional after removing the bad file: {cleared}")
                check(cleared["frame_count"] == FRAMES,
                      f"and the invented frames are gone ({FRAMES} left, not "
                      f"{FRAMES + ESTIMATE})",
                      f"the task still reports {cleared['frame_count']} frames")

                page.reload(wait_until="networkidle")
                page.wait_for_selector("text=frames", timeout=60_000)
                page.wait_for_timeout(1200)
                check(page.locator("[data-frame-count-warning]").count() == 0,
                      "the warning goes away once the count is trustworthy",
                      "the warning is still on screen after the count was established")
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
    raise SystemExit(main())
