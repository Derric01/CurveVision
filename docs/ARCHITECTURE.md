# CurveVision Architecture

CurveVision is a **modular monolith**: one API application, one worker application, one web
application, and the smallest set of stateful dependencies that can support the product.
Module boundaries are enforced in code by explicit interfaces, not by network hops.

It ships in **two shapes — a desktop application and a server — and they are the same
code**. See [§1.1](#11-two-shapes-one-codebase). That constraint is load-bearing: it is why
storage, the job queue, the database dialect and the dataset formats were interfaces from
the first commit rather than direct calls to S3, Redis, PostgreSQL and COCO.

---

## 1. System overview

```
                    ┌─────────────────────────────────────────────┐
  Browser ─────────▶│  web/  React + TS + Vite                    │
  Desktop (Tauri) ─▶│  ├─ features/   route modules               │
                    │  └─ canvas/     annotation engine (no React)│
                    └───────────────────┬─────────────────────────┘
                                        │ HTTPS  /api/v1  (OpenAPI 3.1)
  SDK / CLI / CI ───────────────────────┤
                                        ▼
                    ┌─────────────────────────────────────────────┐
                    │  server/  FastAPI (async)                   │
                    │  api/      routers · deps · error handlers  │
                    │  services/ business logic (no HTTP types)   │
                    │  domain/   SQLAlchemy 2.0 typed models      │
                    │  ┌───────────┬──────────┬────────┬────────┐ │
                    │  │ storage/  │ formats/ │  ml/   │ jobs/  │ │
                    │  │ Storage   │ Dataset  │ Model  │ Job    │ │
                    │  │ protocol  │ Format   │Provider│ Queue  │ │
                    │  └───────────┴──────────┴────────┴────────┘ │
                    └───┬──────────────┬──────────────┬───────────┘
                        │              │              │
                 ┌──────▼─────┐  ┌─────▼─────┐  ┌─────▼──────────┐
                 │ PostgreSQL │  │   Redis   │  │ S3 / MinIO /   │
                 │            │  │ cache +   │  │ local FS       │
                 │            │  │ broker    │  │                │
                 └────────────┘  └─────┬─────┘  └────────────────┘
                                       │
                                 ┌─────▼─────────────────────┐
                                 │ worker/  Dramatiq         │
                                 │ import · export · media   │
                                 │ inference · webhooks · qa │
                                 └───────────────────────────┘
                                       │  HTTP inference contract
                                 ┌─────▼─────────────────────┐
                                 │ Your model server         │
                                 │ (Triton/TorchServe/…)     │
                                 └───────────────────────────┘
```

Everything in the middle box ships as **one image**. The worker is the same image with a
different entrypoint.

### 1.1 Two shapes, one codebase

The diagram above is the *server* shape. The desktop application is the same middle box with
different things plugged into its four seams, running as a child process of a native window:

```
  ┌──────────────────────────────────────────────────────────────────┐
  │  Tauri shell  (desktop/shell)              457 lines of Rust     │
  │  · spawns the server below and kills it on exit                  │
  │  · reads one line of JSON: { url, token, data_dir, version }     │
  │  · opens the window at that url, injects the token before load   │
  │  · native folder / file dialogs                                  │
  └───────────────────────────────┬──────────────────────────────────┘
                                  │ spawns, loopback only
  ┌───────────────────────────────▼──────────────────────────────────┐
  │  curvevision-local  (desktop/sidecar)     one 38 MB executable   │
  │                                                                  │
  │   the same server/ application  +  the same web/ bundle          │
  │   ┌───────────┬──────────┬────────┬────────┐                     │
  │   │ storage/  │ formats/ │  ml/   │ jobs/  │  ← the same seams   │
  │   │ local FS  │  same    │  same  │ inline │                     │
  │   └───────────┴──────────┴────────┴────────┘                     │
  │                     SQLite, in the app data directory            │
  └──────────────────────────────────────────────────────────────────┘
```

**What actually differs between the two shapes:**

| | Desktop | Server |
| --- | --- | --- |
| Database | SQLite file in the app data dir | PostgreSQL |
| Storage | Local filesystem, and files annotated *in place* | S3 / MinIO / local |
| Job queue | `inline` — runs in-process | `dramatiq` — Redis + worker processes |
| Web bundle | Served by the app itself (`web_root`) | Served by nginx in front |
| Sign-in | None; one local account, token injected by the shell | Real accounts, roles, orgs |
| Reachability | `127.0.0.1` on an OS-assigned port | Whatever the operator exposes |
| `local_mode` | `True` — unlocks annotating local paths | `False` — those routes 404 |

Every row is a **Settings value**, not a branch in the business logic. `services/` cannot
tell which shape it is running in, and that is the property to preserve.

**Three rules keep this honest:**

1. **No second implementation.** There is no `desktop/` copy of the editor, the API or the
   exporters. The shell is ~450 lines of Rust whose entire job is process supervision and
   native dialogs.
2. **Desktop-only capability is gated at the API edge, never in a service.** Reading
   arbitrary local paths is a *feature* on your own machine and *arbitrary file disclosure*
   on a shared one. `api/v1/local.py` refuses every route with a 404 unless
   `settings.local_mode`, so the capability does not exist on a server at all.
3. **Same-origin by construction.** The desktop window loads the editor from the local
   server rather than from a `tauri://` asset URL, so there is no CORS boundary, no second
   origin, and no desktop-only auth path to get wrong.

A useful consequence: `curvevision-local` run on its own, with no shell, opens a complete
working CurveVision in an ordinary browser. The desktop app is a window around that.

---

## 2. Layering rules

| Layer | May import | Must not |
| --- | --- | --- |
| `api/` | `schemas/`, `services/`, `core/` | contain business logic or touch the ORM directly |
| `services/` | `domain/`, `core/`, the four seam packages | import FastAPI or Pydantic request models |
| `domain/` | `core/db` only | import services or API |
| `storage/`, `formats/`, `ml/`, `jobs/` | `core/` | import `domain/` models (they take plain data) |

The seam packages are deliberately ignorant of the domain: `Storage` moves bytes,
`DatasetFormat` consumes a `DatasetView` projection, `ModelProvider` speaks in geometry
primitives, `JobQueue` moves serialisable payloads. That is what keeps them swappable and
unit-testable without a database.

---

## 3. Data model

```
User ──< OrganizationMembership >── Organization
                                          │
                              ┌───────────┴────────────┐
                              ▼                        ▼
                          Project ──< Label ──< Attribute
                              │        (project-owned schema, task override allowed)
                    ┌─────────┼──────────────┐
                    ▼         ▼              ▼
                  Task ──< Asset       DatasetVersion (immutable release)
                    │      (content-addressed by sha256)
                    ▼
                   Job  (start_frame..stop_frame, assignee, reviewer, state)
                    │
        ┌───────────┼───────────┬──────────────┬─────────────┐
        ▼           ▼           ▼              ▼             ▼
      Shape       Track      Tag          Issue         AnnotationEvent
                    │                       │             (history/blame)
                    ▼                       ▼
               TrackShape                Comment
             (keyframes)
```

### Why the job is the unit of work

A task owns media; a job owns *work*. Assignment, state (`new → in_progress → submitted →
accepted/rejected`), review, and progress all attach to the job. This is what allows a
10,000-frame video to be annotated by five people in parallel and reviewed by a sixth, and
it is why every annotation row carries a `job_id`: it makes "load the annotations I need"
a single indexed lookup.

### Annotation representation

* `Shape` — one geometry on one frame.
* `Track` — one object over time, materialised as `TrackShape` keyframes plus interpolation.
* `Tag` — classification on a frame or on the whole job.

Geometry is a flat `float[]` of coordinates in image pixel space, plus a `type` discriminator.
Rectangles are `[x1,y1,x2,y2]`; rotated rectangles add `rotation`; polygons/polylines/points
are `[x1,y1,…,xn,yn]`; ellipses are `[cx,cy,rx,ry]`; masks are RLE plus a bbox; skeletons are
points plus a label-defined edge graph.

Attributes are stored as a JSON object validated at the service boundary against the label's
attribute schema. This is the one place we accept a schemaless column, and it is a considered
trade: attribute values are *always* read with their parent object and never queried
independently, so a side table would triple row counts for no query benefit.

### Interpolation

Between two keyframes, position is linearly interpolated. `outside=true` on a keyframe means
the object is absent from that point until the next keyframe. For polygons and polylines with
differing vertex counts, both shapes are resampled to a common arc-length parameterisation
before interpolating — the naive index-pairing approach produces visibly wrong geometry and
is a common bug in this class of tool.

---

## 4. Request lifecycle

1. ASGI middleware assigns a `request_id`, binds it to the logging context, and starts a
   latency timer.
2. Authentication resolves a `Principal` from a Bearer JWT or an API token (`cv_…`).
3. The router's resource dependency loads the target row (project/task/job) *once*.
4. The policy engine answers `(principal, role in scope, resource, action)`.
5. The service executes inside one transaction; the session commits at the service boundary.
6. The response model serialises; errors become RFC 9457 `application/problem+json`.
7. Metrics and a structured access log line are emitted.

---

## 5. Background jobs

`JobQueue` has two implementations:

* `InlineJobQueue` — runs the task synchronously. Used in tests and in `dev` mode so a
  contributor needs no Redis to exercise import/export end to end.
* `DramatiqJobQueue` — Redis broker, queue routing (`media`, `import`, `export`, `inference`,
  `webhooks`), retries with exponential backoff.

Every task is **idempotent** and keyed: a `BackgroundTask` row records
`(idempotency_key, state, result_ref)`, so a redelivery after a worker crash resumes rather
than duplicates. Long-running tasks report progress into that row, which is what the UI
polls.

---

## 6. Media pipeline

```
upload (resumable, chunked) → sha256 → dedupe → Asset row
                                          │
                    ┌─────────────────────┴──────────────────┐
                    ▼                                        ▼
              image: probe dims, thumbnail            video: probe fps/dims/duration,
                                                      build keyframe index, extract
                                                      frame chunks (N frames/chunk)
                                          │
                                          ▼
                            chunks + thumbnails → object storage
                                          │
                                          ▼
                         client: LRU chunk cache + ±1 chunk prefetch
```

**Status:** the chunk model and planning exist; extraction and chunked delivery are *In
Progress*, and the client currently fetches one frame per request. That is fine for images
and is the blocker for usable video annotation — one request per frame is the difference
between a video annotator that is pleasant and one that is unusable.

Image handling uses **Pillow**; video uses **PyAV** (FFmpeg bindings). Both are optional
imports: the server starts and the full test suite passes without them, degrading to
"dimensions unknown" rather than crashing, which keeps the contributor onramp cheap.

---

## 7. Security architecture

* Argon2id password hashing; JWT access tokens (short TTL) + rotating, revocable refresh
  tokens stored hashed.
* API tokens are `cv_<public_id>_<secret>`; only a SHA-256 hash of the secret is stored, and
  the plaintext is shown exactly once.
* Authorization is centralised: one policy table, resolved per request against the loaded
  resource. No permission logic in view bodies.
* Uploads are validated by extension **and** magic bytes, with a configurable size cap and a
  `ContentScanner` hook for ClamAV or a vendor scanner.
* Security headers, strict CORS allow-list, per-token and per-IP rate limits.
* `AuditEvent` records security-relevant actions (auth, permission changes, exports,
  deletions) with actor, IP and target.

See [SECURITY.md](./SECURITY.md) for the threat model and reporting process.

---

## 8. Open-Source Building Blocks & Build-vs-Extend Decisions

The rule: **use existing OSS → extend existing OSS → build**, and only build when there is a
concrete technical reason. Below is every decision that materially shaped the architecture.

### 8.0 When we adapt source rather than depend on it

Most reuse is a dependency: add the package, record the licence, move on. Occasionally what
we want is a few hundred lines of *source* from inside a much larger application, in our own
idiom rather than as a dependency. That is a different act and it is governed by
[ADR 0007](./adr/0007-reusing-third-party-code.md), which sets out both halves of the
question.

**May we?** Not until a licence audit has been run against the upstream repository *at a
named commit* — the top-level `LICENSE` verbatim, any other `LICENSE` in the tree, every
distinct `SPDX-License-Identifier` across the source headers, and whatever caveats the
project documents about itself. A permissive licence is permission with conditions. Under
MIT the condition is that the copyright notice and licence text travel with the code; that
obligation is the price of the reuse, and it is what
[THIRD_PARTY_NOTICES.md](./THIRD_PARTY_NOTICES.md) exists to discharge.

**Should we?** Reuse pays for **hard-won domain knowledge** and costs for **framework glue**.
Glue does not survive the trip: moving a Django/DRF idiom into an async FastAPI codebase is a
rewrite wearing reuse's clothes, and it leaves foreign idioms to maintain forever. Domain
knowledge moves almost free, because the knowledge is in the algorithm rather than the
framework around it.

| Nature of the code | Decision |
| --- | --- |
| Algorithms, decoders, format edge cases | **Adapt** — with the obligations above |
| Domain knowledge in another library's shape | **Reference**; port the quirks, not the structure |
| A separable library | **Depend on it** |
| Behaviour that must match exactly | **Share test vectors, not code** |
| ORM models, serialisers, viewsets, permissions | **Do not adapt** |
| An architecture an ADR already rejected on measured grounds | **Do not adapt** |

In practice the reuse budget has gone almost entirely into **media handling**. Video
decoding is years of accumulated edge cases — variable frame rates, broken keyframe indices,
rotation metadata, containers that lie about their duration — and re-deriving it would be
the least defensible code we could write. `server/curvevision/media/video.py` is the one
adapted file, and it carries its upstream copyright header in place.

Everywhere else the architectures diverge by design: [ADR 0003](./adr/0003-canvas2d-with-spatial-index.md)
chose Canvas2D plus an R-tree over an SVG DOM on a measured picking advantage of roughly
7,000× at 100k shapes, and [ADR 0004](./adr/0004-streaming-format-registry.md) chose a streaming format
registry because a memory-resident one cannot export 500k images. Adopting the alternatives
to gain code would undo benchmarked decisions.

### Backend

| Subsystem | Options considered | Selected | License | Why | What CurveVision changes | Upstreamable? |
| --- | --- | --- | --- | --- | --- | --- |
| Web framework | Django+DRF, FastAPI, Litestar, Flask | **FastAPI** | MIT | Async-native for an I/O-bound workload; one type declaration drives validation, OpenAPI 3.1, SDK types and client types. Django's batteries are real but DRF serializers duplicate typing we get free from Pydantic. | Nothing — used as-is | n/a |
| ORM | Django ORM, SQLAlchemy 2.0, SQLModel, Tortoise, Piccolo | **SQLAlchemy 2.0** | MIT | Typed `Mapped[...]` models, explicit unit-of-work, the most production-proven Python ORM. SQLModel conflates persistence and API schemas, which is exactly the coupling we want to avoid. | Small portable `GUID`/`JSONVariant` type decorators for SQLite-vs-Postgres parity in tests | Type decorators are too project-specific to upstream |
| Migrations | Alembic, custom SQL, Atlas | **Alembic** | MIT | The standard, integrates with SQLAlchemy metadata | Nothing | n/a |
| Validation/serialisation | Pydantic v2, attrs+cattrs, marshmallow | **Pydantic v2** | MIT | Rust-backed performance; native FastAPI integration | Nothing | n/a |
| Password hashing | passlib+bcrypt, argon2-cffi, bcrypt | **argon2-cffi** | MIT | Argon2id is the PHC winner and current OWASP recommendation. passlib is effectively unmaintained (last release 2020) and is a poor foundation for auth. | Nothing | n/a |
| JWT | PyJWT, python-jose, authlib | **PyJWT** | MIT | Minimal, audited, maintained. python-jose has a history of unmaintained periods and CVEs. | Nothing | n/a |
| Background jobs | Celery, RQ, Dramatiq, arq, Huey, Taskiq | **Dramatiq** | LGPL-3.0 (used as an unmodified library dependency) | Simpler operationally than Celery, more robust than RQ (native retries, message deduplication, graceful shutdown). Media work is CPU-bound and sync, so a sync worker beats an asyncio one. | Wrapped behind `JobQueue` so a deployment can swap it; an `InlineJobQueue` makes the whole system testable with no broker | n/a |
| Policy / authz | Open Policy Agent, pycasbin, oso, py-abac, build | **Build** (in-process policy module) | — | The decision is a pure function of rows already loaded. OPA adds a process and Rego; Casbin's matcher model fits RBAC but fits our assignment-scoped rules (`annotator may write only on jobs assigned to them`) badly enough that the "config" becomes code anyway. The whole policy is ~150 lines of declarative tables and is exhaustively unit-tested. | n/a | n/a |
| Object storage | boto3, minio-py, apache-libcloud, fsspec | **boto3** (+ `Storage` protocol) | Apache-2.0 | The S3 API is the de-facto standard; boto3 works against AWS, MinIO, R2, Ceph, Backblaze | Wrapped behind a 7-method `Storage` protocol; `LocalStorage` needs no dependency at all | n/a |
| Image processing | Pillow, OpenCV, scikit-image, VIPS/pyvips | **Pillow** | MIT-CMU | Sufficient for probe + thumbnail; a fraction of OpenCV's install size. pyvips is faster for huge images and is a *Planned* optional backend behind the same interface. | Optional import — server runs without it | n/a |
| Video decode | PyAV, imageio-ffmpeg, opencv, ffmpeg subprocess | **PyAV** | BSD-3-Clause (wraps FFmpeg: LGPL/GPL) | Real frame-accurate seeking and keyframe indexing, which subprocess piping cannot give you | Optional extra; licensing documented in THIRD_PARTY_NOTICES | n/a |
| Dataset formats | Datumaro, FiftyOne, custom | **Build a streaming registry**, Datumaro bridge *Planned* | (Datumaro: MIT) | Datumaro is excellent but loads datasets into memory; a server exporting 500k images cannot. Its transitive deps (NumPy/OpenCV) are also heavy for the API image. | We would rather contribute a streaming/iterator export API to Datumaro than maintain a parallel implementation forever | **Yes — candidate upstream contribution** |
| ML serving | Nuclio, Triton, TorchServe, BentoML, Ray Serve, KServe | **None — an HTTP contract** | — | Bundling a serving platform forces our choice on every operator and drags model licenses into the core. A documented HTTP schema means all of the above work with zero CurveVision plugins. | We define and publish the inference contract | n/a |
| Metrics | prometheus-client, OpenTelemetry, statsd | **prometheus-client** now, OTel *Planned* | Apache-2.0 | Every self-hoster already knows how to scrape Prometheus | Nothing | n/a |
| Logging | structlog, loguru, stdlib | **stdlib logging + a JSON formatter** | — | ~40 lines vs a dependency; keeps library log integration trivial | n/a | n/a |
| HTTP client | httpx, requests, aiohttp | **httpx** | BSD-3 | Sync+async in one API; `ASGITransport` lets the SDK's own tests run against the app with no server | Nothing | n/a |
| CLI | Typer, Click, argparse | **Typer** | MIT | Type-hint-driven commands match the SDK's typed style | Nothing | n/a |
| Lint/format/types | ruff, black+flake8+isort, mypy, pyright | **ruff + mypy** | MIT | ruff replaces four tools at ~100x the speed | Nothing | n/a |

### Frontend

| Subsystem | Options considered | Selected | License | Why | What CurveVision changes | Upstreamable? |
| --- | --- | --- | --- | --- | --- | --- |
| Framework | React, Vue, Svelte, SolidJS | **React 18** | MIT | Largest contributor pool for an OSS project; the canvas engine is framework-agnostic anyway so the framework's rendering model is not on the hot path | Nothing | n/a |
| Build | Vite, webpack, Rspack, Parcel | **Vite** | MIT | Fast cold start and HMR; first-class TS | Nothing | n/a |
| Server state | TanStack Query, SWR, RTK Query, Apollo | **TanStack Query** | MIT | Caching, retries, invalidation, and optimistic updates are exactly our needs and are hard to get right by hand | Nothing | n/a |
| Client state | Zustand, Redux Toolkit, Jotai, MobX | **Zustand** | MIT | The editor needs a fast, non-reactive-by-default store the canvas can read imperatively without re-render storms. Redux's action pipeline adds overhead on a 60fps path. | Nothing | n/a |
| Styling | Tailwind, CSS Modules, styled-components, Ant Design | **Tailwind CSS** | MIT | No runtime cost; avoids the heavy component-library lock-in that makes a product look like every other tool built on it. We build ~15 primitives ourselves for a distinct CurveVision identity. | Own design system on top | n/a |
| Spatial index | rbush, flatbush, kdbush, build a quadtree | **rbush** | MIT | Best-in-class R-tree, tiny, supports dynamic insert/remove — which flatbush (static) cannot do, and shapes are edited constantly | Wrapped behind a `SpatialIndex` interface so flatbush can back a read-only review mode later | n/a |
| Canvas rendering | SVG DOM, Canvas2D, PixiJS, Konva, fabric.js, raw WebGL | **Build on Canvas2D** | — | Konva/fabric are scene-graph libraries optimised for general editing, carrying object models and hit-testing we would fight for annotation-specific needs (100k shapes, viewport culling, per-label styling, dirty-rect layers). PixiJS is a strong WebGL option and is the *Planned* second `Renderer` implementation behind the same interface. | The engine is ours; the `Renderer` interface keeps a PixiJS/WebGL backend a drop-in | n/a |
| Icons | lucide-react, heroicons, phosphor | **lucide-react** | ISC | Comprehensive, tree-shakeable, permissive | Nothing | n/a |
| Testing | Vitest, Jest | **Vitest** | MIT | Shares the Vite pipeline; no separate transform config | Nothing | n/a |

### Deployment

| Subsystem | Selected | Why |
| --- | --- | --- |
| Local/self-host | **Docker Compose**, 6 services | One command, no orchestrator to learn |
| Object storage (dev) | **MinIO** (AGPL-3.0, run as an unmodified service, not linked) | The reference S3-compatible server |
| Database | **PostgreSQL 16** | Right answer; nothing else considered seriously |
| Cache/broker | **Redis 7** | Cache + Dramatiq broker; no on-disk store needed since chunks live in object storage |
| Kubernetes | Helm chart — *Planned* | Compose first; Helm when someone actually needs it |

### Where building was justified

Four things are genuinely ours, each for a stated reason:

1. **The annotation canvas engine** — no OSS library targets 100k-shape viewport-culled
   annotation with per-label styling and an annotation-specific tool model.
2. **The policy module** — the decision is a pure function of loaded rows; a process or a
   second language is disproportionate.
3. **The streaming dataset-format registry** — existing libraries are memory-resident by
   design; ours is iterator-based. We intend to try upstreaming the idea rather than
   maintaining a fork forever.
4. **The inference contract** — a schema, not a system; deliberately *less* than the
   serving platforms it replaces.

Everything else in the stack is somebody else's well-maintained code, and that is the point.
See [THIRD_PARTY_NOTICES.md](./THIRD_PARTY_NOTICES.md) for attribution.

---

## 9. Performance model

| Concern | Approach |
| --- | --- |
| Listing millions of assets | rows + object keys; always paginated; covering indexes on `(task_id, index)` |
| 100k annotations in a job | server streams per-job with optional frame-range filter; client culls via R-tree |
| Long video | chunked frames + client LRU + prefetch — *In Progress*; the client currently fetches one frame per request |
| Concurrent editors | optimistic versioning on `job.annotation_version`; a stale write is rejected with 409 rather than merged |
| Slow operations | all async background jobs with idempotency keys and progress rows |
| Frontend frame time | layered canvases, dirty rects, rAF-throttled input, viewport culling |

Benchmarks: `server/tests/benchmarks/`, `web/src/canvas/__bench__/`.

---

## 10. Architecture decision records

Material decisions are recorded in [`docs/adr/`](./adr/). Each ADR states context, the
options weighed, the decision, and the consequences we accept.
