# Handoff

> **What this file is.** The current, honest state of CurveVision, written for whoever picks
> it up next — a different session, possibly a different agent entirely. Read
> [`AGENTS.md`](./AGENTS.md) first; it is the working contract. Update this file after every
> iteration, including the ones that mostly failed.
>
> **Last updated:** 2026-11-08 (iteration 101) · branch `claude/start-work-yx080h` · PRs
> [#1](https://github.com/Derric01/CurveVision/pull/1)–[#39](https://github.com/Derric01/CurveVision/pull/39)
> **all merged**, #37 carrying iterations 84 (the rest) to 98, #38 iteration 99 and #39 iteration 100.
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
> concrete costs, in rough order: re-reading this file (iterations 1–95 now live in
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
project and label schema, add media, draw, review, export to any of fifteen formats — and
is tested. The exporters cover COCO, CVAT XML, Pascal VOC, KITTI, MOTChallenge, indexed
segmentation masks, the lossless native format, and the Ultralytics YOLO family in four
shapes (detection/segmentation, OBB, pose, classification), each declaring in its
`capabilities` exactly what it cannot carry rather than dropping it silently.

The desktop shape works: a packaged single-executable server, a Tauri shell that supervises
it, folders annotated in place without copying a byte, installers built on Linux, macOS and
Windows (unsigned), and a switch to a team's shared server. Since iteration 85 the **real**
application is driven in CI over WebDriver — which found that the shell had never granted
its commands, so the native pickers had never worked in a built app until then.

**The tree is green.** `./scripts/check.sh` passes all fifteen steps: 927 server tests (the
PostgreSQL and S3 ones skip without a server; CI provides both), 16 SDK, 775 web, and
`scripts/` is lint-clean under its own `ruff.toml`. Thirty-four browser harnesses drive the application in a real Chromium, nightly and on every push to `main` —
thirty-one against the packaged desktop server, and `verify_workspace.py` against the real
server too — and **all thirty-two pass on GitHub's runners** (iteration 70, run
38282080179: the first green `browser.yml` since 10-08). (Iteration 52 arrived to a red tree — mypy 2 — and fixed it first.)

**The nightly browser run had been red since 10-08, and hid a real defect** (iteration 70).
Every run of `browser.yml` on `main` from 10-08 failed, three or four harnesses at a time —
while this file called those harnesses the project's safety net. The cause was timing:
autosave flushes every four seconds, and on CI's slower runners it often landed before a
harness clicked Save, which was then disabled and timed out. Among those failures was a
real one: **a box drawn in the editor lost its selection when its first autosave landed**
— the reload brings it back under the server's id — so the attributes panel vanished under
whoever was filling it in, within four seconds of drawing. The selection now follows the
box to its saved id; the harnesses save through one helper that treats "autosave already
did it" as done; and the deselection is checked on purpose rather than by luck.

**A backup restores what it holds** (iteration 72): the native format's import keeps each
annotation's provenance and confidence — a pending model suggestion is still one after a
restore — and each tag's attributes, and a track exported to the native format or CVAT XML
comes back with the keyframes it was drawn with rather than one on every frame.

**A CVAT XML track is read the way CVAT reads it** (iteration 71): keyframes, with the
frames between them interpolated and `outside` as the only departure, split across jobs
the way CVAT splits one. A file listing only its keyframes used to come back empty between
them. The export now marks every gap in a track `outside`, which CVAT needs to show it
absent there. MOTChallenge and the native format keep "a gap is the object leaving", which
is what a per-frame format means by one.

**Skeleton joints stored before iteration 60 are repaired, and every joint is typed**
(iteration 69). A migration rewrites the joints that a COCO import or a model stored by
name, that a native import stored with another project's ids, or whose label had no joints
at all (it gains the joints its skeletons named), in `shapes` and `track_shapes` alike. The
API now types `elements` on the way out, so the web client's type contract holds with no
exception left. Verified on SQLite and on PostgreSQL 16, where it found what SQLite could
not (a timezone-aware timestamp written to a column declared naive).

**Skeletons can be moved and edited, and tracked ones work** (iteration 68). A skeleton's
geometry is its joints; its `points` are their flattened copy, kept for picking. Three
paths knew only `points`: **moving a skeleton or dragging one of its joints changed
nothing that was drawn or saved**; **a tracked skeleton was drawn on no frame of the
editor**, and on the server its joints froze between keyframes and jumped on the next; and
**saving any edit to a tracked skeleton deleted all its joints**, because a track update
replaces its keyframes and they were sent without them. Each joint is now interpolated on
its own, matched by label (CVAT's rule), on the server and in the editor alike.

**Ultralytics itself reads and trains on every YOLO export** (iteration 67). Installed
from PyPI here (its PyTorch index is blocked), it loaded each of the five tasks through its
own dataset classes and found what a restatement of its rules could not: **every YOLO
export's `data.yaml` failed unless training was started from inside the export** —
Ultralytics resolves `path: .` against the working directory. Fixed, and pose now writes
`flip_idx` from the joints' left/right names (without it Ultralytics turns flip
augmentation off). Every label now matches what was annotated and one epoch of each task
trains through validation; `.github/workflows/ultralytics.yml` keeps it so.

**Every exporter is held to the reader that consumes it, and a workspace can be run from
the application** (iteration 66, asked for as "finish everything", with export quality
"industry grade"). Exports were read back by Datumaro (CVAT's export library) and by
CVAT's own XML reader, and Ultralytics' loader rules were restated as tests (Ultralytics
itself needs PyTorch, which this container cannot install). They found real defects: **every
YOLO export was refused by Ultralytics** — `data.yaml` named `images/train` and the files
went to `images/default` — and **a YOLO file mixing boxes and polygons corrupted the boxes**,
because Ultralytics reads such a file as segments throughout; **CVAT XML dropped rotated
boxes and skeletons**, wrote empty tracks and a frame past the last, and lacked
`original_size`; VOC's labelmap colours, the segmentation image set and MOTChallenge's label
file were where no reader looks. All fixed, each with a test written against the reader's
rule. In the application: a **Workspace** page (add people by username or email, change
roles, remove; mint and revoke API tokens), **dataset versions** on the project page,
**renaming and deleting** projects and tasks, and images and accepted-only on export. Three
server defects came with them: **an admin could make themselves owner** and then demote the
owner; **a released dataset version kept only a hash** — exporting it read the project as
it was at export time — and now freezes its annotations; and **deleting a task never removed
its images from storage**.

**COCO exports are held to pycocotools, the reader the industry trains through**
(iteration 65, asked for as "industry grade" export quality). Round trips through this
project's own importer could not catch a convention got wrong both ways, and the reference
reader found five: **masks were left out of COCO entirely** (and COCO masks were not read on
import — they fell back to a box); a **rotated box's `bbox` was the unrotated box**, which
a detector would learn as written; a skeleton's **`bbox` stretched to (0, 0)** whenever a
joint was absent; **absent joints kept stale coordinates** instead of `0,0,0` (YOLO Pose
too); and **`num_keypoints` counted only visible joints**, so COCOeval skipped people whose
joints were all occluded. All fixed; `COCOeval` now scores an export against itself at AP
1.0 for boxes, masks and keypoints.

**Annotations made elsewhere can be imported from the task page** (iteration 64). An
*Import annotations* panel, for workspace maintainers: the formats that import, an archive,
append or replace (replace waits for a ticked warning that it deletes the task's
annotations), and a summary of what was stored and dropped. Until now `POST
/tasks/{id}/import` had no caller in the application; iteration 62 made what it stores
faithful first.

**AI-assisted annotation works with an API key** (iteration 63, asked for by the user).
The Models page opens on hosted services: Claude (through Anthropic's official SDK) and,
through one OpenAI-compatible provider, OpenAI, Google Gemini, xAI Grok, OpenRouter, Mistral,
Groq, Together and a local Ollama — or any other service at a typed base URL. Pick one,
paste the key, *Connect*, pick a model from the service's own list of models that take
images, *Add*; the editor's Auto-annotate panel then runs it on the project's own labels
and the boxes land as suggestions. [ADR 0010](./docs/adr/0010-hosted-vision-language-models.md)
amends ADR 0005 and says what this costs: **boxes from a general vision-language model are
approximate** (a first draft to review, not a trained detector), boxes only, and **frames go
to the service and its bill** — said on the form. **Not verified against any real service**:
every test and harness uses a fake of each request shape, because a real call needs a key.

**An import keeps attributes and tracks** (iteration 62). Every format's reader produced
both, and the import route threw both away: each imported shape was stored with no
attributes, and a track came back as one unrelated box per frame — while the README's
round-trip table said CVAT XML carries attributes and tracks, MOTChallenge object identity,
and the native format "everything, losslessly". Now an attribute is kept where the label
declares it and accepts the value (the rest dropped and named, as CVAT drops them), and a
track comes back as one track, absent wherever the file stops listing it. **Import is still
not reachable from the application** — that is the next best action.

**A model is added from the application, with an endpoint and a key** (iteration 61). The
editor's Auto-annotate panel told people to "register an inference endpoint" and nothing in
the application could: it took a JSON body to `POST /models`. A **Models** page now does it:
paste the endpoint and its API key, *Connect*, and CurveVision asks the endpoint what it
serves (`GET <endpoint>/models` — in the contract from the start, documented for model
authors to implement, and **called by nothing**), fills in the model's kind and labels, and
adds it; a wrong key or a dead endpoint is reported before anything is saved. The editor now
offers only the job's workspace's models (it listed every workspace's, and the server refused
the others). **What this does not change:** CurveVision ships no model and no reference
server yet, so the first model is still one somebody stands up behind the contract — the
README had claimed Triton, TorchServe and "every hosted vendor already speak" it, which was
false and is corrected.

**A skeleton that arrives from outside is stored with joints the editor can read**
(iteration 60). A joint is stored under the id of one of its label's child labels, which is
how the editor draws it and every exporter writes it. A COCO import stored each joint by
*name* instead, with no id; the native format carried the *source* project's joint ids into
whatever project it was imported into; a skeleton label the import created had no joints at
all; and a model's predicted joints were stored as the model sent them. Each joint is now
matched by name to the label's joints (`services/skeletons.py`), exports carry the names to
match by, an import creates a skeleton label with its joints and bones, and a model's
normalised joints are rescaled like its other coordinates. Two routes it went through were
500s on the way: adding a label with joints, or editing one, through `/projects/{id}/labels`.
Rows written before this held names until iteration 69's migration repaired them.

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

Honestly incomplete, and marked as such everywhere: *signed* desktop installers, blocked
outside the repository (unsigned ones are built on every platform since iteration 84), and a reference model server, which needs a repository this session
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

*Frame tags in the editor, and the editor set beside CVAT's row by row (docs/CVAT_PARITY.md), 102. Releases people can download and check, 101. Least confident first, and a fresh install that did not start, 100. Model suggestions decided one at a time, 99. Click-to-segment with an interactor model, 98. Webhooks on the Workspace page, 97; delivering for the first time, and only their own workspace's, 96. The system's file dialogs driven in the real application, 95. The native menu driven at the keyboard, 94. The Helm chart making its own bucket, 93. Desktop auto-update, wired and tested short of the maintainers' key, 92. SAML single sign-on, 91. Every additional format on the 1.0 list — LabelMe, Cityscapes, Open Images, TFRecord — each
held in CI to a reader written by other people, iterations 86–89; the Datumaro bridge, its
formats measured, 90. The desktop app on a team server, and the shell's commands granted — the
pickers had never worked in a built app — iteration 85; desktop installers on every platform, `aiosqlite` made
a real dependency, Windows shutdown fixed, MinIO replaced by Versity Gateway and the S3
backend tested for the first time, 84; the Helm chart installed into a live cluster in CI,
83; a TypeScript client, 82; OIDC sign-on, 81; a Helm chart, 80. Iterations 1–79 are in the
archive.*

**CI on GitHub**: `ci.yml` runs for PRs (and on dispatch); `desktop.yml` for PRs touching
the desktop and on tags; `browser.yml` and `ultralytics.yml` only when dispatched. All
are recorded under *Verification performed* for iteration 85's commit.

~~**Before launch: pin what an install resolves to**~~ — done in iteration 101 (`server/constraints.txt`). The server's
dependencies are lower bounds only; a fresh install picked FastAPI 0.162 and the server did
not start. A constraints or lock file — generated, checked by CI, used by the Dockerfile,
`ci.yml` and `desktop/sidecar/build.py` — so an install made on launch day is the one that
was tested, and upgrading becomes a deliberate change.

**The 1.0 list in `docs/ROADMAP.md` is done** except what needs someone else: signing
certificates, an npm token, real AI keys, an external security review. The additional
formats shipped in iterations 86–89, the Datumaro bridge in 90, SAML in 91 and the auto-update wiring in 92. What remains
with no dependency on the user, in order:

1. ~~The desktop webview's storage across launches~~ — done for Linux in iteration 85
   (check 8 of `verify_desktop_shell.py`); Windows and macOS have no WebDriver bridge.
2. ~~**The native menu**~~ and ~~**the native dialogs**~~ — driven at the keyboard since
   iterations 94–95 (`xdotool` under Xvfb). What is left of the desktop is per-platform:
   macOS's and Windows' webviews and dialogs have no WebDriver bridge, so they are covered
   by the installer builds and nothing more.
3. ~~**The Helm chart could run `storage-init`**~~ — done in iteration 93, with the
   opt-out (`storage.s3.createBucket`) it needed first.
4. ~~The additional formats on the 1.0 list~~ — done: LabelMe (86), Cityscapes (87), Open
   Images (88), TFRecord (89), each held in CI to a reader written by other people.
5. ~~**Auto-update**~~ — wired and tested with a throwaway key in iteration 92; turning it
   on is the maintainers' (their key pair in the repository's secrets, a release with
   `latest.json` — `desktop/README.md`, *Releasing an update*).

**First, if a key is available: try iteration 63 against the real services.** Add Claude,
OpenAI and one OpenRouter vision model on the Models page with real keys, run each on a few
frames of a real project, and record here what each gets right and where its boxes land.
Nothing in this repository has called a real service; the request shapes are pinned against
fakes written from each API's documentation. The likely adjustments are in
`ml/vision_llm.py` (prompt wording, the 0–1000 convention) and the `NOT_CHAT` filter in
`ml/openai_compatible.py`. This needs the user's keys — ask rather than skip it.


*The iteration-62 sweep's list of features with an API and no way in through the
application is empty since iteration 97: webhooks — which iteration 96 found had never
delivered anything — are managed from the Workspace page.*


*Iteration 61 was chosen over the action below because the user asked whether AI annotation
is "add the API key and it works". It was not even that: there was no screen to add a key
to. That is fixed; what still stands between a new user and a first prediction is a model
server to point at — the first item under* Then, still needing the user.

**Then, still needing the user:**

* **A reference model server, in its own repository — now the largest gap in "AI just
  works".** Since iteration 61, adding a model is a URL and a key; what a new user lacks is
  anything to put at that URL. A small FastAPI server that implements the contract,
  including `GET /models`, around one permissively licensed detector would make the first
  run a `docker run` and a paste. Choosing that model and creating the repository are the
  user's decisions. The open-vocabulary case is the one below.
* **A reference open-vocabulary model server, in its own repository.** The contract can
  express *find these classes*, and nothing exists to point it at — which is the onboarding
  cost ADR 0005 accepted and explicitly planned to mitigate with "documented reference
  implementations… as separate repositories rather than bundled weights". YOLO-World is
  **GPL-v3** (AILab-CVC, on MMDetection/MMYOLO; Ultralytics' own build is AGPL-3.0), so it
  must stay outside this repository — which is exactly what ADR 0005 already decided, and no
  part of it needs revisiting. A FastAPI wrapper speaking the documented contract is perhaps
  a day's work; it needs the user's go-ahead because it is a new repository this session has
  no access to create.
* **Code signing** — installers are built on every platform since iteration 84; signing them
  needs certificates the project does not have.

The 1.0 list's infrastructure items are done (iterations 76–83). What is left on it is a
Datumaro bridge and an external security review — the second is not something a session
can do for itself.

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

**Exports held to the readers that consume them** (iteration 66) — YOLO's layout matches
its `data.yaml`; a dataset with any polygon is written as segments throughout; boxes are
clipped before conversion; classification is `train/<class>/`; CVAT XML carries rotated
boxes, skeletons with their joints declared as labels, `original_size`, no empty tracks and
no frame past the last; VOC's labelmap is `r,g,b` and a rotated box is its enclosing box;
segmentation masks list their image set; MOTChallenge's labels are at `gt/labels.txt`.
`tests/unit/test_export_conformance.py`.

**A workspace run from the application** (iteration 66) — the *Workspace* page (members
and API tokens; API tokens only in the desktop shape), dataset versions on the project page
(create, release, download any version in any format, with images), renaming and deleting
projects and tasks, and images and accepted-only on the project export. `verify_workspace.py`
drives all of it in both shapes.

**Releases are reproducible** (iteration 66) — a release freezes its annotations in the
native format, checked by hash on every export; `POST /projects/{id}/versions/{id}/export`
and `export_dataset_version` in the SDK.

**COCO conformance with pycocotools** (iteration 65) — masks exported as uncompressed
column-major RLE with `iscrowd: 0`, and read back (compressed strings too); rotated boxes as
their exact polygon with the enclosing `bbox`; keypoints per spec (absent `0,0,0`,
`num_keypoints` over v > 0, box over labelled joints); YOLO Pose absent joints `0 0 0`.
`tests/unit/test_coco_reference.py` and `tests/api/test_coco_export.py`.

**Importing annotations from the task page** (iteration 64) — `AnnotationImportPanel` and
`annotationImport.ts`: importable formats from `GET /formats`, append or a confirmed replace,
and `describeImport` saying what was stored, created and dropped. Shown to maintainers and
above (`TASK`/`IMPORT` floors there). Driven in a browser (`verify_annotation_import.py`).

**Hosted AI models by API key** (iteration 63) — `ml/anthropic_provider.py` (Claude via the
`anthropic` SDK: Models API filtered to `image_input`, structured outputs where supported,
`fallbacks: "default"` on the models that take it, refusals read per frame),
`ml/openai_compatible.py` (`GET /models` + `POST /chat/completions` over `httpx`), and
`ml/vision_llm.py` (prompt, 0–1000 boxes to pixels, lenient reading, image downscaling,
four frames at a time). The Models page's *Hosted AI service* tab with ten presets. Tested
against fakes of each request shape — never a real service.

**Imports keep attributes and tracks** (iteration 62) — `services/datasets.write_import`
stores what a format read: attributes the label declares and accepts (the rest dropped and
named in `warnings`), records sharing a `track_id` as one track with `outside` keyframes
wherever the file stops listing it, split at job boundaries, and a one-frame track as a
shape. The route returns a typed `ImportResultOut` with `tracks_imported`. Round trips
through the native format, CVAT XML and MOTChallenge, and a CVAT-written file, are tested.

**Adding a model from the application** (iteration 61) — a **Models** page lists the
workspace's models and adds one from an endpoint and an API key: `POST /models/discover`
asks the endpoint's `GET /models` and the form is filled in from the answer, or typed in
when the endpoint does not implement discovery. A refused key, a dead endpoint and a
malformed answer are each a 502 saying which. The key is sent as a bearer token on every
request to the endpoint and never returned. Driven in a browser
(`verify_model_registration.py`).

**The web client's API types are checked against the server** — `scripts/api_types.py`
builds the OpenAPI document from the app itself (no server, no database) and generates
`web/src/api/schema.ts` with `openapi-typescript`; `--check` runs in `./scripts/check.sh`
and in CI's server job. `web/src/api/__tests__/contract.ts` makes `tsc` fail when a field
the hand-written `types.ts` declares for a response is missing from what the server sends
or typed differently, for 31 response types; fields the schema marks optional only because
they have a default are treated as sent, since response models serialise them. One gap is
held open by name — skeleton `elements` — with the reason beside it: rows written before
iteration 60 still hold joints by name.

**Skeletons from outside the editor are stored by joint id** (iteration 60):
`services/skeletons.py` matches each joint by name to the label's declared joints, for a
dataset import and for a model's predictions; exports carry each joint's name; an import
creates a skeleton label with its joints and bones; a normalised model's joints are
rescaled; and a joint that is not an object is a malformed response, not a 500.

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
| Webhooks | Complete since iterations 96–97 — before 96 nothing was ever sent. Signed delivery, capped exponential backoff with jitter, a retry policy that distinguishes "the receiver is struggling" from "the receiver said no"; twelve events emitted; ping and recent deliveries; a panel on the Workspace page, driven in a browser against a real receiver. |
| Keypoint (skeleton) tool | Complete and driven in a browser, through to the exported `yolo_pose` rows: joints placed in declared order, `X` skips one, `Enter` finishes early, `Alt`-click marks a joint occluded, and the bones are drawn. |
| Masks | Stored, **exported** (`cvat_xml` writes CVAT's real mask element and reads it back; `segmentation_mask` paints the pixels), **drawn** — the editor renders the pixels and picks by them — and now **paintable**: the brush creates, grows, shrinks and (by erasing every pixel) deletes one, driven end to end in a browser. Nothing outstanding. |
| Reviewing suggestions | Complete and measured in a browser: a job-wide count, accept and reject in bulk over shapes, tracks and tags, and an accepted suggestion drawn solid rather than dashed. Since iteration 99 the selected ones are accepted or rejected on their own, and *Next suggestion* walks the waiting ones frame by frame. |
| Auto-annotate | Complete for the "run over these frames" kinds and driven in a browser against a real model server: a model is **added from the Models page** with an endpoint and a key (iteration 61), and the editor offers the job's workspace's models; model picker, class box for an open-vocabulary model, the plan stated before the run, unmatched classes named before the run, predictions stored as reviewable suggestions. Interactive kinds (`interactor`, `tracker`) are listed with the reason this panel cannot drive them rather than hidden. |
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

1. **Hosted AI is unverified against the real services** (iteration 63): an API key now
   gets a model, but no request has gone to Anthropic, OpenAI or any other real endpoint.
   A trained detector for production volume still needs a model server — no reference one
   exists yet (ADR 0005's separate repositories).
2. **Code signing** — *blocked outside the repository*: installers are built on every
   platform (iteration 84) but unsigned; signing needs an Apple Developer ID and a Windows
   code-signing certificate the project does not have.
3. **Publishing** the TypeScript client to npm (an `NPM_TOKEN` secret) and a first tagged
   release whose installers are attached — both outward acts under the maintainers' names.

---

## Verification performed

Iteration 102:

* Web: lint, typecheck, 813 tests (4 new), build.
* `scripts/verify_frame_tags.py`: all 7 checks pass. A broken build that tags frame 0
  whatever the frame fails checks 4, 5 and 6.

Iteration 101:

* `./scripts/check.sh`, sixteen steps green (the pin check is new): 940 server tests, 809
  web, 16 SDK, at version 1.0.0.
* `scripts/check_constraints.py` fails when a pin is removed (`fastapi` deleted: "pins no
  version for: fastapi"); `scripts/release_check.py` passes at v1.0.0 and names all eleven
  places at v0.9.0.
* The constraints resolve, in a dry run, for the Docker image's extras and for the desktop
  set with the SDK.
* `actionlint` on `release.yml`, `desktop.yml`, `ci.yml` and `browser.yml`: clean.

Iteration 100:

* `suggestions.test.ts`, 23 tests (3 new): least sure first wherever it is in the job, then
  ascending and round again; the confidence as a person reads it.
* `scripts/verify_suggestion_review.py`, now 7 checks with five frames of differing
  confidence, all passing; the new one fails when the panel ignores the order (it went to
  the 60% one on the next frame instead of the 30% one after it). The seeding run needed
  `confidence_threshold: 0.1` — the default 0.5 drops a 30% guess before anyone sees it.
* On a fresh install (FastAPI 0.162, Starlette 1.10, Pydantic 2.14): the server failed at
  import before the fix; after it, `./scripts/check.sh` all fifteen steps green — 940
  server tests, 809 web, 16 SDK — with the generated API types unchanged.

Iteration 99:

* `suggestions.test.ts`, 20 tests (6 new): only the selected suggestions still waiting, never
  tags; the next one on or after the frame on screen, moving on from the one selected,
  wrapping round, nothing when nothing waits.
* `./scripts/check.sh` all fifteen steps green: 940 server tests, 806 web, 16 SDK.
* `scripts/verify_suggestion_review.py` in Chromium against the real server, with
  suggestions written by a real model run through the inference route: 6 checks, all
  passing — Next selects the first; Accept keeps only it, still `source="model"`; Next moves
  frame; Reject deletes only it; an unsaved drag is kept through Accept; no page errors.
  Sabotaged (the flush removed): the drag check failed, the edit lost.

Iteration 98:

* `interactorTool.test.ts`, 11 tests: clicks on and off the object become prompts in image
  pixels; the answer is drawn and Enter keeps it as a model-drawn polygon; a late answer to
  older clicks does not replace a newer one; Enter while waiting refuses; Backspace re-asks,
  and the last one clears; a failing model is reported with the clicks kept; another frame
  starts again; no model says where to add one; Escape drops an answer in flight.
  `interactorModels.test.ts`, 4: which models qualify, and the outline chosen from an answer.
* `./scripts/check.sh` all fifteen steps green: 940 server tests, 802 web, 16 SDK.
* `scripts/verify_interactor.py` in Chromium against the real server and a click-answering
  model server: 6 checks, all passing. Sabotaged in one build (every click sent as on the
  object; the kept shape marked manual): the two checks that cover those failed, recorded
  as failures rather than a crash after making the harness's waits non-fatal.

Iteration 97:

* `scripts/verify_webhooks.py` in Chromium against the real server: 9 checks, every one
  passing — the secret shown once; *Send a test* reaching the receiver signed with it; a
  task created through the API arriving as `task.created`, signed; a refusing receiver (401)
  reported as refusing and sent exactly one attempt; the deliveries listed newest first; a
  paused webhook sent nothing; an annotator not offered the panel; deletion. Sabotaged (see
  above): 3 failures, exactly the checks the sabotage breaks. The build was checked before
  the sabotaged run.
* `webhooks.test.ts`, 10 tests: the twelve events offered once each, who may manage them,
  receiver addresses, what each kind of delivery outcome says; the contract check covers
  the three new types.

Iteration 96:

* `tests/api/test_webhooks.py`, 13 tests. Written first, and failing for the right reasons:
  a subscriber received `[]` where `['task.created']` was due, and an outsider's webhook
  naming another workspace's project was created (201). Now: every one of the twelve
  events reaches a subscriber with a valid signature and its resource; only subscribed
  events, only the project's own for a project webhook, nothing for a paused one, no job
  queued when nobody listens, a failing receiver not failing the change; another
  workspace's project refused, a row stored before the check inert (sabotaged — the
  workspace condition removed — and it failed), deliveries and ping hidden from outsiders
  and annotators; a ping answered with the receiver's reply; deliveries newest first.
* mypy clean; the generated API types regenerated. `./scripts/check.sh` all fifteen steps
  green: 940 server tests, 777 web, 16 SDK.

Iteration 95:

* `verify_desktop_shell.py`, all 18 checks passing locally; sabotaged (`choose_folder` and
  `choose_files` returning nothing whatever was chosen) and exactly the two new ones failed.
  A first attempt at that sabotage did not compile and so tested the unchanged binary —
  caught because it passed, and redone in a form that compiled. Check a sabotage's build
  before believing its run.
* Web lint, types and build; the sidecar rebuilt with the new page.

Iteration 94:

* `verify_desktop_shell.py`, all 16 checks passing locally (Ubuntu 24.04, xdotool 3.20160805,
  WebKitWebDriver 2.52.6), the two new ones included. Sabotaged — the shell's team-server
  menu event renamed and *Use This Computer*'s forgetting removed — and exactly those two
  failed.
* On GitHub at `2ed6a57`: the WebDriver job, with `xdotool` installed, passed — the
  menu checks included.

Iteration 93:

* `helm lint` and `helm template` (Helm 3.16.2, built from source here): the hook renders
  with the S3 settings inline; with `existingSecret`, the credentials come from that Secret;
  with `createBucket=false`, it is not rendered.
* The hook's command with exactly the hook's environment — no database, no secret key —
  against Versity Gateway: "Created bucket curvevision.", exit 0; again, "already exists",
  exit 0; with credentials the store refuses, the 403 named and exit 1.
* On GitHub at `2ed6a57`: *Helm chart installs and serves, in kind* passed with S3
  starting empty — the bucket the upload used was the hook's.

Iteration 92:

* `cargo test` in the shell: 18 tests, including `resolve` each way it can go (built, absent,
  blank, a release build ignoring the environment, a debug build preferring it) and
  `a_python_made_signature_verifies`; `cargo clippy --all-targets -D warnings` and `cargo
  fmt --check` clean.
* `verify_desktop_shell.py` check 9, in its own launch against a local feed announcing
  9.9.9: found, offered, a tampered download refused, another key's signature refused —
  passing every time it was reached; each refusal's message is the one minisign gives for
  that fault, so neither is a download or parse error passing for a refusal. The whole harness, four local runs: two passed all 14 checks, two hit the
  check-3 window-swap flake described under *Known issues*, which predates this iteration.
* `UpdateNotice.test.tsx`: no notice when unconfigured or current, the version and notes
  when one is offered.
* `scripts/updater_manifest.py` on a fake release (the three platforms' names as Tauri
  writes them): the feed names each with its signature; a folder of unsigned files is
  refused.
* `./scripts/check.sh`: all fifteen steps green; 927 server tests (16 skip), 16 SDK, 777 web.

Iteration 91:

* `tests/api/test_saml.py`, 23 tests, against assertions signed with xmlsec by a test
  provider's key: a first sign-in makes an account, the next finds it by NameID though the
  email changed, an existing account is linked by email, the sign-in page offers it, the
  metadata names the ACS; refused — unsigned, signed by another key, changed after signing,
  a second unsigned assertion beside the signed one, the wrong audience, recipient, issuer
  or destination, expired, unsolicited, answering a request older than ten minutes, a login
  code without the tab's verifier, a code not made here, garbage; the policy settings; off
  in the desktop shape. Three sabotages (validation ignored, any verifier accepted,
  unsolicited accepted) caught by 9, 1 and 1 failures.
* `tests/api/test_sso.py`, 17 tests, unchanged in substance after the account-linking move
  (one expectation gained the two new sign-in options).
* Web: `sso.test.ts` adds the SAML callback and the verifier's hash, recomputed in the test;
  the generated API types regenerated and the contract check passing.
* `scripts/verify_saml.py` in Chromium: 5 checks, every one passing. Sabotaged (the tab
  keeps the wrong verifier) and the sign-in check failed — after a first attempt that
  proved nothing: a sabotage that broke the build left the previous build in place, and
  the harness passed against it.
* `scripts/record_demo.py`: the video recorded; the COCO export it shows holds the six
  annotations drawn in it.
* `./scripts/check.sh`: all fifteen steps green; 927 server tests (16 skip: PostgreSQL, S3 and the
  Datumaro round trips), 16 SDK, 775 web.

Iteration 90:

* `tests/unit/test_formats_datumaro_bridge.py`, 10 tests. Without Datumaro (the core suite):
  the measured table names real shape types, nothing registers without Datumaro, a bridged
  format declares what was measured, archive paths outside the archive are refused. With
  Datumaro 1.13.11 (`datumaro.yml`): every measured format is offered; Datumaro's own format
  keeps every shape it can hold, with occlusion, z-order, attributes and a mask pixel for
  pixel; ImageNet folders as image labels; DOTA keeping a rotated box turned the right way;
  items not in the task reported once; an unreadable dataset a warning. Two sabotages (the
  rotation's sign, an ellipse built from the wrong corners) each caught.
* `scripts/datumaro_bridge_probe.py --check`: the table matches a fresh measurement, 14
  formats bridged.
* `./scripts/check.sh`: all fifteen steps green; 904 server tests (16 skip: PostgreSQL, S3, and the
  Datumaro round trips, which run in `datumaro.yml`), 16 SDK, 771 web.

Iteration 89:

* `tests/unit/test_formats_tfrecord.py`, 19 tests: CRC-32C against its published check value
  and a bitwise reference at every alignment; the framing, and a damaged or cut record
  refused; the encoding byte for byte against protobuf's own serialisation, every kind round
  trip (negative and empty included), unpacked repeated values read, a cut message refused;
  the label map in either quoting, `display_name`; the export's files, image, boxes, flags,
  masks all-or-none; the import of an export, classes from the label map, sharded file names,
  and everything unreadable reported while the rest imports.
* `tests/api/test_import_fidelity.py`: TFRecord through the real import route — which found
  the float32 noise now rounded away.
* `scripts/verify_tfrecord.py` with TensorFlow 2.21.0 and Datumaro 1.13.11: 14 checks, every
  one passing; four sabotages each caught (a broken checksum stops TensorFlow outright).
  `verify_open_images.py` and the Open Images tests rerun after the shared rasteriser.
* `./scripts/check.sh`: all fifteen steps green; 894 server tests (10 skip without PostgreSQL or
  S3), 16 SDK, 771 web.

Iteration 88:

* `tests/unit/test_formats_open_images.py`, 15 tests: the files and the class list without a
  header, a box normalised to six places with its flags (``-1`` for one never recorded), a
  model's confidence kept, tags as human and machine image labels, a polygon as the mask it
  covers and the box around it, a mask round-tripping pixel for pixel and arriving once with
  its box's flags, safe mask names from names that are not file names; and on import the
  dataset's own files (MIDs, a header, masks at half resolution naming their box), a label
  verified absent, machine labels, images not in the task as one warning, and what cannot be
  read reported while the rest imports.
* `tests/api/test_import_fidelity.py`: an Open Images archive through the real import route,
  at the size the task knows each image to be.
* `scripts/verify_open_images.py` with Datumaro 1.13.11: 16 checks, every one passing; four
  sabotages each caught, and three more against the unit tests (absent labels read as tags,
  MIDs left as names, masks not scaled), each caught.
* `./scripts/check.sh`: all fifteen steps green; 874 server tests (10 skip without PostgreSQL or
  S3), 16 SDK, 771 web.

Iteration 87:

* `tests/unit/test_formats_cityscapes.py`, 20 tests: the layout and where the image goes,
  Cityscapes' ids, colours and training ids, which shape wins a pixel, instances numbered per
  class from 0, a crowd with no number, a licence plate listed and never drawn, boxes and
  ellipses as their polygons, a mask pixel for pixel, a project's own labels after a
  background; and on import an export's polygons in their order, the dataset's own names,
  instance and label-id images as masks of exactly their pixels, `label_colors.txt` names,
  and everything unreadable reported while the rest imports.
* `tests/api/test_import_fidelity.py`: a Cityscapes archive through the real import route,
  one frame from its polygons file and one from its instance image alone.
* `scripts/verify_cityscapes.py` with cityscapesScripts 2.3.0 and Datumaro 1.13.11: 14
  checks, every one passing, the module raising no deprecation warning. Sabotaged three
  ways — instances numbered from 1, the drawing order reversed, a crowd given an instance
  number — and each caught; the reversed order only on the frame with a mask, which is why a
  unit test pins it too (and fails under the same sabotage).
* `./scripts/check.sh`: all fifteen steps green; 858 server tests (10 skip without PostgreSQL or
  S3), 16 SDK, 771 web.

Iteration 86:

* `tests/unit/test_formats_labelme.py`, 18 tests: each geometry in labelme's terms both
  ways, a rotated box as its real corners and back, an ellipse that is not a circle as the
  polygon tracing it, several points as one group, flags, description and tags, masks pixel
  for pixel, and what cannot be read reported while the rest imports.
* `scripts/verify_labelme.py` with labelme 6.3.1, 14 checks — every one passing. Sabotaged
  (the circle written at twice its radius; the mask PNG reversed): each caught, the second
  only after the test mask was made asymmetric — the first one was symmetric under a 180°
  turn, so a reversed mask passed for it.
* `tests/api/test_import_fidelity.py`: a labelme archive through the real import route, and
  a native archive into a project with none of its labels. Both failed before the route
  fix and pass after it.
* `./scripts/check.sh`: all fifteen steps green; 837 server tests.
* PR #37 on GitHub, `de4bab3`: the LabelMe and Ultralytics workflows green. The WebDriver
  job on 24.04 failed for the first time — not on anything this iteration touched: check 3
  asks each window for its address while the shell is closing the local one, and
  WebKitWebDriver dropped the connection for the dying window. `_shows` took an HTTP error
  as "not this window" but let a dropped connection end the run; it now takes both.

Iteration 85:

* PR #37 on GitHub, `50bba3d`: CI green — including the compose stack brought up and used
  end to end (the first time), the `helm-install` kind job, and the server suite with
  PostgreSQL and S3; the installers built on macOS, Windows and Linux. The WebDriver
  harness, then still a step of the 22.04 installer job, failed there as described under
  iteration 85; its own 24.04 job's result is recorded in the next session's notes if not
  here.
* `tests/unit/test_settings_from_environment.py`, `tests/unit/test_s3_presigning.py`,
  `tests/api/test_dramatiq_broker.py` (fresh interpreters, as the worker and the API
  start; the enqueue test against real Redis) and the fresh-process backup test in
  `tests/api/test_backup.py`: each failed before its fix, as the deployment had.
* `scripts/helm_smoke.py` against a server with a real Dramatiq worker on Redis here: the
  queued job finished, nothing in the worker's log.
* `tests/api/test_team_server_check.py`, 26 tests: addresses as a person types them become
  origins, and what is not a web address (or carries a password) is refused before anything
  is fetched; a team server is recognised with its version, a redirect to https is followed
  and remembered, another desktop installation, a server whose database is down, something
  that is not CurveVision, nothing answering and an untrusted certificate each get their
  own sentence; the route is 404 on a shared server and needs the installation's
  credential.
* Rust (`cargo test`, 13): the address rules, the choice surviving a restart and a damaged
  file meaning this computer, the credential script guarded by origin, the capability
  granting every command to the local window and only to it, and the server stopped by
  end-of-file rather than killed. `cargo clippy -D warnings` and `cargo fmt --check` clean.
* `scripts/verify_team_server.py`, 11 checks, against the packaged server and a team server.
  Sabotaged (the desktop installation accepted; the typed address handed to the shell):
  3 checks failed, then green again after restoring and rebuilding.
* `scripts/verify_desktop_shell.py`, 10 checks, the **built shell** over WebDriver under
  Xvfb. First run: `invoke('connection')` refused, "not allowed" — the bug this iteration
  fixed. After the fix, all nine pass. Sabotaged (the capability granted to the team window
  too; a payload injected into it): the two isolation checks failed, and the output showed
  the team server's page reading this computer's token through the shell.
* Check 8, added after: IndexedDB written in the first launch and read back in the third,
  on this computer's origin. Sabotaged (the webview's data folder removed between
  launches): that check failed, and only that one.

Iteration 84:

* The installers workflow on GitHub, on `dad342d`: `.deb`/`.rpm`/`.AppImage`, `.dmg` and
  the NSIS `.exe` all built; the `.deb` installed, its server started, the health check in
  local mode and the editor's HTML read from it. The two runs before were red on all three
  platforms (`aiosqlite` missing) and then on Windows (the orphaned server) — both fixed.
* `tests/unit/test_packaging.py` pins the default database's driver to the required
  dependencies.
* Versity Gateway v1.8.0 built from source here (`go install`): `tests/api/test_s3_storage.py`
  (storage-init creating the bucket and saying so again; put, get, stream, a presigned URL
  fetched as is, append, delete) passes against it, and `scripts/helm_smoke.py`'s whole
  flow passes against a server on S3 through it, with the gateway's metadata in a sidecar
  directory.

Iteration 83:

* `scripts/helm_smoke.py` against a real server here. On GitHub, on `50bba3d`: the
  `helm-install` job green — every pod ready, the release used through its web service
  (sign-up, an image uploaded and read back, an annotation exported as COCO, a background
  job run by a worker), a backup from inside the cluster, `helm upgrade`. It took five
  runs and found five bugs to get there; see iteration 85's *Deployments that had never
  started*.

Iteration 82:

* `sdk/typescript/test/client.test.ts` (the client against a recording `fetch`: bearer
  token, path built from the schema, a token function asked per request, a problem
  document thrown as `CurveVisionError`) and `test/live.test.ts` **against a real server**
  started by `scripts/with_server.py` (a project created with a label and read back; a 404
  with the server's reason): 5 passing. `npm pack --dry-run`: 5 files, 19 kB.
* `scripts/api_types.py` writes both copies from one OpenAPI document and `--check`s both;
  the client's is generated with `--default-non-nullable false`, because otherwise a
  request had to send every field the server defaults (found by the live test's
  `labels: [{ name: 'car' }]` failing to typecheck).
* `check_notices.py` now reads the client's `package.json`; it caught `openapi-fetch`.
* `./scripts/check.sh` runs the client's typecheck and its tests against a real server;
  green. CI's SDK job does the same.
* Iteration 81 on GitHub: CI and all 33 browser harnesses green, `verify_sso.py` included.

Iteration 81:

* `tests/api/test_sso.py`, 17 tests against a provider in the same process that checks
  PKCE and the client secret and signs RS256 ID tokens: a first sign-in creates the
  account and a second finds it; a verified email signs into the existing account and an
  unverified one never does; a new account joins the default workspace; `return_to` stays
  on this site; refused — a wrong nonce, audience, issuer, an expired token, a token signed
  with a key the provider does not publish, a forged `state`, a code spent twice, a domain
  not allowed, a new person when accounts are not created; `/auth/options` tells the page
  what to offer, and `oidc_only` turns passwords off.
* `scripts/verify_sso.py`, in Chromium against the real server and a provider the script
  runs: the button is offered, the round trip lands signed in on the projects page as the
  account the provider's email made, a callback this tab did not start is refused in the
  browser, and a cancelled sign-in says so. Added to `browser.yml`.
* `sso.test.ts`: the callback check, six cases. Full server suite green; the migration
  checked against the models by the suite.
* The Helm chart gains the SSO settings; lint and kubeconform still clean.
* Iteration 80 on GitHub: every job green, including the new `helm` job and the web
  image's `nginx -T` check.
* Full `./scripts/check.sh` green.

Iteration 80:

* `helm lint`, and `helm template` piped through `kubeconform -strict` against the
  Kubernetes 1.30 schemas: 9 resources valid with an ingress and the chart's own Secret, 7
  with `existingSecret`. Without its required values the chart refuses with the value to
  set. Helm built from source here (`get.helm.sh` is blocked from this container); CI uses
  `azure/setup-helm` and the same commands, in a new `helm` job.
* The web image's nginx config became a template (`deploy/nginx.conf.template`); CI's
  Docker job now loads the built image and checks `nginx -T` proxies to `api:8000` by
  default and to a release's own API service when told. Not run here: no Docker daemon.
* **Not verified: an install into a running cluster.** Said so in `OPERATIONS.md` and the
  roadmap.
* Iteration 79 on GitHub: CI green, including the new SQLite→PostgreSQL restore step.
* Full `./scripts/check.sh` green.

Iteration 79:

* `tests/api/test_backup.py`: a migrated desktop installation seeded through the API (a
  skeleton label with its joints, images, a model's pending suggestion, a track, a tagged
  frame, a released version) is backed up, restored into a fresh installation, provisioned,
  and read back through the API — projects, labels, annotations, both frames' bytes, and an
  export of the release — **equal to before**. Into SQLite and into **PostgreSQL 16**
  (installed and started in this container; `ci.yml`'s PostgreSQL job runs it too). Refused:
  an installation with rows in it, one at another schema revision, an archive with a
  damaged object, a tar that is not a backup. And the commands themselves, `backup
  --desktop` and `restore --desktop`, round-trip a desktop data directory and refuse a
  second restore.
* Iteration 78 on GitHub: CI green.
* `docker compose config` valid with the new `/backups` volume.
* Full `./scripts/check.sh` green.

Iteration 78:

* `tests/api/test_tracing.py`, against OpenTelemetry's in-memory exporter: a request is
  a trace named by its route template, with its SQL statements as spans in the same trace;
  `/metrics` and the health check make none; a background job is a span in the trace of
  the request that queued it; a JSON log line written in a span carries that span's
  `trace_id` and `span_id`; off, nothing is configured. Full server suite green with it.
* **Found writing them:** the SQLAlchemy instrumentation declares `sqlalchemy < 2.1`, and
  against the 2.1.1 installed here it instrumented *nothing*, saying so in one log line.
  `skip_dep_check=True`, and the SQL-span test is what would catch a release where its
  hooks stopped working.
* `scripts/check_notices.py` caught the five new packages missing from
  `THIRD_PARTY_NOTICES.md` (and the dev extra naming its own package); both fixed.
* Iteration 77 on GitHub: every CI job green, and the `clamav` job's clamd — the official
  image, real signatures — logged `Eicar-Test-Signature FOUND` for the test's stream.
* Full `./scripts/check.sh` green.

Iteration 77:

* `tests/api/test_content_scanning.py`, 13 tests. **Against a real clamd** (ClamAV 1.4,
  installed here with `apt`; `freshclam` cannot reach `database.clamav.net` from this
  container, so clamd ran one custom hash signature for the EICAR test file — a real
  clamd, a real `INSTREAM`, one signature): a clean PNG is clean, EICAR is found and
  named, a file of several chunks is scanned. Against an in-process stand-in that records
  the framing: the whole file arrives in length-prefixed chunks after `zINSTREAM\0`, a
  `FOUND` reply names the signature, an `ERROR` reply is not clean. Through the API with
  the scanner replaced: infected is a 422 naming the signature and stores nothing,
  unscannable is a 503 and stores nothing, clean is stored; `fail_open` accepts the
  unscannable. A clamd that is not there is an error, never a pass.
* `ci.yml` gains a `clamav` job against the official `clamav/clamav:stable` image with its
  real signatures — the first run on GitHub is the check that the official EICAR signature
  is matched too.
* Iteration 76 on GitHub: CI green, its new *Rate limit shared through Redis* step
  included.
* `docker compose config` valid with and without `--profile scanning`.
* Full `./scripts/check.sh` green.

Iteration 76:

* `tests/api/test_rate_limit.py`, the limiter's first tests: in memory, the request past
  the limit is a 429 problem with `Retry-After`, each token has its own limit, and two app
  instances each allow the whole limit (the defect, pinned); **through a real Redis**
  (`redis-server` 7 in this container; a `redis` service in `ci.yml`, as PostgreSQL has),
  two app instances enforce one limit between them and each token keeps its own; and with
  Redis unreachable, requests are let through (fail open).
* The CSRF marker checked against the code before rewriting it: the server sets and reads
  no cookie (`grep` for `set_cookie`, `request.cookies`: none), and the web client sends a
  bearer header.
* Full `./scripts/check.sh` green.

Iteration 75:

* `test_label_schema.py::TestEditingAttributes::test_a_default_the_server_fills_in_is_stored_as_its_type`:
  a checkbox, number, select and text default filled in by the server come back as
  `False`, `3.0`, `"led"`, `"none"`; it failed first with `'false'`. Full server suite:
  725 passed, 1 skipped (the PostgreSQL variant without its URL).

Iteration 74:

* `verify_save_refusals.py` claims 9 and 10: Submit pressed while its save fails (a 500
  injected with `page.route`), and pressed while a save the server refuses is held on the
  wire. **Against the iteration-73 editor both submitted the job** (`new → submitted`,
  `in_progress → submitted`; claim 10 puts the job back first so it is checked on its own);
  both pass now, the job's state read from the API.
* `review.test.ts`: `submitBlocker`, the rule Submit applies, three tests.
* Rebuilt sidecar: `verify_save_refusals`, `verify_job_review`, `verify_attribute_values`,
  `verify_crash_recovery` pass. Full `./scripts/check.sh` green.

Iteration 73:

* **Reproduced in a browser first.** `verify_attribute_values.py` claim 2b holds the new
  box's first save open (`page.route`), types into its panel, then lets the save through:
  against the editor before this iteration (fix stashed, web and sidecar rebuilt) the box
  saved as `{}`. Claim 2c holds a save of an existing box open, types again, fails the held
  request with a 500, and lets the retry through: against the editor with only the first
  fix it fails, the retry sending the older value. Both pass with the fixes.
* **An intermittent harness failure was the app's.** `verify_attribute_editor.py`'s last
  step failed once in four local runs, saying (thanks to iteration 70's diagnostics) that
  the box was never selected; its screenshot showed the frame unfitted, at 100% in the
  corner. `engine.test.ts` pins the cause — a fit asked of a canvas with no size yet — and
  fails without the fix (`fitWhenSized`); with it, eight runs out of eight pass.
* `drafts.test.ts`: four tests of `savedForm`, the one rule both fixes share — an edit to
  a drawn object goes under the server id once its first save has answered, and is the
  creation itself until then — including the crash copy, which keeps it either way.
* Against the rebuilt sidecar, every harness that saves from the editor:
  `verify_attribute_values`, `verify_crash_recovery`, `verify_save_refusals`,
  `verify_attribute_editor`, `verify_tool_sync`, `verify_mask_brush`,
  `verify_track_canvas_edit`, `verify_keyframe_editing`, `verify_cuboid`,
  `verify_scissors`, `verify_skeleton_tool`, `verify_tracked_skeleton`,
  `verify_auto_annotate`, `verify_shape_frame` all pass; then **all 32 harnesses in
  `browser.yml`, against the final build: 32 passing.**
* Iteration 72's commit on GitHub: `ci.yml`, `ultralytics.yml` and all 32 browser
  harnesses green (dispatched).
* Full `./scripts/check.sh`: green (725 server collected, 762 web).

Iteration 72:

* `tests/api/test_lossless_round_trip.py`, eight tests through the API — draw, export,
  import into a second project, read back: a manual shape, a pending suggestion
  (`model`, 0.7) and an accepted one (`model`, none) each come back as they were; a tag
  with its attributes and provenance; a rectangle track with keyframes on 0 and 3 and a
  departure on 5, and a polygon that gains a vertex between keyframes on 0 and 4, each
  with exactly those keyframes, through the native format and through CVAT XML (whose two
  decimals the reproduction tolerance absorbs); a `keyframe="0"` frame interpolation does
  not reproduce kept as a keyframe; an import from CVAT XML still claiming no provenance.
  **Seven fail against the code before this iteration**; the eighth is a guard, and held
  before too.
* `test_export_conformance.py` pins CVAT XML's `keyframe="0"` on a frame between keyframes.
* `test_import_fidelity.py`: three expectations changed, each because the old one encoded
  the loss — keyframes on every frame of a round trip (now the drawn ones), and the CVAT
  file's `keyframe="0"` frame that says `occluded="1"` (now kept, where CVAT drops it: see
  the test's docstring). Writing the job-split test's new path found a real defect, fixed:
  a departure right after a part's entering shape was stripped as a leading one.
* `merge._with_track_id` rebuilt a record field by field and would have reset `keyframe`;
  it is `dataclasses.replace` now.
* `ci.yml` and `ultralytics.yml` dispatched on iteration 71's commit: both green on GitHub.
* Rebuilt sidecar: `verify_annotation_import`, `verify_cuboid`, `verify_tracked_skeleton`
  and `verify_track_timeline` pass.
* Full `./scripts/check.sh`: green (725 server collected, 756 web).

Iteration 71:

* **CVAT read first**: `load_anno` in `cvat/apps/dataset_manager/formats/cvat.py` (only
  `keyframe="1"` shapes are kept; an `outside` keyframe is kept as the departure) and
  `_slice_track` in `annotation.py` (a track split across jobs gets the interpolated shape
  on each part's boundary; leading departures are dropped). The import now does both.
* `tests/api/test_import_fidelity.py`: five new tests through the import route —
  interpolated between two keyframes, checked through an export of the result (frame 2
  halfway, frame 5 held); a `keyframe="0"` frame not made a keyframe; an `outside`
  keyframe ending a run and a later keyframe starting one; a track split at a job boundary
  carrying on moving into the next job; a one-frame track closed at once a shape, one left
  open a track. **All five fail against the old code**, as does the rewritten CVAT-file
  test, whose expectation was the old rule's and is now CVAT's (held on frame 5, frame 1
  interpolated rather than a keyframe).
* A gapped track round-trips through the native format, CVAT XML and MOTChallenge, and
  comes back absent where it was absent. With the new reader and the old exporter, the
  CVAT XML case fails; `test_export_conformance.py` pins the export itself (an `outside`
  keyframe on the first frame of each gap and after the last run).
* MOTChallenge and the native format keep "a gap is the object leaving": both list a
  track on every frame it is on and on no other.
* `verify_annotation_import.py` (imports a CVAT XML track) and `verify_cuboid.py` (exports
  CVAT XML) against a rebuilt sidecar: both pass.
* Full `./scripts/check.sh`: green (716 server collected, 756 web).

Iteration 70:

* **The CI failures, read from the runner logs**: every `main` run of `browser.yml` since
  10-08 failed three or four of `verify_mask_brush`, `verify_attribute_values`,
  `verify_attribute_editor`, `verify_save_refusals` — each a 30-second timeout clicking a
  disabled Save, except `verify_attribute_values`, where a new box was saved with `{}`
  instead of the value typed into it. All passed locally, so the difference was timing.
* **Reproduced deterministically**: waiting 4.5 s (one autosave flush) after drawing a box
  made its attributes panel disappear, every time, and the value typed afterwards was never
  saved. `scene.test.ts` gains two tests (one failing first). The fix needed a second pass:
  a drawn box's `id` and the `clientId` it is saved under are different values.
* `verify_attribute_values` now waits past an autosave on purpose and checks the panel is
  still there; against the old editor (fix stashed, rebuilt) it fails, and passes with it.
* `save_now` in `scripts/screenshot.py` replaces five unconditional Save clicks.
* **All 32 harnesses in `browser.yml`, run locally against the packaged app: 32 passing.**
* **On GitHub's runners, first dispatch (run 38279585168, `cd3fc42`): 31 of 32.** The one
  left, `verify_attribute_editor`, said its last editor move was "refused" — but before
  `save_now` the same step had timed out on a Save that stayed *disabled*, so no save was
  sent at all: the drag had landed on the canvas before the job's box was on it (a fixed
  1.2 s wait after loading). Not reproducible locally, even with Chromium's CPU throttled
  6×. The step now waits for the box to be listed in the objects panel, and its failure
  names which way it failed — refused with the server's answer, never selected, or selected
  but not moved (checked by a copy that clicks off the box: "never selected").
* **On GitHub's runners, second dispatch (run 38282080179, `0b6b38e`): all 32 harnesses
  passing** — the first green `browser.yml` since 10-08. (Before it, the one harness alone:
  run 38281668412, green.)
* Full `./scripts/check.sh`: green (756 web).

Iteration 69:

* `tests/api/test_joint_repair_migration.py`, on a real migrated installation: data made
  through the API, three rows rewritten the old ways with SQL (joints by name and out of
  order; a skeleton on a label with no joints; a keyframe carrying another project's ids),
  then migrated down past the repair and up, and read back through the API. It failed
  before the migration existed (the by-name row came back raw). **Runs on SQLite and on
  PostgreSQL 16** (`CURVEVISION_TEST_POSTGRES_URL`; CI's PostgreSQL job now sets it). The
  PostgreSQL run failed first — the migration declared `created_at` as a naive `DateTime`,
  and asyncpg refuses an aware value for it — and passes with the app's `UTCDateTime`.
* The type contract: with `WithoutJoints` gone, mistyping `ApiSkeletonElement.label_id`
  gives five `contract.ts` errors; restored, none.
* `test_the_newest_migration_runs_in_both_directions` drove whatever was newest (`-1`),
  which stopped being the index migration it asserts about; it now names that revision.
* Full `./scripts/check.sh`: green.

Iteration 68:

* **Server**: `TestTrackedSkeleton` (4, two failing first: joints frozen, joints paired by
  position) and `test_a_tracked_skeleton_s_joints_move_between_keyframes` (API, through the
  per-frame endpoint and a COCO export; fails without the fix with the frame-0 joints).
* **Editor**: `trackedSkeleton.test.ts` (7 — interpolation with the server's vectors,
  drawing, saving with joints, adding a keyframe, a placed keyframe, a departure; all 7 fail
  with the four source files stashed) and two in `tools.test.ts` (moving a skeleton,
  dragging one joint past a skipped one; both failed first).
* **Browser**: `scripts/verify_tracked_skeleton.py`, 7 checks, all passing on the packaged
  app. Against the old editor (fix stashed, rebuilt) four fail: nothing drawn at the
  interpolated joints, no keyframe saved, the untracked move saving nothing. Its first run
  had one check wrong — the frame-0 nose is where this frame's label chip is drawn — and the
  check was moved to the frame-0 tail, not loosened.
* Full `./scripts/check.sh`: green.

Iteration 67:

* **Ultralytics 8.4.175 with PyTorch 2.14.1**, from PyPI into a scratch environment with the
  server package: `scripts/verify_ultralytics.py`, 15 checks, all passing. For detection
  (boxes only, and with a polygon), segmentation, OBB, pose and classification, Ultralytics'
  `check_det_dataset`/`check_cls_dataset` accept the archive, `YOLODataset` and
  `ClassificationDataset` load every image (`0 corrupt`) with labels equal to values
  computed from the source geometry, and one epoch of each trains through validation (from
  `yolo11n*.yaml`, no weights downloaded). Run from a working directory outside the export.
* **The script's first run failed**, before any fix: `images not found, missing path
  '/home/user/CurveVision/images/val'` — the `path: .` defect.
* **Sabotage, so the checks are known to fail**: with the iteration-66 mixed-rows bug
  restored and `path: .` put back, four checks fail by name — Ultralytics 8.4 refuses a
  file mixing box and segment rows outright ("labels mix segment and detection rows", every
  such image dropped as corrupt), and the OBB and pose yaml cannot be found. Restored.
* `tests/unit/test_export_conformance.py` gains 9: `flip_idx` derived and refused, the val
  split for `yolo`/`yolo_obb`/`yolo_pose`, no `path:` key in any `data.yaml`, and the
  classification README asking for `val/` only without one. The `flip_idx`, split and
  `path` tests failed before their fixes; the README test was checked by stashing the fix.
* `verify_workspace.py` was claimed (iteration 66) to run nightly and was not in
  `browser.yml`; it also assumed a `server/.venv` a runner does not have. Both fixed; it
  has not yet run on a runner.

Iteration 66:

* **Readers.** A rich dataset (tracks, a rotated box, a polygon, a mask, a skeleton with
  visible, occluded and absent joints, a polyline, attributes) exported through every format
  and read by Datumaro 1.13.11: VOC, KITTI, MOTChallenge, segmentation and COCO load with the
  right labels and boxes. CVAT XML was then read by **CVAT's own extractor** (CVAT's
  `develop` `formats/cvat.py`, against `cvat-ai/datumaro`, Django stubbed) in both modes:
  every box, the rotated box with its rotation, polygon, polyline and the skeleton with
  `head`/`hand`/`foot` as visible/hidden/absent come back. Datumaro's own CVAT reader cannot
  read skeletons (it parses their joints as track shapes) or masks inside tracks, which CVAT
  itself writes; those are Datumaro's limits, recorded under *Known issues*.
* `tests/unit/test_export_conformance.py` (17), each against the reader's rule; the YOLO
  ones restate Ultralytics' `img2label_paths`, the [0, 1] range, the class bound and the
  per-task row widths.
* **The escalation**, reproduced first: as an admin, `PATCH` of their own membership to
  `owner` answered 200 and adding a newcomer as `owner` 201. `TestWhoManagesWhom` (5).
* **Reclaiming media**: `tests/api/test_deletion.py` (3) fail with the new enqueue removed —
  checked by doing exactly that, then restoring.
* **Reproducible releases**: `TestAReleaseIsReproducible` (5) — an edit after release does
  not reach the release's export while it does reach the project's; images by hash; a draft
  exports live; an altered frozen copy is refused; deleting the project deletes the copies.
  `read_view` is pinned as the exact inverse of the native export.
* **Browser**: `scripts/verify_workspace.py`, 20 checks, all passing — against the real
  server (uvicorn, SQLite, the built bundle) as an admin, then the packaged desktop server.
  Its screenshots found two things the tests could not: "1 frames · 1 annotations", and the
  delete form squeezed beside the task's status badge. Both fixed.
* Full `./scripts/check.sh`: green (690 server, 16 SDK, 745 web). It caught one test
  written against the old behaviour — the local-media collector test called the collector by
  hand after a delete, which now runs it — updated to assert the same thing directly.

Iteration 65:

* `tests/unit/test_coco_reference.py` (9) and `tests/api/test_coco_export.py` (1), written
  first: on the old exporter six fail — the missing mask, the unrotated `bbox`, the stale
  absent joint and `num_keypoints`, and the end-to-end export. With the fix, pycocotools
  loads every export, rasterises every segmentation, the mask is pixel-identical after
  COCO's RLE (an L-shape, so a transposed decode cannot pass), a compressed RLE made by
  pycocotools' own encoder reads back identically, and `COCOeval` gives AP 1.0 on boxes,
  masks and keypoints.
* The YOLO Pose absent-joint test failed first (`0.950000` where `0.000000` belonged).
* What the tests themselves had wrong, for the next person: pycocotools cannot segm-evaluate
  a category holding a box written with `segmentation: []` (as CVAT and Datumaro write
  boxes), and keypoint evaluation needs per-joint sigmas for a non-COCO skeleton.
* `./scripts/check.sh`: **all thirteen pass** — 658 server, 15 SDK, 718 web.

Iteration 64:

* `annotationImport.test.ts` (7). `contract.ts` now holds `AnnotationImportResult` to
  `ImportResultOut`.
* `verify_annotation_import.py` (10 checks) against the packaged server, importing an
  archive written as CVAT writes one. Its first run failed one check and was right to: a
  CVAT XML track lands in a task whose files are named differently, because CVAT places
  tracks by frame number. The panel's copy said frames match by file name; it now says which
  formats match how, and the "nothing matched" step uses COCO. One sabotage build (replace
  sendable unticked, the client always sending `append`, tracks left out of the summary)
  failed exactly those three checks; the restored build matches the clean bundle hash.
* `./scripts/check.sh`: **all thirteen pass** — 647 server, 15 SDK, 718 web.

Iteration 63:

* `tests/unit/test_hosted_vision.py` (22): answer reading (fences, case, swapped corners,
  percentages, no JSON), image downscaling, Claude through the real SDK on an `httpx2` mock
  transport (image block, schema, `fallbacks` + beta header on Opus 5.5 and not on Haiku
  5.5, refusal, wrong key, unknown model), and the OpenAI-compatible provider (data URL, no
  `response_format`/`temperature`, answer in parts, model-list filtering, wrong key and
  base URL). The first run caught a real bug: rounding the downscaled size landed 102
  pixels over the 1.15-megapixel cap; it floors now.
* `test_model_registration.py::TestHostedModels`: discover, register and run a hosted model
  through the API; the box lands in the frame's pixels and the prompt names the project's
  labels.
* `verify_hosted_model.py` (15 checks) against the packaged server and a real localhost
  OpenAI-shaped service, through *Other OpenAI-compatible service*. Made to fail first: one
  sabotage build (unscaled boxes, no non-chat filter, no privacy note, no class names in the
  prompt) failed exactly those four checks. `verify_model_registration.py` updated for the
  new tab and passing.
* The packaged desktop server builds with the SDK in it and passes its smoke test.
* `./scripts/check.sh`: **all thirteen pass** — 647 server, 15 SDK, 711 web; notices cover
  `anthropic`, `httpx2`, `jiter`, `docstring-parser`.
* **Not done: any call to a real service.** No key was available.

Iteration 62:

* `tests/api/test_import_fidelity.py` (8) written first; all seven that existed then failed
  on the old route — no track came back from any format, and the CVAT file's track frames
  came back as loose boxes. The eighth (a track crossing a job boundary) was added with the
  fix. Two rules were then sabotaged one at a time: no `outside` keyframes fails five tests,
  no attributes kept fails four.
* The first fix failed both CVAT XML round trips: its exporter writes a standalone shape as
  a one-frame track when the dataset has tracks (CVAT's own convention), so it came back as
  a track. One-frame tracks are now shapes.
* `pytest tests/api tests/unit/test_formats*.py` and the SDK suite pass;
  `./scripts/check.sh`: **all thirteen pass** — 624 server, 15 SDK, 699 web; `api types`
  regenerated for `ImportResultOut`.
* No browser harness: nothing in the application reaches import yet (the next best action).

Iteration 61:

* `tests/api/test_model_registration.py` (10) written against the route before it existed:
  all ten fail on the old server and pass on the new. `registration.test.ts` (13) for the
  form's rules.
* `verify_model_registration.py`, against the packaged server and a real localhost model
  server that wants a key: **24 checks pass**. Each was made to fail first, in three
  sabotage builds (each confirmed to compile, by bundle hash): no link in the empty panel;
  discovery's model id dropped; the key not part of "connected"; the key left out of the
  registration; `config` (with the key) added to `ModelRegistrationOut` together with the
  card rendering the model; and the editor listing every workspace's models. The first
  build's config sabotage passed — the key sabotage had already removed the key it would
  have leaked — so it was rebuilt alone, and then failed both key checks. Restored builds
  match the clean bundle hash.
* The workspace check found a harness bug, not a product one: with two workspaces the
  Models page opens on the first, and the removal step removed the other one's model. The
  step now picks the workspace.
* `verify_auto_annotate.py` still passes (33), after the panel's change.
* `./scripts/check.sh`: **all thirteen pass** — 616 server, 15 SDK, 699 web; `api types`
  regenerated for the two new schemas, and the contract covers both.
* Not done: the page was not driven as an annotator (the desktop shape has one person); the
  rank is pinned by the API test and `canRegisterModels`.

Iteration 60:

* Reproduced first, as tests that failed before the change:
  `TestImportingSkeletons` (COCO stored `{"name": "nose", ...}` with no `label_id`; the
  native format stored the source project's joint ids; a label the import created had no
  children), the inference test (`KeyError: 'label_id'` — the model's joints stored as sent),
  `test_a_skeleton_label_comes_back_with_its_joints` (500 on `POST` without the
  `_label_out` fix), and five of the six in `tests/unit/test_http_provider.py` (the sixth,
  joints in pixels kept as sent, is a guard that passes either way).
* `./scripts/check.sh`: **all thirteen pass** — 606 server, 15 SDK, 686 web. `api
  types` unchanged: no response schema changed.
* No browser harness: the editor was not changed, and what it reads was pinned at the API.
  Not done: importing a real third-party COCO keypoints file (the tests round-trip this
  project's own COCO export, which is what the reader is written against).

Iteration 59:

* `./scripts/check.sh`: **all thirteen pass** (the new step is `api types`) — 591 server,
  15 SDK, 686 web.
* Both directions bite. Renaming `z_order` in `ApiShape` makes `tsc -b` *and* `npm run
  typecheck` (what CI's web job runs) fail with `["missing", "zorder"]`; renaming a field
  of a server schema makes `scripts/api_types.py --check` exit 1. Both restored.
* Typing `elements` on the server made the contract hold with no exception, and the server
  suite passed — but the change was reverted on reading the import path, which stores
  joints the strict type would refuse on read (see *Next best action*). Not run: CI's new
  "API types match the server" step, which only runs on GitHub.

Iteration 58:

* `TestAResentCreate` (3 tests) written first: the resent shape failed with an
  `IntegrityError` on `uq_shape_client_id` before the change. All three pass after, and all
  three fail again with the client-id lookup disabled.
* `./scripts/check.sh`: **all twelve pass** — 591 server, 15 SDK, 686 web. No browser
  harness: nothing the editor sends changed, and it could not reach the defect.

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

* **No webhook had ever received an event** (iteration 96). The signing, the retry policy
  and the `webhook.dispatch` job were built and unit-tested, and no code queued the job, so
  the roadmap's *Done* was false. Twelve events are now emitted from the routes that make
  the changes. Found while planning a webhooks screen, by asking what it would show.
* **A webhook could name another workspace's project** (iteration 96), and would have
  received everything that project did, had anything been sent. Refused on creation;
  matched on the workspace on delivery.
* **A server run as several instances let a client through once per instance** (iteration
  76): the only rate limiter counted in each process. Redis now backs it when configured.
* **A default the server filled in was stored as a string** (iteration 75): a checkbox's
  `"false"`, a number's `"3"`, whenever an annotation came from the SDK, the CLI, an import
  or a model — and every export carried the string.
* **Submit handed a job to review without work the annotator could still see** (iteration
  74): when the save Submit runs first failed outright, or was refused, or was still in
  flight from the periodic autosave (`flush` returned at once rather than waiting), the job
  was submitted anyway. Submit now refuses, saying why, unless that save left nothing.
* **Typing into a new box's attributes while its first save was in flight was lost**
  (iteration 73): autosave dropped any edit to a drawn object not queued to be created,
  which is where it is while its creation is on the wire and until the reload swaps its
  server id in. Iteration 70 made that window the normal one, by keeping the panel up.
* **A save that failed put its batch back over the edits made while it was in flight**
  (iteration 73), so the retry sent the older value (found by reading in iteration 53).
* **The editor sometimes opened a frame at 100% in the top-left corner** instead of fitted
  (iteration 73): when the image loaded before the canvas' first resize, the fit was asked
  of a 0x0 viewport, did nothing, and nothing asked again. It was the intermittent "the
  box was never selected" in `verify_attribute_editor.py` one run in four locally, since
  every harness clicks where a fitted image would be — and very likely iteration 70's CI
  failure of the same step, which predates the diagnostics that would say so.
* **Restoring a native backup turned every pending model suggestion into finished work**
  (iteration 72): the format wrote each annotation's `source` and `confidence` and the
  import ignored both, storing everything as `imported`. A tag lost its attributes too.
* **Every track exported to CVAT XML or the native format came back with a keyframe on
  every frame** (iteration 72), in this application and in CVAT: neither export said which
  frames were authored.
* **A CVAT XML track with sparse keyframes came back empty between them** (iteration 71):
  the import read a frame a track did not list as the object being gone, where CVAT reads
  it as interpolated. A file written by hand, by a script or by another tool — anything
  but CVAT, which lists every frame — lost every frame between its keyframes, and a track
  nothing closed was ended after its last keyframe instead of held to the end of the job.
* **A CVAT XML export of a track that leaves and comes back showed it in CVAT throughout
  its absence** (iteration 71): only a track's end was marked `outside`, and CVAT
  interpolates across a frame a track does not list.
* **A drawn box lost its selection, and its attributes panel, when its first autosave
  landed** (iteration 70): the reload brings it back under the server's id, and the scene
  dropped selected ids it no longer had.
* **The nightly browser run had failed on every `main` push and schedule since 10-08**
  (iteration 70): five harnesses clicked Save unconditionally, which times out whenever
  autosave has already saved.
* **`verify_attribute_editor` blamed a refused save for a drag that never happened**
  (iteration 70): it dragged after a fixed 1.2 s, before the box was on the canvas on CI's
  runners. It now waits for the box, and says which way a move failed.
* **Moving a skeleton, or dragging one of its joints, changed nothing drawn or saved**
  (iteration 68): the select tool moved `points`, the joints' flattened copy.
* **A tracked skeleton was invisible in the editor, froze between keyframes, and lost all its
  joints on any save** (iteration 68).
* **Every YOLO export failed to train unless started from inside its directory**
  (iteration 67): `data.yaml` said `path: .`, which Ultralytics resolves against the working
  directory. Found only by running Ultralytics itself.
* **YOLO OBB and Pose pointed `val` at the training images** even when frames were in a
  `val` subset, and **Pose wrote no `flip_idx`** (iteration 67).
* **`verify_workspace.py` was documented as running nightly and was not** (iteration 67).
* **Every YOLO export was refused by Ultralytics** (iteration 66): images and labels went
  to `images/default` while `data.yaml` named `images/train`. And **a YOLO file mixing box
  and polygon lines corrupted the boxes**: Ultralytics reads any file with a line longer
  than six values as segments throughout.
* **An admin could make themselves owner** and then demote the owner (iteration 66). The
  policy gated *who may manage members*; nothing gated *which*.
* **A released dataset version kept only a hash and statistics** (iteration 66); exporting
  "the release" read the project as it was at export time.
* **Deleting a task or project left its media in storage forever** (iteration 66): the
  collector existed and nothing queued it. It also deleted files before rows, so a
  concurrent upload of the same image could lose it.
* **CVAT XML dropped rotated boxes and skeletons**, wrote empty `<track>`s and a closing
  frame past the last, and lacked `original_size` (iteration 66).
* **Pascal VOC's labelmap gave `#rrggbb` colours, and rotated boxes as their unrotated
  box; segmentation masks had no image set; MOTChallenge's label list was not where readers
  look** (iteration 66).
* **COCO exports dropped masks; rotated boxes, partly visible skeletons and keypoint counts
  were wrong** (iteration 65) — see *Status*. Root cause: COCO was only ever checked against
  this project's own importer.

* **Every import dropped attributes and broke tracks apart** (iteration 62), contradicting
  the README's round-trip table for CVAT XML, MOTChallenge and the native format. Root
  cause: the route stored `attributes={}` and ignored `track_id`; the readers had always
  produced both.

* **Nothing in the application could add a model** (iteration 61), while the editor's empty
  panel said to "register an inference endpoint". Only `POST /models` with a hand-written
  body could. Now the Models page.
* **Model discovery was documented and dead** (iteration 61): `HttpModelProvider.list_models`
  had no caller, so a model server implementing `GET /models`, as `docs/API.md` asked, got
  nothing for it. It also swallowed every failure as "no discovery", which would have hidden
  a refused key; it now says which failure it was.
* **The editor offered models from other workspaces** (iteration 61), which the server
  refused for the job with a 404: `api.models()` was called with no workspace.
* **The README claimed Triton, TorchServe and "every hosted vendor" already speak the
  inference contract** (iteration 61). None does; each needs a thin wrapper. Corrected.

* **Joints that came from outside were stored where nothing could read them** (iteration
  60): by name from a COCO import or a model, by another project's ids from a native import.
  Root cause: the import and inference paths stored `elements` as they arrived, and only
  the editor's own writes used joint ids. Fixed in `services/skeletons.py`, used by both.
* **A skeleton label an import created had no joints** (iteration 60): `LabelSpec` carried
  the keypoints and edges, and the route created the label from its name and colour only.
* **Adding or editing a label with joints through `/projects/{id}/labels` was a 500**
  (iteration 60), though the label was written: serialising a joint read the joint's own
  children, which `_label_out` did not load, and async SQLAlchemy cannot load lazily
  mid-serialisation. `load_project_labels` already loaded the extra level, with that comment.
* **A normalising model's joints stayed in `[0, 1]`** (iteration 60): `HttpModelProvider`
  rescaled `points` and passed `elements` through, so every joint landed in the top-left
  pixel.

* **A create resent with a `client_id` the job already had was a 500** (iteration 58), for
  shapes and tracks alike: `uq_*_client_id` and no check before the insert. Reachable by a
  script that omits `annotation_version` and retries after a lost answer. Now the same object
  again is answered with the one it made, and a different object under the id is a named
  422.

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

* **WebKitWebDriver drops connections** (2.52.6 here; the CI runner's too). Around a window
  opening or closing, and during a long-held asynchronous script, it closes the HTTP
  connection without answering. `verify_desktop_shell.py` crashed on it: check 3 locally
  (iteration 92), a poll after a relaunch, and check 9 in CI on `efc528f` (iteration 96's
  push), mid `install_update`. Iteration 97 made the harness tolerate it rather than retry
  whole runs: shell commands are started by one short request and collected by polls
  (`invoke`), and a poll whose connection drops counts as "not yet" (`until`). Before: 2
  crashes in 4 local runs. After: 6 runs of 6 clean. A session that has really gone still
  fails, as a check with its reason. If a new check crashes the same way, it is holding a
  request open or polling outside `until`.
* **Datumaro 1.x reads Cityscapes differently from the dataset** (iteration 87). It takes
  `instanceIds` values as class ids, where the dataset (and cityscapesScripts, and this
  export) write class x 1000 + instance, so it finds no car or person in real Cityscapes
  instance images; it reads `labelTrainIds` first when present, which is why the export
  writes one for every project; and it skips label index 0, which in its training-id map is
  the road, so it reads no road from anyone's files. Its `label_colors.txt` reader keeps only
  the first word of a name, so a project label with a space (`wooden crate`) reaches
  Datumaro truncated. This project's reader handles all four.
* **YOLO Pose writes `flip_idx` only when joint names say left and right** (iteration 67).
  A skeleton named `hand1`/`hand2` gets none, and Ultralytics then trains without flip
  augmentation — slower to generalise, never wrong. Naming joints `left_…`/`right_…` fixes it.
* **Datumaro's CVAT reader cannot read skeletons or masks inside tracks** (iteration 66).
  Both are what CVAT itself writes, and CVAT's own reader reads ours; Datumaro users
  importing a CVAT XML with either will hit Datumaro's limit, not ours.
* **A release's images are found by hash in storage** (iteration 66): delete every task
  holding an image and a later download of the release has no image for it (the export
  lists which). The annotations are frozen; the pictures are not copied.
* **Versions released before iteration 66 have no frozen copy** and answer `409` on export,
  saying so. Their hash and statistics still describe them.
* **YOLO classification writes `val/` only for frames in a `val` subset**; with none, the
  export's README says how to make one (iteration 67: it now says so only then).
  Splitting someone's data is their decision.
* **Hosted models have never met a real service** (iteration 63). The request shapes come
  from each API's documentation and the Anthropic SDK; how good the boxes are, and whether
  a given service's model list or JSON habits need adjusting, is unknown until somebody runs
  it with a key.
* **A hosted run is one request per frame**, four at a time, inside the HTTP request that
  started it: a run over hundreds of frames is slow and can hit the service's rate limit,
  reported as a 502 that says so. Run it on a frame range.
* **Boxes only** from hosted models: no masks, polygons, keypoints or whole-frame tags yet.

* **A MOTChallenge track comes back with a keyframe on every frame it lists**: the format
  has no way to say which frames were keyed. CVAT XML and the native format do, and keep
  them (iteration 72). The positions are right either way.
* **Only the native format brings provenance in**: an import from any other format stores
  `source: "imported"` with no confidence, because nothing else wrote its `source` from an
  annotation of this server's. Tag attributes likewise come in only from the native format.
* **A job-wide tag (one with no frame) is exported onto every frame of its job**, by every
  format, so an import brings it back as one tag per frame. No export format here has a
  place for a tag that belongs to no frame.

* **A model's API key is stored as given**, in `model_registrations.config`, as a webhook's
  signing secret is in `webhooks.secret`. Neither is returned by the API; anyone who can
  read the database can read both. Encrypting secrets at rest is not done for either.
* **The Models page opens on the first workspace** when there are several, not on the one
  last used; a picker switches it.

* **The webviews' storage across launches is verified on Linux only.** Recovery in the
  desktop shape rests on the webview keeping IndexedDB for an origin from one launch to the
  next. `scripts/verify_desktop_shell.py` (check 8) proves it for WebKitGTK in the built
  application, and fails when the webview's data folder is removed between launches;
  WebView2 (Windows) and WKWebView (macOS) have no WebDriver bridge in Tauri and remain
  the platform default, unverified here.
* **WebDriver on Ubuntu 22.04 (WebKitGTK 2.50) dropped its session** two runs in three, a
  moment after the shell closed a window; tauri-driver reported the WebKit driver
  resetting the connection, and the shell printed no panic. On 2.52 it has never
  happened, so the harness runs on 24.04. Unknown: whether the shell itself misbehaves on
  22.04 when it swaps windows. Iteration 86 saw the same reset once on 2.52, and it was the
  harness probing the window being closed (now tolerated) — so the 22.04 failures may have
  been that too; unproven until the harness runs there again. Iterations 88 and 89 met the
  same reset at the other end, ending a session (the application closing as WebKitWebDriver
  answers); `quit()` now takes a dropped connection as the session over, and the checks
  after each quit still establish that the application stopped. Worth checking by hand on a 22.04 desktop: connect to a team
  server and back a few times.
* **The native menus are driven by no test.** WebDriver cannot click them; *Use This
  Computer* shares `switch_to_local` with the code the WebDriver harness does exercise.
* **The desktop installers are unsigned** (iteration 84): macOS asks the person to open the
  app from System Settings, and Windows SmartScreen warns.
* **Until an offer is restored or discarded, new work is not copied**: rewriting the copy
  would replace what was found. New work still saves every four seconds; what is lost to a
  crash in that window is at most what failed to save meanwhile.
* **Two tabs on one job share one copy**, keyed by job: the later write wins, so a tab that
  crashes while another keeps working can lose its copy. Two tabs on one job also 409 each
  other at the server, so this is not a way anybody works.
* **A desktop relaunch that starts before the last server has let go of its port** takes
  another port, and so another origin, for that launch; the remembered port is kept, so the
  next launch is back on it.
* **A refusal of an entry autosave cannot set aside still puts the batch back whole** —
  anything outside `created_shapes`, `updated_shapes` and `updated_tracks`. After iteration
  55 nothing autosave sends is known to hit this, but the fallback is the old behaviour.
* **`verify_track_canvas_edit.py`'s other undo steps share the timer race** its "never
  saved" step now guards against: an autosave landing between an edit and its Ctrl+Z
  makes the undo a no-op (the reload clears the stack), and the step then fails. Seen in
  that one step this session (twice in about twenty runs); the drag-undo, saved-shape and
  track steps have the same exposure over shorter windows and were not seen to fail. The
  guard there is the pattern to copy if they are.
* **Defaults recorded as strings before iteration 75 stay strings** until the annotation's
  next save, which coerces them; no migration rewrites them.
* **A rename or removal of an attribute with a default is refused as soon as the label is
  used**, because a default is recorded on every annotation saved with the label. That is
  the stated cost of refusing rather than rewriting (see *Decisions*), and the form says so.
* **The PostgreSQL run of the attribute tests is manual.** CI's Postgres job only migrates;
  the whole suite runs on SQLite.
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

* **A YOLO dataset with any polygon is segments throughout** (iteration 66), boxes as their
  four corners. The alternative — dropping polygons from the detection format — loses data;
  mixing loses boxes. Ultralytics derives the same box from the corners, and the importer
  reads four axis-aligned corners back as a rectangle (so a polygon drawn as a perfect
  rectangle comes back as one).
* **Below the owner, members manage only members below them** (iteration 66), CVAT's rule.
  CVAT has one owner; this has several, so owners manage anyone and the last one is
  protected by the existing 409.
* **A release freezes annotations, not images** (iteration 66). Copying images per release
  multiplies storage by the number of releases; finding them by content hash costs nothing
  and fails only when someone deleted every task that held them — said in the export.
* **COCO masks use `iscrowd: 0`, not CVAT's `iscrowd: 1`** (iteration 65). Datumaro writes
  every mask as a crowd region; Detectron2 and MMDetection then ignore it in training. A
  mask drawn here is one object, so it is written as one.
* **Pascal VOC stays 0-based floats** (iteration 65), as Datumaro writes and reads it,
  rather than the devkit's 1-based integers: interoperating with CVAT outweighs the devkit
  letter, and every common loader parses floats.

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

### 102f — a security audit, and the checks that keep it true

Asked for "more security checks so others can safely install it". Ran every scanner against
what ships, fixed what they found, and made each a permanent CI check
(`.github/workflows/security.yml`, on every PR, `main`, and weekly):

* **pip-audit** on `server/constraints.txt`: nothing found.
* **npm audit** (production): `react-router` 6 had an open redirect and no patched 6.x, so
  it was upgraded to **7.18.5**. Typecheck, lint, 818 tests and the build pass.
* **bandit**: the CVAT XML and Pascal VOC importers parsed untrusted XML with the standard
  library. They now use **defusedxml** (declared, pinned and in THIRD_PARTY_NOTICES).
  `tests/unit/test_formats_hostile_xml.py` feeds both an entity bomb and an XXE probe;
  with the old parser restored, all four hostile cases fail.
* **cargo audit**: 10 vulnerabilities, now 0. The TLS crates moved to patched releases,
  and `tauri-plugin-updater` went 2.12 → 2.14.1, which takes `zip` 4.6 → 9.5. **Not built
  here** (no WebKitGTK dev libraries in this container): `desktop.yml` on the PR builds
  and tests all four platforms.
* **Secrets**: the whole git history was scanned for common credential patterns. Clean.
* **Trivy** (pinned to a commit) for lockfiles and secrets, failing on a finding; its
  configuration scan of the Dockerfiles and Helm chart reports without failing. **Not run
  here**: downloading the binary was refused in this container, so CI is its first run.
* **Dependabot** for pip, npm, cargo and actions. Releases now carry an **SPDX SBOM**.

### 102e — a product website, and the Mac builds proven

* **`site/index.html`**: the landing page, one self-contained file in the app's pitch-black
  style. It has a hero with the real editor screenshot, the formats it exports, the
  features, real numbers only, the demo video, desktop against team server, and a download
  card per platform. The download button names the visitor's own OS. It is responsive
  (no sideways scroll at phone width), and shows no fake logos or invented users.
  **`pages.yml`** publishes it to `https://derric01.github.io/CurveVision/` on a push to
  `main`, copying in the screenshot, video and icon. **A maintainer must turn Pages on
  once:** Settings → Pages → Source: GitHub Actions.
* **The Mac builds passed** on PR #41, Apple Silicon (`macos-15`) and Intel
  (`macos-15-intel`), including `codesign --verify --deep --strict` and the architecture
  check.
* **WebDriver harness:** a third place where WebKitGTK drops the connection, the team
  server's sign-in click, which navigates. It now tolerates the drop and checks the
  outcome, as the first submit already did.

### 102d — pitch black and white: a monochrome look, two themes

Asked for a look like ChatGPT's, "white and black theme, pitch black".

* **Every colour is a CSS variable** (`web/src/index.css`), and `tailwind.config.js` maps
  the existing `ink` and `curve` names onto them. So no markup changed; the
  `<alpha-value>` form keeps `bg-ink-900/90` working.
* **Dark, the default, is pitch black**: page `#000`, panels barely lifted, neutral greys,
  and white as the only accent (the primary button is white with black text). **Light**
  is the same scale turned over, with black as the accent. The pale status tints (amber,
  red, green, sky) get darker shades in light, or they could not be read on white.
* **A toggle** (sun/moon) in the top bar and on the sign-in page, remembered per browser
  (`ui/theme.ts`, tested), and applied before React renders, so the page never flashes.
* Pill buttons, an outlined secondary, rounder inputs and panels. The logo is drawn in
  `currentColor`, and the favicon is black and white. On the canvas, the marquee, hover
  and handle colours are monochrome too; label colours are untouched.
* The demo video and the README screenshots are regenerated in the new look (the
  screenshot needed the packaged server rebuilt, since it embeds the web build).

Verified: web lint, typecheck, 818 tests (2 new); browser harnesses `verify_tool_sync`,
`verify_view_settings`, `verify_interactor`, `verify_workspace`, `verify_frame_tags` and
`verify_mask_brush` pass; both themes screenshotted on sign-in, projects, project and
editor.

### 102c — a demo video worth watching, and an editor rail that scrolls by itself

Asked for a marketing video that is "very realistic and effective". `scripts/record_demo.py`
is rewritten. Every frame is still the real application:

* **A real model.** `scripts/demo_detector.py` serves **YOLO11n** (ONNX, on CPU through
  onnxruntime) behind the HTTP inference contract. It finds the cup (79%), the cat (87%)
  and the astronaut (90%); the video reviews those, rejects one, and draws by hand what the
  model cannot know ("saucer" is no COCO class) or missed (the spoon, the rocket). The
  weights are AGPL-3.0 and downloaded at run time from Ultralytics' release, never
  committed. Neither onnxruntime nor the model is a CurveVision dependency.
* **1080p at 30 fps.** Chromium runs in an app window (no tabs or address bar) on Xvfb,
  captured with ffmpeg's x11grab, not Playwright's low-bitrate recorder. A drawn pointer,
  click ripples, a keycap for each shortcut, captions, and a title and closing card with
  the download link. Chrome's password-manager bubble is off in the profile. Its download
  animation, which no flag removes in an app window, is painted over from the export on.
* **Checked, twice.** The COCO export must hold exactly the six objects the video makes,
  and each hand-drawn shape must land where it was meant to (IoU ≥ 0.8). An earlier take
  passed a count-only check with the saucer drawn over the whole photograph, because a
  reviewed suggestion had left the view zoomed in. The video now clicks *Fit to frame*
  before drawing, as a person would.

Two product fixes the recording exposed:

* **The editor's right rail scrolls on its own** (`EditorPage.tsx`). A long panel used to
  scroll the whole editor, canvas included, under the pointer. The object list keeps a
  minimum height. Re-run and passing: `verify_auto_annotate`, `verify_suggestion_review`,
  `verify_frame_tags`, `verify_issues_panel`, `verify_attribute_editor`,
  `verify_quality_panel`.
* **A model with many classes no longer floods the panel.** `listBriefly` names the first
  eight and counts the rest, in the plan and in the unmatched note (`autoAnnotate.ts`, with
  tests). An 80-class COCO detector printed all 80, twice.

### 102b — a Mac download for every Mac, and one that opens

The macOS build had been cancelled before starting on every run since 2026-11-02: GitHub
**retired the `macos-14` image** that day, after October brownouts. It was not billing, as
first guessed. Now:

* Apple Silicon builds on `macos-15` and Intel on `macos-15-intel` (GitHub's last Intel
  image, until August 2027). Each platform's artifact has its own name.
* The app is **signed ad hoc** (`signingIdentity: "-"`). Unsigned, a downloaded Apple
  Silicon app is refused as "damaged". With an ad-hoc seal, macOS offers *Open Anyway*.
  The CI step runs `codesign --verify --deep --strict`, and `file` checks the binaries
  are for the runner's architecture.
* Tauri names the update archive `CurveVision.app.tar.gz` on both Macs, so in a release
  one would overwrite the other. Each is renamed `CurveVision_<v>_<arch>.app.tar.gz`, and
  `updater_manifest.py` now writes both `darwin-aarch64` and `darwin-x86_64`.
* The README and release notes list both `.dmg` files and say how to open the app the
  first time, with the `xattr` way out if a Mac says "damaged".

**Not verified here:** no Mac in this container. The PR's `desktop.yml` run is the test.
The first-launch wording is from Apple's current behaviour, not checked on a real Mac.

### 102 — frame tags in the editor; CurveVision beside CVAT, row by row

Asked to "ensure that all the core features in our application is similar to CVAT". Read
CVAT's annotation UI and server from a source checkout and set each core feature beside this
code, the result in **`docs/CVAT_PARITY.md`**: what is done, what is partial and what is
missing, with the gaps ranked.

The biggest gap a CVAT user would meet first was **tags**: the server stored frame tags
from the start, every tag-carrying format imports and exports them, and the editor could
neither show nor make one. Now it can:

* `frameTags.ts` lists the frame's tags (named, coloured, a model's unaccepted guess
  marked) and the labels still on offer, meaning the project's own labels, never a
  skeleton's joints, and never one the frame already carries. Tests are in
  `__tests__/frameTags.test.ts`.
* `FrameTagsPanel` in the right rail shows chips with a remove button and a *Tag this
  frame…* picker. The tags are also drawn in the canvas's top-right corner.
* `useAutosave.writeTags` writes `created_tags`/`deleted_tags` **in autosave's turn**: it
  waits for a save in flight and moves the job's version on as a save does, so a tag never
  races a shape save on the same job. Putting tags into autosave's queue would have been
  invasive and was not needed.
* `scripts/verify_frame_tags.py` (in `browser.yml`) runs 7 checks: add, a second tag,
  another frame's own tags, a box saved afterwards, remove one only, and no page errors.
  The panel was deliberately broken to write every tag to frame 0, and checks 4–6 failed.
  The version hand-off on its own is **not** isolated by the harness: with it removed, the
  re-read after each write still resynchronises the version and the box saves. The
  docstring says so.

**The WebDriver harness crashed on a dropped connection in check 1** (CI, this PR):
the failure message asked the driver for the URL again, and an argument is built whether
the check passes or not, so WebKitGTK's known reset there ended the run before any check
reported. Check 1 now reads the URL once through `until`, and every failure message uses
`WebDriver.where()`, which cannot raise. The reset itself remains the known issue below.

**Next, from CVAT_PARITY.md's ranked list:** object properties in the editor (relabel —
`engine.relabelSelection` exists with no caller — occluded, z-order, per-object hide and
lock), then video playback and brightness/contrast.

Verified: see *Verification performed*.

## Iteration 101

### 101 — a download page people can trust: releases, checksums, provenance, pinned versions

Asked to make CurveVision "easy for others to download and try, and safe". Until now a
version tag built installers into workflow artifacts that expired in 14 days; nothing made
a release, and nothing published the images.

* **`release.yml`**, on a `v*` tag: checks every declared version matches the tag
  (`scripts/release_check.py`, eleven places); builds the installers by calling
  `desktop.yml` (now `workflow_call`, its tag trigger removed so nothing builds twice; its
  concurrency group renamed, since a called workflow takes the caller's name); pushes the
  server and web images to GHCR with provenance; scans every file with ClamAV (a detection
  stops it), writes `SHA256SUMS`, attests every file, builds the update feed when the
  updater key exists, and leaves a **draft** release a maintainer publishes.
* **Pinned versions.** `server/constraints.txt` (104 packages, `scripts/update_constraints.sh`)
  is used by the Dockerfile, CI, the browser job and the desktop builds;
  `scripts/check_constraints.py` (in `check.sh` and CI) fails when a declared dependency
  has no pin. This closes iteration 100's open cause.
* **Version 1.0.0** across every manifest, and the Helm chart now pulls the published
  images by default.
* **README**: a *Download* section with the file for each platform, how to verify it, and
  what the unsigned-installer warnings look like; stale rows corrected (the mask brush and
  click-to-segment said *Planned*). `docs/RELEASING.md` for maintainers; SECURITY.md says
  what a download is and is not (not code-signed).

**Not run:** `release.yml` itself. It runs on a tag, and tagging is the maintainers' call.
It is linted (actionlint, clean) and its parts are each exercised elsewhere (installers in
`desktop.yml`, images in `ci.yml`), but the first tag is its first run: watch it, and expect
to fix something small. The installer names in the notes follow Tauri's conventions and are
not yet confirmed against a real run.

Verified: see *Verification performed*.

## Iteration 100

### 100 — review the model's least confident guesses first; a fresh install that did not start

The first step on the roadmap's *active learning*: where a model was least sure is where a
person's decision corrects it most. The suggestions panel's *Next suggestion* can now walk
the job's waiting suggestions **least confident first** (`ReviewOrder`, a checkbox shown
when more than one is waiting) instead of by frame, and the selected suggestion says how
sure the model was ("30% sure"). Ranking frames no model has been run on is still to come;
the roadmap says so.

The harness check was first written so that frame order would have passed it too — the
reviewer stood on the frame just before the least-sure one. Rearranged so the two orders
disagree, and sabotaging the order fails it.

**A fresh install did not start.** This session began in a new container, and installing
the server as CI does resolved `fastapi>=0.115` to 0.162, whose `FastAPI()` no longer takes
`openapi_version`: the server failed at import, so every install made since that release —
a new contributor's, the Docker image's, CI's next run — was broken while `main` looked
green from its last run weeks earlier. Fixed by dropping the argument (3.1.0 is the default
throughout the allowed range). **Not fixed: the cause.** Nothing pins what an install
resolves to, so the next breaking release of any dependency does this again. A lock or
constraints file for the server, used by CI, the Dockerfile and the desktop sidecar build,
is the first item under *Next best action*.

Verified: see *Verification performed*.

## Iteration 99

### 99 — model suggestions, one at a time

Suggestions could be accepted or rejected only all at once; one was "accepted" by editing
it and "rejected" by deleting it, which a reviewer had to know, and *Reject all* to drop one
bad box was not an option. The suggestions panel now decides the suggestions among the
selected objects on their own (*Accept* / *Reject*, `selectedForReview`), and *Next
suggestion* walks the job's waiting ones by frame — a track at its first frame — selecting
each and changing frame to it, round to the start after the last (`nextSuggestion`). The
endpoint always took ids; nothing sent a subset.

The browser harness found the one thing the unit tests could not: a suggestion dragged and
then decided before autosave ran **lost the drag** — the decision's refetch replaced what
the editor held. The panel now saves first (`beforeDeciding = autosave.flush`), for the bulk
buttons too. Removing that flush fails the check that covers it.

Verified: see *Verification performed*.

## Iteration 98

### 98 — click an object, a model outlines it

The roadmap's *Future* named interactive segmentation, and everything below the editor was
there: the inference contract carried `prompts` for `interactor` models, the route took
them, `persist: false` returned predictions without storing them. Nothing sent a click; the
auto-annotate panel listed interactor models only to say it could not run them.

The *Outline* tool (`I`, `canvas/interactor.ts`), after CVAT's interactors: left-click the
object, right-click (or Alt-click) what is not it; after each click the model's outline is
redrawn, and Enter keeps it as a polygon with `source = "model"` and no confidence — the
pair an accepted suggestion carries. Model answers arrive after the click that asked, and a
later one can arrive first, so requests are numbered and only the newest answer is shown;
an answer for clicks taken back (Backspace), for a cancelled outline, or for another frame
is dropped. The engine gained `setInteractor` and a `deliver` callback for results that come
later than the event that caused them, applied only while the asking tool is still active.
The editor asks the workspace's active interactor (a picker when there are several) for the
frame on screen. Prompt shape documented in `docs/API.md`: `{"positive": [[x, y]],
"negative": [[x, y]]}`, answered with a polygon.

Also: `IMPLEMENTATION_PLAN.md` still listed hosted vendors as *Planned* (iteration 63 built
them) — corrected.

Verified: see *Verification performed*.

## Iteration 97

### 97 — webhooks on the Workspace page

The last feature with an API and no way in through the application. `WebhooksPanel`, shown
to maintainers and above (the server's floor): add a webhook — its address, what it is
for, and the events, grouped as a person thinks of them (`webhooks.ts`); the signing secret
shown once beside a few lines of Python that check it; *Send a test* (the ping), answered
in place with what the receiver said and what that means; pause and resume; delete, in two
steps; and the recent deliveries, each with its outcome. The wording follows the retry
policy: a 4xx is the receiver refusing, which retrying will not change; a 5xx or no answer
is the receiver struggling. Shaped after CVAT's webhook settings.

`scripts/verify_webhooks.py` drives it in Chromium against the real server and a receiver
of its own that checks every signature: 9 checks, from the secret shown once to deletion,
and an annotator not offered the panel. Sabotaged in one build — the form sending the
default events instead of the ones ticked, and the panel shown to everyone — and the three
checks those break failed. Added to `browser.yml`.

Also: **the desktop WebDriver job failed on GitHub at `efc528f`** (iteration 96, which did
not touch the desktop). The driver dropped a long-held asynchronous script during check 9;
see *Known issues* for the cause and the harness change, verified by 6 clean runs of 6.

Verified: see *Verification performed*.

## Iteration 96

### 96 — webhooks deliver, for the first time; and only their own workspace's

Planning a webhooks screen — the last API feature with no way in through the app — began
with what the screen would show of a webhook's deliveries, and the answer was: none, ever.
`services/webhooks.py` could sign, send, retry with backoff and give up correctly, and the
`webhook.dispatch` job existed; nothing queued it. The roadmap said *Done*.

`webhooks.emit()` now queues the event once the change is committed, and only when an
active webhook wants it — a workspace without webhooks pays one query per change, not one
job. The twelve events of `WebhookEvent` are emitted where the changes happen (projects,
tasks, jobs and reviews, editor saves, issues, releases), each carrying the resource as the
API returns it; `task.completed` fires when a task's last job is accepted or it is marked
completed. Reading the creation route on the way found the second bug: `project_id` was
never checked against the workspace. Refused now (422, the same answer whether the project
is elsewhere or nowhere), and matching requires the workspace as well, for rows already
stored.

For whoever builds a receiver: `POST /webhooks/{id}/ping` sends a signed `ping` now and
answers with what the receiver said, and `GET /webhooks/{id}/deliveries` lists the recent
ones — status, error, attempts, whether it will be retried, never the body or the secret.
Shaped after CVAT's webhook ping and deliveries list.

`tests/api/test_webhooks.py`, 13 tests through the real routes against a receiver
(`webhooks.transport`, a test seam for `httpx.MockTransport`). The screen is next.

Verified: see *Verification performed*.

## Earlier iterations

Iterations **1–95** are in [`docs/iterations/ARCHIVE.md`](./docs/iterations/ARCHIVE.md) —
moved there so that orienting costs a few hundred lines rather than four thousand. Read them
when `git log` points you at an iteration number, or when you are about to build something
and want to know whether it was already tried and rejected. The lessons from them that are
still load-bearing are restated above, in *Next best action* and in
[`docs/CONTRIBUTING.md`](./docs/CONTRIBUTING.md#browser-harnesses); the archive is the
reasoning, not the rules.

**When this file's recent window grows past six iterations, move the oldest to the top of
the archive** rather than deleting it.
