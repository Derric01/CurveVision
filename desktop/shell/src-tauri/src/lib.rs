//! CurveVision Desktop.
//!
//! A thin shell around the real product. It does three things a browser cannot:
//!
//! 1. **Runs the server for you.** The packaged CurveVision server is spawned as a child
//!    process on launch and killed on exit, so installing an annotation tool does not
//!    mean installing Python, PostgreSQL, Redis and an object store first.
//! 2. **Signs you in.** The server mints a token at startup and the shell hands it to the
//!    page before it loads. On a single-user machine there is nobody else to be, so there
//!    is no sign-in screen.
//! 3. **Opens folders.** A native directory picker, so a folder of images on an external
//!    drive can be annotated where it sits instead of uploaded first.
//!
//! It can also show a team's shared server instead of this computer's — see `team`.
//!
//! Everything else — the editor, the API, the exporters — is the same code the server
//! deployment runs. This is deliberately not a second implementation of the product.

mod sidecar;
mod team;
mod updates;

use std::path::PathBuf;
use std::sync::Arc;

use serde::Serialize;
use tauri::menu::{AboutMetadata, Menu, MenuItem, PredefinedMenuItem, Submenu};
use tauri::{AppHandle, Emitter, Manager, RunEvent, State, Url, WebviewUrl, WebviewWindowBuilder};
use tauri_plugin_dialog::DialogExt;

use sidecar::{Handshake, Sidecar};
use team::{LOCAL_WINDOW, TEAM_WINDOW};

/// The executable the server is packaged as, built by `desktop/sidecar/build.py`.
#[cfg(windows)]
const SIDECAR_NAME: &str = "curvevision-local.exe";
#[cfg(not(windows))]
const SIDECAR_NAME: &str = "curvevision-local";

/// What the web application is told about its host.
#[derive(Clone, Serialize)]
struct Connection {
    url: String,
    token: String,
    data_dir: String,
    version: String,
    /// Lets the same bundle behave differently when it is running in a browser against a
    /// shared server, where there *are* other people and a sign-in screen is correct.
    desktop: bool,
}

impl From<&Handshake> for Connection {
    fn from(handshake: &Handshake) -> Self {
        Self {
            url: handshake.url.clone(),
            token: handshake.token.clone(),
            data_dir: handshake.data_dir.clone(),
            version: handshake.version.clone(),
            desktop: true,
        }
    }
}

/// Where this installation keeps its data. Shown in the interface so it is never a mystery
/// which folder to back up.
#[tauri::command]
fn connection(state: State<'_, Arc<Sidecar>>) -> Connection {
    Connection::from(&state.handshake)
}

/// Ask the user for a folder of images to annotate in place.
///
/// The dialog is native and the shell only ever returns the chosen path — the web
/// application has no filesystem access of its own, and the server reads the folder.
#[tauri::command]
async fn choose_folder(app: AppHandle, title: Option<String>) -> Option<String> {
    let (sender, receiver) = std::sync::mpsc::channel();
    app.dialog()
        .file()
        .set_title(title.as_deref().unwrap_or("Choose a folder of images"))
        .pick_folder(move |chosen| {
            let _ = sender.send(chosen.map(|path| path.to_string()));
        });
    receiver.recv().ok().flatten()
}

/// Ask the user for individual image or video files.
#[tauri::command]
async fn choose_files(app: AppHandle) -> Vec<String> {
    let (sender, receiver) = std::sync::mpsc::channel();
    app.dialog()
        .file()
        .set_title("Choose images or video")
        .add_filter(
            "Media",
            &[
                "jpg", "jpeg", "png", "bmp", "webp", "tif", "tiff", "mp4", "mov", "mkv", "avi",
                "webm",
            ],
        )
        .pick_files(move |chosen| {
            let paths = chosen
                .map(|files| files.into_iter().map(|path| path.to_string()).collect())
                .unwrap_or_default();
            let _ = sender.send(paths);
        });
    receiver.recv().unwrap_or_default()
}

