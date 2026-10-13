//! Supervising the local CurveVision server.
//!
//! The shell owns exactly one child process: the packaged server from
//! `desktop/sidecar`. It is spawned on launch, watched while the app runs, and killed
//! when the app exits — a desktop application that leaves a web server running after its
//! window closes is a bug people find out about the hard way.

use std::io::{BufRead, BufReader};
use std::path::PathBuf;
use std::process::{Child, ChildStdout, Command, Stdio};
use std::sync::Mutex;
use std::time::{Duration, Instant};

use serde::{Deserialize, Serialize};

/// The line the server prints once it is listening. Everything before it is log output.
const HANDSHAKE_PREFIX: &str = "CURVEVISION_READY ";

/// How long to wait for that line. Generous: a first launch migrates the database, and a
/// cold start on a slow disk with an antivirus scanner watching is not fast.
const HANDSHAKE_TIMEOUT: Duration = Duration::from_secs(90);

/// Where the server is listening and how to authenticate with it.
///
/// Handed to the web application at load time, so it never shows a sign-in screen: on a
/// single-user machine there is nobody else to be.
#[derive(Clone, Debug, Deserialize, Serialize)]
pub struct Handshake {
    pub url: String,
    pub token: String,
    pub data_dir: String,
    pub version: String,
}

/// A running server, killed when this value is dropped.
#[derive(Debug)]
pub struct Sidecar {
    child: Mutex<Option<Child>>,
    pub handshake: Handshake,
}

impl Sidecar {
    /// Stop the server. Idempotent, so the exit handler and `Drop` can both call it.
    pub fn shutdown(&self) {
        let mut guard = match self.child.lock() {
            Ok(guard) => guard,
            Err(poisoned) => poisoned.into_inner(),
        };
        if let Some(mut child) = guard.take() {
            stop(&mut child, SHUTDOWN_GRACE);
        }
    }
}

/// How long the server gets to stop by itself before it is killed.
const SHUTDOWN_GRACE: Duration = Duration::from_secs(10);

/// Stop the server: end-of-file on its standard input first, which it answers by shutting
/// down cleanly, then a kill if it has not gone within `grace`.
///
/// The order matters most on Windows. A one-file PyInstaller executable is a bootloader
/// with the real server as its child, and killing the bootloader leaves that child running
/// with the database open; end-of-file reaches the child, because it reads the same pipe.
fn stop(child: &mut Child, grace: Duration) {
    drop(child.stdin.take());
    let deadline = Instant::now() + grace;
    while Instant::now() < deadline {
        match child.try_wait() {
            Ok(Some(_)) => return,
            Ok(None) => std::thread::sleep(Duration::from_millis(50)),
            Err(_) => break,
        }
    }
    let _ = child.kill();
    let _ = child.wait();
}

impl Drop for Sidecar {
    fn drop(&mut self) {
        self.shutdown();
    }
}

/// Start the server and wait until it says where it is listening.
pub fn start(executable: &PathBuf) -> Result<Sidecar, String> {
    if !executable.is_file() {
        return Err(format!(
            "The CurveVision server is missing from this installation.\n\nExpected it at:\n{}",
            executable.display()
        ));
    }

    let mut command = Command::new(executable);
    command
        // The server watches this pipe. If the shell is force-quit or crashes, the OS
        // closes our end, the server sees end-of-file and stops -- so no window can ever
        // be closed while an HTTP server keeps running behind it.
        .arg("--exit-with-parent")
        .stdin(Stdio::piped())
        .stdout(Stdio::piped())
        .stderr(Stdio::piped());
    hide_console_window(&mut command);

    let mut child = command
        .spawn()
        .map_err(|error| format!("Could not start the CurveVision server: {error}"))?;

    // Nothing reads standard error, so it has to be drained: a full pipe buffer would
    // block the server mid-request, and the symptom would be an application that freezes
    // for no visible reason.
    if let Some(stderr) = child.stderr.take() {
        std::thread::spawn(move || {
            for line in BufReader::new(stderr).lines().map_while(Result::ok) {
                eprintln!("[server] {line}");
            }
        });
    }

    let stdout = child
        .stdout
        .take()
        .ok_or_else(|| "The CurveVision server produced no output.".to_string())?;

    match read_handshake(stdout) {
        Ok((handshake, reader)) => {
            // Whatever the server logs from here on is useful when someone reports a bug,
            // and nothing is waiting on it, so it goes to a thread.
            std::thread::spawn(move || {
                for line in reader.lines().map_while(Result::ok) {
                    eprintln!("[server] {line}");
                }
            });
            Ok(Sidecar {
                child: Mutex::new(Some(child)),
                handshake,
            })
        }
        Err(error) => {
            let _ = child.kill();
            let _ = child.wait();
            Err(error)
        }
    }
}

