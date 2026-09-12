# Handoff

> **What this file is.** The current, honest state of CurveVision, written for whoever picks
> it up next — a different session, possibly a different agent entirely. Read
> [`AGENTS.md`](./AGENTS.md) first; it is the working contract. Update this file after every
> iteration, including the ones that mostly failed.
>
> **Last updated:** 2026-09-12 (iteration 8) · branch `claude/curvevision-platform-build-n1g71n` · PRs [#1](https://github.com/Derric01/CurveVision/pull/1)–[#5](https://github.com/Derric01/CurveVision/pull/5) merged · iterations 6–8 in open PR [#6](https://github.com/Derric01/CurveVision/pull/6)

---

## Status

CurveVision is a working annotation platform in **two shapes from one codebase**: a desktop
application and a server. The manual annotation path is complete end to end — create a
project and label schema, add media, draw, review, export COCO/YOLO/Pascal VOC/native — and
is tested. The desktop shape works: a packaged single-executable server, a Tauri shell that
supervises it, and folders annotated in place without copying a byte.

**The tree is green.** `./scripts/check.sh` passes all nine steps.

Honestly incomplete, and marked as such everywhere: video annotation (chunked frame
extraction), the track-editing timeline, the mask brush, the keypoint UI, quality reports,
resumable uploads, and signed desktop installers.

The desktop window **signs itself in from the connection the shell injects** and opens
straight into the application.

**Video tasks are now annotatable, and no longer slow to step through.** Uploading a video
produces a task with many frames, and the editor is served each frame as an image —
previously a frame request returned the whole container, which made video unusable.
`media/video.py` is adapted from CVAT under ADR 0007.

Frames are served from **chunks**, end to end: 36 frames decoded in one pass, stored as one
archive, and fetched by the editor as one request. Measured on a 600-frame 640×480 clip,
stepping through frames 400–435 went from 15,066 decoded frames (3.62s) to 900 (0.78s); in
Chromium, the same 36 steps went from 36 requests to 3. The cost is a sparse scrub, which
pays to build a chunk it mostly does not use — that trade, and the numbers behind it, are in
the implementation plan. Per-frame decoding and per-frame fetching both stay as fallbacks,
so a video is never *unservable*, only slower.

**The application has now been driven in a real browser**, which it never had been, and
that immediately found two defects that made it unusable and that every existing test
missed: a Content-Security-Policy that blocked the page's own scripts (the desktop window
opened blank), and frame images fetched by an `<img>` tag that cannot send a bearer token
(no image ever rendered). Both fixed, both covered by tests that assert response *headers*
rather than status codes.

`scripts/screenshot.py` is the harness that found them, and it is kept: it drives the
packaged app in Chromium, seeds a project, draws with real pointer events and photographs
the result. The README's screenshots come from it, **on real photographs** — public domain
and CC0, committed under `docs/images/samples/` with their attribution.

**The open-folder flow is connected.** The desktop build offers *Add media from this
computer* on the task page — the operating system's own dialog, then
`/tasks/{id}/local-import` with the chosen path — and File ▸ Open Folder… (Cmd/Ctrl+O) does
the same. A browser is not offered it, because a browser cannot open a native dialog. That
was the last unconnected piece of the desktop application.

---

## Next best action

**The track-editing timeline.** It is the largest remaining gap between "you can annotate
video" and "you would want to". The interpolation engine exists and is held to the same test
vectors on both sides, `Track` / `TrackShape` keyframes are modelled and stored, and the
editor already interpolates for display — what is missing is the UI for *editing* a track:
seeing its keyframes on the timeline, adding and removing them, and seeing where a track
starts and ends.

Where the pieces are:

| Piece | Where |
| --- | --- |
| `Track` + `TrackShape` keyframes, outline/visibility per keyframe | `domain/annotation.py` |
| Server-side interpolation, arc-length resampling | `services/annotations.py` |
| Client-side interpolation, same test vectors | `web/src/features/editor/interpolate.ts` |
| The frame strip the timeline would extend | `EditorPage.tsx`, the footer |

Start by rendering a track's keyframes on the existing frame strip — read-only, no editing.
That is small, visible, and it forces the data plumbing (which tracks exist in this job,
which frames are keyframes) that everything else needs.

**Then, in rough order:**

* **Pre-build chunks after a video upload.** `media.build_chunks` builds rather than plans,
  and nothing enqueues it. The care needed: the desktop queue is inline, so enqueueing at
  upload would decode a whole clip while the user waits. `media.probe_task` is now enqueued
  from the same place and already decodes the whole file to count it — so the honest move is
  probably to have *one* job that counts and builds in a single pass, rather than two that
  each walk the file.
* **Surface a task whose frame count could not be corrected.** The job reports "this task
  already has annotation work"; nothing shows it to anyone.
* **Put a browser harness in CI.** Three now exist (`screenshot.py`,
  `verify_local_import.py`, `verify_chunked_frames.py`) and between them they have found
  every defect the unit suites missed. Nightly or pre-release; each needs a packaged sidecar
  and a Chromium.

## Completed

Functionality that works and is covered by tests.

**Server** — FastAPI + SQLAlchemy 2.0 (async, typed `Mapped[...]`), Alembic, Argon2id
passwords, JWT sessions and `cv_` API tokens. Users, organizations with roles, projects,
label schemas with typed attributes, tasks, jobs, the review state machine, issues and
comments, annotation history, webhooks with HMAC signing.

