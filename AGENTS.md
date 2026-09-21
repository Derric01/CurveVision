# Working on CurveVision as a coding agent

**This file is the contract. If you were told "Start work.", this is your instruction set.**

It is deliberately agent-agnostic. It assumes only that you can read files, run shell
commands, and edit code. It does not assume which model or harness you are.

Your job is not to plan, or to demonstrate activity. **Your job is to finish the product**,
one verified increment at a time, leaving the repository in a better and still-working state
after every iteration.

---

## The contract

```
Start work.
   │
   ▼
ORIENT ──▶ ┌──────────────────────────────────────────────────────┐
           │  Review → Plan → Implement → Test → Debug →          │
           │  Self-review → Document (handoff.md) → Continue      │
           └──────────────────────────────────────────────────────┘
                                  ▲                    │
                                  └────────────────────┘
```

You decide what to do next. Do not wait to be told. Stop only for the reasons in
[When to stop](#when-to-stop).

---

## Phase 0 — Orient. Do this before editing anything.

Skipping this is how agents waste hours rebuilding what already exists. It costs a few
minutes.

1. **Read [`handoff.md`](./handoff.md).** It is the previous agent's snapshot: what is done,
   what is in flight, what broke, what was tried and rejected, and what it thought you
   should do next. Treat it as a strong hint, not gospel — verify before trusting.

2. **Read the plan of record**, in this order:
   - [`docs/IMPLEMENTATION_PLAN.md`](./docs/IMPLEMENTATION_PLAN.md) — the phases and their
     honest status markers. The **Purpose and constraints** section at the top states what
     the product is; that part is not negotiable without the maintainer.
   - [`docs/ARCHITECTURE.md`](./docs/ARCHITECTURE.md) — especially §1.1 (two shapes),
     §2 (layering rules) and §8 (build-vs-extend, including the source-reuse policy).
   - [`docs/ROADMAP.md`](./docs/ROADMAP.md) — what is Done / In Progress / Planned.
   - [`docs/adr/`](./docs/adr/) — decisions already made. **Do not relitigate an ADR**
     without a new fact that its author did not have.

3. **Look at the actual code.** Documentation describes intent; the code is the truth.
   Cheap, high-signal moves:
   ```bash
   git log --oneline -20              # what happened recently
   git status                         # uncommitted work someone left behind
   ls server/curvevision web/src      # what modules actually exist
   rg -n "TODO|FIXME|NotImplemented" --glob '!node_modules' --glob '!*.lock'
   ```

4. **Run the verification suite before changing anything.** You need to know whether it was
   already broken when you arrived — otherwise you will spend an hour debugging a failure
   you did not cause.
   ```bash
   ./scripts/check.sh
   ```

5. **Reconcile plan against reality.** For the two or three items the docs call *In
   Progress* or *next*, open the code and check. You are looking for exactly three
   categories:

   | Finding | What you do |
   | --- | --- |
   | Documented as done, and it is | Move on. Do not re-verify the whole thing. |
   | Documented as done, but is missing or broken | **This is the highest-value work there is.** A false "Done" is worse than a known gap. Fix it, or correct the doc. |
   | Documented as Planned/In Progress | Candidate for this iteration. |

6. **Choose one task** — the highest-value next thing, not the next unchecked box. See
   [Choosing what to work on](#choosing-what-to-work-on).

---

## Choosing what to work on

Rank candidates in this order. The first category with anything in it wins.

1. **Something is broken.** Failing tests, a red build, a regression, a crash. Nothing else
   matters until the tree is green.
2. **Something is claimed but false.** The docs say Done and it is not. This destroys trust
   in every other claim in the repository.
3. **Something is nearly finished.** A partial implementation one honest push from working
   beats a new feature at 0%. Finishing beats starting.
4. **The next thing that unblocks the most.** Prefer work that other work depends on.
5. **The highest user value that fits in one iteration.** What would a person using this
   notice and care about?

Deliberately **not** on that list: tidying, renaming, reorganising, and refactors with no
behaviour change. Those are allowed only as a *small part* of a change that has a real
purpose.

**Sizing.** One iteration should be a coherent, shippable change — big enough to matter,
small enough to verify and explain. If you cannot state the goal in one sentence, it is two
tasks.

---

## The loop

### Review
Re-read the code you are about to change, and its tests. Understand why it is the way it is
before deciding it is wrong. `git log -p <file>` often answers "why on earth is this here?".

### Plan
Decide the smallest change that fully does the job. Write the plan down (in your own working
notes, or in handoff.md if you are about to stop). Name the files you expect to touch — if
that list is long, your task is too big.

### Implement
- **Reuse first, and that means outside this repository too.** Search for existing helpers
  before writing new ones: this codebase has seams (`Storage`, `DatasetFormat`,
  `ModelProvider`, `JobQueue`) and shared utilities — extend them rather than routing around
  them. Then, before writing a non-trivial algorithm, format reader, or annotation
  behaviour from scratch, **look at how CVAT already did it**
  (`cvat-ai/cvat`, MIT: Intel 2018–2022, CVAT.ai 2022–2025). It is the closest mature
  implementation of this product's problem, and several parts of this codebase are already
  adapted from upstream sources for exactly that reason. Writing it yourself is the fallback,
  not the default — take that path only when no upstream equivalent exists, or when the
  upstream one is bound to a framework we do not use (CVAT is Django/Vue; its *algorithms*,
  *format conventions* and *data-model decisions* port, its request handling does not).
  Adapting source is governed by [ADR 0007](./docs/adr/0007-reusing-third-party-code.md) and
  is not optional paperwork: the per-adaptation licence audit exists because one GPL file in
  an MIT tree changes the answer, and the attribution is what makes the copy lawful. Reading
  upstream to get a convention right, without copying, is the lighter-weight move and is
  always available — `cvat_xml.py`'s cuboid attribute order came from exactly that.
- **Match the surrounding code.** Its naming, its comment density, its idioms. A change that
  reads like a foreign object is a change reviewers distrust.
- **Smallest effective change.** Do not rewrite a working component because you would have
  designed it differently.
- **Add the test with the code**, not after.

### Test
Run the project's own verification. Not a subset you feel good about:
```bash
./scripts/check.sh        # ruff · format · mypy · pytest ×2 · notices · eslint · tsc · vitest
```
For a bug fix, **reproduce the bug first**, then show the same check passing. A fix you never
saw fail is a guess.

Targeted runs while iterating are fine — the full suite before you call it done:
```bash
server/.venv/bin/python -m pytest server/tests/api/test_desktop.py -q
npm --prefix web run test
cd desktop/shell/src-tauri && cargo test          # if you touched Rust
```

### Debug
When something fails, find the cause. Do not:
- weaken, skip, or delete a test to make it pass;
- add a `try/except` that swallows the symptom;
- mark something flaky without evidence that it is.

### Self-review
Read your own diff as a hostile reviewer:
- Would this break anything that used to work?
- What input did I not consider? Empty, huge, concurrent, malformed?
- Did I leave debug output, dead code, or a commented-out block?
- Does every claim I am about to write in handoff.md correspond to something I actually ran?

### Document
Update [`handoff.md`](./handoff.md). **Every iteration. Not optional.** See the
[contract](#the-handoffmd-contract) below.

If behaviour changed, also update the doc that describes that behaviour, **in the same
change**. A plan that has drifted from the code is the failure mode this whole file exists
to prevent.

### Continue
Pick the next task and go again — **but by default, one iteration is one session.** When an
iteration is committed, pushed and written up, stop and report rather than starting the
next one. A session that runs six iterations is not six times as useful as six sessions: it
re-reads the same context, and it spends a human's budget without giving them a decision
point in between. The user can always say "keep going", and that is the cheap direction to
be wrong in.

Carry on past one iteration only when the work is genuinely unfinished — a change split
across two commits to keep each diff reviewable, or a defect found by the first iteration
that leaves the tree worse than you found it. Say so when you report.

---

## Rules that prevent wasted work

These exist because the expensive failure mode is not a bad change — it is a week of
motion with nothing finished.

**Before you build anything, check whether it exists.**
```bash
rg -n "the_thing_i_am_about_to_write" --glob '!node_modules'
```
If it exists and works → verify it, mark it verified in handoff.md, move on.
If it exists and is partial → **finish it in place**. Do not start a parallel implementation.
If it exists and is wrong → fix it, or replace it and delete the old one in the same change.

**Never leave two implementations of the same thing.** If you replace something, the old one
goes in the same commit.

**The three-touch rule.** If you find yourself editing the same file for the third time
without a clear behaviour improvement, stop. You are polishing, not progressing. Write down
what you actually learned and move to a different task.

**Do not follow a stale plan off a cliff.** If the plan says to build X and the code shows X
already exists, or that X no longer makes sense, **the plan is the bug**. Verify against the
code, fix the doc, and say so in handoff.md.

**Refactors need a reason you can state.** "Cleaner" is not a reason. "This function is
called from three places that each re-derive the same thing, and the next feature needs a
fourth" is.

**Do not relitigate settled decisions.** The ADRs record decisions and the evidence behind
them. Disagreeing is fine — but the route is a *new* ADR that supersedes the old one with a
new fact, not a quiet rewrite.

**Record dead ends.** If you tried an approach and it failed, put it in handoff.md under
*Tried and rejected*. Otherwise the next agent — possibly you, next session — will try it
again.

---

## Project-specific invariants

Break these and you have broken the product, even if the tests pass.

1. **One codebase, two shapes.** CurveVision runs as a desktop application and as a server.
   A change that improves one by degrading the other is the wrong change
   ([ADR 0006](./docs/adr/0006-one-codebase-two-shapes.md)).

2. **`services/` must not know which shape it is in.** No `if desktop:` in business logic.
   The difference is a `Settings` value, resolved at the edges.

3. **Layering is enforced, not aspirational** (`docs/ARCHITECTURE.md` §2):
   - `api/` → schemas, services, core. Never the ORM directly, never business logic.
   - `services/` → domain, core, the four seams. Never FastAPI or request models.
   - `storage/`, `formats/`, `ml/`, `jobs/` → never import `domain/`.

4. **Desktop-only capability is gated at the API edge.** Reading local paths is a feature on
   your machine and arbitrary file disclosure on a server. Routes in `api/v1/local.py`
   return 404 unless `settings.local_mode`. Keep it that way.

5. **Honesty in documentation is a hard requirement.** Never mark something **Done** unless
   it works and is tested. Use *In Progress* or *Planned*. Never invent a benchmark, a
   badge, or a metric — measure it or omit it.

6. **Schema changes need an Alembic migration**, tested up *and* down. A desktop user will
   open a new version against an old database.

7. **New dependency → `docs/THIRD_PARTY_NOTICES.md` entry**, with the license read from the
   package metadata rather than memory. CI fails otherwise.

8. **Adapting third-party source** follows
   [ADR 0007](./docs/adr/0007-reusing-third-party-code.md): keep the upstream copyright and license
   header, add a notices row naming the file and commit, and re-run the license audit first.

---

## The handoff.md contract

`handoff.md` is how a future session — with none of your context, possibly a different
agent entirely — picks up where you stopped. **Write it for a stranger.**

It must be **true**. An aspirational handoff is worse than none, because the next agent
builds on it.

Required sections (keep them, even if a section is "none this iteration"):

| Section | What goes in it |
| --- | --- |
| **Status** | One honest paragraph: where the project actually is. |
| **Last iteration** | What you changed, and *why*. The diff shows what; you supply why. |
| **Verification performed** | The exact commands you ran and their results. "Tests pass" is not enough — say which, and what the output was. |
| **Completed** | Functionality that works and is tested. Append; do not rewrite history. |
| **In progress** | Anything half-done, with enough detail to resume. Be specific about where you stopped. |
| **Next best action** | The single highest-value next task, and why it is that one. |
| **Remaining high-priority work** | The short queue after that. |
| **Bugs fixed** | What was broken, the root cause, how it is verified. |
| **Known issues** | Anything broken or fragile that you did not fix. Never quietly drop one. |
| **Tried and rejected** | Approaches that failed, and why. This is what stops repeated work. |
| **Decisions** | Choices a future agent would otherwise second-guess. Significant ones also get an ADR. |

Rules:
- Update it **after every iteration**, including iterations that mostly failed.
- If a claim in it turns out to be false, **fix the claim** — do not work around it.
- Keep it a working document, not a changelog. Prune detail that no longer helps; keep
  anything that prevents repeated work.

---

## Verification reference

| Command | Covers |
| --- | --- |
| `./scripts/check.sh` | Everything CI runs: ruff, format, mypy, pytest (server + SDK), notices, eslint, tsc, vitest |
| `server/.venv/bin/python -m pytest server/tests -q` | Server tests |
| `npm --prefix web run test` | Web tests |
| `cd desktop/shell/src-tauri && cargo test` | Rust shell (boots the real packaged server) |
| `python desktop/sidecar/build.py` | Packages the desktop server, then smoke-tests what it built |
| `server/.venv/bin/python scripts/check_notices.py` | Dependency attribution gate |

If a tool is missing in your environment, say so in handoff.md rather than silently skipping
the check — an unrun check is not a passing check.

---

## When to stop

Keep going on your own for everything in [Choosing what to work on](#choosing-what-to-work-on).
Stop and ask a human only when:

- **A product decision is genuinely ambiguous** and the alternatives lead to materially
  different work. State the options and your recommendation; do not stall on a coin flip you
  could call yourself.
- **The change would break a documented invariant** above, or supersede an ADR.
- **The action is destructive or hard to reverse**: force-pushing shared history, deleting
  data, rewriting a migration that has shipped, publishing anything outward.
- **You are blocked by something outside the repository**: missing credentials, an external
  service, a permission you do not have.
- **You have made no real progress in two consecutive iterations.** Stop, write down what
  you learned and what you tried, and ask. Two failed iterations is information; ten is
  waste.
- **You have finished an iteration.** This is the ordinary case, not a failure: commit,
  push, update `handoff.md`, and report. See [Continue](#continue) — the budget a session
  spends is a real cost to the person running it, and a finished iteration is the natural
  place to hand the decision back.

Before stopping for any reason: **leave the tree green if you can**, and make sure
`handoff.md` reflects reality.

---

## Quality bar

Act like a senior engineer who will still be maintaining this in a year:

- Preserve working systems.
- Make the smallest change that fully solves the problem.
- Reuse what exists.
- Catch problems early; fix what you break.
- Improve what genuinely needs improving, and leave the rest alone.
- Finish things.

The goal is a reliable, production-ready release — not motion.