/// Ask where to save an export, via the operating system's own save dialog.
///
/// Returns the chosen path, or nothing when the user cancelled. The local server writes the
/// archive there (`/projects/{id}/local-export`): an export with its images can be tens of
/// gigabytes, which a download through the webview would hold in the page's memory.
#[tauri::command]
async fn choose_save_file(app: AppHandle, default_name: String) -> Option<String> {
    let (sender, receiver) = std::sync::mpsc::channel();
    app.dialog()
        .file()
        .set_title("Save export")
        .set_file_name(&default_name)
        .add_filter("Zip archive", &["zip"])
        .save_file(move |chosen| {
            let _ = sender.send(chosen.map(|path| path.to_string()));
        });
    receiver.recv().ok().flatten()
}

/// Find the packaged server.
///
/// Installed, it sits beside the shell's own executable, which is where Tauri puts an
/// external binary. In a source checkout it has not been installed anywhere, so fall back
/// to wherever `build.py` last wrote it.
fn locate_sidecar(app: &AppHandle) -> PathBuf {
    if let Ok(resource) = app
        .path()
        .resolve(SIDECAR_NAME, tauri::path::BaseDirectory::Resource)
    {
        if resource.is_file() {
            return resource;
        }
    }
    if let Ok(executable) = std::env::current_exe() {
        if let Some(alongside) = executable.parent().map(|dir| dir.join(SIDECAR_NAME)) {
            if alongside.is_file() {
                return alongside;
            }
        }
    }
    // `target/debug/curvevision-desktop` -> the repository root.
    std::env::current_dir()
        .unwrap_or_default()
        .join("../../sidecar/dist")
        .join(SIDECAR_NAME)
}

/// Where the team server choice is remembered between launches.
fn remembered_path(app: &AppHandle) -> Option<PathBuf> {
    app.path()
        .app_config_dir()
        .ok()
        .map(|folder| folder.join("connection.json"))
}

/// The window is created here rather than declared in `tauri.conf.json` because it cannot
/// exist until the server does: its URL contains a port the OS chose a moment ago, and its
/// initialization script contains a token that was minted at the same time.
fn open_local_window(app: &AppHandle, handshake: &Handshake, path: &str) -> tauri::Result<()> {
    let connection = Connection::from(handshake);
    let injected = serde_json::to_string(&connection).unwrap_or_else(|_| "null".to_string());

    let local: Url = handshake
        .url
        .parse()
        .map_err(|_| tauri::Error::WebviewNotFound)?;
    let url = local
        .join(path)
        .map_err(|_| tauri::Error::WebviewNotFound)?;

    WebviewWindowBuilder::new(app, LOCAL_WINDOW, WebviewUrl::External(url))
        .title("CurveVision")
        .inner_size(1440.0, 900.0)
        .min_inner_size(960.0, 600.0)
        .resizable(true)
        .center()
        // Runs before any of the page's own scripts, so the application never renders a
        // signed-out state it would immediately have to replace — and only on this
        // computer's server, so the credential goes nowhere else.
        .initialization_script(team::initialization_script(
            &local.origin().ascii_serialization(),
            &injected,
        ))
        .build()?;
    Ok(())
}

/// A team server's pages, in a window with no initialization script and — because the
/// capability file names only the local window — no access to the shell.
fn open_team_window(app: &AppHandle, url: &Url) -> tauri::Result<()> {
    WebviewWindowBuilder::new(app, TEAM_WINDOW, WebviewUrl::External(url.clone()))
        .title(format!(
            "CurveVision — {}",
            url.host_str().unwrap_or_default()
        ))
        .inner_size(1440.0, 900.0)
        .min_inner_size(960.0, 600.0)
        .resizable(true)
        .center()
        .build()?;
    Ok(())
}

