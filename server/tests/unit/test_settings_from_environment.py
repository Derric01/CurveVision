"""Settings read from the environment, as every deployment sets them.

The list-valued settings are documented, and set by `docker-compose.yml` and the Helm chart,
as comma-separated text (`CURVEVISION_CORS_ORIGINS=http://localhost:8080`). pydantic-settings
decodes a list-typed variable as JSON before any validator sees it, so until iteration 85 each
of those deployments failed at start: the first CI job ever to run `docker compose up` found
it. The tests here set the variables the way the deployments do.
"""

from __future__ import annotations

import pytest

from curvevision.core.config import Settings


@pytest.mark.parametrize(
    ("variable", "field", "value", "expected"),
    [
        (
            "CURVEVISION_CORS_ORIGINS",
            "cors_origins",
            "http://localhost:8080",
            ("http://localhost:8080",),
        ),
        (
            "CURVEVISION_CORS_ORIGINS",
            "cors_origins",
            "https://a.example, https://b.example",
            ("https://a.example", "https://b.example"),
        ),
        ("CURVEVISION_CORS_ORIGINS", "cors_origins", "", ()),
        (
            "CURVEVISION_OIDC_ALLOWED_DOMAINS",
            "oidc_allowed_domains",
            "example.com,example.org",
            ("example.com", "example.org"),
        ),
        (
            "CURVEVISION_ALLOWED_IMAGE_EXTENSIONS",
            "allowed_image_extensions",
            ".jpg,.png",
            (".jpg", ".png"),
        ),
        (
            "CURVEVISION_ALLOWED_VIDEO_EXTENSIONS",
            "allowed_video_extensions",
            ".mp4",
            (".mp4",),
        ),
    ],
)
def test_a_comma_separated_list_is_read(
    monkeypatch: pytest.MonkeyPatch, variable: str, field: str, value: str, expected: tuple
) -> None:
    monkeypatch.setenv(variable, value)
    assert getattr(Settings(), field) == expected


def test_a_json_list_still_is(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("CURVEVISION_CORS_ORIGINS", '["https://a.example","https://b.example"]')
    assert Settings().cors_origins == ("https://a.example", "https://b.example")
