# Releasing CurveVision

A release is a version tag. Everything a person downloads is built from it by
[`release.yml`](../.github/workflows/release.yml), on GitHub's runners, and lands in a
**draft** release that a maintainer reads and publishes. Nothing is public until then.

## Making one

1. **Every version matches.** `python scripts/release_check.py v1.0.0` — the release stops
   at this step otherwise. To move to a new version, change each file it names (server,
   both SDKs, web, the desktop shell, the Helm chart), run `./scripts/check.sh`, and merge.
2. **Tag `main`** once CI is green on it:

   ```bash
   git checkout main && git pull
   git tag -a v1.0.0 -m "CurveVision 1.0.0"
   git push origin v1.0.0
   ```

3. **Wait for the workflow** (about 40 minutes; the installers build on three platforms and
   the real application is driven over WebDriver before anything is published).
4. **Read the draft** under *Releases*: the files, `SHA256SUMS`, and the notes. Download one
   installer and check it the way a user would (below). Then **Publish**.

The draft carries, from that tag and nothing else:

| What | Built by | Checked by |
| --- | --- | --- |
| Windows `.exe`, macOS `.dmg`, Linux `.deb` / `.rpm` / `.AppImage` | `desktop.yml`, each on its own platform | the `.deb` installed and its server used; the real app driven over WebDriver |
| `ghcr.io/<owner>/curvevision-server` and `-web` at the version and `latest` | `release.yml` | built from the same Dockerfiles CI builds |
| `SHA256SUMS` | `release.yml` | — |
| `latest.json`, the desktop update feed | `scripts/updater_manifest.py` | only when the updater's signing key is configured (`desktop/README.md`) |

Every file, and both images, carries a signed build-provenance attestation, and every file
is scanned with ClamAV first: a detection stops the release.

## What a user checks

```bash
sha256sum --check --ignore-missing SHA256SUMS
gh attestation verify <file> --repo Derric01/CurveVision
gh attestation verify oci://ghcr.io/derric01/curvevision-server:1.0.0 --repo Derric01/CurveVision
```

The first says the bytes are the ones published; the second says GitHub's own runner built
them from this repository's tagged source, in this workflow.

## Not done yet, and why

* **Code signing.** The installers are unsigned until the project has an Apple Developer ID
  (and notarisation) and a Windows code-signing certificate. Windows SmartScreen and macOS
  Gatekeeper warn until then; the release notes say what to click.
* **The updater's signing key.** Without `TAURI_SIGNING_PRIVATE_KEY` and its public half in
  the repository's secrets, the installers cannot update themselves (`desktop/README.md`,
  *Releasing an update*).

## Dependencies

What a release installs is pinned in `server/constraints.txt` — the versions CI tested.
To take newer ones, run `./scripts/update_constraints.sh`, then `./scripts/check.sh`, and
merge that as its own change before tagging.
