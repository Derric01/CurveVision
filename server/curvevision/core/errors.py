"""Domain errors and their RFC 9457 ``application/problem+json`` representation.

Services raise these; the API layer translates them. A service never imports
``fastapi.HTTPException`` -- that is what keeps services callable from the CLI, the worker
and tests without an HTTP stack.
"""

from __future__ import annotations

from typing import Any


class CurveVisionError(Exception):
    """Base class for every error CurveVision raises deliberately."""

    status_code: int = 500
    error_type: str = "internal_error"
    title: str = "Internal server error"

    def __init__(self, detail: str | None = None, **extra: Any) -> None:
        super().__init__(detail or self.title)
        self.detail = detail or self.title
        self.extra = extra

    def to_problem(self, instance: str | None = None) -> dict[str, Any]:
        problem: dict[str, Any] = {
            "type": f"https://curvevision.dev/errors/{self.error_type}",
            "title": self.title,
            "status": self.status_code,
            "detail": self.detail,
        }
        if instance:
            problem["instance"] = instance
        problem.update(self.extra)
        return problem


class NotFoundError(CurveVisionError):
    status_code = 404
    error_type = "not_found"
    title = "Resource not found"


class PermissionDeniedError(CurveVisionError):
    status_code = 403
    error_type = "permission_denied"
    title = "Permission denied"


class AuthenticationError(CurveVisionError):
    status_code = 401
    error_type = "authentication_failed"
    title = "Authentication failed"


class ValidationError(CurveVisionError):
    status_code = 422
    error_type = "validation_error"
    title = "Validation error"


class ConflictError(CurveVisionError):
    status_code = 409
    error_type = "conflict"
    title = "Conflict"


class RateLimitedError(CurveVisionError):
    status_code = 429
    error_type = "rate_limited"
    title = "Too many requests"


class UnsupportedFormatError(CurveVisionError):
    status_code = 400
    error_type = "unsupported_format"
    title = "Unsupported dataset format"


class ProviderError(CurveVisionError):
    status_code = 502
    error_type = "provider_error"
    title = "Model provider error"
