//! Declares the shell's own commands to Tauri, which generates an `allow-…` permission for
//! each (`capabilities/default.json` grants them to the local window).
//!
//! Without this, Tauri refuses every one of them: the window's page is served by the local
//! server over `http://127.0.0.1`, which Tauri treats as a remote origin, and it lets a
//! remote origin call only what a capability names. Until iteration 85 nothing did, so the
//! folder and file pickers never worked in the built application — and nothing could tell,
//! because the only tests stood in for the IPC bridge (`scripts/verify_desktop_shell.py`
//! now drives the real one).

const COMMANDS: &[&str] = &[
    "connection",
    "choose_folder",
    "choose_files",
    "connect_to_team_server",
];

fn main() {
    tauri_build::try_build(
        tauri_build::Attributes::new()
            .app_manifest(tauri_build::AppManifest::new().commands(COMMANDS)),
    )
    .expect("failed to run tauri-build");
}
