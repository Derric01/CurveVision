# CurveVision

**Read [`AGENTS.md`](./AGENTS.md) before doing anything else.** It is the working contract
for any coding agent on this repository, and it is deliberately not Claude-specific — the
same protocol applies whichever agent is driving.

If you were told **"Start work."**, `AGENTS.md` is your complete instruction set: orient
first (read [`handoff.md`](./handoff.md), read the plan, check the code against it, run
`./scripts/check.sh`), then run the loop — Review → Plan → Implement → Test → Debug →
Self-review → Document → Continue.

Two rules about scope, because they cost real money to get wrong:

- **One iteration per session, by default.** Finish it, commit, push, write up `handoff.md`,
  then stop and report. Running iteration after iteration in a single session spends the
  budget of the person who asked without ever giving them a decision point. They can always
  say "keep going".
- **Reuse before you write, including from outside this repository.** CVAT (`cvat-ai/cvat`,
  MIT) is the closest mature implementation of this product's problem; read how it solved
  something before writing your own. `AGENTS.md` § *Implement* and
  [ADR 0007](./docs/adr/0007-reusing-third-party-code.md) say what that obliges you to do.

Two things it is easy to skip and expensive to skip:

- **`handoff.md` is updated every iteration.** It is how the next session knows where you
  stopped, and what you already tried and rejected.
- **Never mark something Done unless it works and is tested.** *In Progress* and *Planned*
  are honest; a false Done is not.

## Orientation

| Where | What |
| --- | --- |
| [`AGENTS.md`](./AGENTS.md) | The working contract — start here |
| [`handoff.md`](./handoff.md) | Current state, in flight, next best action |
| [`docs/iterations/ARCHIVE.md`](./docs/iterations/ARCHIVE.md) | Why things are the way they are — iterations 1–47 |
| [`docs/IMPLEMENTATION_PLAN.md`](./docs/IMPLEMENTATION_PLAN.md) | Plan of record, with honest status markers |
| [`docs/ARCHITECTURE.md`](./docs/ARCHITECTURE.md) | §1.1 two shapes · §2 layering rules · §8 build-vs-extend |
| [`docs/adr/`](./docs/adr/) | Decisions already made — supersede, do not quietly rewrite |
| `./scripts/check.sh` | Everything CI runs, in one command |

## The one-line summary of the product

CurveVision is an open-source computer-vision annotation platform that ships in **two shapes
from one codebase**: a desktop application you install, and a server your team opens in a
browser. Neither is the lesser. A change that improves one by degrading the other is the
wrong change.
