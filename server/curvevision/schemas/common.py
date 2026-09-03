"""Shared schema plumbing."""

from __future__ import annotations

import re
from typing import Annotated

from pydantic import AfterValidator, BaseModel, ConfigDict

_SLUG_RE = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*$")


def _check_slug(value: str) -> str:
    if not _SLUG_RE.match(value):
        raise ValueError("must be lowercase alphanumeric words separated by single hyphens")
    return value


Slug = Annotated[str, AfterValidator(_check_slug)]


class ORMModel(BaseModel):
    """Response model read directly from a SQLAlchemy row."""

    model_config = ConfigDict(from_attributes=True)


class StrictModel(BaseModel):
    """Request model that rejects unknown fields.

    Silently ignoring a misspelled field is how clients ship bugs that look like server
    bugs, so request bodies are strict.
    """

    model_config = ConfigDict(extra="forbid")
