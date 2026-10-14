"""Single sign-on through a SAML 2.0 identity provider (`services/saml.py`), iteration 91.

Against an identity provider in the test that does what a real one does where it matters:
it reads the AuthnRequest the browser is sent with, and answers with a Response whose
assertion it signs with its own key through xmlsec, as Entra ID, Okta or Keycloak would.
Each test signs a person in the way the web application does — start with a verifier's
hash, the provider posts to the assertion consumer service, complete with the verifier —
and the rest try the ways an answer can be forged, replayed, misdirected or stolen.
"""

from __future__ import annotations

import base64
import datetime as dt
import hashlib
import secrets
import time
import uuid
from typing import Any
from urllib.parse import parse_qs, urlparse

import httpx
import pytest

pytest.importorskip("onelogin.saml2", reason="SAML needs the `saml` extra")

from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.x509.oid import NameOID
from onelogin.saml2.constants import OneLogin_Saml2_Constants
from onelogin.saml2.utils import OneLogin_Saml2_Utils

from curvevision.core.config import Settings
from curvevision.services import saml

IDP = "https://idp.example.com/saml"
SSO_URL = "https://idp.example.com/saml/sso"
ACS = "https://cv.example.com/api/v1/auth/saml/acs"
CALLBACK = "https://cv.example.com/auth/callback"
SP = "https://cv.example.com/api/v1/auth/saml/metadata"


def keypair(name: str) -> tuple[str, str]:
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    subject = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, name)])
    now = dt.datetime.now(dt.UTC)
    cert = (
        x509.CertificateBuilder()
        .subject_name(subject)
        .issuer_name(subject)
        .public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now - dt.timedelta(days=1))
        .not_valid_after(now + dt.timedelta(days=30))
        .sign(key, hashes.SHA256())
    )
    pem_key = key.private_bytes(
        serialization.Encoding.PEM,
        serialization.PrivateFormat.TraditionalOpenSSL,
        serialization.NoEncryption(),
    ).decode()
    return pem_key, cert.public_bytes(serialization.Encoding.PEM).decode()


#: The provider's real key, and an impostor's.
KEY, CERT = keypair("idp.example.com")
FORGER_KEY, FORGER_CERT = keypair("idp.example.com")


def instant(offset: int = 0) -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(time.time() + offset))


def attribute(name: str, value: str) -> str:
    return (
        f'<saml:Attribute Name="{name}"><saml:AttributeValue xsi:type="xs:string">'
        f"{value}</saml:AttributeValue></saml:Attribute>"
    )


def assertion(
    request_id: str | None,
    *,
    name_id: str = "persistent-alice",
    email: str = "alice@example.com",
    name: str = "Alice A",
    audience: str = SP,
    recipient: str = ACS,
    not_on_or_after: int = 300,
    issuer: str = IDP,
) -> str:
    responding = f' InResponseTo="{request_id}"' if request_id else ""
    attributes = "".join(attribute(k, v) for k, v in (("email", email), ("displayName", name)) if v)
    return (
        '<saml:Assertion xmlns:saml="urn:oasis:names:tc:SAML:2.0:assertion" '
        'xmlns:xs="http://www.w3.org/2001/XMLSchema" '
        'xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance" '
        f'ID="_a{uuid.uuid4().hex}" Version="2.0" IssueInstant="{instant()}">'
        f"<saml:Issuer>{issuer}</saml:Issuer>"
        "<saml:Subject>"
        f'<saml:NameID Format="urn:oasis:names:tc:SAML:2.0:nameid-format:persistent">'
        f"{name_id}</saml:NameID>"
        '<saml:SubjectConfirmation Method="urn:oasis:names:tc:SAML:2.0:cm:bearer">'
        f'<saml:SubjectConfirmationData NotOnOrAfter="{instant(not_on_or_after)}" '
        f'Recipient="{recipient}"{responding}/>'
        "</saml:SubjectConfirmation></saml:Subject>"
        f'<saml:Conditions NotBefore="{instant(-60)}" NotOnOrAfter="{instant(not_on_or_after)}">'
        f"<saml:AudienceRestriction><saml:Audience>{audience}</saml:Audience>"
        "</saml:AudienceRestriction></saml:Conditions>"
        f'<saml:AuthnStatement AuthnInstant="{instant()}" SessionIndex="_s1">'
        "<saml:AuthnContext><saml:AuthnContextClassRef>"
        "urn:oasis:names:tc:SAML:2.0:ac:classes:PasswordProtectedTransport"
        "</saml:AuthnContextClassRef></saml:AuthnContext></saml:AuthnStatement>"
        f"<saml:AttributeStatement>{attributes}</saml:AttributeStatement>"
        "</saml:Assertion>"
    )


