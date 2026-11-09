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
| Windows `.exe`, macOS `.dmg` for Apple Silicon and for Intel, Linux `.deb` / `.rpm` / `.AppImage` | `desktop.yml`, each on its own platform | the `.deb` installed and its server used; the real app driven over WebDriver |
| `ghcr.io/<owner>/curvevision-server` and `-web` at the version and `latest` | `release.yml` | built from the same Dockerfiles CI builds |
| `SHA256SUMS` | `release.yml` | — |
| `latest.json`, the desktop update feed | `scripts/updater_manifest.py` | only when the updater's signing key is configured (`desktop/README.md`) |

Every file, and both images, carries a signed build-provenance attestation, and every file
is scanned with ClamAV first: a detection stops the release.

## What a user checks

```bash
cosign verify-blob SHA256SUMS --bundle SHA256SUMS.sigstore.json --certificate-identity-regexp 'https://github.com/Derric01/CurveVision/.github/workflows/release.yml@.*' --certificate-oidc-issuer https://token.actions.githubusercontent.com
sha256sum --check --ignore-missing SHA256SUMS
gh attestation verify <file> --repo Derric01/CurveVision
cosign verify ghcr.io/derric01/curvevision-server:1.0.0 --certificate-identity-regexp 'https://github.com/Derric01/CurveVision/.github/workflows/release.yml@.*' --certificate-oidc-issuer https://token.actions.githubusercontent.com
```

The first says the checksum list was signed by this repository's release workflow (Sigstore,
keyless: no long-lived key exists to steal); the second that the bytes are the ones it lists;
the third that GitHub's own runner built them from the tagged source; the fourth that the
image is the one the release workflow pushed. Every release also carries an SPDX SBOM.

## Certificates and badges: who can get what

Done in this repository, free, nothing to apply for:

| What | How | Proves |
| --- | --- | --- |
| Build provenance (SLSA) | `actions/attest-build-provenance` on every file and image | built by GitHub from this tagged source |
| Sigstore signatures | `cosign`, keyless, in `release.yml` | the checksum list and images came from the release workflow |
| SBOM | `anchore/sbom-action`, SPDX | every component and version shipped |
| Vulnerability scanning | `security.yml`, Dependabot | no known-vulnerable dependency ships unnoticed |
| HTTPS for the website | GitHub Pages | automatic certificate on `derric01.github.io` |
| HTTPS for a team server | `deploy/compose.https.yml` (Caddy) or cert-manager on Kubernetes | a Let's Encrypt certificate, renewed automatically (`docs/OPERATIONS.md`, *HTTPS*) |

Ready, waiting on **the repository being public**:

| What | Where |
| --- | --- |
| OpenSSF Scorecard (rating and badge) | `scorecard.yml`, skipped while private |
| CodeQL code scanning | `codeql.yml`, skipped while private |
| GitHub Pages (free plan) | `pages.yml` |
| Downloads anyone can open | releases of a private repository need a GitHub login with access |

Only the maintainer can obtain, because each needs an identity, an account or a payment:

| What | Cost | Removes |
| --- | --- | --- |
| **Apple Developer Program** (Developer ID + notarisation) | US$99 a year | the macOS "could not verify" step |
| **Windows code signing**: [SignPath Foundation](https://signpath.org/) for open source, or a commercial certificate | free for qualifying OSS (application and review), otherwise paid | SmartScreen's "unrecognised app" warning |
| **Updater signing key** (`TAURI_SIGNING_PRIVATE_KEY` and the public key as repository secrets) | free; generate it on your own machine with `npx @tauri-apps/cli signer generate` | nothing visible; enables signed auto-updates |
| **OpenSSF Best Practices badge** at [bestpractices.dev](https://www.bestpractices.dev/) | free; a questionnaire about the project | shows the project meets OpenSSF's criteria |
| **Private vulnerability reporting** (Settings → Security) | free | a private channel for reports, as `docs/SECURITY.md` asks |

Never paste a private key into a chat, an issue or a commit: repository secrets only.

## Not done yet, and why

* **Code signing.** The installers are unsigned until the project has an Apple Developer ID
  (and notarisation) and a Windows code-signing certificate. Windows SmartScreen and macOS
  Gatekeeper warn until then; the release notes say what to click. The Mac app is signed
  ad hoc (`signingIdentity: "-"` in `tauri.conf.json`), which a Developer ID replaces:
  set `APPLE_SIGNING_IDENTITY` and the notarisation credentials Tauri reads, and remove it.
* **Intel Macs after August 2027.** `macos-15-intel` is GitHub's last Intel image. When it
  retires, Intel builds need a self-hosted Intel Mac or end.
* **The updater's signing key.** Without `TAURI_SIGNING_PRIVATE_KEY` and its public half in
  the repository's secrets, the installers cannot update themselves (`desktop/README.md`,
  *Releasing an update*).

## Dependencies

What a release installs is pinned in `server/constraints.txt` — the versions CI tested.
To take newer ones, run `./scripts/update_constraints.sh`, then `./scripts/check.sh`, and
merge that as its own change before tagging.
