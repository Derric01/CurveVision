# ADR 0006 — One codebase, two shapes: desktop and server

**Status:** Accepted · **Date:** 2026-09

## Context

CurveVision was built server-first: Docker Compose, PostgreSQL, MinIO, multi-user
organizations and roles. That is the right shape for a team, and the wrong shape for the
person we most want to reach — someone with a folder of images who wants to draw boxes on
them this afternoon and has never heard of Docker.

The reverse is equally true. A desktop-only tool cannot give a team a shared review queue,
an audit trail, or a dataset release everyone can point at.

Both users are real, and neither is a degraded version of the other.

The trap is obvious and common: build the server product, then build a "desktop edition"
that is a second implementation with its own storage layer, its own auth, its own editor
quirks, and a feature gap that only widens. Two products wearing one name.

## Options

1. **Server only.** A desktop user runs Docker, or does not use CurveVision.
2. **Two implementations.** A native desktop app alongside the web app, sharing a wire
   protocol and nothing else.
3. **An offline "lite" edition.** A cut-down desktop build with a subset of features.
4. **One application, two configurations.** The same server binary runs on a laptop against
   SQLite and local files, or in a container against PostgreSQL and S3. A thin native shell
   supervises the local one and opens a window on it.

## Decision

**Option 4.** One codebase. The desktop application is the same server, packaged as a single
executable, supervised by a ~450-line Rust shell whose entire job is starting a child
process and opening native dialogs.

The difference between the two shapes is *configuration*:

| | Desktop | Server |
| --- | --- | --- |
| Database | SQLite in the app data dir | PostgreSQL |
| Storage | Local filesystem, files annotated in place | S3 / MinIO / local |
| Jobs | `inline` | `dramatiq` + Redis |
| Web bundle | Served by the app itself | Served by nginx in front |
| Sign-in | None; one local account | Real accounts, roles, orgs |
| `local_mode` | `True` | `False` |

Three constraints make this more than an aspiration:

1. **`services/` may not know which shape it is in.** No `if desktop:` in business logic.
   Everything above is a `Settings` value resolved at the edges.
2. **Desktop-only capability is gated at the API edge.** Reading arbitrary local paths is a
   feature on your own machine and a vulnerability on a shared one. `api/v1/local.py`
   returns 404 on every route unless `local_mode`, so on a server the capability does not
   exist rather than being merely forbidden.
3. **The desktop window loads the editor from the local server**, not from a `tauri://`
   asset URL. Same-origin by construction: no CORS boundary, no second origin, no
   desktop-only auth path.

## Consequences

**What it cost.** Adding the desktop application changed **1,460 lines added, 42 removed**
across four commits. Nothing was rewritten. The four seam interfaces — `Storage`,
`JobQueue`, `DatasetFormat`, `ModelProvider` — plus the `GUID`/`EnumString`/`JSONDocument`
type decorators that let SQLite and PostgreSQL behave identically, were all already there.
They were written to keep the tests free of external services; the desktop app was the
payoff nobody had planned for.

**What it buys.**

* A person installs one file and annotates. No account, no server, no Docker.
* A team runs the same application and gets real collaboration.
* `curvevision-local` on its own opens a complete working CurveVision in a browser, which
  is also the fastest possible development setup.
* One bug fix fixes both shapes. One feature ships to both.

**What it costs going forward.**

* PyInstaller does not cross-compile, so releases need one CI runner per platform.
* ~1.5–2.1 s from spawn to handshake on Linux, and a ~38 MB executable. Acceptable for an
  application launched a few times a day; it would not be for a CLI.
* Every new feature must be considered in both shapes. A feature that only makes sense with
  Redis needs a degraded-but-honest path on a laptop, or an explicit "server only" marker.
* The shell is Rust, a third language in the repository. Bounded deliberately: process
  supervision and dialogs, nothing else.

**The rule this ADR exists to enforce:** a change that improves one shape by degrading the
other is the wrong change, no matter how much tidier it looks.
