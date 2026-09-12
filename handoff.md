# Handoff

> **What this file is.** The current, honest state of CurveVision, written for whoever picks
> it up next — a different session, possibly a different agent entirely. Read
> [`AGENTS.md`](./AGENTS.md) first; it is the working contract. Update this file after every
> iteration, including the ones that mostly failed.
>
> **Last updated:** 2026-09-12 · branch `claude/curvevision-platform-build-n1g71n` · PRs [#1](https://github.com/Derric01/CurveVision/pull/1), [#2](https://github.com/Derric01/CurveVision/pull/2), [#3](https://github.com/Derric01/CurveVision/pull/3) all merged

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

The desktop window now **signs itself in from the connection the shell injects** and opens
straight into the application. The remaining desktop gap is the open-folder flow: the server
endpoint and the shell's native picker both exist and are tested, but the web UI does not
call them yet, so importing a folder still goes through the ordinary upload path.

---

## Next best action

**Wire the open-folder flow into the web UI.**

Everything it depends on exists and is verified; this is the last piece of the desktop
story that is not connected end to end.

Available already:

| Piece | Where |
| --- | --- |
| `POST /tasks/{id}/local-import` `{path, recursive}` | `server/curvevision/api/v1/local.py`, 404s unless `local_mode` |
| `choose_folder` / `choose_files` Tauri commands | `desktop/shell/src-tauri/src/lib.rs` |
| `menu:open-folder` event (File ▸ Open Folder…, Cmd/Ctrl+O) | emitted by the shell, nothing listens |
| `desktop` connection object in the page | `web/src/desktop.ts` |

What it needs:

1. **A way to call the shell.** The shell runs with `withGlobalTauri: false`, so
   `window.__TAURI__` does not exist and the page cannot `invoke` without
   `@tauri-apps/api`. Two options, and this is the one real decision in the task:
   - add `@tauri-apps/api` and **dynamic-import** it behind the `isDesktop()` check, so a
     browser never downloads it — needs a `docs/THIRD_PARTY_NOTICES.md` entry (the CI gate
     will catch it if forgotten); or
   - flip `withGlobalTauri: true` and use `window.__TAURI__.core.invoke`, no dependency,
     but it exposes the whole JS API surface to the page.
   Prefer the first: it keeps the tighter Tauri setting, and the capability allow-list in
   `capabilities/default.json` already limits what is reachable either way.
2. An **Open folder** action on the task page, shown only when `isDesktop()`.
3. A listener for the `menu:open-folder` event doing the same thing.
4. Surface the result honestly: `imported` count, and the `skipped` list when non-empty —
   the endpoint deliberately reports per-file failures rather than failing the import.

Verify with the real packaged app, not just unit tests: build the web bundle, run
`desktop/sidecar/build.py`, and drive it (`/tmp` scratch scripts from previous iterations
show the pattern — spawn, read the handshake, call the API with its token).

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

**Also** — Python SDK and CLI, Docker Compose deployment, CI, issue/PR templates, and the
full docs set including seven ADRs.

---

## In progress

| Item | Where it stands |
| --- | --- |
| Open-folder flow in the web UI | Server endpoint, shell commands and menu event all exist and are tested; nothing in `web/` calls them. See [Next best action](#next-best-action). |
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

**Made the desktop window sign itself in** — the blocker between a packaged app that runs
and one that is usable.

- `web/src/desktop.ts` (new) reads and validates the injected connection. Only two fields
  are load-bearing (`desktop === true`, and a non-empty token); the rest is cosmetic and
  tolerated when missing, so the shell gaining or dropping an informational field cannot
  break the app. The `desktop` flag is checked for `=== true` rather than truthiness
  specifically so a stray global cannot switch off authentication.
- `api/client.ts` prefers the injected token over `tokenStore`. One line; the browser path
  is untouched, and the existing refresh logic already no-ops without a refresh token.
- `store/session.ts` gained an `unavailable` status for desktop-mode restore failures.
- `App.tsx` renders that state with the underlying error and a **Try again** button, and
  swaps the username/sign-out chrome for the version in desktop mode.
- Two Rust tests pin the injected JSON contract from the shell's side.

Also updated the status markers in `desktop/README.md`, `docs/ROADMAP.md` and
`docs/IMPLEMENTATION_PLAN.md` Phase 9, and split "desktop-aware frontend" into the part
that is now **Done** (authentication) and the part that is not (open-folder).

**A self-review caught two things before this landed:** the error screen originally asserted
"the server rejected this session", which is only one of the causes that reach that state —
a connection failure reaches it too — and it offered no way out but restarting the whole
application. Both fixed.

## Verification performed

This iteration:

```
./scripts/check.sh                    all 9 steps green
  ruff · ruff format · mypy (77 files) · pytest server (160) · pytest sdk (12)
  notices (51 deps) · eslint · tsc · vitest (115 — 8 new)

cargo test --lib (desktop/shell)      5 passed (2 new: the injected JSON contract)
```

Then against the **real packaged application**, because unit tests cannot prove the shipped
bundle and the shell agree — rebuilt `web/dist`, repackaged the sidecar, spawned the binary
and read its handshake:

| Check | Result |
| --- | --- |
| The served bundle contains code reading `__CURVEVISION__` | yes, in `/assets/index-*.js` |
| `GET /api/v1/auth/me` with the injected token | 200, user `local`, superuser |
| The same call with no token | 401 — loopback is the boundary, not anonymity |
| `GET /api/v1/organizations` with that token | 200, `[('local', 'owner')]` |

Carried forward from earlier iterations (still true, not re-run this time): loopback-only
refusal from the host's non-loopback address, path-traversal probes against the SPA route,
`kill -9` orphan prevention, and the migration up/down/up.

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

*No new bugs were found in the latest iteration.* The two issues the self-review caught
(an error message asserting a cause it could not know, and a screen with no way out) were in
unshipped code from the same iteration, not pre-existing defects.

---

## Known issues

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
