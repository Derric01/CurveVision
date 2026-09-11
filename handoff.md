# Handoff

> **What this file is.** The current, honest state of CurveVision, written for whoever picks
> it up next — a different session, possibly a different agent entirely. Read
> [`AGENTS.md`](./AGENTS.md) first; it is the working contract. Update this file after every
> iteration, including the ones that mostly failed.
>
> **Last updated:** 2026-09-11 · branch `claude/curvevision-platform-build-n1g71n` · PR [#2](https://github.com/Derric01/CurveVision/pull/2)

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

The immediate gap is that the **frontend does not yet read the connection the desktop shell
injects**, so the desktop window still shows a sign-in screen it should not. That is the next
task and it is small.

---

## Next best action

**Make the frontend desktop-aware** (`web/src`).

The shell already injects, before any page script runs:

```js
window.__CURVEVISION__ = { url, token, data_dir, version, desktop: true }
```

Nothing in `web/` reads it yet. Needed:

1. Read it at startup; when `desktop === true`, seed the API client with the token and skip
   the sign-in screen entirely (do not render a signed-out state first — the value is
   available before React mounts).
2. Hide multi-user chrome in desktop mode: organization switching, member management,
   invitations. There is one person here.
3. Wire an **Open folder…** action to `POST /tasks/{id}/local-import`, using the shell's
   native picker via `invoke("choose_folder")`. The shell also emits a `menu:open-folder`
   event from its File menu — listen for it.
4. Keep the browser path unchanged. The same bundle serves a real server with real accounts;
   `desktop` simply is not set there.

Why this one: it is the last thing between the current state and a desktop app that opens
straight into the editor, and every piece it depends on already exists and is verified.

After that: **adapt CVAT's `media_extractors.py`** for video frame extraction — see
[Remaining high-priority work](#remaining-high-priority-work).

---

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

**Also** — Python SDK and CLI, Docker Compose deployment, CI, issue/PR templates, and the
full docs set including seven ADRs.

---

## In progress

| Item | Where it stands |
| --- | --- |
| Desktop-aware frontend | Nothing started in `web/`. The shell side is done and verified. See [Next best action](#next-best-action). |
| Video annotation | Probing, the data model and frame addressing are done. Chunked extraction is not. `MediaChunk` and the chunk *plan* exist; the client still fetches one frame per request. |
| Track editing UI | Model and interpolation done on both sides; the keyframe timeline UI does not exist. |
| Webhooks | Delivery works and is signed; retry/backoff is not wired to the queue. |

---

## Remaining high-priority work

1. **Desktop-aware frontend** — above.
2. **Video frame extraction, by adapting CVAT rather than re-deriving it.** Governed by
   [ADR 0007](./docs/adr/0007-cvat-reuse-policy.md). CVAT's `media_extractors.py` is 1,649
   lines of MIT-licensed frame-accurate seeking, keyframe indexing, EXIF orientation and
   chunk writing, with only three CVAT imports and one DRF exception standing between it and
   portability. Our `server/curvevision/media/` is 182 lines of probing. **When it lands it
   must keep CVAT's copyright header, gain a `THIRD_PARTY_NOTICES.md` "Adapted source" row
   naming file and commit, and the license audit must be re-run first.**
3. **Chunked media delivery client-side** — the blocker for usable video annotation.
4. **Track-editing timeline UI.**
5. **Signed installers in CI** — one runner per platform; PyInstaller does not cross-compile.
6. **Webhook retry/backoff** wired to the job queue.

---

## Last iteration

Built the desktop shape and then re-grounded the documentation.

- **Desktop mode, in place annotation, packaging, and the Tauri shell** (commits `e0ffedc`,
  `d25f0e9`, `3e6be4c`, `4f283aa`). Notably this changed **1,460 lines added and 42 removed**
  in pre-existing code — nothing was rewritten, because the four seams and the portable
  column types were already there.
- **Docs realigned to the truth** (`c54b8ff`). The docs had described the desktop app as
  *Planned* while it worked, and had no written policy on CVAT reuse. Added ADR 0006 (one
  codebase, two shapes) and ADR 0007 (CVAT reuse, with a license audit performed rather than
  recalled), rewrote the plan's purpose section around the two shapes, and corrected Phase 9.
- **Hardened `scripts/check.sh`** — it silently skipped the notices gate that CI runs, and
  hard-coded `server/.venv/bin`, so it would fail for an agent whose environment lives
  elsewhere. Now nine steps, with a PATH fallback.
- **Added the agent protocol**: `AGENTS.md`, `CLAUDE.md`, `.claude/skills/start-work/`, and
  this file.

---

## Verification performed

```
./scripts/check.sh                    all 9 steps green
  ruff · ruff format · mypy (77 files) · pytest server (160) · pytest sdk (12)
  notices (51 deps) · eslint · tsc · vitest (107)

cargo test --lib (desktop/shell)      3 passed
cargo clippy --all-targets -D warnings clean
```

Beyond the suite, measured rather than assumed:

- **The packaged binary driven end to end**: spawn → handshake parsed from stdout → import a
  nested folder of five photographs in place → **0 files written to the app data directory**
  → frame bytes byte-identical to the originals → annotations saved → COCO export *with
  images* read back off disk.
- **Loopback-only**: connecting to the host's non-loopback address is refused
  (`ConnectionRefusedError`).
- **Path traversal**: raw un-normalised sockets (`/../../../../etc/passwd`, `/..%2f..%2f`,
  `/%2e%2e/`, `/....//`) against the SPA route — none escaped the bundle.
- **Orphan prevention**: `kill -9` on the shell; the server was gone within 0.5 s.
- **Migration** run up, down, and up again.
- **Start-up cost**: ~1.5–2.1 s spawn to handshake; 38 MB sidecar, 6.2 MB shell (Linux
  x86-64, release).
- **All 91 relative links in the docs resolve.**

---

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

---

## Known issues

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
