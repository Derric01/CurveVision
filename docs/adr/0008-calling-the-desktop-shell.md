# ADR 0008 — How the page calls the desktop shell

**Status:** Accepted · **Date:** 2026-09

## Context

Almost everything the desktop application does, it does through the ordinary HTTP API,
because the shell spawns a real CurveVision server and the window is same-origin with it.
Two things cannot work that way, because they are not the server's to do:

- **A native folder picker.** The browser's `<input type="file">` gives us file *contents*,
  which is precisely what we do not want — the point of local mode is to annotate files
  where they are, without copying a byte. What we need is a *path*, and only the operating
  system's own dialog can supply one.
- **The application menu.** File ▸ Open Folder… (Cmd/Ctrl+O) lives in the native menu bar,
  and the shell emits `menu:open-folder` when it is used.

Both mean calling Tauri's IPC from the page. There were three ways to do it.

## Options

### 1. `withGlobalTauri: true`

Tauri attaches a convenience object to `window`, and the page calls `window.__TAURI__.core
.invoke(...)`. No new dependency.

Reading the Tauri source settled a question that had been left open: `withGlobalTauri` does
**not** control whether IPC is available. The bootstrap (`__TAURI_INTERNALS__`) is injected
either way; the flag only decides whether the convenience wrapper is attached. So the real
security boundary is `capabilities/default.json`, which is short and explicit, and not this
flag.

That makes the flag a matter of surface area rather than of security: it puts the whole
shell API on a global of a page served over HTTP, and it has no types, so every call site
hand-writes the shape of a response.

### 2. Call `__TAURI_INTERNALS__.invoke` directly

No dependency, no global. Also no contract: it is an internal name that Tauri is free to
change between minor versions, and nothing would fail at build time when it did.

### 3. `@tauri-apps/api`, dynamically imported

The supported client for exactly this, typed, versioned alongside the Rust crate. Costs one
npm dependency and an attribution entry.

## Decision

**Option 3.** `withGlobalTauri` stays `false`.

The import is dynamic and guarded by `isDesktop()`, so the browser build never fetches the
chunk — the code is in the bundle as a separate chunk that is only requested inside the
shell. It sits behind three functions in `web/src/desktop.ts` (`chooseFolder`,
`onOpenFolder`, `OPEN_FOLDER_EVENT`), which are the only place in the application that knows
a shell exists.

That seam is load-bearing for a reason beyond tidiness: Playwright drives Chromium, not the
Tauri webview, so a real `invoke()` across the IPC bridge has **no automated coverage
anywhere**, and cannot have any in this environment. Keeping the uncoverable part down to
three short functions is what lets everything either side of it be tested — and the seam's
contract in a browser (`null` from the picker, a no-op subscription) is itself tested, so
the browser build cannot start throwing from a menu subscription at mount.

## Consequences

- One dependency, one attribution entry, one code-split chunk a browser never loads.
- Version coupling: `@tauri-apps/api` 2.x pairs with the `tauri` 2.x crate. Upgrading one
  means upgrading the other.
- The shell API is not reachable from arbitrary page script via a global.
- IPC itself remains verified by hand on a real desktop, and this is stated in
  `handoff.md` under *Known issues* rather than implied to be covered.

## Revisit if

- Tauri changes how the frontend API is distributed, or `withGlobalTauri` starts gating IPC
  rather than only the convenience object.
- A way to drive the real webview in CI becomes available — then the seam can be tested
  through, and the argument for keeping it this thin gets stronger, not weaker.
