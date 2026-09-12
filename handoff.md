# Handoff

> **What this file is.** The current, honest state of CurveVision, written for whoever picks
> it up next — a different session, possibly a different agent entirely. Read
> [`AGENTS.md`](./AGENTS.md) first; it is the working contract. Update this file after every
> iteration, including the ones that mostly failed.
>
> **Last updated:** 2026-09-12 (iteration 2) · branch `claude/curvevision-platform-build-n1g71n` · PRs [#1](https://github.com/Derric01/CurveVision/pull/1), [#2](https://github.com/Derric01/CurveVision/pull/2), [#3](https://github.com/Derric01/CurveVision/pull/3) all merged

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

**Video tasks are now annotatable.** Uploading a video produces a task with many frames, and
the editor is served each frame as an image — previously a frame request returned the whole
container, which made video unusable. `media/video.py` is adapted from CVAT under ADR 0007.
Frames are decoded from the start of the file, so deep scrubbing is O(n); chunked delivery
is the next piece and is what turns "works" into "comfortable".

The remaining desktop gap is the open-folder flow: the server endpoint and the shell's
native picker both exist and are tested, but the web UI does not call them yet, so importing
a folder still goes through the ordinary upload path.

---

## Next best action

**Chunked media delivery**, which is now the single thing most limiting video annotation.

Serving frame *n* decodes from the start of the file (`services/media.render_video_frame`),
so scrubbing to frame 4,000 of a long video decodes 4,000 frames. That is correct — a frame
number must identify the same picture every time, which seeking cannot guarantee — but it
is slow, and it is slow in exactly the interaction annotators do most.

The design already exists and is half-built:

| Piece | State |
| --- | --- |
| `MediaChunk` model (task, index, quality, start_frame, frame_count, storage_key) | exists |
| `media.chunk_count` / `chunk_range` helpers | exist, tested |
| `media.build_chunks` job computing the chunk *plan* | exists; writes no archives |
| `frames_per_chunk` setting (default 36) | exists |
| `VideoReader.iterate_frames(wanted)` — decode many frames in one pass | **exists, and is what makes this cheap now** |

The shape: `build_chunks` decodes each run of N frames in a single sequential pass and
stores them as one zip per chunk; the frame endpoint serves from the chunk; the client keeps
an LRU and prefetches ±1. One pass per chunk instead of one pass per frame is the whole win.

Start with the server half (build and serve chunks) and keep per-frame decoding as the
fallback for a chunk that has not been built yet — that way nothing regresses while the
background job catches up.

**Also worth doing, smaller:** `_estimate_frame_count` in `media/probe.py` still uses
container metadata at upload time, so a task's frame count can be slightly wrong for
variable-frame-rate video. `VideoReader.frame_count()` is exact. The right fix is to correct
the count in the background job (where paying for a full decode is fine) rather than in the
upload request — the `media.probe_task` job is the natural place.

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

1. **The open-folder flow in the web UI** — the last unconnected piece of the desktop
   story. Deferred this iteration because verifying Tauri IPC from the page needs a rendered
   webview, which this environment cannot provide; see *Tried and rejected*.
2. **Chunked media delivery** — see [Next best action](#next-best-action). Now the main
   limit on video annotation, and `VideoReader.iterate_frames(wanted)` already makes the
   server half cheap: one decode pass per chunk instead of one per frame.
3. **An exact frame count for video**, corrected in a background job rather than estimated
   at upload.
4. **Track-editing timeline UI.**
5. **Signed installers in CI** — one runner per platform; PyInstaller does not cross-compile.
6. **Webhook retry/backoff** wired to the job queue.

---

## Last iteration

**Made video tasks annotatable**, by adapting CVAT's decoding strategy rather than
re-deriving it.

The bug: `GET /tasks/{id}/frames/{n}/data` streamed the *blob*. For an image that is the
frame; for a video it is the entire container. Every frame of a video task returned the same
multi-megabyte file, so video annotation did not work at all.

- `server/curvevision/media/video.py` (new, adapted from CVAT `media_extractors.py` at
  `1d0c395`). Three things were worth taking and would have been painful to rediscover:
  frames are addressed **in decode order rather than by seeking**, because seeking lands on
  the nearest keyframe and a frame number must identify the same picture every time an
  annotation refers to it; frame counts are **measured by decoding**, because
  `stream.frames` is zero in many containers and wrong in others; and two real-file edge
  cases — the `DURATION` metadata fallback (Matroska routinely omits a stream duration) and
  rotation metadata, without which phone video is annotated sideways.
- `services/media.render_video_frame` decodes off the event loop, reading a blob annotated
  in place straight from its path so a large video is not loaded into memory for one frame.
- `api/v1/tasks.frame_data` serves that for video blobs.
- `media/probe.py` now delegates metadata to the reader, gaining the duration fallback, and
  keeps a *fast* frame-count estimate at upload time with the reason written down — an exact
  count means decoding the whole video, and that cannot happen inside an upload request.

**Departures from CVAT's version, deliberately:** rotation is applied when a frame becomes
an image (Pillow, already a dependency) rather than to the decoded frame (NumPy, which would
be a new one); there is no 3D/point-cloud dimension, no manifest, and no Django or DRF.

**A gap this turned up:** `av` was not in the `dev` extra, so every video test would have
**silently skipped in CI** — the hardest code in the repository shipping untested while the
suite showed green. Added, for the same reason Pillow already was.

## Verification performed

This iteration:

```
./scripts/check.sh                    all 9 steps green
  ruff · ruff format · mypy (78 files) · pytest server (188) · pytest sdk (12)
  notices (51 deps) · eslint · tsc · vitest (115)
```

28 of those server tests are new: 21 unit tests that encode a real video with PyAV and
decode it back (a mocked decoder would test the mock), and 7 API tests driving upload →
task → frame.

**The regression tests were proved to bite.** The frame-serving fix was disabled and the
suite re-run: three tests failed, including `PIL.UnidentifiedImageError` when the editor
received a container where it expected a picture. Restored, all pass. A fix never seen to
fail is a guess.

Carried forward from earlier iterations (still true, not re-run this time): the packaged
desktop binary authenticating from the injected connection, loopback-only refusal,
path-traversal probes, `kill -9` orphan prevention, and the migration up/down/up.

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
| Video tests would have skipped silently in CI | `av` was in the `media` extra but not `dev`, and CI installs `[dev]`. `pytest.importorskip` would have skipped every video test while the suite reported green. | Added to `dev`; the tests run rather than skip |

---

## Known issues

- **Deep scrubbing in a long video is slow**, by construction: frame *n* costs *n* frame
  decodes. Correct but not comfortable; chunked delivery is the fix and is the next task.
- **A video task's frame count is an estimate**, taken from container metadata at upload
  time because an exact count means decoding the whole file. It can be slightly wrong for
  variable-frame-rate video. `VideoReader.frame_count()` is exact and cheap to call from a
  background job; nothing calls it yet.
- **The desktop UI has been verified through the API, not through a rendered window.**
  The chain is covered — the shipped bundle reads the injection, the token authenticates,
  both sides pin the JSON keys — but no test renders `App.tsx` and asserts that the sign-in
  screen is skipped. The web test setup is `environment: node` with no DOM, so adding React
  rendering tests would mean jsdom plus `@testing-library/react`. Worth doing when the UI
  surface grows; not worth it for one conditional.
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

- **Computing `start_frame` from a `SUM` per asset during import.** Looked like a bug fix;
  was not. `tasks.recount_frames` already reindexes every offset from position order after
  the batch, so the per-asset value is provisional either way — and the SUM added a query per
  file, which for a 50,000-image folder is 50,000 extra queries. Reverted to the cheap
  provisional value plus an explicit `position` passed by the bulk importer.
- **`asyncio.run()` inside an async test** to drive `desktop.bootstrap`. It cannot work:
  Alembic owns its own event loop. `bootstrap` is a synchronous entry point by design; tests
  call it through `asyncio.to_thread`, exactly as the packaged sidecar does before uvicorn
  starts.
- **Building the open-folder flow this iteration**, which the previous handoff named as the
  next action. Investigated and deferred, not forgotten. The page runs with
  `withGlobalTauri: false`, so calling the shell needs either `@tauri-apps/api`
  (dynamic-imported behind `isDesktop()`) or flipping that flag. Reading the Tauri source
  settled the security question — `withGlobalTauri` only controls whether the convenience
  object is attached to `window`; the IPC bootstrap (`__TAURI_INTERNALS__`) is injected
  either way, so the real boundary is `capabilities/default.json`, not the flag. **The
  blocker is verification, not the decision:** proving IPC works from the page's remote
  origin needs a rendered webview, which this environment has no way to drive. Video
  extraction was fully verifiable and higher value, so it went first. Whoever picks this up
  should either have a desktop to test on, or add a webview-driving harness before starting.
- **Rotating video frames through NumPy**, as CVAT does. It is the natural port of their
  code, but it would add NumPy as a dependency for an operation Pillow — already required —
  performs on the very next line, when the frame becomes an image. Rotation is applied at
  image conversion instead.
- **Counting a video's frames accurately at upload time.** It is the correct number, and it
  means decoding the entire file inside an HTTP request; a two-hour video would time out.
  The fast estimate is kept at upload with the reason written in the code, and the exact
  count belongs in a background job.
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
- **Optional dependencies are installed in `dev`.** Pillow and PyAV are optional at runtime
  and the server degrades honestly without them — but a test that skips is not a test that
  passes, so CI installs both.