def sign(xml: str, key: str = KEY, cert: str = CERT) -> str:
    signed = OneLogin_Saml2_Utils.add_sign(
        xml,
        key,
        cert,
        sign_algorithm=OneLogin_Saml2_Constants.RSA_SHA256,
        digest_algorithm=OneLogin_Saml2_Constants.SHA256,
    )
    return signed.decode() if isinstance(signed, bytes) else str(signed)


def response(
    request_id: str | None, body: str, *, destination: str = ACS, issuer: str = IDP
) -> str:
    responding = f' InResponseTo="{request_id}"' if request_id else ""
    if body.startswith("<?xml"):
        body = body.split("?>", 1)[1]
    return (
        '<samlp:Response xmlns:samlp="urn:oasis:names:tc:SAML:2.0:protocol" '
        'xmlns:saml="urn:oasis:names:tc:SAML:2.0:assertion" '
        f'ID="_r{uuid.uuid4().hex}" Version="2.0" IssueInstant="{instant()}" '
        f'Destination="{destination}"{responding}>'
        f"<saml:Issuer>{issuer}</saml:Issuer>"
        '<samlp:Status><samlp:StatusCode Value="urn:oasis:names:tc:SAML:2.0:status:Success"/>'
        "</samlp:Status>"
        f"{body}</samlp:Response>"
    )


@pytest.fixture
def settings(settings: Settings) -> Settings:
    saml.reset_caches()
    return settings.model_copy(
        update={
            "saml_enabled": True,
            "saml_acs_url": ACS,
            "saml_callback_url": CALLBACK,
            "saml_idp_entity_id": IDP,
            "saml_idp_sso_url": SSO_URL,
            "saml_idp_x509_cert": CERT,
            "saml_display_name": "Corporate sign-in",
        }
    )


def verifier_pair() -> tuple[str, str]:
    verifier = secrets.token_urlsafe(48)
    hashed = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest())
    return verifier, hashed.decode().rstrip("=")


async def start(client: httpx.AsyncClient, return_to: str = "/") -> tuple[str, str, str]:
    """``(request id, relay state, verifier)`` for a sign-in this tab began."""
    verifier, hashed = verifier_pair()
    started = await client.post(
        "/api/v1/auth/saml/start", json={"verifier_sha256": hashed, "return_to": return_to}
    )
    assert started.status_code == 200, started.text
    url = urlparse(started.json()["url"])
    assert f"{url.scheme}://{url.netloc}{url.path}" == SSO_URL
    query = {k: v[0] for k, v in parse_qs(url.query).items()}
    request = OneLogin_Saml2_Utils.decode_base64_and_inflate(query["SAMLRequest"]).decode()
    request_id = request.split(' ID="', 1)[1].split('"', 1)[0]
    assert f'AssertionConsumerServiceURL="{ACS}"' in request
    return request_id, query.get("RelayState", ""), verifier


async def post_to_acs(client: httpx.AsyncClient, xml: str, relay: str = "/") -> dict[str, str]:
    answered = await client.post(
        "/api/v1/auth/saml/acs",
        data={"SAMLResponse": base64.b64encode(xml.encode()).decode(), "RelayState": relay},
        follow_redirects=False,
    )
    assert answered.status_code == 303, answered.text
    location = urlparse(answered.headers["location"])
    assert f"{location.scheme}://{location.netloc}{location.path}" == CALLBACK
    return {k: v[0] for k, v in parse_qs(location.query).items()}


async def sign_in(client: httpx.AsyncClient, **claims: Any) -> httpx.Response:
    request_id, relay, verifier = await start(client, return_to="/projects")
    answer = await post_to_acs(
        client, response(request_id, sign(assertion(request_id, **claims))), relay
    )
    assert "saml" in answer, answer
    return await client.post(
        "/api/v1/auth/saml/complete", json={"code": answer["saml"], "verifier": verifier}
    )


