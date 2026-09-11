# ADR 0007 — What we reuse from CVAT, and what we do not

**Status:** Accepted · **Date:** 2026-09

## Context

CVAT is the most mature open-source annotation platform there is. CurveVision was asked to
use it as an engineering reference where legally permitted and practical, and to avoid
re-deriving what already works.

Two questions have to be answered before any code moves: *may we?* and *should we?*

## May we? — the license audit

Performed against `cvat-ai/cvat` at commit `1d0c395` (2026-09-11):

| Finding | Result |
| --- | --- |
| Top-level `LICENSE` | MIT — © 2018–2022 Intel Corporation, © 2022–2025 CVAT.ai Corporation |
| Other `LICENSE` files anywhere in the tree | None |
| Distinct `SPDX-License-Identifier` values across 1,473 source-file headers | `MIT` — every one, no exceptions |
| Caveats CVAT documents itself | `/serverless` may reference third-party model assets under separate, sometimes non-commercial, licenses; FFmpeg is LGPL/GPL, reached via PyAV |

**Yes**, with conditions. An adapted file keeps its CVAT copyright line and MIT notice, adds
ours for the modifications, and is listed in
[THIRD_PARTY_NOTICES.md](../THIRD_PARTY_NOTICES.md). We adapt nothing from `/serverless`
and ship no FFmpeg binaries. This audit is re-run before each new adaptation, because a
license can change between releases.

## Should we? — the test that decides

Reuse pays when code encodes **hard-won domain knowledge**. It costs when code is mostly
**framework glue**.

CVAT is Django + DRF + SVG.js. CurveVision is FastAPI + SQLAlchemy 2.0 + Canvas2D. Moving
glue across that gap is not reuse — it is a rewrite wearing reuse's clothes, and it leaves
Django idioms to maintain inside an async codebase forever. Moving domain knowledge across
that gap is nearly free, because the knowledge lives in the algorithm, not the framework.

## Decision

| CVAT component | Nature | Decision |
| --- | --- | --- |
| `cvat/apps/engine/media_extractors.py` — 1,649 lines; PyAV/Pillow/NumPy, 3 CVAT imports and one DRF exception. Frame-accurate seeking, keyframe indexing, EXIF orientation, chunk writers. | Domain knowledge | **Adapt** |
| Format edge cases across 20+ converters in `dataset_manager/formats/` | Domain knowledge in a Datumaro-shaped wrapper | **Reference; port quirks selectively** |
| Datumaro | A library | **Depend on it eventually** (bridge stays *Planned*) |
| Interpolation and track semantics | Domain knowledge | **Reference for behaviour parity**; reuse test vectors |
| `cvat-canvas` (SVG.js + `svg.draw.js`/`svg.resize.js`) | Architecture | **Do not adapt** |
| Django models, DRF serializers, viewsets, permissions | Framework glue | **Do not adapt** |
| `cvat-core`, `cvat-ui` | Glue and product identity | **Do not adapt** |

**The reuse budget goes almost entirely into media handling**, because that is where CVAT's
advantage is both real and transferable. Our `media/` package is 182 lines of probing, and
our largest In Progress gap is precisely "chunked frame extraction". Video decoding is years
of accumulated edge cases — variable frame rates, broken keyframe indices, rotation metadata,
codecs that lie about their duration. Re-deriving it would be the least defensible code we
could write.

Two "do not adapt" entries deserve their reasoning stated, because they look like refusals
to reuse:

* **`cvat-canvas`.** [ADR 0003](./0003-canvas2d-with-spatial-index.md) chose Canvas2D plus
  an R-tree over an SVG DOM on a *measured* ~500x picking advantage at 100k shapes. Adopting
  an SVG scene graph now would undo a benchmarked decision to gain code we would then have
  to slow down.
* **Datumaro as a hard dependency.** [ADR 0004](./0004-streaming-format-registry.md) chose a
  streaming registry because Datumaro is memory-resident and a server exporting 500k images
  cannot be. Supporting evidence found during this audit: **CVAT does not depend on upstream
  Datumaro either** — it pins its own fork (`cvat-ai/datumaro`) to a commit hash. Upstream
  did not fit their needs any more than ours.

## Consequences

* Video annotation gets finished by adapting proven code rather than writing new decoders,
  and it gets finished sooner.
* CurveVision carries a real attribution obligation, tracked in THIRD_PARTY_NOTICES.md and
  enforced by `scripts/check_notices.py` in CI.
* Adapted files diverge from upstream over time. Accepted: the alternative is a fork of a
  Django application inside a FastAPI one.
* Where behaviour should match CVAT — interpolation especially — we test against shared
  vectors rather than shared code, so a project imported from CVAT annotates identically.
* This ADR is the answer to "why didn't you just reuse CVAT?" and equally to "why did you
  copy that from CVAT?". Both questions have the same test behind them.
