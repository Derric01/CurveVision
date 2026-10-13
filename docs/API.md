# API

CurveVision exposes one REST API at `/api/v1`. The web application, the Python SDK and the
CLI are all clients of it — there is no private surface, so anything the UI can do, a script
can do.

Interactive documentation ships with the server:

* **Swagger UI** — `/api/docs`
* **ReDoc** — `/api/redoc`
* **OpenAPI 3.1 schema** — `/api/v1/openapi.json`

The schema is generated from the same Pydantic models that validate requests, so it cannot
drift from the implementation.

## Authentication

Two credential types share the `Authorization: Bearer` header.

### Session tokens — for browsers

```http
POST /api/v1/auth/login
{ "identifier": "alice", "password": "…" }

→ { "access_token": "eyJ…", "refresh_token": "eyJ…", "expires_in": 1800 }
```

Access tokens are short-lived. Refresh tokens **rotate**: `POST /api/v1/auth/refresh`
returns a new pair and invalidates the one you sent. Replaying a refresh token fails, which
is what makes a stolen one usable at most once.

### Single sign-on — through an OpenID Connect provider

When the server is configured for it ([OPERATIONS.md](./OPERATIONS.md#single-sign-on)),
`GET /api/v1/auth/options` says so (`"sso": true`, and the button's label). A sign-in is
three steps, and ends with the same token pair as a password:

```http
POST /api/v1/auth/sso/start        { "return_to": "/projects" }
→ { "authorization_url": "https://idp…/auth?…", "state": "eyJ…" }

# The browser goes to authorization_url; the provider sends it back to
# <oidc_redirect_uri>?code=…&state=…  — check `state` is the one you were given.

POST /api/v1/auth/sso/complete     { "code": "…", "state": "eyJ…" }
→ { "access_token": "…", "refresh_token": "…", "expires_in": 1800, "return_to": "/projects" }
```

The server keeps no login state: `state` is signed and expires in ten minutes. Comparing
it with the one you were given is the *client's* job — it is what stops a sign-in started
elsewhere being finished in your browser — and the web application does it.

### API tokens — for scripts, CI and the SDK

```http
POST /api/v1/auth/tokens
{ "name": "nightly-export" }

→ { "id": "…", "token": "cv_a1b2c3d4_xY9…" }
```

**The token is shown once.** Only a hash is stored. Tokens do not expire unless you set
`expires_at`, and each can be revoked individually — which is why they, not passwords, are
the right credential for anything unattended. The app mints, lists and revokes them on the
*Workspace* page (*API tokens* in the desktop application), with a 90-day expiry by default.

## Conventions

* **Errors** are RFC 9457 `application/problem+json`:

  ```json
  {
    "type": "https://curvevision.dev/errors/validation_error",
    "title": "Request validation failed",
    "status": 422,
    "detail": "One or more fields are invalid",
    "errors": [{ "location": ["body", "points"], "message": "polygon requires at least 3 points", "type": "value_error" }]
  }
  ```

  An annotation batch (`PATCH /jobs/{id}/annotations`) is one transaction and is refused
  whole, but a refusal of one of its entries names that entry the same way — a `422` for
  `"Attribute 'plate' is required"`, or a `404` for a shape the job does not have, carries
  `"errors": [{"location": ["body", "created_shapes", 1], ...}]`. A client can set that entry
  aside and send the rest; the editor's autosave does exactly that.

  A create carries a `client_id`, unique per job. Sending one again — a retry after its
  answer was lost — is answered with the object it already made, in `id_map`; the same id
  on a different object is a named `422`.

* **Pagination** is offset-based with one envelope everywhere:
  `{ "count": 412, "limit": 50, "offset": 0, "results": [...] }`

* **Unknown request fields are rejected**, not ignored. A silently dropped typo is how
  clients ship bugs that look like server bugs.

* **404 rather than 403** for resources outside your organizations, so their existence is
  not observable.

* **Status codes**: `200` read/update, `201` created, `204` deleted, `401` unauthenticated,
  `403` not permitted, `404` absent or invisible, `409` conflict (stale version, locked
  job, released dataset version), `422` validation, `429` rate limited.

## Resources

| Area | Endpoints |
| --- | --- |
| Auth | `/auth/register`, `/auth/login`, `/auth/refresh`, `/auth/logout`, `/auth/me`, `/auth/password`, `/auth/tokens` |
| Organizations | `/organizations`, `/organizations/{id}/members` |
| Projects | `/projects`, `/projects/{id}`, `/projects/{id}/labels`, `/projects/{id}/statistics` |
| Tasks | `/tasks`, `/tasks/{id}`, `/tasks/{id}/assets`, `/tasks/{id}/jobs`, `/tasks/{id}/progress` |
| Media | `/tasks/{id}/media`, `/tasks/{id}/media/recount`, `/tasks/{id}/frames/{frame}`, `/tasks/{id}/frames/{frame}/data` |
| Jobs | `/jobs`, `/jobs/{id}`, `/jobs/{id}/review` |
| Annotations | `/jobs/{id}/annotations`, `/jobs/{id}/frames/{frame}/annotations`, `/jobs/{id}/history` |
| Review | `/jobs/{id}/issues`, `/jobs/{id}/issues/{id}/comments` |
| Quality | `/tasks/{id}/ground-truth`, `/jobs/{id}/quality`, `/tasks/{id}/quality` |
| Datasets | `/projects/{id}/export`, `/tasks/{id}/import`, `/projects/{id}/versions`, `/projects/{id}/versions/{id}/release`, `/projects/{id}/versions/{id}/export` |
| AI | `/models`, `/models/discover`, `/jobs/{id}/inference`, `/jobs/{id}/suggestions` |
| Integrations | `/webhooks` |
| System | `/health`, `/formats`, `/background-tasks` |

### Members, and who manages whom

```http
POST /api/v1/organizations/{id}/members   { "identifier": "ann@example.com", "role": "annotator" }
PATCH /api/v1/organizations/{id}/members/{membership}   { "role": "reviewer" }
```

Someone is added by the username or email they signed up with. Managing members takes
admin rank; below the owner, a member changes or removes only members ranked below them and
gives only roles below their own (`403` otherwise) — CVAT's rule. An owner manages anyone,
and the last owner can be neither demoted nor removed (`409`). *Until iteration 66 an admin
could make themselves owner and then demote the owner.* The app does all of this on the
*Workspace* page.

## Frame counts, and when to trust one

A video task is created with an **estimated** frame count. An exact one means decoding the
whole file, which cannot happen inside an upload request, so the estimate comes from
container metadata — `stream.frames` where the container declares it, and
`int(duration x frame_rate)` where it does not. Both drift, and the damaging direction is
high: the task then offers frames that do not exist, and an annotator who steps onto one
meets what looks like missing media.

A background job (`media.probe_task`) decodes the file and replaces the estimate, normally
within seconds of upload. It can also fail to: the task already carries annotations, so its
frame ranges are not the job's to move; or the file is truncated, moved, or encoded with
something the build cannot decode.

```http
GET /api/v1/tasks/{task_id}/media
```

```json
{
  "task_id": "…",
  "media_kind": "video",
  "frame_count": 13,
  "frames_per_chunk": 36,
  "chunk_count": 1,
  "frame_count_exact": false,
  "estimated_assets": ["truncated.mkv"],
  "estimated_asset_count": 1
}
```

`frame_count_exact` is false while any asset's count is still an estimate.
`estimated_assets` names up to five of them so a warning can say *which file*;
`estimated_asset_count` is the true total. `AssetOut.frame_count_exact` carries the same
flag per asset. An image is always exact — it contributes exactly one frame by definition,
and nothing has to decode it to know that.

```http
POST /api/v1/tasks/{task_id}/media/recount
```

Queues a fresh count. Returns `202` and the background task, not a result: counting a
two-hour clip is minutes of decoding, which is why it is not on the request path. Poll the
background task, or re-read `GET /tasks/{id}/media` and watch `frame_count_exact`. Requires
`update` on the task, because a corrected count repartitions the task's jobs; reading the
warning requires only read access, since the annotator who meets the missing frame is the
one who needs to see it.

A task whose count cannot be established keeps its estimate rather than being zeroed —
a wrong number is more useful than no media at all — and stays flagged until something
counts it or the offending asset is removed.

## Annotations

### Reading

```http
GET /api/v1/jobs/{job_id}/annotations
```

Returns the whole job: `shapes` (one geometry on one frame), `tracks` (an object over time,
as keyframes) and `tags` (classifications), plus the current `annotation_version`.

Tracks come back **unexpanded**. Clients that want the server to interpolate can ask per
frame instead:

```http
GET /api/v1/jobs/{job_id}/frames/{frame}/annotations
```

That materialises every track at that frame, with `source: "interpolated"` on positions
that were not authored by a human. The editor interpolates client-side for scrubbing speed
and uses the same algorithm; both are held to the same test vectors.

### Writing

One **batch document** per save, not one request per object:

```http
PATCH /api/v1/jobs/{job_id}/annotations
{
  "annotation_version": 7,
  "created_shapes": [
    { "client_id": "tmp-1", "label_id": "…", "frame": 0,
      "shape_type": "rectangle", "points": [10, 20, 110, 220],
      "attributes": { "colour": "red" } }
  ],
  "updated_shapes": [...],
  "deleted_shapes": ["…"]
}

→ { "annotation_version": 8,
    "created": { "shapes": 1 },
    "id_map": { "tmp-1": "3f2a…" } }
```

Two things make this work:

* **`annotation_version` is optimistic concurrency.** Send the version you last read. If
  the job has moved on, the write is rejected with `409` and the versions in the body —
  rather than one annotator silently overwriting another.
* **`client_id` reconciles optimistic objects.** The editor creates an object locally with
  a temporary id; `id_map` tells it the server id without a round trip per object.

Omit `annotation_version` to write unconditionally. That is right for a script that owns
the job and wrong for an interactive editor.

Geometry is `[x1, y1, x2, y2, …]` in **image pixel space**. Rectangles are two corners,
ellipses are `[cx, cy, rx, ry]`, polygons and polylines are vertex lists.

## Quality: scoring work against ground truth

A task can hold one **ground-truth job** — the answer key a reviewer annotates as carefully
as they can. Every other job on the task is then scored against it.

```http
POST /api/v1/tasks/{id}/ground-truth   { "start_frame": 0, "stop_frame": 49 }
POST /api/v1/jobs/{id}/quality         { "iou_threshold": 0.5 }
GET  /api/v1/jobs/{id}/quality
GET  /api/v1/tasks/{id}/quality
```

Both frame bounds are optional and default to the whole task. Narrowing the range is the
normal case on video: checking 50 frames properly beats checking 5,000 carelessly.

A report carries overall `precision`, `recall` and `f1`, plus `details` with a per-label
breakdown and every conflict named:

```json
{
  "iou_threshold": 0.5, "precision": 0.9, "recall": 0.75, "f1": 0.818,
  "annotation_version": 14,
  "details": {
    "compared_frames": 50, "matched": 18, "missing": 6, "extra": 2, "mean_iou": 0.87,
    "per_label": {
      "3f2a…": { "matched": 12, "missing": 1, "extra": 0,
                 "precision": 1.0, "recall": 0.923, "f1": 0.96, "mean_iou": 0.91 }
    },
    "conflicts": [
      { "kind": "missing", "frame": 12, "label_id": null,
        "expected_label_id": "3f2a…", "ground_truth_shape_id": "…", "iou": null },
      { "kind": "wrong_label", "frame": 30, "shape_id": "…",
        "label_id": "9c17…", "expected_label_id": "3f2a…", "iou": 0.88 },
      { "kind": "poor_overlap", "frame": 31, "shape_id": "…",
        "label_id": "3f2a…", "expected_label_id": "3f2a…", "iou": 0.41 },
      { "kind": "extra", "frame": 44, "shape_id": "…",
        "label_id": "9c17…", "expected_label_id": null }
    ]
  }
}
```

`per_label` is keyed by label id, as everything else in this API is; resolve names from
`GET /projects/{id}/labels`. A conflict's two label fields each mean exactly one thing on
every kind: **`label_id` is the annotated shape's label** — `null` on `missing`, where
nothing was annotated — and **`expected_label_id` is the ground truth's** — `null` on
`extra`, where the ground truth has nothing there. They can differ on a `poor_overlap` as
well as a `wrong_label`, because candidates are paired by geometry rather than by label.

`mean_iou` averages over matched pairs only — it answers "how tight were the boxes you got
right", not "how right were you"; that is what `f1` is for.

Six things worth knowing about what the number means:

* **A box drawn too loosely costs precision as well as recall.** Below the threshold it is
  not that object, so it is a false positive exactly as an invented box is. It is reported
  once, as `poor_overlap` rather than as both a miss and an extra.

* **Only frames the ground truth covers are scored.** A ground truth over frames 0-49 says
  nothing about frame 300, and counting unchecked work as correct would inflate the score
  in exactly the direction that makes a team trust bad data.
* **Tracks and shapes are compared on equal terms.** A track is evaluated at its
  interpolated position on every frame, so an annotator using tracks and a reviewer using
  shapes score the same.
* **Geometry is exact, not bounding-box.** Polygons are clipped against one another
  (Sutherland–Hodgman) and measured by the shoelace formula; two triangles sharing a
  bounding box score near zero, not one.
* **Reading the ground truth's annotations takes reviewer rank**, or assignment to that
  job. An annotator who can read the answer key makes the score meaningless.

* **A report says which version of the job it scored.** `annotation_version` is the job's
  version at the moment of the comparison. Compare it against the job's `annotation_version`
  now: if they differ, the annotations have moved on and the score describes work that no
  longer exists. Without that, the failure runs the wrong way — an annotator fixes
  everything, the old F1 still reads 0.5, and a reviewer rejects the job on a measurement of
  something that is no longer there. It is `null` on reports written before the server
  recorded it, which is an honest "cannot say" rather than a guess.

Computing a report is a `review` action; a ground-truth job cannot be scored against
itself. The comparison runs inline rather than as a background job.

The whole loop has a UI. The **task page** creates the ground-truth job — both frame bounds
optional, blank meaning the whole task — marks it as the answer key rather than listing it
among ordinary work, and shows each job's latest F1 beside its state, flagged when the job
has changed since. The **editor** shows the report itself: the three scores, a per-label
breakdown ordered worst-first, and the conflicts as a list where **clicking one seeks to its
frame** — a conflict is a place, not a statistic.

## Overlapping jobs, and what export does with them

A task created with `segment_size` and `overlap` splits into jobs that deliberately share
frames, so a track can stay continuous across a job seam — whoever annotates job 2 can see
where the object was at the end of job 1.

```http
POST /api/v1/tasks   { "project_id": "…", "name": "Batch 1",
                       "segment_size": 500, "overlap": 20 }
```

Those shared frames get annotated twice, so **export reconciles them rather than
concatenating**. On a shared frame, two shapes are the same object when they carry the same
label, the same shape type, and geometry agreeing above 0.75 IoU; the copy from the earlier
job is kept. Pairing is an optimal one-to-one assignment, not a greedy sweep, so two objects
close together are both matched rather than one being stranded and shipped as a duplicate.

Three things are deliberately **not** merged:

* **Different labels.** Two annotators disagreeing about what an object is, is a
  disagreement for review — collapsing it would silently pick one of them.
* **Shapes that enclose no area** (polylines, point sets, skeletons). IoU says nothing about
  them, so they are left as two objects rather than merged on a guess.
* **Anything inside a single job.** Two close boxes one annotator drew are their business.

Track identity survives the seam: a track matched across the boundary is exported under one
`track_id` for its whole life, rather than appearing to vanish and be replaced. Track ids are
allocated per task, so two unrelated tracks in different jobs never collide.

## Import and export

```http
GET  /api/v1/formats
POST /api/v1/projects/{id}/export      { "format": "coco", "only_accepted": true }
POST /api/v1/tasks/{id}/import?format=coco   (multipart: file=<archive.zip>)
```

`GET /formats` returns what each format can and cannot represent. An export that would drop
annotations says so **before** you rely on it — in the `X-CurveVision-Warnings` response
header and in a note inside the archive:

```
X-CurveVision-Warnings: COCO 1.0 cannot represent polyline; those annotations were omitted.
```

Silently dropping annotations is how people discover a broken dataset during training,
which is far too late.

**COCO is held to pycocotools**, the reader Detectron2, MMDetection and torchvision go
through (`tests/unit/test_coco_reference.py`): the document loads, every segmentation
rasterises, each `bbox` is the tight box of what it covers, and `COCOeval` scores the ground
truth against itself at AP 1.0 for boxes, masks and keypoints. Conventions it follows:

* **Masks** are uncompressed RLE, column-major over the whole image, with `iscrowd: 0` —
  instances to train on. (CVAT writes masks with `iscrowd: 1`, which Detectron2 ignores as
  crowd regions.) Compressed RLE strings are read on import.
* **Rotated boxes** are written as their exact four-corner polygon, with the enclosing
  axis-aligned box as `bbox`; they read back as polygons.
* **Keypoints**: an absent joint is `0,0,0`, `num_keypoints` counts labelled joints
  (visible or occluded), and `bbox` covers only those. YOLO Pose writes absent joints as
  `0 0 0` too, as Ultralytics' own COCO converter does.
* **Pascal VOC** coordinates are 0-based floats, as Datumaro (CVAT's exporter) writes and
  reads them — not the devkit's 1-based integers.

**The other formats are held to the readers people use** (`tests/unit/test_export_conformance.py`),
checked in iteration 66 against Datumaro and CVAT's own XML reader:

* **YOLO** writes `images/train` and `labels/train`, which is what `data.yaml` names; `val`
  is the training images when no frame is in a `val` subset (Ultralytics requires one).
  A dataset with any polygon is written as segments throughout — a box as its four
  corners — because Ultralytics reads a file mixing box and polygon lines as all segments,
  turning each box into a wrong one. Boxes are clipped to the image before they become
  centre and size. YOLO classification is `train/<class>/<image>`. OBB and Pose name the
  `val` split the same way, and Pose writes `flip_idx` — which joint each becomes when the
  image is mirrored — when the joints' names say it (`left_eye`/`right_eye`, `l_wrist`,
  `Tail_L`): without it Ultralytics turns flip augmentation off, and a guessed one would
  teach a model that a left eye is a right eye, so none is written when the names do not
  decide it. `data.yaml` has no `path:` key: Ultralytics resolves one against the working
  directory, so the `path: .` written until iteration 67 failed with "images not found"
  anywhere but inside the export.
* **Ultralytics itself reads every YOLO export and trains on it**
  (`scripts/verify_ultralytics.py`, run by `.github/workflows/ultralytics.yml` whenever an
  exporter changes and weekly): its own dataset classes load every image with no corrupt
  or missing labels, each class, box, polygon, oriented corner and keypoint matches what was
  annotated, and one epoch of each task trains through validation.
* **CVAT XML** writes `original_size`, rotated boxes as `<box rotation>`, skeletons as
  `<skeleton>` with each joint declared as a label under its parent, and no closing frame
  past the last frame.
* **Pascal VOC** writes its labelmap as `name:r,g,b::`, and a rotated box as the box around
  its rotated corners. **Segmentation masks** list their frames in
  `ImageSets/Segmentation/default.txt`. **MOTChallenge** puts its label list at
  `gt/labels.txt`.

### What an import keeps

```http
POST /api/v1/tasks/{id}/import?format=cvat_xml&conflict_policy=append
→ { "shapes_imported": 41, "tracks_imported": 3, "tags_imported": 0, "frames_matched": 120,
    "labels_created": [], "warnings": ["Dropped attributes the project's labels do not declare: car.speed"] }
```

Frames are matched to the task's media by image name. `conflict_policy=replace` clears the
task's jobs first; `append`, the default, adds to what is there.

* **Attributes** are kept where the label declares them and accepts the value. The rest
  are dropped and named in `warnings` — CVAT's importer, by default, also keeps only declared
  ones. Storing a value the label would refuse makes the annotation's next save fail.
* **Tracks** come back as tracks. Every format sees a track as one record per frame it is on;
  the import gives each listed frame a keyframe, and marks the object as gone (`outside`)
  wherever the file stops listing it — between two runs and after the last. A track that
  crosses into another job becomes one track in each, and one listed on a single frame is
  stored as a shape, which is how a tracked CVAT dump writes a shape that was never tracked.
  *Until iteration 62 every imported shape lost its attributes, and a track came back as one
  unrelated box per frame.*
* **Skeleton joints** are matched to the label's joints by name.
* **Provenance is not carried in**: everything an import writes is `source: "imported"`,
  whatever the file says it was.

### Dataset versions

```http
POST /api/v1/projects/{id}/versions                      { "name": "v1.0" }       → draft
POST /api/v1/projects/{id}/versions/{version}/release                             → released
POST /api/v1/projects/{id}/versions/{version}/export     { "format": "yolo", "include_images": true }
```

**Releasing freezes the annotations.** The release stores them in the lossless native
format, with each frame's image hash, and records a `content_hash` and statistics. Exporting
a released version returns those annotations in any format, however the project has
changed since — the dataset an experiment is reproduced from (`X-CurveVision-Content-Hash`
on the response names it). A draft exports the project's current annotations for its tasks.
A released version cannot be changed or deleted, only superseded.

* Images come from storage by hash. One whose every task has since been deleted is gone;
  the export lists the frames that lost theirs.
* A frozen copy altered in storage is refused (`409`): its hash is checked on every export.
* Versions released before iteration 66 kept only the hash and statistics, and answer
  `409` saying so. *Until then, "exporting a release" read the project as it was at export
  time.*

### Deleting

Deleting a task or a project deletes its jobs, annotations, reviews and versions, and
queues the removal of its images and videos from storage — except any another task still
uses (images are stored once per content hash). *Until iteration 66 nothing queued that
job, and every deleted task's media stayed on disk.* The app asks for the name to be typed
before deleting either.

### Shipped formats

| Format | Import | Export | Carries | Use it for |
| --- | :-: | :-: | --- | --- |
| **COCO** | ✓ | ✓ | boxes, rotated boxes (as polygons), polygons, masks (RLE), keypoints | the default for detection and segmentation |
| **YOLO** | ✓ | ✓ | boxes, segmentation polygons | training a YOLO model directly |
| **YOLO OBB** | ✓ | ✓ | **oriented** boxes — the angle survives | aerial and satellite imagery, industrial inspection, document layout |
| **YOLO Pose** | — | ✓ | skeletons as keypoints with visibility | human and animal pose models |
| **YOLO Classification** | — | ✓ | one whole-image tag, as a directory tree | image classification |
| **Pascal VOC** | ✓ | ✓ | boxes | older toolchains that expect it |
| **KITTI** | ✓ | ✓ | boxes, truncation, occlusion | robotics and autonomous driving |
| **MOTChallenge** | ✓ | ✓ | boxes **with object identity across frames** | tracking; anything where "the same object" matters |
| **CVAT XML** | ✓ | ✓ | boxes, polygons, polylines, points, ellipses, masks, tags, attributes, **tracks** | moving a project in or out of CVAT; the most expressive format here |
| **Segmentation mask** | — | ✓ | indexed PNG, one class per pixel | semantic segmentation; perception layers |
| **CurveVision JSON** | ✓ | ✓ | everything | backups and instance-to-instance moves |

**KITTI's 3D columns are written as the devkit's "unknown" values, not invented.**
CurveVision annotates 2D images and has no 3D extent to report; zeros and a rotation of −10
(outside the valid range) let a reader tell, where plausible-looking numbers would not.

**Segmentation masks are export-only on purpose.** A mask does not record the polygons it
was painted from, and tracing contours back out would produce shapes with hundreds of
vertices that no annotator drew. Import COCO or CVAT XML instead.

**A rotated rectangle keeps its angle only in YOLO OBB and CVAT XML.** Plain YOLO, COCO,
KITTI and MOT have no oriented-box primitive, so a rotated shape is written as the
axis-aligned box around its *rotated corners* — the smallest straight box that actually
contains the object. That is the right answer for a detection dataset and it is still a
loss, so `GET /formats` says so before you rely on it.

## AI-assisted annotation

CurveVision never bundles model weights. A model is either a **hosted service** called with
an API key, or **an endpoint you run** that speaks the contract below; either way the
platform calls it, and predictions land as suggestions.

### Hosted services, by API key

Two built-in providers ([ADR 0010](./adr/0010-hosted-vision-language-models.md)):

| `provider` | `config` | Reaches |
| --- | --- | --- |
| `anthropic` | `{"api_key": "sk-ant-…", "model": "claude-opus-5-5"}` | Claude, through Anthropic's official SDK |
| `openai_compatible` | `{"base_url": "…", "api_key": "…", "model": "…"}` | Anything that accepts the OpenAI Chat Completions shape |

Base URLs the Models page uses for `openai_compatible`: OpenAI `https://api.openai.com/v1`,
Google Gemini `https://generativelanguage.googleapis.com/v1beta/openai`, xAI
`https://api.x.ai/v1`, OpenRouter `https://openrouter.ai/api/v1`, Mistral
`https://api.mistral.ai/v1`, Groq `https://api.groq.com/openai/v1`, Together
`https://api.together.xyz/v1`, a local Ollama `http://localhost:11434/v1` (no key).

```http
POST /api/v1/models/discover?organization_id={id}
{ "provider": "anthropic", "config": { "api_key": "sk-ant-…" } }
→ { "models": [ { "id": "claude-opus-5-5", "name": "Claude Opus 5.5", "kind": "detector",
                  "open_vocabulary": true, … } ] }
```

Discovery lists only models that take images — from Anthropic's Models API capabilities,
from OpenRouter's `input_modalities`, and elsewhere by leaving out ids that are plainly not
chat models. Register one with `kind: "detector"` and `open_vocabulary: true`.

**What a run does:** one request per frame (four at a time), the frame downscaled to at
most 1568 px and 1.15 megapixels as JPEG, asking for the project's labels by name and for
boxes in coordinates normalised to 0–1000, which the server converts to pixels. A frame
whose answer cannot be read is named in `warnings` and the rest are kept. For Claude, the
answer is held to a JSON schema where the model supports structured outputs, and
`fallbacks: "default"` re-runs a frame a safety classifier declines on Anthropic's
recommended substitute, on the models that support it. A refused key, an unknown model or a
rate limit is a 502 that says which.

### Registering a model

**From the application**, the **Models** page does this: paste the endpoint's URL and, if it
wants one, its API key, and press *Connect*. CurveVision asks the endpoint what it serves
(below), fills in the model's name, kind and labels from the answer, and adds it once you
have checked them. It is then in the editor's Auto-annotate panel for everyone in the
workspace. A wrong key or an endpoint that is not answering is reported at *Connect*, before
anything is saved. Adding a model needs the maintainer rank; running one, the annotator rank.

Before registering, ask an endpoint what it serves — the same request a registration makes
the server send, so it needs the same rank:

```http
POST /api/v1/models/discover?organization_id={id}
{ "config": { "endpoint": "http://my-inference:9000/infer", "api_key": "…" } }

→ { "models": [ { "id": "yolo-v8n", "name": "YOLOv8 nano", "kind": "detector",
                  "labels": ["person", "car"], "shape_types": ["rectangle"],
                  "open_vocabulary": false, "description": null } ] }
```

`models` is empty when the endpoint answers `GET /models` with 404, 405 or 501 — it is up
and does not implement discovery, so the labels are named by hand. Anything else that goes
wrong is a **502** whose `detail` says what: nothing listening, the key refused (401/403),
an error, or an answer that is not a model list.

**From the API**:

```http
POST /api/v1/models?organization_id={id}
{
  "slug": "yolo-v8n",
  "name": "YOLOv8 nano",
  "kind": "detector",
  "provider": "http",
  "config": { "endpoint": "http://my-inference:9000/infer", "api_key": "…", "model": "yolo-v8n" },
  "output_labels": ["person", "car"]
}
```

`config.api_key`, when set, is sent as `Authorization: Bearer …` on every request to the
endpoint; it is never returned by the API. `config.model` is the name the endpoint knows the
model by and is sent as `model` on each inference request; it defaults to the slug.

### Fixed-head and open-vocabulary models

A detector trained on a fixed set of classes can only find those, and `output_labels` is the
whole of it. **Open-vocabulary** detectors — YOLO-World, Grounding DINO, OWL-ViT — take the
class names as *text at inference time* instead, so they can find "forklift" without anybody
training a forklift detector. Say so when you register one:

```http
POST /api/v1/models?organization_id={id}
{
  "slug": "yolo-world",
  "name": "YOLO-World",
  "kind": "detector",
  "provider": "http",
  "config": { "endpoint": "http://my-inference:9000/infer" },
  "output_labels": ["person", "car"],
  "open_vocabulary": true
}
```

For such a model `output_labels` is a **default, not a boundary**. The flag defaults to
`false`, which is the safe reading: a prompt sent to a fixed-head server is dropped without
comment.

The two kinds fail in opposite directions, so CurveVision treats them differently rather
than passing everything through:

| | fixed head | open vocabulary |
| --- | --- | --- |
| `classes` requested | **422**, naming what the model *can* find | sent as given |
| no `classes` requested | `classes: []` | falls back to **the project's own label names** |

That fallback is the everyday path: a project that has declared `forklift` and `pallet` has
already said what it is looking for, so *auto-annotate this job* needs no extra input. An
open-vocabulary model with neither requested classes nor a project schema is a 422 too —
asking such a model for nothing returns nothing, which reads as a broken model.

Joints of a skeleton label are excluded from that fallback: "left wrist" is part of an
object, not an object to go looking for.

### The inference contract

Any service implementing this works — Triton, TorchServe, BentoML, Ray Serve, a hosted
vendor, or a script. **Request:**

```json
{
  "model": "yolo-world",
  "confidence_threshold": 0.5,
  "classes": ["forklift", "pallet", "hi-vis vest"],
  "frames": [
    { "frame": 0, "width": 1920, "height": 1080,
      "content_type": "image/jpeg", "image": "<base64>" }
  ]
}
```

`classes` is empty for a fixed-head model, and a server that does not read open-vocabulary
prompts can ignore the key entirely. `GET <endpoint>/models` may advertise
`"open_vocabulary": true` per model for discovery.

**Response:**

```json
{
  "shapes": [
    { "frame": 0, "label": "person", "type": "rectangle",
      "points": [10, 20, 110, 220], "confidence": 0.93 }
  ],
  "tags": [],
  "warnings": []
}
```

Coordinates are absolute pixels. Return `"normalised": true` at the top level to send
`[0,1]` coordinates instead, and CurveVision rescales — a skeleton's joints included.

A **skeleton** names its joints, since a model cannot know the project's label ids:

```json
{ "frame": 0, "label": "person", "type": "skeleton", "points": [], "confidence": 0.9,
  "elements": [ { "name": "nose", "points": [412, 160] },
                { "name": "left_wrist", "outside": true } ] }
```

Each joint is matched by name to a joint the mapped label declares, and stored in the
label's order. A declared joint the model did not return is stored as not visible (as the
editor records a skipped one); a joint the label does not declare is dropped. A skeleton
mapped to a label that declares no joints is not stored, and the run's `warnings` say so
(`person (no joints) x3`). Dataset imports match joints the same way.

Optionally implement `GET <endpoint>/models` returning `{"models": [...]}` — each entry an
`id` (what CurveVision will send as `model`), a `name`, a `kind`, its `labels`, and
`"open_vocabulary": true` if it reads `classes`. The Models page calls it when somebody adds
your endpoint, so implementing it is what makes adding your model a URL and a key; without
it (answer 404), they type the labels in. What it returns is copied onto the registration
when it is saved, not re-read on each run. *Until iteration 61 nothing called it.*

### Running it

```http
POST /api/v1/jobs/{job_id}/inference
{
  "model_id": "…",
  "job_id": "…",
  "frames": [0, 1, 2],
  "label_mapping": { "person": "<project label id>" },
  "confidence_threshold": 0.5,
  "persist": true
}
```

Predictions land as ordinary annotations with `source: "model"` and a confidence. They are
selectable, editable and deletable like anything else, and shown distinctly in the editor.

**Model labels must be mapped explicitly.** An unmapped output is dropped and reported —
inventing project labels from a model's vocabulary is how label schemas rot.

Editing a prediction flips its `source` to `model_corrected`, so the dataset records that a
human fixed a machine's guess. That distinction survives to export and matters for auditing
dataset quality.

Set `persist: false` to preview without writing — the interactive path.

Accept or reject in bulk:

```http
POST /api/v1/jobs/{job_id}/suggestions
{ "shape_ids": ["…"], "accepted": false }
```

## Webhooks

```http
POST /api/v1/webhooks?organization_id={id}
{ "target_url": "https://example.com/hook", "events": ["job.accepted", "task.completed"] }

→ { "secret": "…" }   // shown once
```

Deliveries carry:

```
X-CurveVision-Event: job.accepted
X-CurveVision-Delivery: <uuid>
X-CurveVision-Signature: sha256=<hex>
```

The signature is `HMAC-SHA256(secret, raw_body)`. **Verify it with a constant-time
comparison** before trusting the payload.

### Retries

A delivery that fails is retried up to **5 attempts in total**, with exponential backoff
(10s, 20s, 40s, 80s) capped at 10 minutes and spread by ±20% jitter — a receiver that falls
over drops every delivery in flight at once, and retrying them on the same schedule would
reproduce the spike exactly when it can least take it.

**What is retried is decided by who is wrong.** A 5xx, a timeout or a refused connection says
the receiver is struggling and the same request may work later. A 4xx says the receiver
understood the request and rejected it, and repeating it cannot change the answer — so a 401,
404 or 410 is **not** retried. Two exceptions, because both mean *later* rather than *never*:
**408 Request Timeout** and **429 Too Many Requests**.

**A retry is the same event again.** It resends the identical body and the same
`X-CurveVision-Delivery` id, so a receiver that already processed an attempt and then failed
to reply can deduplicate on that id rather than doing the work twice. Treat delivery as
**at-least-once**.

`delivered_at` on a delivery is set only by a 2xx. A delivery with attempts recorded, no
`delivered_at`, and a 4xx or an exhausted attempt count is one nobody will try again.

## Rate limiting

Default 600 requests per minute (`CURVEVISION_RATE_LIMIT_PER_MINUTE`; `0` turns it off),
over a sliding one-minute window, keyed by API token or client address. Exceeding it returns
`429` as `application/problem+json`, with `Retry-After` in seconds.

Where the counts live is `CURVEVISION_RATE_LIMIT_BACKEND`:

* `memory` (the default) — in the server process. Right for one instance and for the
  desktop application; a server run as *N* processes or replicas allows *N* times the limit.
* `redis` — in `CURVEVISION_REDIS_URL`, shared by every instance, counted on Redis' own
  clock so instances whose clocks disagree still share one window. Needs the `redis` extra
  (`pip install "curvevision-server[redis]"`). If Redis cannot be reached, requests are
  **allowed** and the outage is logged once — a limiter failing must not take the API with
  it.

## Clients

* **Python SDK and CLI** — [`sdk/python`](../sdk/python/README.md)
* **TypeScript** — the web app's client in `web/src/api/` is a working reference; a
  published package is *Planned*.
* **Anything else** — generate from `/api/v1/openapi.json`.
