"""Authentication, sessions and API tokens."""

from __future__ import annotations

import httpx
import pytest

from tests.conftest import register


class TestRegistration:
    async def test_first_account_becomes_the_instance_administrator(
        self, client: httpx.AsyncClient
    ) -> None:
        """A fresh self-hosted instance must be administrable without shell access."""
        first = await register(client, "first")
        second = await register(client, "second")

        assert first.user["is_superuser"] is True
        assert second.user["is_superuser"] is False

    async def test_duplicate_email_is_rejected(self, client: httpx.AsyncClient) -> None:
        await register(client, "taken")
        response = await client.post(
            "/api/v1/auth/register",
            json={
                "email": "taken@example.com",
                "username": "someoneelse",
                "password": "correct-horse-42",
            },
        )
        assert response.status_code == 409
        assert response.headers["content-type"].startswith("application/problem+json")

    async def test_weak_password_is_rejected_with_the_requirement(
        self, client: httpx.AsyncClient
    ) -> None:
        response = await client.post(
            "/api/v1/auth/register",
            json={
                "email": "weak@example.com",
                "username": "weakling",
                "password": "password",
            },
        )
        assert response.status_code == 422
        assert "common" in response.text.lower() or "characters" in response.text.lower()

    async def test_unknown_body_fields_are_rejected(self, client: httpx.AsyncClient) -> None:
        """Silently ignoring a misspelled field makes client bugs look like server bugs."""
        response = await client.post(
            "/api/v1/auth/register",
            json={
                "email": "strict@example.com",
                "username": "strict",
                "password": "correct-horse-42",
                "is_superuser": True,
            },
        )
        assert response.status_code == 422


class TestLogin:
    async def test_login_by_username_or_email(self, client: httpx.AsyncClient) -> None:
        await register(client, "dualid")
        for identifier in ("dualid", "dualid@example.com"):
            response = await client.post(
                "/api/v1/auth/login",
                json={"identifier": identifier, "password": "correct-horse-42"},
            )
            assert response.status_code == 200, identifier

    @pytest.mark.parametrize(
        ("identifier", "password"),
        [("ghost", "correct-horse-42"), ("real", "wrong-password")],
    )
    async def test_bad_credentials_give_one_indistinguishable_error(
        self, client: httpx.AsyncClient, identifier: str, password: str
    ) -> None:
        """A distinct 'no such user' response is a user-enumeration oracle."""
        await register(client, "real")
        response = await client.post(
            "/api/v1/auth/login", json={"identifier": identifier, "password": password}
        )
        assert response.status_code == 401
        assert response.json()["detail"] == "Incorrect credentials"

    async def test_unauthenticated_requests_are_rejected(self, client: httpx.AsyncClient) -> None:
        assert (await client.get("/api/v1/auth/me")).status_code == 401
        assert (await client.get("/api/v1/projects")).status_code == 401


class TestRefreshRotation:
    async def test_refresh_returns_a_new_pair_and_burns_the_old_one(
        self, client: httpx.AsyncClient
    ) -> None:
        """Rotation means a stolen refresh token is usable at most once."""
        await register(client, "rotator")
        login = await client.post(
            "/api/v1/auth/login",
            json={"identifier": "rotator", "password": "correct-horse-42"},
        )
        original = login.json()["refresh_token"]

        first = await client.post("/api/v1/auth/refresh", json={"refresh_token": original})
        assert first.status_code == 200
        assert first.json()["refresh_token"] != original

        replay = await client.post("/api/v1/auth/refresh", json={"refresh_token": original})
        assert replay.status_code == 401

    async def test_logout_revokes_the_refresh_token(self, client: httpx.AsyncClient) -> None:
        await register(client, "leaver")
        login = await client.post(
            "/api/v1/auth/login",
            json={"identifier": "leaver", "password": "correct-horse-42"},
        )
        refresh = login.json()["refresh_token"]

        assert (
            await client.post("/api/v1/auth/logout", json={"refresh_token": refresh})
        ).status_code == 204
        assert (
            await client.post("/api/v1/auth/refresh", json={"refresh_token": refresh})
        ).status_code == 401


class TestApiTokens:
    async def test_token_is_shown_once_and_then_authenticates(
        self, client: httpx.AsyncClient
    ) -> None:
        actor = await register(client, "automation")

        created = await actor.post("/api/v1/auth/tokens", json={"name": "ci"})
        assert created.status_code == 201
        token = created.json()["token"]
        assert token.startswith("cv_")

        listed = await actor.get("/api/v1/auth/tokens")
        assert listed.status_code == 200
        assert "token" not in listed.json()[0], "the secret must never be readable again"

        me = await client.get("/api/v1/auth/me", headers={"Authorization": f"Bearer {token}"})
        assert me.status_code == 200
        assert me.json()["username"] == "automation"

    async def test_revoked_token_stops_working(self, client: httpx.AsyncClient) -> None:
        actor = await register(client, "revoker")
        created = await actor.post("/api/v1/auth/tokens", json={"name": "temporary"})
        token = created.json()["token"]
        token_id = created.json()["id"]

        assert (await actor.delete(f"/api/v1/auth/tokens/{token_id}")).status_code == 204

        response = await client.get("/api/v1/auth/me", headers={"Authorization": f"Bearer {token}"})
        assert response.status_code == 401

    @pytest.mark.parametrize("bad", ["cv_only_two", "cv__", "notatoken", "cv_abc_"])
    async def test_malformed_tokens_are_rejected(self, client: httpx.AsyncClient, bad: str) -> None:
        response = await client.get("/api/v1/auth/me", headers={"Authorization": f"Bearer {bad}"})
        assert response.status_code == 401


class TestPasswordChange:
    async def test_changing_a_password_ends_other_sessions(self, client: httpx.AsyncClient) -> None:
        actor = await register(client, "changer")
        login = await client.post(
            "/api/v1/auth/login",
            json={"identifier": "changer", "password": "correct-horse-42"},
        )
        refresh = login.json()["refresh_token"]

        response = await actor.post(
            "/api/v1/auth/password",
            json={"current_password": "correct-horse-42", "new_password": "brand-new-secret"},
        )
        assert response.status_code == 204

        assert (
            await client.post("/api/v1/auth/refresh", json={"refresh_token": refresh})
        ).status_code == 401

    async def test_wrong_current_password_is_rejected(self, client: httpx.AsyncClient) -> None:
        actor = await register(client, "forgetful")
        response = await actor.post(
            "/api/v1/auth/password",
            json={"current_password": "not-it", "new_password": "brand-new-secret"},
        )
        assert response.status_code == 401


class TestSecurityHeaders:
    async def test_responses_carry_hardening_headers(self, client: httpx.AsyncClient) -> None:
        response = await client.get("/api/v1/health")
        assert response.headers["X-Content-Type-Options"] == "nosniff"
        assert response.headers["X-Frame-Options"] == "DENY"
        assert "frame-ancestors 'none'" in response.headers["Content-Security-Policy"]

    async def test_every_response_carries_a_request_id(self, client: httpx.AsyncClient) -> None:
        response = await client.get("/api/v1/health")
        assert response.headers.get("X-Request-ID")
