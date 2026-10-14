//! Updating the application from a signed release feed.
//!
//! Tauri's updater fetches a feed (`latest.json`), compares its version with this one, and
//! installs the download only when its minisign signature verifies against the public key
//! built into the application — the feed itself is not trusted, the signature is.
//!
//! **Where the key and the feed come from.** Neither is in the repository: the key pair is the
//! maintainers', and a release build is given the public half and the feed's URL through
//! `CURVEVISION_UPDATER_PUBKEY` and `CURVEVISION_UPDATE_URL` at compile time
//! (`.github/workflows/desktop.yml`, from repository secrets). A build without them — every
//! build made from a checkout — says updates are not configured rather than checking
//! somewhere it should not. A debug build also reads both at run time, so the WebDriver
//! harness can point a development build at a feed and a key of its own; a release build
//! never does, so its feed cannot be redirected from the environment.

use serde::Serialize;
use tauri::{AppHandle, Runtime, Url};
use tauri_plugin_updater::UpdaterExt;

/// The release public key and feed this build was given, if any.
const BUILT_PUBKEY: Option<&str> = option_env!("CURVEVISION_UPDATER_PUBKEY");
const BUILT_FEED: Option<&str> = option_env!("CURVEVISION_UPDATE_URL");

/// What the application knows about updates, for the page to show.
#[derive(Debug, Clone, Serialize, PartialEq, Eq)]
pub struct UpdateStatus {
    /// Whether this build can check for updates at all.
    pub configured: bool,
    /// The running version.
    pub current: String,
    /// A newer version the feed offers, if there is one.
    pub available: Option<String>,
    /// That version's release notes, as the feed gives them.
    pub notes: Option<String>,
}

/// The key and feed to use: the built ones, or in a debug build the environment's first.
///
/// Pure, so each way it can resolve is tested without a build.
pub fn resolve(
    built_pubkey: Option<&str>,
    built_feed: Option<&str>,
    runtime_pubkey: Option<String>,
    runtime_feed: Option<String>,
    debug: bool,
) -> Option<(String, Url)> {
    let pick = |runtime: Option<String>, built: Option<&str>| -> Option<String> {
        let runtime = if debug { runtime } else { None };
        runtime
            .or_else(|| built.map(str::to_owned))
            .map(|value| value.trim().to_owned())
            .filter(|value| !value.is_empty())
    };
    let pubkey = pick(runtime_pubkey, built_pubkey)?;
    let feed = Url::parse(&pick(runtime_feed, built_feed)?).ok()?;
    Some((pubkey, feed))
}

fn configured() -> Option<(String, Url)> {
    resolve(
        BUILT_PUBKEY,
        BUILT_FEED,
        std::env::var("CURVEVISION_UPDATER_PUBKEY").ok(),
        std::env::var("CURVEVISION_UPDATE_URL").ok(),
        cfg!(debug_assertions),
    )
}

fn updater<R: Runtime>(
    app: &AppHandle<R>,
) -> Result<Option<tauri_plugin_updater::Updater>, String> {
    let Some((pubkey, feed)) = configured() else {
        return Ok(None);
    };
    app.updater_builder()
        .pubkey(pubkey)
        .endpoints(vec![feed])
        .map_err(|error| error.to_string())?
        .build()
        .map(Some)
        .map_err(|error| error.to_string())
}

/// Ask the feed whether there is a newer version.
pub async fn check<R: Runtime>(app: &AppHandle<R>) -> Result<UpdateStatus, String> {
    let current = app.package_info().version.to_string();
    let Some(updater) = updater(app)? else {
        return Ok(UpdateStatus {
            configured: false,
            current,
            available: None,
            notes: None,
        });
    };
    let update = updater.check().await.map_err(|error| error.to_string())?;
    Ok(UpdateStatus {
        configured: true,
        current,
        available: update.as_ref().map(|u| u.version.clone()),
        notes: update.and_then(|u| u.body),
    })
}

/// Download the newer version, verify its signature, install it and restart.
///
/// Returns only on failure: an update that installed restarts the application.
pub async fn install<R: Runtime>(app: &AppHandle<R>) -> Result<(), String> {
    let Some(updater) = updater(app)? else {
        return Err("Updates are not configured for this build.".into());
    };
    let Some(update) = updater.check().await.map_err(|error| error.to_string())? else {
        return Err("There is no newer version to install.".into());
    };
    update
        .download_and_install(|_, _| {}, || {})
        .await
        .map_err(|error| format!("The update was not installed: {error}"))?;
    app.restart();
}

