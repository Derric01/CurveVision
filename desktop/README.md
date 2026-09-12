# CurveVision Desktop

**Install one file, point at a folder, start drawing.** No account, no server, no Docker,
no configuration. Your images never move off your machine — CurveVision annotates them
where they already sit.

This is one of the two shapes CurveVision ships in, and it is not the lesser one. The other
is a server your team opens in a browser. They are the same code; see
[ADR 0006](../docs/adr/0006-one-codebase-two-shapes.md).

## How it fits together

```
  ┌──────────────────────────────────────────────────────────────┐
  │  shell/     Tauri v2 · 457 lines of Rust                     │
  │  · spawns the server below, kills it on every exit path      │
  │  · reads one line of JSON: { url, token, data_dir, version } │
  │  · opens the window there, injects the token before load     │
  │  · native folder and file dialogs                            │
  └──────────────────────────────┬───────────────────────────────┘
                                 │  spawns · loopback only
  ┌──────────────────────────────▼───────────────────────────────┐
  │  sidecar/   curvevision-local — one executable, ~38 MB       │
  │             the whole CurveVision server + the web editor    │
  │             SQLite · local files · in-process jobs           │
  └──────────────────────────────────────────────────────────────┘
```

The shell is deliberately thin. It does the three things a browser cannot — run the server,
sign you in, open native dialogs — and nothing else. The editor, the API and the exporters
are the same code a server deployment runs; there is no second implementation here.

## Build it

```bash
pip install -e 'server[dev,media,desktop]'
npm --prefix web install && npm --prefix web run build   # the editor, bundled into the server
python desktop/sidecar/build.py                          # builds, then smoke-tests

cd desktop/shell/src-tauri
cargo build --release
```

[`sidecar/README.md`](./sidecar/README.md) documents the packaging, the handshake protocol
and the measured numbers.

### Without the shell

The packaged server is useful on its own — it is the shortest path to a working CurveVision
with no configuration whatsoever:

```bash
./sidecar/dist/curvevision-local                 # prints its URL and token, then serves
./sidecar/dist/curvevision-local --port 8000     # a fixed port, for scripting
./sidecar/dist/curvevision-local --print-data-dir
```

Open the printed URL in any browser and you get the complete application.

## Status

**Working, verified end to end against the packaged binary:**

| | |
| --- | --- |
| Zero-configuration local mode — app data dir, Alembic migration, one local account | **Done** |
| Handshake — loopback-only OS-assigned port, fresh token each launch, previous revoked | **Done** |
| Packaged server: ~38 MB, ~1.5–2.1 s spawn to handshake (Linux, x86-64) | **Done** |
| Shell: process supervision, no-sign-in token injection, native dialogs, menus (6.2 MB release binary) | **Done** |
| Never orphans the server — verified by `kill -9` on the shell | **Done** |
| Annotating local folders in place, nothing copied | **Done** |
| The editor served by the app itself, same-origin with its API | **Done** |
| Frontend reads the injected connection and skips the sign-in screen | **Done** |
| Open-folder flow wired to the shell's native picker | **In Progress** — the server endpoint and the shell command exist; the web UI does not call them yet |
| Signed installers (`.dmg`, `.msi`, `.AppImage`) built per platform in CI | **Planned** |
| Auto-update | **Planned** |
| Pointing the desktop app at a shared team server | **Planned** |

Measurements are from this repository's own Linux build, not targets. They will differ on
other platforms.

## Where your data lives

One directory, which you can copy to another machine:

| Platform | Path |
| --- | --- |
| macOS | `~/Library/Application Support/CurveVision` |
| Windows | `%LOCALAPPDATA%\CurveVision` |
| Linux | `$XDG_DATA_HOME/CurveVision`, else `~/.local/share/CurveVision` |

It holds `curvevision.db` (all your projects and annotations), `media/` (anything you
uploaded rather than annotated in place), and `secret.key`. **Images you annotated in place
are not in here** — they are wherever you left them, and CurveVision never writes to or
deletes them.

## Why Tauri rather than Electron

| | Tauri | Electron |
| --- | --- | --- |
| Shell binary | **6.2 MB** measured here (release, Linux x86-64) | ~150 MB typical |
| Memory | System webview | Bundled Chromium per app |
| Security model | Explicit capability allow-list | Broad by default |
| Backend language | Rust | Node |

The system webview is the real trade: rendering differs slightly across platforms. For a
Canvas2D annotation surface with no exotic CSS, that is an acceptable cost for a far
smaller download. The capability allow-list is in
[`shell/src-tauri/capabilities/default.json`](./shell/src-tauri/capabilities/default.json)
and is deliberately short.

## Security

The auto-provisioned account with no password is safe for exactly one reason: **the server
binds `127.0.0.1` on an OS-assigned port and is reachable from nothing else.** Verified by
connecting to the host's non-loopback address and being refused.

Two further properties:

* An unauthenticated request is still rejected (401), so a stray page in a browser on the
  same machine gets nothing.
* Reading arbitrary local paths — the whole point of annotating in place — exists *only*
  when `local_mode` is on. On a shared server those routes return 404, so the capability is
  absent rather than merely forbidden.
