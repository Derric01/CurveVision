"""Single sign-on through an OpenID Connect provider (`services/oidc.py`), iteration 81.

Against a provider in the same process that does what a real one does where it matters:
publishes a discovery document and a key set, checks the PKCE verifier and the client's
secret at its token endpoint, and signs ID tokens with RS256. Each test signs a person in
the way the web application does — start, "authorize" at the provider, complete — and then
calls the API with the tokens it got.
"""

from __future__ import annotations

import base64
import hashlib
import secrets
import time
from collections.abc import Iterator
from typing import Any
from urllib.parse import parse_qs, urlparse

import httpx
import jwt
import pytest

pytest.importorskip("cryptography", reason="RS256 needs the `oidc` extra")

from cryptography.hazmat.primitives.asymmetric import rsa

from curvevision.core.config import Settings
from curvevision.services import oidc
from tests.conftest import ApiActor

ISSUER = "https://idp.example.com/realms/team"
CLIENT_ID = "curvevision"
CLIENT_SECRET = "s3cret"
REDIRECT = "https://cv.example.com/auth/callback"


class Provider:
    """An OpenID Connect provider, as far as the server can tell."""

    def __init__(self) -> None:
        self.key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
        self.kid = "key-1"
        self.codes: dict[str, dict[str, Any]] = {}
        #: Claims the next ID token carries, beyond the standard ones.
        self.person: dict[str, Any] = {}
        #: Overrides for the next ID token's standard claims (to forge a bad one).
        self.tamper: dict[str, Any] = {}
        self.sign_with: Any = None

    def jwks(self) -> dict[str, Any]:
        public = jwt.algorithms.RSAAlgorithm.to_jwk(self.key.public_key(), as_dict=True)
        return {"keys": [{**public, "kid": self.kid, "use": "sig", "alg": "RS256"}]}

    def authorize(self, url: str) -> str:
        """What the provider does when the person approves: a code for this request."""
        query = {k: v[0] for k, v in parse_qs(urlparse(url).query).items()}
        assert query["client_id"] == CLIENT_ID
        assert query["redirect_uri"] == REDIRECT
        assert query["code_challenge_method"] == "S256"
        code = secrets.token_urlsafe(16)
        self.codes[code] = {"nonce": query["nonce"], "challenge": query["code_challenge"]}
        return code

    def handle(self, request: httpx.Request) -> httpx.Response:
        path = request.url.path
        if path.endswith("/.well-known/openid-configuration"):
            return httpx.Response(
                200,
                json={
                    "issuer": ISSUER,
                    "authorization_endpoint": f"{ISSUER}/protocol/openid-connect/auth",
                    "token_endpoint": f"{ISSUER}/protocol/openid-connect/token",
                    "jwks_uri": f"{ISSUER}/protocol/openid-connect/certs",
                },
            )
        if path.endswith("/certs"):
            return httpx.Response(200, json=self.jwks())
        if path.endswith("/token"):
            return self.token(request)
        return httpx.Response(404)

    def token(self, request: httpx.Request) -> httpx.Response:
        expected = base64.b64encode(f"{CLIENT_ID}:{CLIENT_SECRET}".encode()).decode()
        if request.headers.get("authorization") != f"Basic {expected}":
            return httpx.Response(401, json={"error": "invalid_client"})
        form = {k: v[0] for k, v in parse_qs(request.content.decode()).items()}
        grant = self.codes.pop(form.get("code", ""), None)
        if grant is None:
            return httpx.Response(400, json={"error": "invalid_grant"})
        digest = hashlib.sha256(form["code_verifier"].encode()).digest()
        if base64.urlsafe_b64encode(digest).rstrip(b"=").decode() != grant["challenge"]:
            return httpx.Response(
                400, json={"error": "invalid_grant", "error_description": "PKCE mismatch"}
            )
        now = int(time.time())
        claims = {
            "iss": ISSUER,
            "aud": CLIENT_ID,
            "iat": now,
            "exp": now + 300,
            "nonce": grant["nonce"],
            **self.person,
            **self.tamper,
        }
        token = jwt.encode(
            claims, self.sign_with or self.key, algorithm="RS256", headers={"kid": self.kid}
        )
        return httpx.Response(200, json={"id_token": token, "access_token": "unused"})