**Policy engine** — a pure function `can(principal, action, context)` over a declarative
`ROLE_FLOOR` table. Role-dependent denials are 403 from the policy; role-independent state
(locked jobs, released dataset versions) is 409 from services. Exhaustively unit-tested.

**Annotation model** — `Shape` / `Track` + `TrackShape` keyframes / `Tag`. Linear
interpolation with **arc-length resampling** when polygon vertex counts differ, implemented
identically on both sides and held to the same test vectors. Optimistic concurrency on
`job.annotation_version`: a stale write is rejected with 409, never merged.

**Four seam packages** (`storage`, `formats`, `ml`, `jobs`) that never import `domain/`.
`Storage` is a 7-method protocol with local-filesystem and S3 backends. Formats is a
streaming registry with COCO, YOLO, Pascal VOC and native, plus entry-point plugin loading.
`ModelProvider` is an HTTP inference contract. `JobQueue` is inline or Dramatiq.

**Editor** (`web/src/canvas/`) — framework-agnostic TypeScript importing nothing from React.
Layered Canvas2D, R-tree viewport culling and O(log n) picking, tool state machines, a
command stack with drag coalescing, rAF-throttled input. Rectangle, polygon, polyline,
points, ellipse; selection, move, vertex editing, marquee; undo/redo, zoom/pan, keyboard
first. Autosave through an IndexedDB write-ahead buffer with 409 surfaced as a conflict.

**Desktop** — `curvevision.desktop` resolves the per-OS app data directory, migrates SQLite
with Alembic (not `create_all`), provisions one local account and workspace on first launch,
mints a fresh API token each launch while revoking the previous one, binds `127.0.0.1` on an
OS-assigned port, and prints one line of JSON for the shell. PyInstaller packaging
(`desktop/sidecar/`), and a 457-line Tauri shell (`desktop/shell/`) that supervises it,
injects the token, and provides native dialogs and menus.

**Annotating in place** — `MediaBlob.source_path` records where a file *is*.
`POST /tasks/{id}/local-import` walks a folder in a worker thread, sorts by path so frame
numbers are reproducible, and reports unreadable files in `skipped` rather than failing.
The orphan-blob collector never deletes a file it did not write. Every route in
`api/v1/local.py` returns 404 unless `settings.local_mode`.

**Exact video frame counts** — `media.probe_task` decodes each video asset to count its
frames, corrects the task and its jobs, and discards the chunks the renumbering invalidated.
Enqueued after an upload or local import that added video. Declines rather than half-applies
when annotations already exist.

**Chunked video frames, end to end** — `build_chunk` decodes a chunk's frames in one pass
and stores them as one ZIP of JPEGs named by task-global frame number, recorded as
`MediaChunk`. The frame endpoint reads from a built chunk, builds the chunk when it misses,
and decodes the single frame when neither is possible. `GET /tasks/{id}/chunks/{n}` serves
the archive; the editor fetches it through `ChunkCache` (three-archive LRU, shared in-flight
requests, ±1 prefetch) and reads it with a dependency-free stored-ZIP reader that refuses
anything it does not understand. 17× fewer decoded frames and 12× fewer requests when
stepping; the measurements and the sparse-scrub cost are in `docs/IMPLEMENTATION_PLAN.md`.

**Opening a folder from the desktop app** — *Add media from this computer* on the task page,
and File ▸ Open Folder… (Cmd/Ctrl+O), both call `chooseFolder()` and post the path. Shown in
the desktop build only. The result is summarised by a pure, separately tested function that
refuses to call a partial import a success. `scripts/verify_local_import.py` drives it in a
browser against the packaged server; ADR 0008 records how the page reaches the shell and
what about it is not covered.

**Desktop sign-in** — `web/src/desktop.ts` validates the `window.__CURVEVISION__` object the
shell injects before any page script runs. The API client prefers that token over any stored
session; it is never written to `localStorage`, because the shell mints a fresh one each
launch and revokes the previous one. In desktop mode a failed restore becomes a recoverable
`unavailable` screen with a retry, not a sign-in form the user has no password for, and the
username/sign-out chrome is replaced by the version. The injected shape is pinned from
**both** sides — a Rust test asserts the exact JSON keys, and the TypeScript tests parse the
same fixture — because a rename on one side alone would silently strand the desktop window
on a sign-in screen.

