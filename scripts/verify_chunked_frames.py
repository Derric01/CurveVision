#!/usr/bin/env python3
"""Step through a video in a real browser and count what the editor actually asked for.

    python scripts/verify_chunked_frames.py

Chunked delivery makes a *request-count* claim: stepping through 36 frames should cost one
request, not 36. That claim lives across a Python endpoint, a ZIP written by one language
and read by another, a cache, a prefetch and a React effect -- and every layer of it can be
unit-tested while the whole still fetches per frame. Only a browser stepping through a real
video settles it, so that is what this does.

It asserts three things:

* **The frames arrive from chunks.** Stepping through 36 frames makes a couple of chunk
  requests and at most one per-frame request, instead of 36.
* **Every frame rendered is a different picture.** A cache that handed back one frame for
  every frame number would satisfy every request count above while being completely wrong,
  and a box drawn on the wrong picture is a silent, permanent error.
* **It falls back completely.** With chunking switched off server-side, the same walk makes
  36 per-frame requests and still renders all 36 distinct frames -- no gaps.

Needs the packaged server (`python desktop/sidecar/build.py`) and a Chromium Playwright can
drive; set `CURVEVISION_CHROMIUM` if the bundled one is not found.
"""

from __future__ import annotations

import io
import json
import sys
import tempfile
from pathlib import Path

from screenshot import LABELS, api, find_chromium, start_server

FRAMES = 60
STEPS = 36
#: How long to let the canvas repaint after a keypress before sampling it.
SETTLE_MS = 400


def make_video(frames: int = FRAMES, width: int = 320, height: int = 240) -> bytes:
    """A real encoded clip whose frames survive the encoder as distinct pictures.

    The first version ramped a flat background colour by 4/255 per frame, and 13 of 36
    frames came back byte-identical to their predecessor: a step that small is quantised
    away by mpeg4, so the *clip* had duplicate frames and the check that noticed it was
    blaming the editor. A white bar that moves a full 8 pixels per frame survives any sane
    quantiser, which is what makes "every frame is a different picture" a real assertion.
    """
    import av
    from PIL import Image, ImageDraw

    buffer = io.BytesIO()
    with av.open(buffer, mode="w", format="mp4") as container:
        stream = container.add_stream("mpeg4", rate=25)
        stream.width, stream.height, stream.pix_fmt = width, height, "yuv420p"
        for index in range(frames):
            picture = Image.new("RGB", (width, height), (24, 32, 48))
            draw = ImageDraw.Draw(picture)
            x = (index * 8) % (width - 24)
            draw.rectangle([x, 0, x + 24, height], fill=(240, 240, 240))
            for packet in stream.encode(av.VideoFrame.from_image(picture)):
                container.mux(packet)
        for packet in stream.encode():
            container.mux(packet)
    return buffer.getvalue()


def seed(base: str, token: str, clip: bytes) -> tuple[str, str]:
    org = api(base, token, "/organizations")[0]
    project = api(base, token, "/projects", {
        "organization_id": org["id"], "slug": "footage", "name": "Footage",
        "description": "A clip, stepped through frame by frame.", "labels": LABELS,
    })
    task = api(base, token, "/tasks", {
        "project_id": project["id"], "name": "Clip 01", "media_kind": "video",
    })
    api(base, token, f"/tasks/{task['id']}/assets", files=clip, filename="clip.mp4",
        content_type="video/mp4")
    job = api(base, token, f"/tasks/{task['id']}/jobs")[0]
    return str(task["id"]), str(job["id"])


