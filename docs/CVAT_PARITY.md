# CurveVision beside CVAT

CVAT (`cvat-ai/cvat`, MIT) is the most complete open-source annotation tool, and the one
this project reuses ideas from first ([ADR 0007](./adr/0007-reusing-third-party-code.md)).
This page sets its core features beside CurveVision's, row by row, so someone choosing
between them, or someone choosing what to build next, sees what is done, what is partial
and what is missing.

It was written in iteration 102 against a CVAT source checkout, reading its annotation
UI (`cvat-ui/src/components/annotation-page`) and its server (`cvat/apps`), and against
this repository's own code and tests, not its prose. A row says **Done** only when it
works and something tests it.

**Status:** **Done** · **Partial** (what is missing is said) · **Missing** (not built yet)
· **Not planned** (a decision, with the reason, in [ROADMAP](./ROADMAP.md)).

## Drawing

| CVAT | CurveVision |
| --- | --- |
| Rectangles, polygons, polylines, points, ellipses | **Done** |
| Rotated rectangles | **Done** |
| Cuboids (2D projection) | **Done** |
| Skeletons | **Done** — placed joint by joint, tracked too |
| Masks with a brush and eraser | **Done** — paint, grow, shrink, erase; exported pixel for pixel |
| Tags (a label for the whole frame) | **Done** — iteration 102; the server stored them from the start, the editor could not show or make one until then (`scripts/verify_frame_tags.py`) |
| Intelligent scissors | **Done** — live-wire, [ADR 0009](./adr/0009-intelligent-scissors.md) |
| AI interactors (SAM and the like: positive and negative clicks) | **Done** — needs an interactor model server; none is bundled |
| AI detectors run over a job, as suggestions | **Done** — accept or reject one at a time or all, least confident first |
| AI trackers (a model follows an object across frames) | **Missing** — tracks interpolate between keyframes, which CVAT also does; no tracker model kind yet |
| OpenCV tools in the browser (histogram equalisation, tracker) | **Not planned** as such — scissors is implemented directly to avoid the ~10 MB OpenCV build |
| 3D point clouds (LiDAR cuboids) | **Not planned** — see ROADMAP |

## Editing objects

| CVAT | CurveVision |
| --- | --- |
| Select, move, resize, edit vertices, marquee selection | **Done** |
| Undo and redo | **Done** — drags coalesce into one step |
| Copy and paste | **Missing** |
| Attributes per label (select, radio, checkbox, text, number), mutable on tracks | **Done** |
| Change an object's label | **Partial** — the engine can relabel a selection; nothing in the editor offers it yet |
| Occluded flag | **Partial** — stored, imported and exported; not settable in the editor |
| Z-order (bring forward, send back) | **Partial** — stored and exported; not settable in the editor |
| Hide or lock one object | **Partial** — per label only, not per object |
| Group objects | **Partial** — the group id is stored and exported; no grouping in the editor |
| Merge, split tracks; propagate a shape over frames | **Missing** |
| Attribute annotation mode (walk every object, attribute by attribute, from the keyboard) | **Missing** |
| Filter objects by label or attribute | **Missing** |

## Frames and video

| CVAT | CurveVision |
| --- | --- |
| Image sets and video tasks | **Done** — video frames decoded and served in chunks |
| Step frame by frame, jump to a frame | **Done** |
| Tracks with keyframes, interpolation and "outside" | **Done** — with a timeline to add, remove and drag keyframes |
| Play a video at a chosen speed | **Missing** — frames are stepped, not played |
| Brightness, contrast, saturation of the frame on screen | **Missing** |
| Show or hide label names, fill opacity | **Done** |

## Work and review

| CVAT | CurveVision |
| --- | --- |
| Organizations, roles, invitations | **Done** — six roles |
| Projects, tasks, jobs; a task split into jobs; overlap | **Done** — overlapping jobs merged on export |
| Assign jobs, job stages and states | **Done** — `new → in_progress → submitted → accepted/rejected` |
| Review mode with issues pinned to the frame, comments, resolve and reopen | **Done** |
| Ground truth and quality reports | **Done** — precision, recall, F1 per label, conflicts that seek to their frame |
| Honeypots and consensus annotation | **Not planned** for 1.0 — see ROADMAP |
| Annotation history | **Done** |
| Analytics dashboards | **Missing** — ROADMAP lists dataset analytics as future work |

## Data in and out

| CVAT | CurveVision |
| --- | --- |
| Upload from the browser, resumable | **Done** |
| Cloud storage | **Partial** — any S3-compatible store; Azure Blob and Google Cloud Storage **not planned** |
| Annotate a local folder without copying | **Done** — CVAT has no equivalent of the desktop app's local folders |
| Import and export formats | **Done** — CVAT XML, COCO, YOLO (detect, segment, OBB, pose, classify), Pascal VOC, KITTI, LabelMe, Cityscapes, Open Images, TFRecord, MOTChallenge, segmentation masks, CurveVision JSON |
| Dataset versions | **Done** — immutable releases with a content hash, downloadable in any format |
| REST API, Python SDK, CLI | **Done** — and a TypeScript client |
| Webhooks | **Done** — signed, retried, managed from the Workspace page |
| Single sign-on | **Done** — OpenID Connect and SAML |
| Kubernetes (Helm) | **Done** |
| A desktop application | **Done** — CVAT ships none |

## The gaps, in the order they would be built

Every row marked *Missing* or *Partial* above, ranked by how often an annotator meets it:

1. **Object properties in the editor:** relabel, occluded, z-order, and hide or lock one object.
   The data model and the formats already carry them, so this is editor work.
2. **Video playback and image adjustments:** play, pause and speed, plus brightness and
   contrast on the frame.
3. **Copy and paste**, then **propagate** a shape over the next frames.
4. **Filter objects** by label and attribute.
5. **Attribute annotation mode.**
6. **Merge and split tracks; group objects.**
7. **Tracker models; analytics.**
