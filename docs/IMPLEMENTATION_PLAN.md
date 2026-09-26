# CurveVision Implementation Plan

> **Status of this document:** this is the engineering plan of record. It describes the
> full target platform. Individual phases carry a status marker so that the plan can be
> read as a truthful description of where the project actually is:
>
> | Marker | Meaning |
> | --- | --- |
> | **Done** | Implemented, covered by automated tests, usable today |
> | **In Progress** | Partially implemented; usable but incomplete |
> | **Planned** | Designed here, not implemented |
>
> The [README](../README.md) feature table and [ROADMAP](./ROADMAP.md) are kept
> consistent with these markers. If you find a discrepancy, the code is the truth and
> the docs are the bug — please open an issue.

---

## 0. Purpose and constraints

CurveVision is an open-source annotation and dataset platform for computer vision.

### The product, in one paragraph

**One product, two ways to run it, and neither is the lesser.** A person who wants an
annotation tool downloads an installer, double-clicks it, points at a folder of images and
starts drawing — no account, no server, no Docker, no configuration. A team that wants to
divide work, review it and keep a shared history runs the same application as a server and
opens it in a browser. The desktop app is not a cut-down offline viewer, and the web app is
not the "real" version the desktop one imitates: they are the same code, reached two ways.

This is the thing to protect when making a technical decision. If a change makes one of
those two shapes better by making the other worse, it is the wrong change.

### Two workflows, equally first-class

```
Manual-first     upload → label schema → draw → review → export
AI-assisted      upload → run model → accept/correct → review → export
```

Neither workflow may require the other. The AI subsystem is an *accelerator layered on top
of* the annotation engine, never a precondition for it.

### Two deployment shapes, one codebase

```
Desktop                                  Server
────────────────────────────────────     ────────────────────────────────────
Tauri window                             Any browser
  └─ loads http://127.0.0.1:<port>         └─ loads https://curvevision.example
        │                                        │
        ▼                                        ▼
  the same web/ bundle                     the same web/ bundle
        │                                        │
        ▼                                        ▼
  the same server/ application             the same server/ application
  SQLite · local files · inline jobs       Postgres · S3 · Redis + workers
```

The difference between the two columns is **configuration, not code**: which database URL,
which storage backend, which job queue. Every one of those was already an interface before
the desktop app existed, which is why adding it changed 42 lines of existing code.

What each shape must be allowed to be good at:

| | Desktop | Server |
| --- | --- | --- |
| Setup | Double-click an installer | `docker compose up` |
| Accounts | None. There is one person here | Real users, roles, orgs |
| Data | Stays on the machine, annotated in place | Shared, backed up, audited |
| Scale | One person's laptop | N API replicas, M workers |

### Constraints that shape every decision below

1. **Installable by a person who does not know what Docker is.** An installer must yield a
   working CurveVision with no account, no license key, no external service.
2. **Self-hostable by one person on one machine.** `docker compose up` must yield the same
   platform for a team.
3. **Horizontally scalable when needed.** The same code must run with N API replicas and M
   workers against managed Postgres/Redis/S3.
4. **No artificial capability paywalls.** Everything needed to produce a high-quality dataset
   is in the open-source core.
