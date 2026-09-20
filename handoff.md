# Handoff

> **What this file is.** The current, honest state of CurveVision, written for whoever picks
> it up next — a different session, possibly a different agent entirely. Read
> [`AGENTS.md`](./AGENTS.md) first; it is the working contract. Update this file after every
> iteration, including the ones that mostly failed.
>
> **Last updated:** 2026-09-20 (iteration 39) · branch `claude/curvevision-platform-build-n1g71n` · PRs [#1](https://github.com/Derric01/CurveVision/pull/1)–[#17](https://github.com/Derric01/CurveVision/pull/17) **all merged**. [#14](https://github.com/Derric01/CurveVision/pull/14) carried iterations 27–29 (the Node 20 bump, the provisional frame count, the skeleton tool); [#15](https://github.com/Derric01/CurveVision/pull/15) carried 30–31 (the two mask iterations); [#16](https://github.com/Derric01/CurveVision/pull/16) carried 32–33 (the comparison bounding box, open vocabulary); [#17](https://github.com/Derric01/CurveVision/pull/17) carried 34–35 (auto-annotate, suggestion review) — it merged mid-session, while 36–39 were still in flight on the same branch, so those four commits were rebased onto the post-merge `main` rather than left stacked on merged history. Iterations 36–39 are unmerged with no PR open yet.

---

## Status

CurveVision is a working annotation platform in **two shapes from one codebase**: a desktop
application and a server. The manual annotation path is complete end to end — create a
project and label schema, add media, draw, review, export to any of eleven formats — and
is tested. The exporters cover COCO, CVAT XML, Pascal VOC, KITTI, MOTChallenge, indexed
segmentation masks, the lossless native format, and the Ultralytics YOLO family in four
shapes (detection/segmentation, OBB, pose, classification), each declaring in its
`capabilities` exactly what it cannot carry rather than dropping it silently.

The desktop shape works: a packaged single-executable server, a Tauri shell that supervises
it, and folders annotated in place without copying a byte.

**The tree is green.** `./scripts/check.sh` passes all nine steps: 515 server tests, 13 SDK,
529 web. Sixteen browser harnesses drive the packaged desktop application in a real Chromium,
nightly and on every push to `main`.

**A number the platform is not sure about says so.** A video task's frame count starts as an
estimate; where it cannot be replaced by a decoded one — the task already carries annotations,
or the file is truncated or undecodable — the task page says so, names the file, and offers to
recount. That matters because an overstated count offers frames that do not exist.

**The view settings that already existed can now be turned off.** `Scene.showLabels`,
`showSuggestions` and `fillOpacity` were plain public fields nothing outside `Scene` ever
wrote to — flipping one directly would have changed nothing on screen, since nothing called
`invalidate()` for a raw field write. Now a getter/setter pair each, wired as controlled
props on `AnnotationCanvas`, with two toggle buttons and a slider in the Labels panel. The
harness written to prove this caught its own first draft's wrong assumption first: hiding
"labels" hides only the small name chip drawn above a shape, never the shape itself, which
its own first run correctly reported as a failure before the harness was fixed to match.

**A keyboard tool shortcut updates the toolbar and its hints, not only the engine.**
A toolbar click and a shortcut key updated different sources of truth — React's `tool`
state, and the engine's own tool, respectively — with no event connecting the second back to
the first. The engine always ran the right tool; the toolbar highlight and any `tool === 'x'`
hint (the skeleton one, and the brush one this session added) simply stayed on whatever was
active before the key was pressed. Fixed with one new event, `EngineEvents.toolChanged`, and
one shared `activateTool` function so a click and a shortcut can no longer diverge.

**A mask can be painted, not only placed vertex by vertex.** A stroke paints a filled disc
into a frame-sized buffer; dragging leaves a continuous line; right-click or Alt erases;
`[`/`]` resize the brush. Painting into the current *selection* edits that mask instead of
starting a new one, and the selected mask is hidden from the shapes layer for the length of
the edit so an eraser is not left looking like it does nothing underneath its own draft.
Erasing every pixel deletes the object outright, on commit, since the server refuses to
store a mask with no payload. Masks are no longer the one shape type this platform could
carry but not create.

**A shape drawn on any frame but a job's first now lands on the frame it was drawn on.**
It did not before: `AnnotationEngine` had no concept of "current frame", every tool stamped a
new shape's `frame` with a hardcoded placeholder of `0`, and nothing corrected it. The failure
was silent — no error, no warning, the shape was simply gone the next time that frame was
opened — and every browser harness before this one used a single-frame task, the one case
where frame 0 is also the only frame. Fixed centrally in `AnnotationEngine.applyResult`
(`setFrame`, called from `AnnotationCanvas`), which is what makes it fix every drawing tool
at once rather than one at a time. Confirmed with a live browser probe before the fix and
after it, and pinned by a new unit test and a new browser harness that draws with two
different tools on two different frames and reads the result back over a full page reload.

**What a model proposes can be accepted or rejected.** Accepting keeps the annotation and
keeps `source="model"` — the dataset still records that a machine drew it and a human agreed
— clearing only the confidence; rejecting deletes it. That pair is what separates a
suggestion awaiting review from one already accepted, and the canvas, the object list and
the review panel all turn on the same predicate, so they cannot disagree about what is
outstanding. Until this iteration the editor dashed every model-sourced annotation, which
would have made accepting one look like a button that did nothing.

**A model can be run from the editor.** The right rail has an auto-annotate panel: choose a
model, and for an open-vocabulary one type what to look for; predictions land as ordinary
annotations marked `source="model"`, drawn dashed, editable like anything drawn by hand. It
says before the run what it will look for, where those classes came from, and which of them
have no project label to land in — the server reports that last one only afterwards, once the
model has already spent the time. Driven end to end against a **real** model server in
`verify_auto_annotate.py`. What still does not exist is a model to point it at: see below.

**A model can be asked to find things by name.** The inference contract assumed every model
has a fixed label space, so there was no way to say *find forklifts* unless somebody had
trained a forklift detector. A registration now declares `open_vocabulary`, a run carries
`classes`, and an open-vocabulary run with none falls back to the project's own label
schema — a project that declared `forklift` and `pallet` has already said what it wants.

**Scoring a job against a ground truth is fast, and that is measured.** This file said for
several iterations that the comparison should move to the unused `quality` queue because a
large ground truth would be slow. Measuring it found the worry aimed at the wrong dimension —
frames are free; *objects per frame* was quadratic — and an exact bounding-box rejection ahead
of the polygon clip took the worst case from 34 seconds to 0.7. The inline path stays, now
for a reason somebody can reproduce.

**A mask is drawn as its pixels.** The editor used to draw one through the same branch as a
rectangle — an empty box where the pixels were — so an annotator could not tell a mask
covering a whole car from one covering its wing mirror. It now paints the pixels, and picks
by them: clicking an empty corner of a mask's bounding box no longer selects it.

**A mask now actually exports.** It did not, despite the roadmap saying so: `cvat_xml`
declared `ShapeType.MASK` and wrote the two corners of the bounding box, and
`segmentation_mask` — the mask format — matched no mask shape and exported a frame of them as
background. Both write the real thing now, `cvat_xml` reads it back, and `formats/rle.py`
states the encoding once so nothing has to guess it.

**A skeleton can now be drawn, not only stored and exported.** The joints of a label are
placed in the order it declares them, a joint nobody can see is recorded as invisible rather
than dropped — which is what keeps `yolo_pose`'s positional triples lined up — and the bones
are drawn. That closes the last case of the platform exporting a dataset shape it could not
produce.

Honestly incomplete, and marked as such everywhere: the mask brush, resumable uploads, and
signed desktop installers.

**Annotation quality is measured rather than declared, and a reviewer can now see it.** A
task holds a ground-truth job; scoring an annotation job against it produces per-label
precision/recall/F1 and a conflict list naming each mistake. The editor shows that report in
its right-hand panel, and **clicking a conflict seeks to its frame** — a conflict is a place,
not a statistic. Building the panel turned up a real gap in the model: a report recorded no
version of the job it scored, so a score taken before the annotator fixed everything looked
current. `QualityReport.annotation_version` closes that, and the panel marks a stale report
stale. The answer key is created from the task page too, and a blank frame range there means
*the whole task* rather than frame 0 — a distinction a browser harness exists to protect.

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

**A reference open-vocabulary model server, in its own repository.** The contract can now
express *find these classes*, and nothing exists to point it at — which is the onboarding
cost ADR 0005 accepted and explicitly planned to mitigate with "documented reference
implementations… as separate repositories rather than bundled weights". YOLO-World is
**GPL-v3** (AILab-CVC, on MMDetection/MMYOLO; Ultralytics' own build is AGPL-3.0), so it
must stay outside this repository — which is exactly what ADR 0005 already decided, and no
part of it needs revisiting. A FastAPI wrapper speaking the documented contract is perhaps a
day's work; it needs the user's go-ahead because it is a new repository.

**Then: an auto-annotate surface in the editor.** Nothing in the UI calls the inference
endpoint at all. With the fallback above, the minimum useful surface is a button and an
optional class box — the project schema supplies the rest.

**Then: the mask brush.** Everything around it is now in place — the encoding is stated on
both sides and pinned by a shared fixture, two formats carry a mask, and the editor draws one
pixel for pixel and picks it by its pixels. `canvas/mask.ts` already exports `encodeRle`,
which is what a brush commits with, and the scissors tool already reads frame pixels through
`ToolContext.imageData`, which is the awkward part of a brush solved once already.

Two things to decide before starting. **A brush edits an existing mask as often as it starts
a new one**, so the tool needs a notion of "the mask I am editing" that the other tools do
not have — probably the current selection when it is a mask of the active label. And **a
stroke is not a shape**: dragging paints into a scratch bitmap, and only the pointer-up
commits. Keeping that bitmap the size of the *frame* and cropping to its filled extent on
commit is simpler than growing a box as the stroke wanders, and a frame-sized `Uint8Array` is
a few hundred kilobytes.

*Superseded: "a UI for creating a ground-truth job", "drag a keyframe along its lane",
"show issues in the editor", "put a browser harness in CI" and "surface a task whose frame
count could not be corrected" — all done. This section had also fused two paragraphs into one
unreadable one; that is repaired.*

> **A note for whoever writes the next harness.** The ten in `scripts/` have now found every
> defect the unit suites missed, most recently a deleted asset whose frames the task went on
> counting and a pose exporter that collapsed a straight-armed skeleton's box to zero area. They need a packaged sidecar and a Chromium, so nightly rather than per-push, and
> the sidecar **embeds `web/dist`** — a run needs `npm --prefix web run build` *and* a sidecar
> rebuild, or it silently tests the previous frontend.

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

* **Surface a task whose frame count could not be corrected.** The job reports "this task
  already has annotation work"; nothing shows it to anyone.
* **Put a browser harness in CI.** Nine now exist (`screenshot.py`, `verify_local_import.py`,
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
| Pre-building chunks after upload | Done: probing chains the build once the frame numbering is settled, so the first annotator no longer pays the decode. Not fused into one pass — see the iteration note for why that is not available in general. |
| Surfacing an uncorrected frame count | Complete and driven in a browser. `Asset.frame_count_exact` records whether a count was established by decoding; `GET /tasks/{id}/media` reports it and names the estimated files; the task page warns and offers `POST /tasks/{id}/media/recount`. Both ways a count stays provisional are covered — the declined correction and the file nothing could decode. |
| Webhooks | Complete: signed delivery, capped exponential backoff with jitter, and a retry policy that distinguishes "the receiver is struggling" from "the receiver said no". |
| Keypoint (skeleton) tool | Complete and driven in a browser, through to the exported `yolo_pose` rows: joints placed in declared order, `X` skips one, `Enter` finishes early, `Alt`-click marks a joint occluded, and the bones are drawn. |
| Masks | Stored, **exported** (`cvat_xml` writes CVAT's real mask element and reads it back; `segmentation_mask` paints the pixels), **drawn** — the editor renders the pixels and picks by them — and now **paintable**: the brush creates, grows, shrinks and (by erasing every pixel) deletes one, driven end to end in a browser. Nothing outstanding. |
| Reviewing suggestions | Complete and measured in a browser: a job-wide count, accept and reject in bulk over shapes, tracks and tags, and an accepted suggestion drawn solid rather than dashed. Per-object accept/reject buttons do not exist — an individual suggestion is accepted by editing it and rejected by deleting it, which is what the editor already does. |
| Auto-annotate | Complete for the "run over these frames" kinds and driven in a browser against a real model server: model picker, class box for an open-vocabulary model, the plan stated before the run, unmatched classes named before the run, predictions stored as reviewable suggestions. Interactive kinds (`interactor`, `tracker`) are listed with the reason this panel cannot drive them rather than hidden. |
| Quality reports | Complete end to end and driven in a browser: the task page creates the answer key and shows each job's latest F1, the editor shows the report and seeks to a conflict on click, and a stale report is marked stale. The comparison runs **inline, deliberately and measurably** — 200,000 shapes a side over 10,000 frames score in 4.4s. Nothing outstanding. |
| View settings (label chips, suggestions, fill opacity) | Complete and measured in a browser: three `Scene` fields that had always been read by the renderer but never written to from anywhere now have a getter/setter pair and a control each in the Labels panel. |

*This table went stale once — it still listed the open-folder flow and chunked delivery as
unbuilt several iterations after both shipped, because the narrative sections above were
being updated and this one was not. Check it against* Completed *before trusting it.*

---

## Remaining high-priority work

1. **No model ships, so auto-annotate cannot be tried.** ADR 0005 accepted this
   deliberately ("No out-of-the-box models… a genuine onboarding cost") and planned
   reference servers in separate repositories. The contract is now ready for one.
2. **`choose_files` is still unused.** The shell can open a native *file* picker as well as a
   folder one, and `/tasks/{id}/local-import` accepts a file path. Connecting it is small, and
   deliberately left until someone wants it — the folder case is the one that matters.
3. **Signed installers in CI** — *blocked outside the repository*: one runner per platform
   (PyInstaller does not cross-compile) and signing certificates the project does not have.

---

## Last iteration

### 39 — the view settings that already existed can now be turned off

`Scene.showLabels`, `Scene.showSuggestions` and `Scene.fillOpacity` are as old as the
renderer — `isVisible` and the fill colour have always read them — but were plain public
fields nothing outside `Scene` ever wrote to. Two of the three were named explicitly in this
file as a known gap; the third (`showLabels`) had the identical problem and was simply never
flagged, found while wiring the other two. Fixing two of three identical, adjacent bugs and
leaving the third would have been the kind of inconsistency a reviewer asks about on sight,
so all three were done together.

**A wrong assumption caught by the harness meant to catch wrong assumptions.** The first
version of `verify_view_settings.py` asserted that toggling `showLabels` off hid *both*
shapes on the frame — that is not what it has ever done. Reading the renderer closely
(`Renderer.drawShapes`) showed `showLabels` gates only `paintLabel`, the small name and
confidence chip drawn above a shape's corner; the shape itself is drawn unconditionally,
filtered only by each label's own per-label visibility. The harness's own first run reported
this correctly as a failure — "something is still painted with labels hidden" — which is
exactly the discipline this project asks for: measure before believing, including about your
own code from five minutes ago. Fixed by correcting the harness to probe the chip's own
15px-tall band rather than the shape's centre, and by fixing the UI control's title text
("Hide label names on shapes"), which had made the identical wrong assumption the harness did.

**The fields became a getter/setter pair**, matching how `labels` is already held rather
than the raw public fields these three used to be — a plain field write does not call
`invalidate()`, so assigning one directly would have silently changed nothing on screen, and
`setShowLabels`/`setShowSuggestions`/`setFillOpacity` are what a UI control now has to go
through, each calling `Scene`'s existing `onChange` hook that the engine already wires to a
repaint. `setFillOpacity` clamps to `[0, 1]`; all three are no-ops when the value does not
actually change, so a control that fires on every render does not force a repaint for nothing.

**Wired as controlled props on `AnnotationCanvas`**, the same shape as `activeLabelId` and
`tool` rather than a direct `engine.scene.setX()` call from a click handler — three new
`useEffect`s next to the ones `currentFrame` and `tool` already have, so the engine is
correct from the very first render rather than only after the first click. The controls
themselves sit in the Labels panel header: two small toggle buttons and a fill-opacity
slider, all reporting through the exact same `Scene` methods a future keyboard shortcut or
menu item would.

**`scene.test.ts` is `Scene`'s first dedicated unit test file** — nine tests on the
getter/setter contract: read-back, repaint-on-real-change, no repaint on a no-op, clamping,
and that `setShowSuggestions` genuinely feeds `isVisible`'s predicate. `verify_view_settings.py`
proves the wiring the unit test cannot: two shapes seeded directly over the API — one
manual, one an unreviewed suggestion — probed by reading actual canvas pixels rather than
component state, including the fill-opacity claim (18% vs. 100% alpha, 46 vs. 255).
Confirmed that harness bites the usual way this session has: removed the fill-opacity effect,
checked `tsc`'s exit status before trusting the rebuild, watched exactly that one check fail,
restored.

Verified: `./scripts/check.sh` green — 515 server tests, 13 SDK, 529 web (9 new); sixteen
browser harnesses, the new one included, all passing against the packaged application.


## Iteration 38

### 38 — a keyboard tool shortcut now updates the toolbar and its hints

Logged during the mask brush iteration as a separable finding, and confirmed with a live
browser probe before touching anything, per this session's own rule: press `r`, and the
engine genuinely switched to `RectangleTool` — a drag right after it produced a rectangle —
while the toolbar stayed highlighted on Select. Not a drawing bug; the correct tool always
ran. But real, and general: it holds for every tool's shortcut, not only the ones added
recently, because a toolbar click and a keyboard shortcut have always updated **different
sources of truth**. A click sets React's own `tool` state, which an effect in
`AnnotationCanvas` propagates down to the engine. A shortcut key calls
`AnnotationEngine.handleKey` directly, which switches the engine's tool with no path back to
React — `EngineEvents` had no event for "the active tool changed."

Fixed by adding one: `toolChanged`, fired from every real `setTool` call (the guard against
switching to the tool already active means it never fires for a no-op), wired through
`AnnotationCanvas`'s existing callback-ref pattern to a new `onToolChange` prop.
`EditorPage`'s toolbar click and the new engine event now both funnel through one
`activateTool` function — set the tool, clear the status line — so a shortcut and a click
produce the identical outcome instead of one of the two paths being the only one anybody
remembered to keep in sync.

Why this mattered beyond cosmetics: the tool-specific hint bars this session added to the
brush (iteration 37) and the one already there for the skeleton tool both gate on
`tool === 'x'` — React's state, not the engine's. Before this fix, switching to either by
keyboard would have left the hint showing nothing at all, or the *previous* tool's line,
silently undermining the exact feature those hints exist for.

`engine.test.ts` gained four tests pinning `toolChanged`'s contract directly — that it fires
for `handleKey`, that it also fires for a direct `setTool` call, that it does not fire for a
no-op, and that a sequence of shortcuts each fire exactly once — confirmed to bite by
reverting the one-line emission and watching three of the four go red.
`scripts/verify_tool_sync.py` proves the wiring the unit test cannot: that
`AnnotationCanvas` forwards the event, that `EditorPage` acts on it, and that the toolbar
highlight and the skeleton hint bar actually repaint on the packaged application. Confirmed
that harness bites too, the same way as always this session — revert, check `tsc`'s exit
status before trusting the rebuild, watch four of its eight checks fail, restore.

Verified: `./scripts/check.sh` green — 515 server tests, 13 SDK, 520 web (4 new); fifteen
browser harnesses, the new one included, all passing against the packaged application.


## Iteration 37

### 37 — a mask can be painted, not only placed vertex by vertex

Masks were the last shape type this platform could carry but not create. Storage, both
export formats and pixel-accurate rendering all existed; nothing let an annotator draw one.
The pieces were already positioned for it — `canvas/mask.ts`'s docstring for `encodeRle`
literally said "the encoder a brush will need" — so this iteration built the brush.

**A stroke paints into a buffer the size of the whole frame**, not a box that grows as the
stroke wanders: re-deriving a tight box on every pointer-move would cost more than a
frame-sized `Uint8Array` does, and a new pure function, `paintedBounds`, finds the real box
once, when the stroke ends. Only that cropped box is ever encoded or committed. `paintDisc`
and `paintStroke` (a disc at every point along a segment, so a fast drag leaves no gaps) are
the primitives; `cropFlags` and `paintMaskInto` (stamping a stored mask into the buffer at
its absolute position) are what let a stroke read from and write back to the real encoding.
All four are pure and tested in `mask.test.ts` without a DOM, next to the encoding they build
on.

**Editing an existing mask is choosing the selection, not clicking on the canvas.** A single
selected mask of the active label is what the brush paints into; anything else — nothing
selected, several things, a different label — starts a new one. Painting "whatever is under
the cursor" would let a stray stroke silently absorb an unrelated mask; requiring a
deliberate selection first does not, at the cost of one extra click to start an edit.

**The selected mask is hidden from the shapes layer for the length of the stroke.** Without
this, erasing part of it would look like it did nothing: the shapes layer would keep
painting the *original*, unedited mask underneath the shrinking overlay draft, since the
draft only paints where it now covers and does not erase what is beneath it. The hide is a
direct, unrecorded scene mutation — the same move `SelectTool`'s live drag already makes —
and `cancel()` puts it back if a stroke is aborted before it commits.

**Erasing every pixel deletes the object, on commit, never mid-stroke.** The server refuses
to store a mask shape with no payload, and there is no valid "empty mask" for a client to
hold even transiently, so a frame that has been erased to nothing mid-drag simply keeps
showing the last non-empty state rather than mutating anything; only the final state at
pointer-up decides between an update and a deletion. That decision needed a small,
previously nonexistent capability: `ToolResult` gained a `deleted` field, and
`AnnotationEngine.applyResult` gained a branch for it, mirroring the delete command
`deleteSelection` already used — the first tool ever to need to say "this stroke removed the
object outright" rather than "here is what it created or changed."

**A real, previously-invisible defect turned up before a line of brush code was written.**
Orienting for this task meant reading `AnnotationEngine` closely enough to see that it has no
concept of "current frame" at all — iteration 36, in this same session, fixed every drawing
tool silently saving new shapes to frame 0. Building the brush on top of a corrected engine
meant it inherited the fix for free; `brushTool.test.ts` pins that inheritance explicitly
(`'leaves the frame at 0, for the engine to correct'`) rather than assuming it.

**A second defect, smaller, logged rather than fixed here.** Wiring the brush's status hint
(mirroring the skeleton tool's) surfaced that a keyboard shortcut and a toolbar click update
different sources of truth: a button click sets React state, which an effect propagates to
the engine; a shortcut key calls `engine.handleKey` directly, which switches the engine's
tool with no path back to React at all. So after pressing `b`, the engine is genuinely on the
brush tool, but the toolbar's highlighted button and any `tool === 'x'` conditional hint stay
on whatever was active before. Real, but general — every tool's shortcut has always had it —
and separable from finishing the brush, so it is `handoff.md`'s next item rather than folded
into this diff.

**Testing this needed a live browser probe of its own kind**, since nothing before this
harness had ever driven a real pointer drag through a raster tool. `verify_mask_brush.py`
reads the shapes-layer canvas to prove a stroke visibly paints something (an API record
alone cannot distinguish "painted" from "the request silently never reached the canvas"),
then drives the full lifecycle over the wire: paint, grow by editing the selection, shrink by
erasing, resize with `]`, and finally erase everything and watch the object disappear.
Re-selecting between edits is a plain canvas click through the Select tool rather than a
click on the object-list row — a row click calls `focusAnnotation`, which recentres the
viewport, and would have silently invalidated every fixed screen coordinate the rest of the
harness depends on. Confirmed the harness bites by unregistering `BrushTool` from
`createTool` (checking `tsc`'s exit status before trusting the rebuild, learned the hard way
twice already this session) and watching the very first stroke fail to produce anything to
save.

Verified: `./scripts/check.sh` green — 515 server tests, 13 SDK, 516 web (60 new, including
`brushTool.test.ts`'s 17 and 20 new pixel-function tests in `mask.test.ts`); fourteen browser
harnesses, the new one included, all passing against the packaged application.


## Iteration 36

### 36 — every drawing tool silently saved a new shape to frame 0

Found while orienting for the mask brush, before writing a line of brush code: `AnnotationEngine`
has no notion of "current frame" at all. `Scene` holds whatever `setAnnotations` last loaded
— which the caller has already filtered to one frame — but every tool builds its draft
through `draftAnnotation`, which sets `frame: 0` as a placeholder, and nothing corrected it.

**Confirmed with a live browser probe before touching anything**, per the project's own
rule of measuring before building: seed a 3-frame task, navigate to frame 2, draw a
rectangle with Playwright, save, read the annotation back over the API. It came back stored
at frame 0. No error, no warning — the shape was simply not there the next time frame 2 was
opened. Every browser harness written before this one used a single-frame task, which is the
one case where frame 0 is also the *only* frame, so nothing could have caught it, and this
almost certainly means **every shape ever drawn on a non-first frame of a multi-frame job in
this product's history landed on the wrong frame.**

The fix is centralised rather than per-tool, on purpose: `AnnotationEngine.applyResult` is
the one place every tool's `created` result already passes through, so a `setFrame(frame)`
method plus one line there fixes rectangle, polygon, polyline, ellipse, points, scissors and
skeleton at once, with no tool needing to know what frame it is on. `AnnotationCanvas` calls
`setFrame` whenever `currentFrame` changes, in its own effect next to the existing
`setAnnotations` one — two different calls for two different things, which is exactly the gap
that let this happen: loading the right frame's *existing* shapes was always correct, and
telling the engine which frame a *new* one belongs to had no path at all.

**Testing this needed a first**: `AnnotationEngine` has never had a unit test, because it
schedules its repaint through `requestAnimationFrame`, which plain Node does not define, and
painting needs a 2D context this environment has no DOM to produce. Neither turns out to be
necessary: `applyResult` runs synchronously inside `pointerUp`, before any repaint is
scheduled, so `engine.test.ts` stubs `requestAnimationFrame` with a callback that is recorded
and never invoked — nothing ever reaches the fake layer contexts, which is what lets them be
empty objects. Four tests, one of which stamps two different frames across two draws and
checks the created events differ, specifically to catch a fix that stamps every new shape
with the same wrong constant instead of reading the real current frame.

Also added `scripts/verify_shape_frame.py`, since the unit test proves the logic but not the
wiring — that `AnnotationCanvas` actually calls `setFrame`, that the value survives a save and
a full page reload, and that it is not a constant that happens to match one test's number.
Draws with **two different tools** (rectangle and ellipse) on two different non-zero frames,
because the fix lives in one shared place specifically so it would not need to be added
per-tool, and a fix that only patched `RectangleTool` should not be able to pass this.

**A harness-design lesson, recorded because it wasted the most time this iteration.** The
harness's first version checked the on-screen object count immediately after drawing and
failed on a passing build. The object list is deliberately *not* optimistic — it renders
`annotations.data`, the React Query cache, which only changes once a write round-trips and
the query is invalidated; drawing alone does not touch it. The fix was not to weaken the
check but to make the harness match the product: click the existing manual "Save" button and
wait for the round trip, rather than either trusting the 4-second periodic autosave or
asserting against a value the UI was never designed to update immediately.

**And a repeat of last iteration's exact mistake, this time on purpose, to get past it
correctly.** Reverting the engine fix to confirm the harness bites left `currentFrame`
write-only, which `tsc` correctly flagged — and a failed `tsc` means `vite build` never runs,
so a stale (fixed) `dist` would have been embedded in the sidecar, and the harness would have
"passed" against code it was not actually testing, exactly as the mask-review harness did in
iteration 35. Checked the build's exit status this time before trusting the run: added a
throwaway getter that reads `currentFrame` so the revert compiles, confirmed `tsc` succeeded,
*then* ran the harness — which failed with both shapes on frame 0, as expected. Removed the
getter and restored the real fix afterward.

Verified: `./scripts/check.sh` green — 515 server tests, 13 SDK, 484 web (4 new); thirteen
browser harnesses, the new one included, all passing against the packaged application.


## Iteration 35

### 35 — accepting a suggestion used to change nothing anybody could see

Iteration 34 gave the editor a way to run a model. This is the other half, and it turned up
a defect that had been sitting quietly under the whole feature.

`POST /jobs/{id}/suggestions` and the client's `decideSuggestions` had both existed for many
iterations with nothing calling them. That was the obvious gap. The one underneath it was
worse: **the renderer could not tell an accepted suggestion from a pending one.** Accepting
clears a prediction's `confidence` and deliberately *keeps* `source = "model"`, so the
dataset still records that a machine drew the geometry and a human agreed — while the editor
dashed every `source === 'model'` annotation. Wire up the button and it would have appeared
to do nothing at all.

So the state is a **pair**, and `isUnreviewed` in `canvas/types.ts` now says so once:

| | `confidence` set | `confidence` null |
| --- | --- | --- |
| `source = "model"` | **awaiting review** | accepted as it stands |
| `source = "model_corrected"` | — | accepted, with a human's edits |

The renderer, the scene's visibility filter and the object list's flag all turn on it, so
the canvas and the list cannot disagree about what is outstanding. A prediction always
arrives carrying a confidence (`PredictedShape.confidence` defaults to `1.0`), so a null one
is never a model that simply declined to say.

**The review panel renders nothing when nothing is waiting** — an ordinary hand-annotated
job loses no space in an already-full rail — and its count is **job-wide**, because a run
covers the whole job and an "accept all" scoped to the frame on screen would leave the rest
silently pending.

**The measurement, and a false green worth recording.** "Accepting makes it look accepted"
is a claim about pixels, so the harness measures pixels: it walks the columns of a thin band
across a box's top edge and counts the ones holding an *opaque* pixel, which separates the
stroke from the 0.18-alpha fill beneath it. A `[6, 4]` dash lights 74% of them; a solid
stroke lights 100%.

Reverting the renderer to confirm the check bites reported **every check passed** — because
`tsc` had failed on the now-unused import, `npm run build` never wrote a bundle, and the
run exercised the *previous* frontend. `grep -cE "^error"` had swallowed the failure, since
a TypeScript error line starts with the file name. That is exactly the hazard
`docs/CONTRIBUTING.md` warns about, met from the other direction: not a forgotten rebuild
but a *failed* one reported as a pass. With the revert actually in the bundle the check
failed properly — 70%, "accepting changed nothing on screen". **Check a build's exit status,
never a grep of its output.**

Also widened `decideSuggestions` to tracks and tags. The endpoint has always taken all
three; the client sent only shapes, which would have silently left a tracker's or a
classifier's output unreviewable.

Verified: `./scripts/check.sh` green — 515 server tests, 13 SDK, 480 web (16 new); the
auto-annotate harness now drives the whole loop, run → review → accept → run → reject, with
21 checks against the packaged application.


## Iteration 34

### 34 — auto-annotate from the editor

Iteration 33 built the contract for asking a model to find things by name. Nothing in the
product could ask. `api.models`, `api.runInference` and `api.decideSuggestions` had been on
the web client since the **first** web iteration with nothing calling them — grep found zero
call sites — which is the same shape of gap as the issues API before iteration 22: a whole
server path with no way in.

The panel sits in the editor's right rail, under the object list. Choose a model, and for an
open-vocabulary one type what to look for; what comes back lands as ordinary annotations with
`source: "model"`, drawn dashed, editable and deletable like anything drawn by hand.

**Every decision it makes is a pure function in `autoAnnotate.ts`, tested without a DOM.**
Two of them deliberately restate rules the server already enforces, which is worth naming
rather than leaving as accidental duplication:

- `blockedReason` mirrors `services/inference.resolve_classes`. The server's 422 is still the
  authority; this exists so a dead end is visible **before** a round trip.
- `labelMapping` anticipates what `persist_predictions` will drop. The server reports unmapped
  labels *after* the run, by which point the model has already spent the time. A class with no
  matching project label is now called out before the button is pressed, with the fix ("add
  the label first") rather than just the loss.

Both are phrased on screen as what *will* happen, never as what did.

**The bug the harness found, which is the reason it exists.** The class box is only shown for
an open-vocabulary model. Type `forklift`, then pick a fixed-head model out of the same list:
the box unmounts with the text still in component state, `blockedReason` still saw it, and the
run button went **disabled over text the annotator could no longer see or clear**. A dead end
with no way out, and the first version of the harness passed straight over it — the check read
`run.is_disabled() or classes.count() == 0`, and the second disjunct is *always* true for a
fixed-head model, so it asserted nothing. A check that cannot fail is worse than no check: it
reads like coverage.

The fix is not to block harder. The run goes ahead over the model's own labels, and
`ignoredClassesNote` says plainly that the typed classes are ignored and which kind of model
would use them. The rewritten harness asserts the button is live, the note names `forklift`,
and — the part that mirrors the server rule — that the request on the wire carries **no**
class prompt, which is what the server would 422 for.

**The model server in the harness is real**, not a stub inside the app: an `HTTPServer` on
localhost speaking the documented contract in about thirty lines, which is itself the
demonstration of ADR 0005 — attaching a model is a URL, not a deployment project. The packaged
sidecar reaches it exactly as it would reach anybody's Triton box, so the run exercises
browser → API → provider → HTTP → annotation tables rather than a mock of the middle. It
records every request, which is what lets the harness assert the claim a silent drop would look
identical to: **the classes typed are the classes the model was asked for.**

Verified: `./scripts/check.sh` green — 515 server tests, 13 SDK, 464 web (40 new); twelve
browser harnesses, the new one included, all checks passing against the packaged application.
The two new checks were confirmed to bite by running them against the unfixed panel and
watching Playwright time out on a disabled button.


## Iteration 33

### 33 — a model can be asked to find things by name

Prompted by a direct question: *"if we wanna detect whatever is there in a given image we
need to use yolo world internally and google cv models right?"* The category was right, and
looking for the blocker found one in the contract rather than in the model choice.

`ml/base.py` documented `ModelDescriptor.labels` as *"the model's own label space"* — a
closed-vocabulary assumption — and `InferenceRequest.prompts` was specified for interactive
geometry: *"click points, a box, a previous mask."* So **there was no way to say "detect:
forklift, pallet, hi-vis vest"**. Open-vocabulary detectors — YOLO-World, Grounding DINO,
OWL-ViT — take the class names as text at inference time and are exactly what "detect
whatever is there" needs, and the contract could not express them.

**The two kinds fail in opposite directions, which is why the difference is declared rather
than guessed.** Sending classes to a fixed-head model does nothing: the server drops the
key, the caller gets an ordinary empty result, and nothing says why. Sending none to an
open-vocabulary model returns nothing at all, which reads as a broken model rather than an
empty prompt. So:

| | fixed head | open vocabulary |
| --- | --- | --- |
| `classes` requested | **422**, naming what the model *can* find | sent as given |
| no `classes` requested | `classes: []` | falls back to **the project's own label names** |

That fallback is the product decision worth keeping. A project that has declared `forklift`
and `pallet` has already said what it is looking for, so *auto-annotate this job* needs no
extra input at all. Skeleton joints are excluded from it: "left wrist" is part of an object,
not an object to go looking for.

`open_vocabulary` is a **first-class column** with a migration, not a key in `config` —
`config` is documented as provider *connection* settings (endpoint, headers, timeouts), and
whether a model has a fixed label space is a property of the model. It sits next to
`output_labels` because the two describe the same thing from opposite ends. It defaults to
false, which is the safe reading for every model registered before it existed.

Five tests were confirmed to bite by ignoring the flag and watching the refusals disappear.

**On Google Cloud Vision, since it was asked about in the same breath.** It can be an option
an operator opts into — the HTTP contract already allows it with zero core changes — but it
must never be a default. README line 10 is *"your images never leave your infrastructure"*
and line 66 *"no phone-home, no telemetry"*; a cloud provider that silently became the
default would make the product's headline claim false. If one is ever wired up, the UI has
to say plainly that the chosen model ships images off the machine.

**What is still missing, and it is the thing a user would notice first:** nothing ships to
point this at. ADR 0005 accepted that deliberately and planned the mitigation — reference
implementations as *separate repositories rather than bundled weights*. YOLO-World is
**GPL-v3** (AILab-CVC, built on MMDetection/MMYOLO — not Ultralytics, whose own build is
AGPL-3.0), verified from the project's own README rather than assumed. That copyleft is
precisely what ADR 0005 exists to keep out of the MIT core, and its separate-repository
answer handles it without revisiting anything.

Verified: `./scripts/check.sh` green — 515 server tests (18 new), 13 SDK, 424 web; the
migration applied, rolled back and re-applied against a real SQLite database with a
pre-existing model registration in it, which came back `open_vocabulary = 0`.


## Iteration 32

### 32 — the comparison did not need a queue; it needed a bounding box

This file carried an item for several iterations: *"run the comparison as a background job —
`QUEUE_ROUTING` already has a `quality` queue and nothing routes to it… a ground truth over
thousands of frames changes that calculus."* That was a prediction, and `AGENTS.md` says not
to invent a benchmark. So the first move was to measure it rather than to build the queue.

**The measurement refuted the premise and found a better problem.**
`tests/benchmarks/test_comparison_scale.py` varies the two dimensions independently, because
a single "shapes" number would hide which one matters:

| case | before |
| --- | --- |
| 1,000 frames × 2 objects | 0.056s |
| 10,000 frames × 2 | 0.570s |
| **100,000 frames × 2** | **5.68s** |
| 100 frames × 10 objects | 0.097s |
| 100 frames × 50 objects | 2.28s |
| **100 frames × 200 objects** | **34.0s** |
| busy street: 10,000 frames × 20 | 37.2s |

Frames are free and perfectly linear — a hundred thousand of them cost 5.7 seconds. The item
was aimed at the dimension that does not hurt. What hurts is **objects per frame**, and it is
quadratic: five times the shapes on a frame cost twenty-three times the work.

**Why.** `_compare_frame` ran a full `shape_iou` — build both polygons, two shoelace areas,
a Sutherland–Hodgman clip — for *every* (annotated, truth) pair on the frame, including the
overwhelming majority nowhere near each other. For an ellipse it built a 64-vertex outline to
discover the two shapes did not touch. On a frame of 200 objects a side that is 40,000
polygon clips to find roughly 200 real overlaps.

**The fix is an exact rejection, not an approximation.** `shape_bounds` gives each shape's
axis-aligned box once per shape rather than once per pair, and `boxes_overlap` skips the pair
when the boxes do not intersect: two shapes whose boxes are disjoint cannot share area, so
their IoU is zero and the pair was never going to be a candidate.

| case | before | after |
| --- | --- | --- |
| 100 frames × 200 objects | 34.0s | **0.715s** (48×) |
| 100 frames × 50 objects | 2.28s | 0.117s (20×) |
| busy street | 37.2s | **4.43s** (8.4×) |
| 100,000 frames × 2 | 5.68s | 4.58s |

Throughput is now roughly **flat at ~40,000 shapes/s whatever the crowding** — the quadratic
is gone in practice. The whole benchmark file went from 85s to 16s.

**So the inline path stays, and the docs now say why with a number.** A queue would have
moved a 34-second computation off the request without making it any less than 34 seconds, and
the reviewer would have swapped an answer for a task id to poll. The claim was corrected in
four places that all carried it: the endpoint docstring, `ROADMAP.md` (twice),
`IMPLEMENTATION_PLAN.md`, and this file.

**The risk in a broad phase is rejecting a pair that should have matched**, which would be a
silent scoring bug rather than a slow one. It can only happen by under-reporting a shape's
extent, so the tests cover the shape where the extent is not the coordinate list: an ellipse
stores `[cx, cy, rx, ry]`, and reading its extent straight off the coordinates would put the
box somewhere else entirely. There is a containment invariant — *every vertex `to_polygon`
produces lies inside `shape_bounds`* — checked across all five area shapes, and it was
confirmed to fail (with two others) when the ellipse case is removed.

Verified: `./scripts/check.sh` green — 480 server tests (12 new), 13 SDK, 424 web.


## Iteration 31

### 31 — a mask is drawn as its pixels

The editor drew a mask through the same branch as a rectangle: an empty box where the pixels
were. An annotator could not tell a mask covering a whole car from one covering its wing
mirror, and could not review an imported one at all. With export fixed the iteration before,
this was the other half of making masks usable before a brush is worth building — **you
cannot review a brush whose output you cannot see.**

`canvas/mask.ts` is the TypeScript half of the encoding, and its docstring says out loud that
it is *the second statement of a convention that also lives in `formats/rle.py`*. Two
implementations of one encoding is exactly the arrangement that drifts, and a drift here is
silent: the browser paints one set of pixels and the exporter writes another, with nothing
failing. So both test suites now carry **the same worked example** — an L in a 4×3 box,
encoding to `[0, 3, 1, 1]`, covering four named absolute pixels — and each file's fixture
comment points at the other. If either side changes its mind, that side goes red.

**Rendering.** The decoded bitmap is built once per shape into an offscreen canvas and cached
on the mask payload's identity, so a repaint is a `drawImage` rather than a megapixel loop on
the pointer-move path. The payload is replaced rather than mutated when a shape changes, so
identity is a sound key and needs no version counter; `forgetMasks()` clears the cache when
the frame changes, which also handles a pending shape whose client id becomes a server id.
Smoothing is off: a mask is a per-pixel yes or no, and a softened edge shows the annotator a
boundary that is not the one being stored. A mask that paints its own pixels does not also
get the translucent bounding-box fill, which would tint the empty corners exactly like the
covered ones.

**Picking follows the pixels too.** `hitTest` used to accept any click inside the bounding
box, which for a thin diagonal mask means swallowing clicks across a large empty area and
sitting on top of whatever is really there. It now consults the mask, with a tolerance ring
probed around the pointer so a click a couple of pixels off a visible edge still reaches it —
an annotator clicking a mask and selecting nothing is the worse failure. `annotationBounds`
reads the mask's own box rather than `points`, which an importer or a tool may not have set.

**The harness reads the canvas back rather than trusting a screenshot.**
`scripts/verify_mask_rendering.py` seeds a plus with unequal arms, then uses `getImageData`
to check the crossing and both arms are painted, an empty part of the frame is not, and the
pixel *only a transposed mask would cover* is not either. Then it clicks a covered pixel and
an empty corner and checks the selection each time.

**Two harness bugs on the way, both worth writing down**, because both looked exactly like
renderer bugs:

1. The first version reverse-engineered the image-to-canvas transform from the extent of what
   was painted. That extent includes the outline stroke, so every probe landed off by a few
   pixels and the harness reported the centre of the mask transparent and the corner of its
   box painted — a perfect description of the bug it was meant to catch, from a correct
   renderer.
2. Measuring the frame's rectangle off the *shapes* layer includes the label chip drawn above
   the shape's top-left corner, which stretched it 15px upwards. The fix is to measure off
   the **media** layer, which holds the frame and nothing else, so its lit extent is exactly
   the fitted frame. The aspect-ratio check went from 1.465 to 1.500 and every probe landed.

The lesson for the next harness: when an assertion about pixels fails, suspect the coordinate
conversion before the renderer, and prefer a measurement the page can give you exactly over
one derived from what you drew.

Verified: `./scripts/check.sh` green — 480 server tests, 13 SDK, 424 web (28 new); the
harness green against a freshly packaged sidecar.


## Iteration 30

### 30 — a mask now actually exports

`docs/ROADMAP.md` said *"RLE storage and export exist; the drawing tool does not"*. Storage
did. Export did not — and the claim was about to become the foundation for a mask brush,
which would have produced data only CurveVision's own JSON could carry.

Proved before it was fixed, by exporting one mask shape through every format and looking at
the bytes:

* **`cvat_xml`** listed `ShapeType.MASK` in its capabilities and its notes said masks were
  carried. Its writer fell through to the generic branch and emitted
  `<mask points="10.00,10.00;13.00,12.00">` — the two corners of the bounding box. Every
  pixel dropped, into a file that parses cleanly and looks complete.
* **`segmentation_mask`** — the *segmentation mask* format — matched no mask shape at all.
  A frame of masks exported as a PNG whose only pixel value was 0. Its declared
  `shape_types` were honest about this, but a user exporting masks to the mask format got an
  empty image.
* Only `curvevision` (native JSON) carried one, because it passes the payload through
  verbatim without understanding it.

**`formats/rle.py` states the encoding once.** There was no decoder anywhere on the server,
so the convention had never been written down and nothing could have disagreed with it
visibly. It now says: runs alternate **starting with background** (a leading `0` is normal
and load-bearing), read **row-major inside the box**, summing to `width * height`, with a
trimmed trailing background run accepted on read and emitted on write so `decode` and
`encode` are exact inverses.

**What was checked against CVAT, and what was not, is written down too.** The element —
`rle`, `left`, `top`, `width`, `height`, with the spans *inclusive* (`right - left + 1`) —
was read out of CVAT's own XML serialiser, so the attribute shape is verified rather than
assumed. The run *parity* is not stated in that file; starting with background is the
standard reading and the one that makes a leading `0` mean anything, and the docstring says
that is our convention rather than claiming CVAT byte-compatibility we did not confirm.

`cvat_xml` now writes the real element and reads it back — a mask survives the round trip
byte for byte, and `points` comes back as the inclusive box so bounds, hit-testing and the
label chip still work. `segmentation_mask` paints the pixels through `ImageDraw.point`, in
one Pillow crossing per shape and with no second copy of the mask in memory, and now declares
`ShapeType.MASK`. A shape typed `mask` with no payload is *dropped* rather than written as an
empty element, because a reader cannot tell an empty mask from one that failed to serialise.
A corrupt mask costs its own shape and not the other thousand frames of the export.

**Three more stale claims fell out of the audit and are corrected.** The README's format
table said **"Four formats"** and listed four; eleven ship. `IMPLEMENTATION_PLAN` still
listed KITTI as *Planned* — it shipped some iterations ago, along with MOT, CVAT XML,
segmentation masks and the four YOLO variants. And the README had **two** rows for masks,
one of them a leftover from last iteration's edit, both now replaced by one that says
exactly what is true: stored and exported, drawn as a bounding box rather than its pixels,
and no brush creates one.

The two regressions are pinned by tests that were **confirmed to fail against the old code**
— four for `cvat_xml` and four for `segmentation_mask`, checked by putting the old behaviour
back and watching them go red.

Verified: `./scripts/check.sh` green — 480 server tests (34 new), 13 SDK, 396 web.


## Iteration 29

### 29 — a skeleton can be drawn

The platform has exported `yolo_pose` since the YOLO-variants iteration. Nothing in it could
*draw* a skeleton: the shape type, the parent/child label schema, `skeleton_edges`, the
`elements` write path and four exporters all existed, and the one missing piece was the tool.
It exported a dataset shape it could not produce.

**Joint order is the whole design.** `yolo_pose` writes `px py v` positionally — the third
triple *is* the third declared joint, and there is no name in the file to correct a
mis-ordering. So the tool walks a label's children strictly in order and never lets the
annotator choose which joint to place next, and a joint nobody can see is **skipped, not
omitted**: `X` records it as a zero-visibility element in its own slot. Omitting it would
shorten the row and move every later joint one place left — a dataset that looks correct and
teaches a model to put elbows where wrists are.

Two endings, because the common case should not need a keystroke: placing the last joint
commits by itself, and `Enter` finishes early with the rest padded as skipped. `Backspace`
takes back the last decision, placed or skipped; `Alt`-click marks a joint occluded
(Ultralytics visibility 1 rather than 2); `Escape` throws the skeleton away. A skeleton whose
joints were *all* skipped is discarded rather than written — it is a claim with no content.

**`canvas/skeleton.ts` is pure and holds every rule**, so the ordering can be tested without a
canvas, and the renderer and the tool read the same joint order from one place
(`Scene.skeletonFor`) rather than each deriving it. The draft on screen is built by the same
`buildElements` that produces the commit, so a preview cannot drift from what lands.

**A bone with a skipped endpoint is not drawn.** A line to where a hidden joint "would have
been" is indistinguishable on screen from one somebody annotated, which is the one thing a
keypoint tool must never produce.

**One real bug in the exporter, surfaced by the harness.** `yolo_pose` derives each object's
box from its located keypoints, and that box collapses whenever they are collinear — an arm
seen straight on, or a pose with a single visible joint. The harness's three clicks along one
canvas row exported `h=0.000000` for both poses, and every trainer that reads these files
drops a zero-area box: the annotation vanishes silently. A degenerate side is now given one
pixel, which is the smallest thing that is not nothing. Two tests cover it and both were
confirmed to fail without the guard.

**One UI regression caught on the way.** Joint labels have to be in the canvas' style list —
otherwise a joint has no name or colour — but they are not labels anybody draws *with*. The
first version listed all seventeen of them in the Labels panel alongside their parent, eating
the 1–9 number shortcuts. `LabelStyle.parentId` and `drawableLabels()` separate the two
audiences; the harness asserts the panel offers `['arm', 'car']` and not the joints.

New engine surface, kept small: `ToolResult.status` and an `EngineEvents.toolStatusChanged`,
because the joint order is nowhere on the canvas and without a line of text the annotator is
clicking blind through a seventeen-joint pose. `skeletonHint.ts` turns that into copy,
including the two dead ends — no label has joints, or the chosen one does not — where saying
*which* labels would work is the difference between a next step and a tool that ignores
clicks.

`scripts/verify_skeleton_tool.py` draws two skeletons in a real Chromium, one complete and one
with its middle joint skipped, and follows the second all the way into the exported label
file: same row width for both, visibility 0 in the middle slot, and the joints either side
still visible. That last assertion is the one that would catch a regression in any of the six
layers between the click and the file.

Verified: `./scripts/check.sh` green — 446 server tests, 13 SDK, 396 web (68 of them new for
the skeleton rules, the state machine and the hint copy); the harness green against a freshly
packaged sidecar.


## Iterations 27–28

Two iterations on one branch, both small, both closing something that was quietly wrong.

### 27 — CI was running on a deprecated Node

GitHub now forces every action declaring `using: node20` onto Node 24 and warns on each run.
Four actions were in that set across both workflows. Each moved to the lowest major that
actually declares `node24`, which keeps the breaking-change surface as small as the fix
allows — and the target versions were read out of each action's own `action.yml` rather than
from release notes, because one of them is not what you would guess: **`upload-artifact@v5`
still declares `node20`** and would have fixed nothing. v6 is the minimum there.

Separately, the Node the project is *built with* was pinned to 20, which reached end of life
in April 2026 — a different thing from the action runtime, and not covered by that bump. It
is now 22, the version this repository is actually developed and tested against. The web
image, the README badge and the two docs that state the requirement moved with it, and
`web/package.json` declares `engines.node` so the claim lives in the file the badge links to
instead of only in prose.

`browser.yml` does not run on pull requests by design, so the `upload-artifact@v6` bump is
not covered by PR CI. It was exercised by running the harnesses locally; a
`workflow_dispatch` run is the way to confirm it in CI.

### 28 — a frame count that is a guess now says so

This was the oldest open item in this file: *"the job reports 'this task already has
annotation work'; nothing shows it to anyone."*

A video task is created with an estimated frame count, because counting means decoding the
whole file. `media.probe_task` normally replaces the estimate within seconds. It can also
**decline** — the task already carries annotations, so its frame ranges are not the job's to
move — or **fail**, on a file that is truncated, moved, or undecodable. In both cases the task
kept a number that may offer frames the media does not contain, and an annotator who reaches
one meets what looks like missing media. The outcome was reported into a background-task row
nobody reads.

**The model now records it.** `Asset.frame_count_exact` says whether a count was established
by decoding. It is set where the asset is created (an image is exact by construction; a video
is not), and set again by `correct_frame_counts` for every asset it manages to count —
**including the ones whose number did not move**, because the flag records *the decode*, not
the change. Marking only the assets that changed would leave every correctly-estimated video
warning forever.

`GET /tasks/{id}/media` reports `frame_count_exact`, names up to five estimated files and
gives the true total; `AssetOut` carries the same flag per asset. The task page turns that
into a warning that **names the file**, with a **Recount frames** button
(`POST /tasks/{id}/media/recount`, which needs `update` on the task — a corrected count
repartitions jobs — while *reading* the warning needs only read access, since the annotator
who meets the missing frame is the one who needs to see it).

**A performance bug fell out of it.** `needs_exact_count` used to mean only "this task holds
video", so adding three photographs to a task holding a counted two-hour clip scheduled a job
that decoded the whole clip again to arrive at a number already on the row. It now means "…
whose count has not been established", and `correct_frame_counts` skips an asset already
marked exact. Blobs are content-addressed and immutable, so there is nothing to re-check.

**And a real bug, found by the browser harness rather than by any unit test.**
`scripts/verify_frame_count_warning.py` deletes the uncountable clip and expects the frames it
invented to go with it. They did not. `delete_asset` marked the row deleted; `recount_frames`
then queried the assets — and the sessionmaker is built with **`autoflush=False`**, so the
DELETE had not reached the database and the recount summed the very asset it was dropping.
The task kept a frame count, and job ranges, for media that was gone. One `await
session.flush()` fixes it. It reproduces on a plain three-image task with no video anywhere
near it, which is now a test.

**On the migration's backfill.** Image assets are set exact — an image contributes exactly one
frame by definition. Video assets are left False, which understates what is known for the
ones already counted before the column existed. That is the safe direction: the consequence is
a warning on a task that turns out to be fine, cleared by one idempotent re-probe. The
opposite default would silence the warning on exactly the tasks it exists for.

**On the harness, which is the part worth copying.** The state is manufactured *the way a user
reaches it*, entirely through the public API, with nothing reaching into the database to fake
a flag: the second upload is a clip truncated mid-stream — what an interrupted transfer
produces — whose header still probes (2.333s at 3 fps, so the task takes the estimate of six)
and which decodes to none of them. Six frames, none of which exist. The truncation offset is
**searched for rather than hardcoded**, and the harness asserts that premise before it
asserts anything else, so a future PyAV that decodes the file differently fails on the premise
and says so instead of passing while testing nothing.

Verified: `./scripts/check.sh` green — nine steps, **444 server tests, 13 SDK, 339 web**; the migration
applied, rolled back and re-applied against a real SQLite database **with rows in it**, with
the backfill checked per asset kind; the harness green against a freshly packaged sidecar.


## Iteration 26

**The browser harnesses run automatically.** Eight of them existed and had found every defect
the unit suites missed; none ran without somebody remembering to. `.github/workflows/browser.yml`
runs them **nightly and on every push to `main`**.

**Not on pull requests, deliberately.** A PyInstaller build plus eight end-to-end runs is
roughly twenty minutes. Per-PR that is a tax on every push, and CI that slow stops being run
at all. Running at merge catches a regression minutes after it lands rather than up to a day
later, which is the useful half of the tradeoff at a fraction of the cost.

**The step order is the whole point, and the workflow says so in a comment.** The packaged
sidecar *embeds* `web/dist`, so building the frontend after the sidecar — or not at all —
leaves the harnesses driving whichever frontend was bundled last. Nothing fails; the run just
exercises code that is not the code under review. That has already cost one confusing session
locally, which is why it is written down in the workflow, in `CONTRIBUTING.md`, and here.

**One failure does not stop the rest.** The loop collects failures and reports them together,
because a run that stops at the first one reports a single broken thing per night — the
slowest possible way to fix several. The shell was tested against stub scripts rather than
assumed: all-pass exits 0, a mid-list failure still runs everything after it and exits 1, and
single-harness selection works.

Screenshots are uploaded as an artifact on failure. On a red run they are the fastest way to
see what the page actually looked like, which no assertion message conveys.

`scripts/screenshot.py` is excluded: it is the README screenshot generator rather than a
check, and it writes into `docs/images/`.

---

## Iteration 25

**Webhook delivery survives a failing receiver — the last item Beta named.**
Delivery was signed and recorded; nothing retried it, so a receiver that was restarting when
an event fired lost it permanently. `webhook.retry` now re-attempts with capped exponential
backoff (10s, 20s, 40s, 80s, capped at 10 minutes) and ±20% jitter.

**The policy decision worth knowing: who is wrong decides whether to retry.** A 5xx, a
timeout or a refused connection says the receiver is struggling and the same request may work
later. A 4xx says the receiver understood and rejected it — a rotated secret, a decommissioned
path — and repeating it cannot change the answer. Retrying those is not resilience; it is a
slow burst of identical failing requests against somebody's endpoint, which looks like an
attack and fills their logs. Two exceptions, because both mean *later* rather than *never*:
**408** and **429**.

**A pre-existing bug this uncovered, and it was load-bearing.** `dispatch` stamped
`delivered_at` for **any** response, a 500 included — so a failed delivery was
indistinguishable from a good one in the table, and would have made every retry a no-op, since
a delivery that looks delivered is never re-attempted. `delivered_at` is now set only by a
2xx. Restoring the old line fails four tests, which is how it was confirmed rather than
assumed.

**A retry is the same event again**: identical bytes, identical `delivery_id`, freshly signed.
A receiver that processed an attempt and then failed to reply can deduplicate on that id
rather than doing the work twice. Delivery is **at-least-once**, and `docs/API.md` now says so.

**No new column.** "Gave up" is derivable — no success, and `should_retry` says no — so
`exhausted()` computes it rather than a column duplicating what `attempts`, `status_code` and
`delivered_at` already say and eventually disagreeing with them.

The queue grew `delay_seconds`: Dramatiq takes it as `delay` in milliseconds, the inline queue
sleeps before running, and the test queue ignores it deliberately so the suite does not sleep
out an 80-second backoff for real.

**Webhooks had no tests at all** before this. There are 35 now.

---

## Iteration 24

**A video's chunks are built at upload, not by the first person to open it.**
`media.build_chunks` had existed with nothing enqueueing it, so a chunk was only ever built
by the request that first asked for one — which is a request somebody is waiting on.
`media.probe_task` now chains it.

**Why it is chained rather than fused into the count, which is what this file previously
proposed.** Counting and building both walk the video, so one pass looks like the obvious
saving. It is not available in general: a chunk is addressed by the *task's* frame numbering,
and that numbering is only final once **every** asset has been counted — an earlier asset
gaining a frame shifts every later asset's offset, which is exactly why `discard_chunks`
throws them all away wholesale. A fused pass is therefore correct only for a single-asset
task and would need a second implementation for every other shape, to save one decode that is
already off the request path.

**Two corrections to what this file used to say:**

* *"The desktop queue is inline, so a job that takes a minute blocks the request that queued
  it."* **Not true.** `InlineJobQueue` defaults to `wait=False` and schedules through
  `asyncio.create_task`; only the test configuration uses `wait=True`. Nothing blocks.
* The single-pass proposal above, which the ordering constraint rules out.

**A bug I introduced and the tests caught.** The build was first enqueued with an idempotency
key of task + frame count, to stop repeated probes stacking decodes. That key is wrong in the
direction that loses data: a probe which *discards* the chunks and re-enqueues dedupes against
the earlier, already-succeeded job for the same numbering — so the build never runs and the
task is left with **no chunks at all**, while every job row reads "succeeded". A key cannot
express "the chunks from that build still exist". The key is gone; the cost of not
deduplicating is one job row and a no-op pass over chunk indices, because `build_chunk`
returns an existing chunk rather than rebuilding it.

**Two existing tests changed meaning and were rewritten rather than relaxed.**
`test_serving_a_frame_builds_its_chunk_and_the_next_frames_reuse_it` asserted "nothing is
decoded until a frame is asked for", which is precisely what this iteration changes; it now
discards the pre-built chunks first, so it still tests the lazy fallback — the path that
guarantees a frame is never *unavailable*, only slower. The race test counted every chunk on
the task to prove no duplicate; it now counts chunk 0 specifically, which is what the race was
actually over.

---

## Iteration 23

**An issue can be pinned to a point on the image.** `Issue.position` had been stored and
accepted by the API with nothing placing one, so an issue said "frame 40, this object" rather
than "here". *Pin* arms the canvas, one click places the point, and the pin draws on the
overlay — amber while open, dimmed once resolved, brighter for the thread being read.

**A pin is overlay state, not a scene object.** Keeping it out of the scene is what
guarantees it cannot be selected, dragged, deleted by a marquee, or exported — four
properties that would otherwise each need remembering as a special case. It is drawn at a
**fixed screen size**: a marker that shrinks with the image stops doing the one job it has.

**Picking is armed and spent in one click.** The engine disarms itself on the click, so the
canvas never sits in a mode the annotator has to find their way out of. Panning still works
while armed, because a reviewer needs to reach the part of the frame they mean first.

**`hasPin` is stricter than "the field exists".** `position` is free-form JSON server-side
and defaults to `[]`, so a partial pair or a `null` that survived a round trip would place a
marker at `NaN` — which paints nothing and leaves the annotator hunting for a pin that was
never drawn. Only two finite numbers count.

*A note on the harness, because it was wrong first.* The pin check originally asserted the
stored point was near the centre of the **image**, on the reasoning that clicking the centre
of the canvas hits the centre of the frame. It does not: an earlier step in the same harness
focuses the selected object, so the viewport is centred on that box at 479%. The assertion
now expects the focused box's centre — `[20, 40, 90, 110]` → `(55, 75)` — which the
implementation hits **exactly, off by 0.0 px**. That is a stronger check than the tolerance it
replaced, and it fails for either mistake worth catching: a screen coordinate passed straight
through, and a conversion that ignores pan or scale.

---

## Iteration 22

**Issues are reachable from the editor — and building the panel found a bug that had
disconnected seven controls.**

Issues are how a reviewer sends work back. The model, the API and the permissions have
existed since the first iteration and the roadmap said **Done**; nothing called them, so
receiving review feedback meant reading it out of the database. That row was a false Done and
is now a true one: the editor lists issues, opens one on the current frame, replies on a
thread, and resolves or reopens.

**The bug the harness found is much larger than the panel.** `AnnotationCanvas` exposed its
engine with

```ts
useImperativeHandle(ref, () => ({ engine: engineRef.current }), []);
```

`useImperativeHandle` runs as a layout effect and is declared *above* the effect that
constructs the engine, so it captured `null` — and with an empty dependency list it stayed
null for the life of the component. **Every control the editor drives through that handle did
nothing**: undo, redo, delete selection, fit to frame, the label visibility and lock toggles,
and focusing an object from the object list. No error, no console warning, just seven buttons
wired to nothing. The handle is now a getter that reads the live ref, and the harness asserts
it by clicking an object row and watching the zoom move (335% → 479%).

**The anchoring rule worth keeping in mind.** A track materialised onto a frame is not a
shape: it has no row in the shapes table, and the editor gives it the *track's* id. Sending
that as `shape_id` sets a foreign key to nothing — the issue saves, lists, and silently stops
pointing at the object it was about. `anchorFor` picks the column from `annotation.trackId`,
and the harness asserts against the API which column was actually filled in.

Only a single selection anchors: "these three boxes are wrong" is a different comment from
"this one is", and an issue points at one object.

**Not built, and stated rather than implied:** issues carry a frame and an object, not a
**point**. `position` is stored and the API accepts it, but placing a pin needs a
click-to-place interaction on the canvas that does not exist.

*Note for whoever adds the next React component:* the test environment is `node` with no
jsdom, so a ref-timing bug like this one is invisible to `vitest` by construction. The
browser harnesses are the layer that catches it, and they did. Adding jsdom plus a React
testing library would catch this class earlier at the cost of two dependencies — a real
option, not taken here.

---

## Iteration 21

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
  ruff · ruff format · mypy · pytest server (434) · pytest sdk (13)
  notices (52 deps) · eslint · tsc · vitest (317)
```

Iteration 26 added no tests: it is a CI workflow, and the thing it runs is the test. The shell
logic was exercised against stub harnesses (pass, fail, pass) to confirm the loop continues
past a failure and still exits non-zero, and the YAML was parsed rather than eyeballed.

Iteration 25 added 35 server tests (434, up from 399) — webhooks had none at all, which is
its own finding for a feature the roadmap called partly built. The `delivered_at` fix was
confirmed by restoring the old line and watching four tests fail.

Iteration 24 added 5 server tests (399, up from 394) and no web tests — the change is
entirely server-side. All five were confirmed to fail with the chaining reverted, and the one
that matters most was confirmed to fail with the idempotency key restored:

```
FAILED test_a_probe_that_discards_chunks_rebuilds_them   -   assert []
```

That is the bug the key caused, reproduced exactly: chunks discarded, build deduplicated
against a job that had already succeeded, nothing rebuilt.

Iteration 23 added 8 web tests (317, up from 309) and no server tests — `position` has been
in the schema since the first iteration; what was missing was anything that wrote to it.

```
ok   clicking the image places a pin and the panel says where
     pinned at 55, 75
     position stored: [55.0, 75.0]
ok   the issue carries a two-number point
     expected the focused box's centre (55.0, 75.0); off by 0.0, 0.0 px
ok   the stored point is in image space, through the live viewport
```

Iteration 22 added 16 web tests (309, up from 293) and no server tests — the issues API was
covered from the first iteration; what was missing was a caller.

```
     zoom: 335% -> 479%
ok   clicking an object reaches the engine and focuses it
ok   the panel says it will attach the issue to the selected object
     anchor: shape_id=None track_id=45ae1a7b-…
ok   an issue on a track anchors by track_id, not as a phantom shape
     thread: ['this box is too loose on the left', 'tightened it']
ok   the reply landed on the same thread, after the first comment
ok   resolving records who closed it and when
ok   a resolved issue leaves the open list
ok   but is still reachable, rather than hidden
```

The zoom line is the discriminating one: it is what fails when the canvas handle is dead, and
it failed twice before the fix — the first time for a different reason, because the harness
located object rows as `aside li button` and the *label* list matches that selector too.
Clicking a label row selects the active label and leaves the selection empty, which looks
exactly like a broken anchor. `data-object-id` now tells them apart.

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
| **`cvat_xml` dropped every pixel of every mask** | The writer had no mask branch, so a mask fell through to the generic `points` one and was written as the two corners of its bounding box — while the format's capabilities declared `ShapeType.MASK` and its notes said masks were carried. The file parsed cleanly and looked complete. | Four tests in `test_formats_masks.py::TestCvatXmlMasks`, confirmed to fail with the old branch restored |
| **`segmentation_mask` could not export a segmentation mask** | A mask shape matched nothing in `FILLABLE`, so it contributed no pixels and a frame of masks exported as a page of background. | Four tests in `test_formats_masks.py::TestSegmentationMask`, confirmed to fail with `ShapeType.MASK` taken back out of `FILLABLE` |
| **`yolo_pose` collapsed a straight pose's box to zero area** | The box is derived from the located keypoints, so collinear joints — an arm seen straight on, or a pose with one visible joint — gave `w=0` or `h=0`. Every trainer that reads these files drops a zero-area box, so the annotation vanished silently. A degenerate side now gets one pixel. | `scripts/verify_skeleton_tool.py` exported `h=0.000000`; `test_a_collapsed_box_is_given_a_pixel_rather_than_written_as_nothing` and `test_a_single_visible_joint_still_gets_a_box`, both confirmed to fail without the guard |
| **A deleted asset's frames were still counted** | `delete_asset` marked the row deleted; every caller then recounts the task's frames. The sessionmaker is built with `autoflush=False`, so the DELETE had not reached the database and the recount summed the asset it was dropping — leaving a frame count, and job ranges, for media that was gone. One `await session.flush()`. | `scripts/verify_frame_count_warning.py` found it; `test_deleting_an_asset_leaves_the_task_counting_only_what_is_left` reproduces it on three images with no video involved |
| Every upload re-decoded video that had already been counted | `needs_exact_count` meant only "this task holds video", so adding three photographs to a task holding a counted two-hour clip scheduled a full decode of the clip. It now also requires the count to be unestablished, and `correct_frame_counts` skips an asset already marked exact. | `test_a_counted_video_is_not_decoded_a_second_time`, which counts the decodes |
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
| **Seven editor controls were wired to nothing** | `AnnotationCanvas` built its imperative handle as `{ engine: engineRef.current }` with an empty dependency list. `useImperativeHandle` runs as a layout effect and was declared above the effect that constructs the engine, so it captured `null` and never updated. Undo, redo, delete selection, fit to frame, both label toggles and focus-an-object all silently did nothing — no error, no warning. | `scripts/verify_issues_panel.py`, which clicks an object row and asserts the zoom changes (335% → 479%); it failed before the fix and passes after |
| Issues were marked **Done** with no UI at all | The model, API and permissions shipped in the first iteration and the roadmap row was never qualified. A reviewer could not see or open one from the application. | The row now says what exists, and `verify_issues_panel.py` drives open → reply → resolve → reopen |
| Every object on a job seam was **exported twice** | A task with `overlap > 0` shares frames between two jobs by design; export appended each job's shapes instead of reconciling them. The archive is well-formed and nothing errors, so a model simply trains on doubled boxes. `overlap` is API-settable and had no test coverage at all. | `test_one_object_annotated_in_both_jobs_is_exported_once`, written to fail first |
| Two unrelated tracks in different jobs shared one `track_id` | The id fell back to a job-local `enumerate` index, so job 1's first object and job 2's first object were both `0`. Any consumer grouping by track id welds them into one. No overlap needed — a plain segmented task was enough. | `test_two_unrelated_tracks_in_different_jobs_get_different_ids`, written to fail first |
| A track crossing a job seam was exported as two objects | Even once the duplicate was removed, the two halves kept different ids, so the export said the car vanished and a stranger appeared — the precise discontinuity the overlap exists to prevent. | `test_a_track_crossing_the_seam_keeps_one_identity`; confirmed to fail with unification disabled |
| A box drawn too loosely scored **precision 1.0** | A below-threshold annotated shape was counted as a false negative against the ground truth but skipped in the false-positive pass, so it never reached precision's denominator. An annotator who drew everything sloppily got "everything you drew was right". Found by dumping a real report to check a doc example, not by a test. | `test_a_near_miss_costs_precision_as_well_as_recall` and `test_one_loose_box_over_two_objects_is_counted_once`, both written to fail first |
| An annotator could read the ground truth they were scored against | `(ANNOTATION, VIEW)` floors at `Role.VIEWER`, and nothing distinguished a ground-truth job from any other. The answer key was readable by everyone being tested on it. | `test_an_annotator_cannot_read_the_answer_key`; confirmed to fail with the guard disabled |
| A polyline could "match" a rectangle | `to_polygon` returned a polyline's vertices, which the shoelace formula closes into a phantom triangle — a three-point polyline reported an area of 25 and could claim agreement that does not exist. | `AREA_SHAPES` gates it; `test_a_polyline_encloses_nothing` |

---

## Known issues

- **A recount on a task that carries annotations decodes the file to reach the same
  refusal.** When `rebuild_jobs` declines, the whole correction is rolled back — including the
  `frame_count_exact` flags, which is right, because the numbers on the rows are the estimates
  again and claiming they were established by decoding would be false. The cost is that the
  next probe walks the file again. The alternative is a task that stops warning about a number
  nobody ever managed to fix, which is worse. If this ever matters, recording the counted
  number *without* applying it is the route — it would also let the warning say "the real
  count is 1,438, not 1,500", which is more useful than what it says now.
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
