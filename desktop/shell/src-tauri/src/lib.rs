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
//! Everything else — the editor, the API, the exporters — is the same code the server
//! deployment runs. This is deliberately not a second implementation of the product.

mod sidecar;

use std::path::PathBuf;
use std::sync::Arc;

use serde::Serialize;
use tauri::menu::{AboutMetadata, Menu, MenuItem, PredefinedMenuItem, Submenu};
use tauri::{AppHandle, Emitter, Manager, RunEvent, State, WebviewUrl, WebviewWindowBuilder};
use tauri_plugin_dialog::DialogExt;

use sidecar::{Handshake, Sidecar};

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

/// The window is created here rather than declared in `tauri.conf.json` because it cannot
/// exist until the server does: its URL contains a port the OS chose a moment ago, and its
/// initialization script contains a token that was minted at the same time.
fn open_main_window(app: &AppHandle, handshake: &Handshake) -> tauri::Result<()> {
    let connection = Connection::from(handshake);
    let injected = serde_json::to_string(&connection).unwrap_or_else(|_| "null".to_string());

    let url = handshake
        .url
        .parse()
        .map_err(|_| tauri::Error::WebviewNotFound)?;

    WebviewWindowBuilder::new(app, "main", WebviewUrl::External(url))
        .title("CurveVision")
        .inner_size(1440.0, 900.0)
        .min_inner_size(960.0, 600.0)
        .resizable(true)
        .center()
        // Runs before any of the page's own scripts, so the application never renders a
        // signed-out state it would immediately have to replace.
        .initialization_script(format!("window.__CURVEVISION__ = {injected};"))
        .build()?;
    Ok(())
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

    let file = Submenu::with_items(
        app,
        "File",
        true,
        &[
            &open_folder,
            &show_data,
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
        &[&PredefinedMenuItem::about(
            app,
            Some("About CurveVision"),
            Some(AboutMetadata {
                name: Some("CurveVision".into()),
                version: Some(env!("CARGO_PKG_VERSION").into()),
                license: Some("MIT".into()),
                website: Some("https://github.com/Derric01/CurveVision".into()),
                ..Default::default()
            }),
        )?],
    )?;

    Menu::with_items(app, &[&file, &edit, &help])
}

#[cfg_attr(mobile, tauri::mobile_entry_point)]
pub fn run() {
    tauri::Builder::default()
        .plugin(tauri_plugin_dialog::init())
        .plugin(tauri_plugin_opener::init())
        .invoke_handler(tauri::generate_handler![
            connection,
            choose_folder,
            choose_files
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

            open_main_window(&handle, &server.handshake)?;
            app.set_menu(build_menu(&handle)?)?;
            app.manage(server);
            Ok(())
        })
        .on_menu_event(|app, event| match event.id().as_ref() {
            // The menu only signals intent; the web application owns what happens next,
            // because only it knows which task the folder should be imported into.
            "open-folder" => {
                let _ = app.emit("menu:open-folder", ());
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
