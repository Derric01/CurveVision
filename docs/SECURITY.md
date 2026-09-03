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
  a short-lived presigned URL.
* Errors are RFC 9457 problem documents. Unexpected exceptions return a generic message;
  the detail goes to the structured log, not to the caller.

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

* **Rate limiting is per-process** and in-memory. It protects one instance from a runaway
  client; a multi-replica deployment needs a limit at the ingress. A Redis-backed limiter is
  planned.
* **No content scanning by default.** A `ContentScanner` hook exists; wiring it to ClamAV or
  a vendor scanner is planned and currently unimplemented.
* **No OIDC/SAML.** The `AuthBackend` seam exists; the implementation does not.
* **No audit-log tamper protection.** Audit events live in the same database as everything
  else; an attacker with database write access can alter them.
* **CurveVision is young.** It has not had an external security audit. Weigh that when
  deciding what data to put in it.
