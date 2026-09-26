# Handoff

> **What this file is.** The current, honest state of CurveVision, written for whoever picks
> it up next — a different session, possibly a different agent entirely. Read
> [`AGENTS.md`](./AGENTS.md) first; it is the working contract. Update this file after every
> iteration, including the ones that mostly failed.
>
> **Last updated:** 2026-09-26 (iteration 52) · branch `claude/start-work-yx080h` · PRs
> [#1](https://github.com/Derric01/CurveVision/pull/1)–[#22](https://github.com/Derric01/CurveVision/pull/22)
> **all merged**, the last of them carrying iterations 45–51. Iteration 52 is pushed to
> `claude/start-work-yx080h` with no PR opened yet.
>
> *Two things worth knowing about this repository's PR rhythm, which replace the
> PR-by-PR changelog that used to sit here and had stopped helping anybody.* **PRs are
> approved and merged fast, sometimes mid-session** — three times in one earlier session,
> while later commits were still landing on the same branch. Check `git merge-base
> --is-ancestor HEAD origin/main` before pushing rather than after something looks wrong.
> **And a merged branch is finished**: restart it from the new `origin/main` (`git checkout
> -B <branch> origin/main`) rather than stacking new work on already-merged history, which
> is what this session did to begin iteration 45.
>
> **Keep the session short.** One iteration, then commit, push, update this file and report —
> `AGENTS.md` § *Continue* and the `start-work` skill both say so now, because they did not
> before and a single session ran six iterations and roughly a third of a week's budget. The
> concrete costs, in rough order: re-reading this file (iterations 1–46 now live in
> [`docs/iterations/ARCHIVE.md`](./docs/iterations/ARCHIVE.md), which cut it from 3,800
> lines to 1,000 — keep it that way); `python desktop/sidecar/build.py`, which is a
> PyInstaller run of a minute or two and is needed *again* for every sabotage-and-restore
> cycle; and full `./scripts/check.sh` runs. Run the targeted check while you work
> (`pytest <one file>`, `npm --prefix web run test`) and the full one once before you commit.
> Batch sabotages into a single rebuild where the checks are independent.

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

**The tree is green.** `./scripts/check.sh` passes all twelve steps: 582 server tests, 15
SDK, 628 web, and `scripts/` is lint-clean under its own `ruff.toml`. It was **not** green on
arrival this session: `mypy>=1.10` now installs mypy 2.3.1, which rejected an untouched
`services/annotations.py`, so the next CI run on `main` would have failed too. Fixed first,
in its own commit. Twenty-three browser harnesses drive the packaged desktop application in a
real Chromium, nightly and on every push to `main`.

**A label's attributes can be edited, and no edit strands a value already recorded.** The
project page's label form now edits attributes too: add one, rename it, give a select more
options, set a default, remove one. The server side was the careful part and had a real
defect in it. Values are stored on each annotation **keyed by attribute name**, and
`validate_attributes` rejects an undeclared key — so `PUT /labels/{id}` accepted removing or
renaming an attribute, changing its type or dropping a select option, and every annotation
carrying the old value was then refused on its **next save**. The editor sends a shape's
attributes back on every autosave, so the person who found out was the annotator, from a 422
on a box they had only moved. `update_label` now refuses such an edit before touching
anything, using CVAT's rules for an attribute that exists (type and per-frame flag fixed,
options may grow but not shrink) and refusing a rename or removal only while something is
recorded under the name — which keeps a schema correctable before it is used. What the form
still cannot do is set an attribute's **value** on a shape: see *Next best action*.

**Every timestamp the API returns is UTC, and says so.** `DateTime(timezone=True)` means
what it says on PostgreSQL and cannot on SQLite, which has no time-zone type — so the same
column handed back an aware value from one and a naive value from the other, and the same
instant serialised as `...Z` from a freshly-written instance and with no suffix at all once
the row had been read back. A browser reads a suffix-less timestamp as **local** time, so
the desktop shape, which is the SQLite one, showed every time shifted by the viewer's own
UTC offset. `UTCDateTime` in `core/types.py` normalises both directions, alongside `GUID`
and `EnumString` and for the same stated reason: that file is where a backend difference is
allowed to exist. No migration — the DDL it emits is the DDL that was there.

**A `PATCH` can now clear a field, and its answer is not stale.** The same pair of defects
had been found three times in four iterations, each time by building a screen that needed
the behaviour: a nullable field that `is not None` could set but never clear, and a response
built from an instance whose eagerly-loaded relationship still held the previous value.
Iteration 50 went looking for the rest instead of waiting for the fourth. `PATCH /tasks/{id}`
and `PATCH /projects/{id}` had both, on `assignee_id`, `owner_id` and `description` — so a
task could be assigned and never unassigned, a project could be given an owner it could
never be rid of, and a description could be written and never removed. A third
inconsistency turned up in the same sweep: `PATCH /tasks/{id}` returned `TaskDetail` with
`progress` left null while `GET` filled it in, so a screen rendering what a write answered
would blank out the progress bar it had been showing.

**A project's label schema is no longer fixed the moment it is created.** A label is added
from the project page and removed there, which nothing in the application could do:
`POST`/`DELETE /projects/{id}/labels` and the policy gating them had existed since the
initial schema with **nothing calling `api.createLabel`**, so a project that turned out to
need a `van` class could only get one from the SDK, the CLI or curl. A label added later now
lands at the **end** of the schema — it used to take position 0 like every unpositioned
label, and the listing orders by `(position, name)`, so `van` added to a `car`/`pedestrian`
project appeared first, in an order nobody chose. Deleting is safe to offer because the
server refuses a label annotations still reference rather than cascading, and the panel shows
that refusal. A label is **renamed and recoloured** in place too, through a payload rebuilt
from the whole label the server reported — `PUT` is a replace, so posting only the changed
fields would reset the position, lift the shape restriction and delete the attribute
definitions. Editing the **attributes** themselves is still not built. Two further server
defects came out of writing those tests first: a rename could walk straight around
`create_label`'s duplicate-name rule, and the response to a `PUT` reported attributes it had
just deleted.