@pytest.fixture
def provider(monkeypatch: pytest.MonkeyPatch) -> Iterator[Provider]:
    idp = Provider()
    oidc.reset_caches()
    monkeypatch.setattr(
        oidc, "client", lambda: httpx.AsyncClient(transport=httpx.MockTransport(idp.handle))
    )
    yield idp
    oidc.reset_caches()


@pytest.fixture
def settings(settings: Settings) -> Settings:
    return settings.model_copy(
        update={
            "oidc_enabled": True,
            "oidc_issuer": ISSUER,
            "oidc_client_id": CLIENT_ID,
            "oidc_client_secret": CLIENT_SECRET,
            "oidc_redirect_uri": REDIRECT,
            "oidc_display_name": "Team SSO",
        }
    )


async def sign_in(
    client: httpx.AsyncClient, provider: Provider, *, return_to: str = "/"
) -> httpx.Response:
    started = await client.post("/api/v1/auth/sso/start", json={"return_to": return_to})
    assert started.status_code == 200, started.text
    body = started.json()
    code = provider.authorize(body["authorization_url"])
    return await client.post(
        "/api/v1/auth/sso/complete", json={"code": code, "state": body["state"]}
    )


async def me(client: httpx.AsyncClient, tokens: dict[str, Any]) -> dict[str, Any]:
    response = await client.get(
        "/api/v1/auth/me", headers={"Authorization": f"Bearer {tokens['access_token']}"}
    )
    assert response.status_code == 200, response.text
    return dict(response.json())


ALICE = {"sub": "a-1", "email": "Alice@Example.com", "email_verified": True, "name": "Alice A"}


