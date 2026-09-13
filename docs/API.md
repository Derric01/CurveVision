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

### API tokens — for scripts, CI and the SDK

```http
POST /api/v1/auth/tokens
{ "name": "nightly-export" }

→ { "id": "…", "token": "cv_a1b2c3d4_xY9…" }
```

**The token is shown once.** Only a hash is stored. Tokens do not expire unless you set
`expires_at`, and each can be revoked individually — which is why they, not passwords, are
the right credential for anything unattended.

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
| Media | `/tasks/{id}/media`, `/tasks/{id}/frames/{frame}`, `/tasks/{id}/frames/{frame}/data` |
| Jobs | `/jobs`, `/jobs/{id}`, `/jobs/{id}/review` |
| Annotations | `/jobs/{id}/annotations`, `/jobs/{id}/frames/{frame}/annotations`, `/jobs/{id}/history` |
| Review | `/jobs/{id}/issues`, `/jobs/{id}/issues/{id}/comments` |
| Quality | `/tasks/{id}/ground-truth`, `/jobs/{id}/quality`, `/tasks/{id}/quality` |
| Datasets | `/projects/{id}/export`, `/tasks/{id}/import`, `/projects/{id}/versions` |
| AI | `/models`, `/jobs/{id}/inference`, `/jobs/{id}/suggestions` |
| Integrations | `/webhooks` |
| System | `/health`, `/formats`, `/background-tasks` |

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
  "details": {
    "compared_frames": 50, "matched": 18, "missing": 6, "extra": 2, "mean_iou": 0.87,
    "per_label": {
      "3f2a…": { "matched": 12, "missing": 1, "extra": 0,
                 "precision": 1.0, "recall": 0.923, "f1": 0.96, "mean_iou": 0.91 }
    },
    "conflicts": [
      { "kind": "missing", "frame": 12, "ground_truth_shape_id": "…", "iou": null },
      { "kind": "wrong_label", "frame": 30, "shape_id": "…",
        "label_id": "…", "expected_label_id": "…", "iou": 0.88 },
      { "kind": "poor_overlap", "frame": 31, "shape_id": "…", "iou": 0.41 }
    ]
  }
}
```

`per_label` is keyed by label id, as everything else in this API is; resolve names from
`GET /projects/{id}/labels`. `mean_iou` averages over matched pairs only — it answers "how
tight were the boxes you got right", not "how right were you"; that is what `f1` is for.

Five things worth knowing about what the number means:

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

Computing a report is a `review` action; a ground-truth job cannot be scored against
itself. The comparison runs inline rather than as a background job.

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

### Shipped formats

| Format | Import | Export | Carries | Use it for |
| --- | :-: | :-: | --- | --- |
| **COCO** | ✓ | ✓ | boxes, polygons, keypoints | the default for detection and segmentation |
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

CurveVision never bundles model weights and never imports a vendor SDK. You register an
endpoint; the platform calls it.

### Registering a model

```http
POST /api/v1/models?organization_id={id}
{
  "slug": "yolo-v8n",
  "name": "YOLOv8 nano",
  "kind": "detector",
  "provider": "http",
  "config": { "endpoint": "http://my-inference:9000/infer" },
  "output_labels": ["person", "car"]
}
```

### The inference contract

Any service implementing this works — Triton, TorchServe, BentoML, Ray Serve, a hosted
vendor, or a script. **Request:**

```json
{
  "model": "yolo-v8n",
  "confidence_threshold": 0.5,
  "frames": [
    { "frame": 0, "width": 1920, "height": 1080,
      "content_type": "image/jpeg", "image": "<base64>" }
  ]
}
```

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
`[0,1]` coordinates instead, and CurveVision rescales.

Optionally implement `GET <endpoint>/models` returning `{"models": [...]}` for discovery;
without it, the labels configured on the registration are used.

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

## Rate limiting

Default 600 requests per minute, keyed by API token or client address. Exceeding it returns
`429` with `Retry-After`. Note the limit is per-process — see
[SECURITY.md](./SECURITY.md#known-limitations).

## Clients

* **Python SDK and CLI** — [`sdk/python`](../sdk/python/README.md)
* **TypeScript** — the web app's client in `web/src/api/` is a working reference; a
  published package is *Planned*.
* **Anything else** — generate from `/api/v1/openapi.json`.
