# The sidecar

CurveVision Desktop is a Tauri shell around a local CurveVision server. That server is
this directory's product: a single self-contained executable the shell spawns as a child
process, which needs no Python, no PostgreSQL, no Redis and no object store on the user's
machine.

## Build it

```bash
pip install -e 'server[dev,media,desktop]'
python desktop/sidecar/build.py
```

The output lands in `desktop/sidecar/dist/curvevision-local` (`.exe` on Windows). Unless
you pass `--skip-tests`, the script then launches what it built, waits for the handshake,
checks the API answers, and fails the build if any of that does not happen — a binary that
does not start is worse than no binary, and packaging breaks in ways source never does.

**PyInstaller does not cross-compile.** A macOS build must run on macOS, a Windows build on
Windows. The release workflow therefore builds on one runner per platform.

### Measured on Linux (x86-64, Python 3.11)

| | |
| --- | --- |
| Executable | 37 MB |
| Time from spawn to handshake | ~1.5–2.1 s |

These are the numbers from this repository's own build, not a target. They will differ on
other platforms and with other Python versions.

## How the shell talks to it

The shell spawns the executable and reads its standard output until one line begins with
`CURVEVISION_READY `. The rest of that line is JSON:

```json
{
  "url": "http://127.0.0.1:49312",
  "token": "cv_...",
  "data_dir": "/home/you/.local/share/CurveVision",
  "version": "0.1.0"
}
```

* **`url`** — always loopback, always an OS-assigned port. A fixed port would collide with
  a second instance and with whatever else the user is running.
* **`token`** — a fresh API token, minted on every launch, with the previous one revoked.
  A token that ends up in a log or a crash report stops working when the app restarts.
* **`data_dir`** — the database, media and signing key for this installation.

The process stays in the foreground and exits when terminated, which is what Tauri's
sidecar lifecycle expects.

## Running it without the shell

The same executable is useful on its own — it is the shortest path to a working
CurveVision with no configuration at all:

```bash
./curvevision-local                     # prints its URL and token, then serves
./curvevision-local --port 8000         # a fixed port, for scripting against
./curvevision-local --print-data-dir    # where this installation keeps its data
```

Installed from source, the same entry point is `curvevision-local`.

## What the spec has to say

Read [`curvevision-local.spec`](./curvevision-local.spec); the comments explain each
inclusion. The two that are easy to get wrong:

* **`curvevision/migrations` is bundled as data.** Alembic reads `env.py` and every
  revision script from disk at runtime. Without them, a second release could not upgrade a
  database written by the first — and a desktop user *will* install an update over their
  existing work.
* **`uvicorn`, `aiosqlite` and the anyio backend are hidden imports.** All three are
  resolved from strings at runtime, so no static analysis finds them.

## What is not in here

No S3 client, no Redis, no Dramatiq, no PostgreSQL driver. They are not excluded by a
rule; they are simply not installed in the build environment, and a local installation has
no use for them. Point the desktop app at a shared server instead, and that server brings
its own.
