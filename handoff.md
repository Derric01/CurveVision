# Handoff

> **What this file is.** The current, honest state of CurveVision, written for whoever picks
> it up next — a different session, possibly a different agent entirely. Read
> [`AGENTS.md`](./AGENTS.md) first; it is the working contract. Update this file after every
> iteration, including the ones that mostly failed.
>
> **Last updated:** 2026-09-13 (iteration 21) · branch `claude/curvevision-platform-build-n1g71n` · PRs [#1](https://github.com/Derric01/CurveVision/pull/1)–[#10](https://github.com/Derric01/CurveVision/pull/10) merged (iterations 1–16) · PR [#11](https://github.com/Derric01/CurveVision/pull/11) merged (iterations 17–20) · iteration 21 on the branch, restarted from `origin/main` at `87a25b6`

---

## Status

CurveVision is a working annotation platform in **two shapes from one codebase**: a desktop
application and a server. The manual annotation path is complete end to end — create a
project and label schema, add media, draw, review, export COCO/YOLO/Pascal VOC/native — and
is tested. The desktop shape works: a packaged single-executable server, a Tauri shell that
supervises it, and folders annotated in place without copying a byte.

**The tree is green.** `./scripts/check.sh` passes all nine steps.

Honestly incomplete, and marked as such everywhere: the mask brush, the keypoint UI, an
in-editor view of issues, resumable uploads, and signed desktop installers.

**Annotation quality is measured rather than declared, and a reviewer can now see it.** A
task holds a ground-truth job; scoring an annotation job against it produces per-label
precision/recall/F1 and a conflict list naming each mistake. The editor shows that report in
its right-hand panel, and **clicking a conflict seeks to its frame** — a conflict is a place,
not a statistic. Building the panel turned up a real gap in the model: a report recorded no
version of the job it scored, so a score taken before the annotator fixed everything looked
current. `QualityReport.annotation_version` closes that, and the panel marks a stale report
stale. Still no UI for *creating* a ground-truth job; that stays an API or CLI call.

The desktop window **signs itself in from the connection the shell injects** and opens
straight into the application.

**Video tasks are now annotatable, and no longer slow to step through.** Uploading a video
produces a task with many frames, and the editor is served each frame as an image —
previously a frame request returned the whole container, which made video unusable.
`media/video.py` is adapted from another project's MIT-licensed source under ADR 0007.

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

**Show issues in the editor.** `api.issues` / `createIssue` / `resolveIssue` have been
on the client since the first web iteration with nothing calling them. The quality panel is
the pattern to copy — both are frame-anchored lists a reviewer clicks through — and an issue
additionally carries a `position`, so it can be drawn on the canvas rather than only listed.

**Then: run the comparison as a background job.** `QUEUE_ROUTING` already has a `quality`
queue and nothing routes to it. Scoring is arithmetic over rows already in the database, so
inline is right today; a ground truth over thousands of frames changes that calculus, and the
endpoint's docstring says so.

*Superseded: "a UI for creating a ground-truth job" and "drag a keyframe along its lane",
both done.*

<details><summary>What that took, for whoever wires the next pointer interaction</summary>

Two traps, both found by reviewing the implementation rather than by a failing test:

**A drag ends with a `click` on whatever is underneath.** The lane is a click-to-seek button,
so the first version moved the keyframe *and* jumped the playhead to where it was dropped.
The obvious fix — a flag the click handler consumes — is worse than it looks: when the click
does not arrive, the flag stays set and swallows the next real seek instead. The markers are
now **siblings of the seek button rather than children**, so there is no click to suppress.

**Press versus drag is decided from the pointer positions, in `pointerup`,** not inferred in
a click handler. `endDrag` knows whether the pointer moved; a click handler does not. The
marker's `onClick` is keyboard-only, gated on `event.detail === 0`.

Also: `setPointerCapture` is not optional. The lane is 18px tall and a drag leaves it
immediately and constantly — the harness deliberately drags 30px off-lane mid-gesture.

*Correcting an earlier note here: keyframe edits do **not** go through `useAutosave`. That
path builds only `created_shapes`/`updated_shapes` and has no track surface. They go through
a mutation that flushes the shape buffer, re-reads `annotation_version`, then writes
`updated_tracks`.*

</details>

**Then, in rough order:**

* **Pre-build chunks after a video upload.** `media.build_chunks` builds rather than plans,
  and nothing enqueues it. `media.probe_task` is now enqueued from the same place and
  already decodes the whole file to count it — so the honest move is probably **one** job
  that counts and builds in a single pass, rather than two that each walk the file.
* **Surface a task whose frame count could not be corrected.** The job reports "this task
  already has annotation work"; nothing shows it to anyone.
* **Put a browser harness in CI.** Eight now exist (`screenshot.py`, `verify_local_import.py`,
  `verify_chunked_frames.py`, `verify_track_timeline.py`, `verify_scissors.py`,
  `verify_keyframe_editing.py`, `verify_quality_panel.py`) and between them they have found
  every defect the unit suites missed — including this iteration's. Nightly or pre-release;
  each needs a packaged sidecar and a Chromium. Note the sidecar embeds `web/dist`, so a
  harness run needs `npm --prefix web run build` **and** a sidecar rebuild, or it silently
  tests the previous frontend.

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

**The track timeline** — a lane per track under the frame slider showing where it is
present and where its keyframes are, with `,`/`.` stepping between keyframes and a click
seeking. Presence follows the same `outside` rules as the interpolator, asserted frame by
frame. Read-only: editing keyframes from the timeline is not built.

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

**Intelligent scissors** — an edge-snapping boundary tool. `canvas/scissors.ts` implements
live-wire (Mortensen & Barrett, SIGGRAPH 1995) directly rather than loading a ~10 MB OpenCV
build; the search is lazy, so anchoring costs 2.8 ms and a short drag single-digit
milliseconds. Bindings match `PathTool`. Output is simplified to an editable polygon.
Verified in Chromium against a disc: every vertex within 1.5 px of the rim.
[ADR 0009](./docs/adr/0009-intelligent-scissors.md).

**Editing track keyframes from the timeline** — select a track, then `K` adds a keyframe at
the current frame (carrying the interpolated position, so nothing moves) or removes the one
already there, and `O` marks where the object leaves. `keyframes.ts` is pure and separately
tested; `framesThatMoved` checks the no-movement rule frame by frame rather than asserting
it. Removing the last keyframe deletes the track rather than leaving an empty one.
`scripts/verify_keyframe_editing.py` drives it in Chromium and asserts against the API.

**Eleven dataset formats** — COCO, all five Ultralytics YOLO tasks (detection, segmentation,
OBB, pose, classification), Pascal VOC, KITTI, MOTChallenge, CVAT XML, segmentation masks and
the lossless native one. Each declares what it can represent and warns before an export drops
anything. **CVAT XML round-trips both directions**; **YOLO OBB and CVAT XML are the only two
that keep a rotated box's angle**. Round-tripped through their own readers in
`tests/unit/test_formats_robotics.py` and `test_formats_yolo_variants.py`.

**Superseded:** **Eight dataset formats** — COCO, YOLO, Pascal VOC, KITTI, MOTChallenge, CVAT XML,
segmentation masks and the lossless native one. Each declares what it can represent and
warns before an export drops anything. **CVAT XML round-trips in both directions**, which is
what stops work done elsewhere being stranded here. KITTI and MOT are the robotics-facing
pair: the driving convention, and object identity across frames. All are round-tripped
through their own readers in `tests/unit/test_formats_robotics.py`.

**Merging overlapping jobs** — export reconciles the frames two jobs share instead of
concatenating them. Two shapes are one object when label, shape type and geometry all agree
(≥ 0.75 IoU, exact rather than bounding-box), and the earlier job's copy is kept. Pairing is
an **optimal** one-to-one assignment (`services/matching.py`, the Hungarian algorithm in ~90
lines with no dependency), because greedy strands a real correspondence when two objects sit
close together and then ships the duplicate. Never merged: different labels (a disagreement
to review), shapes that enclose no area, and anything inside a single job. Track identity is
unified across the seam by union-find, and track ids are allocated per task. Adapted from the
upstream `ObjectManager` design; credited in
[THIRD_PARTY_NOTICES](./docs/THIRD_PARTY_NOTICES.md#adapted-source).

**Quality reports against ground truth** — `POST /tasks/{id}/ground-truth` creates the job
holding a task's answer key (one per task, any contiguous frame range, defaulting to the
whole task); `POST /jobs/{id}/quality` scores an annotation job against it and stores a
`QualityReport`. Matching is greedy on descending IoU, which is COCO's rule, and geometry is
exact — polygons clipped by Sutherland–Hodgman and measured by the shoelace formula, so two
triangles sharing a bounding box score near zero rather than one. Conflicts are classified
into *missing*, *extra*, *wrong label* and *poor overlap*, because "you missed it", "you
invented it", "you called it the wrong thing" and "you were close" need different fixes.
Three ways the number could have been dishonest are each refused by a test: scoring frames
the ground truth never covered, scoring a job against itself, and letting the annotator read
the answer key. `services/comparison.py` is pure — no ORM import — and follows the
comparison strategy of another project's MIT-licensed source, credited in
[THIRD_PARTY_NOTICES](./docs/THIRD_PARTY_NOTICES.md#adapted-source). Reachable from the SDK
(`create_ground_truth_job`, `score_job`) and the CLI (`task ground-truth`, `job score`).

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

**Video frames** — `media/video.py`, **adapted from another project's MIT-licensed source**
at the commit named under [ADR 0007](./docs/adr/0007-reusing-third-party-code.md), carrying
its upstream copyright header and recorded in
[THIRD_PARTY_NOTICES](./docs/THIRD_PARTY_NOTICES.md#adapted-source) with the licence text in
`licenses/MIT-video-decoding.txt`. Frames are addressed in decode order rather than by seeking,
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
| Track keyframe **editing** | Complete: `K` adds or removes a keyframe, `O` marks a departure, and a marker can be dragged along its lane. Verified in a browser, not only in unit tests. |
| Pre-building chunks after upload | `media.build_chunks` builds rather than plans, and nothing enqueues it. `media.probe_task` already walks the whole file to count frames, so one job that counts *and* builds probably beats two that each decode it. |
| Surfacing an uncorrected frame count | When a task already carries annotations, `media.probe_task` declines the correction and says so in its result. Nothing shows that to a user. |
| Webhooks | Delivery works and is signed; retry/backoff is not wired to the queue. |
| Mask brush, keypoint UI | Storage, export and the model exist on both sides; neither drawing tool does. |
| Quality reports | Complete end to end and driven in a browser: the task page creates the answer key and shows each job's latest F1, the editor shows the report and seeks to a conflict on click, and a stale report is marked stale. What is left: the comparison runs inline rather than on the `quality` queue, which a very large ground truth would change. |

*This table went stale once — it still listed the open-folder flow and chunked delivery as
unbuilt several iterations after both shipped, because the narrative sections above were
being updated and this one was not. Check it against* Completed *before trusting it.*

---

## Remaining high-priority work

1. **Issues in the editor.** `api.issues` / `createIssue` / `resolveIssue` have had no UI
   since the first web iteration. The quality panel is the pattern to copy.
2. **Pre-build chunks after a video upload.** `media.build_chunks` builds rather than plans,
   and nothing enqueues it. Careful: the desktop queue is inline, and `media.probe_task`
   already decodes the whole file — one job that counts and builds probably beats two.
3. **A browser harness in CI.** Eight exist and between them they have found every defect
   the unit suites missed, this iteration's included. Note the sidecar embeds `web/dist`, so
   a run needs `npm --prefix web run build` **and** a sidecar rebuild, or it silently tests
   the previous frontend.
4. **Signed installers in CI** — one runner per platform; PyInstaller does not cross-compile.
5. **Webhook retry/backoff** wired to the job queue.
6. **`choose_files` is still unused.** The shell can open a native *file* picker as well as a
   folder one, and `/tasks/{id}/local-import` accepts a file path. Connecting it is small, and
   deliberately left until someone wants it — the folder case is the one that matters.

---

## Last iteration

**The quality feature is complete end to end, and has been driven that way in a browser.**
Creating a ground-truth job was the last part only the API and the CLI could reach — and it
is the decision that gives every score on the task its meaning. The task page now creates it,
with both frame bounds optional.

**The claim that carries the most weight: a blank frame field means the whole task, not frame
0.** Both bounds default to the full range server-side. A form reading an empty field as `0`
would produce a **one-frame answer key** while looking like it had done what was asked, and
every score afterwards would be a real-looking number computed from one frame.
`planRange` returns `null` for a blank bound and the request omits the key entirely;
`verify_ground_truth_setup.py` submits the form untouched and asserts the job covers every
frame. Two unit tests pin it from both ends — blank is not 0, and a typed `0` is still 0.

**The answer key is no longer listed as ordinary work.** It was "Job #5" among the others, so
the one job nobody should be handed for ordinary annotation looked exactly like the ones they
should. It now has its own panel, marked, with a warning when nothing has been annotated in
it — scoring against an empty ground truth marks every object the annotator drew as invented,
which produces a precision of 0 that says nothing about their work.

The job list also shows each job's latest F1 beside its state, flagged **stale** when the job
has changed since it was scored. The two answer different questions — "has it been reviewed"
and "was it right" — and a job can be accepted with a poor score.

`scripts/verify_ground_truth_setup.py` is the eighth harness and the first to drive **both
halves as one workflow**: declare the answer key on the task page, annotate it, then score a
job from the editor and get P=1.00 R=0.50 over six frames — a number that follows from the
ground truth just declared, so a panel showing a hardcoded score would fail.

---

## Iteration 20

**A keyframe can be dragged along its lane.** `moveKeyframe` had been written and tested for
two iterations with nothing calling it; grabbing a marker and dropping it on another frame
now moves it, keeping its geometry. A drop onto an occupied frame is refused rather than
merged — the API rejects two shapes on one frame, and merging would silently discard one of
the annotator's positions.

The arithmetic that turns a pointer x into a frame is now `frameAtPosition` in `timeline.ts`,
the inverse of the existing `framePosition`, tested against it across four frame ranges.
Click-to-seek and drag-to-move both go through it, so they cannot drift a frame apart — a
marker that lands next to where you dropped it is the kind of thing an annotator stops
trusting and works around.

**Two bugs were caught by reviewing the implementation, before any verification ran.** Both
are recorded under [Next best action](#next-best-action) because they generalise to the next
pointer interaction somebody wires: a drag ends with a `click` on whatever is underneath, and
the flag that suppresses it gets stuck set whenever the click does not arrive. The markers
are siblings of the seek button now, so there is nothing to suppress; press-versus-drag is
decided in `pointerup` from the pointer positions themselves.

`verify_keyframe_editing.py` was extended rather than joined by an eighth harness — same
feature, same seeded clip. It drags the marker 30px off the 18px lane mid-gesture, which is
what a drag without `setPointerCapture` cannot survive.

---

## Iteration 19

**A reviewer can read a quality report in the editor — and building the panel exposed two
defects in the data behind it.**

The comparison engine has been correct and tested since it landed. The only way to see a
report was `curvevision job score`, which means the reviewer who needs it was the one person
not looking at a terminal. It is now a panel in the editor's right sidebar: precision,
recall and F1; a per-label breakdown ordered **worst first**, because a schema of twelve
labels sorted alphabetically buries the thing you opened the report to find; and the
conflicts as a list where **clicking one seeks to its frame**.

That last part is the design. **A conflict is a place, not a statistic.** The comparison
already addresses every conflict by frame and by shape, so a reviewer reads "missed a car on
frame 214", clicks, and is looking at frame 214. A report that only totals things tells you
a job is bad without telling you where.

Pure logic lives in `features/editor/quality.ts` and is tested without a DOM; `QualityPanel`
renders it. Truncation is stated rather than silent — a capped list says "200 of 3,412"
instead of implying the job has 200 problems — and a label the project schema no longer has
still gets a row, because dropping it would quietly subtract its conflicts from what the
reviewer sees.

**Defect 1, found by reading the model: a report did not record what it measured.** A
`QualityReport` stored a score and a timestamp and nothing about the annotations it scored.
So a report read as current however much work had landed since — and it failed in the worst
direction: the annotator fixes everything, the old F1 of 0.4 is still on screen, and a
reviewer rejects the job on a measurement of work that no longer exists.
`QualityReport.annotation_version` (migration `4b1c7de9a20f`) records the job's version at
the moment of comparison, and the panel marks a report stale when the job has moved on.
Nullable, because reports written before the column genuinely cannot say — and presenting
*that* as "current" is the failure the column exists to prevent.

**Defect 2, found by the browser harness, not the unit suite: a conflict's two label fields
swapped meaning depending on the kind.** `label_id` carried the ground truth's label on
`missing` and `poor_overlap` and the annotator's on `extra` and `wrong_label`; nothing said
so, and `expected_label_id` was populated on `wrong_label` alone. The screenshot showed
`Missed: unlabelled` where the ground truth plainly said *car* — the unit test had happened
to construct its fixture the way the client read it, so both agreed and both were wrong.

Worse than the miss: because candidates are paired by **geometry, not by label**, a
`poor_overlap` conflict reported `reference.label_id` while `shape_id` named the annotator's
shape. A reviewer shown "Loose geometry: car" clicked through to a box labelled *person*,
having been told something false about their own work, authoritatively.

Each field now means one thing on every kind: `label_id` is the annotated shape's label
(null on a miss, where nothing was annotated), `expected_label_id` is the ground truth's
(null on an extra, where the ground truth has nothing). Four server tests pin it,
`docs/API.md` states it, and the panel shows the second label only where it differs — "car →
car" is noise.

**`scripts/verify_quality_panel.py`** is the seventh browser harness and the seventh to find
something the unit suites could not. It seeds a job wrong in four different ways so all four
conflict kinds are exercised, then checks the reviewer's actual path: the panel offers to
score an unscored job, the score renders, **a conflict row moves the editor** (frame 1 → 5,
the seeded target), and annotating afterwards marks the report stale.

One trap worth recording: the packaged sidecar **embeds `web/dist`**, so a harness run needs
`npm --prefix web run build` *and* a sidecar rebuild. Without both it silently exercises the
previous frontend — which is what the first run of this harness did, timing out on a panel
that existed in the source and not in the binary.

---

## Iteration 18

**Keyframes can be edited from the timeline — and the browser check found a second bug.**

The timeline showed a track's keyframes; now `K` adds or removes one and `O` marks where the
object leaves, on whichever track is selected. The pure logic is `keyframes.ts`; the editor
is the state machine around it.

**The rule everything rests on: adding a keyframe must move the object on no other frame.**
A track interpolates between stored positions, so a keyframe carrying the wrong geometry
shifts every frame between it and its neighbours — the annotator sees a box they did not
draw, on frames they were not looking at. `toggleKeyframe` inserts the *interpolated*
position, which is by construction what was already on screen, and `framesThatMoved` checks
that frame by frame rather than trusting the argument. Replacing it with the obvious wrong
implementation (copy the previous keyframe's points) fails four tests.

**`markDeparture` turned out to need more than it looks.** `interpolateTrack` deliberately
*holds* the previous position when the next keyframe is a departure, rather than animating
towards coordinates that are usually a stale copy. So dropping a departure at frame 6 onto a
track with keyframes at 0 and 10 froze frames 1–5 at the frame-0 position — five frames
nobody touched, silently stopped moving. A test caught it. The fix pins the already-displayed
shape at frame 5 first, which changes nothing by itself and leaves every earlier frame exactly
where it was.

**`scripts/verify_keyframe_editing.py` then found a real defect the unit tests could not.**
Scrubbing frames quickly made the editor throw: `AnnotationCanvas` called `image.decode()`,
caught the rejection with `.catch(() => undefined)` — and then **carried on into the success
path anyway**, handing a *broken* `HTMLImageElement` to the renderer, where `drawImage`
threw. Now a failed decode clears the media instead. Clearing rather than keeping the previous
frame is deliberate: a frame number identifies one picture, so leaving the old image under a
new frame number would let somebody draw a box on the wrong one.

**A claim in this file was wrong and is corrected.** It said keyframe editing would be "an
annotation write through the existing autosave path — `useAutosave` already batches and
handles the 409". `useAutosave` builds only `created_shapes`/`updated_shapes`; it has no track
surface at all. Track edits go through a mutation that flushes the shape buffer first, re-reads
`annotation_version`, then writes `updated_tracks`.

### Iteration 17

**The other three Ultralytics YOLO tasks — and a shipped format that was quietly wrong.**

Orienting for "we have YOLO export too, I hope all the types industry wants" turned up
something better than a feature request: **plain YOLO was silently discarding rotation**.

A rotated rectangle is stored as its *unrotated* box plus an angle. The exporter took
`points[:4]` and normalised them, so a 100×20 bar turned 90° — which occupies 20×100 on
screen — was written as **100×20**. A box that does not contain the object it names. Worse,
`capabilities.shape_types` did *not* list `ROTATED_RECTANGLE`, so the pre-export warning told
the user rotated shapes had been **dropped** while they were being written wrong. Silently
bad data plus a warning saying the opposite.

Fixed by taking the axis-aligned bounds of the *rotated corners*, which is the honest
detection answer, and by declaring the shape type so the capability report stops lying.
`rotated_corners()` is now a shared helper in `formats/base.py`, since three formats need it.

**Three new formats**, each a task Ultralytics trains and somebody's industry depends on:

* **YOLO OBB** — four corners per object, so a rotated box **keeps its angle**. The only
  format here besides CVAT XML that does. Aerial imagery, industrial inspection, document
  layout: wrap any of those in a straight box and most of the box is background. Imports too,
  and a quadrilateral that is *not* rectangular comes back as a **polygon** rather than being
  squared off into a box it does not fit.
* **YOLO Pose** — skeletons as keypoints with 0/1/2 visibility. A missing joint is **padded**
  rather than omitted, because a short line shifts every later value into the wrong joint.
  `data.yaml` carries `kpt_shape`, which Ultralytics needs to size the pose head.
* **YOLO Classification** — the odd one out: no label files, because the **directory tree is
  the annotation**. That is also its limit, so a frame with two tags is reported rather than
  filed under whichever came first.

**An existing test had to be widened, carefully.** `test_every_format_declares_its_
capabilities_honestly` asserted every format declares at least one shape type — true until a
whole-image classification format, which legitimately carries none. Widened to "shapes **or**
tags" rather than deleted, with a companion test proving the guard can still fail, because a
guard that cannot fail is worse than no guard.

### Iteration 16

**Four new dataset formats, and a red CI check on main fixed.**

The ask was CVAT parity on export, plus something for robotics. The measured gap: their 33
exporters against our 4. Most of the remainder are single-dataset conventions (LFW,
VGGFace2, Market-1501, ICDAR, WiderFace) that matter enormously to whoever needs them and
not at all to anyone else. Four were worth having now, and they are the four that cover
*shapes of annotation* the existing formats could not carry:

* **KITTI** — what robotics and driving work reaches for first. Boxes with truncation
  computed from the frame edge and occlusion carried through. **The 3D columns are written
  as the devkit's "unknown" values rather than invented**: zeros and a rotation of −10,
  outside the valid range, so a reader can tell. Plausible numbers there would be fiction
  somebody trains on.
* **MOTChallenge** — the first format here that carries **object identity across frames**.
  A detection format says a car is in frame 40; a tracking format says it is the *same* car
  as frame 39. Frames are 1-based where we are 0-based, and untracked shapes get ids above
  1,000,000 so a consumer can tell they were synthesised.
* **CVAT XML** — the migration bridge, both directions. The most expressive format here:
  boxes, polygons, polylines, points, ellipses, masks, tags, attributes **and** tracks with
  keyframes. A tool that can only be entered is a trap; this is what makes leaving possible.
* **Segmentation masks** — indexed PNG, one class per pixel, VOC palette. Overlaps resolved
  by z-order, so the shape in front wins. **Export only, deliberately**: a mask does not
  record the polygons it was painted from, and tracing contours back would replace someone's
  work with a machine's approximation of it.

**The Docker CI check had been failing on main**, and it was a real bug rather than the
environment. `npm run build` runs `tsc -b --noEmit`, which typechecks `vite.config.ts`. That
file carried a `test:` block — vitest's, not vite's — and Vite's own `defineConfig` does not
declare that key. It *appeared* to work because test files import vitest and pull in its type
augmentation, and `.dockerignore` excludes every `__tests__` directory. So the config
typechecked on every developer machine and failed in every container.

Reproduced by copying the web tree without its test directories and running the same command,
then fixed by importing `defineConfig` from `vitest/config`, which declares the key on its own
terms. `src/__tests__/buildConfig.test.ts` guards it, because the import looks redundant and
is exactly the kind of thing a tidy-up would "fix" straight back into a broken build.

### Iteration 15

**Intelligent scissors: click once, and the boundary snaps to the edge under your cursor.**
The most-requested tool in this space, and the one that makes tracing a curved object — a
leaf, a sign, a coat — take three clicks instead of forty.

**The reference implementation is a 196-line wrapper around a 9,991,739-byte OpenCV.js
asset.** All of its algorithm lives in OpenCV's C++, so "reuse their code" would have bought
the state machine and none of the substance. The decision, recorded in
[ADR 0009](./docs/adr/0009-intelligent-scissors.md), was to implement the published live-wire
algorithm (Mortensen & Barrett, SIGGRAPH 1995) directly: ~300 lines, no dependency, and
`web/src/canvas/` keeps its rule of importing nothing.

* **`canvas/scissors.ts`** — Sobel gradients, Laplacian zero-crossings and edge orientation
  feed a three-term cost; Dijkstra from the anchor gives a tree, and the path to any cursor
  position is a walk back up it. Dropping the direction term is tempting and is the
  difference between a wire that hugs a curve and one that shortcuts across it.
* **`ScissorsTool`** shares `PathTool`'s bindings exactly — click commits, Enter finishes,
  Backspace unwinds one anchor, Escape abandons — because switching tools should not mean
  relearning your hands.
* **Ramer–Douglas–Peucker on the way out.** The wire arrives one vertex per pixel; a
  400-pixel boundary as 400 vertices is unusable as a polygon. The disc in the browser check
  comes back as 36.

**Two measured departures from a literal reading of the paper:**

* **Dial's algorithm, not a binary heap.** Link costs are bounded small integers.
* **The search is lazy** — this one mattered most. Building the whole tree per click measured
  **200 ms** on a 1024×576 grid, a visible hitch, and nearly all of it wasted: an annotator's
  next click is tens of pixels along a boundary, not across the image. Expanding only as far
  as the cursor asks took `setAnchor` to **2.8 ms**, and a 40 px drag to **5.9 ms** settling
  1,149 pixels instead of 590,000. Correctness survives because Dijkstra settles in
  non-decreasing cost order; `resuming the search cannot change an answer it already gave`
  asserts that against cold recomputation rather than leaving it as an argument.

An `acos` lookup table took another 70 ms off before the laziness landed — the direction term
was making ~4.7 million `Math.acos` calls per click.

**A bug found by a test rather than argued about:** an exactly-zero Laplacian is *not* an
edge. Flat regions are full of exact zeros, and marking them made empty space cheap to cross,
so the wire wandered instead of tracking boundaries. Only a sign change is a crossing.

**Verified in a real browser**, which is where this project's defects have always actually
lived. `scripts/verify_scissors.py` seeds a disc, drives the editor in Chromium with real
pointer events, and measures the polygon that comes back: 36 vertices, every one within
**1.5 px** of the rim (radius 149.6–151.5 against a true 150), and the nearest vertex to the
chord's midpoint 45 px away — so it followed the arc rather than cutting across. The
screenshot was looked at, not just written.

*The harness's own first run failed, and it was the harness: it never clicked a label, and
every drawing tool declines silently without one. Worth knowing before debugging the next
tool.*

### Iteration 14

**Overlapping jobs are reconciled on export instead of concatenated, and two silent
data-corruption bugs went with it.** This came from a direct question — *use more of the
upstream project's code, many people contributed to it* — and the right answer turned out
not to be "port more lines" but "go find where their accumulated experience covers a hole we
have". There was one, and it was serious.

**The bug.** A task with `overlap > 0` hands the same frames to two annotators on purpose:
it is how a track stays continuous across a job seam. Export walked the jobs and *appended*
every job's shapes, so every object in the seam shipped **twice**. Nothing complained — the
archive is well-formed, the frame count is right, and a model trains on doubled boxes with
no error anywhere. `overlap` is a first-class, API-settable task field and had **zero test
coverage**.

**The second bug, found while fixing the first.** `track_id` fell back to a job-local
`enumerate` index, so job 1's first car and job 2's first pedestrian were both `track_id: 0`.
Any consumer grouping by track id welds two unrelated objects into one. This needed no
overlap at all — a plain segmented task was enough — and it is arguably the worse of the
two, because a duplicate is visible and a weld is not. Track ids are now allocated per task,
with an explicit `object_id` still honoured and generated ids stepping around those values.

* **`services/merge.py`** follows the upstream `ObjectManager`/`ShapeManager`/`TagManager`
  design: only objects inside the overlap are candidates, one cost matrix per frame, an
  optimal one-to-one assignment, a similarity floor, and "the earlier job wins". Their
  threshold of 0.25 is a tuned constant and was inherited rather than re-guessed.
* **`services/matching.py`** is the Hungarian algorithm, ~90 lines, no dependency. The
  upstream uses `scipy.optimize.linear_sum_assignment`; SciPy is tens of megabytes inside a
  single-executable desktop build, for one function on matrices smaller than 10×10.
* **Their similarity function was not taken** — it returns 0 for points, polylines, ellipses
  and 2D cuboids with a `FIXME` saying so. `comparison.py`'s exact IoU (shoelace +
  Sutherland–Hodgman, built last iteration for quality scoring) is strictly better and now
  serves both engines.
* **Track identity is unified across the seam** by a union-find over the ids discovered to
  be one object. Without it the merge would remove the duplicate and still leave the car
  appearing to vanish and be replaced — which is the exact discontinuity the overlap was
  bought to prevent, so this is the point of the feature rather than a nicety.

**A measurement I got wrong before, corrected.** An earlier iteration reported that only
**2.4%** of the upstream Python is "free of Django/DRF/Datumaro" and treated that as the
ceiling on reuse. That measured the wrong thing. Counting *import lines* rather than
*importability*: `dataset_manager/annotation.py` is 1,237 lines with **4** framework
imports; `media_extractors.py` is 1,649 with 4; `annotation_matching.py` is 1,287 with 3.
The coupling is shallow and sits at the edges. The honest figure is that the framework-bound
*surface* is large and the framework-bound *domain logic* is small — so the reusable
fraction is far higher than 2.4%, and the real constraint is finding holes worth filling,
not licence or portability.

**Still available and shallow-coupled**, in rough order of value:
`consensus/intersect_merge.py` (551 lines, 3 framework imports) — merging N annotators'
work on the same frames by agreement, the natural extension of what landed here;
`dataset_manager/annotation.py`'s `TrackManager.get_interpolated_shapes` (~420 lines) for
mask and skeleton interpolation, which ours holds rather than interpolates. The second is
**deliberately not taken yet**: our interpolation is implemented identically in TypeScript
and Python against shared test vectors, and that parity is load-bearing for client-side
scrubbing. Swapping one half for a different algorithm would break it, and the shapes it
would improve (masks, skeletons) have no drawing tool yet.

### Iteration 13

**Made the quality report real.** `QualityReport` declared `iou_threshold`, `precision`,
`recall`, `f1` and `details`, and **nothing computed any of it** — no service, no handler,
no endpoint. A table with metric columns that nothing writes to is the most flattering kind
of unfinished: it reads as a shipped feature in every schema diagram.

* **`services/comparison.py`** is the arithmetic, and it imports no ORM — `ComparableShape`
  is a `Protocol`, so the module is pure and testable without a database. Greedy matching on
  descending IoU (COCO's rule), exact polygon geometry (Sutherland–Hodgman clipping, shoelace
  area), and four named conflict kinds. It follows the comparison strategy of another
  project's MIT-licensed source; credited in THIRD_PARTY_NOTICES.
* **`services/quality.py`** decides *what* to measure, which is where the judgement is:
  only frames the ground truth covers, and tracks flattened to their interpolated position
  on every frame so a track and a shape describing the same object score as a match.
* **`POST /tasks/{id}/ground-truth`** was the missing link. Without it the feature was
  unreachable — the tests had to insert a `Job` row directly, which is exactly the signal
  that a feature is not finished. One per task, refusing a second: `find_ground_truth_job`
  takes the lowest-indexed one, so two would silently decide every score on the task.
* **The answer key is now protected.** Any org member at viewer rank could read any job's
  annotations, ground-truth jobs included — so an annotator being scored could read the
  answers. `ResourceContext` gained `job_kind`, and reading a ground-truth job's annotations
  now takes reviewer rank or assignment to that job. A score an annotator could have copied
  measures nothing, which is worse than no score because it looks like evidence.
* **SDK and CLI** reach all of it: `create_ground_truth_job`, `score_job`,
  `quality_report`, `task_quality`; `curvevision task ground-truth` and
  `curvevision job score --iou`. Both CLI commands were driven against a live server.

**A flattering-failure bug, found by looking at a real report rather than at the tests.**
Dumping an actual response to check the API doc's example showed `missing: 2` against only
one `missing` conflict — and the arithmetic behind it counted a below-threshold box as a
false negative but **not** as a false positive. So an annotator who drew every box too
loosely scored precision 1.0: "everything you drew was right", when by the threshold's own
definition none of it was. It was also inconsistent with the wrong-label branch four lines
up, which correctly counts one mistake on both sides. Fixed, with two tests written to fail
first; the conflict is still *reported* once, because a reviewer wants one row saying "you
were close", not two.

**Parked and deleted rather than committed:** a honeypot-frame selector adapted from the
same upstream source. Our `Job` model has only contiguous `start_frame`/`stop_frame` with no
per-job frame list, so sprinkling ground-truth frames through ordinary jobs has no caller
without a schema change. It was written, then deleted — committing it would have been dead
code wearing the appearance of a feature, which is the thing this iteration set out to
remove.

### Iteration 12

**A doc-drift audit, prompted by a good question: had the fast pace cost quality?** It had,
in four places. None of them was code — the tree stayed green throughout — and all four were
documentation claiming something that had stopped being true.

* **`ARCHITECTURE.md` §6 still said chunked delivery was unbuilt** and that "the client
  currently fetches one frame per request", two iterations after it shipped. §9's performance
  table said the same. Both corrected, with the measured numbers.
* **The *In progress* table here was entirely stale** — it listed the open-folder flow and
  chunked media delivery as unbuilt, several iterations after both shipped. The narrative
  sections above it were being updated every iteration and this table was not. Rewritten,
  with a note on the table saying it has gone stale before.
* ***Remaining high-priority work* had a duplicated item 6** and a stale item 1, both from my
  own edits colliding.
* **Deleting `ARCHITECTURE_COMPARISON.md` took an honest limitations list with it.** Most of
  that document was comparison and no loss, but its §6 listed what the platform *does not do*
  — and one item, that only S3-compatible object storage is supported and Azure Blob and GCS
  are not, was recorded nowhere else afterwards. Restored to `ROADMAP.md` as **What it does
  not do**, stated as our own limitations, which needs no comparison to anyone.

Checked and found *not* lost: the job-as-unit-of-work rationale, the attributes-as-JSON
rationale ("a side table would triple row counts for no query benefit"), the arc-length
resampling reasoning, and the `Segment`-collapse rationale — all still in `ARCHITECTURE.md`
or the plan.

The lesson worth carrying: **narrative prose gets updated because you are writing it; tables
and status markers elsewhere in the same file do not.** After finishing a feature, grep for
its name across the docs rather than editing the section you happen to be in.

### Iteration 11

**The editor shows where every track lives.** Until now a track was invisible unless you
happened to scrub onto a frame it occupied: the canvas draws this frame and nothing told you
an object exists two hundred frames away, or that the one in front of you vanishes in six.

The subtlety this is really about: **a track's presence is not one span.** A keyframe marked
`outside` is the object *leaving* — behind a wall, out of shot — and it can come back. A bar
drawn from first keyframe to last claims the object is on screen throughout its absences,
and an annotator who trusts it goes looking for something that is not there.

* **`timeline.ts`** derives presence from the keyframes in one pass: `trackSegments`,
  `trackRows`, `framePosition`, `adjacentKeyframe`. Pure, and separate from the component
  because the rules are the part worth testing.
* **`TrackTimeline.tsx`** draws a lane per track under the frame slider — presence as bars,
  keyframes as ticks, departures drawn hollow because they mark where the object *goes*, not
  where it is. Clicking a lane seeks to that frame. Hidden entirely when a job has no tracks,
  so an image task loses no canvas height.
* **`,` and `.` step between keyframes** rather than frames, which is how you move through a
  track annotated every thirtieth frame without pressing an arrow thirty times.

The load-bearing test is `agrees with the interpolator on every frame`: for eight fixtures
covering every lifetime shape that occurs — leaves and returns, leaves twice, outside from
the very first keyframe, a departure adjacent to a return — it asserts frame by frame that
"the timeline says present" equals "`interpolateTrack` returns a shape". Two implementations
of presence *will* drift, and the drift is invisible in the worst way: the bar says the
object is there and the canvas draws nothing.

Replacing `trackSegments` with the naive "first keyframe to last" fails 6 of the 19 tests,
that property first.

`scripts/verify_track_timeline.py` seeds a 48-frame clip with three tracks — one present
throughout, one that leaves at frame 8 and returns at 26, one starting late — and checks in
Chromium that the lanes render, that the returning track is drawn as **two** runs rather than
one, and that `,`/`.` move between keyframes.

### Iteration 10

**Corrected a benchmark the README invites readers to check.** The reconcile pass that opens
every iteration found it: the README said *"picking from 100,000 shapes takes ~1 µs; a linear
scan of 10,000 takes ~550 µs"* and called it a ~500× gap. Two problems, and the second is the
one that mattered.

* **The number had drifted.** `npm run bench` now reports the scan at ~410–500 µs, not 550.
* **The comparison was not like for like.** Picking from *100,000* against a scan of
  *10,000* is two different datasets. `scan` was only benched at 1k and 10k, so the honest
  comparison could not be made at all.

`scan` now runs at every scale the index is measured at, and the figures are:

| Shapes | Pick, with the R-tree | Pick, by linear scan | Gap |
| --- | --- | --- | --- |
| 10,000 | ~0.3 µs | ~450 µs | ~1,500× |
| 100,000 | ~0.7 µs | ~5,000 µs | ~7,000× |

Three runs, one machine; the scan at 100k varies between 4.6 ms and 6.4 ms, so the
multiplier is rounded down rather than up. Corrected in the README, ADR 0003,
`ARCHITECTURE.md`, ADR 0007 and here.

The honest number is **larger** than the one it replaces, which is worth saying plainly: the
old figure understated the result while being methodologically wrong. ADR 0003 carries a
dated note recording the correction rather than quietly restating it — the decision it
records is unchanged.

### Iteration 9

**Removed the upstream project's name from everywhere it was not legally required.** It
appeared roughly 100 times across 20 files; it now appears four times across three, and
every one of those is the attribution MIT obliges.

What went: `docs/ARCHITECTURE_COMPARISON.md` (deleted outright), the named comparisons in
ADRs 0001–0005, the §8.0 reuse table in `ARCHITECTURE.md`, the README's acknowledgement and
its "use the mature tool instead" pointer, the discretionary row in THIRD_PARTY_NOTICES, and
the prose in the plan, the roadmap, AGENTS, `docker-compose.yml` and two source comments.
ADR 0007 was rewritten from a project-specific reuse policy into a general one —
`0007-reusing-third-party-code.md` — which is more useful anyway, since it now governs any
future adaptation rather than one project's.

What stayed, and why it had to:

| Where | What |
| --- | --- |
| `server/curvevision/media/video.py`, lines 1–5 | The upstream copyright line and SPDX identifier |
| `docs/THIRD_PARTY_NOTICES.md`, the Adapted source row | Names the file, the upstream path, the commit and the copyright holders |
| `licenses/MIT-video-decoding.txt` | The full licence text (renamed from `MIT-cvat.txt`) |

The README keeps **one short named acknowledgement** — the upstream project, the one file,
and a link to the notices. A repository that names a project in its licence file while
refusing to name it in prose reads as evasive, and the mention costs a sentence.

**MIT's single condition is that the copyright notice travels with the code.** Retaining it
*is* the licence; removing it while keeping the code is the one thing the permission does
not extend to. ADR 0007 now says so explicitly, including the only clean route to removing
the attribution entirely: re-derive the file independently and delete the adapted one in the
same change. That was offered and declined — the code stays, so the notice stays.

Where a passage's value depended on naming the project — "their production compose file runs
eighteen services" — it was **deleted rather than anonymised**, because "a mature platform
runs eighteen services" is an unverifiable claim and this repository does not make those.

Verified with a link checker over all 127 relative links in the docs, since deleting a
document and renaming an ADR is exactly how a docs tree quietly rots.

### Iteration 8

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
* **Two licensing claims were false.** Two documents still asserted that no third-party
  source was present in this repository while the notices file listed `media/video.py` as
  adapted, three sections earlier. Iteration 3 corrected that sentence in the README and
  missed its two copies. Both now point at the adaptation table as the authoritative list.

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
desktop application was Planned with no working build, and that no adapted source code was
present. Both corrected; the second is a licensing claim and was the more urgent.

## Verification performed

```
./scripts/check.sh                    all 9 steps green
  ruff · ruff format · mypy · pytest server (394) · pytest sdk (13)
  notices (52 deps) · eslint · tsc · vitest (293)
```

Iteration 21 added 21 web tests (293, up from 272) and no server tests — the ground-truth
endpoint was already covered by `tests/api/test_quality.py`; what was missing was a caller.

```
ok   a task with no ground truth offers to create one
ok   a ground-truth job now exists on the task
     it covers frames 0-5 of 0-5
ok   leaving both frame fields blank means the whole task, not frame 0
ok   the form is replaced by a description once one exists
ok   the ground-truth job is marked as the answer key
ok   an empty answer key says so, rather than looking ready to score
ok   the editor offers to score the job against the new answer key
     score: P=1.00 R=0.50 F1=0.67 over 6 frames
ok   the score reflects the ground truth just declared (one of two objects found)
ok   all 6 frames were compared, because the answer key covers them
```

Iteration 20 added 7 web tests (272, up from 265) and no server tests — the drag is entirely
client-side, and `moveKeyframe` was already covered. The browser harness is what proves it:

```
ok   the keyframe at frame 10 is a grabbable marker
     keyframes after dragging 10 -> 5: [0, 5, 14, 15, 20]
ok   dragging moved the keyframe from 10 to 5
     its x is 110.0 (the dragged keyframe's own x was 110)
ok   the keyframe kept its geometry across the move
     frame readout: '16 / 24' -> '16 / 24'
ok   releasing a dragged keyframe does not also seek the editor
ok   dragging raises nothing
```

The frame readout is the discriminating one: it is the assertion that fails if the lane's
click-to-seek fires after a drop.

Iteration 19 added 5 server tests (394, up from 389) and 30 web tests (265, up from 235).
Both defects were confirmed to fail when reverted rather than assumed:

* Removing `annotation_version=job.annotation_version` from `build_report` fails
  `test_a_report_records_the_version_of_the_job_it_scored` with `assert None == 1`.
* The conflict-label fix is pinned by four tests in `TestConflictLabelsMeanOneThing`,
  including the one that would have caught it: a loose box whose label differs from the
  ground truth's must report *its own* label, not the reference's.

Migration `4b1c7de9a20f` was applied and rolled back against a real SQLite database rather
than only generated — `quality_reports.annotation_version` present after `upgrade head`,
gone after `downgrade -1`. The test suite builds its schema with `create_all`, so a
migration is never exercised there.

`scripts/verify_quality_panel.py` drives the whole path in Chromium against the packaged
server:

```
ok   an unscored job offers to check itself against ground truth
ok   and says plainly that it has not been scored
     server-side score: P=0.25 R=0.25 F1=0.25;
     conflicts ['extra', 'missing', 'poor_overlap', 'wrong_label']
ok   all four conflict kinds are exercised
ok   the panel shows the score
ok   conflicts are listed as rows a reviewer can act on
     frame readout: '1 / 6' -> '5 / 6'
ok   clicking a conflict seeks the editor to the frame it is about
ok   a report is marked stale once the job is annotated further
```

Iteration 18 added 28 web tests (235, up from 207). The no-movement rule is checked by
`framesThatMoved` across every frame in the range, and replacing `toggleKeyframe` with the
obvious wrong implementation — copy the previous keyframe's points — fails four of them.

`scripts/verify_keyframe_editing.py` drives it in Chromium against the packaged server and
asserts against the **API**, not the DOM: the question is whether the annotation is now what
the annotator asked for. It reported the new keyframe's x as exactly **110.0**, the
interpolated position, and confirmed frame 14 was pinned ahead of the departure at 15. It also
found the broken-image bug above on its first run — the fifth harness, and the fifth time one
has caught something the unit suites could not.

Iteration 17 added 28 server tests (389, up from 361). The rotation fix is pinned by
`test_a_rotated_box_exports_its_real_extent`, confirmed to fail when the fix is reverted, and
by `test_the_capabilities_no_longer_claim_it_is_dropped` for the second half of the bug. The
Docker check fixed last iteration was **confirmed red→green on main**: run 25 (the PR #9
merge) failed, run 27 (the PR #10 merge) succeeded.

Iteration 16 added 35 server tests (361, up from 326) and 2 web tests (207). Every new
format is round-tripped through its own reader: an exporter that writes something its own
importer cannot read is the failure that file exists to catch, and it is invisible from
reading either half alone. Two bugs the tests caught while writing them — `ShapeRecord` is a
slots dataclass with no `__dict__`, and my first fixtures passed `str` where `ImportSource`
returns `bytes` by contract.

The Docker failure was **reproduced before it was fixed**: the web tree copied without its
`__tests__` directories, then `npx tsc -b --noEmit`, which failed with the same
`'test' does not exist in type 'UserConfigExport'` CI reported. The same command passes after
the fix.

Iteration 15 added 35 web tests (205, up from 170): 22 on the live-wire algorithm and 13 on
the tool. The load-bearing ones use images whose correct answer is known and is visibly *not*
a straight line — a bulging band and a circle — so an implementation that ignored the pixels
would fail. Replacing the cost function with a constant fails four of them.

`scripts/verify_scissors.py` then drove it in Chromium against the packaged server: 36
vertices, every one within 1.5 px of a disc's rim, and the nearest vertex to the chord's
midpoint 45 px away.

Iteration 14 added 54 server tests (326, up from 272): 24 on the merge rules, 23 on the
assignment solver and 8 over HTTP (including a frame four jobs all cover). Two were written to **fail first** and did — the
duplicate-on-export and the track-id collision, both of which were real bugs rather than
hypotheticals. Verified to bite by breaking the code they cover:

| Broken deliberately | Caught by |
| --- | --- |
| Track-pair recording in the merge disabled | `test_a_track_crossing_the_seam_keeps_one_identity` |
| The optimal solver swapped for a greedy one | `test_the_optimal_assignment_is_used_where_greedy_would_fail`, plus 8 of the solver's brute-force cases |

The solver is checked **against brute force** — every permutation, on hundreds of random
matrices from 1×1 to 5×5, square and rectangular. An assignment algorithm that is subtly
wrong does not crash; it quietly returns a slightly worse pairing and every caller believes
it, so computing the true optimum independently is the only honest test.

One test was caught checking nothing and rewritten: the first version of the seam test
asserted that the *shared frames* carried one track id, which is trivially true once the
duplicate is dropped. It passed with the feature disabled. The property that matters is that
the id **before** the seam equals the id **after** it.

Iteration 13 added 51 server tests (272, up from 221): 32 in
`tests/services/test_comparison.py` and 19 in `tests/api/test_quality.py`, plus one SDK test
that drives the whole loop over real HTTP. Two of them were written to **fail first** and
did, which is how the precision bug above was caught rather than argued about. Three more
were confirmed to bite by breaking the code they cover:

| Broken deliberately | Caught by |
| --- | --- |
| `Action.UPDATE` → `Action.VIEW` on ground-truth creation | `test_an_annotator_cannot_declare_the_ground_truth` |
| The ground-truth annotation guard disabled | `test_an_annotator_cannot_read_the_answer_key` |
| `frames=None` instead of the overlap | `test_frames_the_ground_truth_never_covered_are_not_scored` |

The load-bearing one is `test_a_polygon_is_not_compared_by_its_bounding_box`: two triangles
sharing a bounding box must score under 0.02, because a bbox approximation would report them
as the same object and every polygon score would be fiction.

Both CLI commands were run against a live uvicorn server — `task ground-truth` (including
its 409 on a second) and `job score` at two thresholds — because a CLI has no test harness
here and "it type-checks" is not the same as "it works".

Iteration 8 added 8 tests in `server/tests/api/test_frame_count_correction.py` (221 server
tests, up from 213) and confirmed they bite: with the correction disabled, 6 of the 8 fail,
including the one that matters — `test_the_frame_the_estimate_lost_is_servable`, which asks
for the last frame over HTTP and gets it.

Iteration 11 added 19 web tests in `timeline.test.ts` (170 web tests, up from 151) and
confirmed they bite: the naive one-span implementation fails 6 of them.

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
| Two documents claimed no third-party source is present, while a third section of one of them listed the file that is | Iteration 3 corrected that sentence in the README only; two other documents kept their copies. A licensing claim that contradicts itself three sections apart is worse than no claim. | Both now defer to **THIRD_PARTY_NOTICES § Adapted source** as the authoritative list |
| Scrubbing frames quickly **threw in the canvas** | `AnnotationCanvas` called `image.decode()`, caught the rejection with `.catch(() => undefined)`, and then carried on into the success path anyway — handing a *broken* `HTMLImageElement` to the renderer, where `drawImage` throws. A swallowed error that does not stop the code it was swallowed for. | `scripts/verify_keyframe_editing.py`, which steps frames fast enough to lose a decode; it now asserts the page raises nothing |
| Marking a departure **froze the frames leading up to it** | `interpolateTrack` holds the previous position when the next keyframe is `outside`, so a departure at frame 6 on a track keyframed at 0 and 10 stopped the object moving on frames 1–5. | `pins the frame before, because a departure freezes what leads up to it` |
| A **rotated rectangle exported as its unrotated box** in YOLO | A rotated rectangle is stored as the unrotated box plus an angle; the exporter used `points[:4]` directly, so a 100×20 bar turned 90° (occupying 20×100) was written as 100×20 — a box that does not contain its object. `capabilities` also omitted `ROTATED_RECTANGLE`, so the warning said such shapes were *dropped* while they were being written wrong. | `test_a_rotated_box_exports_its_real_extent` and `test_the_capabilities_no_longer_claim_it_is_dropped`; the first confirmed to fail with the fix reverted |
| A quality report **did not record what it measured** | `QualityReport` stored a score and a timestamp and nothing about the annotations behind it, so a report read as current however much work had landed since. The failure ran the wrong way: the annotator fixes everything, the old F1 stays on screen, and a reviewer rejects the job on a measurement of work that no longer exists. | `test_a_report_records_the_version_of_the_job_it_scored`, confirmed to fail with the fix reverted (`assert None == 1`) |
| A conflict's two label fields **swapped meaning depending on the kind** | `label_id` carried the ground truth's label on `missing` and `poor_overlap` and the annotator's on `extra` and `wrong_label`; `expected_label_id` was set on `wrong_label` alone. The panel showed `Missed: unlabelled` where the ground truth said *car*. Worse: candidates are paired by **geometry, not label**, so a `poor_overlap` reported the reference's label while `shape_id` named the annotator's shape — "Loose geometry: car" clicking through to a box labelled *person*. | `TestConflictLabelsMeanOneThing` (4 tests) and `scripts/verify_quality_panel.py`, which is what caught it: the unit fixture happened to be built the way the client read it, so both agreed and both were wrong |
| Every object on a job seam was **exported twice** | A task with `overlap > 0` shares frames between two jobs by design; export appended each job's shapes instead of reconciling them. The archive is well-formed and nothing errors, so a model simply trains on doubled boxes. `overlap` is API-settable and had no test coverage at all. | `test_one_object_annotated_in_both_jobs_is_exported_once`, written to fail first |
| Two unrelated tracks in different jobs shared one `track_id` | The id fell back to a job-local `enumerate` index, so job 1's first object and job 2's first object were both `0`. Any consumer grouping by track id welds them into one. No overlap needed — a plain segmented task was enough. | `test_two_unrelated_tracks_in_different_jobs_get_different_ids`, written to fail first |
| A track crossing a job seam was exported as two objects | Even once the duplicate was removed, the two halves kept different ids, so the export said the car vanished and a stranger appeared — the precise discontinuity the overlap exists to prevent. | `test_a_track_crossing_the_seam_keeps_one_identity`; confirmed to fail with unification disabled |
| A box drawn too loosely scored **precision 1.0** | A below-threshold annotated shape was counted as a false negative against the ground truth but skipped in the false-positive pass, so it never reached precision's denominator. An annotator who drew everything sloppily got "everything you drew was right". Found by dumping a real report to check a doc example, not by a test. | `test_a_near_miss_costs_precision_as_well_as_recall` and `test_one_loose_box_over_two_objects_is_counted_once`, both written to fail first |
| An annotator could read the ground truth they were scored against | `(ANNOTATION, VIEW)` floors at `Role.VIEWER`, and nothing distinguished a ground-truth job from any other. The answer key was readable by everyone being tested on it. | `test_an_annotator_cannot_read_the_answer_key`; confirmed to fail with the guard disabled |
| A polyline could "match" a rectangle | `to_polygon` returned a polyline's vertices, which the shoelace formula closes into a phantom triangle — a three-point polyline reported an area of 25 and could claim agreement that does not exist. | `AREA_SHAPES` gates it; `test_a_polyline_encloses_nothing` |

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
- **Merging happens at export, not in the editor.** Two annotators working the same seam
  still both see their own copy while annotating; the reconciliation is applied when the
  dataset is built. That is the right place for it — merging live would mean editing
  someone's job under them — but it means the duplicate is invisible until export.
- **A merged object keeps the earlier job's geometry verbatim.** No averaging of the two
  annotators' boxes. Averaging is defensible and is what a consensus pass would do; picking
  one is predictable, which matters more when nobody is watching. `consensus/intersect_merge.py`
  upstream is the piece to adapt if averaging is ever wanted.
- **A keyframe cannot be dragged along its lane yet.** `moveKeyframe` is written and tested
  but nothing calls it; it needs a pointer-drag rather than a shortcut.
- **Keyframe edits are one write each, not batched.** Each `K` or `O` flushes the shape
  buffer, re-reads the version and PATCHes. That is right for deliberate discrete edits and
  would be wrong for a drag, which is the other reason dragging is not wired yet.
- **YOLO Pose and Classification are export-only.** Pose because `data.yaml` records how
  many keypoints there are but not what they are called, so an import would attach every
  joint to the wrong name; classification because the directory tree *is* the annotation, so
  importing means matching images to frames and a mismatch tags the wrong picture. Both
  refuse with the reason rather than half-working.
- **YOLO OBB writes a polygon as its axis-aligned bounding quadrilateral**, which is a real
  approximation rather than a minimum-area fit. Polygons belong in `yolo` segmentation; the
  capability note says so. A proper rotating-calipers minimum-area rectangle would be the fix
  if anyone needs it.
- **The scissors run on the main thread.** A cursor move that jumps across a large frame can
  cost ~100 ms in the worst case (full 1920 px width, 285k pixels settled). Typical use is
  single-digit milliseconds because the search is lazy, but a very long drag will stutter. A
  worker is the fix if it ever bites; the per-pixel feature pass would move first.
- **Scissors output is a polygon, not a mask.** Which is what the upstream tool produces too,
  and what every shipped export format can carry — but a mask brush would want the region,
  not the boundary.
- **The scissors search grid caps at 1024 on the long side.** On a 4K frame that is a 4x
  downscale, so the wire lands within a few source pixels of the true edge rather than on it
  exactly. Raising it is a constant, and costs time quadratically.
- **A quality report has no UI.** Creating a ground-truth job and scoring against it are
  API, SDK and CLI only. A reviewer working in the editor cannot see any of it, which means
  the feature is currently for scripted workflows. The conflict list is frame- and
  shape-addressed already, so a panel that seeks to a conflict is the obvious next step.
- **The comparison runs inline, not on the queue.** It is arithmetic over rows already in
  the database and a reviewer asking "how did this go" should get an answer rather than a
  task id to poll — but a ground truth of many thousands of frames would make that a slow
  request. `QUEUE_ROUTING` already has a `quality` queue waiting for the day it matters.
- **A ground-truth job covers one contiguous frame range.** `Job` has `start_frame` and
  `stop_frame` and no per-job frame list, so ground-truth frames cannot be *sprinkled*
  through a task the way a honeypot scheme wants. Checking a contiguous slice is the honest
  version of the feature that fits the model; anything finer needs a schema change first.
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

- **Greedy matching for the merge.** Four lines, and it loses pairs that matter. With the
  similarity matrix `[[0.951, 0.860], [0.818, 0.667]]` it takes the best pair first and then
  finds the remainder below the floor, keeping a duplicate the optimal assignment removes.
  `test_the_optimal_assignment_is_used_where_greedy_would_fail` computes the greedy answer
  inline so the test fails if anyone swaps the solver back. Note the deliberate
  inconsistency: `comparison.py` *does* match greedily, because greedy-by-descending-IoU is
  COCO's rule and a quality score that disagrees with COCO would be the wrong number.
- **SciPy for the assignment**, as the upstream uses. Correct and battle-tested, and tens of
  megabytes inside a single-file desktop executable for one function on matrices that are
  almost always smaller than 10×10.
- **Porting the upstream similarity function along with the merge design.** It returns 0 for
  points, polylines, ellipses and 2D cuboids, with a `FIXME` saying so. Ours already computes
  exact IoU for every area shape, so taking theirs would have been a downgrade wearing the
  authority of upstream code. Take the strategy, keep the better primitive.
- **Replacing our interpolation with the upstream `TrackManager`.** Theirs handles mask and
  skeleton interpolation, which ours holds rather than interpolates — a real gap. Not taken:
  our interpolation is implemented identically in TypeScript and Python against shared test
  vectors, and that parity is what makes client-side scrubbing safe. Half of a matched pair
  cannot be swapped for a different algorithm. Revisit when the mask brush and keypoint UI
  exist and the parity can be re-established on both sides at once.
- **Deleting the "every format declares shape types" assertion** when a whole-image
  classification format made it fail. The format is right and the assertion was too narrow,
  but removing it would have left nothing stopping an exporter that carries nothing at all.
  Widened to "shapes **or** tags", with a companion test proving the guard can still fail.
- **Forcing every four-corner OBB label back into a rotated rectangle on import.** The format
  stores a quadrilateral, which is more general; squaring off one that is not rectangular
  quietly moves somebody's annotation. Non-rectangular quads come back as polygons.
- **Loading OpenCV.js for the scissors, as upstream does.** 9,991,739 bytes for one tool, in
  a web bundle that is ~320 kB today and a desktop app that ships as one executable. Their
  196 lines are state management; the algorithm is OpenCV's C++, so reusing their code would
  have bought the wrapper and none of the substance. Full reasoning in ADR 0009. Revisit only
  if a tool needs *several* OpenCV algorithms, at which point the asset is amortised.
- **Building the whole shortest-path tree on each scissors click.** The textbook shape of the
  algorithm, and a 200 ms hitch per click of which almost all was wasted. Expanding the
  frontier lazily is the same algorithm with the same answers and ~70x less work for a
  typical drag.
- **Treating an exactly-zero Laplacian as an edge** in the scissors cost. It looks like the
  obvious reading of "zero crossing" and it is wrong: flat regions are full of exact zeros,
  so it made empty space cheap to cross and the wire stopped tracking boundaries. Only a sign
  change between neighbours is a crossing.
- **A honeypot-frame selector**, adapted from the same upstream source as the comparison
  strategy. It was written, and then deleted: `Job` carries only a contiguous frame range,
  so nothing in this codebase can *call* a function that picks scattered ground-truth frames
  per job. Committing it would have been dead code that reads like a shipped feature —
  precisely what this iteration existed to remove. Revisit if `Job` ever grows an explicit
  frame list; the selector is ~60 lines and easy to rewrite then.
- **Comparing shapes by their bounding boxes.** Vastly simpler, and it would make every
  polygon score fiction: two triangles that share a bounding box would report IoU 1.0.
  Sutherland–Hodgman clipping plus the shoelace formula is about 40 lines and is pinned by
  `test_a_polygon_is_not_compared_by_its_bounding_box`.
- **Matching annotations to ground truth by object identity.** The obvious reading of
  "compare two jobs", and it scores every track against every shape at zero — which would
  make the quality feature useless on exactly the video work it exists for. Matching is by
  *position*: tracks are flattened to their interpolated shape on each frame first.
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
- **Rotating video frames through NumPy**, as the upstream code does. It is the natural
  port, but it would add NumPy as a dependency for an operation Pillow — already required —
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
- **[ADR 0007](./docs/adr/0007-reusing-third-party-code.md) — reusing third-party source.**
  No source is adapted until a licence audit has been run against the upstream repository at
  a named commit. Reuse pays for hard-won domain knowledge (video decoding) and costs for
  framework glue (Django, DRF, SVG.js). **Under MIT the copyright notice travels with the
  code**, and removing it while keeping the code is not an option the ADR leaves open — the
  answer to an unwanted attribution is to re-derive the file, not to drop the notice.
  Never adopt an architecture an ADR rejected on measured grounds: Canvas2D was chosen over
  an SVG DOM on a picking advantage of roughly 7,000× at 100k shapes
  ([ADR 0003](./docs/adr/0003-canvas2d-with-spatial-index.md)).
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
