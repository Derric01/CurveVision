"""Offset pagination with a hard cap.

Every list endpoint returns the same envelope so the SDK and the frontend have exactly one
listing shape to handle.
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Annotated, Any, Generic, TypeVar

from fastapi import Depends, Query
from pydantic import BaseModel, Field
from sqlalchemy import Select, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from curvevision.core.config import Settings, get_settings

T = TypeVar("T")


class PageParams(BaseModel):
    limit: int = Field(default=50, ge=1, le=500)
    offset: int = Field(default=0, ge=0)


def page_params(
    settings: Annotated[Settings, Depends(get_settings)],
    limit: int | None = Query(default=None, ge=1, description="Items per page"),
    offset: int = Query(default=0, ge=0),
) -> PageParams:
    effective = min(limit or settings.default_page_size, settings.max_page_size)
    return PageParams(limit=effective, offset=offset)


PageParamsDep = Annotated[PageParams, Depends(page_params)]


class Page(BaseModel, Generic[T]):
    count: int = Field(description="Total number of matching items")
    limit: int
    offset: int
    results: list[T]

    @property
    def has_more(self) -> bool:
        return self.offset + len(self.results) < self.count


async def paginate(
    session: AsyncSession,
    statement: Select[Any],
    params: PageParams,
) -> tuple[int, Sequence[Any]]:
    """Return ``(total, rows)`` for ``statement`` under ``params``.

    The count query strips ordering and eager-load options, which otherwise make Postgres
    plan a far more expensive query than counting requires.
    """
    count_stmt = select(func.count()).select_from(statement.order_by(None).options().subquery())
    total = int((await session.execute(count_stmt)).scalar_one())
    rows = (
        (await session.execute(statement.limit(params.limit).offset(params.offset))).scalars().all()
    )
    return total, rows