async def me(client: httpx.AsyncClient, tokens: dict[str, Any]) -> dict[str, Any]:
    found = await client.get(
        "/api/v1/auth/me", headers={"Authorization": f"Bearer {tokens['access_token']}"}
    )
    assert found.status_code == 200, found.text
    return dict(found.json())


class TestSigningIn:
    async def test_a_first_sign_in_makes_an_account_from_the_assertion(
        self, client: httpx.AsyncClient
    ) -> None:
        signed_in = await sign_in(client)
        assert signed_in.status_code == 200, signed_in.text
        tokens = signed_in.json()
        assert tokens["return_to"] == "/projects"
        person = await me(client, tokens)
        assert (person["email"], person["full_name"]) == ("alice@example.com", "Alice A")

    async def test_the_next_sign_in_finds_the_same_account_by_its_name_id(
        self, client: httpx.AsyncClient
    ) -> None:
        first = await me(client, (await sign_in(client)).json())
        # The address changed at the provider; the NameID did not.
        again = await me(client, (await sign_in(client, email="alice@new.example.com")).json())
        assert again["id"] == first["id"]

    async def test_an_existing_account_is_linked_by_its_email(
        self, client: httpx.AsyncClient
    ) -> None:
        registered = await client.post(
            "/api/v1/auth/register",
            json={"username": "alice", "email": "alice@example.com", "password": "pw-1234567890"},
        )
        assert registered.status_code == 201, registered.text
        person = await me(client, (await sign_in(client)).json())
        assert person["id"] == registered.json()["id"]

    async def test_the_sign_in_page_is_told_to_offer_it(self, client: httpx.AsyncClient) -> None:
        options = (await client.get("/api/v1/auth/options")).json()
        assert (options["saml"], options["saml_name"]) == (True, "Corporate sign-in")

    async def test_metadata_tells_the_provider_where_to_answer(
        self, client: httpx.AsyncClient
    ) -> None:
        metadata = await client.get("/api/v1/auth/saml/metadata")
        assert metadata.status_code == 200
        assert metadata.headers["content-type"].startswith("application/samlmetadata+xml")
        assert f'entityID="{SP}"' in metadata.text
        assert f'Location="{ACS}"' in metadata.text


