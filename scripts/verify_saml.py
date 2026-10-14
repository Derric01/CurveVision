#!/usr/bin/env python3
"""SAML single sign-on, in a real browser, against an identity provider this script runs.

    python scripts/verify_saml.py

The server shape only (the desktop application has no sign-in). The provider is a small
SAML identity provider in this process: it reads the AuthnRequest the browser brings, and
answers as Entra ID, Okta or AD FS do for someone already signed in to them — a page that
posts a Response, its assertion signed with the provider's key through xmlsec, to the
service provider's assertion consumer service. Claims:

1. The sign-in page offers "Sign in with Acme sign-in", told so by the server.
2. Pressing it goes to the provider, posts back to the server, returns to the web
   application and lands signed in, on the projects page, as an account the first sign-in
   created from the assertion.
3. The login code that came back, opened in another tab — one that did not start the
   sign-in and so holds no verifier — is refused there, and signs nobody in.
4. A response the server refused (here, one forged with another key) comes back as a
   refusal the callback page shows, not a sign-in.

Needs the server with the `saml` extra, the built web application
(`npm --prefix web run build`) and a Chromium.
"""

from __future__ import annotations

import base64
import contextlib
import datetime as dt
import html
import sys
import tempfile
import threading
import time
import uuid
import zlib
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

sys.path.insert(0, str(Path(__file__).parent))

from screenshot import find_chromium
from verify_workspace import free_port, start_web_server

IDP_ENTITY = "https://idp.harness.example/saml"
PERSON = {"name_id": "persistent-ines", "email": "ines@acme.example", "name": "Ines Varga"}


def keypair(name: str) -> tuple[str, str]:
    from cryptography import x509
    from cryptography.hazmat.primitives import hashes, serialization
    from cryptography.hazmat.primitives.asymmetric import rsa
    from cryptography.x509.oid import NameOID

    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    subject = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, name)])
    now = dt.datetime.now(dt.UTC)
    cert = (
        x509.CertificateBuilder().subject_name(subject).issuer_name(subject)
        .public_key(key.public_key()).serial_number(x509.random_serial_number())
        .not_valid_before(now - dt.timedelta(days=1)).not_valid_after(now + dt.timedelta(days=7))
        .sign(key, hashes.SHA256())
    )
    return (
        key.private_bytes(serialization.Encoding.PEM,
                          serialization.PrivateFormat.TraditionalOpenSSL,
                          serialization.NoEncryption()).decode(),
        cert.public_bytes(serialization.Encoding.PEM).decode(),
    )


def instant(offset: int = 0) -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(time.time() + offset))


def signed_response(request_id: str, acs: str, audience: str, key: str, cert: str) -> str:
    from onelogin.saml2.constants import OneLogin_Saml2_Constants
    from onelogin.saml2.utils import OneLogin_Saml2_Utils

    attributes = "".join(
        f'<saml:Attribute Name="{n}"><saml:AttributeValue xsi:type="xs:string">{v}'
        "</saml:AttributeValue></saml:Attribute>"
        for n, v in (("email", PERSON["email"]), ("displayName", PERSON["name"]))
    )
    assertion = (
        '<saml:Assertion xmlns:saml="urn:oasis:names:tc:SAML:2.0:assertion" '
        'xmlns:xs="http://www.w3.org/2001/XMLSchema" '
        'xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance" '
        f'ID="_a{uuid.uuid4().hex}" Version="2.0" IssueInstant="{instant()}">'
        f"<saml:Issuer>{IDP_ENTITY}</saml:Issuer><saml:Subject>"
        '<saml:NameID Format="urn:oasis:names:tc:SAML:2.0:nameid-format:persistent">'
        f'{PERSON["name_id"]}</saml:NameID>'
        '<saml:SubjectConfirmation Method="urn:oasis:names:tc:SAML:2.0:cm:bearer">'
        f'<saml:SubjectConfirmationData NotOnOrAfter="{instant(300)}" Recipient="{acs}" '
        f'InResponseTo="{request_id}"/></saml:SubjectConfirmation></saml:Subject>'
        f'<saml:Conditions NotBefore="{instant(-60)}" NotOnOrAfter="{instant(300)}">'
        f"<saml:AudienceRestriction><saml:Audience>{audience}</saml:Audience>"
        "</saml:AudienceRestriction></saml:Conditions>"
        f'<saml:AuthnStatement AuthnInstant="{instant()}" SessionIndex="_s1"><saml:AuthnContext>'
        "<saml:AuthnContextClassRef>urn:oasis:names:tc:SAML:2.0:ac:classes:"
        "PasswordProtectedTransport</saml:AuthnContextClassRef></saml:AuthnContext>"
        f"</saml:AuthnStatement><saml:AttributeStatement>{attributes}"
        "</saml:AttributeStatement></saml:Assertion>"
    )
    signed = OneLogin_Saml2_Utils.add_sign(
        assertion, key, cert,
        sign_algorithm=OneLogin_Saml2_Constants.RSA_SHA256,
        digest_algorithm=OneLogin_Saml2_Constants.SHA256,
    )
    body = (signed.decode() if isinstance(signed, bytes) else str(signed)).split("?>", 1)[-1]
    return (
        '<samlp:Response xmlns:samlp="urn:oasis:names:tc:SAML:2.0:protocol" '
        'xmlns:saml="urn:oasis:names:tc:SAML:2.0:assertion" '
        f'ID="_r{uuid.uuid4().hex}" Version="2.0" IssueInstant="{instant()}" '
        f'Destination="{acs}" InResponseTo="{request_id}">'
        f"<saml:Issuer>{IDP_ENTITY}</saml:Issuer><samlp:Status><samlp:StatusCode "
        'Value="urn:oasis:names:tc:SAML:2.0:status:Success"/></samlp:Status>'
        f"{body}</samlp:Response>"
    )