**A reviewer can ask what is waiting for them.** `GET /jobs?mine=true` filtered on
`assignee_id` alone, so the question "what is waiting on *me* to check" could not be asked
at all: a named reviewer opened each task in turn and read its job list. `reviewing=true` is
the other half of it, and the My work page now asks both and splits the answer — only a
`submitted` job can be reviewed, so everything else named to that reviewer is listed apart
from the work rather than counted as it. Each row names its **task**, which `GET /jobs` had
eagerly loaded since it was written without ever returning: a queue that spans every project
is unreadable as "Job #2, frames 0–1". The whole endpoint was also untested — including the
membership filter deciding whose work a caller can see at all — which it no longer is.

**A job can be handed to a person from the task page, and taken back.** `Job.assignee_id`
and `Job.reviewer_id` have been enforced by the policy engine and settable through
`PATCH /jobs/{id}` since the first iterations, and **no screen set either** — the web client
did not even have a `members` method to ask who the candidates were, so dividing a task
between three annotators meant three API calls from a terminal. Every job row on the task
page now carries an annotator picker and a reviewer picker. Writing the tests before the
code found two server bugs that nothing reading the database could have seen: an omitted
field and an explicit `null` were read the same way, so the nullable column had no route back
to null and **an assigned job could never be unassigned**; and because those relationships
are eagerly loaded under `expire_on_commit=False`, writing the *id* left the loaded
relationship stale, so **the response named the previous holder** — a job gaining its first
assignee came back as `assignee: null`, which a picker would render straight back at the
person who had just assigned it.

**A reviewer can finally rule on a job from the application.** `POST /jobs/{id}/review`, the
state machine behind it and the web client's `reviewJob` had all existed since early in the
project with **nothing calling the last of them**: an annotator could press Submit and no
reviewer could accept or send the job back from the editor at all — the review loop
`docs/ROADMAP.md` called Done ran only for the SDK, the CLI and curl. The editor's rail now
carries the decision, and sending work back **requires a reason**, which the endpoint files
as an issue on the job so the annotator meets it on the page instead of learning only that
the work was refused.

**A large file can now be uploaded a chunk at a time, and resumed if the connection drops.**
The `UploadSession` model, its schemas and `Storage.append` had existed since early in the
project, explicitly documented as prepared for "the resumable-upload protocol" — and
`upload_assets`'s own docstring already claimed "files larger than the request-size limit
use the resumable upload endpoints instead," which was false: no such endpoints existed.
Four now do: `POST /tasks/{id}/uploads` declares a filename and size and is validated
against the same rules a direct upload uses, before any bytes exist; `PATCH .../{id}`
appends a chunk at the offset the client believes it is resuming from, rejecting a mismatch
with a 409 naming the real offset so a chunk resent after a dropped response cannot
duplicate bytes; `GET .../{id}` reports the current offset for a client that lost track of
its own progress; and `POST .../{id}/complete` reads the assembled bytes back and hands them
to the exact dedupe/probe path (`_ingest_bytes`, extracted from `ingest_upload` for this)
a direct multipart upload already uses, so the two can never validate or store a file
differently. The same "recount, rebuild jobs, mark ready, maybe enqueue a probe" sequence
was already duplicated between the direct-upload and local-import routes; a third copy for
this path was the "the next feature needs a fourth" case `AGENTS.md` names as the reason to
pull something out, so it is now one function, `finish_ingestion`, called by all three.
Reached from the SDK, the CLI and a task-page upload panel too, which iteration 43 added —
see *Completed*. (This paragraph said for three iterations that it was API-only; that was
written in iteration 42 and was true for about a day.)

**A cuboid can now be drawn, not only carried.** `ShapeType.CUBOID` has been in the domain
model since early in the project — the comparison/merge IoU logic, track interpolation and
the minimum-points table all already knew about it — but nothing let a human draw one, paint
it, or export it. All three now exist: a two-stage tool (drag the front face, then move and
click to set the depth), a wireframe renderer, and CVAT XML support using CVAT's own real
`xtl1,ytl1,...,ybr2` attribute convention rather than a generic points list, so a box drawn
here reads back as the same box in real CVAT.

**A number the platform is not sure about says so.** A video task's frame count starts as an
estimate; where it cannot be replaced by a decoded one — the task already carries annotations,
or the file is truncated or undecodable — the task page says so, names the file, and offers to
recount. That matters because an overstated count offers frames that do not exist.

