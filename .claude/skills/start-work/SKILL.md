---
name: start-work
description: Autonomously continue building CurveVision. Orients against the real state of the codebase (handoff.md, the plan, the code, the test suite), picks the highest-value next task, implements and verifies it, updates handoff.md, and continues. Use when the user says "start work", "continue", "keep building", "pick up where you left off", or otherwise asks you to drive the project forward without naming a specific task.
---

# Start work

Drive CurveVision forward autonomously: understand the real state, pick the highest-value
next task, implement it to production quality, verify it, document it truthfully, continue.

## The protocol lives in AGENTS.md

**Read [`AGENTS.md`](../../../AGENTS.md) now and follow it.** It is the complete contract
and it is agent-agnostic, so there is exactly one copy to keep true. This file is only the
entry point.

Do not skip Phase 0. Orienting takes a few minutes; rebuilding something that already exists
takes hours.

## What "start work" means in practice

1. **Orient** — read `handoff.md`; read `docs/IMPLEMENTATION_PLAN.md`, `docs/ARCHITECTURE.md`
   (§1.1, §2, §8) and `docs/adr/`; look at the actual code; run `./scripts/check.sh` to learn
   whether the tree was already broken before you touched it.
2. **Reconcile** — compare what the docs claim against what the code does. A feature marked
   **Done** that is missing or broken is the highest-value thing you can find.
3. **Choose one task** — broken first, then false claims, then nearly-finished work, then
   what unblocks the most, then highest user value. Not the next unchecked box.
4. **Loop** — Review → Plan → Implement → Test → Debug → Self-review → Document.
   Then **stop and report**: one iteration is one session by default. "Continue" in
   `AGENTS.md` means pick up the next task *when asked to*, not run until the context runs
   out. More, shorter sessions beat one long one — they cost the user less and give them a
   say in between.
5. **Update `handoff.md` every iteration**, truthfully, including the iterations that mostly
   failed.

## Non-negotiables

- Reuse before you build — inside this repository *and* outside it. Finish partial work in
  place rather than starting a parallel implementation, never leave two implementations of
  the same thing, and look at how CVAT (`cvat-ai/cvat`, MIT) solved a problem before writing
  your own version of it. Adapting source follows ADR 0007; reading it to get a convention
  right costs nothing and obliges nothing.
- Smallest effective change. No rewrites without a stated technical reason, and a
  significant one gets an ADR.
- `./scripts/check.sh` must pass before you call a task done. Never weaken a test to make it
  pass.
- Never mark something **Done** unless it works and is tested. Never invent a benchmark or a
  metric — measure it or leave it out.
- Respect the invariants in `AGENTS.md` § *Project-specific invariants*, especially: one
  codebase two shapes, `services/` never knows which shape it is in, and local-path routes
  stay gated behind `settings.local_mode`.

## Stop when the iteration is done, or when AGENTS.md says to

The ordinary stop is **a finished iteration**: committed, pushed, written up. Report, and
let the user decide what comes next.

Stop mid-iteration for a genuinely ambiguous product decision, a documented invariant in the
way, a destructive or outward-facing action, an external blocker, or two consecutive
iterations with no real progress.

What "Start work." *does* mean: do not stall asking which task to pick, and do not present a
plan instead of a change. Choose, build it properly, finish it. What it does not mean is
"keep going until the context is exhausted".

Leave the tree green and `handoff.md` accurate before you stop for any reason.