/// Read the server's output until it announces itself.
///
/// Returns the reader as well, so the caller keeps draining the pipe. Leaving it unread
/// would eventually fill the OS buffer and block the server mid-request.
fn read_handshake(stdout: ChildStdout) -> Result<(Handshake, BufReader<ChildStdout>), String> {
    let mut reader = BufReader::new(stdout);
    let deadline = Instant::now() + HANDSHAKE_TIMEOUT;
    let mut preamble: Vec<String> = Vec::new();

    while Instant::now() < deadline {
        let mut line = String::new();
        match reader.read_line(&mut line) {
            Ok(0) => break, // the process closed its output, which means it exited
            Ok(_) => {}
            Err(error) => return Err(format!("Lost contact with the server: {error}")),
        }

        if let Some(payload) = line.trim_end().strip_prefix(HANDSHAKE_PREFIX) {
            let handshake: Handshake = serde_json::from_str(payload)
                .map_err(|error| format!("The server said something unexpected: {error}"))?;
            return Ok((handshake, reader));
        }
        preamble.push(line.trim_end().to_string());
        // Keep the tail only: an unbounded log of a server that never starts helps nobody.
        if preamble.len() > 60 {
            preamble.remove(0);
        }
    }

    Err(format!(
        "The CurveVision server did not finish starting.\n\nIts last output was:\n{}",
        if preamble.is_empty() {
            "(nothing)".to_string()
        } else {
            preamble.join("\n")
        }
    ))
}

#[cfg(windows)]
fn hide_console_window(command: &mut Command) {
    use std::os::windows::process::CommandExt;
    // CREATE_NO_WINDOW: without it, launching the app flashes a console window, which
    // looks broken even though nothing is wrong.
    command.creation_flags(0x0800_0000);
}

#[cfg(not(windows))]
fn hide_console_window(_command: &mut Command) {}

#[cfg(test)]
mod tests {
    use super::*;
    use std::path::Path;

    /// The packaged server, if it has been built. `cargo test` is useful without it, so
    /// the tests that need it say so and skip.
    fn packaged_server() -> Option<PathBuf> {
        let name = if cfg!(windows) {
            "curvevision-local.exe"
        } else {
            "curvevision-local"
        };
        let path = Path::new(env!("CARGO_MANIFEST_DIR"))
            .join("../../sidecar/dist")
            .join(name);
        path.is_file().then_some(path)
    }

    #[test]
    fn a_missing_server_is_reported_in_words_a_person_can_act_on() {
        let error = start(&PathBuf::from("/nonexistent/curvevision-local")).unwrap_err();
        assert!(error.contains("missing from this installation"), "{error}");
        assert!(error.contains("/nonexistent/curvevision-local"), "{error}");
    }

    #[test]
    fn something_that_is_not_the_server_does_not_hang_forever() {
        // `true` exits immediately without saying anything. The shell must notice that
        // the pipe closed rather than sit on the handshake timeout.
        let path = PathBuf::from(if cfg!(windows) {
            "C:\\Windows\\System32\\where.exe"
        } else {
            "/bin/true"
        });
        if !path.is_file() {
            return;
        }
        let started = Instant::now();
        let error = start(&path).unwrap_err();
        assert!(error.contains("did not finish starting"), "{error}");
        assert!(
            started.elapsed() < HANDSHAKE_TIMEOUT,
            "waited for the full timeout"
        );
    }

    /// `cat` stops at end-of-file, as the server does: it must be allowed to, not killed.
    #[cfg(unix)]
    #[test]
    fn a_child_that_stops_at_end_of_file_is_not_killed() {
        let mut child = Command::new("cat")
            .stdin(Stdio::piped())
            .stdout(Stdio::null())
            .spawn()
            .expect("cat");
        stop(&mut child, Duration::from_secs(10));
        assert!(child.wait().expect("reaped").success());
    }

    /// `sleep` ignores its input: after the grace period it is killed, not waited on forever.
    #[cfg(unix)]
    #[test]
    fn a_child_that_ignores_end_of_file_is_killed_after_the_grace_period() {
        let mut child = Command::new("sleep")
            .arg("30")
            .stdin(Stdio::piped())
            .spawn()
            .expect("sleep");
        let started = Instant::now();
        stop(&mut child, Duration::from_millis(300));
        assert!(started.elapsed() < Duration::from_secs(5));
        assert!(!child.wait().expect("reaped").success());
    }

    #[test]
    fn the_server_starts_announces_itself_and_stops_when_told() {
        let Some(executable) = packaged_server() else {
            eprintln!("skipped: build the server first with desktop/sidecar/build.py");
            return;
        };

        let server = start(&executable).expect("the packaged server should start");
        assert!(server.handshake.url.starts_with("http://127.0.0.1:"));
        assert!(server.handshake.token.starts_with("cv_"));
        assert!(!server.handshake.version.is_empty());

        let pid = {
            let guard = server.child.lock().unwrap();
            guard.as_ref().expect("a running child").id()
        };

        server.shutdown();
        assert!(
            server.child.lock().unwrap().is_none(),
            "shutdown should have reaped the child"
        );
        // Idempotent: the exit handler and `Drop` both call it.
        server.shutdown();

        #[cfg(unix)]
        {
            std::thread::sleep(Duration::from_millis(500));
            assert!(
                !Path::new(&format!("/proc/{pid}")).exists(),
                "the server was left running"
            );
        }
        #[cfg(not(unix))]
        let _ = pid;
    }
}
