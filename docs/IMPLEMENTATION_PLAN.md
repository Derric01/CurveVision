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

CurveVision is an open-source annotation and dataset platform for computer vision. It must
serve two equally first-class workflows:

```
Manual-first     upload → label schema → draw → review → export
AI-assisted      upload → run model → accept/correct → review → export
```

Neither workflow may require the other. The AI subsystem is an *accelerator layered on top
of* the annotation engine, never a precondition for it.

Constraints that shape every decision below:

1. **Self-hostable by one person on one machine.** `docker compose up` must yield a working
   platform with no cloud account, no license key, no external inference service.
2. **Horizontally scalable when needed.** The same code must run with N API replicas and M
   workers against managed Postgres/Redis/S3.
3. **No artificial capability paywalls.** Everything needed to produce a high-quality dataset
   is in the open-source core.
4. **Leverage the ecosystem.** Prefer mature OSS over bespoke code; see
   [ARCHITECTURE.md § Open-Source Building Blocks](./ARCHITECTURE.md#open-source-building-blocks--build-vs-extend-decisions).

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
├─ desktop/                  # Tauri shell (Planned)
├─ deploy/                   # docker compose, Dockerfiles, helm (Planned)
├─ docs/
└─ scripts/                  # dev scripts
```

**Rationale for a modular monolith.** CVAT's production compose file runs 18 services. That
is a *deployment* answer to what is mostly a *module boundary* problem. CurveVision keeps
one deployable API image and one worker image; the module boundaries live in Python packages
with explicit interfaces (`storage.Storage`, `ml.ModelProvider`, `formats.DatasetFormat`,
`jobs.JobQueue`). If a boundary ever needs to become a network boundary, the interface is
already the seam.

### 0.2 Development environment

* Python 3.11+, Node 20+.
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
* Transactional integrity: one session per request, commit at the service boundary, and
  `SELECT … FOR UPDATE` on annotation writes to serialise concurrent editors per job.

### 0.5 Object storage

* `Storage` protocol: `put`, `get`, `open_stream`, `delete`, `exists`, `presign_get`,
  `presign_put`.
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
  split (which CVAT also makes, and which is the correct decomposition) is what makes
  parallel annotation, review, and progress tracking tractable. CurveVision simplifies it by
  dropping CVAT's intermediate `Segment` entity: a Job *is* the segment
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
`ellipse`, `mask`, `cuboid` (*Planned*), `skeleton` (keypoints with an edge graph).

Points are stored as a flat `float[]` (`[x1,y1,x2,y2,…]`) in a JSON column. Masks are stored
RLE-encoded with a bounding box, which keeps a 4K instance mask in the low kilobytes.

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
polygon, polyline, point, ellipse; select/transform; zoom/pan; undo/redo; keyboard map),
frame navigation, label sidebar, object list, autosave to the API.
**In Progress:** mask brush tool, skeleton editing UI, track keyframe timeline UI.
**Planned:** cuboid, magnetic lasso, multi-user presence cursors.

---

## Phase 3 — Images and video · **In Progress**

* **Image datasets** — **Done.** Upload (multipart + resumable), SHA-256 dedupe, dimension
  probing, thumbnail generation, ordered frame indexing.
* **Video datasets** — **In Progress.** Videos are probed for duration/fps/dimensions; frame
  addressing is by index. Frame extraction runs as a background job producing *chunks*
  (see below). The manifest/keyframe index is implemented; GPU-free decode uses PyAV.
* **Chunked media delivery** — the browser never requests one HTTP call per frame. Frames are
  grouped into chunks of N (default 36) delivered as a single archive or fragmented MP4;
  the client keeps a small LRU of decoded chunks and prefetches ±1 chunk in the scroll
  direction. This is the single most important media decision for annotation throughput.
* **Progressive loading** — a low-resolution proxy chunk is served first so the annotator can
  start immediately; the full-resolution chunk swaps in when decoded.
* **Large-file handling** — resumable uploads (`PATCH`-by-offset), streaming hashing, and a
  hard limit expressed in config rather than code.

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

Shipped: **COCO** (instances + keypoints), **YOLO** (detection + segmentation), **Pascal VOC**,
**CurveVision JSON** (lossless native). Each declares its capabilities, and the export API
warns which annotations will be dropped *before* running (e.g. polylines are not
representable in COCO instances).

Planned: KITTI, LabelMe, Open Images, TFRecord, Datumaro bridge.

---

## Phase 5 — Review and quality control · **In Progress**

* Job state machine: `new → in_progress → submitted → (accepted | rejected → in_progress)`.
* Reviewer role, review assignment separate from annotation assignment.
* **Issues**: a positioned, threaded comment anchored to a frame and optional shape, with
  `open`/`resolved` states — the mechanism by which a reviewer sends work back.
* **Annotation history**: every write records an `AnnotationEvent` (actor, action, before/after
  diff) enabling per-object blame and rollback.
* **Quality metrics** (*In Progress*): ground-truth job comparison producing per-label
  precision/recall/IoU and a conflict list.

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

Predictions land as **suggestions**: annotations with `source = "model"` and a confidence,
shown distinctly in the editor, and individually acceptable/rejectable/editable. Accepting
flips `source` to `"model_corrected"` so dataset provenance survives to export.

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

## Phase 9 — Desktop application · **Planned**

Tauri v2 shell reusing the exact web bundle, adding:
* a local dataset picker with direct filesystem access (no upload round trip),
* an embedded local server mode for fully offline annotation,
* background sync to a remote CurveVision instance when connectivity returns.

Explicit non-goal: a second implementation of the product. The desktop app is a shell plus a
storage/transport adapter behind the same API contract.

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
| Concurrent annotators | jobs are the concurrency unit; annotation writes take a per-job row lock and use optimistic versioning |
| Heavy operations | everything slow (frame extraction, export, import, inference, quality reports) is a background job with idempotency keys |
| Resumable uploads | offset-based `PATCH` protocol |

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
