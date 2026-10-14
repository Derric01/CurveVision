# ADR 0011 — The Datumaro bridge, as ADR 0004 planned it

**Status:** Accepted · **Date:** 2026-10 · **Carries out:** [ADR 0004](./0004-streaming-format-registry.md)

## Context

ADR 0004 built a streaming format registry instead of building on Datumaro, because
Datumaro holds a whole dataset in memory, and it planned "a Datumaro bridge … as an optional
extra" for the long tail. By iteration 89 the registry carries fifteen formats, each held in
CI to a reader written by other people. What is left are single-dataset conventions —
ImageNet's folders, CIFAR, MNIST, LFW, VGGFace2, WIDER FACE, DOTA's rotated boxes, CamVid,
Pascal VOC's segmentation layouts, COCO panoptic, LabelMe 3.0 XML, Datumaro's own format —
that matter to whoever needs one and to nobody else, and that Datumaro already reads and
writes.

## Decision

`curvevision/formats/datumaro_bridge.py` registers one format per Datumaro format,
`datumaro-<name>`, **only when Datumaro is installed** (`pip install 'curvevision[datumaro]'`),
found with `importlib.util.find_spec` so that a server without it neither imports nor offers
it. Export turns a `DatasetView` into Datumaro items and has Datumaro write them; import has
Datumaro read the archive and turns its items into an `ImportResult`.

Three things keep the bridge inside the rules this project already holds its formats to:

* **Capabilities are measured, not declared.** Datumaro's formats are too many and too
  uneven to describe from their documentation. `scripts/datumaro_bridge_probe.py`
  round-trips one shape of every type, a tag and an attribute through each format and the
  bridge, and records what came back — with its label, its box within a pixel and a half —
  and as what, in `datumaro_formats.json`. A format that carries nothing is not offered. CI
  re-measures (`--check`) whenever a format changes and weekly, so a Datumaro release that
  changes what a format keeps changes what the export API warns about.
* **Memory holds the annotations, not the images.** Each image is written to a temporary
  file as it streams past and read back by Datumaro when it writes it; each mask is drawn
  only when Datumaro asks for it. The annotations of a very large dataset are still held at
  once — that is Datumaro's model, and the reason the formats people use most are this
  project's own.
* **Optional, and not shipped by default.** Datumaro brings a scientific stack. It is not in
  the desktop application, and the server image adds it only when built with
  `--build-arg SERVER_EXTRAS=...,datumaro`.

## Consequences

* Fourteen more formats for a server that installs the extra, at the cost of one module and
  a measured table, and a CI job that keeps the table true.
* A bridged format is only as good as Datumaro's: its ImageNet reader names items
  `label:name`, which the bridge matches by the image's file name; its Segment Anything and
  ICDAR readers give annotations no class, so neither is offered.
* Formats this project writes itself are not bridged, so there is one implementation of
  each.