/// Show `url`'s pages instead of whatever the window shows now. The new window opens
/// before the old one closes: closing the last window would quit the application.
fn switch_to_team(app: &AppHandle, url: &Url) -> tauri::Result<()> {
    if let Some(previous) = app.get_webview_window(TEAM_WINDOW) {
        previous.destroy()?;
    }
    open_team_window(app, url)?;
    if let Some(local) = app.get_webview_window(LOCAL_WINDOW) {
        local.close()?;
    }
    Ok(())
}

/// Show this computer's server, at `path`, instead of a team server.
fn switch_to_local(app: &AppHandle, path: &str) -> tauri::Result<()> {
    let Some(server) = app.try_state::<Arc<Sidecar>>() else {
        return Ok(());
    };
    match app.get_webview_window(LOCAL_WINDOW) {
        Some(local) => {
            if let Ok(url) = server
                .handshake
                .url
                .parse::<Url>()
                .and_then(|u| u.join(path))
            {
                local.navigate(url)?;
            }
            local.set_focus()?;
        }
        None => open_local_window(app, &server.handshake, path)?,
    }
    if let Some(team) = app.get_webview_window(TEAM_WINDOW) {
        team.close()?;
    }
    Ok(())
}

/// Switch the window to a team server, and remember it for the next launch.
///
/// Called by the local window's Team server page once it has checked the address with the
/// server itself; the shell checks only what it would be unsafe to open or store.
///
/// `async` because it creates a window: a synchronous command runs on the main thread,
/// and creating a window from one deadlocks on Windows.
#[tauri::command]
async fn connect_to_team_server(app: AppHandle, url: String) -> Result<(), String> {
    let url = team::team_server_url(&url)?;
    if let Some(path) = remembered_path(&app) {
        team::remember(&path, Some(&url))
            .map_err(|error| format!("Could not remember the team server: {error}"))?;
    }
    switch_to_team(&app, &url).map_err(|error| format!("Could not open {url}: {error}"))
}

fn build_menu(app: &AppHandle) -> tauri::Result<Menu<tauri::Wry>> {
    let open_folder = MenuItem::with_id(
        app,
        "open-folder",
        "Open Folder…",
        true,
        Some("CmdOrCtrl+O"),
    )?;
    let show_data =
        MenuItem::with_id(app, "show-data-dir", "Show Data Folder", true, None::<&str>)?;
    let team_server = MenuItem::with_id(
        app,
        "team-server",
        "Connect to a Team Server…",
        true,
        None::<&str>,
    )?;
    let this_computer = MenuItem::with_id(
        app,
        "use-this-computer",
        "Use This Computer",
        true,
        None::<&str>,
    )?;

    let file = Submenu::with_items(
        app,
        "File",
        true,
        &[
            &open_folder,
            &show_data,
            &PredefinedMenuItem::separator(app)?,
            &team_server,
            &this_computer,
            &PredefinedMenuItem::separator(app)?,
            &PredefinedMenuItem::close_window(app, None)?,
        ],
    )?;
    let edit = Submenu::with_items(
        app,
        "Edit",
        true,
        &[
            &PredefinedMenuItem::undo(app, None)?,
            &PredefinedMenuItem::redo(app, None)?,
            &PredefinedMenuItem::separator(app)?,
            &PredefinedMenuItem::cut(app, None)?,
            &PredefinedMenuItem::copy(app, None)?,
            &PredefinedMenuItem::paste(app, None)?,
            &PredefinedMenuItem::select_all(app, None)?,
        ],
    )?;
    let help = Submenu::with_items(
        app,
        "Help",
        true,
        &[
            &MenuItem::with_id(
                app,
                "check-for-updates",
                "Check for Updates…",
                true,
                None::<&str>,
            )?,
            &PredefinedMenuItem::separator(app)?,
            &PredefinedMenuItem::about(
                app,
                Some("About CurveVision"),
                Some(AboutMetadata {
                    name: Some("CurveVision".into()),
                    version: Some(env!("CARGO_PKG_VERSION").into()),
                    license: Some("MIT".into()),
                    website: Some("https://github.com/Derric01/CurveVision".into()),
                    ..Default::default()
                }),
            )?,
        ],
    )?;

    Menu::with_items(app, &[&file, &edit, &help])
}

