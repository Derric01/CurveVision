# Security

## Reporting a vulnerability

**Do not open a public issue for a security vulnerability.**

Report it privately through
[GitHub's private vulnerability reporting](https://github.com/Derric01/CurveVision/security/advisories/new)
(Security → Report a vulnerability). If that is unavailable, contact a maintainer directly
and ask for a private channel before sharing details.

Please include:

* what you found and where,
* how to reproduce it,
* what an attacker could achieve, and
* the version or commit you tested.

**What to expect.** CurveVision is a young project maintained by volunteers. We will
acknowledge a report as promptly as we can and keep you updated on the fix. We will credit
you in the advisory unless you would rather stay anonymous. We do not run a bug bounty.

Please give us a reasonable opportunity to ship a fix before disclosing publicly.

## What is in scope

Anything in this repository: the API server, the worker, the web application, the SDK and
CLI, and the deployment configuration under `deploy/`.

Particularly interesting to us:

* authentication and session handling,
* the authorization policy (`server/curvevision/policy/`),
* cross-tenant data access — one organization reading another's projects, tasks or media,
* file upload handling and path traversal,
* SSRF through the model-inference or webhook URLs,
* anything that lets an annotator read or write a job they are not entitled to.

## Out of scope

* Vulnerabilities in third-party dependencies with no CurveVision-specific exploit path —
  report those upstream (we will happily help).
* Findings that require an already-compromised administrator account.
* Missing hardening headers on endpoints that serve no HTML.
* Denial of service through resource exhaustion on an instance with no rate limits
  configured — see the operator guidance below.
* Reports from automated scanners with no demonstrated impact.

## How CurveVision is built to be secure

Stating the design so you know what to test *against*, and so operators know what they get.

### Authentication

* Passwords are hashed with **Argon2id** (`argon2-cffi`), the PHC winner and current OWASP
  recommendation. Hashes are transparently upgraded on login when parameters change.
* Sessions are short-lived JWT access tokens plus **rotating** refresh tokens, stored
  server-side as hashes so they can be revoked. A refresh token is single-use: replaying
  one fails.
* API tokens are `cv_<public_id>_<secret>`. Only a SHA-256 hash of the secret is stored and
  the plaintext is shown exactly once. Comparison is constant-time.
* Changing a password revokes every other session.
* Failed logins return one indistinguishable error for "no such user" and "wrong password",
  so the endpoint is not a user-enumeration oracle.

### Authorization

* Every rule lives in one file, `server/curvevision/policy/engine.py`, as a declarative
  table. There is no permission logic in route handlers.
* `can()` is a pure function of the principal and a pre-loaded resource context, which is
  why it can be — and is — exhaustively unit-tested.
* Resources outside the caller's organizations return **404, not 403**, so their existence
  is not observable.
* List endpoints filter by membership **in the query**, so an invisible row never enters a
  result set or a pagination count.
* An id inside a request body is checked against the workspace the request acts in, not
  only the id in the path. Iteration 96 found one that was not: a webhook took any
  `project_id`, so a maintainer of one workspace could subscribe to another workspace's
  project and receive its events. It could not be exploited in practice before then, only
  because no event was being sent at all. Creation now refuses it, and deliveries are matched
  on the workspace too, so a row written earlier stays inert.

### Input and file handling

* Every request body is validated by Pydantic v2; unknown fields are rejected rather than
  ignored.
* Uploads are checked by extension **and** magic bytes, and a file whose content
  contradicts its extension is refused.
* Geometry is validated against frame bounds and per-shape minimum vertex counts.
* Archive members are validated on open: a path containing `..`, a leading `/`, or an
  absolute path is rejected before any importer sees it.
* `LocalStorage` resolves every key and refuses anything outside the storage root.

### Transport and headers

* `X-Content-Type-Options`, `X-Frame-Options: DENY`, a restrictive `Content-Security-Policy`
  on the JSON API, `Referrer-Policy`, and `Permissions-Policy` on every response.
* CORS uses an explicit allow-list — never `*`, because the API accepts credentials.
* Media is never served from a public bucket. It goes through an authenticated endpoint or
  a short-lived presigned URL — the latter only when the operator names an address browsers
  can reach the store at (`s3_public_endpoint_url`).
* Errors are RFC 9457 problem documents. Unexpected exceptions return a generic message;
  the detail goes to the structured log, not to the caller.

### The desktop application

* The local server binds to loopback only, and authenticates its window with a token minted
  at each launch; the previous launch's token is revoked.
* The routes that read the local disk (`/tasks/{id}/local-import`) or fetch an address a
  client names (`/local/team-server-check`) exist only in local mode. On a shared server
  they return 404: there, the first would be arbitrary file disclosure and the second a
  server-side request forgery.
* The shell's commands (native dialogs, opening the data folder, switching servers) are
  granted to the local window only (`desktop/shell/src-tauri/capabilities/default.json`),
  and Tauri refuses any command from a remote origin no capability names.
* **A team server is shown in a window of its own** (iteration 85), with no access to the
  shell and no initialization script, so none of this computer's credentials reach it. The
  local window's script, which carries the token, also checks the page's origin before
  handing it over, in case that window is ever navigated elsewhere.
* The shell stores the chosen team server's address only — origin, never a user name or
  password — in the application's config folder.

### What you download

* **Built by the repository, not by a person.** Installers and images are built from a
  version tag by [`release.yml`](../.github/workflows/release.yml) on GitHub's runners,
  and published only after a maintainer reads the draft ([RELEASING.md](./RELEASING.md)).
* **Checkable.** Each file is listed in `SHA256SUMS` and carries a signed build-provenance
  attestation naming the commit and workflow run that built it:
  `gh attestation verify <file> --repo Derric01/CurveVision`.
* **Scanned.** Every file is scanned with ClamAV before the release is drafted; a detection
  stops the release.
* **Pinned.** What a release installs is `server/constraints.txt` — the versions CI tested
  — not whatever a package index served that day. CI fails if a declared dependency has no
  pinned version.
* **Not yet code-signed** — see *Known limitations*.

### Auditing

`AuditEvent` records security-relevant actions — authentication, permission changes, token
creation and revocation, exports and deletions — with actor, IP address and target.

## Operator responsibilities

CurveVision cannot enforce these for you:

1. **Set `CURVEVISION_SECRET_KEY`.** The server refuses to start in production without it.
2. **Terminate TLS.** Compose ships plain HTTP behind nginx; put it behind a TLS-terminating
   proxy.
3. **Change every default password** in `.env.example` before exposing the instance.
4. **Restrict `/metrics`.** The shipped nginx config allows only private networks.
5. **Set `CURVEVISION_ALLOW_REGISTRATION=false`** on a private instance once your accounts
   exist.
6. **Review model and webhook URLs.** Both are operator-supplied and are fetched by the
   server. On a network where internal services are reachable, treat the ability to register
   them as equivalent to SSRF and restrict who holds `maintainer`.
7. **Back up PostgreSQL and object storage together.** An annotation row without its media
   is not a usable dataset.

## Known limitations

Stated plainly, because a security document that only lists strengths is not useful:

* **The installers are not code-signed by a publisher.** That needs an Apple Developer ID
  (with notarisation) and a Windows code-signing certificate, which the project does not
  have yet. The Mac app is signed ad hoc, which seals its contents (macOS on Apple Silicon
  runs nothing unsigned) but names no publisher. Windows SmartScreen and macOS Gatekeeper
  therefore warn on first run. Checksums and
  provenance attestations (*What you download*, above) are how a download is verified
  until then; they say who built it, which a signature would also say, but the operating
  system does not check them for you.
* **Rate limiting is per-process unless Redis backs it.** The default keeps counts in each
  process; a server run as several instances should set `rate_limit_backend = "redis"`
  ([API.md](./API.md#rate-limiting)), which shares one limit between them. It **fails
  open**: while Redis is unreachable, nothing is rate-limited (and the outage is logged).
* **No content scanning by default.** ClamAV scanning is built in and off by default
  (`content_scanner = "clamd"`, [OPERATIONS.md](./OPERATIONS.md#scanning-uploads-for-malware));
  it fails closed. Another scanner is one class implementing `ContentScanner` in
  `media/scanning.py`.
* **Single sign-on: OpenID Connect and SAML 2.0** ([OPERATIONS.md](./OPERATIONS.md#single-sign-on)).
  An account is linked by the provider's issuer and subject (`services/external_identity.py`,
  one set of rules for both); a first sign-in links to an existing account only through an
  email the provider vouches for. SAML responses are validated by python3-saml (signature
  over the assertion, issuer, audience, destination, recipient, validity window); the
  request ID is an HMAC over the hash of a verifier the starting browser tab keeps, so a
  response captured or a login code leaked signs nobody in without that tab, and an
  unsolicited response is refused. Single logout is not implemented.
* **No audit-log tamper protection.** Audit events live in the same database as everything
  else; an attacker with database write access can alter them.
* **CurveVision is young.** It has not had an external security audit. Weigh that when
  deciding what data to put in it.