class TestSigningIn:
    async def test_a_first_sign_in_creates_an_account_and_a_second_finds_it(
        self, client: httpx.AsyncClient, provider: Provider
    ) -> None:
        provider.person = {**ALICE, "preferred_username": "alice"}

        first = await sign_in(client, provider)
        assert first.status_code == 200, first.text
        alice = await me(client, first.json())
        assert (alice["email"], alice["username"], alice["full_name"]) == (
            "alice@example.com",
            "alice",
            "Alice A",
        )

        again = await me(client, (await sign_in(client, provider)).json())
        assert again["id"] == alice["id"]

    async def test_an_existing_account_with_the_verified_email_is_the_one_signed_into(
        self, owner: ApiActor, client: httpx.AsyncClient, provider: Provider
    ) -> None:
        existing = (await owner.get("/api/v1/auth/me")).json()
        provider.person = {"sub": "o-1", "email": existing["email"], "email_verified": True}

        tokens = (await sign_in(client, provider)).json()

        assert (await me(client, tokens))["id"] == existing["id"]

    async def test_an_unverified_email_never_claims_an_existing_account(
        self, owner: ApiActor, client: httpx.AsyncClient, provider: Provider
    ) -> None:
        existing = (await owner.get("/api/v1/auth/me")).json()
        provider.person = {"sub": "x-1", "email": existing["email"], "email_verified": False}

        refused = await sign_in(client, provider)

        assert refused.status_code == 401
        assert "verified email" in refused.json()["detail"]

    async def test_a_new_account_joins_the_default_workspace(
        self,
        organization: dict[str, Any],
        client: httpx.AsyncClient,
        provider: Provider,
        settings: Settings,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        monkeypatch.setattr(settings, "oidc_default_workspace", organization["slug"])
        provider.person = ALICE

        tokens = (await sign_in(client, provider)).json()

        workspaces = await client.get(
            "/api/v1/organizations",
            headers={"Authorization": f"Bearer {tokens['access_token']}"},
        )
        assert [w["slug"] for w in workspaces.json()] == [organization["slug"]]

    async def test_it_goes_where_it_was_going_on_this_site_and_nowhere_else(
        self, client: httpx.AsyncClient, provider: Provider
    ) -> None:
        provider.person = ALICE
        assert (await sign_in(client, provider, return_to="/projects/7")).json()[
            "return_to"
        ] == "/projects/7"
        provider.person = ALICE
        assert (await sign_in(client, provider, return_to="//evil.example")).json()[
            "return_to"
        ] == "/"


class TestRefused:
    @pytest.mark.parametrize(
        ("tamper", "why"),
        [
            ({"nonce": "another"}, "not issued for this sign-in"),
            ({"aud": "someone-else"}, "not valid"),
            ({"iss": "https://elsewhere.example"}, "not valid"),
            ({"exp": 1}, "not valid"),
        ],
    )
    async def test_an_id_token_that_is_not_for_this_sign_in(
        self, client: httpx.AsyncClient, provider: Provider, tamper: dict[str, Any], why: str
    ) -> None:
        provider.person, provider.tamper = ALICE, tamper

        refused = await sign_in(client, provider)

        assert refused.status_code == 401, refused.text
        assert why in refused.json()["detail"]

    async def test_an_id_token_signed_with_a_key_the_provider_does_not_publish(
        self, client: httpx.AsyncClient, provider: Provider
    ) -> None:
        provider.person = ALICE
        provider.sign_with = rsa.generate_private_key(public_exponent=65537, key_size=2048)

        refused = await sign_in(client, provider)

        assert refused.status_code == 401
        assert "not valid" in refused.json()["detail"]

    async def test_a_state_this_server_did_not_sign(
        self, client: httpx.AsyncClient, provider: Provider
    ) -> None:
        forged = jwt.encode({"aud": "curvevision:oidc-state", "nonce": "n"}, "guess", "HS256")
        refused = await client.post(
            "/api/v1/auth/sso/complete", json={"code": "c", "state": forged}
        )
        assert refused.status_code == 401
        assert "not started here" in refused.json()["detail"]

    async def test_a_code_spent_twice(self, client: httpx.AsyncClient, provider: Provider) -> None:
        provider.person = ALICE
        started = (await client.post("/api/v1/auth/sso/start", json={})).json()
        code = provider.authorize(started["authorization_url"])
        payload = {"code": code, "state": started["state"]}
        assert (await client.post("/api/v1/auth/sso/complete", json=payload)).status_code == 200

        again = await client.post("/api/v1/auth/sso/complete", json=payload)

        assert again.status_code == 401

    async def test_a_domain_not_allowed(
        self,
        client: httpx.AsyncClient,
        provider: Provider,
        settings: Settings,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        monkeypatch.setattr(settings, "oidc_allowed_domains", ("team.example",))
        provider.person = ALICE

        refused = await sign_in(client, provider)

        assert refused.status_code == 401
        assert "may not sign in" in refused.json()["detail"]

    async def test_a_new_person_when_accounts_are_not_created(
        self,
        client: httpx.AsyncClient,
        provider: Provider,
        settings: Settings,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        monkeypatch.setattr(settings, "oidc_create_users", False)
        provider.person = ALICE

        refused = await sign_in(client, provider)

        assert refused.status_code == 401
        assert "no account here" in refused.json()["detail"]


class TestTheSignInPage:
    async def test_it_is_told_what_to_offer(
        self, client: httpx.AsyncClient, settings: Settings, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        options = (await client.get("/api/v1/auth/options")).json()
        assert options == {
            "password": True,
            "registration": True,
            "sso": True,
            "sso_name": "Team SSO",
        }

        monkeypatch.setattr(settings, "oidc_only", True)
        options = (await client.get("/api/v1/auth/options")).json()
        assert (options["password"], options["registration"]) == (False, False)

    async def test_sso_only_turns_password_sign_in_off(
        self,
        owner: ApiActor,
        client: httpx.AsyncClient,
        settings: Settings,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        monkeypatch.setattr(settings, "oidc_only", True)

        refused = await client.post(
            "/api/v1/auth/login", json={"identifier": "owner", "password": "irrelevant"}
        )

        assert refused.status_code == 403
        assert "Team SSO" in refused.json()["detail"]


class TestWithoutAProvider:
    async def test_the_page_offers_passwords_only_and_starting_one_is_a_404(
        self, client: httpx.AsyncClient, settings: Settings, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(settings, "oidc_enabled", False)

        options = (await client.get("/api/v1/auth/options")).json()
        started = await client.post("/api/v1/auth/sso/start", json={})

        assert (options["sso"], options["password"]) == (False, True)
        assert started.status_code == 404
