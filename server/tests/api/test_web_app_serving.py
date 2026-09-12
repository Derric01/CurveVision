"""Serving the web application from the server process.

This is how the desktop build ships the whole product as one executable, and how
`curvevision-local` opens a complete CurveVision in a browser with no configuration.

These tests exist because of a real failure: the server's `default-src 'none'` policy —
correct for a JSON API, and correct when it was written — silently blocked every script
and stylesheet once the same process began serving the application. Every request
succeeded with a 200. The window was blank. Nothing that only checked status codes or
response bodies would have noticed, so these assert the *headers that decide whether a
browser will run what it was sent*.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import httpx
import pytest

from curvevision.core.config import Settings

STRICT = "default-src 'none'; frame-ancestors 'none'"


@pytest.fixture
def built_web_app(tmp_path: Path) -> Path:
    """A directory shaped like `web/dist` after a Vite build."""
    root = tmp_path / "dist"
    (root / "assets").mkdir(parents=True)
    (root / "index.html").write_text(
        "<!doctype html><html><head>"
        '<link rel="stylesheet" href="/assets/index-abc123.css">'
        '</head><body><div id="root"></div>'
        '<script type="module" src="/assets/index-abc123.js"></script>'
        "</body></html>"
    )
    (root / "assets" / "index-abc123.js").write_text("export const app = 1;")
    (root / "assets" / "index-abc123.css").write_text(":root{color:#fff}")
    (root / "favicon.svg").write_text("<svg xmlns='http://www.w3.org/2000/svg'/>")
    return root


def app_serving(settings: Settings, web_root: Path) -> Any:
    from curvevision.main import create_app

    return create_app(settings.model_copy(update={"web_root": str(web_root)}))


async def get(application: Any, path: str) -> httpx.Response:
    transport = httpx.ASGITransport(app=application)
    async with (
        httpx.AsyncClient(transport=transport, base_url="http://testserver") as client,
        application.router.lifespan_context(application),
    ):
        return await client.get(path)


# ----------------------------------------------------------- the policy that broke this


async def test_the_served_page_is_allowed_to_load_its_own_scripts(
    settings: Settings, built_web_app: Path
) -> None:
    """The regression. `default-src 'none'` here means a blank window, not a secure one."""
    response = await get(app_serving(settings, built_web_app), "/")
    policy = response.headers["content-security-policy"]

    assert "default-src 'none'" not in policy
    assert "script-src 'self'" in policy
    assert "style-src 'self' 'unsafe-inline'" in policy
    assert "connect-src 'self'" in policy


async def test_the_editor_may_render_frames_it_fetched(
    settings: Settings, built_web_app: Path
) -> None:
    """Frames and thumbnails arrive as blobs; a canvas produces more of them."""
    response = await get(app_serving(settings, built_web_app), "/")
    policy = response.headers["content-security-policy"]
    assert "img-src 'self' data: blob:" in policy


async def test_the_relaxed_policy_still_refuses_the_things_that_matter(
    settings: Settings, built_web_app: Path
) -> None:
    response = await get(app_serving(settings, built_web_app), "/")
    policy = response.headers["content-security-policy"]

    assert "frame-ancestors 'none'" in policy  # no clickjacking
    assert "object-src 'none'" in policy  # no plugins
    assert "base-uri 'self'" in policy  # no base-tag hijacking
    # Scripts from anywhere else are still refused, which is the directive that matters.
    assert "script-src 'self';" in policy
    assert "'unsafe-eval'" not in policy
    assert "script-src 'self' 'unsafe-inline'" not in policy


async def test_asset_responses_can_also_be_loaded(settings: Settings, built_web_app: Path) -> None:
    application = app_serving(settings, built_web_app)
    for path in ("/assets/index-abc123.js", "/assets/index-abc123.css"):
        response = await get(application, path)
        assert response.status_code == 200, path
        assert "default-src 'none'" not in response.headers["content-security-policy"], path


# ---------------------------------------------------------------- the API is unaffected


async def test_the_api_keeps_the_strict_policy_even_when_the_app_is_served(
    settings: Settings, built_web_app: Path
) -> None:
    """Relaxing the policy for the page must not relax it for the API."""
    response = await get(app_serving(settings, built_web_app), "/api/v1/health")
    assert response.status_code == 200
    assert response.headers["content-security-policy"] == STRICT


async def test_an_api_only_deployment_is_untouched(settings: Settings) -> None:
    """Every containerised deployment leaves `web_root` unset and keeps the strict policy."""
    from curvevision.main import create_app

    response = await get(create_app(settings), "/api/v1/health")
    assert response.headers["content-security-policy"] == STRICT


# --------------------------------------------------------------------------- the routes


async def test_the_index_is_served_at_the_root(settings: Settings, built_web_app: Path) -> None:
    response = await get(app_serving(settings, built_web_app), "/")
    assert response.status_code == 200
    assert b'id="root"' in response.content


async def test_a_deep_link_loads_the_application_rather_than_404ing(
    settings: Settings, built_web_app: Path
) -> None:
    """The editor uses client-side routing, so `/jobs/<id>` has no file behind it."""
    application = app_serving(settings, built_web_app)
    index = await get(application, "/")
    deep = await get(application, "/jobs/2f1c8a2e-0000-4000-8000-000000000000")

    assert deep.status_code == 200
    assert deep.content == index.content


async def test_a_real_file_is_served_rather_than_the_shell(
    settings: Settings, built_web_app: Path
) -> None:
    response = await get(app_serving(settings, built_web_app), "/favicon.svg")
    assert response.status_code == 200
    assert response.content.startswith(b"<svg")


@pytest.mark.parametrize(
    "path",
    [
        "/../../../../etc/passwd",
        "/..%2f..%2fetc%2fpasswd",
        "/assets/../../../../etc/passwd",
    ],
)
async def test_no_path_escapes_the_bundle(
    settings: Settings, built_web_app: Path, path: str
) -> None:
    response = await get(app_serving(settings, built_web_app), path)
    assert b"root:x:" not in response.content


async def test_a_web_root_without_an_index_serves_the_api_only(
    settings: Settings, tmp_path: Path
) -> None:
    """A misconfigured path must not take the API down with it."""
    empty = tmp_path / "not-a-build"
    empty.mkdir()
    application = app_serving(settings, empty)

    health = await get(application, "/api/v1/health")
    assert health.status_code == 200
    # And the root falls back to the API descriptor rather than a broken page.
    root = await get(application, "/")
    assert root.status_code == 200
    assert root.json()["name"] == "CurveVision"
