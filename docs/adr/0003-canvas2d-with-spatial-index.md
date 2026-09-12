# ADR 0003 — Canvas2D with an R-tree, not an SVG DOM

**Status:** Accepted · **Date:** 2026-02

## Context

The annotation canvas is the most performance-sensitive surface in the product. A single
job can hold 100,000 objects, and annotators pan and zoom constantly through sessions
lasting hours.

The established approach renders annotations as SVG DOM nodes. That buys browser-native hit
testing, CSS styling and accessibility — a completely reasonable choice whose ceiling is the
DOM: each shape is one or more elements, and browsers degrade noticeably in the low
thousands, particularly during pan/zoom when transforms invalidate large parts of a layer.

## Options

1. **SVG DOM.** Simplest, best accessibility, hits a wall at scale.
2. **A scene-graph library** — Konva, fabric.js, PixiJS. Mature, but each carries an object
   model and hit-testing scheme optimised for general editing, which we would fight for
   annotation-specific needs.
3. **Canvas2D with our own scene and spatial index.**
4. **WebGL from the start.** Fastest ceiling, most complexity, hardest to debug.

## Decision

**Option 3.** A layered Canvas2D renderer (media / shapes / overlay) over a scene indexed
by an R-tree (`rbush`, MIT).

The two properties that follow:

* **Viewport culling.** `Scene.visible()` queries the R-tree, so a 100,000-object job paints
  the ~50 objects on screen. Frame time is decoupled from dataset size.
* **O(log n) picking.** The R-tree narrows candidates; a precise geometric test picks the
  winner, breaking ties by z-order then *smallest area* — so a small object inside a large
  one is selectable, which is the single most irritating bug in this class of tool.

`rbush` rather than `flatbush` because shapes are edited constantly and flatbush is static.
Wrapped behind a `SpatialIndex` interface anyway, so a read-only review mode could use the
faster static structure later.

## Consequences

**We accept:**

* No browser-native accessibility on the canvas surface. Compensated by a fully
  keyboard-navigable object list beside it, which is where screen-reader users work anyway.
* We implement our own hit testing, text rendering and handle drawing. Roughly 1,500 lines,
  covered by 88 tests.
* Canvas2D has a ceiling too, well above SVG's but not infinite.

**We gain (measured, `npm run bench`):**

* Picking from 100,000 shapes: ~1 µs. A linear scan of 10,000: ~550 µs. A ~500x gap, and
  the gap widens with size.
* Viewport queries stay within an order of magnitude across 1k / 10k / 100k, which is the
  culling claim stated as a number.
* Hover and drag repaint a transparent overlay, never the shape set.

**Deliberately kept open:** the `Renderer` interface admits a WebGL implementation without
touching the scene, tools or commands. Canvas2D is fast enough that this is not urgent, and
building it first would have been speculative.
