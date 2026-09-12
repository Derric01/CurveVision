# ADR 0007 — Reusing third-party code, and what that obliges us to do

**Status:** Accepted · **Date:** 2026-09 · **Revised:** 2026-09 (generalised from a
project-specific policy to the rule that governs all source reuse)

## Context

CurveVision is asked to prefer mature open source over bespoke code, and to avoid
re-deriving what already works. Some of what we want is a package we can depend on. Some of
it is *source* — a few hundred lines inside a much larger application, which we want in our
own idiom rather than as a dependency.

Copying source is a different act from adding a dependency, and it has to be answered
deliberately, twice: *may we?* and *should we?*

## May we? — the licence audit

No source is adapted until an audit has been performed **against the upstream repository at
a named commit**, not from memory or from what a README says. The audit records:

| Question | Why it matters |
| --- | --- |
| The top-level `LICENSE`, verbatim, with its copyright holders | This is what we must reproduce |
| Any other `LICENSE` file anywhere in the tree | Sub-directories routinely differ |
| Every distinct `SPDX-License-Identifier` across the source headers | A single GPL file in an MIT project changes the answer |
| Caveats the project documents about itself | Model weights, bundled binaries and codecs frequently carry separate terms |

A permissive licence is permission with conditions, not permission without them. **Under
MIT — the case we have — the single condition is that the copyright notice and the licence
text travel with the code.** That obligation is the price of the reuse, and it is not
negotiable by us: it is what makes the copy lawful. Concretely, every adapted file:

1. keeps the upstream copyright line and SPDX identifier at the top, adds ours for the
   modifications, and says in a comment what was taken and what was changed;
2. is listed in [THIRD_PARTY_NOTICES.md](../THIRD_PARTY_NOTICES.md#adapted-source) by file,
   by upstream path, and by the commit it was taken from;
3. is covered by the full upstream licence text in [`licenses/`](../../licenses/).

The audit is re-run before **each** new adaptation, because a licence can change between
releases.

## Should we? — the test that decides

Reuse pays when code encodes **hard-won domain knowledge**. It costs when code is mostly
**framework glue**.

Glue does not survive the trip. Moving a Django/DRF idiom into an async FastAPI codebase is
not reuse — it is a rewrite wearing reuse's clothes, and it leaves foreign idioms to
maintain forever. Domain knowledge moves almost free, because the knowledge lives in the
algorithm rather than the framework around it.

| Nature of the code | Decision |
| --- | --- |
| Domain knowledge: algorithms, edge cases, formats, decoders | **Adapt** — with the obligations above |
| Domain knowledge wrapped in another library's shape | **Reference**; port the quirks, not the structure |
| A genuinely separable library | **Depend on it** rather than copying |
| Behaviour we must match exactly | **Share test vectors, not code** |
| Framework glue: ORM models, serialisers, viewsets, permissions | **Do not adapt** |
| Architecture we have already decided against on evidence | **Do not adapt** |

That last row matters. Reuse is not automatically the right answer: where an ADR chose a
design on *measured* grounds — [ADR 0003](./0003-canvas2d-with-spatial-index.md) chose
Canvas2D plus an R-tree over an SVG DOM on a ~500× picking advantage at 100k shapes, and
[ADR 0004](./0004-streaming-format-registry.md) chose a streaming registry because a
memory-resident one cannot export 500k images — adopting the alternative to gain code would
undo a benchmarked decision and cost the performance it bought.

## Decision

**The reuse budget goes where the upstream advantage is both real and transferable**, and
in practice that has meant media handling. Video decoding is years of accumulated edge
cases — variable frame rates, broken keyframe indices, rotation metadata, containers that
lie about their duration. Re-deriving it would be the least defensible code we could write.

`server/curvevision/media/video.py` is the one adapted file today. It carries its upstream
copyright header, is listed in THIRD_PARTY_NOTICES, and the licence text is in `licenses/`.

## Consequences

* Video annotation was finished by adapting proven code rather than writing new decoders,
  and it was finished sooner.
* CurveVision carries a real attribution obligation, tracked in
  [THIRD_PARTY_NOTICES.md](../THIRD_PARTY_NOTICES.md) and enforced in CI by
  `scripts/check_notices.py`.
* Adapted files diverge from upstream over time. Accepted: the alternative is carrying a
  foreign framework inside ours.
* Where behaviour must match another tool — interpolation especially — we test against
  shared vectors rather than shared code, so a project imported from elsewhere annotates
  identically without us inheriting its architecture.
* **Removing an attribution while keeping the code it covers is not an option this ADR
  leaves open.** If a file's attribution is ever unwanted, the answer is to re-derive that
  file independently and delete the adapted one in the same change — not to quietly drop
  the notice.
