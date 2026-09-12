# ADR 0009 — Implementing intelligent scissors rather than loading OpenCV

**Status:** Accepted · **Date:** 2026-09

## Context

Tracing a curved object by clicking vertices is the slowest and least accurate thing in
manual annotation. **Intelligent scissors** fixes it: click once to anchor, and the boundary
from that anchor to the cursor snaps to the strongest edge between them, live. It is the
single most-requested tool in this space and the one people name when they say an annotator
feels good to use.

The reference implementation in the tool CurveVision draws on is a **196-line wrapper around
OpenCV.js**. All of the algorithm lives in OpenCV's C++ `segmentation_IntelligentScissorsMB`,
compiled to WebAssembly; their file is state management — tracking anchors, rebuilding the
cost map, converting coordinates. The asset it wraps is **9,991,739 bytes**.

So "reuse their code" here buys the state machine and none of the substance. The real
decision is where the algorithm comes from.

## Options

### 1. Load OpenCV.js, as upstream does

The algorithm is mature, fast (C++/WASM), and someone else maintains it.

Against it:

- **~10 MB, for one tool.** CurveVision ships as a single desktop executable; the entire web
  bundle today is ~320 kB of JavaScript. This would be thirty times the application.
- **It is a WASM runtime in a page with a deliberately narrow CSP.** The served application
  has the narrowest Content-Security-Policy that lets it run
  ([ADR 0003](./0003-canvas2d-with-spatial-index.md) neighbours this reasoning); WASM needs
  that policy widened.
- **`web/src/canvas/` has no dependencies at all**, by design — it imports nothing, not even
  React. The rule has held through five tools, the spatial index and the renderer.
- Lazy-loading softens the cost but does not remove it: the first use of the tool becomes a
  10 MB download, which on a conference wifi is the tool not working.

### 2. Implement live-wire directly

The algorithm is **Mortensen & Barrett, *Intelligent Scissors for Image Composition*,
SIGGRAPH 1995**. It is published, self-contained, and about 300 lines: per-pixel edge
features (Sobel gradient, Laplacian zero-crossing, edge orientation), a weighted local cost,
and Dijkstra from the anchor.

Against it: it is ours to get right and ours to maintain, and a subtly wrong cost function
produces a tool that *looks* like it works and quietly traces the wrong edge.

## Decision

**Implement it** (`web/src/canvas/scissors.ts`), and test it against images whose correct
answer is known and is visibly not a straight line.

The maintenance risk is answered by the tests rather than by argument: a bulging band and a
circle, where a wire that ignored the image would produce a chord. Replacing the cost
function with a constant fails four of them.

Two departures from a literal reading of the paper, both measured:

- **Dial's algorithm, not a binary heap.** Link costs are bounded small integers, so bucket
  queues give amortised O(1) per pop.
- **The search is lazy.** Building the whole shortest-path tree on each click measured
  **~200 ms** on a 1024×576 grid — a visible hitch — and nearly all of it was wasted, because
  an annotator's next click is tens of pixels along a boundary, not across the image.
  Expanding the frontier only as far as the cursor asks moves that cost to where it is
  actually used:

  | | Whole tree up front | Lazy |
  | --- | --- | --- |
  | `setAnchor` | 200 ms | **2.8 ms** |
  | First move 40 px away | (included above) | **5.9 ms** (1,149 px settled) |
  | First move 320 px away | (included above) | **32 ms** (89,860 px settled) |
  | Worst case, full 1920 px width | 200 ms | **100 ms** (285,236 px settled) |
  | Subsequent moves | 0.03 ms | 0.13 ms |

  Correctness survives because Dijkstra settles nodes in non-decreasing cost order: a pixel
  popped from the queue already has its final distance, so pausing and resuming cannot change
  an answer already given. `resuming the search cannot change an answer it already gave`
  asserts that against cold recomputation.

Also taken from upstream's design, because it is a sound idea independent of OpenCV:
**run the search on a downscaled grid** (they use a fixed 1024×1024). CurveVision scales the
longest side to 1024 and **preserves aspect ratio**, because distorting it distorts gradient
directions, and gradient direction is one of the three cost terms.

## Consequences

- The editor keeps its no-dependency rule and the desktop build does not grow.
- The cost function is ours to tune. That is a liability *and* the reason the tool can be
  improved for annotation specifically rather than accepting a general-purpose default.
- **We own the bugs.** One was found during implementation and is recorded in the tests: an
  exactly-zero Laplacian is not an edge, and treating it as one made flat ground cheap to
  cross, so the wire wandered instead of tracking boundaries. Flat regions are full of exact
  zeros; only a *sign change* is a crossing.
- Mask output is not wired up. The tool produces polygons, which is what the upstream tool's
  scissors produce too, and what every shipped export format can carry.
- No GPU path. At the measured numbers it is not needed; if 4K frames at a larger working
  size ever become the case, the per-pixel feature pass is the part that would move to a
  worker, not the search.

## Attribution

No upstream code is copied — the implementation is from the published algorithm, and the
upstream wrapper delegates to OpenCV rather than implementing it. The *shape of the tool*
(anchor, live preview, backspace to unwind, commit on click) follows theirs, and
`docs/THIRD_PARTY_NOTICES.md` records that under the policy in
[ADR 0007](./0007-reusing-third-party-code.md).