#[cfg(test)]
mod tests {
    use super::*;

    const FEED: &str = "https://releases.example.com/latest.json";

    #[test]
    fn a_build_given_a_key_and_a_feed_checks_there() {
        let (key, feed) = resolve(Some("KEY"), Some(FEED), None, None, false).unwrap();
        assert_eq!((key.as_str(), feed.as_str()), ("KEY", FEED));
    }

    #[test]
    fn a_build_given_neither_is_not_configured() {
        assert_eq!(resolve(None, None, None, None, false), None);
        assert_eq!(resolve(Some("KEY"), None, None, None, false), None);
        assert_eq!(resolve(Some("  "), Some(FEED), None, None, false), None);
        assert_eq!(
            resolve(Some("KEY"), Some("not a url"), None, None, false),
            None
        );
    }

    #[test]
    fn a_release_build_cannot_be_pointed_elsewhere_from_the_environment() {
        let elsewhere = Some("http://127.0.0.1:9/latest.json".to_owned());
        let (key, feed) = resolve(
            Some("KEY"),
            Some(FEED),
            Some("OTHER".into()),
            elsewhere.clone(),
            false,
        )
        .unwrap();
        assert_eq!((key.as_str(), feed.as_str()), ("KEY", FEED));
        assert_eq!(
            resolve(None, None, Some("OTHER".into()), elsewhere, false),
            None
        );
    }

    /// A key pair and signature made by `scripts/minisign.py`, which the WebDriver harness
    /// uses to sign updates. Verified as the updater verifies a download (base64 text, then
    /// minisign), so a refusal the harness sees is the content's, not a malformed signature.
    #[test]
    fn a_python_made_signature_verifies() {
        use base64::Engine;
        use minisign_verify::{PublicKey, Signature};

        const PUBKEY: &str = "dW50cnVzdGVkIGNvbW1lbnQ6IG1pbmlzaWduIHB1YmxpYyBrZXk6IEVGQThCREM3REVBMUFCRjMKUldUenE2SGV4NzJvNzMwMGVDb3FhVWhOQnA2OVJoZUhiZUxoZDc1eGFoeHhNang1Qk1iYVY3OUIK";
        const SIGNATURE: &str = "dW50cnVzdGVkIGNvbW1lbnQ6IHNpZ25hdHVyZSBmcm9tIGEgQ3VydmVWaXNpb24gdGVzdCBrZXkKUlVUenE2SGV4NzJvNytLcitGZEt3Ui81OEdMYzJreE9jMmJ2Tnh1UDBhSkFFZTFmQ1VyMTFVT2EzWkgvYzZxcFVzd0o4UVd4bFlrYXRyYXJPOGF1cGlhUFVpYWtwVmYzNmdRPQp0cnVzdGVkIGNvbW1lbnQ6IHRpbWVzdGFtcDowCWZpbGU6dXBkYXRlClJjeDB2WWx5VjViWHNxcjFaMVhXNm5mK1ZYeFdWUS9Ba2s5ejRDbzNvRUtCQmx6L29hYU4rbEpXaDlKREN4QmNMNy9CRjNvTmFCTDIrQ1JJb01jN0FBPT0K";
        const DATA: &[u8] = b"CurveVision update test vector";

        let text = |b64: &str| {
            String::from_utf8(
                base64::engine::general_purpose::STANDARD
                    .decode(b64)
                    .unwrap(),
            )
            .unwrap()
        };
        let key = PublicKey::decode(&text(PUBKEY)).unwrap();
        let signature = Signature::decode(&text(SIGNATURE)).unwrap();
        key.verify(DATA, &signature, true).unwrap();
        // ...and the same signature does not cover anything else.
        assert!(key.verify(b"something else", &signature, true).is_err());
    }

    #[test]
    fn a_debug_build_takes_the_environment_first() {
        let (key, feed) = resolve(
            Some("KEY"),
            Some(FEED),
            Some("TEST".into()),
            Some("http://127.0.0.1:9/latest.json".into()),
            true,
        )
        .unwrap();
        assert_eq!(
            (key.as_str(), feed.as_str()),
            ("TEST", "http://127.0.0.1:9/latest.json")
        );
    }
}