def walk(browser: object, base: str, job_id: str, injection: str) -> tuple[
    list[str], list[str], list[str]
]:
    """Open the editor and step through `STEPS` frames.

    Returns the chunk URLs requested, the per-frame URLs requested, and a fingerprint of
    the canvas after each step — the canvas is what the annotator draws on, so it is what
    has to be shown to change.
    """
    page = browser.new_page(viewport={"width": 1400, "height": 900})  # type: ignore[attr-defined]
    chunks: list[str] = []
    frames: list[str] = []

    def record(request: object) -> None:
        url = getattr(request, "url", "")
        if "/chunks/" in url:
            chunks.append(url)
        elif "/frames/" in url and url.endswith("/data"):
            frames.append(url)

    page.on("request", record)
    page.add_init_script(f"window.__CURVEVISION__ = {injection};")
    page.goto(f"{base}/jobs/{job_id}", wait_until="networkidle")
    page.wait_for_selector("canvas", timeout=60_000)
    page.wait_for_timeout(2500)

    drawn: list[str] = []
    for step in range(STEPS):
        if step:
            page.keyboard.press("ArrowRight")
        page.wait_for_timeout(SETTLE_MS)
        # A hash of the whole rendered canvas. An earlier version fingerprinted the tail of
        # the data URL, which is cheaper than the thing it identifies and therefore not a
        # fingerprint at all.
        drawn.append(page.evaluate(
            "() => { const c = document.querySelector('canvas');"
            " if (!c) return '';"
            " const s = c.toDataURL('image/png');"
            " let h = 5381;"
            " for (let i = 0; i < s.length; i++)"
            "   h = ((h * 33) ^ s.charCodeAt(i)) >>> 0;"
            " return s.length + ':' + h.toString(16); }"
        ))
    page.wait_for_timeout(1000)
    page.close()
    return chunks, frames, drawn


def main() -> int:
    from playwright.sync_api import sync_playwright

    failures: list[str] = []

    def check(condition: bool, passed: str, failed: str) -> None:
        if condition:
            print(f"  ok   {passed}")
        else:
            failures.append(failed)
            print(f"  FAIL {failed}")

    clip = make_video()
    print(f"clip: {FRAMES} frames, 320x240, {len(clip) // 1024} KB\n")

    for label, env, expectation in (
        ("chunked", {}, "chunks"),
        ("chunking off", {"CURVEVISION_FRAMES_PER_CHUNK": "0"}, "per frame"),
    ):
        with tempfile.TemporaryDirectory(prefix="curvevision-chunks-") as workspace:
            process, handshake = start_server(Path(workspace) / "data", env)
            base, token = handshake["url"], handshake["token"]
            try:
                task_id, job_id = seed(base, token, clip)
                meta = api(base, token, f"/tasks/{task_id}/media")
                print(f"{label}: {meta['frame_count']} frames, "
                      f"{meta['frames_per_chunk']} per chunk, {meta['chunk_count']} chunks")
                injection = json.dumps({
                    "url": base, "token": token, "data_dir": handshake["data_dir"],
                    "version": handshake["version"], "desktop": True,
                })

                with sync_playwright() as p:
                    launch: dict[str, object] = {"headless": True}
                    if (chromium := find_chromium()) is not None:
                        launch["executable_path"] = chromium
                    browser = p.chromium.launch(**launch)
                    chunks, frames, drawn = walk(browser, base, job_id, injection)
                    browser.close()

                print(f"  {len(chunks)} chunk requests, {len(frames)} per-frame requests "
                      f"for {STEPS} frames")

                if expectation == "chunks":
                    check(len(chunks) >= 1, "the editor fetched chunks",
                          "the editor never asked for a chunk")
                    # One per-frame request is expected and wanted: the first frame is
                    # asked for before `/tasks/{id}/media` has answered, so it comes down
                    # the single-frame path while the chunk is still in flight. The first
                    # picture appears without waiting for 36, and it costs a round trip
                    # rather than a decode -- the server builds the chunk to answer it.
                    check(len(frames) <= 1,
                          f"{STEPS} frames cost {len(frames)} per-frame request(s)",
                          f"still fetching per frame: {len(frames)} for {STEPS} frames")
                    check(len(chunks) < STEPS,
                          f"{len(chunks)} chunk requests beats {STEPS} frame requests",
                          f"{len(chunks)} chunk requests is no better than per-frame")
                else:
                    # The fallback has to be complete, not partial: with no chunks to be
                    # had, every frame comes from the single-frame endpoint and the editor
                    # must not be left showing gaps.
                    check(len(chunks) == 0, "no chunk was requested when there are none",
                          f"asked for {len(chunks)} chunks with chunking off")
                    check(len(frames) >= STEPS,
                          f"every one of {STEPS} frames was fetched individually",
                          f"only {len(frames)} per-frame requests for {STEPS} frames")

                check(len(set(drawn)) == len(drawn),
                      f"all {len(drawn)} rendered frames are different pictures",
                      f"only {len(set(drawn))} distinct pictures across {len(drawn)} frames")
                print()
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
