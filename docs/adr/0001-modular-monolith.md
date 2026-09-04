# ADR 0001 — A modular monolith, not microservices

**Status:** Accepted · **Date:** 2026-01

## Context

CurveVision has several clearly separable concerns: media processing, dataset conversion,
inference orchestration, annotation storage, and the API itself. They have genuinely
different resource profiles — media work is CPU-bound, annotation writes are database-bound,
inference is network-bound.

The obvious modern reflex is to split them into services. CVAT's production compose file
runs eighteen.

## Options

1. **Microservices from the start.** Each concern its own deployable, communicating over
   HTTP or a queue.
2. **Modular monolith.** One API image, one worker image, with module boundaries enforced
   in code by explicit interfaces.
3. **A monolith with no internal boundaries.** Fastest to write, and the reason so many
   projects cannot later extract anything.

## Decision

**Option 2.** One API application and one worker application. The boundaries are Python
packages with explicit protocols: `Storage`, `DatasetFormat`, `ModelProvider`, `JobQueue`.
Each takes plain data and never imports domain models.

Queue routing gives per-workload isolation *when wanted* without a service per queue:
`dramatiq --queues media` and `--queues export` are two deployments of one image.

## Consequences

**We accept:**

* One deployable cannot be scaled per-concern independently. Mitigated by queue routing,
  which covers the case that actually arises (media work starving exports).
* A memory leak in one module affects the whole process.
* Discipline is required: nothing stops someone importing across a boundary except review
  and the layering table in DEVELOPMENT.md.

**We gain:**

* Six services in the deployment instead of eighteen. For a project whose primary audience
  is self-hosters, that difference decides whether people actually run it.
* A test suite that needs no services at all — a contributor clones the repo and runs
  `pytest`. That is impossible once module boundaries are network boundaries.
* No distributed-transaction problems, no service-discovery layer, no version skew between
  services during a deploy.

**If this needs to change:** the interfaces are already the seams. `Storage` becomes a
media service, `ModelProvider` an inference gateway, without touching the domain. We will
make that split when a boundary demonstrably needs it, not in anticipation.
