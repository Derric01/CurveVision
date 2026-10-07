# Handoff

> **What this file is.** The current, honest state of CurveVision, written for whoever picks
> it up next — a different session, possibly a different agent entirely. Read
> [`AGENTS.md`](./AGENTS.md) first; it is the working contract. Update this file after every
> iteration, including the ones that mostly failed.
>
> **Last updated:** 2026-10-07 (iteration 57) · branch `claude/start-work-yx080h` · PRs
> [#1](https://github.com/Derric01/CurveVision/pull/1)–[#26](https://github.com/Derric01/CurveVision/pull/26)
> **all merged**, #26 carrying iteration 55. Iterations 56 and 57 are pushed to the branch,
> unmerged, with no PR opened yet.
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
> concrete costs, in rough order: re-reading this file (iterations 1–51 now live in
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

**The tree is green.** `./scripts/check.sh` passes all twelve steps: 588 server tests, 15
SDK, 686 web, and `scripts/` is lint-clean under its own `ruff.toml`. Twenty-seven browser
harnesses drive the packaged desktop application in a real Chromium, nightly and on every
push to `main`. (Iteration 52 arrived to a red tree — mypy 2 — and fixed it first.)

**One object the server refuses no longer fails every save after it** (iteration 55). A
save is one batch and one transaction, refused whole, and autosave put a refused batch back
to retry — so a box of a label with a `required` attribute nobody had filled in failed its
save, and **every save after it in the session failed with it**, whatever was drawn.
Reproduced in a browser first: a `car` refused for its `plate`, a `sign` drawn afterwards,
and the server holding neither. The server now names the entry a refusal came from, in the
`errors[].location` convention request validation already used (`["body",
"created_shapes", 1]`). Autosave sets that one object aside with the server's reason, saves
the rest at once, keeps it on the canvas across the reload that follows (it is on no server,
so the reload used to wipe it), and sends it again as soon as it is edited. A banner names
it, the object list marks it, the panel marks the missing value, and Submit waits. A new
object starts with its label's defaults, typed, and a required checkbox unticked — CVAT's
`appendDefaultAttributes`. And the new harness surfaced a second road to the same
poisoning: a box deleted between its save and the reload that follows went out under its
**local** id, which the server cannot parse — a 422 naming no entry, every save after it.
It now goes out under the id its save returned.

**Work that never reached the server is offered back when the job is opened again**
(iteration 56). Autosave had written a copy of unsaved work to IndexedDB on every edit
since early in the project, and nothing ever read it back, while the roadmap called crash
recovery done — found in iteration 55. Now the copy holds everything the server has not
confirmed (what is queued, what a request is carrying, what the server refused), and
opening the job offers back whatever of it the server does not already have: a notice with
Restore and Discard, and Submit waits on the answer. A drawn object is recognised on the
server by its client id, so work whose save landed although the browser never heard is not
offered, or saved, twice; and restoring never overwrites what the session has changed since.
**In the desktop shape it could not have worked at all**: the local server took a new port
every launch, the page's origin includes the port, and IndexedDB is per origin — so every
launch began with an empty one. The server now takes the port it had last time when that is
free (`desktop.port_for`). Driven in a browser through a closed page, a dropped connection,
a save that landed unheard and a killed-and-relaunched server; not yet in the Tauri shell's
own webview.

**An annotator can set an object's attribute values from the editor** (iteration 54). The
right rail shows an Attributes panel for the one selected object — a control per attribute
by type — and every change is an undoable engine edit saved by autosave. On a tracked object
a mutable value goes on the keyframe at that frame and any other on the track, and the
editor now *shows* a tracked object's values as the server exports them (the track's merged
with those held since the last keyframe; it showed the track's alone). A keyframe added
with `K` or by a drag starts from the values in force instead of resetting them.

**Editing a tracked object on the canvas no longer breaks saving, and an undone deletion
stays undone.** Both were silent data loss on the editor's hottest path, found in iteration
53 while reading the autosave code for the attribute work. A tracked object is drawn under
its **track's** id, and autosave sent a drag of one as a *shape* update: the server answered
404, the whole batch failed, and autosave — which puts a failed batch back to retry — carried
the bad entry into every later save, so **nothing drawn after touching a tracked box reached
the server**. Deleting one sent the track id as a shape, which deleted nothing. Now a tracked
object moved on a frame becomes a keyframe there (CVAT's `Track.savePoints` behaviour), a
relabel applies to the whole track, and Delete deletes the track, all in the same autosave
batch. Separately, **deleting an object and pressing Ctrl+Z before the next autosave still
deleted it on the server** — the undo arrived as an update with the deletion still queued,
and deletions are applied last. An update now cancels a queued deletion. Both reproduced in a
real browser first.

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
recorded under the name — which keeps a schema correctable before it is used. An
attribute's **value** is set from the editor (iteration 54, above).

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

**Make a duplicate `client_id` a named refusal, not a 500** (found in iteration 56). The server
keeps one `client_id` per job (`uq_shape_client_id`) and does not check for it, so a create
resent after its first attempt landed is an `IntegrityError`. The editor cannot reach it —
a resend carries a stale `annotation_version` and gets a 409 first, and a restore skips what
the server has — but `annotation_version` is optional, so a script that omits it and retries
can. Found by reading, not reproduced. A named 422 through `_entry`, or treating it as the
retry it is and answering with the existing id, are the two shapes of fix.

*Offering `required` on the label form was iteration 57; crash recovery, 56.*

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
> `frameInfo`. `reviewJob` was wired that iteration and `createLabel` in iteration 48; the
> other three are still uncalled (checked again in iteration 55) — a task cannot be deleted
> from the application. Iteration 46 came from the same sweep, one level out: `api.members` did
> not exist *to* be called, which is the same gap wearing a different hat. **A client method
> nobody calls is this
> codebase's most reliable tell** for a feature that is complete everywhere except where a
> person could reach it: it is how the auto-annotate panel, the suggestion review and the
> issues panel were each found missing, and now this.
> Iteration 55 found the crash-recovery gap the same way, one level down: not an API
> method but an exported hook helper, `loadDraft`, with no caller.

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
first. Autosave with 409 surfaced as a conflict, and a copy of unsaved work in IndexedDB
that is offered back — see the crash recovery entry below. *(This line claimed the buffer
before iteration 56, when nothing read it back.)*

**Desktop** — `curvevision.desktop` resolves the per-OS app data directory, migrates SQLite
with Alembic (not `create_all`), provisions one local account and workspace on first launch,
mints a fresh API token each launch while revoking the previous one, binds `127.0.0.1` on the
port it had last launch when that is free (an OS-assigned one when not), and prints one line
of JSON for the shell. PyInstaller packaging
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
as "One or more fields are invalid". It offers `required` since iteration 57, never shown
fixed; the server's refusal of a requirement some annotation cannot meet is what is shown.
`attributeSchema.ts` is pure and separately tested (18 tests); `TestEditingAttributes` has
20 server tests, also run against a real PostgreSQL 16; `scripts/verify_attribute_editor.py`
drives the form and then the editor, dragging and saving the annotator's box after the edits.

**Setting attribute values in the editor** — `AttributesPanel.tsx` on the right rail, for
the one selected object (read from the engine's scene, so a box drawn a moment ago has one
before any save): checkbox, select, radio, text and number controls; a blank text or number
clears the value rather than storing `""`, and a number mid-typing is not sent until it
parses (`attributeValues.ts`, pure and tested). Changes go through
`engine.setSelectionAttribute`, undoable, coalesced per attribute so typing is one undo
step, skipping locked labels. Tracked objects: `applyCanvasEdits` splits values by the
label's mutable set — per-frame on the keyframe, the rest on the track — and
`frameAnnotations` shows `attributesAt(track, frame)`. `scripts/verify_attribute_values.py`
(8 checks).

**Crash recovery** — `drafts.ts` keeps the copy and decides what comes back; the hook drives
it. The copy (`draftOf`) is the batch in flight, the one queued behind it and the refused
objects, with deletions under server ids (a never-confirmed object's under its client id),
stamped with the `annotation_version` it was made against, rewritten 250 ms after an edit
and at once on each save's answer, and deleted when nothing is left. On opening a job,
`loadDraft` → `readDraft` (which drops anything malformed) → `planRestore` against the
server's document: a created object whose client id the server has is skipped, an update or
deletion of something the server no longer has is dropped, and anything the session has
touched is left alone. Only a non-empty plan is offered; the copy is not rewritten until
Restore or Discard, and Submit waits. Restore queues the plan, lays it on the canvas even if
saves are failing (`unsavedOn` now includes the batch in flight; `withUnsaved` skips a
drawn object the server's frame already has by client id) and saves. Desktop:
`desktop.port_for` reuses the last port when free, so the origin and its IndexedDB survive a
relaunch. 4 server tests, 15 web; `scripts/verify_crash_recovery.py` (10 checks).

**A refused save does not poison autosave** — `write_annotations` names the entry a
refusal came from (`_entry` in `services/annotations.py`: `errors: [{"location": ["body",
"created_shapes", 1], ...}]`, documented in `docs/API.md`), for `ValidationError` and
`NotFoundError` alike; the batch is still one transaction. `refusedEntries`
(`refusals.ts`) reads that and request validation's own locations the same way. Autosave
holds a refused created shape, updated shape or tracked object (all its frames' edits) out
of the queue with the reason, tries the rest at once, and releases it into the queue on the
next edit — a never-saved one back as a creation; deleting a held never-saved object
discards it. `withUnsaved` (`adapters.ts`) lays what autosave holds — queued or refused —
over a frame when it is loaded from the server, read only then so an edit never reloads
the canvas. The editor shows a banner naming each object (and its frame, as a button, when
it is elsewhere), marks its row, marks a missing required value on its control
(`isMissing`), and disables Submit while anything is held. A new object starts with
`initialAttributes` — its label's defaults, typed, and `false` for a required checkbox
with none — laid under it in `AnnotationEngine.applyResult`. Queued shape deletions are
sent under the server id each object's first save returned (`id_map`), and dropped for an
object never saved; a failed first save of an object deleted meanwhile cancels out.
2 server tests, 18 web; `scripts/verify_save_refusals.py` (14 checks).

**Editing a tracked object on the canvas** — a drag, a vertex edit or an occlusion toggle on
a tracked object records a keyframe on that frame carrying the new geometry, and a keyframe
already there keeps its per-frame attributes (`placeKeyframe` in `keyframes.ts`); a relabel
applies to the whole track; Delete deletes the track. Autosave carries these in the same
batch and under the same `annotation_version` as shape edits, as `updated_tracks` and
`deleted_tracks`, placed on the track as the server holds it at flush time (read fresh, and
a version mismatch there is reported as the conflict a 409 would be). An edit that leaves a
tracked object exactly where the track already shows it writes nothing, so an undo — which
re-emits every object on the frame — does not litter every track on screen with keyframes.
The timeline's own keyframe writes (`K`, `O`, dragging along a lane) now build their payload
through the same `toApiTrackUpdate`, which keeps each keyframe's stored `z_order` rather than
zeroing it. And an update for an object whose deletion is still queued — an undone deletion
— cancels it, including for an object never saved at all. `keyframes.test.ts` has 9 new
tests; `scripts/verify_track_canvas_edit.py` drives all of it in a browser (12 checks).

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
| Track keyframe **editing** | Complete: `K` adds or removes a keyframe, `O` marks a departure, a marker can be dragged along its lane, and a tracked object moved on the canvas becomes a keyframe on that frame (Delete deletes the track). Verified in a browser, not only in unit tests. |
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
| Label schema | Complete, driven in a browser: a label is added, renamed, recoloured and removed, and its **attributes** are added, renamed, extended and removed from the same form — with any edit that would strand a value already recorded refused by the server. Attribute **values** are set from the editor's right rail (iteration 54), and a required one nobody has set no longer poisons autosave (iteration 55). The form offers `required` (iteration 57). |
| Autosave crash recovery | Complete in a browser (iteration 56), driven through a closed page, a dropped connection, a save that landed unheard and a killed-and-relaunched desktop server. Not yet checked in the Tauri shell's own webview, whose storage persisting across launches is the platform default. |
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

Iteration 57:

* `./scripts/check.sh` after: **all twelve pass** — 588 server, 15 SDK, 686 web (no unit
  change: `draftToPayload` already carried `required`, and was already tested for it).
* `verify_attribute_editor.py` gained step 7b. Against the build before the change, exactly
  its two new checks fail (no control to tick). Against the new build (`index-D7GUoz04.js`)
  all checks pass, as do `verify_label_schema` and `verify_save_refusals`.

Iteration 56:

* `./scripts/check.sh` on arrival (branch restarted from `origin/main` after #26 merged):
  **all twelve pass** — 584 server, 15 SDK, 671 web. After: **all twelve pass** — 588
  server (4 new, `TestTheSamePortAcrossLaunches`), 15 SDK, 686 web (15 new: 14 in
  `drafts.test.ts`, 1 in `unsaved.test.ts`).
* Server sabotage: without `SO_REUSEADDR` in the probe, exactly "a port the last run just
  let go of is reused" fails (the TIME_WAIT case); with the remembered port ignored, the
  three tests that depend on it fail. Restored.
* `scripts/verify_crash_recovery.py`, 10 checks. Against the pre-change build (saved before
  any edit, `index-C6upVjdo.js`): 8 fail — nothing is offered, and the relaunched server
  comes back on another port; the two that pass are guards against offering too much, which
  a build that offers nothing trivially meets. Against the final build
  (`index-B55s3UYP.js`): all 10 pass. One combined sabotage build (`index-O2zLKjtu.js`:
  restore without the client-id check, and the server ignoring its remembered port): exactly
  the landed-unheard check (the offer read "2 changes") and the two desktop checks fail.
* The first run on the new code failed two checks, both found and fixed rather than
  explained away: the restored box was not drawn while saves failed (`restore` saves at
  once, and `unsavedOn` did not include the batch in flight); and the relaunch took a new
  port because `process.kill()` had killed only the packaged binary's bootloader, leaving
  its server child holding the port — a harness fault, fixed with `crash()`.
* On the build before the last two small changes (`index-Dq2XEwlK.js`: restore's error
  message and the notice's grammar), every check passes in `verify_crash_recovery`,
  `verify_save_refusals`, `verify_track_canvas_edit`, `verify_attribute_values`,
  `verify_attribute_editor`, `verify_shape_frame`, `verify_keyframe_editing`,
  `verify_label_schema`, `verify_mask_brush`, `verify_skeleton_tool`, `verify_cuboid`,
  `verify_tool_sync`, `verify_job_review`, `verify_local_import` and
  `verify_resumable_upload`; on the final build, `verify_crash_recovery` and
  `verify_save_refusals` again. No `curvevision-local` left running after any of them.

Iteration 55:

* `./scripts/check.sh` on arrival (branch restarted from `origin/main` after #25 merged):
  **all twelve pass** — 582 server, 15 SDK, 653 web.
* The defect reproduced first, in a browser against the packaged pre-change build: a `car`
  box with a required `plate` → `422 Attribute 'plate' is required`; a `sign` drawn after
  it → 422 again, and again on the timer; the server held 0 shapes; the banner named the
  attribute and no object.
* Server: `pytest tests/api/test_workflow.py -k TestAnnotationEngine` — 10 pass. With
  `_entry` made a no-op, both new tests fail. Restored.
* `./scripts/check.sh` after: **all twelve pass** — 584 server (2 new), 15 SDK, 671 web
  (18 new: 3 engine, 6 `attributeValues`, 3 `refusals`, 6 `unsaved`).
* `scripts/verify_save_refusals.py`, 14 checks. Against the pre-change build (the 12 it had
  then): 10 fail — every check but "mounts" and "raises nothing". Against the final build:
  all 14 pass. Sabotage A+C, one build (`index-BfeNfpst.js`: overlay emptied, seeding
  removed): exactly "still on the canvas after that save", "after that save the bus still
  shows the route cleared" and the van check fail, plus the two fix checks that depend on
  them. Sabotage B (a held object never released): **the first attempt did not compile**
  (`web exit 1`, a narrowing to `never`) and the sidecar packaged the previous bundle —
  caught by the exit status; redone (`index-dMuKk8-_.js`): exactly the two "fixing it saves"
  checks fail. The Submit check failed on the build without the guard and passes with it;
  the deletion check failed on the build without the translation (`[150, 150] → [150, 150,
  250]`: the deleted box still there, the next never saved) and passes with it. Its first
  draft **could not fail** (it counted boxes) — see `docs/CONTRIBUTING.md`.
* `verify_track_canvas_edit.py` **failed once**, two checks with a 422 on every later save,
  in about fourteen runs this session. Twelve re-runs (six on the pre-change build, six on
  the new) did not repeat it. A scratch probe holding the editor's reads back two seconds
  reproduced the same signature every run — `deleted_shapes: ["local-…"]` → 422, every save
  after — which check 8 above now pins. That the one failure took exactly this road is
  inferred from the signature, not proven.
* After the translation fix it **failed once more, differently**: "the never-saved shape was
  lost although its deletion was undone". Cause: the harness's own premise. The 4-second
  autosave saved the box before its Delete, the reload landed before the Ctrl+Z and cleared
  the undo stack, and the deletion — now sent correctly — deleted it. (Before this
  iteration the same race poisoned every later save instead.) The step now repeats an
  attempt an autosave went out during; in the next four runs one attempt was repeated, and
  all four passed. The same race showed a real display bug, fixed: after that reload the
  deleted box reappeared until the next save, because the overlay hid it by its local id.
* `./scripts/check.sh` on the final code: all twelve pass. On the final build
  (`index-C6upVjdo.js`): `verify_save_refusals` and `verify_track_canvas_edit` (four runs)
  pass. The build before the last two fixes (`index-StK0f35Z.js`, differing only in the
  overlay's deleted ids and the notice's wording) also passed `verify_attribute_values`,
  `verify_attribute_editor`, `verify_shape_frame`, `verify_keyframe_editing`,
  `verify_label_schema`, `verify_mask_brush`, `verify_skeleton_tool`, `verify_cuboid`,
  `verify_tool_sync`, `verify_job_review`, `verify_track_timeline`, `verify_scissors` and
  `verify_view_settings`.

Iteration 54:

* `./scripts/check.sh` after the change: **all twelve pass** — 582 server, 15 SDK, 653 web
  (16 new: 4 engine, 8 `attributeValues`, 4 track-attribute tests in `keyframes.test.ts`).
  The baseline run at the start was killed with its shell and not repeated; `main` had been
  green in CI on the merge of #24.
* `scripts/verify_attribute_values.py` against the build from before the change: 6 of 8
  checks fail. After: all 8 pass. Sabotage E (`frameAnnotations` back to the track's
  attributes only): exactly "on the next frame the editor shows the value as held" fails.
  Sabotage F (autosave told nothing is mutable): exactly the two track checks fail. Each
  sabotage build's hash was checked to differ; the final bundle `index-CwZHHqck.js` matches
  the first good build.
* On the final build `verify_track_canvas_edit`, `verify_keyframe_editing`,
  `verify_shape_frame` and `verify_attribute_editor` also pass.

## Bugs fixed

* **Crash recovery did not exist, though the roadmap said Done** (iteration 56). Root
  cause: the IndexedDB copy was written and nothing read it. Now read on opening and
  offered. Verified by `verify_crash_recovery.py` before and after.
* **In the desktop shape a new port each launch hid everything the page kept** (iteration
  56) — storage is per origin, and the origin includes the port. `desktop.port_for`.
* **The copy dropped what a save was carrying** (iteration 56): rewritten from the queue
  alone, an edit during a save removed the in-flight objects from it. And it was never
  rewritten when the queue emptied, so a box drawn and then deleted stayed in it; and each
  write opened its own connection, so two in quick succession could land in either order.
  Pinned in `drafts.test.ts` (the first), by construction (the rest).

* **One object the server refused failed every save after it** (iteration 55). Root cause:
  a batch is refused whole, the refusal named no entry, and autosave put the whole batch
  back to retry, so it was refused again every time. The trigger found first was a
  `required` attribute with no default on a newly drawn box; any per-entry refusal did the
  same. Verified by `verify_save_refusals.py` before and after.
* **A box deleted between its save and the reload that follows poisoned autosave the same
  way** (iteration 55). Root cause: until the reload the canvas knows a new object by a
  local id, and the deletion was sent under it — a parse error for the whole request. Now
  sent under the id `id_map` returned. Pinned by check 8, which holds the reload back.
* **A required checkbox with no default showed unticked and was refused for having no
  value** (iteration 55): it now starts `false`. And an object drawn in the editor records
  its label's defaults typed — a checkbox default as a boolean, not the string the server
  fills in.
* **A keyframe added with `K` or by a canvas drag reset every mutable attribute value from
  that frame on** (iteration 54): it was created with `attributes: {}`, and the server
  holds a value only until the next keyframe changes it. Now it starts from the values in
  force. Pinned in `keyframes.test.ts`.
* **The editor showed a tracked object with the track's attributes only**, hiding every
  per-frame value (iteration 54). Pinned by sabotage E.
* **Editing a tracked object on the canvas poisoned autosave** (iteration 53). Root cause: a
  tracked object is drawn under its track's id and autosave only knew how to send shapes,
  so a drag became `updated_shapes` for a nonexistent shape → 404 → the batch put back and
  retried with the bad entry forever. Every later save failed. Verified by
  `verify_track_canvas_edit.py` before and after.
* **Deleting a tracked object deleted nothing** (iteration 53): the track id went out as a
  shape id. Now `deleted_tracks`.
* **Undoing a deletion before the next save lost the object anyway** (iteration 53). Root
  cause: the undo arrived as an update while the deletion stayed queued, and the server
  applies deletions last; for a never-saved object the undo was ignored outright. Verified
  for a saved shape, a never-saved shape and a track.
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

* **The Tauri webviews' storage across launches is not verified.** Recovery in the
  desktop shape rests on the webview keeping IndexedDB for an origin from one launch to the
  next — the default for WebView2, WKWebView and Tauri's WebKitGTK setup, but not run
  here, where the shell has no display. Everything up to that is verified with the packaged
  server and a persistent Chromium profile.
* **Until an offer is restored or discarded, new work is not copied**: rewriting the copy
  would replace what was found. New work still saves every four seconds; what is lost to a
  crash in that window is at most what failed to save meanwhile.
* **Two tabs on one job share one copy**, keyed by job: the later write wins, so a tab that
  crashes while another keeps working can lose its copy. Two tabs on one job also 409 each
  other at the server, so this is not a way anybody works.
* **A desktop relaunch that starts before the last server has let go of its port** takes
  another port, and so another origin, for that launch; the remembered port is kept, so the
  next launch is back on it.
* **An edit to a new object made during its first save, or before the reload after it, is
  dropped.** `record` skips an update for a `pending` object not in `created`, which is
  where a new object is while its save is in flight and until the reload swaps its server
  id in. Typing into the attribute panel right after drawing can lose keystrokes that way;
  the reload then shows the saved value. Found by reading in iteration 55; the `local` map
  added for deletions could carry the reload half, the in-flight half needs more.
* **A refusal of an entry autosave cannot set aside still puts the batch back whole** —
  anything outside `created_shapes`, `updated_shapes` and `updated_tracks`. After iteration
  55 nothing autosave sends is known to hit this, but the fallback is the old behaviour.
* **`verify_track_canvas_edit.py`'s other undo steps share the timer race** its "never
  saved" step now guards against: an autosave landing between an edit and its Ctrl+Z
  makes the undo a no-op (the reload clears the stack), and the step then fails. Seen in
  that one step this session (twice in about twenty runs); the drag-undo, saved-shape and
  track steps have the same exposure over shorter windows and were not seen to fail. The
  guard there is the pattern to copy if they are.
* **Submit can still go ahead when a refusal arrives during its own flush**: the guard reads
  what is held before Submit is pressed, and `flush` does not report what it held back.
* **A default is recorded as a string** when the *server* fills it in —
  `validate_attributes` writes `definition.default_value` uncoerced, so a checkbox default
  lands as `"false"` from the SDK, the CLI or an import, while the editor now sends it
  typed (iteration 55). The next save of that annotation coerces it. Harmless to saving,
  visible in exports.
* **A rename or removal of an attribute with a default is refused as soon as the label is
  used**, because a default is recorded on every annotation saved with the label. That is
  the stated cost of refusing rather than rewriting (see *Decisions*), and the form says so.
* **The PostgreSQL run of the attribute tests is manual.** CI's Postgres job only migrates;
  the whole suite runs on SQLite.
* **A failed flush restores the older copy of a shape over a newer edit.** When a flush
  fails, autosave puts its batch back with `Map.set`, overwriting an edit to the same shape
  made while the request was in flight — the next retry sends the older geometry. Found by
  reading, iteration 53; track edits restored in the same place do keep the newer one. Narrow
  (an edit during a failing request), not fixed.
* **An undo on an empty stack still re-emits the frame**, so Ctrl+Z after a save writes a
  no-op update of every shape on screen and bumps `annotation_version`. Harmless; found while
  tracing a harness.
* **Undoing a tracked object's drag after it has been saved is not possible**, like any undo
  after a save (the save reloads the frame, which clears the undo stack). Pre-existing, and
  the reason the suspected bug under *Tried and rejected* is not one.

## Tried and rejected

* **Restoring the copy without asking** (iteration 56). A crash and a deliberate "Leave
  site" look the same from the next launch, and the job may have moved on; an offer costs
  one click. Only the offer is automatic, and only when the server lacks something in it.
* **Keeping the copy on the server instead** (iteration 56): it would survive a desktop
  relaunch on any port, but not the case the browser copy exists for in the server shape —
  a dropped connection — and it is a table, a migration and routes. The desktop problem was
  the origin, and keeping the port fixes that at the edge.
* **Loading the desktop window from the shell's bundled frontend** for a fixed origin
  (`tauri://…`) (iteration 56): every API call would become cross-origin, with CORS and CSP
  to get right, and it changes how the page reaches the server — ADR-sized, for what one
  remembered port does.

* **Starting a required select or radio at its first option**, as the previous handoff
  suggested (iteration 55). Rejected: it records a choice nobody made, and makes `required`
  mean "defaults to the first option" — which a schema author who meant that could have
  said with a default. A checkbox is different only because unticked is what the control
  shows.
* **Checking `required` in the browser and holding the save** (iteration 55). Rejected: a
  second copy of the server's rule (`attributeValues.ts` says why it has none), and it
  would cover one refusal of several — a label moved out of the project, a shape type the
  label no longer permits, a frame outside the job. The server naming the entry covers all
  of them. The panel *marks* a missing value; it does not hold anything back.
* **Recomputing the displayed frame whenever autosave's contents change** (iteration 55).
  Rejected before building: that list feeds `setAnnotations`, which reloads the scene and
  clears the undo stack, so it would do both on every mouse move of a drag. `unsavedOn` is
  read only when the server's document or the frame changes.
* **Re-running an intermittent harness failure until it repeats** (iteration 55): twelve
  runs, no repeat. Holding the editor's reads back with `page.route` reproduced it every
  run.
* **"Undoing a deletion after it has been saved poisons autosave too"** — suspected in
  iteration 53 by reading, and not reachable: a save's reload runs `setAnnotations`, which
  clears the undo stack, so there is nothing to undo. Checked in a browser; recorded so
  nobody chases it again.
* **Asking the database whether a value is recorded, by JSON path.** SQLAlchemy's
  `attributes[name].as_string()` is portable in form, but its SQLite rendering (`$."name"`)
  does not escape a `"` in the key and attribute names are free text; a dialect-specific
  expression would put backend knowledge into `services/`. Read in Python, streamed, and only
  for an edit that removes, renames or newly requires something.
* **A type annotation on the `(Shape, Track, Tag)` tuple** for the mypy 2 failure. Does
  nothing: the join to `Base` happens inside `select()`'s overloads.
* **Pinning `mypy<2`.** Would only defer the same error.

## Decisions

* **Offer, do not put back, and put back nothing the server has** (iteration 56). The plan
  is checked against the server's document when offered and again when restored.
* **A remembered port that is busy is not forgotten** (iteration 56): the instance holding
  it is usually the first one, and its origin is the one with the copy in it.

* **A refused object is held until it is edited, not retried on every flush** (iteration
  55). Unchanged it can only be refused again, and each retry would cost the rest of the
  batch a round trip. The edit is presumed to be the fix; if it is not, the server says so
  and it is held again.
* **The server names the refused entry in `errors[].location`**, the shape request
  validation already used, rather than in a new field (iteration 55): one reader on the
  client for a malformed entry and a refused one, and nothing new in `docs/API.md` beyond
  a paragraph.
* **Submit is disabled while anything is held** (iteration 55). A reviewer would otherwise
  be handed a job without an object the annotator is still looking at.
* **Delete on a tracked object deletes the whole track** (iteration 53). A track is one
  object; "it leaves the frame here" already has its own key, `O`. Not checked against
  CVAT's source, unlike the keyframe-on-move behaviour, which is (`Track.savePoints`).
* **Tracked edits travel in the autosave batch, not through the timeline's `editTrack`
  mutation** (iteration 53). A drag emits an update per mouse move; the batch coalesces them
  and writes once, atomically with shape edits and under the same version. `editTrack`
  re-reads the version before writing, which skips conflict detection; the batch does not.
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

### 57 — the label form offers `required`

The next best action, and small: iterations 54 and 55 removed the two reasons it was held
back (no way to set a value; one refused object failing every later save), and the server
rule was already there — `_refuse_stranding_values` refuses a newly required attribute
while an annotation of the label has no value and there is no default. One checkbox per
attribute row, never shown fixed, because whether it is allowed depends on data the form
cannot see; the refusal arrives through the panel's existing error line. The harness's first
draft of the new step died on a Playwright timeout against the old build (no checkbox to
tick); it now reports a missing control as the two FAILs it should be.

Verified: see *Verification performed*.

## Iteration 56

### 56 — unsaved work survives a crash, in both shapes

The next best action from iteration 55, which had found that crash recovery was a false
Done: the copy was written on every edit and never read. Orienting turned up the reason it
could not have worked on the desktop even if it had been read. The window loads the page
from `http://127.0.0.1:<port>`, the local server took a new port every launch, and a
browser keeps IndexedDB per origin — port included — so every launch started with an empty
one. Nothing in an ADR fixes the port; `_free_port`'s objection is to a *fixed* one
colliding, which a remembered port with a fallback avoids. A stable origin was checked for
side effects first: `index.html` is served `no-cache`, assets are content-hashed, and
nothing in `localStorage` depends on starting empty.

**CVAT was looked at for the recovery itself and has none to port** — its client keeps
unsaved changes in memory and warns on leaving — so the design is this project's own: offer
rather than restore, check the offer against the server by client id, and never overwrite
what the session has changed since.

**Reproduced first, as a harness against the build from before the change**: a box drawn
while saves fail and the page closed is not offered back, and a killed-and-relaunched
server comes back on another port. The first run against the new code then failed twice —
the restored box was not drawn while saves still failed, and the relaunch took a new port.
The first was a real gap (`unsavedOn` ignored the batch in flight); the second was the
harness killing only the packaged binary's bootloader. Both are written up above, the
second in `docs/CONTRIBUTING.md`.

**Found, not fixed:** a resent create whose `client_id` the job already has is a 500 —
the editor cannot reach it, a script can. See *Next best action*.

Verified: see *Verification performed*.

## Iteration 55

### 55 — one refused object no longer fails every save after it

The next best action since iteration 54, and it was a defect before it was a feature: the
handoff inferred from the retry path that a `required` attribute would poison autosave and
said to reproduce it first. It did, in a browser, on the first try — the `car` refused, the
`sign` after it refused with it, nothing on the server. `AGENTS.md` ranks broken first.

**The fix is the server's to start.** The client could not set the bad object aside because
nothing said which one it was: `"Attribute 'plate' is required"` names an attribute, and the
same batch can hold ten boxes of that label. `write_annotations` now names the entry, in the
`errors[].location` shape request validation already used, so one client reader handles
both. CVAT was read first (`cvat-core`'s `annotations-saver.ts` and
`annotation-common.ts`): its saver fails a whole save too, and it avoids *this* trigger by
giving every attribute a default (`appendDefaultAttributes`) and having no `required` —
which is where starting a new object from its defaults came from. Convention only; no code
adapted.

**The hard part was the display, not the queue.** Setting an object aside and saving the
rest is a few lines. But a save reloads the frame from the server, and a refused new box is
on no server, so the reload that followed a partial save **wiped it off the canvas** — held,
named in a banner, and impossible to select. `withUnsaved` lays what autosave holds back
over the frame, read only when the frame is (re)loaded: on every edit, it would reload the
scene and clear the undo stack under the pointer.

**The harness found one more road to the same place.** `verify_track_canvas_edit.py` failed
once in about fourteen runs, with a 422 on every save after a certain point. It would not
repeat; holding the editor's reads back with `page.route` made it repeat every time: a box
deleted between its save and the reload is still known by its local id, and the deletion
went out under it — unparseable, so the whole request was a 422 naming nothing. Fixed with
the `id_map` every save already returns. Two harness lessons from it are in
`docs/CONTRIBUTING.md`, including a check that could not fail because it counted boxes.

**Found and not fixed: crash recovery does not exist**, though the roadmap said Done. The
draft is written on every edit and nothing reads it. Corrected in `docs/ROADMAP.md` and the
plan; it is the next best action.

Verified: see *Verification performed*.

## Iteration 54

### 54 — an annotator can set an object's attribute values

The next best action since iteration 52, unblocked by 53. Mostly assembly: the engine
already had the undoable-edit pattern (`toggleOccluded`), autosave already routed tracked
objects, and CVAT's split of per-frame versus per-track values is the server's own rule
(`interpolate_track`: values "hold until the next keyframe changes them"), so the web side
copies that rule rather than inventing one.

**Two defects found on the way**, both in the track half: the editor displayed a tracked
object with the track's attributes only, so a per-frame value was invisible; and every
keyframe added in the browser started with no attributes, silently resetting mutable values
from that frame on. See *Bugs fixed*.

**Deliberately not done:** offering `required` on the label form. A required attribute
with no default still makes a newly drawn shape's first save fail and — worse — poison
autosave; that is the new next best action.

Verified: see *Verification performed*.

## Iteration 53

### 53 — a tracked object can be moved on the canvas without breaking every save after it

Started on the handoff's next best action, the attribute value panel, and stopped before
writing any of it: reading `useAutosave` to see how a panel's edit would be saved showed that
a tracked object — drawn under its track's id — would be sent as a shape. A browser run
confirmed it was already broken without any panel: one drag of a tracked box and **every
later save in the session failed**, the rectangle drawn afterwards included. `AGENTS.md`
ranks broken above everything, and a panel built on top would have inherited the defect for
its tracked half, so this iteration fixes the ground first.

**CVAT read for the behaviour.** `cvat-core/src/annotations-objects/track.ts`,
`savePoints`: editing a tracked object's points at a frame stores a shape there, copied from
the interpolated one. `placeKeyframe` is that rule; convention only, no code adapted.

**One design choice worth knowing.** The obvious route was the timeline's existing
`editTrack` mutation. It is the wrong one for the canvas: a drag emits an update on every
mouse move, and `editTrack` writes per call and re-reads the version first, which skips
conflict detection. Autosave already coalesces per object and writes once under the version
it last read, so the tracked edits went there, into a second buffer keyed by track and frame.

**The sweep found a second defect in the same function.** Delete, then Ctrl+Z before the
autosave: the editor showed the object and the server deleted it. Confirmed in a browser,
fixed, including the never-saved case, which failed differently (the undo was ignored).

**The harness caught its author three times**, recorded in `docs/CONTRIBUTING.md`: a
companion check that passed on the bug because the step before it never saved; an "undo"
check that pressed Ctrl+Z after a save — where the stack is already empty — and was really
testing a keypress, found with a request log; and a sabotage that did not compile, so the
sidecar packaged the previous bundle and everything passed. The unchanged bundle hash gave it
away.

**What was still not done:** the attribute value panel itself — built in iteration 54.

Verified, every command run in that session:


* `./scripts/check.sh` on arrival (after restarting the branch from `origin/main`): **all
  twelve pass**. After this iteration: **all twelve pass** — 582 server, 15 SDK, 637 web
  (9 new in `keyframes.test.ts`). `main`'s CI and browser workflows were green on the merge
  of #23.
* Both defects reproduced in a real browser against the packaged app **before** any fix:
  dragging a tracked box → `PATCH` 404, "Save failed", and a rectangle drawn afterwards
  also 404 with 0 shapes on the server; Delete then Ctrl+Z → editor shows 1 object, server
  holds 0 after the save.
* `scripts/verify_track_canvas_edit.py` against the **unfixed** build: 5 of its first 8
  checks failed, one more exposed as vacuous and gated. Against the fixed build: all pass.
  Against the build with the track fix but not the undo fix: exactly the three undo checks
  failed. Sabotage C (`placeKeyframe` never reports a no-op): exactly "gives the track
  nobody moved no keyframe" failed. Sabotage D (the `discarded` bookkeeping removed):
  exactly "a shape drawn, deleted and undone before it was ever saved" failed. A harness-side
  sabotage (no Ctrl+Z) failed "undoing a drag … leaves the moved car where it was", proving
  the drag lands. **One sabotage build failed to compile** (`vite build` exit 1) and
  silently re-packaged the previous bundle; caught by the unchanged bundle hash, redone.
  Final bundle `index-C8cNIcBb.js`, the same hash as the first fixed build.
* On that final build: `verify_track_canvas_edit` (12/12), `verify_mask_brush`,
  `verify_shape_frame`, `verify_keyframe_editing`, `verify_track_timeline`,
  `verify_tool_sync`, `verify_skeleton_tool`, `verify_cuboid`, `verify_attribute_editor` —
  every check in all nine passes.

## Iteration 52

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

Verified, every command run in that session:


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

## Earlier iterations

Iterations **1–51** are in [`docs/iterations/ARCHIVE.md`](./docs/iterations/ARCHIVE.md) —
moved there so that orienting costs a few hundred lines rather than four thousand. Read them
when `git log` points you at an iteration number, or when you are about to build something
and want to know whether it was already tried and rejected. The lessons from them that are
still load-bearing are restated above, in *Next best action* and in
[`docs/CONTRIBUTING.md`](./docs/CONTRIBUTING.md#browser-harnesses); the archive is the
reasoning, not the rules.

**When this file's recent window grows past six iterations, move the oldest to the top of
the archive** rather than deleting it.