**Video frames** — `media/video.py`, **adapted from CVAT** at commit `1d0c395` under
[ADR 0007](./docs/adr/0007-cvat-reuse-policy.md), carrying its copyright header and recorded
in [THIRD_PARTY_NOTICES](./docs/THIRD_PARTY_NOTICES.md#adapted-source) with the license text
in `licenses/MIT-cvat.txt`. Frames are addressed in decode order rather than by seeking,
counted by decoding rather than trusted from the container, and served as JPEG with rotation
metadata applied. A video task is annotatable end to end.

**Serving the application from the server** — `web_root` with an SPA fallback and a
Content-Security-Policy that actually permits the page to run, which is what makes the
desktop app one executable and `curvevision-local` a complete CurveVision in a browser.

**A browser harness** — `scripts/screenshot.py` drives the packaged application in Chromium,
seeds a project, draws with real pointer events and photographs the result. It produces the
README's screenshots and found two release-blocking bugs on its first run.

**Also** — Python SDK and CLI, Docker Compose deployment, CI, issue/PR templates, and the
full docs set including seven ADRs.

---

## In progress

| Item | Where it stands |
| --- | --- |
| Open-folder flow in the web UI | Server endpoint, shell commands and menu event all exist and are tested; nothing in `web/` calls them. See [Next best action](#next-best-action). |
| Chunked media delivery | `MediaChunk`, the chunk plan job and the range helpers exist; no archives are written and the client still fetches one frame per request. Now the main limit on video. See [Next best action](#next-best-action). |
| Track editing UI | Model and interpolation done on both sides; the keyframe timeline UI does not exist. |
| Webhooks | Delivery works and is signed; retry/backoff is not wired to the queue. |

---

## Remaining high-priority work

1. **Track-editing timeline UI** — the model and interpolation exist on both sides; the UI
   does not.
2. **Pre-build chunks after a video upload.** `media.build_chunks` builds rather than plans,
   and nothing enqueues it. Careful: the desktop queue is inline.
3. **A browser harness in CI.** Three exist and between them they have found every defect the
   unit suites missed.
4. **Signed installers in CI** — one runner per platform; PyInstaller does not cross-compile.
5. **Webhook retry/backoff** wired to the job queue.
6. **`choose_files` is still unused** — the shell can open a native *file* picker as well as
   a folder one.
6. **`choose_files` is still unused.** The shell can open a native *file* picker as well as a
   folder one, and `/tasks/{id}/local-import` accepts a file path. Connecting it is small, and
   deliberately left until someone wants it — the folder case is the one that matters.

---

## Last iteration

**A video task's frame count is now counted, not guessed.** It was estimated at upload from
container metadata, because an exact count means decoding the whole file and that cannot
happen inside an HTTP request. Where the container declares a count the estimate is exact;
where it does not — **Matroska, the everyday case** — it falls back to `int(duration × rate)`
and truncation loses a frame:

    7 frames at 3 fps → duration 2.333s → int(2.333 × 3) = 6

A task that says 6 when there are 7 does not look broken. The last frame is simply never
offered, never labelled and never exported.

* **`media.probe_task` was extended rather than a new handler added.** Its stated job was
  already "recompute a task's frame index after media changed"; it just never counted. It
  now corrects every video asset by decoding (off-thread), recounts the task, rebuilds the
  jobs, and discards the chunks — which are addressed by frame range, so a renumbering
  invalidates them wholesale.
* **It is enqueued after an upload and after a local import**, and only when the task
  actually holds video, so a folder of 50,000 photographs schedules nothing.
* **It will not trample existing work.** `rebuild_jobs` refuses once a job carries
  annotations; the correction declines *entirely* rather than applying half of itself, and
  reports why. A task wrong in a known, reported way beats one quietly inconsistent.

The bug is demonstrated rather than described: `test_the_estimate_this_corrects_is_genuinely
_wrong` asserts the estimate really is 6 and the truth really is 7, so the other seven tests
cannot pass while correcting nothing.

### Iteration 7

**The editor fetches a chunk instead of 36 frames.** The server half landed last iteration;
this is the client half, and the request count is the claim it makes.

* **`web/src/media/storedZip.ts`** reads the archive. No dependency: the server writes
  `ZIP_STORED`, so every entry's bytes sit verbatim in the file and there is nothing to
  inflate — this is container parsing, and `fflate` would have been shipped for the half we
  do not need. It **refuses** what it does not understand (a compressed entry, a missing
  central directory, an entry running past the end) rather than guessing, because handing
  the editor the wrong pixels for a frame number is worse than being slow.
* **`ChunkCache`** keeps a three-archive LRU, shares one in-flight request between frames of
  the same chunk, prefetches the neighbouring chunks, and remembers a chunk that came back
  unreadable so one bad archive does not become a failed request per keystroke.
* **Every path can decline.** An image task, chunking off, an unreadable archive, a failed
  request, a frame the archive does not contain — each returns `null`, and
  `useFrameObjectUrl` falls through to the single-frame endpoint. A frame must never fail to
  appear because an optimisation did not work out.

**Measured in Chromium against the packaged server**, stepping through 36 frames:

| | Chunk requests | Per-frame requests | Distinct pictures rendered |
| --- | --- | --- | --- |
| Chunked | 2 | 1 | 36 / 36 |
| `frames_per_chunk = 0` | 0 | 36 | 36 / 36 |

The single per-frame request is the first frame, asked for before `/tasks/{id}/media` has
answered. Kept deliberately: the first picture appears without waiting for a whole chunk,
and it costs a round trip rather than a decode, because the server builds the chunk to
answer it.

### Iteration 6

**Video frames are served from pre-decoded chunks.** Reaching frame *n* means decoding *n*
frames — a frame number has to identify the same picture every time, so seeking is out — and
stepping through frames one at a time paid that cost again on every frame.

* **`build_chunk`** decodes a chunk's frames in **one** pass (`VideoReader.frames_jpeg`, new,
  sharing the rotation and encode path with `frame_jpeg` rather than duplicating it) and
  stores them as one uncompressed ZIP of JPEGs named by task-global frame number. Recorded as
  the `MediaChunk` row the model has had since the first migration.
* **The frame endpoint builds the chunk it needs**, rather than a background job doing it or
  a client asking for one. That is the decision worth understanding: reaching frame *n* costs
  *n* decodes either way, so finishing the pass to the end of the chunk is nearly free and
  makes the next 35 frames constant-time. An earlier scoping had the endpoint only *read*
  chunks, which delivered nothing at all, because nothing built any.
* **Per-frame decoding stays**, as the fallback for chunking switched off, an image asset in
  the range, or a row whose archive storage lost. Each of those has a test. A video is never
  *unservable*, only slower.
* **`GET /tasks/{id}/chunks/{n}`** serves the archive, for the client work that comes next.
* **`media.build_chunks`** now builds rather than only planning, committing each chunk as it
  goes so an interrupted run is useful. Nothing enqueues it yet — see *Next best action* for
  why that needs care in desktop mode.

**Measured**, not asserted, on a 600-frame 640×480 clip, counting every picture the decoder
produced (`iterate_frames` instrumented, since counting yielded frames would have missed the
frames walked past to reach them — the first attempt did exactly that and reported nonsense):

| Access pattern | Frames decoded before | after | Wall clock |
| --- | --- | --- | --- |
| Step through frames 0–35 | 666 | **36** | 0.84s → **0.53s** |
| Step through frames 400–435 | 15,066 | **900** | 3.62s → **0.78s** |
| Scrub: every 100th frame | 1,506 | 1,620 | 0.40s → **0.99s** |

The third row is a real regression and is recorded as one: a sparse scrub builds chunks it
mostly does not use, and each build JPEG-encodes 36 frames instead of one. It is the right
trade — annotation is overwhelmingly sequential, and those chunks make whatever follows the
scrub free — but it is a trade, not a free win.

### Iteration 5

**Connected the open-folder flow — the last unwired piece of the desktop application.**
Deferred twice before this, both times because it could not be verified; a browser harness
existed by this iteration, so it went first.

* **How the page calls the shell is now decided and recorded**: `@tauri-apps/api`, imported
  dynamically behind `isDesktop()`, with `withGlobalTauri` left `false`. The build confirms
  the split — `core-*.js` (0.24 kB) and `event-*.js` (1.45 kB) are separate chunks a browser
  never requests. Reasoning, and the two rejected alternatives, in
  [ADR 0008](./docs/adr/0008-calling-the-desktop-shell.md).
* **The whole shell surface is three functions** in `web/src/desktop.ts` — `chooseFolder`,
  `onOpenFolder`, `OPEN_FOLDER_EVENT` — because that is the part with no automated coverage
  anywhere, and it should be as small as it can be. Their browser contract (`null` from the
  picker, a no-op subscription) is itself tested, so the browser build cannot start throwing
  from a menu subscription at mount.
* **The task page offers *Add media from this computer*** in the desktop build only, and the
  shell's File ▸ Open Folder… menu event does the same thing through the same function.
* **The result is reported honestly.** `summariseImport` is a pure function with its own
  tests, and the case it exists for is the partial one: the server attaches what it can and
  lists what it could not, so a folder with one corrupt file must never be reported as a
  clean success. It also distinguishes "no media found" from "nothing could be read", which
  are different problems with different fixes.
* **`scripts/verify_local_import.py`** drives all of it in Chromium against the packaged
  server — eleven checks: the browser build is not offered the picker, the desktop build is,
  mounting with no shell raises nothing, and a missing shell shows an error rather than
  white-screening.

  Then it **clicks the whole thing through**. `invoke` bottoms out at
  `window.__TAURI_INTERNALS__.invoke`; standing in for *that*, and only that, leaves
  everything above it real — the click, the dynamic import, the HTTP call, the cache
  invalidation, and the sentence the user reads. On a folder of four photographs plus one
  corrupt image the page says *"Imported 4 files; skipped 1 file. This task now has 4
  frames."* and names `truncated.jpg` with the server's own reason. This is not a claim that
  IPC works; it is how much can be checked without it.

  The browser-build check was confirmed to fail when the `isDesktop()` guard is removed.

### Iteration 4

**Replaced the screenshots' synthetic frames with real photographs, and fixed what that
exposed.** The complaint was that the imagery did not look real, and it was right: the old
frames were vector shapes drawn in PIL.

* **Four real photographs are now committed** under `docs/images/samples/` — an espresso cup
  (CC0), a cat (CC0), an astronaut portrait (NASA, public domain) and a rocket on the pad
  (SpaceX, public domain). `scripts/extract_sample_images.py` is how they were produced, and
  `docs/images/samples/CREDITS.md` carries licence and attribution per file. scikit-image was
  a one-time extraction tool, **not** a new dependency; the JPEGs are the artefact.
* **The label schema now matches the imagery** (`cup`, `saucer`, `spoon`, `cat`, `person`,
  `rocket`) and covers the whole set rather than only the frame being photographed.
* **Boxes are measured in image pixels, not viewport fractions.** `canvas_point()` in the
  screenshot script mirrors `fitToImage` from `web/src/canvas/viewport.ts`, so a box measured
  once off the photograph lands on the object at any window size. The old fractions were
  tuned to one layout and would have slid off it silently.
* **The project page is photographed last**, after the annotations are drawn, so its
  statistics (`Shapes 3`, `Unannotated frames 3`, class distribution) describe work that
  actually happened. Captured before, it showed an empty project next to a full editor.
* **The trim height is measured, not hard-coded** — a magic `770` had already stopped
  matching the page and cut a card in half once the statistics grew a row.

Two defects came out of looking at the result:

* **The editor's label list clipped through the middle of a row** at six labels — a fixed
  `max-h-52` (13rem) cap. Now proportional (`max-h-[30vh]`), so a normal window shows the
  whole schema and a short one still scrolls.
* **Two licensing claims were false.** `THIRD_PARTY_NOTICES.md` and
  `ARCHITECTURE_COMPARISON.md` both still asserted "No CVAT source code is present in this
  repository" while the same notices file listed `media/video.py` as adapted from CVAT three
  sections earlier. Iteration 3 corrected that sentence in the README and missed its two
  copies. Both now point at the adaptation table as the authoritative list.

### Iteration 3 (for context)

**Drove the application in a real browser for the first time, and fixed the two defects
that found.** Also rewrote the README as a landing page.

Both bugs hid behind a 200, which is why 300 tests missed them:

* **The page never loaded.** `Content-Security-Policy: default-src 'none'` is exactly right
  for a JSON API and was written when that is all this process was. Once the same process
  began serving the web bundle, it blocked every script and stylesheet. The desktop window
  had been opening blank. The API keeps the strict policy; only responses that *are* the
  application get one that lets it run, and an API-only deployment is untouched.
* **No image ever rendered.** The editor set `<img>.src` to the frame endpoint with
  `crossOrigin = 'use-credentials'`, which sends cookies — but the API authenticates with
  bearer tokens, so every frame was a 401 and the canvas drew a broken image. Frames are now
  fetched through the API client and handed over as object URLs, revoked when replaced.

`scripts/screenshot.py` is the harness that found both, kept deliberately. It seeds a
project from synthetic frames, drives the real packaged app in Chromium, draws with real
pointer events, and photographs the result.

**README rewritten as a landing page**: hero screenshot of the working editor, the problem
in the reader's words, three columns on why it exists, then the fastest path to running it.
Badges limited to verifiable facts — CI, licence, language versions — with no download
counts, star counts or coverage number there is no gate for.

**Two claims in it had gone false**, which matters more than the layout: it still said the
desktop application was Planned with no working build, and that no CVAT source code was
present. Both corrected; the second is a licensing claim and was the more urgent.

## Verification performed

```
./scripts/check.sh                    all 9 steps green
  ruff · ruff format · mypy (78 files) · pytest server (221) · pytest sdk (12)
  notices (52 deps) · eslint · tsc · vitest (151)
```

Iteration 8 added 8 tests in `server/tests/api/test_frame_count_correction.py` (221 server
tests, up from 213) and confirmed they bite: with the correction disabled, 6 of the 8 fail,
including the one that matters — `test_the_frame_the_estimate_lost_is_servable`, which asks
for the last frame over HTTP and gets it.

Iteration 7 added 26 web tests (151 total, up from 125): 7 on the ZIP reader, with fixtures
built by Node's own zlib rather than by the reader under test, and 19 on the cache, which
count *requests* — a cache that produced the right pixels while still fetching per frame
would pass a pixel test and miss the entire point.

`scripts/verify_chunked_frames.py` then drove it in Chromium against the packaged server,
in both configurations, with the numbers in the table above. It found two things worth
recording:

* **The test clip had duplicate frames, and the check blamed the editor.** 13 of 36 frames
  came back byte-identical to their predecessor. Decoding the clip directly server-side
  reproduced exactly the same 13 positions — a 4/255 colour ramp is quantised away by
  mpeg4. The fixture was wrong, not the code; it now moves a bar 8 pixels per frame.
* **The first fingerprint was the tail of a data URL**, which collided between frames that
  were genuinely different. A fingerprint cheaper than the thing it identifies is not one.

Iteration 6 added 12 tests in `server/tests/api/test_video_chunks.py` — 213 server tests in
total, up from 201 — and confirmed three of them bite by breaking the code they cover:

| Broken deliberately | Caught by |
| --- | --- |
| The chunk path disabled entirely | `test_serving_a_frame_builds_its_chunk_and_the_next_frames_reuse_it` |
| The offset → task-frame mapping shifted by one | `test_a_frame_from_a_chunk_is_the_same_picture_as_a_decoded_one` |
| The SAVEPOINT and `IntegrityError` handling removed | `test_two_builders_racing_for_the_same_chunk_produce_one_row` |

The second is the one that matters: a chunk that silently changed which picture frame 7 is
would move every annotation already drawn on it.

Iteration 5 ran `scripts/verify_local_import.py` against a freshly built bundle
and sidecar — eleven checks, all green — and confirmed it bites by removing the `isDesktop()`
guard and watching the browser-build check fail.

Iteration 4 rebuilt the web bundle and the PyInstaller sidecar and re-ran
`scripts/screenshot.py` against the packaged binary end to end: four photographs uploaded,
three boxes drawn through real pointer events, autosave settled, both screenshots
regenerated. Each screenshot was then **looked at**, including a 1:1 crop — which is what
caught the label-list clipping and the 4x-magnified mush described under *Tried and
rejected*. A screenshot script that is never run with human eyes on its output is a script
that ships a broken picture.

13 of those server tests are new and assert response **headers** — what a browser acts on,
and what nothing previously checked.

**In a real browser, against the packaged binary** (this is the verification that was
missing, and it is the reason this iteration found anything):

| Check | Before | After |
| --- | --- | --- |
| Page renders | blank; 5 CSP violations in console | renders, console clean |
| Frame image | 401, `drawImage` on a broken element | image drawn |
| Drawing with real pointer events | n/a | 5 shapes, mixed labels, autosave settled |
| Desktop injection path end to end | never exercised in a browser | works; the version chip confirms it |

122 relative links and images across the docs resolve.

## Bugs fixed

| Bug | Root cause | Verified by |
| --- | --- | --- |
| Handlers read a different configuration than their own app | `SettingsDep` resolved via `get_settings()` (the **environment**) while the engine was bound to `create_app(settings)`. Silently wrote media to the wrong directory and made `local_mode` false inside a local install. | `test_request_handlers_read_the_app_s_settings_not_the_environment` |
| Background jobs could disagree with the request that queued them | Job handlers, the storage factory and the queue factory all call `get_settings()`. Added `configure_settings()`, bound in the app lifespan, mirroring `configure_engine()`. | Full suite; media now lands in the app data dir |
| A moved/deleted source file returned a truncated 200 | The error was raised inside the streaming response body, after the status line had been sent. | `test_a_file_the_user_moved_reports_a_missing_frame_not_a_server_error` |
| Packaged binary died at Alembic | Frozen entry script unpacks at the bundle root while its data keeps the package path, so `__file__` pointed nowhere. Now resolves through `sys._MEIPASS`. | Smoke test in `desktop/sidecar/build.py` |
| `check.sh` skipped the notices gate CI runs | Step was never added. | Nine steps now run; gate caught PyInstaller immediately |
| Two ADR links pointed at filenames that do not exist | Guessed names. | Link checker over 91 links |
| Two doc sections both numbered 0.2 | New section collided with Phase 0's numbering. | Manual |

| A video frame request returned the entire video | `frame_data` streamed the blob. For an image the blob *is* the frame; for a video it is a container holding thousands. Video annotation did not work at all. | `tests/api/test_video_media.py` — proved to fail with the fix disabled |
| The web application never loaded when the server served it | `Content-Security-Policy: default-src 'none'` — correct for a JSON API, and correct when written — blocked every script and stylesheet once the same process began serving the bundle. The desktop window opened blank. Every request returned 200. | `tests/api/test_web_app_serving.py` (13 tests), plus a real browser with a clean console |
| No frame image ever rendered in the editor | `<img>.src` on the frame endpoint with `crossOrigin = 'use-credentials'` sends cookies; the API uses bearer tokens, so every frame was 401 and the canvas drew a broken element. | Frames fetched through the API client as object URLs; verified by screenshotting a drawn annotation over a rendered frame |
| Video tests would have skipped silently in CI | `av` was in the `media` extra but not `dev`, and CI installs `[dev]`. `pytest.importorskip` would have skipped every video test while the suite reported green. | Added to `dev`; the tests run rather than skip |
| The editor's label list was cut through the middle of a row | A fixed `max-h-52` (13rem) cap on the list; six labels need ~14rem. Functional — it scrolled — but it looked broken, and a six-label schema is not unusual. Now `max-h-[30vh]`. | Regenerated screenshot: all six labels visible, `OBJECTS` heading intact below |
| Two documents claimed no CVAT source code is present, while a third section of one of them listed the file that is | Iteration 3 corrected that sentence in the README only; `THIRD_PARTY_NOTICES.md` and `ARCHITECTURE_COMPARISON.md` kept their copies. A licensing claim that contradicts itself three sections apart is worse than no claim. | Both now defer to **THIRD_PARTY_NOTICES § Adapting CVAT code** as the authoritative list |

---

## Known issues

- **A sparse scrub costs more than it used to.** Jumping to every 100th frame builds a chunk
  at each landing that it mostly does not use, and each build JPEG-encodes 36 frames rather
  than one: 0.40s → 0.99s across six such jumps in the measurement. Sequential stepping — what
  annotation actually is — got 4.6× faster in exchange, and the chunks a scrub builds make the
  work that follows it free. If this ever needs fixing, building the chunk *after* answering
  the request is the obvious route; it costs a second walk to the chunk start, so measure
  before adopting it.
- **The first frame of a chunk is slower than it was**: 36 decodes where there used to be 1.
  Unavoidable if the other 35 are to be free, and it is why the measurement above reports
  frames 0–35 as a whole rather than frame 0 alone.
- **The stored-ZIP reader handles only what this server writes.** No ZIP64, no compressed
  entries, no encryption. That is deliberate — both ends of the format are ours — and it
  refuses rather than guesses, so the cost of meeting something else is a fallback to
  per-frame fetching, not a wrong picture. If the server ever compresses a chunk, the client
  silently gets slower; the archive format is effectively part of the API contract now.
- **A video task's frame count is an estimate for as long as the correction job takes.** The
  upload still records `int(duration x rate)` when the container declares no count, and
  `media.probe_task` replaces it with a counted one immediately afterwards. Between the two,
  the task is briefly wrong — which is the right trade, because the alternative is decoding
  a two-hour clip inside an HTTP request.
- **A task that already has annotations keeps a wrong frame count.** `rebuild_jobs` refuses
  to repartition frames under existing work, and the correction declines rather than
  applying half of itself. The job reports it; nothing surfaces that to a user yet. Rare by
  construction (the job runs right after upload), but it is a real hole: a task that
  acquired annotations before the job ran stays wrong permanently.
- **The browser harnesses are not in CI.** `scripts/screenshot.py` found two
  release-blocking bugs in one run, and `scripts/verify_local_import.py` is the only check on
  the desktop import flow; nothing stops either regressing automatically. Both need a
  packaged sidecar and a Chromium, so neither is a cheap CI job — but they are the only
  things that test the product as a user meets it. Worth a nightly or pre-release job.
- **Playwright drives Chromium, not the Tauri webview.** The React half is now testable end
  to end; an actual `invoke()` across the IPC bridge still is not.
- **The screenshots are generated, not committed by hand** — re-run `scripts/screenshot.py`
  after any visible UI change or they will drift from the product, which is the specific
  failure mode a screenshot in a README has.
- **The window-close path is code-reviewed, not exercised.** Graceful shutdown is covered by
  a Rust test that boots the real packaged server and proves shutdown reaps it, and the crash
  path is verified by `kill -9` — but *a human clicking the close button* could not be tested
  headlessly. Worth exercising on a real desktop.
- **No signed installers.** `cargo build --release` produces a working binary; producing
  `.dmg`/`.msi`/`.AppImage` needs the Tauri CLI and one runner per platform. Not yet in CI.
- **Docker images have never been built here.** Docker Hub is blocked from this environment
  (403 via the egress proxy). CI's docker job is the first real verification of the
  Dockerfiles.
- **Thumbnails are not generated for in-place media**, deliberately — that would mean writing
  a derived copy of a file we were asked not to copy. The editor scales the original. If this
  becomes a performance problem, the fix is a cache in the app data dir, not a silent copy.
- **Folder import sorts by full path**, so `survey/2024/IMG_0001.jpg` precedes
  `survey/IMG_0000.jpg`. Deterministic and documented, but may surprise. Change only with a
  reason, and update the test that pins it.

---

## Tried and rejected

- **Shipping `fflate` to read the chunk archives.** The obvious "reuse before you build"
  answer, and wrong here: the server writes `ZIP_STORED`, so there is nothing to decompress
  and the library would have been carried for the half we do not use. What is actually
  needed is ~90 lines of container parsing that refuses anything unexpected. Revisit the
  moment the server has a reason to compress a chunk — at which point the library is the
  right answer, not a hand-rolled inflater.
- **Fingerprinting a rendered canvas by the tail of its data URL.** Cheap, and it reported
  collisions between frames that were genuinely different. A fingerprint cheaper than the
  thing it identifies is not a fingerprint. Hash the whole string.
- **Trusting a synthetic test clip to have distinct frames.** A 4/255 colour ramp per frame
  is quantised away by mpeg4: 13 of 36 frames came back identical to their predecessor, and
  the browser check duly reported the editor as broken. Decoding the clip server-side
  reproduced the same 13 positions, which is what isolated it. Move something by 8 pixels,
  not 4 grey levels — and when a check fails, reproduce it one layer down before believing it.
- **A chunk endpoint that only *reads* chunks, with a background job building them.** The
  first scoping of chunked delivery. It passed its tests and delivered nothing: nothing
  enqueued the job, so no chunk ever existed, so every frame took the fallback path and the
  change was invisible. Caught by asking what a user would notice, which is the question the
  contract puts at the end of the task-ranking list for exactly this reason. The endpoint
  that needs the data is the one that should build it.
- **Counting decoded frames by wrapping `iterate_frames`.** The first measurement did this
  and reported 36 decodes for a walk that really cost 15,066: the wrapper counts frames
  *yielded*, and the cost being measured is the frames walked past to reach them. The
  instrumentation has to go inside the decode loop. A benchmark that measures the wrong thing
  is worse than no benchmark, because it gets quoted.
- **Packing image assets into chunks.** An image already serves in constant time, and putting
  one in a chunk would mean re-encoding somebody's PNG as a JPEG for no gain. Chunks hold
  video frames only, which is why every chunk read falls back rather than assuming a hit.
- **`session.rollback()` when losing the build race.** Correct for the row, wrong for the
  caller: it discards whatever else that session had pending. A SAVEPOINT (`begin_nested`)
  confines it, and the race test fails without it.
- **Hand-rolling photorealism in PIL.** The first answer to "the images do not look real"
  was a more elaborate synthetic renderer — single vanishing point, atmospheric perspective,
  contact shadows, depth of field, sensor grain, vignette. It was rendered, looked at, and
  was **worse**: noisy, with buildings floating translucent over the road, and still
  unmistakably vector art. Deleted rather than kept alongside the original. The ceiling on
  procedural realism in a 2D drawing library is low, and real public-domain photography costs
  four files and an attribution table. Do not re-attempt this.
- **Fetching photographs from the obvious places.** Wikimedia Commons, NASA and stock
  libraries are all refused by the egress proxy (403 on CONNECT). PyPI and GitHub are
  reachable, which is why the images come from a Python package's bundled sample data.
  Anything larger in that dataset needs `pooch` and is either poorly suited (a retinal
  fundus, a deep field) or of unclear licence (Middlebury stereo pairs), so 600×400 is the
  practical ceiling here.
- **Capturing the editor at `device_scale_factor=2`.** Sharper chrome, *softer* photograph:
  the editor already fits a 600×400 frame to a ~1200px canvas, and doubling that magnifies it
  to 4× native, which is visibly mushy in a 1:1 crop. Now captured at 1, which is close to
  1:1 for a README on a high-density display anyway. If a larger source photograph ever
  becomes available, revisit — the trade only exists because the frames are small.
- **Computing `start_frame` from a `SUM` per asset during import.** Looked like a bug fix;
  was not. `tasks.recount_frames` already reindexes every offset from position order after
  the batch, so the per-asset value is provisional either way — and the SUM added a query per
  file, which for a 50,000-image folder is 50,000 extra queries. Reverted to the cheap
  provisional value plus an explicit `position` passed by the bulk importer.
- **`asyncio.run()` inside an async test** to drive `desktop.bootstrap`. It cannot work:
  Alembic owns its own event loop. `bootstrap` is a synchronous entry point by design; tests
  call it through `asyncio.to_thread`, exactly as the packaged sidecar does before uvicorn
  starts.
- **Keeping `default-src 'none'` on the served web application.** It reads as the secure
  choice and it is not a choice at all: it blocks the page's own scripts, so the result is
  a blank window, not a hardened one. The API keeps it; the application gets the narrowest
  policy that still runs. If someone tightens this again, the tests in
  `test_web_app_serving.py` say why they should not.
- **Serving frames from a public bucket, or via a query-string token**, to make `<img src>`
  work. Both give up the permission check the media endpoint performs, which is the point
  of it. Fetching with the client's credential and wrapping the bytes in an object URL keeps
  authorisation intact and costs one hook.
- **Deferring the open-folder flow, twice** — *resolved in iteration 5, kept because the
  reasoning still applies to anything else that touches IPC.* Both deferrals were for the
  same reason: proving `invoke()` works from the page needs a rendered Tauri webview, and
  this environment has no way to drive one. That never became possible. What changed is the
  conclusion drawn from it — the *uncoverable* part is three functions, and everything
  around them (which build offers the picker, what happens when the shell is missing, what
  the user is told about a partial import) is coverable and now covered. Waiting for total
  coverage would have meant waiting forever. See ADR 0008.
- **Flipping `withGlobalTauri` to `true`, and calling `__TAURI_INTERNALS__` directly.** The
  two alternatives to taking the `@tauri-apps/api` dependency. Reading the Tauri source
  settled what had been treated as a security question: `withGlobalTauri` only controls
  whether the convenience object is attached to `window`; the IPC bootstrap is injected
  either way, so the real boundary is `capabilities/default.json`, not the flag. That makes
  it a question of surface area and types, which the dependency wins — and calling the
  internals directly buys the same thing with no stability contract at all. ADR 0008.
- **Rotating video frames through NumPy**, as CVAT does. It is the natural port of their
  code, but it would add NumPy as a dependency for an operation Pillow — already required —
  performs on the very next line, when the frame becomes an image. Rotation is applied at
  image conversion instead.
- **Counting a video's frames accurately at upload time.** It is the correct number, and it
  means decoding the entire file inside an HTTP request; a two-hour video would time out.
  The fast estimate is kept at upload with the reason written in the code, and the exact
  count is made by `media.probe_task` immediately afterwards. *Resolved in iteration 8; the
  reasoning still stands for anything else tempted to decode inside a request.*
- **Seeking to a video frame instead of decoding to it.** Much faster, and wrong: seeking
  lands on the nearest keyframe and container timestamps are approximate, so the same frame
  number can resolve to different pictures. Annotations are anchored to frame numbers, so
  that trade is not available. Chunked delivery recovers the speed without giving up the
  guarantee.
- **Storing the injected desktop token in `localStorage`** alongside ordinary sessions.
  Tempting because it needs no change to the client, but wrong: the shell mints a fresh
  token each launch and revokes the previous one, so a stored copy is a stale credential
  that outlives the session that owned it. The token now lives only in memory, read from
  the injected object.
- **Loading the desktop window from a `tauri://` asset URL.** Would have created a second
  origin, a CORS boundary and a desktop-only auth path. The window loads the editor from the
  local server instead — same-origin by construction.

---

## Decisions

Significant ones have ADRs; these are the ones a future agent would otherwise second-guess.

- **[ADR 0006](./docs/adr/0006-one-codebase-two-shapes.md) — one codebase, two shapes.** The
  difference between desktop and server is a `Settings` value in every case. `services/` must
  not be able to tell which shape it is in. **A change that improves one shape by degrading
  the other is the wrong change.**
- **[ADR 0007](./docs/adr/0007-cvat-reuse-policy.md) — CVAT reuse.** Audited at commit
  `1d0c395`: one MIT `LICENSE`, `SPDX-License-Identifier: MIT` on all 1,473 source headers.
  Reuse pays for hard-won domain knowledge (video decoding), costs for framework glue (Django,
  DRF, SVG.js). Adapt `media_extractors.py`; do not adapt `cvat-canvas` — Canvas2D was chosen
  on a *measured* ~500× picking advantage at 100k shapes ([ADR 0003](./docs/adr/0003-canvas2d-with-spatial-index.md)).
- **Desktop-only capability is gated at the API edge, never in a service.** Reading arbitrary
  local paths is a feature on your own machine and arbitrary file disclosure on a shared one.
  `api/v1/local.py` returns 404 unless `local_mode`, so the capability is *absent* on a
  server rather than merely forbidden.
- **The server can serve the web bundle** (`web_root`, SPA fallback). This is what makes the
  desktop app one executable and the editor same-origin with its API. A container deployment
  leaves it unset and keeps nginx in front.
- **A fresh desktop token per launch, previous revoked**, so a token leaked into a log or a
  crash report stops working on restart.
- **A frame number identifies a picture, permanently.** This is the constraint behind the
  video design: it rules out seeking, forces decode-order addressing, and makes chunking
  (rather than caching or approximation) the correct way to get speed back. An annotation
  that drifts to a different frame is worse than a slow one.
- **The product gets tested as a user meets it, not only through its API.** Two defects
  that made the application unusable survived 300 passing tests because both return 200 to
  a non-browser client. Status codes and response bodies are not enough; headers and a real
  rendering engine are.
- **[ADR 0008](./docs/adr/0008-calling-the-desktop-shell.md) — reaching the shell.** The page
  calls Tauri through `@tauri-apps/api`, dynamically imported behind `isDesktop()`, with
  `withGlobalTauri` left `false`. The corollary matters more than the choice: **anything that
  cannot be covered gets confined, not avoided.** IPC has no automated coverage anywhere and
  will not get any here, so it lives in three functions whose browser behaviour *is* tested,
  and every caller is written against that contract.
- **Screenshots are generated from the running product, never mocked up.** A flattering
  mockup is a lie anyone who installs the app can check.
- **Optional dependencies are installed in `dev`.** Pillow and PyAV are optional at runtime
  and the server degrades honestly without them — but a test that skips is not a test that
  passes, so CI installs both.
