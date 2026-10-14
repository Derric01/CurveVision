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
| Handshake — loopback only, on the port it had last launch when that is free (else one the OS assigns), fresh token each launch, previous revoked | **Done** |
| Packaged server: ~38 MB, ~1.5–2.1 s spawn to handshake (Linux, x86-64) | **Done** |
| Shell: process supervision, no-sign-in token injection, native dialogs, menus (6.2 MB release binary) | **Done** |
| Never orphans the server — verified by `kill -9` on the shell | **Done** |
| Annotating local folders in place, nothing copied | **Done** |
| The editor served by the app itself, same-origin with its API | **Done** |
| Frontend reads the injected connection and skips the sign-in screen | **Done** |
| Open-folder and choose-files flows wired to the shell's native pickers | **Done** — but refused by Tauri in every built application until iteration 85: the shell's commands were never granted (ADR 0008, *Update*). Since iteration 95 the whole flow is driven in the real application on Linux: *Open Folder…* (Ctrl+O) and *Choose files…* open GTK's own dialogs, a path is chosen in them at the keyboard, and the task gains the frames, read in place (check 11 of `verify_desktop_shell.py`). macOS's and Windows' dialogs are not driven |
| The real application driven in CI — shell, webview, server — over WebDriver (`scripts/verify_desktop_shell.py`, Linux), including the webview keeping IndexedDB across launches | **Done** |
| Installers built per platform in CI: `.deb`, `.rpm`, `.AppImage`; `.dmg`; NSIS `.exe` (`.github/workflows/desktop.yml`) | **Done, unsigned** — the `.deb` is installed and the server inside it used on every run |
| Code signing (Apple Developer ID and notarisation, Windows Authenticode) | **Blocked** — needs the project's own certificates |
| Auto-update | **Done, unreleased** — *Help ▸ Check for Updates…* and a notice in the app; an update installs only if its signature verifies against the key built in. Driven over WebDriver against a local feed: a newer version is found, and a tampered download or one signed with another key is refused. A real update needs the maintainers' key pair in the repository's secrets and a release carrying `latest.json` — see *Releasing an update* below |
| Pointing the desktop app at a shared team server | **Done** — *File ▸ Connect to a Team Server…*; the team server opens in a window with no access to the shell and none of this computer's credentials. Both menu items are driven at the keyboard under Xvfb (`xdotool`), since WebDriver cannot reach a native menu (check 10 of `verify_desktop_shell.py`) |

Measurements are from this repository's own Linux build, not targets. They will differ on
other platforms.

## Releasing an update

The application installs an update only when the download's minisign signature verifies
against the public key built into it; the feed (`latest.json`) is not trusted, the signature
is. Neither key is in this repository. To turn updates on, a maintainer:

1. Makes the key pair once — `npx @tauri-apps/cli signer generate -w curvevision.key` — and
   keeps the private half somewhere only maintainers reach.
2. Adds the repository secrets `TAURI_SIGNING_PRIVATE_KEY` (the private key's text),
   `TAURI_SIGNING_PRIVATE_KEY_PASSWORD`, and `CURVEVISION_UPDATER_PUBKEY` (the `.pub` file's
   text, as Tauri prints it). From then on `.github/workflows/desktop.yml` builds the public
   key and the feed — this repository's `releases/latest/download/latest.json` — into the
   installers, and signs the AppImage, the NSIS installer and the macOS `.app.tar.gz`.
3. Makes the feed from those artifacts and attaches it, with them, to the release:

   ```sh
   python scripts/updater_manifest.py --version 0.2.0 \
       --base-url https://github.com/Derric01/CurveVision/releases/download/v0.2.0 \
       --notes "What changed" downloaded-artifacts/ > latest.json
   ```

Installs that predate step 2 have no key and cannot update themselves; they say so under
*Help ▸ Check for Updates…*. Losing the private key means every install must be updated by
hand once, to a build with a new public key.

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
binds `127.0.0.1` and is reachable from nothing else.** Verified by
connecting to the host's non-loopback address and being refused.

Two further properties:

* An unauthenticated request is still rejected (401), so a stray page in a browser on the
  same machine gets nothing.
* Reading arbitrary local paths — the whole point of annotating in place — exists *only*
  when `local_mode` is on. On a shared server those routes return 404, so the capability is
  absent rather than merely forbidden.