class TestRefused:
    async def refused(self, client: httpx.AsyncClient, xml: str) -> str:
        answer = await post_to_acs(client, xml)
        assert "saml" not in answer, "an answer that should be refused produced a login code"
        return answer["saml_error"]

    async def test_an_unsigned_assertion(self, client: httpx.AsyncClient) -> None:
        request_id, _relay, _verifier = await start(client)
        assert "not valid" in await self.refused(
            client, response(request_id, assertion(request_id))
        )

    async def test_an_assertion_signed_by_another_key(self, client: httpx.AsyncClient) -> None:
        request_id, _relay, _verifier = await start(client)
        forged = sign(assertion(request_id), FORGER_KEY, FORGER_CERT)
        assert "not valid" in await self.refused(client, response(request_id, forged))

    async def test_an_assertion_changed_after_it_was_signed(
        self, client: httpx.AsyncClient
    ) -> None:
        request_id, _relay, _verifier = await start(client)
        tampered = sign(assertion(request_id)).replace("alice@example.com", "admin@example.com")
        assert "not valid" in await self.refused(client, response(request_id, tampered))

    async def test_a_second_unsigned_assertion_smuggled_beside_a_signed_one(
        self, client: httpx.AsyncClient
    ) -> None:
        # Signature wrapping: the signed assertion is genuine, the other one is the attack.
        request_id, _relay, _verifier = await start(client)
        body = sign(assertion(request_id)) + assertion(request_id, email="admin@example.com")
        assert "not valid" in await self.refused(client, response(request_id, body))

    @pytest.mark.parametrize(
        "claims",
        [
            {"audience": "https://elsewhere.example.com"},
            {"recipient": "https://elsewhere.example.com/acs"},
            {"not_on_or_after": -120},
            {"issuer": "https://impostor.example.com"},
        ],
        ids=["audience", "recipient", "expired", "issuer"],
    )
    async def test_an_assertion_not_meant_for_this_site_now(
        self, client: httpx.AsyncClient, claims: dict[str, Any]
    ) -> None:
        request_id, _relay, _verifier = await start(client)
        xml = response(request_id, sign(assertion(request_id, **claims)))
        assert "not valid" in await self.refused(client, xml)

    async def test_a_response_sent_somewhere_else(self, client: httpx.AsyncClient) -> None:
        request_id, _relay, _verifier = await start(client)
        xml = response(
            request_id, sign(assertion(request_id)), destination="https://elsewhere.example.com"
        )
        assert "not valid" in await self.refused(client, xml)

    async def test_an_unsolicited_response(self, client: httpx.AsyncClient) -> None:
        assert "must start here" in await self.refused(
            client, response(None, sign(assertion(None)))
        )

    async def test_an_answer_to_a_request_that_was_not_made_recently(
        self, client: httpx.AsyncClient, settings: Settings
    ) -> None:
        _verifier, hashed = verifier_pair()
        old = saml.mint_request_id(settings, hashed, now=time.time() - 3600)
        assert "too long" in await self.refused(client, response(old, sign(assertion(old))))

    async def test_a_login_code_finished_without_the_tab_s_verifier(
        self, client: httpx.AsyncClient
    ) -> None:
        # A code that leaked — through history, a referrer, or a replayed response — is
        # useless to anyone without the verifier the starting tab kept.
        request_id, relay, _verifier = await start(client)
        answer = await post_to_acs(client, response(request_id, sign(assertion(request_id))), relay)
        stolen = await client.post(
            "/api/v1/auth/saml/complete",
            json={"code": answer["saml"], "verifier": secrets.token_urlsafe(48)},
        )
        assert stolen.status_code == 401
        assert "not started in this browser tab" in stolen.json()["detail"]

    async def test_a_login_code_not_made_here(self, client: httpx.AsyncClient) -> None:
        forged = await client.post(
            "/api/v1/auth/saml/complete",
            json={"code": "not-a-code", "verifier": secrets.token_urlsafe(48)},
        )
        assert forged.status_code == 401

    async def test_garbage_is_refused_rather_than_an_error(self, client: httpx.AsyncClient) -> None:
        answer = await client.post(
            "/api/v1/auth/saml/acs",
            data={"SAMLResponse": base64.b64encode(b"<not-saml").decode()},
            follow_redirects=False,
        )
        assert answer.status_code == 303
        assert "saml_error" in answer.headers["location"]


class TestPolicy:
    async def test_without_trusting_email_an_existing_account_is_not_taken(
        self, client: httpx.AsyncClient, settings: Settings, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        await client.post(
            "/api/v1/auth/register",
            json={"username": "alice", "email": "alice@example.com", "password": "pw-1234567890"},
        )
        monkeypatch.setattr(settings, "saml_trust_email", False)
        refused = await sign_in(client)
        assert refused.status_code == 401
        assert "verified email" in refused.json()["detail"]

    async def test_only_allowed_domains_sign_in(
        self, client: httpx.AsyncClient, settings: Settings, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(settings, "saml_allowed_domains", ("corp.example.com",))
        refused = await sign_in(client)
        assert refused.status_code == 401
        allowed = await sign_in(client, name_id="bob", email="bob@corp.example.com")
        assert allowed.status_code == 200, allowed.text

    async def test_off_in_the_desktop_shape(
        self, client: httpx.AsyncClient, settings: Settings, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(settings, "local_mode", True)
        assert (await client.get("/api/v1/auth/options")).json()["saml"] is False
        _verifier, hashed = verifier_pair()
        started = await client.post(
            "/api/v1/auth/saml/start", json={"verifier_sha256": hashed, "return_to": "/"}
        )
        assert started.status_code == 404


class TestRequestIds:
    def test_a_request_id_belongs_only_to_the_tab_holding_its_verifier(
        self, settings: Settings
    ) -> None:
        verifier, hashed = verifier_pair()
        minted = saml.mint_request_id(settings, hashed)
        assert minted.startswith("_") and ":" not in minted  # an xs:ID
        assert saml.belongs_to(settings, minted, verifier)
        assert not saml.belongs_to(settings, minted, verifier + "x")
        other = settings.model_copy(update={"secret_key": "another-server-entirely-0123456789"})
        assert not saml.belongs_to(other, minted, verifier)
        assert not saml.belongs_to(settings, "_zz.nonsense", verifier)
