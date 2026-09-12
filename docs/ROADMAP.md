# Roadmap

CurveVision is built by volunteers. **This roadmap has no dates**, because a date we cannot
keep is worse than no date at all. It states what is done, what is next, and what we have
decided against — in that order of usefulness.

**The product is one thing you can run two ways:** a desktop application you install, and a
server your team opens in a browser. Both are first-class, both are the same code, and no
item below may improve one at the other's expense. See
[ARCHITECTURE.md § 1.1](./ARCHITECTURE.md#11-two-shapes-one-codebase).

Status markers match [IMPLEMENTATION_PLAN.md](./IMPLEMENTATION_PLAN.md) and the README:
**Done** means implemented and tested; **In Progress** means partially usable; **Planned**
means designed, not built.

---

## MVP — a useful individual annotation platform

*A single person can upload images, annotate them well, and export a real dataset.*

| | Status |
| --- | --- |
| Users, organizations, projects, tasks, jobs | **Done** |
| Label schema with typed attributes | **Done** |
| Image upload with content-addressed deduplication | **Done** |
| Annotation editor: rectangle, polygon, polyline, points, ellipse | **Done** |
| Selection, move, vertex editing, marquee | **Done** |
| Undo/redo, zoom/pan, keyboard-first workflow | **Done** |
| Autosave with crash recovery | **Done** |
| Export: COCO, YOLO, Pascal VOC, CurveVision JSON | **Done** |
| Import with append/replace conflict policy | **Done** |
| Docker Compose deployment | **Done** |
| Desktop application: zero-config local mode, packaged server, Tauri shell | **Done** |
| Annotate local folders in place, without copying | **Done** |
| Python SDK and CLI | **Done** |

**MVP is complete.** You can run CurveVision today — as a server or as a desktop
application — and produce a real dataset with it.

---

## Beta — collaboration, review, AI assistance

*A team can divide work, review it, and use models to go faster.*

| | Status |
| --- | --- |
| Roles and per-organization membership | **Done** |
| Job assignment and the review state machine | **Done** |
| Issues and comment threads anchored to frames | **Done** |
| Annotation history | **Done** |
| `ModelProvider` abstraction and the HTTP inference contract | **Done** |
| Model predictions as accept/reject suggestions | **Done** |
| Dataset versions with immutable releases | **Done** |
| REST API and API tokens | **Done** |
| Webhooks with HMAC signing | **In Progress** — delivery works; retry/backoff is not wired to the queue |
| Video annotation | **In Progress** — a video task is annotatable, and frames are served from chunks decoded one pass at a time (17× fewer decodes when stepping through frames; see the implementation plan for the measurements). The editor fetches a chunk rather than 36 frames. The track-editing timeline is not built |
| Track editing UI (keyframe timeline) | **In Progress** — the timeline shows every track's keyframes and where it is present, and `,`/`.` step between keyframes; adding, moving and removing keyframes from it is not built |
| Mask brush tool | **Planned** — RLE storage and export exist; the drawing tool does not |
| Skeleton / keypoint editing UI | **Planned** — the model and COCO keypoint export exist |
| Ground-truth quality reports | *Planned* — the storage model exists; nothing computes a report |
| Resumable uploads | *Planned* — the storage model exists; there are no endpoints yet |

**What "Beta" needs before we call it that:** video annotation end to end, the track
timeline, and webhook retries.

---

## 1.0 — a production-grade annotation platform

*An organization can run this for real work and sleep at night.*

| | Status |
| --- | --- |
| Redis-backed distributed rate limiting | **Planned** |
| OpenTelemetry tracing | **Planned** — the instrumentation seam exists |
| Content scanning hook implementation (ClamAV) | **Planned** — the hook exists |
| OIDC / SAML single sign-on | **Planned** — the `AuthBackend` seam exists |
| Kubernetes Helm chart | **Planned** |
| Backup and restore tooling | **Planned** |
| Published TypeScript client package | **Planned** |
| Additional formats: KITTI, LabelMe, Open Images, TFRecord | **Planned** |
| Datumaro bridge for the long tail of formats | **Planned** |
| An external security review | **Planned** |

We will not call a release 1.0 until backup/restore is documented and tested, and the
project has had a security review. Calling it 1.0 sooner would be a claim we cannot back.

---

## Future

Directions we believe in, without commitment:

* **Active learning** — rank unlabelled frames by model uncertainty, so annotation effort
  goes where it changes the model.
* **Model-assisted labelling loops** — train on accepted work, re-run, measure whether
  suggestions are getting better. The `source` provenance chain (`model` →
  `model_corrected`) exists precisely to make this measurable.
* **Interactive segmentation** — click-to-segment via the `interactor` model kind, which
  the contract already accommodates.
* **Dataset analytics** — annotation quality over time, inter-annotator agreement,
  per-annotator throughput and drift.
* **Distributed processing** — parallel frame extraction and export for very large datasets.
* **Offline desktop with sync** — annotate on a plane, reconcile on landing.
* **Advanced QA** — consensus scoring, honeypot frames, automatic reviewer assignment.
* **ML pipeline integrations** — export straight into a training job; import predictions
  back as suggestions.
* **Experiment tracking hooks** — link a dataset release to the runs trained on it, so
  "which data produced this model" has an answer.
* **3D / point cloud annotation** — a large piece of work, and honestly not close.

## The desktop application

A **Tauri v2 shell** that spawns the CurveVision server, packaged by PyInstaller as one
executable, and opens a window on it. Installing it requires no Python, no PostgreSQL, no
Redis and no object store.

**Working today**, verified end to end against the packaged binary:

| | Status |
| --- | --- |
| Zero-configuration local mode: app data dir, Alembic migration, one local account | **Done** |
| Handshake: loopback-only OS-assigned port, fresh token per launch, previous one revoked | **Done** |
| Packaged server (~38 MB, ~1.5–2.1 s from spawn to handshake on Linux) | **Done** |
| Tauri shell: process supervision, no-sign-in token injection, native folder dialogs | **Done** |
| Annotating local folders in place, with nothing copied | **Done** |
| The editor served by the app itself, same-origin with its API | **Done** |
| Frontend reads the injected connection and skips sign-in | **Done** |
| Open-folder flow wired to the shell's native picker | **In Progress** |
| Signed installers for macOS and Windows, built per platform in CI | **Planned** |
| Auto-update | **Planned** |
| Pointing the desktop app at a shared team server | **Planned** — the app is already same-origin-agnostic; this is UI work |

Explicitly **not** a second implementation of the product: the shell is ~450 lines of Rust
whose whole job is starting a child process and opening native dialogs. The editor, the API
and the exporters are the same code the server runs. See
[`desktop/`](../desktop/) and
[ARCHITECTURE.md § 1.1](./ARCHITECTURE.md#11-two-shapes-one-codebase).

Two numbers to keep honest: **1,460 lines added and 42 removed** across the four commits
that turned the server into a desktop application. Nothing was rewritten to get here,
because storage, jobs, the database dialect and the formats were already interfaces.

## What we have decided against

Saying no is part of a roadmap:

* **A plugin marketplace.** Entry points for formats and providers are enough.
* **A bundled model zoo.** Model licenses are frequently more restrictive than the code
  around them; keeping weights on the operator's side of the boundary keeps the core
  unencumbered.
* **A hosted-only feature tier.** The core stays complete. Optional paid services would be
  infrastructure — managed hosting, support, hosted inference — never the ability to
  annotate.
* **Microservices.** The modular monolith holds until a module boundary demonstrably needs
  to become a network boundary. The interfaces are already the seams.

## Contributing to the roadmap

Open an issue describing the problem you have, not just the feature you want — the problem
is what tells us whether the feature is the right answer. Items marked **In Progress** are
the best places to help; see [CONTRIBUTING.md](./CONTRIBUTING.md).
