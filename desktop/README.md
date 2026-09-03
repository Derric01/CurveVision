# CurveVision Desktop — *Planned*

**There is no working desktop build yet.** This directory holds the design so the intent is
reviewable; shipping a stub that looks like an application would be worse than an empty
directory with an honest README.

## What it will be

A [Tauri v2](https://tauri.app/) shell around **the same web bundle the browser serves** —
explicitly not a second implementation of the product. The shared pieces are the API
contract and the entire `web/` application; the desktop-specific part is a storage and
transport adapter.

## Why it earns its existence

Three things a browser cannot do well:

1. **Local datasets without an upload round trip.** A 200 GB folder of frames on an
   external drive should be annotatable in place, not copied into object storage first.
2. **Offline work.** An embedded local server, so annotation continues without
   connectivity, with background sync to a remote instance when it returns.
3. **Large local files.** Direct filesystem access sidesteps browser memory limits on
   multi-gigabyte video.

## Why Tauri rather than Electron

| | Tauri | Electron |
| --- | --- | --- |
| Bundle size | ~10 MB | ~150 MB |
| Memory | System webview | Bundled Chromium per app |
| Security model | Explicit capability allow-list | Broad by default |
| Backend language | Rust | Node |

The system webview is the one real trade: rendering differs slightly across platforms. For
a Canvas2D annotation surface with no exotic CSS, that is an acceptable cost for a 15x
smaller download.

## Planned structure

```
desktop/
├─ src-tauri/
│  ├─ Cargo.toml
│  ├─ tauri.conf.json      capability allow-list: fs, dialog, shell(none)
│  └─ src/
│     ├─ main.rs           window and menu setup
│     ├─ local_store.rs    filesystem-backed Storage implementation
│     └─ sync.rs           background reconciliation with a remote instance
└─ (the web bundle, built from ../web)
```

The local store implements the same `Storage` contract the server uses, which is why the
seam exists at all.

## Open questions

* **Sync conflicts.** The server has optimistic concurrency on `annotation_version`;
  offline editing needs a merge strategy the current model does not describe.
* **Embedded server.** Ship the Python server as a sidecar process, or reimplement a
  subset in Rust? The sidecar is far less work and far larger to distribute.
* **Local vs remote identity.** How offline work is attributed once it syncs.

None of these are resolved. Input welcome — open a discussion.

## Building, once it exists

```bash
cd web && npm run build
cd ../desktop && cargo tauri build
```

Tracked in [../docs/ROADMAP.md](../docs/ROADMAP.md).
