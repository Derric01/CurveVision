# ADR 0005 — An HTTP inference contract, not a bundled serving platform

**Status:** Accepted · **Date:** 2026-02

## Context

AI-assisted annotation needs to run models over frames and turn predictions into editable
annotations. The established answer is Nuclio, a serverless platform, plus a catalogue of
pre-built model wrappers.

## Options

1. **Bundle a serving platform** (Nuclio, Triton, TorchServe) and ship model wrappers.
2. **Import inference libraries directly** — torch or ONNX Runtime inside the worker.
3. **Define an HTTP contract** and let the operator run whatever serves it.

## Decision

**Option 3.** A `ModelProvider` protocol with an `http` implementation, and a documented
request/response schema an operator can satisfy with a 30-line FastAPI script.

## Consequences

**We accept:**

* No out-of-the-box models. A new user cannot click "auto-annotate" without standing
  something up first — a genuine onboarding cost.
* We do not control inference performance or batching; that is the operator's serving stack.
* Network latency per inference call, which matters for interactive segmentation. The
  contract accepts batches, and a click-to-segment path will need a persistent connection.

**We gain:**

* **Licensing stays clean.** Model weights frequently carry more restrictive terms than the
  code around them — several popular detection and segmentation checkpoints are
  non-commercial. Keeping weights entirely on the operator's side means the CurveVision core
  ships unencumbered, and nobody inherits a license they did not choose.
* **Every serving stack works.** Triton, TorchServe, BentoML, Ray Serve, KServe, a script,
  or a hosted vendor — all satisfy an HTTP contract, with zero CurveVision-side plugins.
* **The API image stays small.** No torch, no CUDA, no serving platform.
* **The whole inference path is tested in CI** against an in-process fake provider, with no
  network and no GPU.

**Mitigating the onboarding cost:** documented reference implementations for common models
are planned, as separate repositories rather than bundled weights.

**What this is not:** a claim that serving platforms are bad. It is a claim that a tool
should not force its choice of one on every operator, and that a schema is a better
integration point than a platform.