def provider(port: int, key: str, cert: str, signer: dict[str, str]) -> ThreadingHTTPServer:
    """The identity provider: every request it is brought, it answers with a posted Response.

    `signer` chooses the key it signs with, so a check can make it a forger.
    """

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args: object) -> None:  # quiet
            pass

        def do_GET(self) -> None:
            query = {k: v[0] for k, v in parse_qs(urlparse(self.path).query).items()}
            if "SAMLRequest" not in query:  # the browser asking for a favicon
                self.send_response(404)
                self.end_headers()
                return
            request = zlib.decompress(base64.b64decode(query["SAMLRequest"]), -15).decode()
            request_id = request.split(' ID="', 1)[1].split('"', 1)[0]
            acs = request.split('AssertionConsumerServiceURL="', 1)[1].split('"', 1)[0]
            issuer = request.split("Issuer>", 1)[1].split("<", 1)[0]
            xml = signed_response(request_id, acs, issuer, signer["key"], signer["cert"])
            page = (
                "<!doctype html><html><body onload='document.forms[0].submit()'>"
                f'<form method="post" action="{html.escape(acs)}">'
                f'<input type="hidden" name="SAMLResponse" '
                f'value="{base64.b64encode(xml.encode()).decode()}">'
                f'<input type="hidden" name="RelayState" '
                f'value="{html.escape(query.get("RelayState", ""))}">'
                "<noscript><button>Continue</button></noscript></form></body></html>"
            ).encode()
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(page)))
            self.end_headers()
            self.wfile.write(page)

    server = ThreadingHTTPServer(("127.0.0.1", port), Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    return server


def main() -> int:
    from playwright.sync_api import TimeoutError as PlaywrightTimeout
    from playwright.sync_api import sync_playwright

    failures: list[str] = []

    def check(condition: bool, passed: str, failed: str) -> None:
        if condition:
            print(f"  ok   {passed}")
        else:
            failures.append(failed)
            print(f"  FAIL {failed}")

    key, cert = keypair("idp.harness.example")
    forger_key, forger_cert = keypair("idp.harness.example")
    signer = {"key": key, "cert": cert}
    idp_port, port = free_port(), free_port()
    idp = provider(idp_port, key, cert, signer)

    with tempfile.TemporaryDirectory(prefix="curvevision-saml-") as workdir:
        base = f"http://127.0.0.1:{port}"
        process, base = start_web_server(Path(workdir), port=port, extra_env={
            "CURVEVISION_SAML_ENABLED": "true",
            "CURVEVISION_SAML_ACS_URL": f"{base}/api/v1/auth/saml/acs",
            "CURVEVISION_SAML_CALLBACK_URL": f"{base}/auth/callback",
            "CURVEVISION_SAML_IDP_ENTITY_ID": IDP_ENTITY,
            "CURVEVISION_SAML_IDP_SSO_URL": f"http://127.0.0.1:{idp_port}/sso",
            "CURVEVISION_SAML_IDP_X509_CERT": cert,
            "CURVEVISION_SAML_DISPLAY_NAME": "Acme sign-in",
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
                callbacks: list[str] = []
                page.on("framenavigated", lambda frame: callbacks.append(frame.url)
                        if "/auth/callback" in frame.url else None)

                # ---------------------------------------------------- 1 and 2
                page.goto(f"{base}/login", wait_until="networkidle")
                button = page.locator("[data-saml-start]")
                check(button.count() == 1 and "Acme sign-in" in button.inner_text(),
                      "the sign-in page offers Sign in with Acme sign-in",
                      f"no SAML button (found {button.count()})")
                if button.count() == 1:
                    button.click()
                    # A sign-in that does not arrive is a failed check below, not a crash.
                    with contextlib.suppress(PlaywrightTimeout):
                        page.wait_for_url(f"{base}/projects", timeout=30_000)
                token = page.evaluate("() => localStorage.getItem('curvevision.access')")
                who = page.evaluate(
                    """async (token) => (await fetch('/api/v1/auth/me',
                         {headers: {Authorization: `Bearer ${token}`}})).json()""", token)
                check(page.url == f"{base}/projects" and who.get("email") == PERSON["email"]
                      and who.get("full_name") == PERSON["name"],
                      "to the provider, posted back, and signed in as the account it created",
                      f"ended at {page.url}, signed in as {who}")

                # ------------------------------------------------------------ 3
                stolen = next((url for url in callbacks if "saml=" in url), None)
                stranger = browser.new_page()
                if stolen:
                    stranger.goto(stolen, wait_until="networkidle")
                    said = stranger.locator("[data-sso-callback]").inner_text()
                    check("not started in this browser tab" in said
                          and stranger.evaluate(
                              "() => localStorage.getItem('curvevision.access')") is None,
                          "the login code, opened in a tab that did not start the sign-in, "
                          "is refused and signs nobody in",
                          f"the other tab said {said!r}")
                else:
                    check(False, "", f"no login code came back: {callbacks}")

                # ------------------------------------------------------------ 4
                signer.update(key=forger_key, cert=forger_cert)
                forged = browser.new_page()
                forged.goto(f"{base}/login", wait_until="networkidle")
                forged.locator("[data-saml-start]").click()
                forged.locator("[data-sso-callback]").wait_for(timeout=30_000)
                forged.wait_for_timeout(500)
                said = forged.locator("[data-sso-callback]").inner_text()
                check("not valid" in said and forged.evaluate(
                          "() => localStorage.getItem('curvevision.access')") is None,
                      "an answer signed with another key comes back refused, signing nobody in",
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
