<div align="center">

<img src="web/public/favicon.svg" width="72" height="72" alt="CurveVision" />

# CurveVision

### Open-source annotation and dataset infrastructure for computer vision

**Annotate manually. Accelerate with AI. Build better datasets. Keep control of your data.**

Free · Open source · Self-hostable · Web and desktop

[Quick start](#quick-start) · [Features](#what-it-does) · [Architecture](docs/ARCHITECTURE.md) · [API](docs/API.md) · [Roadmap](docs/ROADMAP.md) · [Contributing](docs/CONTRIBUTING.md)

</div>

---

CurveVision is annotation tooling for people building computer-vision datasets: students,
researchers, indie developers, startups, ML engineers, and teams who would rather not send
their images to someone else's cloud.

It does two things equally well, and **neither requires the other**:

```
Manual-first    upload → define labels → draw → review → export
AI-assisted     upload → run a model → accept or correct → review → export
```

Most tools in this space are either a toy canvas or an enterprise platform with a sales
call attached. CurveVision aims at the space between: the breadth a real annotation
workflow needs, with an editor that stays fast at a hundred thousand objects, that you can
run on a laptop with one command.

> **Status: early.** The manual annotation path is complete and tested end to end. Video
> annotation, the track-editing UI and quality reports are partially built. Every feature
> below is marked with its real state — nothing here is marked done unless it works. If you
> need a mature platform for critical work today, look at
> [CVAT](https://github.com/cvat-ai/cvat); we say so plainly in our
> [architecture comparison](docs/ARCHITECTURE_COMPARISON.md).

---

## Quick start

```bash
git clone https://github.com/Derric01/CurveVision.git
cd CurveVision

cp .env.example .env
python3 -c 'import secrets; print(secrets.token_urlsafe(48))'   # paste into CURVEVISION_SECRET_KEY

docker compose up -d
open http://localhost:8080
```

Six containers, one command, no account, no license key. The first user you register
becomes the instance administrator.

Prefer to run it from source? See [DEVELOPMENT.md](docs/DEVELOPMENT.md) — `./scripts/dev.sh`
gets you a hot-reloading stack.

---

## What it does

### Manual annotation — first class, not a fallback

<table>
<tr><td width="50%">

The editor is keyboard-driven and built for long sessions. Every tool is one keystroke
away, undo works the way it should (one press per gesture, not per pointer-move), and
autosave keeps a local write-ahead buffer so a browser crash does not cost you an
afternoon.

</td><td>

| | |
| --- | --- |
| Rectangles, polygons, polylines, points, ellipses | **Done** |
| Selection, move, vertex editing, marquee | **Done** |
| Undo/redo with drag coalescing | **Done** |
| Zoom, pan, vertex snapping | **Done** |
| Per-label visibility and locking | **Done** |
| Crash-resilient autosave | **Done** |
| Masks (RLE storage and export exist; brush tool does not) | *Planned* |
| Skeletons / keypoints (model and export exist; UI does not) | *Planned* |

</td></tr>
</table>

**It stays fast.** The canvas is a Canvas2D engine with an R-tree spatial index, so
rendering costs what is *on screen* rather than what is in the dataset. Measured on this
machine: picking from 100,000 shapes takes ~1 µs; a linear scan of 10,000 takes ~550 µs.
Run `npm run bench` in `web/` and check for yourself.

### AI-assisted annotation — bring your own model

CurveVision ships **no model weights and imports no vendor SDK**. You point it at an
inference endpoint; predictions arrive as ordinary annotations you can accept, edit or
reject one at a time.

```json
POST /your-endpoint
{ "model": "yolo-v8n", "frames": [{ "frame": 0, "width": 1920, "height": 1080, "image": "<base64>" }] }

← { "shapes": [{ "frame": 0, "label": "person", "type": "rectangle",
                 "points": [10, 20, 110, 220], "confidence": 0.93 }] }
```

That contract is a 30-line FastAPI script. It also happens to be what Triton, TorchServe,
BentoML, Ray Serve and every hosted vendor can already speak — which is the point. Bundling
a serving platform would force our choice on every operator and drag non-commercial model
licenses into the core.

Editing a prediction records `model_corrected` provenance, so your dataset knows which
objects a machine proposed and a human fixed. That distinction survives to export and is
what makes an active-learning loop measurable.

| | |
| --- | --- |
| `ModelProvider` abstraction, HTTP provider | **Done** |
| Predictions as accept/reject suggestions | **Done** |
| Provenance chain (`model` → `model_corrected`) | **Done** |
| Interactive segmentation (click-to-segment) | *Planned* |
| Trackers, OCR, classification model kinds | *Planned* |

### Dataset management

`Dataset → Version → Annotation → Review → Release`

A released version is immutable and carries a content hash, so "which data trained this
model" has an answer. Statistics surface class distribution and — the number people
actually need before training — **how many frames have no annotations at all**.

| | |
| --- | --- |
| Immutable dataset releases with content hashing | **Done** |
| Class distribution and annotation statistics | **Done** |
| Content-addressed media (the same file across tasks is stored once) | **Done** |
| Export jobs with downloadable artifacts | **In Progress** |

### Images and video

| | |
| --- | --- |
| Image datasets, dedupe, thumbnails, frame indexing | **Done** |
| Video probing, frame addressing, track data model | **Done** |
| Client-side track interpolation (scrubbing costs no round trip) | **Done** |
| Chunked frame delivery and extraction | **In Progress** |
| Resumable uploads (protocol exists; client does not use it) | **In Progress** |

Interpolation between keyframes resamples polygons to a common arc-length parameterisation
when vertex counts differ. Pairing vertices by index — the obvious implementation —
visibly scrambles a shape the moment an annotator inserts a vertex.

### Collaboration and review

| | |
| --- | --- |
| Organizations, six roles, membership | **Done** |
| Jobs as the unit of assignment (parallel annotation on one task) | **Done** |
| Review state machine: `new → in_progress → submitted → accepted/rejected` | **Done** |
| Issues and comment threads anchored to a frame and shape | **Done** |
| Annotation history | **Done** |
| Ground-truth quality reports | **In Progress** |

Nobody reviews their own annotation work — the policy enforces it, and there is a test that
says so.

### Import and export

Four formats, each declaring **honestly** what it can represent:

| Format | Round-trips |
| --- | --- |
| **COCO** | rectangles, polygons, keypoints |
| **YOLO** | boxes, segmentation polygons |
| **Pascal VOC** | bounding boxes |
| **CurveVision JSON** | everything, losslessly |

An export that would drop annotations tells you **before** you rely on it — in a response
header and inside the archive. Silently dropping polylines is how people discover a broken
dataset during training, which is far too late.

Adding a format is one class and one `register()` call; third-party formats can register
through an entry point without touching this repository.

### API, SDK and CLI

Everything the UI does, a script can do — there is no private API surface.

```python
from curvevision_sdk import CurveVision

with CurveVision("https://curvevision.example.com", token="cv_...") as cv:
    task = cv.create_task(project.id, name="Batch 1")
    cv.upload(task.id, glob("frames/*.jpg"))
    cv.export(project.id, format="yolo", destination="dataset.zip")
```

```bash
curvevision login https://curvevision.example.com --username alice
curvevision task create <project-id> --name "Batch 1" --upload ./images/*.jpg
curvevision export <project-id> --format coco --output dataset.zip
```

OpenAPI 3.1 at `/api/v1/openapi.json`, generated from the same models that validate
requests — so it cannot drift from the implementation. Swagger UI at `/api/docs`.

### Self-hosting

Six services: API, worker, web, PostgreSQL, Redis, and an S3-compatible store.

That number is a deliberate design goal. Fewer moving parts to operate, back up and reason
about is the difference between "we self-host this" and "we meant to self-host this". Your
media never leaves your infrastructure, and there is no phone-home.

### Desktop application

*Planned.* A Tauri shell around the same web bundle, adding local dataset access without an
upload round trip and offline annotation with sync. `desktop/` holds the design; there is
no working build yet, and we would rather say so than ship a stub.

---

## Architecture

```
  Browser ──┐
  SDK/CLI ──┼──▶  FastAPI (async)  ──▶  PostgreSQL
  CI      ──┘      │  policy · services · domain        Redis (cache + broker)
                   │  ┌────────┬─────────┬────────┐     S3 / MinIO
                   │  │storage │ formats │   ml   │
                   │  └────────┴─────────┴────────┘
                   └──▶ Dramatiq worker ──▶ your model server
```

A **modular monolith**: one API image, one worker image, module boundaries enforced by
explicit interfaces rather than network hops. `Storage`, `DatasetFormat`, `ModelProvider`
and `JobQueue` are the seams — swappable, and unit-testable with no database.

The whole server test suite runs on SQLite with an in-process queue and local files, so a
contributor can clone the repo and run `pytest` with nothing installed.

* [ARCHITECTURE.md](docs/ARCHITECTURE.md) — the design, and an
  [OSS build-vs-extend table](docs/ARCHITECTURE.md#open-source-building-blocks--build-vs-extend-decisions)
  giving the reasoning for every dependency choice.
* [ARCHITECTURE_COMPARISON.md](docs/ARCHITECTURE_COMPARISON.md) — an honest comparison with
  CVAT: what we learned from it, where we differ, what it still does better, and the
  licensing position.
* [IMPLEMENTATION_PLAN.md](docs/IMPLEMENTATION_PLAN.md) — the plan of record.

### Why these choices

| Decision | Reasoning |
| --- | --- |
| FastAPI + Pydantic v2 | One type declaration drives validation, OpenAPI, the SDK and the client types |
| SQLAlchemy 2.0 typed ORM | Real static typing over the domain; explicit transaction boundaries |
| Argon2id via `argon2-cffi` | The PHC winner and OWASP recommendation. `passlib` has been unmaintained since 2020, and auth is a poor place for stale code |
| In-process policy engine | The decision is a pure function of already-loaded rows. A second process and a policy language is a poor trade for self-hosters |
| Canvas2D + R-tree, not SVG | Rendering cost tracks the viewport, not the dataset |
| An HTTP inference contract, not a serving platform | Every serving stack already speaks it; model licenses stay on the operator's side |
| Streaming format registry | A server exporting 500k images cannot hold the dataset in memory |

---

## Roadmap

**MVP is complete** — you can install CurveVision today and produce a real dataset.

**Beta** needs video annotation end to end, the track-editing timeline, and webhook
retries. **1.0** needs distributed rate limiting, backup tooling, and an external security
review — we will not call a release 1.0 before that, because it would be a claim we cannot
back.

Full detail, including what we have decided *against*, in [ROADMAP.md](docs/ROADMAP.md).

---

## Free and open source, without an asterisk

CurveVision's core is complete. There is no crippled edition, no feature held back to
create a paid tier, no annotation limit.

If commercial services ever exist around this project, they will be infrastructure —
managed hosting, support contracts, hosted inference — never the ability to annotate your
own data. Anything else would defeat the point of building it.

Licensed **MIT**. Self-host it, fork it, build a product on it.

---

## Contributing

Contributions of every size are welcome. The items marked *In Progress* in the roadmap have
settled designs and are the easiest places to start.

* [CONTRIBUTING.md](docs/CONTRIBUTING.md) — how to work on it
* [DEVELOPMENT.md](docs/DEVELOPMENT.md) — how to run it
* [CODE_OF_CONDUCT.md](CODE_OF_CONDUCT.md)
* [SECURITY.md](docs/SECURITY.md) — including a candid list of known limitations

```bash
./scripts/dev.sh      # infra in Docker, app on the host with hot reload
./scripts/check.sh    # everything CI runs
```

---

## Community

* **Questions and ideas** — [GitHub Discussions](https://github.com/Derric01/CurveVision/discussions)
* **Bugs and features** — [Issues](https://github.com/Derric01/CurveVision/issues)
* **Security** — privately, per [SECURITY.md](docs/SECURITY.md)

If you use CurveVision for something, we would genuinely like to hear about it — what
worked, and more usefully, what did not.

---

## Acknowledgements

CurveVision exists in a landscape that [CVAT](https://github.com/cvat-ai/cvat) largely
defined, and we studied it deliberately rather than rediscovering its lessons the expensive
way. **No CVAT source code is present in this repository**; what we took was architectural
understanding, and we document exactly what and why. We claim no affiliation with, or
endorsement by, CVAT.ai Corporation or Intel Corporation.

CurveVision is built on other people's well-maintained code, and that is the point. See
[THIRD_PARTY_NOTICES.md](docs/THIRD_PARTY_NOTICES.md) for every dependency, its license and
its authors.

---

<div align="center">

**Build better vision datasets.**

MIT licensed · [Derric01/CurveVision](https://github.com/Derric01/CurveVision)

</div>
