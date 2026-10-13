#!/usr/bin/env python3
"""Single sign-on, in a real browser, against an identity provider this script runs.

    python scripts/verify_sso.py

The server shape only (the desktop application has no sign-in). The provider is a small
OpenID Connect server in this process: it publishes its configuration and keys, approves
every sign-in at once (as a provider does for someone already signed in to it), checks the
PKCE verifier and the client secret, and signs RS256 ID tokens. Claims:

1. The sign-in page offers "Sign in with Team SSO", told so by the server.
2. Pressing it goes to the provider and back, and lands signed in, on the projects page,
   as an account the first sign-in created from the provider's verified email.
3. A callback this tab did not start — a code and state from somewhere else — is refused
   in the browser, before anything is sent: login CSRF.
4. A sign-in cancelled at the provider says so.

Needs the built web application (`npm --prefix web run build`) and a Chromium.
"""

from __future__ import annotations

import base64
import hashlib
import json
import secrets
import sys
import tempfile
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlencode, urlparse

sys.path.insert(0, str(Path(__file__).parent))

from screenshot import find_chromium
from verify_workspace import free_port, start_web_server

CLIENT_ID, CLIENT_SECRET = "curvevision", "harness-secret"
PERSON = {"sub": "alice-1", "email": "alice@team.example", "email_verified": True,
          "preferred_username": "alice", "name": "Alice Example"}


def provider(port: int) -> tuple[ThreadingHTTPServer, str]:
    import jwt
    from cryptography.hazmat.primitives.asymmetric import rsa

    issuer = f"http://127.0.0.1:{port}"
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    grants: dict[str, dict] = {}

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args: object) -> None:
            pass

        def reply(self, status: int, body: dict | None = None, location: str | None = None):
            self.send_response(status)
            if location:
                self.send_header("Location", location)
            payload = json.dumps(body or {}).encode()
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(payload)))
            self.end_headers()
            self.wfile.write(payload)

        def do_GET(self) -> None:
            url = urlparse(self.path)
            query = {k: v[0] for k, v in parse_qs(url.query).items()}
            if url.path == "/.well-known/openid-configuration":
                self.reply(200, {"issuer": issuer, "authorization_endpoint": f"{issuer}/authorize",
                                 "token_endpoint": f"{issuer}/token",
                                 "jwks_uri": f"{issuer}/certs"})
            elif url.path == "/certs":
                public = jwt.algorithms.RSAAlgorithm.to_jwk(key.public_key(), as_dict=True)
                self.reply(200, {"keys": [{**public, "kid": "k1", "use": "sig"}]})
            elif url.path == "/authorize":
                code = secrets.token_urlsafe(16)
                grants[code] = {"nonce": query["nonce"], "challenge": query["code_challenge"]}
                answer = urlencode({"code": code, "state": query["state"]})
                back = f"{query['redirect_uri']}?{answer}"
                self.reply(302, location=back)
            else:
                self.reply(404)

        def do_POST(self) -> None:
            if self.path != "/token":
                self.reply(404)
                return
            expected = base64.b64encode(f"{CLIENT_ID}:{CLIENT_SECRET}".encode()).decode()
            if self.headers.get("Authorization") != f"Basic {expected}":
                self.reply(401, {"error": "invalid_client"})
                return
            length = int(self.headers.get("Content-Length", "0"))
            form = {k: v[0] for k, v in parse_qs(self.rfile.read(length).decode()).items()}
            grant = grants.pop(form.get("code", ""), None)
            digest = hashlib.sha256(form.get("code_verifier", "").encode()).digest()
            if grant is None or base64.urlsafe_b64encode(digest).rstrip(b"=").decode() != grant[
                "challenge"
            ]:
                self.reply(400, {"error": "invalid_grant"})
                return
            now = int(time.time())
            token = jwt.encode({"iss": issuer, "aud": CLIENT_ID, "iat": now, "exp": now + 300,
                                "nonce": grant["nonce"], **PERSON}, key, algorithm="RS256",
                               headers={"kid": "k1"})
            self.reply(200, {"id_token": token, "access_token": "unused"})

    server = ThreadingHTTPServer(("127.0.0.1", port), Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    return server, issuer


def main() -> int:
    from playwright.sync_api import sync_playwright

    failures: list[str] = []

    def check(condition: bool, passed: str, failed: str) -> None:
        if condition:
            print(f"  ok   {passed}")
        else:
            failures.append(failed)
            print(f"  FAIL {failed}")

    idp, issuer = provider(free_port())
    port = free_port()
    with tempfile.TemporaryDirectory(prefix="curvevision-sso-") as workdir:
        process, base = start_web_server(Path(workdir), port=port, extra_env={
            "CURVEVISION_OIDC_ENABLED": "true",
            "CURVEVISION_OIDC_ISSUER": issuer,
            "CURVEVISION_OIDC_CLIENT_ID": CLIENT_ID,
            "CURVEVISION_OIDC_CLIENT_SECRET": CLIENT_SECRET,
            "CURVEVISION_OIDC_REDIRECT_URI": f"http://127.0.0.1:{port}/auth/callback",
            "CURVEVISION_OIDC_DISPLAY_NAME": "Team SSO",
        })
        try:
            with sync_playwright() as p:
                launch: dict[str, object] = {"headless": True}
                if (chromium := find_chromium()) is not None:
                    launch["executable_path"] = chromium
                browser = p.chromium.launch(**launch)
                page = browser.new_page()
                raised: list[str] = []
                page.on("pageerror", lambda exc: raised.append(str(exc)))

                # ---------------------------------------------------- 1 and 2
                page.goto(f"{base}/login", wait_until="networkidle")
                button = page.locator("[data-sso-start]")
                check(button.count() == 1 and "Team SSO" in button.inner_text(),
                      "the sign-in page offers Sign in with Team SSO",
                      f"no SSO button (found {button.count()})")
                if button.count() == 1:
                    button.click()
                    page.wait_for_url(f"{base}/projects", timeout=30_000)
                token = page.evaluate("() => localStorage.getItem('curvevision.access')")
                who = page.evaluate(
                    """async (token) => (await fetch('/api/v1/auth/me',
                         {headers: {Authorization: `Bearer ${token}`}})).json()""", token)
                check(page.url == f"{base}/projects" and who.get("email") == PERSON["email"],
                      "it goes to the provider and back, signed in as the account it created",
                      f"ended at {page.url}, signed in as {who}")
                page.screenshot(path="/tmp/curvevision-sso.png")

                # ------------------------------------------------------------ 3
                stranger = browser.new_page()
                stranger.goto(f"{base}/auth/callback?code=stolen&state=not-this-tab",
                              wait_until="networkidle")
                said = stranger.locator("[data-sso-callback]").inner_text()
                check("not started in this browser" in said
                      and stranger.evaluate(
                          "() => localStorage.getItem('curvevision.access')") is None,
                      "a callback this tab did not start is refused, and signs nobody in",
                      f"the callback page said {said!r}")

                # ------------------------------------------------------------ 4
                stranger.goto(f"{base}/auth/callback?error=access_denied&state=x",
                              wait_until="networkidle")
                said = stranger.locator("[data-sso-callback]").inner_text()
                check("cancelled" in said, "a sign-in cancelled at the provider says so",
                      f"the callback page said {said!r}")

                check(not raised, "nothing raised in the page", f"page errors: {raised}")
                browser.close()
        finally:
            process.terminate()
            process.wait(timeout=20)
            idp.shutdown()

    print()
    if failures:
        print(f"{len(failures)} check(s) failed")
        return 1
    print("every check passed")
    return 0


if __name__ == "__main__":
    sys.exit(main())
