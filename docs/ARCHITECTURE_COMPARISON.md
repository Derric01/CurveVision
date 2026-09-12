# Architecture Comparison: CurveVision and CVAT

CurveVision is a new, independently implemented project. CVAT is the most mature open-source
annotation platform in existence, and we studied it deliberately rather than rediscovering
its lessons the expensive way. This document records what we learned, what we adopted, what
we did differently, and — importantly — the licensing position.

**CurveVision is not a fork or a rename of CVAT.** Most of what we took from CVAT is
*architectural understanding*: which decompositions of the annotation problem hold up under
production load, and which do not.

Where we do adapt CVAT's MIT-licensed code — because the knowledge in it is worth more than
the lines, and rewriting it from scratch would only re-earn its bugs — we say so, keep its
copyright notice in the file, and list it. There is exactly one such file today
(`server/curvevision/media/video.py`, from CVAT's video decoding strategy). The
authoritative list lives in
[THIRD_PARTY_NOTICES.md § Adapting CVAT code](./THIRD_PARTY_NOTICES.md#adapting-cvat-code),
and the policy that governs it in [ADR 0007](./adr/0007-cvat-reuse-policy.md).

---

## 1. Licensing position

### What CVAT is licensed under

| Component | License | Copyright holders (verbatim from `LICENSE`) |
| --- | --- | --- |
| CVAT Community core (`cvat/`, `cvat-ui/`, `cvat-sdk/`, `cvat-cli/`) | MIT | `Copyright (C) 2018-2022 Intel Corporation`, `Copyright (C) 2022-2025 CVAT.ai Corporation` |
| `serverless/` model wrappers | MIT, but individual models and weights carry their own licenses (some non-commercial, e.g. certain YOLO and SAM derivatives) | model authors |
| FFmpeg libraries used by CVAT | LGPL/GPL depending on build configuration | FFmpeg authors |
| CVAT Enterprise / cloud-only features | proprietary, not covered by the MIT grant | CVAT.ai Corporation |

### What this means for CurveVision

1. **MIT permits adaptation with attribution.** Had we copied MIT-licensed CVAT code, we
   would be obliged to preserve the copyright notice and license text. We chose not to copy
   code, so no CVAT copyright notice attaches to CurveVision source files.
2. **Facts, formats and architecture are not the licensed thing.** Interoperating with the
   CVAT XML/JSON annotation format, or independently arriving at a `Project → Task → Job`
   decomposition, does not create a derivative work. Where CurveVision implements a
   CVAT-compatible *format*, it is an independent implementation written from the
   observable structure of the format, and is documented as such.
3. **We do not touch CVAT Enterprise functionality.** Cloud-only/proprietary features are
   explicitly out of scope; nothing in CurveVision is derived from them.
4. **Model weights are never bundled.** CurveVision's AI layer talks to inference endpoints
   the operator chooses to run. This keeps model licenses (which are frequently more
   restrictive than the code around them) entirely the operator's decision, and keeps the
   CurveVision core free of non-commercial encumbrances.
5. **FFmpeg** enters CurveVision only through PyAV, as an optional extra, with its licensing
   documented in [THIRD_PARTY_NOTICES.md](./THIRD_PARTY_NOTICES.md). Distributors who ship
   container images must satisfy LGPL/GPL obligations for the FFmpeg build they include —
   the same obligation CVAT and every other tool in this space carries.
6. **If we ever do vendor MIT-licensed code** — from CVAT or anywhere else — the rule is:
   the file keeps its original copyright header, the vendored path is listed in
   `docs/THIRD_PARTY_NOTICES.md`, and the license text is reproduced under `licenses/`.
   There is a CI check that fails if a declared dependency is missing from the notices file.

CurveVision itself is MIT licensed, which keeps it maximally compatible with the ecosystem
it lives in.

---

## 2. Stack comparison

| Concern | CVAT | CurveVision | Why we differ |
| --- | --- | --- | --- |
| Backend framework | Django + DRF | FastAPI + Pydantic v2 | Native async I/O for a workload that is mostly DB + object-storage waiting; OpenAPI 3.1 generated from the same type annotations that validate requests, so the SDK and frontend clients cannot drift from the server. Django's admin and ORM are excellent, but DRF serializers duplicate typing that Pydantic gives us once. |
| ORM | Django ORM | SQLAlchemy 2.0 (typed, async) | `Mapped[...]` annotations give real static typing over the domain model; explicit session/unit-of-work boundaries make the "one transaction per request, commit at the service edge" rule enforceable. |
| Database | PostgreSQL | PostgreSQL | Same. It is the right answer. |
| Cache/queue broker | Redis (in-memory) + Kvrocks (on-disk) | Redis | Kvrocks exists in CVAT to hold decoded media chunks durably. CurveVision puts chunks in object storage — which self-hosters already have — and uses Redis purely as a cache/broker, removing a stateful service from the deployment. |
| Background jobs | RQ, 8 dedicated worker services | Dramatiq, one worker image with queue routing | 8 worker *services* is a deployment-level answer to a routing problem. One image with `--queues import,export,media` gives the same isolation when you want it and one container when you don't. |
| Permissions | Open Policy Agent (separate service, Rego policies) | In-process policy module | The decision is a pure function of rows already loaded to serve the request. A network hop and a second policy language per authorization check is a real cost for self-hosters and a real barrier for contributors. We keep the *good* idea — centralised, declarative, table-driven policy, never ad-hoc checks in view bodies — without the process. |
| Analytics | ClickHouse + Vector + Grafana | Postgres aggregates + Prometheus `/metrics` | Three additional stateful services to answer "how many annotations did this person make" is disproportionate. A ClickHouse-backed analytics module remains a *Planned* optional add-on for large installations. |
| Reverse proxy | Traefik in-compose | Left to the operator (compose ships Caddy for dev) | Opinionated ingress is the operator's call. |
| ML serving | Nuclio serverless functions | `ModelProvider` abstraction, HTTP provider by default | Nuclio is powerful but is a whole additional platform to learn and operate. An HTTP contract means any of TorchServe, Triton, BentoML, Ray Serve, a FastAPI script, or a hosted vendor works with no CurveVision-side plugin. |
| Frontend | React + Redux + Ant Design, SVG-based `cvat-canvas` | React + Zustand + TanStack Query + Tailwind, Canvas2D engine with an R-tree | See §4. |
| Dataset formats | Datumaro (separate library, 20+ formats) | In-house plugin registry, 4 formats, Datumaro bridge *Planned* | See §5. |
| Deployment | 18 compose services | 6 compose services (api, worker, web, postgres, redis, minio) | Fewer moving parts to operate, back up, and reason about. |

---

## 3. Domain model: what we adopted and what we simplified

CVAT's core decomposition is correct and we adopted it:

* **Project / Task / Job** as three distinct levels, with the *job* — not the task — as the
  unit of assignment, state, and review. This is the decision that makes parallel annotation
  and meaningful progress tracking possible. Systems that treat "task" as both the media
  container and the work unit cannot express "three people annotating one 10,000-frame video".
* **Shapes vs Tracks vs Tags** as three distinct annotation kinds rather than one table with
  nullable columns. Tracks with keyframes plus interpolation is the only representation that
  makes video annotation economically viable.
* **`outside` / `occluded` / `keyframe`** flags on track shapes. These three flags encode the
  situations that actually occur in video (object exits frame, object is hidden, this is a
  human-authored position) and their absence is a common design failure in newer tools.
* **Labels owned by the project, inherited by tasks**, with typed attributes and a `mutable`
  flag distinguishing "this car is red" (immutable over a track) from "this car's indicator
  is on" (changes frame to frame).
* **Chunked media delivery.** Serving frames in batches with client-side prefetch, rather
  than one request per frame, is the difference between a usable and an unusable video
  annotator.

Where CurveVision deliberately diverges:

| CVAT | CurveVision | Reason |
| --- | --- | --- |
| `Task → Segment → Job` | `Task → Job` (job carries `start_frame`/`stop_frame`) | The `Segment` entity is 1:1 with `Job` in practice. Collapsing it removes a join from the hottest query in the system. |
| Annotation "immutable + version" tables split across `LabeledShape`/`LabeledTrack`/`TrackedShape`/`LabeledImage` and their attribute-value side tables | Three tables (`shape`, `track` + `track_shape`, `tag`) with attributes as a validated JSON column | Attribute values are always read with their parent object and never queried independently. A side table per attribute value multiplies row counts by ~3x for no query benefit. Validation happens at the service boundary against the label schema. |
| Media chunks cached in Kvrocks | Chunks written to object storage, cached in Redis by key | Removes a stateful service; chunks become durable and shareable across API replicas for free. |
| Attribute/label spec duplicated per task | Project-owned schema with explicit per-task override | Schema drift between tasks in one project is a leading cause of unusable exports. |
| `source` field distinguishing manual/auto | `source` enum extended with `model_corrected` | Dataset provenance should record that a human fixed a model prediction — that distinction matters for active-learning and for auditing dataset quality. |

---

## 4. The annotation canvas: the most important difference

CVAT's `cvat-canvas` renders annotations as **SVG DOM nodes**. This buys a great deal —
browser-native hit testing, CSS styling, accessibility — and it is a completely reasonable
choice. Its ceiling is the DOM: each shape is one or more elements, and browsers degrade
noticeably somewhere in the low thousands of simultaneously-present nodes, particularly
during pan/zoom when transforms invalidate large parts of the layer.

CurveVision uses a **Canvas2D engine with an explicit scene, an R-tree spatial index, and
layered dirty-rectangle rendering**:

```
 CVAT (SVG)                          CurveVision (Canvas2D + R-tree)
 ──────────                          ───────────────────────────────
 shape → SVG node                    shape → scene entry → rbush index entry
 hit test → browser                  hit test → R-tree query + geometric test  (O(log n))
 render → browser layout/paint       render → cull to viewport, paint only visible
 1000s of shapes → DOM pressure      100k shapes → paint cost ∝ shapes on screen
```

Concretely:

* **Viewport culling** means a job with 100,000 annotations paints the ~50 that are on
  screen. Frame time is decoupled from dataset size.
* **Layer separation** (media / shapes / overlay) means hovering and dragging repaint only a
  transient overlay, never the full shape set.
* **Input throttling to `requestAnimationFrame`** bounds work per frame regardless of how
  fast the pointer emits events.
* **The engine is framework-agnostic TypeScript.** React mounts it and issues commands; it
  imports nothing from React. This is what keeps it testable in Node and prevents the
  "canvas becomes an unmaintainable 5,000-line component" outcome.

The trade-off is honest: we give up browser-native accessibility on the canvas surface and
must implement our own hit testing and text rendering. We consider that the right trade for
the tool's most performance-sensitive surface, and we compensate with a fully
keyboard-navigable object list beside the canvas.

We also plan a WebGL path for the shape layer behind the same `Renderer` interface. It is
*Planned*, not implemented, and the Canvas2D renderer is fast enough that it is not urgent.

---

## 5. Dataset formats: Datumaro vs a plugin registry

CVAT delegates format conversion to [Datumaro](https://github.com/openvinotoolkit/datumaro)
(MIT, Intel/OpenVINO), which supports 20+ formats and is genuinely excellent.

We evaluated using Datumaro directly. The finding:

**For:** enormous format coverage, actively maintained, MIT, battle-tested edge cases.
**Against:** Datumaro's model is "load a dataset into memory as `DatasetItem`s, then convert".
That is exactly right for a CLI conversion tool and exactly wrong for a server that must
export a 500k-image dataset without a 30 GB resident set. Bridging it also means adopting its
transitive dependency set (including NumPy/OpenCV pulls) into the core API image.

**Decision:** implement a small streaming plugin registry with the four formats that cover
the overwhelming majority of real use (COCO, YOLO, Pascal VOC, and a lossless native format),
and expose a **Datumaro bridge as an optional extra** for the long tail, where the
memory-bounded model is acceptable because those exports are rarer and smaller. This is
recorded in the build-vs-extend table in
[ARCHITECTURE.md](./ARCHITECTURE.md#open-source-building-blocks--build-vs-extend-decisions).

We would rather contribute a streaming export API to Datumaro upstream than maintain a
permanent parallel implementation, and that is on the roadmap as an upstream contribution
candidate.

---

## 6. What CVAT does that CurveVision does not (yet)

Stated plainly, because pretending otherwise would be dishonest:

* **3D / point cloud annotation** (LiDAR, cuboids in 3D) — CVAT has it; CurveVision does not.
  Not currently planned for 1.0.
* **20+ dataset formats** — CVAT via Datumaro has far broader coverage.
* **Cloud storage integrations** for Azure and GCS — CurveVision ships S3-compatible only.
* **Maturity.** CVAT has years of production use across many organisations and an ecosystem
  of integrations. CurveVision is new. Anyone choosing an annotation platform for a critical
  project today should weigh that seriously, and we say so in the README.
* **Consensus / honeypot quality workflows** — CVAT has dedicated consensus workers.
  CurveVision's quality module is *In Progress* and starts with ground-truth comparison.

## 7. What CurveVision aims to do better

* **Operational simplicity** — 6 services instead of 18; one worker image; no OPA, no
  ClickHouse, no Kvrocks required to run the product.
* **Editor performance at scale** — spatial-index-backed canvas rather than an SVG DOM.
* **Type-safe contract end to end** — one Pydantic definition drives validation, OpenAPI,
  the TypeScript client types, and the Python SDK.
* **Provider-neutral AI** — an HTTP inference contract instead of a bundled serverless
  platform, so bringing your own model is a URL, not a deployment project.
* **Crash-resilient annotation** — an IndexedDB write-ahead buffer so a browser crash or a
  dropped connection does not lose in-progress work.
* **Contributor onramp** — a new contributor should be able to read the whole permission
  system in one file, and run the whole test suite with no services running.

---

## 8. Attribution

CVAT is a substantial piece of open-source engineering by Intel Corporation and CVAT.ai
Corporation and their contributors. CurveVision exists in a landscape that CVAT largely
defined, and we are explicit about that debt. Where CurveVision implements CVAT-compatible
interchange formats, those are noted in the format plugin's docstring as independent
implementations of a published format.

We make no claim of affiliation with, or endorsement by, CVAT.ai Corporation or Intel
Corporation.