/// Whether a newer version is offered (`updates.rs`), for the page's update notice.
#[tauri::command]
async fn check_for_update(app: AppHandle) -> Result<updates::UpdateStatus, String> {
    updates::check(&app).await
}

/// Download, verify and install the newer version, then restart. Returns only on failure.
#[tauri::command]
async fn install_update(app: AppHandle) -> Result<(), String> {
    updates::install(&app).await
}

/// Help ▸ Check for Updates…: say what the feed says, and offer to install.
async fn check_from_menu(app: AppHandle) {
    use tauri_plugin_dialog::{MessageDialogButtons, MessageDialogKind};
    let (message, offer) = match updates::check(&app).await {
        Ok(status) if !status.configured => (
            "Updates are not configured for this build of CurveVision.".to_owned(),
            false,
        ),
        Ok(updates::UpdateStatus {
            available: Some(version),
            current,
            ..
        }) => (
            format!(
                "CurveVision {version} is available (this is {current}). Install it and restart?"
            ),
            true,
        ),
        Ok(status) => (
            format!("CurveVision {} is the latest version.", status.current),
            false,
        ),
        Err(error) => (format!("Could not check for updates: {error}"), false),
    };
    let dialog = app
        .dialog()
        .message(message)
        .title("Updates")
        .kind(MessageDialogKind::Info);
    let install = if offer {
        dialog
            .buttons(MessageDialogButtons::OkCancelCustom(
                "Install and Restart".into(),
                "Later".into(),
            ))
            .blocking_show()
    } else {
        dialog.buttons(MessageDialogButtons::Ok).blocking_show();
        false
    };
    if install {
        if let Err(error) = updates::install(&app).await {
            app.dialog()
                .message(error)
                .title("Updates")
                .kind(MessageDialogKind::Error)
                .buttons(MessageDialogButtons::Ok)
                .blocking_show();
        }
    }
}

#[cfg_attr(mobile, tauri::mobile_entry_point)]
pub fn run() {
    tauri::Builder::default()
        .plugin(tauri_plugin_dialog::init())
        .plugin(tauri_plugin_opener::init())
        // The key and the feed are given per check (`updates.rs`), so the configuration's
        // own pubkey is left empty and unused.
        .plugin(tauri_plugin_updater::Builder::new().build())
        .invoke_handler(tauri::generate_handler![
            connection,
            choose_folder,
            choose_files,
            choose_save_file,
            connect_to_team_server,
            check_for_update,
            install_update
        ])
        .setup(|app| {
            let handle = app.handle().clone();
            let executable = locate_sidecar(&handle);

            let server = match sidecar::start(&executable) {
                Ok(server) => Arc::new(server),
                Err(message) => {
                    // A dialog rather than a silent exit: the person double-clicked an
                    // icon and deserves to know why nothing happened.
                    use tauri_plugin_dialog::{MessageDialogButtons, MessageDialogKind};
                    handle
                        .dialog()
                        .message(message)
                        .title("CurveVision could not start")
                        .kind(MessageDialogKind::Error)
                        .buttons(MessageDialogButtons::Ok)
                        .blocking_show();
                    handle.exit(1);
                    return Ok(());
                }
            };

            // Where it was left: the team server chosen last time, or this computer.
            match remembered_path(&handle).and_then(|path| team::remembered(&path)) {
                Some(url) => open_team_window(&handle, &url)?,
                None => open_local_window(&handle, &server.handshake, "/")?,
            }
            app.set_menu(build_menu(&handle)?)?;
            app.manage(server);
            Ok(())
        })
        .on_menu_event(|app, event| match event.id().as_ref() {
            // The menu only signals intent; the web application owns what happens next,
            // because only it knows which task the folder should be imported into.
            "open-folder" => {
                let _ = app.emit_to(LOCAL_WINDOW, "menu:open-folder", ());
            }
            // The local window's Team server page asks for the address and checks it.
            "team-server" => {
                if app.get_webview_window(LOCAL_WINDOW).is_some() {
                    let _ = app.emit_to(LOCAL_WINDOW, "menu:team-server", ());
                } else {
                    let _ = switch_to_local(app, "/team-server");
                }
            }
            "use-this-computer" => {
                if let Some(path) = remembered_path(app) {
                    let _ = team::remember(&path, None);
                }
                let _ = switch_to_local(app, "/");
            }
            "check-for-updates" => {
                let app = app.clone();
                tauri::async_runtime::spawn(check_from_menu(app));
            }
            "show-data-dir" => {
                if let Some(server) = app.try_state::<Arc<Sidecar>>() {
                    use tauri_plugin_opener::OpenerExt;
                    let _ = app
                        .opener()
                        .open_path(server.handshake.data_dir.clone(), None::<&str>);
                }
            }
            _ => {}
        })
        .build(tauri::generate_context!())
        .expect("failed to start CurveVision")
        .run(|app, event| {
            // Every path out of the application stops the server. A window closed, a
            // quit from the menu, a signal: all of them land here.
            if matches!(event, RunEvent::ExitRequested { .. } | RunEvent::Exit) {
                if let Some(server) = app.try_state::<Arc<Sidecar>>() {
                    server.shutdown();
                }
            }
        });
}