5. **Reuse battle-tested open source in preference to writing our own.** This is a
   first-order rule, not a fallback. Where a mature permissively licensed project has solved
   something, adapt it; write from scratch only when adapting would produce worse code.

   The reason is not effort saved. It is that **years of contributors have already hit the
   edge cases we have not thought of yet** — the container that omits a stream duration, the
   annotator who traces everything loosely, the two objects close enough that a greedy match
   strands a real correspondence. Those are not things a fresh implementation gets right; they
   are things a fresh implementation discovers in production, on somebody's dataset. CVAT in
   particular has had many hands on it, and its answers to those questions are better than
   ours would be. Every item in
   [THIRD_PARTY_NOTICES § Adapted source](./THIRD_PARTY_NOTICES.md#adapted-source) is there
   because of this rule, and the list should keep growing.

   Two conditions, both from [ADR 0007](./adr/0007-reusing-third-party-code.md):

   * **The licence obligation is the price, and we pay it in full.** Under MIT the condition
     is that the copyright notice and licence text travel with the code. Meeting it is what
     makes the reuse lawful — it is not optional, not negotiable, and not something to be
     quiet about. We name what we took, where it came from, and at which commit.
   * **Domain knowledge ports; framework glue does not.** Algorithms, format quirks and
     decoder edge cases move almost free. Django models, serialisers and viewsets moved into
     an async FastAPI codebase are a rewrite wearing reuse's clothes, and leave foreign idioms
     to maintain forever. When the upstream answer is wrapped in another framework's shape,
     take the answer and leave the shape.

   See also
   [ARCHITECTURE.md § Open-Source Building Blocks](./ARCHITECTURE.md#open-source-building-blocks--build-vs-extend-decisions)
   for the decision table and the licence audit behind it.
6. **Incremental over clean-slate.** Working code is not rewritten because a newer shape
   would be tidier. A rewrite needs a stated technical reason, and the reason goes in an
   ADR.

---

## Phase 0 — Foundations · **Done**

### 0.1 Repository structure

A single repository, modular monolith, no premature service split:

```
curvevision/
├─ server/                   # Python backend (FastAPI) — the whole API surface
│  ├─ curvevision/
│  │  ├─ core/               # config, db, security, errors, logging, pagination
│  │  ├─ domain/             # SQLAlchemy models = the domain model
│  │  ├─ schemas/            # Pydantic v2 request/response contracts
│  │  ├─ services/           # business logic; no HTTP or ORM-session ownership leaks
│  │  ├─ api/                # FastAPI routers, dependencies, error handlers
│  │  ├─ formats/            # pluggable dataset import/export plugins
│  │  ├─ media/              # image/video probing, frame extraction, thumbnails
│  │  ├─ storage/            # object-storage abstraction (local FS, S3-compatible)
│  │  ├─ ml/                 # ModelProvider abstraction + built-in providers
│  │  ├─ jobs/               # background job abstraction (inline, Dramatiq)
│  │  └─ migrations/         # Alembic
│  └─ tests/                 # pytest: unit, api, integration
├─ web/                      # React + TypeScript + Vite frontend
│  └─ src/
│     ├─ canvas/             # the annotation engine (framework-agnostic TS)
│     ├─ features/           # route-level feature modules
│     ├─ api/                # generated-ish typed API client
│     └─ ui/                 # design-system primitives
├─ sdk/python/               # `curvevision` Python SDK + `curvevision` CLI
├─ desktop/                  # the desktop shape of the same application
│  ├─ sidecar/               # PyInstaller build: server + web bundle, one executable
│  └─ shell/                 # Tauri v2 shell (Rust): supervises it, opens the window
├─ deploy/                   # docker compose, Dockerfiles, helm (Planned)
├─ docs/
└─ scripts/                  # dev scripts
```

**Rationale for a modular monolith.** Splitting an annotation platform into a dozen or more
deployed services is a *deployment* answer to what is mostly a *module boundary* problem.
CurveVision keeps
one deployable API image and one worker image; the module boundaries live in Python packages
with explicit interfaces (`storage.Storage`, `ml.ModelProvider`, `formats.DatasetFormat`,
`jobs.JobQueue`). If a boundary ever needs to become a network boundary, the interface is
already the seam.

### 0.2 Development environment

* Python 3.11+, Node 22+.
* `uv` (or `pip`) for Python dependency installation; `pyproject.toml` is the single source
  of truth for backend deps.
* `npm` workspaces are *not* used — `web/` is a single package to keep the toolchain flat.
* `scripts/dev.sh` boots infra via compose and runs API + web with hot reload.
* Tests must run with **zero external services**: the test suite uses SQLite via `aiosqlite`
  and the in-memory job queue and local-filesystem storage backends. Postgres-specific paths
  are exercised in CI's integration job against a real Postgres service container.

### 0.3 Configuration and secrets

* One `Settings` object (`pydantic-settings`), env-var driven, prefix `CURVEVISION_`.
* No module reads `os.environ` directly. `get_settings()` is `lru_cache`d and injected.
* Secrets never have insecure defaults in production mode: `CURVEVISION_SECRET_KEY` must be
  set when `environment != "development"`; the app refuses to start otherwise.
* `.env.example` documents every variable.

### 0.4 Database

* PostgreSQL 16 in production; SQLite for unit tests.
* SQLAlchemy 2.0 typed ORM (`Mapped[...]` / `mapped_column`), async engine.
* Alembic migrations; **every** model change ships with a migration.
* Portability rule: JSON columns use `JSON().with_variant(JSONB, "postgresql")`; no raw SQL
  outside migrations; UUID primary keys stored as native `uuid` on PG and `char(36)` on
  SQLite via a custom `GUID` type.
* Transactional integrity: one session per request, commit at the service boundary.
  Concurrent editors are handled by **optimistic versioning** on `job.annotation_version`
  rather than row locking: a stale write is rejected with 409 and the client reloads.
  Pessimistic locking is *Planned* only if contention proves optimistic checks insufficient.

### 0.5 Object storage

* `Storage` protocol: `put`, `append`, `get`, `stream`, `delete`, `exists`, `public_url`
  (which returns `None` when the backend cannot presign, so the caller proxies instead).
* `LocalStorage` (dev/self-host single node) and `S3Storage` (boto3, works against MinIO,
  AWS S3, Cloudflare R2, Ceph).
* All media access from the browser goes through presigned URLs or an authenticated proxy
  endpoint — never a public bucket.

### 0.6 Authentication

* Password hashing: **Argon2id** via `argon2-cffi` (winner of the Password Hashing
  Competition; `passlib` is effectively unmaintained so it is not used).
* Sessions: short-lived JWT access tokens + long-lived rotating refresh tokens stored
  hashed in the DB so they can be revoked.
* **API tokens** for SDK/CLI/CI: `cv_<id>_<secret>` displayed once, stored as a SHA-256
  hash, with optional expiry and last-used tracking.
* OIDC/SAML: **Planned** — the `AuthBackend` seam exists.

### 0.7 Authorization

* Resource hierarchy: `Organization → Project → Task → Job`, plus user-owned resources.
* Roles: `owner`, `admin`, `maintainer`, `annotator`, `reviewer`, `viewer`.
* A single in-process policy module maps `(role, resource, action) → allow/deny`, plus
  assignment-scoped rules (an annotator may only write annotations on jobs assigned to them
  unless the project allows open assignment).
* Every router dependency resolves the *resource* first, then asks the policy engine. There
  is no "check the role in the view body" pattern.
* Rationale for not using OPA: a second process and a Rego dialect for a decision that is a
  pure function of already-loaded rows is a poor trade for self-hosters. See
  [ARCHITECTURE.md](./ARCHITECTURE.md#open-source-building-blocks--build-vs-extend-decisions).

### 0.8 API conventions

* `/api/v1` prefix; resource-oriented; plural nouns; cursor-free offset pagination with a
  hard `limit` cap (`{count, next, previous, results}`).
* Errors are RFC 9457 `application/problem+json`.
* Every mutating endpoint validates with Pydantic v2; every response is a declared model.
* OpenAPI 3.1 is generated by FastAPI and is the contract the SDK and frontend consume.
* Idempotency: uploads and background job submissions accept an `Idempotency-Key` header.

### 0.9 Testing

* `pytest` + `pytest-asyncio`, `httpx.ASGITransport` (no live server needed).
* Layers: unit (pure logic: interpolation, formats, policy), API (routers via ASGI),
  integration (Postgres + real storage in CI).
* Factories over fixtures-with-magic; every test constructs its own world.
* Performance benchmarks live in `server/tests/benchmarks` and `web/src/canvas/__bench__`
  and are runnable but excluded from the default CI job.

### 0.10 Observability

* `structlog`-style JSON logging with a request-scoped `request_id` bound to every line.
* Prometheus metrics at `/metrics` (request latency histograms, job queue depth, annotation
  write counters).
* OpenTelemetry tracing: **Planned**, with the instrumentation seam in `core/observability`.

### 0.11 CI/CD

GitHub Actions:
* `backend` — ruff, mypy, pytest (SQLite) and pytest (Postgres service container).
* `frontend` — eslint, `tsc --noEmit`, vitest, production build.
* `sdk` — SDK/CLI tests.
* `docker` — build API/worker/web images on push to main.
* `licenses` — verify `docs/THIRD_PARTY_NOTICES.md` covers declared dependencies.

---

## Phase 1 — Core platform · **Done**

### Domain model

```
User ──< OrganizationMembership >── Organization
                                        │
                                        └──< Project ──< LabelDefinition ──< AttributeDefinition
                                                 │
                                                 ├──< Task ──< Asset      (image / video / frame source)
                                                 │        └──< Job        (unit of annotation work)
                                                 │
                                                 └──< DatasetVersion (immutable release)
```

Key decisions:

* **Task vs Job.** A *Task* owns media and configuration. A *Job* is a contiguous slice of a
  task's frame range assigned to one annotator, with its own state and review status. This
  split is the correct decomposition and what makes parallel annotation, review and progress
  tracking tractable. CurveVision simplifies the usual shape by dropping the intermediate
  `Segment` entity that platforms in this space tend to carry: a Job *is* the segment
  (`start_frame`/`stop_frame` live on the job), which removes a join from every hot path.
* **Labels live on the project** by default and are inherited by tasks, so a schema change
  propagates. A task may be created label-standalone for one-off datasets.
* **Attributes** are typed (`select`, `radio`, `checkbox`, `text`, `number`) with a
  `mutable` flag; mutable attributes may change per-frame within a track.
* **Assets** are content-addressed by SHA-256, so re-uploading the same file across tasks
  stores one blob.

### Deliverables

Users, org membership, projects, tasks, jobs, labels, attributes, assets, permissions —
all with API endpoints, migrations and tests.

---

## Phase 2 — Annotation engine · **In Progress**

### Data model

Three annotation kinds, all scoped to a job:

| Kind | Meaning | Frame semantics |
| --- | --- | --- |
| `Shape` | one geometric object on one frame | single `frame` |
| `Track` | one object across frames | ordered `TrackShape` keyframes + interpolation |
| `Tag` | frame-level or job-level classification | single `frame` or null |

Geometry types: `rectangle`, `rotated_rectangle`, `polygon`, `polyline`, `points`,
`ellipse`, `mask`, `cuboid` (a 2D wireframe box — front face plus a depth offset, in CVAT's
own on-disk point order so it round-trips through `cvat_xml`; not the 3D/point-cloud kind),
`skeleton` (keypoints with an edge graph).

Points are stored as a flat `float[]` (`[x1,y1,x2,y2,…]`) in a JSON column. Masks are stored
RLE-encoded with a bounding box, which keeps a 4K instance mask in the low kilobytes. The
encoding is written down once, in `formats/rle.py`: runs alternate **starting with
background**, read row-major inside the box, summing to `width * height`. The element they
serialise into matches CVAT's (`rle`, `left`, `top`, `width`, `height`, with the spans
inclusive), which was read out of CVAT's own serialiser rather than assumed.

`TrackShape` carries `outside` (object left the frame), `occluded`, and `keyframe` flags.
Interpolation between keyframes is linear for rectangles/points/ellipses and, for polygons
and polylines, resamples both endpoints to a common vertex count before interpolating —
which avoids the "shape explodes when vertex counts differ" failure that naive
implementations hit.

### Editor architecture (`web/src/canvas`)

Deliberately **not** a React component tree. The canvas is a plain-TypeScript engine that
React mounts and talks to via a small command API:

```
  Store (annotations, normalized)
        │  read model
        ▼
  Scene ──► SpatialIndex (R-tree, rbush)   ← hit-testing, viewport culling
        │
        ▼
  Renderer (Canvas2D, layered, dirty-rect)
        ▲
        │ input events (throttled to rAF)
  ToolController (state machines: select, rect, polygon, polyline, point, ellipse, pan)
        │
        ▼
  CommandStack (undo/redo, coalescing)
```

* **Rendering**: layered canvases (media / shapes / overlay). Only the overlay re-renders on
  hover and drag; the shape layer re-renders on data change or viewport change.
* **Culling**: the R-tree returns only shapes intersecting the viewport, so a 100k-annotation
  job renders in time proportional to what is on screen, not to dataset size.
* **Hit testing**: R-tree query → precise geometric test, so picking is O(log n).
* **Undo/redo**: a command stack of inverse-pair operations, with coalescing for drags.
* **Autosave**: dirty-set flushing on an interval and on frame change, with a local
  IndexedDB write-ahead buffer so a browser crash or network drop does not lose work.

### Interaction requirements

Undo/redo, zoom/pan (wheel + space-drag), keyboard-first tool switching, selection and
multi-select, copy/paste/duplicate, delete, per-label visibility and lock, opacity control,
filtering, frame navigation with prefetch, and snapping to nearby vertices.

### Status

**Done:** engine core (scene, R-tree index, renderer, command stack, tools for rectangle,
polygon, polyline, point, ellipse, mask brush; select/transform; zoom/pan; undo/redo;
keyboard map), frame navigation, label sidebar, object list, autosave to the API. **Done
since:** the skeleton tool (joints placed in the label's declared order, a joint nobody can
see recorded as invisible rather than dropped, and the bones drawn), track keyframe editing,
the mask brush (paints a filled disc, erases with right-click or Alt, edits the selected
mask instead of starting a new one, and deletes the object outright when a stroke erases it
down to no pixels), and the cuboid tool (drag the front face, then move and click to set the
depth; the 8 corners land in CVAT's own on-disk order, so a box drawn here exports through
`cvat_xml` as the same box real CVAT would write).

**Fixed since, and worth knowing about:** editing a *tracked* object on the canvas broke
saving for the rest of the session. A tracked object is drawn under its track's id, and
autosave sent a drag of one as an `updated_shapes` entry naming a shape that does not
exist — a 404 that failed the whole batch, which autosave put back and retried, so nothing
drawn afterwards reached the server either. Deleting one sent the track's id as a shape,
which matched nothing. Now a drag, a vertex edit or an occlusion toggle on a tracked object
records a **keyframe on that frame** carrying the new geometry, as CVAT does
(`Track.savePoints`); a relabel applies to the whole track; and Delete deletes the track.
Those edits travel in the same autosave batch as shape edits, as `updated_tracks` and
`deleted_tracks`, placed on the track as the server holds it at flush time. An edit that
leaves an object exactly where the track already shows it writes nothing, because an undo
re-emits every object on the frame. The same sweep found that **undoing a deletion before
the next save lost the object anyway**: the undo reaches autosave as an update while the
deletion is still queued, both were sent, and the server applies deletions last — for a
never-saved object the undo was dropped outright. An update for an object whose deletion is
queued now cancels it. `scripts/verify_track_canvas_edit.py` drives all of it.
**Planned:** magnetic lasso, multi-user presence cursors.

---

## Phase 3 — Images and video · **In Progress**

* **Image datasets** — **Done.** Upload (multipart + resumable), SHA-256 dedupe, dimension
  probing, thumbnail generation, ordered frame indexing.
* **Video datasets** — **In Progress.** A video task is annotatable end to end: upload a
  video, get a task with many frames, and the editor is served each frame as an image.
  `curvevision/media/video.py` is **adapted from another project's MIT-licensed source**
  under [ADR 0007](./adr/0007-reusing-third-party-code.md), keeping its upstream copyright
  header and recorded in
  [THIRD_PARTY_NOTICES](./THIRD_PARTY_NOTICES.md#adapted-source). What that bought, and why
  it was not worth re-deriving: frames are addressed in **decode order rather than by
  seeking**, because seeking lands on the nearest keyframe and a frame number must identify
  the same picture every time an annotation refers to it; frame counts are **measured by
  decoding** because `stream.frames` is zero in many containers and wrong in others; and
  two edge cases real files need — the `DURATION` metadata fallback for containers that
  omit a stream duration, and rotation metadata, without which phone video is annotated
  sideways.

  **Frame counts are corrected after upload.** A task is created with an estimate, because
  counting means decoding the whole file and that cannot happen inside an HTTP request.
  Where the container declares a count the estimate is exact; where it does not — Matroska,
  routinely — it becomes `int(duration × rate)`, and truncation loses a frame: 7 frames at
  3 fps gives `int(2.333 × 3) = 6`, and the last frame is then never offered, never
  labelled and never exported. `media.probe_task`, enqueued after any upload or import that
  added video, decodes to count, corrects the task and its jobs, and discards the chunks the
  renumbering invalidated. It declines entirely — rather than applying half of itself — on a
  task that already carries annotations, because repartitioning frames under an annotator
  would orphan their work.

  **And when it cannot correct one, it says so.** `Asset.frame_count_exact` records whether
  a count was established by decoding or is still the upload-time guess, so the two cases
  the job cannot fix — a task that already carries work, and a file that is truncated, moved
  or undecodable — stop being invisible. `GET /tasks/{id}/media` reports
  `frame_count_exact` and names the files still estimated; the task page turns that into a
  warning with a **Recount frames** button (`POST /tasks/{id}/media/recount`). The flag also
  stops the job re-decoding what it already counted: adding three photographs to a task
  holding a counted two-hour clip used to schedule a full decode of the clip.

* **Chunked media delivery** — **Done** on the server. Frames are grouped into chunks of N
  (default 36), decoded in one pass and stored as one ZIP of JPEGs, recorded as a
  `MediaChunk`. `GET /tasks/{id}/chunks/{n}` serves the archive; the frame endpoint takes
  its frame out of a built chunk and builds the chunk when it misses, because reaching
  frame *n* costs *n* decodes either way and finishing the pass is nearly free.

  Measured on a 600-frame 640×480 clip, counting every picture the decoder produced:

  | Access pattern | Frames decoded, before | after | Wall clock |
  | --- | --- | --- | --- |
  | Step through frames 0–35 | 666 | **36** | 0.84s → **0.53s** |
  | Step through frames 400–435 | 15,066 | **900** | 3.62s → **0.78s** |
  | Scrub: every 100th frame | 1,506 | 1,620 | 0.40s → **0.99s** |

  The third row is the honest cost and is not a rounding error: a *sparse* scrub pays to
  build a chunk it mostly does not use, and each build JPEG-encodes 36 frames rather than
  one. It is the right trade because annotation is overwhelmingly sequential — that is what
  the next-frame key and the timeline do — and because those chunks make the work that
  follows the scrub free. Per-frame decoding remains the fallback for every case a chunk
  cannot serve, so a video is never *unservable*, only slower.

  **The editor fetches chunks too.** `ChunkCache` keeps a three-archive LRU, shares one
  request between frames of the same chunk, and prefetches the neighbouring chunks so
  crossing a boundary does not stall. Reading the archive needs no dependency: the server
  writes `ZIP_STORED`, so `web/src/media/storedZip.ts` is container parsing and nothing is
  compressed — and it refuses anything it does not understand rather than guessing, which
  puts the caller back on the single-frame endpoint.

  Measured in Chromium against the packaged server, stepping through 36 frames:

  | | Chunk requests | Per-frame requests |
  | --- | --- | --- |
  | Chunked | 2 | 1 |
  | Chunking off (`frames_per_chunk = 0`) | 0 | 36 |

  The one remaining per-frame request is the first frame, asked for before
  `/tasks/{id}/media` has answered. That is wanted: the first picture appears without
  waiting for 36, and it costs a round trip rather than a decode, because the server builds
  the chunk to answer it. `scripts/verify_chunked_frames.py` drives both rows.
* **Progressive loading** — *Planned*. A low-resolution proxy chunk served first so the
  annotator can start immediately, with the full-resolution chunk swapping in when decoded.
* **Large-file handling** — a size limit expressed in config rather than code is **Done**.
  Resumable uploads are **Done**: `POST /tasks/{id}/uploads` declares a
  filename and size (validated against the same size/extension rules a direct upload uses,
  before any bytes exist), `PATCH .../{id}` appends a chunk at the offset the client
  believes it is resuming from -- a mismatch is a 409 naming the real offset, so a chunk
  resent after a dropped response cannot duplicate bytes -- and `POST .../{id}/complete`
  reads the assembled bytes back and hands them to the exact same dedupe/probe path
  (`_ingest_bytes`) a direct multipart upload uses, so the two can never validate or store a
  file differently. `Storage.append` and both backends already existed for this; only the
  service functions and routes were missing. The SDK (`upload_resumable`) and CLI
  (`task upload-resumable`) read a local file in fixed-size pieces and can resume a session
  by id. The task page's "Upload media" panel -- new this iteration, and not gated to the
  desktop build the way local-import is -- routes a file at or over 20 MB through the same
  chunked path with a progress bar, and everything smaller through the pre-existing
  single-request `uploadAssets` batch call; resuming after a crash or a reload works by
  re-selecting the same file, which is matched by name and size to a session id remembered
  in `localStorage`.

---

## Phase 4 — Dataset import/export · **Done (4 formats)**

A plugin registry keyed by format id:

```python
class DatasetFormat(Protocol):
    id: str; name: str; version: str
    capabilities: FormatCapabilities   # which geometry/annotation kinds survive a round trip
    def export(self, ds: DatasetView, sink: ExportSink) -> None: ...
    def import_(self, source: ImportSource, ctx: ImportContext) -> ImportResult: ...
```

`DatasetView` is a read-only, streaming projection over jobs/frames/annotations, so exporting
a 500k-image dataset never materialises it in memory.

Shipped, eleven: **CurveVision JSON** (lossless native), **CVAT XML 1.1**, **COCO**
(instances + keypoints), **YOLO** (detection + segmentation), **YOLO OBB**, **YOLO Pose**,
**YOLO Classification**, **Pascal VOC**, **KITTI**, **MOTChallenge**, and **segmentation
mask** (indexed PNG). Each declares its capabilities, and the export API warns which
annotations will be dropped *before* running (e.g. polylines are not representable in COCO
instances).

**A capability declaration is a promise, and one of them was not kept.** `cvat_xml` listed
`ShapeType.MASK` and said in its notes that masks were carried; its writer emitted the two
corners of the bounding box and dropped every pixel, into a file that parsed cleanly.
`segmentation_mask` — the format whose entire purpose is masks — matched no mask shape and
exported a frame of them as background. Both now write the real thing, `cvat_xml` reads it
back, and `formats/rle.py` states the encoding once so nothing has to guess it.

Planned: LabelMe, Open Images, TFRecord, Datumaro bridge.

---

## Phase 5 — Review and quality control · **In Progress**

* Job state machine: `new → in_progress → submitted → (accepted | rejected → in_progress)`.
  **Done, and reachable from the editor**: the annotator's Submit and the reviewer's
  accept/send-back are both in the rail. `POST /jobs/{id}/review` and the client's
  `reviewJob` had existed from the first iterations with nothing calling them, so the
  machine ran only for the SDK, the CLI and curl. Sending a job back **requires a reason**
  in the UI, which the endpoint then files as an issue on the job — the API still permits a
  commentless rejection for a script that has said why elsewhere; a person clicking a button
  does not get that option, because work returned unexplained is the failure the comment
  support exists to prevent. Submit is disabled on an `accepted` job rather than sending the
  `accepted → submitted` transition the server refuses with a 409 nothing rendered.
  `scripts/verify_job_review.py` drives submit → send back → read the reason as an issue →
  resubmit → accept against the packaged application.
* Reviewer role, review assignment separate from annotation assignment. **Done**: the task
  page's job rows carry an annotator picker and a reviewer picker, filled from the
  organization's members and narrowed to the ranks the policy engine will accept for each —
  offering somebody the reviewer slot when `ROLE_FLOOR` will refuse them is offering a 403.
  Two server bugs surfaced building it, both fixed with tests written to fail first:
  `update_job` read an omitted field and an explicit `null` identically, so a job could be
  assigned and **never unassigned** through the API; and because the `assignee`/`reviewer`
  relationships are eagerly loaded under `expire_on_commit=False`, the response carried
  whoever held the job *before* the write — a job gaining its first assignee came back as
  `assignee: null`.
* A reviewer's **queue**. **Done**: `GET /jobs` gained `reviewing=true` beside `mine=true`
  — two different questions, *what am I drawing* and *what is waiting on me to check*, each
  narrowing independently — and a `reviewer_id` parameter mirroring the `assignee_id` the
  listing always accepted. The My work page asks both and splits the answer: only a
  `submitted` job can be reviewed (the server's rule, imported from `review.ts` rather than
  restated), and everything else named to that reviewer is listed apart from it, so a
  heading never counts half-drawn work as waiting. Each row names its **task**, which
  `GET /jobs` had eagerly loaded since it was written without ever returning: a queue
  spanning every project cannot be read as "Job #2, frames 0–1". Indexed to match —
  `ix_job_reviewer_state` is `ix_job_assignee_state` with one column changed, since the
  queries are the same shape. `scripts/verify_review_queue.py` drives it against the
  packaged application.
* **Changing a label schema after the project exists.** **Done**: the project page's Label
  schema panel adds a label and removes one. `POST`/`DELETE /projects/{id}/labels` and the
  policy that gates them had existed since the initial schema with **nothing calling
  `api.createLabel`**, so a project that turned out to need another class could only get one
  from the SDK, the CLI or curl — and label schemas are not knowable in advance, which is
  why those endpoints were written. A label sent without a position now goes to the **end**
  of the schema: it used to take position 0 like every other unpositioned label, and the
  listing orders by `(position, name)`, so `van` added to a `car`/`pedestrian` project
  appeared first. Deleting is safe to offer because `delete_label` refuses a label
  annotations still reference rather than cascading; the panel shows that refusal. A label
  is also **renamed and recoloured** in place, through `labelToPayload`, which rebuilds the
  whole label from the one the server reported: `PUT` is a replace, so a form posting only
  the fields it changed would reset the label's position, lift its shape restriction and
  delete its attribute definitions along with the schema validating values already stored on
  annotations. Two more defects surfaced from writing those tests first: `update_label`
  never checked for a **duplicate name**, so `create_label`'s rule could be walked around
  with a rename — and exports key classes by name, so two labels called `car` do not stay
  cosmetic; and the `PUT` **response reported attributes it had just deleted**, because a
  re-read after commit under `expire_on_commit=False` skips eager loads for an instance
  already in the session (`populate_existing=True`). The three routes had no tests at all
  before this; they have fifteen now.

  **A label's attributes are edited from the same form** — added, renamed, given more
  options or a default, removed. Values are stored on each annotation keyed by attribute
  *name*, and `validate_attributes` refuses an undeclared key rather than dropping it, so
  until this the `PUT` accepted a removal or rename and every annotation carrying the old
  value was then refused on its **next save** — an autosave failing on a box the annotator
  had only moved. `update_label` now refuses such an edit first. For an attribute that
  already exists the rules are CVAT's (`LabelSerializer._update_attribute`, read for the
  convention): its type and `mutable` flag are fixed and a select or radio may gain options
  but not lose them. A rename or removal is refused only while an annotation of the label
  records a value under the name — CVAT allows both because it stores values by attribute
  id — and a new or newly-required attribute with no default is refused while annotations
  lack it. Whether a value is recorded is read in Python, streamed, rather than by a JSON
  path whose syntax differs between the two backends. Duplicate attribute names, a
  same-name replacement and two attributes trading names were each a 500 from
  `uq_attribute_name` and are not now, and a default the attribute would itself refuse is a
  422 at the schema. `scripts/verify_attribute_editor.py` drives it, ending in the editor:
  after every edit, the annotator's box is dragged and saved and keeps its recorded value.
  An attribute's **value** is set from the editor (iteration 54): an Attributes panel on the
  right rail for the one selected object, one control per attribute by type, through an
  undoable `engine.setSelectionAttribute`. For a tracked object a mutable value goes on the
  keyframe at that frame and any other on the track, and the editor now shows a tracked
  object's values the way the server exports them — the track's merged with those held
  since the last keyframe, where before it showed the track's alone. A keyframe added with
  `K` or by a drag starts from the values in force rather than from none. The form still
  does not offer `required`: a required attribute with no default is still refused on the
  first save of a newly drawn shape, before anybody could set it.
  `scripts/verify_attribute_values.py` drives it.
* **Merging overlapping jobs** (**Done**): a task with `overlap > 0` hands the same frames to
  two annotators so a track can cross a job seam. Export reconciles those frames instead of
  concatenating them — `services/merge.py`, adapted from the upstream design. Two shapes are
  one object when label, shape type and geometry (≥ 0.75 IoU, exact rather than
  bounding-box) all agree; pairing is an **optimal** assignment rather than greedy, because
  greedy strands a real correspondence when two objects sit close together and then ships
  the duplicate. Different labels are never merged: that is a disagreement to review, not a
  duplicate to collapse. Track identity is unified across the seam, which is the whole point
  of buying the overlap, and track ids are allocated per task so unrelated tracks in
  different jobs cannot collide.
* **Issues**: a positioned, threaded comment anchored to a frame and optional shape, with
  `open`/`resolved` states — the mechanism by which a reviewer sends work back.
* **Annotation history**: every write records an `AnnotationEvent` (actor, action, before/after
  diff) enabling per-object blame and rollback.
* **Quality metrics** (**Done**): `POST /tasks/{id}/ground-truth` adds the job that
  holds a task's answer key; `POST /jobs/{id}/quality` scores an annotation job against it
  and stores a `QualityReport` with per-label precision/recall/F1/mean IoU and a conflict
  list classified into *missing*, *extra*, *wrong label* and *poor overlap*. The editor
  shows the report in its right-hand panel — three scores, a per-label breakdown ordered
  worst-first, and the conflicts as a list where clicking one seeks to its frame, because
  **a conflict is a place, not a statistic**. Five things it gets right and a naive version
  would not:

  * **A box too loose to count as the object costs precision, not only recall.** Counting
    it against recall alone means an annotator who draws everything sloppily scores
    precision 1.0 — the flattering failure this feature exists to prevent.

  * **Only frames the ground truth covers are scored.** A ground truth over frames 0-49 is
    evidence about frames 0-49; counting the annotator's unchecked frame 300 as correct
    inflates the score in the direction that makes a team trust bad data.
  * **Tracks are flattened to their interpolated position on every frame**, so an annotator
    working with tracks and a reviewer working with shapes score the same.
  * **The ground truth's annotations need reviewer rank to read** (or assignment to that
    job). A score an annotator could have copied measures nothing.
  * **A report records the job's `annotation_version`**, so a score taken before the
    annotator fixed everything is shown as stale rather than as current. Without it the
    failure runs the wrong way: the old F1 stays on screen and a reviewer rejects a job on
    a measurement of work that no longer exists.

  Geometry is exact rather than bbox-approximate: polygons are clipped
  (Sutherland–Hodgman) and measured by the shoelace formula, and matching is greedy on
  descending IoU, which is COCO's rule. The task page creates the ground-truth job and
  shows each job's latest F1; the editor shows the report.

  **It runs inline, and that is now a measured decision rather than an assumption.** This
  plan said for several iterations that a very large ground truth would force the comparison
  onto the unused `quality` queue. Measuring it
  (`tests/benchmarks/test_comparison_scale.py`) showed the worry was aimed at the wrong
  dimension: frames are free — 100,000 of them with two objects each score in 4.6 seconds,
  perfectly linear — while *objects per frame* was quadratic, because the matcher ran a full
  polygon clip for every (annotated, truth) pair on a frame, including the overwhelming
  majority nowhere near each other. Two hundred objects a side over a hundred frames took
  **34 seconds**. An exact axis-aligned bounding-box rejection ahead of the clip took the
  same case to **0.7 seconds**, and the busy-street case — 200,000 shapes a side over 10,000
  frames — from 37 seconds to 4.4. Throughput is now roughly flat at ~40,000 shapes/s
  whatever the crowding, so the request path is comfortable and a queue would only have
  hidden the quadratic.

---

## Phase 6 — AI-assisted annotation · **In Progress**

```python
class ModelProvider(Protocol):
    id: str
    def list_models(self) -> list[ModelDescriptor]: ...
    def infer(self, model: str, request: InferenceRequest) -> InferenceResult: ...
```

`ModelDescriptor` declares a `kind` (`detector`, `segmenter`, `interactor`, `tracker`,
`classifier`, `ocr`) and the label space it emits, so the platform can build a mapping UI
from model labels to project labels without hard-coding any model.

Providers:
* `http` — **Done.** Points at any endpoint speaking the CurveVision inference schema; this
  is how self-hosters attach their own SAM/YOLO/DETR service without CurveVision depending on
  torch.
* `builtin` — **Planned.** Optional ONNX Runtime provider for a few small bundled models.
* Hosted vendors — **Planned**, as separate optional packages so the core never imports a
  vendor SDK.

Running a model over a job — **Done.** The editor's right rail has an auto-annotate panel:
pick a model, and for an open-vocabulary one type the classes to look for. It states before
the run what it will search for and where those classes came from, and names any that no
project label can receive, since the server can only report that afterwards. Driven end to
end against a real model server by `scripts/verify_auto_annotate.py`. The kinds that need a
different interaction (`interactor`, `tracker`) are listed with the reason rather than hidden.

Predictions land as **suggestions**: annotations with `source = "model"` and a confidence,
drawn dashed in the editor and editable or deletable like any other annotation — **Done**.
They can be accepted or rejected in bulk from the editor's review panel — **Done**.
Accepting keeps the annotation and keeps `source = "model"`, clearing only the confidence,
so the dataset still records that a machine drew it and a human agreed; rejecting deletes
it. That pair is what the canvas, the object list and the panel all read to tell a
suggestion awaiting review from one already accepted. Editing one flips `source` to
`"model_corrected"`, so dataset provenance survives to export either way. Per-object
accept/reject controls are **Planned**: an individual suggestion is accepted today by
editing it and rejected by deleting it.

---

## Phase 7 — Dataset management · **In Progress**

`Dataset → Version → Annotation → Review → Release`

* `DatasetVersion` is an immutable snapshot: a content hash over the annotation set plus a
  frozen manifest. Once released it cannot be mutated, only superseded.
* Statistics: annotation counts, class distribution, per-annotator throughput, frames with
  zero annotations (a common dataset bug).
* Export jobs are asynchronous with a downloadable artifact and a TTL.
* Import history records what was imported, by whom, with what conflict policy.

---

## Phase 8 — APIs and developer ecosystem · **In Progress**

* **REST API** — **Done**, OpenAPI 3.1 at `/api/v1/openapi.json`.
* **API tokens** — **Done**.
* **Webhooks** — **In Progress**: HMAC-SHA256 signed deliveries with retry/backoff for
  project and task lifecycle events.
* **Python SDK** — **Done** for core resources; typed, `httpx`-based, sync client.
* **CLI** — **Done** for auth, project/task creation, upload, export.

---

## Phase 9 — Desktop application · **In Progress**

Not an add-on: one of the two shapes the product ships in ([Two deployment shapes](#two-deployment-shapes-one-codebase),
[ADR 0006](./adr/0006-one-codebase-two-shapes.md)).

* **Local mode** — **Done.** `local_mode` + `app_data_dir` in Settings. The server resolves
  the per-OS application directory, migrates a SQLite database there with Alembic (not
  `create_all` — a desktop user opens v2 against a v1 database and must not lose it),
  provisions one local account and workspace on first launch, mints a fresh API token each
  launch and revokes the previous one, and prints a single line of JSON on stdout.
* **Loopback-only binding** — **Done.** `127.0.0.1` on an OS-assigned port. This is what
  makes an auto-provisioned password-less account safe, and it is verified by a test that
  connects to the host's non-loopback address and expects refusal. An unauthenticated
  request is still 401.
* **Annotating in place** — **Done.** `MediaBlob.source_path` records where a file *is*
  rather than copying it; `POST /tasks/{id}/local-import` walks a folder in a worker thread,
  sorts by path so frame numbers are reproducible, and reports unreadable files in `skipped`
  rather than failing the import. The orphan-blob collector never deletes a file it did not
  write. Every route in `api/v1/local.py` 404s unless `local_mode`.
* **Serving the editor from the server** — **Done.** `web_root` with an SPA fallback, so the
  desktop window is same-origin with its API and `curvevision-local` alone opens a complete
  CurveVision in a browser.
* **Packaged server** — **Done.** One PyInstaller executable (~38 MB; ~1.5–2.1 s spawn to
  handshake on Linux). Migrations ship as *data*, because Alembic reads them from disk.
  `desktop/sidecar/build.py` builds and then smoke-tests what it built.
* **Tauri shell** — **Done.** ~450 lines of Rust: process supervision, token injection
  before page load, native dialogs, menus. Killed on every exit path, and
  `--exit-with-parent` makes the server stop on end-of-file when the shell cannot run its
  own handler (crash, force quit, `kill -9`) — verified.
* **Desktop-aware frontend** — **Done** for authentication. `web/src/desktop.ts` validates
  the injected `window.__CURVEVISION__`, the API client prefers that token over any
  stored session, and a failed restore in desktop mode shows a recoverable error rather
  than a sign-in form nobody can use. Multi-user chrome is hidden. The shape is pinned
  from both sides: a Rust test asserts the injected JSON keys, and the TypeScript tests
  parse the same fixture.
* **Open-folder flow** — **Done**. The task page offers *Add media from this computer* in
  the desktop build and not in a browser, opening the operating system's own dialog and
  posting the chosen path to `/tasks/{id}/local-import`; File ▸ Open Folder… (Cmd/Ctrl+O)
  does the same. The result is reported honestly — the count imported, and the per-file
  reasons when the server skipped something, which it does rather than failing a folder over
  one corrupt file. `scripts/verify_local_import.py` drives the whole thing in a browser
  against the packaged server. One seam is genuinely uncovered and says so: `invoke()`
  across the Tauri IPC bridge, because Playwright drives Chromium and not the webview — see
  [ADR 0008](./adr/0008-calling-the-desktop-shell.md).
* **Signed installers** — *Planned*. `.dmg`, `.msi`, `.AppImage` built per platform in CI;
  PyInstaller does not cross-compile, so this needs one runner per OS.
* **Auto-update** — *Planned*.
* **Pointing the desktop app at a shared team server** — *Planned*. The application is
  already origin-agnostic; this is UI work, not architecture.

Explicit non-goal, unchanged: a second implementation of the product. The shell supervises a
process and opens dialogs. Adding the whole desktop application changed **1,460 lines added
and 42 removed** in existing code.

---

## Phase 10 — Collaboration · **In Progress**

Organizations, membership roles, task assignment, job assignment, review workflow, issue
threads and activity history are implemented (Phase 1/5). Planned: team sub-groupings,
per-project role overrides, live presence in the editor.

---

## Phase 11 — Performance · **In Progress**

Design targets and how they are met:

| Target | Mechanism |
| --- | --- |
| Millions of assets per instance | assets are rows + object storage keys; no directory scans; all listings paginated and indexed |
| 100k annotations in one job | R-tree culling client-side; server returns annotations per-job, streamed, with an optional frame-range filter |
| Long videos | chunked frame delivery + client LRU + prefetch; never a per-frame request |
| Concurrent annotators | jobs are the concurrency unit; annotation writes use optimistic versioning on `job.annotation_version` (a stale write is rejected, never merged) |
| Heavy operations | everything slow (frame extraction, export, import, inference, quality reports) is a background job with idempotency keys |
| Resumable uploads | offset-based `PATCH` protocol — **Done** end to end: API, SDK, CLI and the web upload panel; see Phase 3 |

Benchmarks live in `server/tests/benchmarks/` (annotation write/read at 1k/10k/100k) and
`web/src/canvas/__bench__/` (index build, viewport query, render frame time).

---

## Phase 12 — Production hardening · **In Progress**

* Rate limiting (Redis token bucket, per-token and per-IP) — **In Progress**.
* Audit log of security-relevant actions — **Done** (`AuditEvent`).
* Security headers + strict CORS allow-list — **Done**.
* CSRF — not applicable to the token/Bearer API; the cookie refresh flow uses
  `SameSite=Strict` plus a double-submit token — **In Progress**.
* Input validation — **Done** (Pydantic v2 everywhere, geometry validated against frame
  bounds).
* File validation — **Done**: extension + magic-byte sniffing + size caps; a
  `ContentScanner` hook exists for ClamAV/vendor scanners (**Planned** implementation).
* Structured logging and metrics — **Done**; tracing — **Planned**.
* Migrations — **Done**; backup/DR runbook — see [OPERATIONS.md](./OPERATIONS.md).

---

## Execution order and definition of done

Each phase is done when: migrations exist, services have unit tests, endpoints have API
tests, the OpenAPI schema is regenerated, docs updated, `ruff`/`mypy`/`eslint`/`tsc` clean,
and the status marker in this file and the README reflect reality.