**Individual files can be attached to a task, not only a whole folder.** `choose_files` —
the shell's native file dialog — was registered with no menu entry and no web-side caller
at all. `chooseFiles()` now mirrors `chooseFolder()`, and the panel gained a second button:
one HTTP call per chosen file, sequentially (the endpoint would race two concurrent calls
for the same task's next frame position), with a file that cannot be attached reported
rather than aborting the rest — the same principle the folder import already lived by,
extended to a whole failed request rather than one bad entry in a directory listing.

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

Honestly incomplete, and marked as such everywhere: signed desktop installers, blocked
outside the repository, and a reference model server, which needs a repository this session
cannot create. Everything else this paragraph has named over the last several iterations —
the mask brush, the resumable upload's SDK, CLI and browser clients, and the reviewer's
queue — is finished, in iterations 37, 43 and 47 respectively.

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

**Let an annotator set an attribute's value on a shape, from the editor.** A label's attribute
*definitions* can now be edited from the project page (iteration 52), and **no screen sets a
value**: the editor carries `shape.attributes` through every save (`adapters.ts`) and never
lets a person change one. So `occluded`, `truncated` or a car's colour — the ordinary reason
a schema has attributes at all — can only be recorded from the SDK, the CLI or an import, or
by a default. It also leaves a real trap reachable today: an attribute that is `required`
with no default (settable from the SDK) makes its label **impossible to draw with in the
editor**, because every tool creates a shape with `attributes: {}` and the server answers
`422 Attribute 'make' is required` — confirmed this session. The attribute form deliberately
does not offer `required` until this exists. The work is a panel on the right rail for the
selected object: one control per attribute by type (checkbox, select, radio, text, number),
writing through the same autosave path as a move; and for a track, a **mutable** attribute's
value belongs to the keyframe (`TrackShape.attributes`) while an immutable one belongs to
the track — the one distinction here that is easy to get wrong. CVAT's
`object-item-details.tsx` and `object-item-attribute.tsx` (under
`cvat-ui/src/components/annotation-page/standard-workspace/objects-side-bar/`, both confirmed
to exist on `develop`) are the obvious reading first. Fix the checkbox-default quirk under *Known issues* in the
same change, since a checkbox control is where it would show.

**A second candidate, smaller and unglamorous: generate `web/src/api/types.ts` from the
OpenAPI schema.** The file is hand-maintained and nothing verifies it against the server —
its own header claimed otherwise until iteration 46 corrected it (see below). A drifted type
is not caught by `tsc`, which only knows what the file says; it is caught by a person
debugging a field that is always `undefined`. The server already serves the schema, so this
is a build step and a check, not a design problem.

**Then, still needing the user:**

* **A reference open-vocabulary model server, in its own repository.** The contract can
  express *find these classes*, and nothing exists to point it at — which is the onboarding
  cost ADR 0005 accepted and explicitly planned to mitigate with "documented reference
  implementations… as separate repositories rather than bundled weights". YOLO-World is
  **GPL-v3** (AILab-CVC, on MMDetection/MMYOLO; Ultralytics' own build is AGPL-3.0), so it
  must stay outside this repository — which is exactly what ADR 0005 already decided, and no
  part of it needs revisiting. A FastAPI wrapper speaking the documented contract is perhaps
  a day's work; it needs the user's go-ahead because it is a new repository this session has
  no access to create.
* **Signed installers in CI** — one runner per platform and signing certificates the project
  does not have.

Past those, the remaining candidates are all 1.0-list infrastructure with no specific gap
pulling any one of them forward — Redis-backed rate limiting, OpenTelemetry tracing, the
ClamAV hook, OIDC/SSO, a Helm chart, backup/restore tooling, a published TypeScript client,
a Datumaro bridge, an external security review. Picking one without a reason beyond "it is
on the list" is exactly the "next unchecked box" `AGENTS.md` says not to default to.

> **How the last two real gaps were found, since both came from the same habit rather than
> from this list.** Iteration 42 found a docstring that named endpoints which did not exist.
> Iteration 45 listed every method on the web API client and grepped each for a caller:
> `reviewJob` had none, and neither did `createLabel`, `deleteTask`, `taskProgress` or
> `frameInfo`. `reviewJob` was wired that iteration; the other four are still uncalled — a
> label cannot be added to a project after it is created, and a task cannot be deleted, from
> the application. Iteration 46 came from the same sweep, one level out: `api.members` did
> not exist *to* be called, which is the same gap wearing a different hat. **A client method
> nobody calls is this
> codebase's most reliable tell** for a feature that is complete everywhere except where a
> person could reach it: it is how the auto-annotate panel, the suggestion review and the
> issues panel were each found missing, and now this.

*This section previously listed an auto-annotate editor surface, the mask brush, a
ground-truth UI, dragging a keyframe along its lane, an issues panel and a browser harness
in CI as upcoming work. All of that has since shipped — see* Completed *— and the section
had drifted into repeating some of it as "next" long after it was done. Trimmed to what is
actually outstanding.*

> **A note for whoever writes the next browser harness.** The twenty-two in `scripts/` have
> now found every defect the unit suites missed, most recently a Submit button that stayed
> enabled on an accepted job and failed silently, and a resumable upload that restarted from
> zero instead of resuming. **Make each new check fail before you trust it passing**:
> iteration 46's "the pickers do not navigate" check passed happily with the pickers nested
> inside the row's link, because `select_option` dispatches a change without a click, and
> iteration 47's queue summary read the right number under a deliberately wrong query
> because the fixture happened to hold exactly one submitted job either way, and
> iteration 48's "the new label is offered in the editor" check waited for that very label
> and then asserted it was there — which can time out but can never fail. A check that
> cannot fail is worse than no check, because it is also a claim; the fixture is part of the
> check, and so is what you waited for.
> They need a packaged sidecar and a Chromium, so nightly rather
> than per-push (see
> `.github/workflows/browser.yml`), and the sidecar **embeds `web/dist`** — a run needs
> `npm --prefix web run build` *and* a sidecar rebuild, or it silently tests the previous
> frontend.

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
that keep a rotated box's angle**. CVAT XML also carries the cuboid shape, using CVAT's own
16 named attributes (`xtl1,ytl1,...,ybr2`) rather than a generic points list — the other ten
formats have no box-with-depth primitive and correctly declare it unsupported. Round-tripped
through their own readers in `tests/unit/test_formats_robotics.py` and
`test_formats_yolo_variants.py`.

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

**Resumable uploads, end to end** — `POST /tasks/{id}/uploads` declares a filename and
size, `PATCH /tasks/{id}/uploads/{upload_id}` appends a chunk at a stated offset (a mismatch
is a 409 naming the real one), `GET` reports the current offset, and
`POST .../complete` finalises through the same dedupe/probe path (`_ingest_bytes`) a direct
upload uses. `Storage.append` and the `UploadSession` model/migration/schemas had existed
since early in the project for exactly this and were unused; only the service functions and
routes were missing. Reachable from the SDK (`CurveVision.upload_resumable`) and the CLI
(`curvevision task upload-resumable`), which read a local file in fixed-size pieces and can
resume a session by id after a crash, and from a new "Upload media" panel on the task page —
visible in both shapes, unlike the desktop-only local-import panel — which sends a file at
or over 20 MB in chunks (with a progress bar) and everything smaller in the single-request
batch path that already existed. Resuming across a page reload works by re-selecting the
same file: its name and size key a remembered session id in `localStorage`. Driven end to
end in a browser, including a chunk deliberately dropped mid-transfer and a resume proven to
continue from the server's real offset rather than restart.

**Reviewing a job, from the application** — the editor's rail carries the reviewer's
decision on a submitted job: accept it, or send it back with a reason that becomes an issue
on the job. The controls render only for a `submitted` job, because that is the only state
the endpoint accepts. Sending back requires a reason in the UI while the API still permits a
commentless rejection — a deliberate asymmetry, since a script that has said why elsewhere
is not the case the requirement protects against. No client-side permission check: whether
this caller may review is the policy engine's answer, so a 403 arrives as the server's own
message, the same way the quality and ground-truth panels behave. Driven end to end in a
browser through submit → send back → read the reason as an issue → resubmit → accept.

**Assigning a job, from the application** — every job row on the task page carries an
annotator picker and a reviewer picker, filled from `GET /organizations/{id}/members`, and
either can be put back to Unassigned. Who is offered differs by field: anyone who can hold
work for the annotator, `reviewer` and above for the reviewer, ordered by the label they are
shown under. `assignment.ts` holds that rule and is pure and separately tested, including the
one that matters most to the server: the empty option becomes an explicit `null` in the
patch, never a dropped key, which is the distinction `update_job` now reads through
`model_fields_set`. No client-side permission check — a viewer sees the pickers and gets the
policy engine's 403 rendered beside the row. The pickers are **siblings** of the row's link,
not children, so using one does not open the editor; that is asserted by a harness check
which had to be rewritten before it could fail.

**A changeable label schema** — the project page's Label schema panel adds a label,
renames or recolours one, and removes one. Who may is the policy engine's answer, so the controls render and a 403 arrives
as the server's message. A new label appends rather than sorting into the middle of the
schema, a proposed colour avoids the ones already in use, and a name is trimmed and its
internal whitespace collapsed before it is sent, because the server's duplicate check is a
string comparison. A rename sends the **whole** label back, rebuilt by `labelToPayload`
from the one the server reported, because `PUT` is a replace: the attributes, the position
and the shape restriction all travel with it, and a label cannot be sent back exactly as it
arrived either — `LabelIn` is strict and rejects the `project_id` and `parent_id` that
`LabelOut` carries. Deleting a label that annotations reference is refused by the server and
the refusal is what the panel shows — the reason a delete control can be offered at all.
`labelSchema.ts` is pure and separately tested; the three label routes, which had no tests
at all, have fifteen. The same form edits the label's **attributes** — see the next entry.

**Editing a label's attributes** — the label form on the project page adds an attribute,
renames one, gives a select or radio more options, sets a default, and removes one. The
server refuses any edit that would leave a recorded value unable to be saved again
(`_refuse_stranding_values` in `services/projects.py`): an existing attribute's type and
`mutable` flag are fixed and its options may grow but not shrink, always, which is CVAT's
rule; a rename or removal is refused while any annotation of the label records a value under
that name, counted across shapes, tracks, track keyframes and tags; and a new or
newly-required attribute without a default is refused while annotations lack a value.
Duplicate attribute names, invalid defaults, and the same-name replacement and name trade
that used to be 500s are handled. The form shows a saved attribute's fixed controls as
fixed and catches a blank or repeated name, a select without options and a non-numeric
number default before Save, because the server's answer to those is a 422 that renders only
as "One or more fields are invalid". It does not offer `required` (see *Next best action*).
`attributeSchema.ts` is pure and separately tested (18 tests); `TestEditingAttributes` has
20 server tests, also run against a real PostgreSQL 16; `scripts/verify_attribute_editor.py`
drives the form and then the editor, dragging and saving the annotator's box after the edits.

**A reviewer's queue** — `GET /jobs` takes `reviewing=true` beside `mine=true`, and a
`reviewer_id` parameter mirroring the `assignee_id` it always accepted. They are two
questions, not one filter with two spellings, and narrow independently. The My work page
asks both: what is waiting on this reviewer (submitted, and named to them), what is named
to them but not submitted yet, and what they are annotating. `myWork.ts` is pure and
separately tested and imports `isReviewable` from the editor's `review.ts` rather than
restating which states can be reviewed. Rows carry the **task name**, from a new
`JobListing` schema that the cross-project listings return and no other job route does —
`Job.task` is a lazy relationship, so putting it on `JobOut` would make every other route
load it. Indexed by `ix_job_reviewer_state`, which is `ix_job_assignee_state` with one
column changed, and that migration is the first in this repository run in both directions
by a test.

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
| Choosing individual files (desktop) | Complete and measured in a browser: `choose_files` now has a caller, one `local-import` call per chosen file, a failed file reported in `skipped` without aborting the others, and the results of a whole batch merged into one summary. |
| Cuboid (2D wireframe box) | Complete and measured in a browser: a two-stage tool (drag the front face, then move and click to set the depth), a wireframe renderer, and CVAT XML export/import using CVAT's own real attribute names. The backend needed no new code at all — `ShapeType.CUBOID`, its minimum-points entry, its IoU comparison and its track interpolation were already there, unused. **Not** the 3D/point-cloud kind — see `docs/ROADMAP.md`'s honestly-unchanged limitation on that. |
| Resumable uploads | Complete and tested end to end — API, SDK, CLI and the web upload panel: create a session, `PATCH` chunks at a stated offset, read the current offset back, complete, resume by id after a crash or a page reload. Driven in a browser through a deliberately dropped chunk and a real resume. Nothing outstanding. |
| Label schema | Complete, driven in a browser: a label is added, renamed, recoloured and removed, and its **attributes** are added, renamed, extended and removed from the same form — with any edit that would strand a value already recorded refused by the server. What is *not* built is setting an attribute's **value** on a shape from the editor; see *Next best action*. |
| Job review | Complete end to end and driven in a browser, all three parts: the **assignment** (an annotator picker and a reviewer picker on each job row of the task page, either clearable back to Unassigned), the **queue** (`GET /jobs?reviewing=true`, split on the My work page into what can be reviewed now and what is merely named to you), and the **decision** (accept or send back with a required reason, from the editor's rail). Nothing outstanding. |

*This table went stale once — it still listed the open-folder flow and chunked delivery as
unbuilt several iterations after both shipped, because the narrative sections above were
being updated and this one was not. Check it against* Completed *before trusting it.*

---

## Remaining high-priority work

1. **No model ships, so auto-annotate cannot be tried.** ADR 0005 accepted this
   deliberately ("No out-of-the-box models… a genuine onboarding cost") and planned
   reference servers in separate repositories. The contract is now ready for one.
2. **Signed installers in CI** — *blocked outside the repository*: one runner per platform
   (PyInstaller does not cross-compile) and signing certificates the project does not have.

---

## Verification performed

Iteration 52, every command run in this container:

* `./scripts/check.sh` on arrival: **eleven of twelve** — `mypy (server)` failed on an
  untouched tree (`"Base" has no attribute "confidence"`, `services/annotations.py`, mypy
  2.3.1). After the fix and all of this iteration: **all twelve pass** — 582 server tests,
  15 SDK, 628 web, ruff/format/mypy/eslint/tsc clean, notices ok.
* `pytest server/tests/api/test_label_schema.py`: 35 pass. 18 of the 20 in
  `TestEditingAttributes` were written before the service change and 16 of those failed
  against it (one only because of a bug in the test's own helper, fixed); the defect was
  also reproduced end to end first (a `PUT` removing `colour` → 200, then the
  editor-shaped save of the untouched shape → `422 Unknown attribute(s) for this label:
  colour`).
* The same two test classes (`TestEditingAttributes`, `TestEditingALabel`), 27 tests, run
  against a **real PostgreSQL 16** with JSONB columns via a throwaway fixture override
  (not committed; CI's Postgres job only runs migrations). Confirmed the rows landed in
  PostgreSQL.
* Sabotage, server: removing the temporary-name step makes the name-trade test fail with
  `IntegrityError`. Restored.
* `npm --prefix web run build` (exit 0) → `python desktop/sidecar/build.py --skip-tests`
  (exit 0) → `scripts/verify_attribute_editor.py`: 16 checks pass. Two sabotage rebuilds:
  **UI** (save ignores the drafts, type picker enabled) failed 6 checks; **server** (the
  refusal disabled) failed 8, including the editor's save of the box being refused.
  Restored; final bundle `index-B7-P7J9E.js`, identical to the server-sabotage build whose
  web source was already the restored one.
* `scripts/verify_label_schema.py`, which drives the same edit form: all 17 checks pass.

## Bugs fixed

* **A label edit stranded recorded attribute values** (iteration 52). Root cause: values
  keyed by attribute name, undeclared keys rejected on save, and `update_label` checking
  nothing. Verified by the reproduction above and `TestEditingAttributes`.
* **Duplicate attribute names, a same-name replacement, and two attributes trading names
  were 500s** from `uq_attribute_name` (iteration 52). `LabelIn` rejects duplicates; removals
  and renames are flushed first, renames via a temporary name. The trade test bites.
* **An invalid default was accepted and written, unchecked, into annotations** (iteration
  52). `AttributeIn` rejects a select default outside its options, a checkbox default other
  than true/false, and a non-numeric number default.
* **`mypy (server)` failed under mypy 2** (iteration 52). A cast in `decide_suggestions`.

## Known issues

* **No screen sets an attribute's value**, and a `required` attribute with no default makes
  its label undrawable in the editor — *Next best action*.
* **A default is recorded as a string.** `validate_attributes` fills a missing value with
  `definition.default_value` uncoerced, so a checkbox default lands as `"false"` while an
  explicit value lands as `false`; the next save of that annotation coerces it. Found by
  reading, not by a failing test; harmless to saving, visible in exports.
* **A rename or removal of an attribute with a default is refused as soon as the label is
  used**, because a default is recorded on every annotation saved with the label. That is
  the stated cost of refusing rather than rewriting (see *Decisions*), and the form says so.
* **The PostgreSQL run of the attribute tests is manual.** CI's Postgres job only migrates;
  the whole suite runs on SQLite.

## Tried and rejected

* **Asking the database whether a value is recorded, by JSON path.** SQLAlchemy's
  `attributes[name].as_string()` is portable in form, but its SQLite rendering (`$."name"`)
  does not escape a `"` in the key and attribute names are free text; a dialect-specific
  expression would put backend knowledge into `services/`. Read in Python, streamed, and only
  for an edit that removes, renames or newly requires something.
* **A type annotation on the `(Shape, Track, Tag)` tuple** for the mypy 2 failure. Does
  nothing: the join to `Base` happens inside `select()`'s overloads.
* **Pinning `mypy<2`.** Would only defer the same error.

## Decisions

* **Refuse, do not rewrite, when a rename or removal would strand values** (iteration 52).
  Rewriting the stored keys is lossless for a rename and is what CVAT's id-keyed storage
  gives it for free — but it changes annotations behind an open editor's back, so it would
  also have to bump `annotation_version` on every affected job to push open editors into a
  409 and a reload. Worth doing if a refused rename turns out to hurt; not worth doing
  without that evidence. The handoff had flagged this as a product decision to state rather
  than make quietly; it is stated here, in the code and in the ROADMAP.
* **Type and `mutable` are fixed for a saved attribute even when nothing is recorded**
  (CVAT's rule), so the rule a person meets does not depend on data they cannot see. Remove
  and re-add is the way to change them, and works whenever nothing is recorded.
* **The attribute form checks names, options and number defaults in the browser**, unlike
  label names. A label's duplicate is a comparison with other rows and the server's 409
  message is readable; these are knowable from the form alone and the server's 422 renders
  as "One or more fields are invalid".

---

## Last iteration

### 52 — a label's attributes can be edited, without stranding what is recorded

Named by this file as next, with the one product question it said should be decided in the
open: what happens to values recorded under an attribute being removed or renamed. Reading
the code first turned the question into a defect. The `PUT` already accepted exactly those
edits — nobody had a form to send one, but the SDK and curl did — and because values are
keyed by name and undeclared keys are refused on save, the failure surfaced one step later,
in somebody else's hands: the annotator's autosave of a box they had only moved. Reproduced
that before changing anything.

**CVAT read first, as `AGENTS.md` now asks.** Its label form and
`LabelSerializer._update_attribute` fix an existing attribute's type and `mutable` flag and
let a select's options grow but not shrink — enforced on the server, not only in the UI. It
allows rename and delete because its values hang off the attribute's id. Here they hang off
the name, so those two are refused while anything is recorded under it and allowed when
nothing is. Convention only; no code was adapted, so ADR 0007's audit does not apply.

**The tests found three more 500s and a silent write** on the way (see *Bugs fixed*), and
the harness found two of its own checks unable to fail under sabotage — one depending on an
earlier step having worked, one meaningful only when the step before it succeeded. Both are
recorded in `docs/CONTRIBUTING.md` beside the earlier variants.

**What the form does not do, deliberately:** offer `required`, and set a value on a shape.
The second is the next piece of work, and the first waits for it.

Verified: see *Verification performed*.

## Iteration 51

### 51 — every timestamp is UTC, and says so

The defect iteration 50 found and deliberately left for its own diff. `DateTime(timezone=
True)` is honoured by PostgreSQL's `timestamptz` and cannot be honoured by SQLite, which has
no time-zone type: the same column handed back an aware `datetime` from one backend and a
naive one from the other.

**Two ways that escaped, and the quiet one is the one that mattered.** In Python it is loud
— comparing a naive value read from SQLite against an aware `utcnow()` raises `TypeError`,
so it announces itself. Over the wire it said nothing: the same instant came back as
`...Z` from an instance that had just been written and with **no suffix at all** once the
row had been read back, and `new Date('2026-09-21T08:29:23')` in a browser is *local* time.
The desktop shape is the SQLite one, so every time it displayed was shifted by the viewer's
own UTC offset — silently, and correctly-looking for anybody sitting in UTC.
`ProjectsPage` renders `new Date(project.created_at).toLocaleDateString()` and `issues.ts`
orders threads by comparing these as strings, which two formats break.

**`core/types.py` is where this belongs, by that file's own rule.** Its docstring already
says the decorators there "are the only place that difference [between PostgreSQL and
SQLite] is allowed to exist", and `EnumString` exists for an exactly analogous reason — a
value read back being an ordinary `str` so that `is SomeEnum.MEMBER` is silently false.
`UTCDateTime` joins them: aware UTC on the way in and on the way out, a naive value assumed
to be UTC because `utcnow` is the only thing that writes one, and an offset value converted
rather than relabelled. All fifteen `DateTime(timezone=True)` columns now use it.

**No migration, deliberately.** `load_dialect_impl` returns `DateTime(timezone=True)`, so
the DDL emitted is the DDL that was already there; nothing about the stored data changes,
only how it is read. A migration would have been a no-op with a version number.

**Confirmed both tests bite** by making `process_result_value` return the value untouched:
the two new ones in `test_timestamps.py` fail, and so do all three of iteration 50's
whole-body comparisons, which is the point — that property is now strict rather than
excluding two fields with an apology.

**No browser harness for this, and not because it is hard.** This container runs in UTC, so
a harness that rendered a date and compared it against the API would pass whether or not the
bug is present: local and UTC are the same thing here. A check that cannot fail is the trap
the last four iterations have each recorded a version of, and adding one here would have
been the most literal case of it yet. The API-level assertion — that both spellings of the
same instant carry an offset — is the one that actually distinguishes the two states.

Verified: `./scripts/check.sh` green, all twelve steps — 562 server tests (2 new in
`test_timestamps.py`), 15 SDK and 610 web unchanged. `TestAWriteAgreesWithTheNextRead` now
compares whole bodies, with nothing excluded.

## Iteration 50

### 50 — the fourth and fifth instances of a bug found by looking, not by tripping

Three iterations in a row had each turned up the same pair of defects while building a
screen: a nullable field an `is not None` test could set but never clear, and a response
carrying an eagerly-loaded relationship from before the write. Job assignment in 46, label
attributes in 49. At three, it is a pattern rather than a coincidence, and the honest next
move is to go and find the rest rather than wait for the sixth to be reported by somebody
using the product.

**The audit is small, because the surface is small.** Five relationships in the whole domain
are `lazy="selectin"` (`Project.owner`, `Task.owner`, `Task.assignee`, `Job.assignee`,
`Job.reviewer`), and every route that writes one of them and returns it is a candidate.
`PATCH /tasks/{id}` and `PATCH /projects/{id}` had both defects, on `assignee_id` and
`owner_id` — and `description` had the clearing half on each, which is the same bug wearing
plainer clothes: a task description could be written and never removed.

**And a third kind of staleness in the same sweep.** `PATCH /tasks/{id}` returns
`TaskDetail`, the same model `GET` returns, with `progress` left null — `read_task` computed
it and the update route did not. One response model with two shapes is a trap for exactly
the caller these fixes are for: a screen rendering what a write answered would blank the
progress bar it was showing a moment earlier. Both routes now build the response through one
`_task_detail`.

**The test that states the property, rather than ten tests that state instances.**
`TestAWriteAgreesWithTheNextRead` asserts that a `PATCH`'s body equals the next `GET`'s,
over the whole body rather than field by field, for a task, a project and a job. That is the
property all three defects violate, and it is the one a future route will violate too.

**It found a fourth thing, which is deliberately not fixed here.** The whole-body comparison
fails on `created_at`/`updated_at` — not because the instants differ, but because the same
instant serialises as `...Z` from a freshly-written instance and with no suffix once read
back from SQLite. That is a real defect and a nastier one than it looks (a browser reads a
suffix-less timestamp as *local* time, so the desktop shape shows every time shifted by the
viewer's offset), but it is a cross-cutting change to a column type and belongs in its own
diff rather than folded into this one. The test excludes exactly those two fields, says why
in a comment, and *Next best action* carries it as the next piece of work.

Verified: `./scripts/check.sh` green, all twelve steps — 560 server tests (10 new in
`test_partial_updates.py`), 15 SDK and 610 web (both unchanged; no client code touched).
No browser harness: nothing on any screen changed, and the three routes are API-level.

## Iteration 49

### 49 — a label can be renamed, without losing what is attached to it

The other half of iteration 48, and the half that had a stated reason for waiting. It turned
out the reason was overstated: I wrote here that a rename "is not a name box; it is an
attribute editor, and the two ship together or not at all". Not so. `PUT` is a replace, but
a payload rebuilt from the label **as the server reported it** carries the attributes
through untouched — `update_label` reuses an attribute row whose id comes back — so renaming
is safe without touching attribute editing at all. Correcting that here because the previous
entry would have sent the next session after a much larger piece of work than this needed.

**Two server defects, both found by writing the tests before the panel.**

* **A rename walked straight around the duplicate-name rule.** `create_label` has always
  refused a second label of the same name; `update_label` did not check at all, so
  `pedestrian` could simply be renamed to `car`. That does not stay cosmetic: exports key
  classes *by name* — COCO categories, a YOLO class list, this project's own class
  distribution, which is a `Record<string, number>` — so two labels called `car` merge or
  collide the moment the dataset leaves.
* **The response reported attributes it had just deleted.** `_label_out` re-reads the label
  with `selectinload` after the commit, but SQLAlchemy skips loader options for an instance
  already in the session, and `expire_on_commit=False` leaves the old collection in place.
  A `PUT` that emptied a label's attributes came back still listing them. This is the third
  appearance of this exact bug shape in four iterations (the job's `assignee`, then
  `reviewer`, now this), and the third different fix for it — `session.refresh` there,
  `populate_existing=True` here, because the read is a fresh query rather than the written
  instance.

**`LabelOut` cannot be sent back to `PUT`, which is worth knowing before writing a client.**
`LabelIn` is a `StrictModel`, so the `project_id` and `parent_id` every read carries are
rejected outright with a 422. The natural safe pattern — read it, change a field, send it
back — does not work without stripping them first, and that knowledge now lives in one
function (`labelToPayload`) with a test, rather than being rediscovered. The alternative
would have been to let the input schema ignore those fields; rejected, because this codebase
deliberately rejects unknown keys rather than dropping them, and the same reasoning applies
here.

**A fixture that made a check unable to fail, again, and caught the same way.** Sabotaging
the rename to post a partial payload correctly failed "keeps its attribute definitions" —
and left "stays where it was in the schema" **passing**, because the label under test was
`car` at position 0 and the sabotage sets position 0. The seed now puts `pedestrian` first
so the renamed label sits at position 1, and the check asserts that too, so a fixture
change that quietly reintroduces the hole fails rather than passes. That is the same lesson
as iteration 47's summary line, in a different disguise.

**And the build trap caught something real, for the first time since it was written down.**
`npm run build` failed on a `tsc -b` error in the new unit test file — which `npm run
typecheck` had not reported, since the two use different project configurations. Because
`vite build` never ran, the sidecar packaged the *previous* bundle, and the harness ran
happily against code that did not include this iteration's changes at all. Caught by
checking the build's exit status rather than trusting the command, exactly as iterations 36,
39 and 45 say to. Worth noting that the harness's failure was a Playwright timeout on a
missing element, which looks nothing like "you tested the wrong bundle".

Verified: `./scripts/check.sh` green, all twelve steps — 550 server tests (7 new in
`TestEditingALabel`), 15 SDK (unchanged), 610 web (5 new for `labelToPayload`).
`scripts/verify_label_schema.py` grew from twelve checks to seventeen and all pass against a
rebuilt sidecar; the partial-payload sabotage was confirmed to fail exactly the two rename
checks and was then restored, with the bundle hash back to `index-BaAzC0sN.js`.

## Iteration 48

### 48 — a project's label schema can be changed after the project exists

Named by this file as next, and found the same way the last four were: `api.createLabel` sat
in the web client with no caller. The consequence is about as ordinary as this product gets
— a project created with `car` and `pedestrian`, and then a van in the third photograph.
`POST`, `PUT` and `DELETE /projects/{id}/labels` have all existed since the initial schema,
the policy engine gates them at `MAINTAINER`, and the only way to reach any of them was the
SDK, the CLI or curl.

**A defect the tests found before the panel existed: a label added later sorted into the
middle.** The listing orders by `(position, name)` and every label sent without a position
took 0, so `aardvark` added to a `car`/`pedestrian` schema came back *first*. That is not
cosmetic: the schema's order is the order the editor's label picker shows and the order its
number-key shortcuts run in, so adding a class silently renumbered the shortcuts an
annotator had learned. The route now counts the project's existing top-level labels and
passes that as the default position — `create_label` already took a `default_position`
parameter for exactly this, used when a project is created and by nothing else. An explicit
position is still honoured, which a test pins.

**Delete is offered because the server already made it safe.** `delete_label` counts the
shapes, tracks and tags referencing the label and refuses with a 409 rather than cascading,
which its own docstring has always said. That is what turns "delete a label" from a
destructive button into an ordinary one: the panel shows the server's own refusal, and the
harness checks that both the label and the annotation are still there afterwards. A
courtesy count beside each label comes from the class distribution the statistics panel
already fetched, so the usual case is not "press delete, read an error".

**What is deliberately not built, and why.** Editing a label in place. `PUT` replaces a
label's attributes wholesale — `update_label` reuses an existing attribute row only when the
client sends its id back — so a rename box posting `{name, color}` would delete every
attribute definition on that label and the schema validating the values already stored on
annotations with it. That needs an attribute editor, which is its own piece of work, and it
is now *Next best action* rather than a quiet gap.

**No duplicate check in the browser, on purpose.** The server's comparison is a
case-sensitive string match in one place; a second copy here would be a second thing to keep
true, and it would be the one that was wrong. The panel sends the name and renders "A label
named 'car' already exists in this project". What the pure module *does* do is trim the name
and collapse its internal whitespace, because "school  bus" and "school bus" are the same
class to everybody except a string comparison — and a string comparison is what decides
whether it is a duplicate.

**A third variant of the same harness mistake, in one iteration's own draft.** Iteration 46
learned that a check has to be made to fail; 47 added that the fixture is part of the check;
this one adds **do not wait for the thing you are about to assert**. The first draft waited
for the new label's name to appear in the editor and then checked that it had — which can
time out, but can never print a FAIL. Rewritten to wait for the label list as a whole and
assert on that label's own id. The delete sabotage found a second, related one: reading
`[data-label-error]` with `inner_text` on a page where nothing rendered it killed the run
with a Playwright timeout instead of reporting the two failures, so a `text_of` helper now
returns `""` for an element that is not there. A check looking for something *missing* has
to read defensively.

**Both sabotages confirmed to bite.** Reverting the position default put `van` back in the
middle and failed exactly that check; deleting the error banner failed exactly the two
checks about the server's refusal reaching the screen, and nothing else. Restored, rebuilt,
and the bundle hash came back byte-identical (`index-Cg6bXkv6.js`).

Verified: `./scripts/check.sh` green, all twelve steps — 543 server tests (8 new in
`test_label_schema.py`), 15 SDK (unchanged), 605 web (12 new in `labelSchema.test.ts`).
`scripts/verify_label_schema.py`'s twelve checks pass against a rebuilt sidecar, and it is
registered in `.github/workflows/browser.yml` and `docs/CONTRIBUTING.md`. No other harness
drives the project page, so there was nothing adjacent to re-run for this one.

## Iteration 47

### 47 — a reviewer can ask what is waiting for them

Straight on from iteration 46, and named by this file as next: the assignment existed, the
decision existed, and between them there was no way in. `GET /jobs?mine=true` has filtered
on `assignee_id` alone since early in the project, so a reviewer with four jobs named to
them could not ask for them — they opened each task in turn and read its job list, which
works for a demo and not for a person with a day's worth of review.

**Two questions, not one filter with two spellings.** `reviewing=true` sits beside
`mine=true` and narrows independently, so asking both gives the jobs where the caller is on
*both* ends of the loop rather than either. That is the reading somebody will expect from
two filters, and it makes `reviewing=true&state=submitted` — which is what a queue actually
is — fall out of the existing `state` parameter instead of needing a special case. A
`reviewer_id` parameter mirrors the `assignee_id` the listing has always accepted, for the
manager asking what one person is holding.

**The endpoint had no test at all — not one.** Not for `mine`, which the My work page in the
navigation bar has always run on every visit, and not for the membership subquery that
decides whose work a caller can see. Five tests now cover it, and writing them turned up the
trap this file keeps recording: **two of the five passed before the feature existed.**
FastAPI ignores an unknown query parameter, so `reviewing=true` was silently dropped and the
assertions happened to hold anyway — one fixture had a single job, so "the list is exactly
this job" was true of the unfiltered listing too. Both were rewritten with a control job
that makes the unfiltered answer different from the filtered one. A test that passes before
its feature exists is not a weak test; it is not a test.

**The queue names the task, which the listing had loaded and never returned.** `GET /jobs`
has had `selectinload(Job.task)` since it was written, for a field nothing read. Across
every project, "Job #2 · frames 0–1" identifies a job to the server and to nobody else. A
new `JobListing` schema carries `task_name` and `project_id` on the cross-project listing
only: `Job.task` is an ordinary lazy relationship, so putting those on `JobOut` itself would
oblige every other job route to start loading it for a field they have no use for.

**Split on what can be acted on, not on everything named to you.** Only a `submitted` job
can be reviewed — the server's rule, and `review.ts`'s `isReviewable`, imported rather than
restated so the two cannot drift. `myWork.ts` partitions on it, and the summary line counts
only the actionable half: a reviewer holding four half-drawn jobs has nothing to do today,
and "4 waiting" would send them looking for work that does not exist. The other half is
still listed, because "nothing is named to you" and "nothing is ready yet" are different
things to tell somebody, and an empty panel says the first when it means the second.

**A migration, and the first one in this repository ever run backwards.**
`ix_job_reviewer_state` is `ix_job_assignee_state` with one column changed, since the queue
is the same query shape as the annotator's landing page and deserves the same index.
`docs/CONTRIBUTING.md` has always asked for migrations "tested in both directions" and
nothing in the suite had ever moved one down — the whole suite only migrated a fresh
database up to head. `test_the_newest_migration_runs_in_both_directions` drives the head
revision down and up again on a real SQLite file, and confirmed it bites by stubbing the
`downgrade` body and watching it fail. That needed `desktop.migrate` split into
`alembic_config` plus a one-line upgrade, so a test can drive the same configuration the
application uses rather than a copy of it.

**The harness sabotage is the one worth remembering.** Pointing the page's review query at
`mine` instead of `reviewing` failed four checks — and left the summary check *passing*,
reading "1 job waiting on you" exactly as expected, because the fixture happened to contain
exactly one submitted job either way. A second control job fixes it: under the sabotage the
page now says "2" and the check fails. Iteration 46's lesson was that a check has to be made
to fail before it can be believed; this one adds that the **fixture** is part of the check,
not scenery around it. The sabotage also crashed the harness on a missing click target,
burying four real failures under a Playwright traceback, so the run now bails out cleanly
with its failure list when the queue is already wrong.

Verified: `./scripts/check.sh` green, all twelve steps — 535 server tests (6 new: five for
the listing, one for the migration), 15 SDK (unchanged), 593 web (9 new in `myWork.test.ts`).
`scripts/verify_review_queue.py`'s twelve checks pass against a rebuilt sidecar, and it is
registered in `.github/workflows/browser.yml` and `docs/CONTRIBUTING.md`. The sabotage above
was restored and the rebuilt bundle hash came back byte-identical (`index-CrOWMP8i.js`).
`verify_job_assignment.py` and `verify_job_review.py` re-run and pass.

## Earlier iterations

Iterations **1–46** are in [`docs/iterations/ARCHIVE.md`](./docs/iterations/ARCHIVE.md) —
moved there so that orienting costs a few hundred lines rather than four thousand. Read them
when `git log` points you at an iteration number, or when you are about to build something
and want to know whether it was already tried and rejected. The lessons from them that are
still load-bearing are restated above, in *Next best action* and in
[`docs/CONTRIBUTING.md`](./docs/CONTRIBUTING.md#browser-harnesses); the archive is the
reasoning, not the rules.

**When this file's recent window grows past six iterations, move the oldest to the top of
the archive** rather than deleting it.