#[cfg(test)]
mod tests {
    use super::*;

    fn sample() -> Handshake {
        Handshake {
            url: "http://127.0.0.1:49312".into(),
            token: "cv_abc123_secret".into(),
            data_dir: "/home/someone/.local/share/CurveVision".into(),
            version: "0.1.0".into(),
        }
    }

    /// The shape injected into the page is a cross-language contract: the web application
    /// parses exactly these keys in `web/src/desktop.ts`, and its own tests use the same
    /// fixture. Renaming a field on either side alone would silently leave the desktop
    /// window showing a sign-in screen nobody can get past, so both sides pin it.
    #[test]
    fn the_injected_connection_keeps_the_keys_the_web_application_reads() {
        let value: serde_json::Value =
            serde_json::to_value(Connection::from(&sample())).expect("serialisable");
        let object = value.as_object().expect("an object");

        let mut keys: Vec<&str> = object.keys().map(String::as_str).collect();
        keys.sort_unstable();
        assert_eq!(keys, ["data_dir", "desktop", "token", "url", "version"]);

        // `desktop` must be a real boolean `true`: the web side rejects anything merely
        // truthy so a stray global cannot switch off authentication.
        assert_eq!(object["desktop"], serde_json::Value::Bool(true));
        assert_eq!(object["token"], "cv_abc123_secret");
        assert_eq!(object["url"], "http://127.0.0.1:49312");
    }

    /// The script is evaluated before any of the page's own code, so it has to be a single
    /// valid statement with the payload embedded — not a template the page completes.
    #[test]
    fn the_initialization_script_is_one_valid_statement() {
        let injected = serde_json::to_string(&Connection::from(&sample())).expect("serialisable");
        let script = team::initialization_script("http://127.0.0.1:49312", &injected);

        assert!(script.starts_with("if (window.location.origin === \"http://127.0.0.1:49312\") {"));
        assert!(script.contains("window.__CURVEVISION__ = {"));
        assert!(script.ends_with("}; }"));
        assert!(!script.contains('\n'));
        // Round-trips, so the page receives what the shell meant to send.
        let parsed: serde_json::Value = serde_json::from_str(&injected).expect("valid JSON");
        assert_eq!(parsed["token"], "cv_abc123_secret");
    }
}
