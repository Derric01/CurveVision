# Roadmap

CurveVision is built by volunteers. **This roadmap has no dates**, because a date we cannot
keep is worse than no date at all. It states what is done, what is next, and what we have
decided against — in that order of usefulness.

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
| Python SDK and CLI | **Done** |

**MVP is complete.** You can install CurveVision today and produce a dataset with it.

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
| Video annotation | **In Progress** — probing and the data model are done; chunked frame extraction is not |
| Track editing UI (keyframe timeline) | **In Progress** — the model and interpolation are done on both sides; the UI is not |
| Mask brush tool | **Planned** — RLE storage and export exist; the drawing tool does not |
| Skeleton / keypoint editing UI | **Planned** — the model and COCO keypoint export exist |
| Ground-truth quality reports | **In Progress** — the model exists; the comparison job does not |
| Resumable uploads | **In Progress** — the protocol and table exist; the client does not use them |

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
| Desktop application (Tauri) | **Planned** — see below |
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

Planned as a **Tauri v2 shell around the same web bundle**, adding local dataset access
without an upload round trip, an embedded server for offline work, and background sync.

Explicitly **not** a second implementation of the product. `desktop/` currently holds the
configuration and a README explaining the design; there is no working desktop build yet,
and we would rather say so than ship a stub that looks like one.

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
