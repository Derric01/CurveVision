# Architecture decision records

Short records of decisions that would otherwise have to be reverse-engineered from the
code, written when the reasoning is still fresh. Each states the context, the options
weighed, the decision, and the consequences we accept.

| ADR | Decision |
| --- | --- |
| [0001](./0001-modular-monolith.md) | A modular monolith, not microservices |
| [0002](./0002-in-process-policy-engine.md) | An in-process policy engine, not Open Policy Agent |
| [0003](./0003-canvas2d-with-spatial-index.md) | Canvas2D with an R-tree, not an SVG DOM |
| [0004](./0004-streaming-format-registry.md) | A streaming format registry, not Datumaro |
| [0005](./0005-http-inference-contract.md) | An HTTP inference contract, not a bundled serving platform |

## Writing one

Add an ADR when a decision is hard to reverse, non-obvious, or one a future contributor
would otherwise relitigate. Copy the structure of an existing one. An ADR is never edited
to change its decision — supersede it with a new one and link back.
