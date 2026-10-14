//! Pointing the desktop application at a team server.
//!
//! The window normally shows this computer's own server. A person whose team runs a shared
//! one can switch to it (File ▸ Connect to a Team Server…), and back (File ▸ Use This
//! Computer). The choice is remembered, so the application opens where it was left.
//!
//! Two rules keep this from weakening the desktop application's security:
//!
//! 1. **A team server's pages get a window of their own**, labelled [`TEAM_WINDOW`]. The
//!    capability file grants the shell's commands to the local window only, and Tauri
//!    refuses every command from a remote origin no capability names — so a team server's
//!    pages cannot open dialogs, read paths or ask for anything else.
//! 2. **This computer's credential is only ever given to this computer's server.** The team
//!    window has no initialization script at all, and the local window's script checks the
//!    page's origin before it hands anything over, in case the local window is ever
//!    navigated somewhere else.

use std::fs;
use std::io;
use std::path::Path;

use serde::{Deserialize, Serialize};
use tauri::Url;

/// The window showing this computer's server.
pub const LOCAL_WINDOW: &str = "main";
/// The window showing a team server.
pub const TEAM_WINDOW: &str = "team";

/// What is remembered between launches, in the application's config folder.
#[derive(Debug, Default, Deserialize, Serialize, PartialEq)]
struct Remembered {
    /// The team server the window last showed, or `None` for this computer.
    team_server: Option<String>,
}

/// The origin of a team server, from what the web application sends.
///
/// The web application has already checked the address with the server itself; this is
/// the shell refusing anything it would be unsafe to open or to store, whatever the page
/// asks: only `http` and `https`, with a host, and no user name or password (which would be
/// stored in plain text). A path is dropped — the application is served from the root.
pub fn team_server_url(address: &str) -> Result<Url, String> {
    let url =
        Url::parse(address.trim()).map_err(|_| format!("“{address}” is not a web address."))?;
    if !matches!(url.scheme(), "http" | "https") {
        return Err(format!("“{address}” is not a web address."));
    }
    if url.host_str().map_or(true, str::is_empty) {
        return Err(format!("“{address}” does not name a server."));
    }
    if !url.username().is_empty() || url.password().is_some() {
        return Err(
            "An address with a user name or password in it cannot be stored safely.".into(),
        );
    }
    Url::parse(&url.origin().ascii_serialization()).map_err(|error| error.to_string())
}

/// The team server to open at launch, if one was chosen last time.
///
/// Anything unreadable — a missing file, a damaged one, an address that would no longer be
/// accepted — means this computer, which always works.
pub fn remembered(path: &Path) -> Option<Url> {
    let text = fs::read_to_string(path).ok()?;
    let remembered: Remembered = serde_json::from_str(&text).ok()?;
    team_server_url(&remembered.team_server?).ok()
}

/// Remember `team_server` (or this computer, for `None`) for the next launch.
///
/// Written to a temporary file and renamed into place, so a crash mid-write leaves the
/// previous choice rather than a half-written file.
pub fn remember(path: &Path, team_server: Option<&Url>) -> io::Result<()> {
    if let Some(folder) = path.parent() {
        fs::create_dir_all(folder)?;
    }
    let remembered = Remembered {
        team_server: team_server.map(|url| url.origin().ascii_serialization()),
    };
    let text = serde_json::to_string_pretty(&remembered).map_err(io::Error::other)?;
    let partial = path.with_extension("json.partial");
    fs::write(&partial, text)?;
    fs::rename(&partial, path)
}

/// The local window's initialization script: the connection, handed over only to a page on
/// this computer's server.
///
/// It runs before the page's own scripts on every page load in that window, wherever the
/// page came from, so it checks the origin rather than trusting the window to stay put.
pub fn initialization_script(local_origin: &str, connection_json: &str) -> String {
    let origin = serde_json::to_string(local_origin).unwrap_or_else(|_| "\"\"".to_string());
    format!("if (window.location.origin === {origin}) {{ window.__CURVEVISION__ = {connection_json}; }}")
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn a_team_server_is_remembered_by_its_origin() {
        let url = team_server_url("https://cv.example.com/projects/42?x=1").unwrap();
        assert_eq!(url.as_str(), "https://cv.example.com/");
        assert_eq!(
            team_server_url("http://10.0.0.5:8080").unwrap().as_str(),
            "http://10.0.0.5:8080/"
        );
    }

    #[test]
    fn what_is_unsafe_to_open_or_store_is_refused() {
        for address in [
            "",
            "cv.example.com",
            "file:///etc/passwd",
            "javascript:alert(1)",
            "ftp://cv.example.com",
            "data:text/html,hello",
            "https://someone:secret@cv.example.com",
            "https://someone@cv.example.com",
        ] {
            assert!(team_server_url(address).is_err(), "{address} was accepted");
        }
    }

    #[test]
    fn the_choice_survives_a_restart_and_can_be_undone() {
        let folder = std::env::temp_dir().join(format!("cv-team-{}", std::process::id()));
        let path = folder.join("connection.json");
        assert_eq!(remembered(&path), None, "nothing chosen yet: this computer");

        let url = team_server_url("https://cv.example.com").unwrap();
        remember(&path, Some(&url)).unwrap();
        assert_eq!(remembered(&path), Some(url));

        remember(&path, None).unwrap();
        assert_eq!(remembered(&path), None);
        let _ = fs::remove_dir_all(folder);
    }

    #[test]
    fn a_damaged_or_tampered_file_means_this_computer() {
        let folder = std::env::temp_dir().join(format!("cv-team-bad-{}", std::process::id()));
        fs::create_dir_all(&folder).unwrap();
        let path = folder.join("connection.json");
        for text in [
            "{not json",
            r#"{"team_server": "file:///etc"}"#,
            r#"{"team_server": 7}"#,
        ] {
            fs::write(&path, text).unwrap();
            assert_eq!(remembered(&path), None, "{text}");
        }
        let _ = fs::remove_dir_all(folder);
    }

    /// The grants, as Tauri reads them. A command missing here is refused in the built
    /// application — which is how the folder pickers went unusable for months — and a
    /// second window named here would hand a team server's pages the shell.
    #[test]
    fn the_shell_is_granted_to_the_local_window_and_only_to_it() {
        let capability: serde_json::Value =
            serde_json::from_str(include_str!("../capabilities/default.json")).unwrap();
        assert_eq!(capability["windows"], serde_json::json!([LOCAL_WINDOW]));
        let granted: Vec<&str> = capability["permissions"]
            .as_array()
            .unwrap()
            .iter()
            .filter_map(|permission| permission.as_str())
            .collect();
        for command in [
            "connection",
            "choose_folder",
            "choose_files",
            "connect_to_team_server",
        ] {
            let permission = format!("allow-{}", command.replace('_', "-"));
            assert!(
                granted.contains(&permission.as_str()),
                "{permission} is not granted"
            );
        }
    }

    #[test]
    fn the_credential_is_handed_only_to_this_computers_server() {
        let script = initialization_script("http://127.0.0.1:49312", r#"{"token":"cv_x"}"#);
        assert_eq!(
            script,
            r#"if (window.location.origin === "http://127.0.0.1:49312") { window.__CURVEVISION__ = {"token":"cv_x"}; }"#
        );
        assert!(!script.contains('\n'));
    }
}
