"""The desktop application asks its own server whether an address is a team server.

Before the desktop window switches to a team server (iteration 85), this computer's server
fetches the team server's health check and says, in a sentence, whether the window can go
there. The check runs here rather than in the page because the page may only talk to its
own origin (`connect-src 'self'`), and widening that for one feature would loosen the policy
for everything else; and it uses the operating system's certificate store (`truststore`),
because a team server inside a company is often signed by the company's own authority.

Local mode only: on a shared server, fetching an address a client names is a server-side
request forgery, so the route does not exist there.
"""

from __future__ import annotations

from typing import Any

import httpx
import pytest

from curvevision.services import team_server

HEALTHY = {
    "status": "ok",
    "version": "0.1.0",
    "environment": "production",
    "database": "ok",
    "storage": "s3",
    "job_queue": "dramatiq",
    "local_mode": False,
}


def answering(handler: Any) -> httpx.MockTransport:
    return httpx.MockTransport(handler)


def json_reply(body: Any, status: int = 200) -> httpx.MockTransport:
    return answering(lambda request: httpx.Response(status, json=body))


class TestTheAddress:
    @pytest.mark.parametrize(
        ("typed", "origin"),
        [
            ("https://cv.example.com", "https://cv.example.com"),
            ("  https://cv.example.com/projects/42?x=1  ", "https://cv.example.com"),
            ("cv.example.com", "https://cv.example.com"),
            ("cv.example.com:8443", "https://cv.example.com:8443"),
            ("http://10.0.0.5:8080/", "http://10.0.0.5:8080"),
            ("HTTPS://CV.Example.COM", "https://cv.example.com"),
        ],
    )
    def test_what_a_person_types_becomes_an_origin(self, typed: str, origin: str) -> None:
        assert team_server.origin_of(typed) == origin

    @pytest.mark.parametrize(
        "typed",
        [
            "",
            "   ",
            "ftp://cv.example.com",
            "file:///etc/passwd",
            "javascript:alert(1)",
            "http://",
            "https://someone:secret@cv.example.com",
        ],
    )
    def test_what_is_not_a_web_address_is_refused(self, typed: str) -> None:
        assert team_server.origin_of(typed) is None


class TestTheCheck:
    async def test_a_team_server_is_recognised_with_its_version(self) -> None:
        seen: list[httpx.Request] = []

        def handler(request: httpx.Request) -> httpx.Response:
            seen.append(request)
            return httpx.Response(200, json=HEALTHY)

        result = await team_server.check("cv.example.com", transport=answering(handler))

        assert result.model_dump() == {
            "ok": True,
            "url": "https://cv.example.com",
            "version": "0.1.0",
            "reason": None,
        }
        assert [str(r.url) for r in seen] == ["https://cv.example.com/api/v1/health"]
        # Nothing of this installation's goes to somebody else's server.
        assert "authorization" not in seen[0].headers
        assert "cookie" not in seen[0].headers

    async def test_a_redirect_to_https_is_followed_and_remembered(self) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            if request.url.scheme == "http":
                return httpx.Response(
                    301, headers={"location": "https://cv.example.com/api/v1/health"}
                )
            return httpx.Response(200, json=HEALTHY)

        result = await team_server.check("http://cv.example.com", transport=answering(handler))

        assert (result.ok, result.url) == (True, "https://cv.example.com")

    async def test_another_desktop_installation_is_not_a_team_server(self) -> None:
        result = await team_server.check(
            "http://192.168.1.4:49312", transport=json_reply({**HEALTHY, "local_mode": True})
        )
        assert not result.ok
        assert "desktop installation, not a team server" in (result.reason or "")

    async def test_a_server_whose_database_is_down_is_refused(self) -> None:
        result = await team_server.check(
            "https://cv.example.com",
            transport=json_reply({**HEALTHY, "status": "degraded", "database": "unavailable"}),
        )
        assert not result.ok
        assert "database is unavailable" in (result.reason or "")

    @pytest.mark.parametrize("body", ["<html>hello</html>", {"status": "ok"}, [1, 2, 3]])
    async def test_something_that_is_not_curvevision_is_refused(self, body: Any) -> None:
        transport = (
            answering(lambda request: httpx.Response(200, text=body))
            if isinstance(body, str)
            else json_reply(body)
        )
        result = await team_server.check("https://example.com", transport=transport)
        assert not result.ok
        assert "not a CurveVision server" in (result.reason or "")

    async def test_nothing_answering_is_said_plainly(self) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            raise httpx.ConnectError("connection refused", request=request)

        result = await team_server.check("https://cv.example.com", transport=answering(handler))
        assert not result.ok
        assert "Could not reach https://cv.example.com" in (result.reason or "")

    async def test_an_untrusted_certificate_is_named_as_such(self) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            raise httpx.ConnectError(
                "[SSL: CERTIFICATE_VERIFY_FAILED] certificate verify failed", request=request
            )

        result = await team_server.check("https://cv.example.com", transport=answering(handler))
        assert not result.ok
        assert "certificate" in (result.reason or "")

    async def test_an_address_is_refused_before_anything_is_fetched(self) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            raise AssertionError("must not be called")

        result = await team_server.check("ftp://cv.example.com", transport=answering(handler))
        assert not result.ok
        assert "not a web address" in (result.reason or "")


class TestTheRoute:
    async def test_a_shared_server_does_not_have_it(self, owner: Any) -> None:
        response = await owner.post(
            "/api/v1/local/team-server-check", json={"address": "https://cv.example.com"}
        )
        assert response.status_code == 404

    async def test_the_desktop_application_does(
        self, owner: Any, settings: Any, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(settings, "local_mode", True)
        monkeypatch.setattr(team_server, "TRANSPORT", json_reply(HEALTHY))

        response = await owner.post(
            "/api/v1/local/team-server-check", json={"address": "cv.example.com"}
        )

        assert response.status_code == 200, response.text
        assert response.json() == {
            "ok": True,
            "url": "https://cv.example.com",
            "version": "0.1.0",
            "reason": None,
        }

    async def test_it_needs_the_installations_credential(
        self, client: httpx.AsyncClient, settings: Any, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(settings, "local_mode", True)
        response = await client.post(
            "/api/v1/local/team-server-check", json={"address": "cv.example.com"}
        )
        assert response.status_code == 401
