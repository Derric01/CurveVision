# ADR 0004 — A streaming format registry, not Datumaro

**Status:** Accepted · **Date:** 2026-02

## Context

CurveVision needs to import and export the standard computer-vision dataset formats, and to
accept new ones without rewriting the dataset system each time.

[Datumaro](https://github.com/openvinotoolkit/datumaro) (MIT, Intel/OpenVINO) supports 20+
formats and is actively maintained; it is what most of this space uses. Adopting it would
be the obvious
"don't reinvent the ecosystem" answer.

## Options

1. **Use Datumaro directly.** Enormous coverage immediately.
2. **Wrap Datumaro behind our own interface.** Coverage plus a seam.
3. **Build a streaming registry** with the formats that cover most real use, and bridge to
   Datumaro later for the long tail.

## Decision

**Option 3**, with a Datumaro bridge planned as an optional extra.

The deciding factor is Datumaro's core model: load a dataset into memory as `DatasetItem`s,
then convert. That is exactly right for a CLI conversion tool and exactly wrong for a
server exporting a 500,000-image dataset — the resident set would be tens of gigabytes.
Its transitive dependencies (NumPy, OpenCV) are also heavy for an API image that otherwise
needs neither.

Our `DatasetView` exposes `frames` as an **iterator**, so an export streams. Shipped
formats: COCO, YOLO, Pascal VOC, and a lossless native format.

## Consequences

**We accept:**

* Four formats instead of twenty. That is a real gap, stated plainly in the README and the
  architecture comparison.
* We own the correctness of each implementation. Mitigated by every format having a
  round-trip test — export, re-import, compare — against the lossless native format.
* Format edge cases Datumaro has already met will surprise us.

**We gain:**

* Exports bounded by frame size, not dataset size.
* A ~30-line plugin interface. Adding a format is one class and one `register()` call;
  third-party formats register through an entry point without touching this repository.
* Honest capability declarations. Each format states what it cannot represent, and an
  export that would drop polylines says so *before* running — in a response header and
  inside the archive. Silently dropping annotations is how people discover a broken dataset
  during training.

**The upstream path, which we prefer to a permanent parallel implementation:** contribute a
streaming/iterator export API to Datumaro, then bridge to it for the long tail. Maintaining
our own twenty-format library would be the wrong outcome.
