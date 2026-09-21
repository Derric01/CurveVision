# Contributing to CurveVision

CurveVision is open source because annotation tooling should not be a luxury good.
Contributions of every size are welcome — a typo fix and a new dataset format are both
worth having.

By participating you agree to the [Code of Conduct](../CODE_OF_CONDUCT.md).

## Before you start

**Open an issue first for anything non-trivial.** Not bureaucracy: it saves you from
building something that conflicts with work already in flight, and it gives us a chance to
say "yes, and here is the seam it should plug into".

Good places to start:

* Anything marked **In Progress** in the [roadmap](./ROADMAP.md) — the design is settled and
  the work is well-defined.
* A new dataset format. The plugin interface is small and there is a round-trip test
  pattern to copy.
* Documentation. If something confused you, it will confuse the next person.

## Setting up

See [DEVELOPMENT.md](./DEVELOPMENT.md). The short version:

```bash
./scripts/dev.sh     # infra in Docker, app on the host with hot reload
./scripts/check.sh   # everything CI runs
```

The server test suite needs **no external services** — SQLite, local storage, an in-process
queue. If you add a test that requires Postgres, mark it `@pytest.mark.postgres`.

## Making a change

1. **Branch** from `main`.
2. **Write the test first** where you can. For a bug fix, a test that fails before your fix
   is the most valuable thing in the pull request.
3. **Keep the diff focused.** A formatting sweep mixed into a behaviour change is very hard
   to review; send it separately.
4. **Run `./scripts/check.sh`** before pushing.
5. **Update the docs** in the same pull request. A feature whose documentation lands later
   usually never gets documented.

### Commit messages

Explain **why**, not just what — the diff already says what. A short subject line, a blank
line, then the reasoning:

```
Reject unknown annotation attributes instead of dropping them

Silently discarding a misspelled attribute meant a scripted import could
lose annotations with no error anywhere. Rejecting the write surfaces the
typo at the point it was made.
```

## What we look for in review

* **Correctness** — especially around annotation writes, permissions and interpolation.
  These are where a bug quietly destroys someone's work.
