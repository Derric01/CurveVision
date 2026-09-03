# ADR 0002 — An in-process policy engine, not Open Policy Agent

**Status:** Accepted · **Date:** 2026-01

## Context

CurveVision's authorization is not trivial. Six roles, a resource hierarchy
(organization → project → task → job), and assignment-scoped rules: an annotator may write
annotations on a job assigned to them, or on an unassigned job when the project allows open
assignment, but never on someone else's — and nobody reviews their own work.

CVAT solves this with Open Policy Agent: a separate service, policies in Rego, consulted
per authorization check.

## Options

1. **Open Policy Agent.** Battle-tested, declarative, decoupled. Adds a service and a
   language.
2. **pycasbin.** In-process, mature, config-driven. Its matcher model fits RBAC well.
3. **Build a small policy module.** A declarative table plus a pure function.

## Decision

**Option 3**, in `server/curvevision/policy/engine.py`: a `ROLE_FLOOR` table mapping
`(resource, action) → minimum role`, plus a short list of assignment-scoped rules. `can()`
is a pure function of a `Principal` and a `ResourceContext` the router has already loaded.

Casbin was the closest call. It handles the role floor well, but the assignment-scoped
rules would need custom matchers written in its expression language — at which point the
"configuration" is code again, in a language with worse tooling than Python.

## Consequences

**We accept:**

* Policy changes require a code change and a deploy, not a config push.
* We own the correctness of an authorization engine, which is a serious thing to own.
  Mitigated by it being a pure function: the whole matrix is unit-tested without a
  database, an HTTP request or a second process.

**We gain:**

* No second process for a decision that is a pure function of already-loaded rows, and no
  network hop per authorization check.
* A contributor can read every permission rule in one sitting. With OPA they would need to
  learn Rego first — a real barrier for a volunteer project.
* Denials are debuggable in a Python stack trace.

**What we kept from the OPA approach:** the actually important idea — centralised,
declarative, table-driven policy, never ad-hoc role checks in route handlers. That property
is what makes authorization auditable, and it does not require a separate service.

**A refinement this forced:** constraints that hold for *everyone* regardless of role — a
locked job, a released dataset version — are state, not permission. They live in services
and return 409 with a message saying what is actually wrong, rather than a 403 claiming a
permission problem. Encoding them in the policy gave the same condition two different
status codes depending on who asked.