* **Tests** — new behaviour needs coverage; a bug fix needs a regression test.
* **Layering** — see the table in [DEVELOPMENT.md](./DEVELOPMENT.md#layering-rules). The
  seam packages must not import domain models.
* **Honesty** — if a feature is partial, say so in the docs and mark it *In Progress*. We
  would much rather ship a documented gap than an undocumented one.
* **Comments that explain reasoning.** Comments restating the code are noise; comments
  explaining why a non-obvious choice was made are the ones that survive.

## Standards

**Python**

* `ruff` and `mypy` clean. Type annotations on everything public.
* Services raise domain errors from `core/errors.py`; only the API layer knows about HTTP.
* Every model change ships with an Alembic migration, tested in both directions.

**TypeScript**

* `eslint` and `tsc --noEmit` clean. `any` is an error.
* `src/canvas/` imports nothing from React. It is a plain-TypeScript engine.
* Anything on a 60fps path avoids allocation in the loop.

## Browser harnesses

`./scripts/check.sh` covers everything CI runs on a push, and it does not open a browser.
Twenty-two scripts in `scripts/` do — they launch the **packaged desktop application**, drive
it in Chromium, and assert against the **API** rather than the DOM, because the question is
not "did the page look right" but "is the data now what the user asked for".

They earn their keep. Between them they have found every defect the unit suites missed: a
Content-Security-Policy that blocked the app's own scripts, frame images that never rendered,
a swallowed decode error that threw on fast scrubbing, a `useImperativeHandle` that captured
`null` and left seven editor controls wired to nothing, an issue anchored to a row in the
wrong table, a model picker that disabled its own run button over text it had just
unmounted the box for, every drawing tool silently saving a new shape to frame 0 whenever it
was actually drawn anywhere else, and a keyboard tool shortcut that genuinely switched the
engine's tool while leaving the toolbar highlighted on the old one. It also caught its own
author's mistake before the fact: the first version of `verify_view_settings.py` asserted
that hiding "labels" hid whole shapes, which is not what `Scene.showLabels` has ever done —
it only gates the small name chip drawn above one. The mask brush harness has not yet found
a defect of its own, but it is the only thing that has ever driven a real pointer drag
through `BrushTool` at all. `verify_cuboid.py`'s own first draft caught a coarser version of
the same trap: it counted lit pixels on the whole shapes layer as proof the wireframe was
drawn, which stayed well above threshold even with the wireframe rendering deliberately
disabled — the label chip alone lit enough pixels to hide a completely broken renderer.
Replaced with two probes at specific edges no fallback rendering path would produce.
`verify_resumable_upload.py` has not yet found a defect of its own either, but its first
draft would not have caught one if it had: asserting only that an interrupted-then-resumed
upload finishes correctly does not distinguish real resumption from a client that silently
abandons the old session and re-uploads the whole file, which also finishes correctly.
Rewritten to assert the mechanism — the retried chunk's own offset — rather than only the
outcome. `verify_job_review.py` pins a defect found by reading the header it was built
alongside: Submit was disabled only for a job already `submitted`, so pressing it on an
`accepted` one sent the `accepted → submitted` transition the server refuses with a 409 that
nothing rendered — an enabled button whose only possible outcome was a silent failure.
`verify_job_assignment.py` is the clearest example of why the bite has to be checked rather
than assumed: its claim that the pickers do not navigate passed happily with the pickers
deliberately nested *inside* the row's link, because `select_option` dispatches a change
without a click and so nothing bubbled to the anchor. Clicking first, as a person does,
makes it fail exactly as it should. `verify_review_queue.py` learned the neighbouring
lesson — **a fixture can make a check pass by coincidence**. Pointing the page's query at
`mine` instead of `reviewing` left its summary line reading "1 job waiting on you", which
is what the check expected, because the fixture happened to hold exactly one submitted job
either way. A second control job makes the sabotaged page say "2" and the check fail.
`verify_label_schema.py` adds the third variant of the same mistake: **do not wait for the
thing you are about to assert**. Its first draft waited for the new label's name to appear
in the editor and then checked that it was there, which can time out but can never report a
failure. It also learned that a check looking for a *missing* element has to read it
defensively — deleting the error banner deliberately killed the run with a Playwright
timeout instead of printing the two FAIL lines it should have.

They run **nightly and on every push to `main`** (`.github/workflows/browser.yml`), not on
pull requests: a PyInstaller build plus twenty-two end-to-end runs is twenty minutes, and CI
that slow stops being run. Trigger one by hand from the Actions tab — the workflow takes a
single harness name — or run one locally:

```bash
npm --prefix web run build          # 1. the frontend
python desktop/sidecar/build.py     # 2. the sidecar, which EMBEDS web/dist
python scripts/verify_issues_panel.py
python scripts/verify_frame_count_warning.py
python scripts/verify_skeleton_tool.py
python scripts/verify_mask_rendering.py
python scripts/verify_auto_annotate.py
python scripts/verify_shape_frame.py
python scripts/verify_mask_brush.py
python scripts/verify_tool_sync.py
python scripts/verify_view_settings.py
python scripts/verify_cuboid.py
python scripts/verify_resumable_upload.py
python scripts/verify_job_review.py
python scripts/verify_job_assignment.py
python scripts/verify_review_queue.py
python scripts/verify_label_schema.py
```

**Both build steps, in that order, every time.** The packaged sidecar embeds `web/dist`, so a
harness run after a frontend change without a sidecar rebuild silently exercises the
*previous* frontend. Nothing fails; the test just is not testing your code. That has already
cost one confusing debugging session.

If you add a harness, add it to the workflow's list — and prefer asserting against the API
over the DOM, so the check survives a redesign.

## Performance-sensitive areas

Two places where a change needs a benchmark, not an opinion:

* **`web/src/canvas/`** — `npm run bench`. The claim under test is that cost tracks what is
  on screen, not dataset size.
* **`server/curvevision/services/annotations.py`** — `pytest -m benchmark -s`.

If you make one of these slower for a good reason, say so in the pull request with the
numbers.

## Adding a dependency

Every dependency is a maintenance and security commitment made on behalf of everyone who
self-hosts CurveVision. Before adding one:

1. Can the standard library do it in under ~50 lines? (Our JSON logger can.)
2. Is it actively maintained, and appropriately licensed?
3. Does it pull in a large transitive tree?
4. Does it belong in the core, or behind an optional extra?

Then **add it to [THIRD_PARTY_NOTICES.md](./THIRD_PARTY_NOTICES.md)** with its license and
copyright holder. CI fails otherwise, deliberately — shipping software to users without
attribution is a licensing problem, not an oversight.

We would rather extend or contribute upstream to an existing project than fork it, and
rather use a good library than write our own. The reasoning behind each major choice is in
[ARCHITECTURE.md](./ARCHITECTURE.md#open-source-building-blocks--build-vs-extend-decisions).

## Reporting bugs

Include the CurveVision version or commit, what you did, what happened, what you expected,
and the `X-Request-ID` from the failing response if you have it — every log line carries it,
so it takes us straight to the cause.

**Security issues do not go in the issue tracker.** See [SECURITY.md](./SECURITY.md).

## Licensing

Contributions are accepted under the [MIT License](../LICENSE). By opening a pull request
you confirm you have the right to contribute the code and are happy for it to be released
under that license.

Do not paste code from another project into a pull request, even a permissively licensed
one, without flagging it clearly. Attribution has to be handled deliberately, and it is far
easier to do at review time than to untangle later.
