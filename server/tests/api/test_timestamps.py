"""Every timestamp is UTC, and says so, on both backends.

`DateTime(timezone=True)` means what it says on PostgreSQL and cannot on SQLite, which has
no time-zone type: the same column hands back an aware value from one and a naive value
from the other. `UTCDateTime` is where that difference is allowed to exist, alongside `GUID`
and `EnumString`, and these are the two ways it used to escape.

The quiet one is the reason this matters beyond tidiness. A naive timestamp serialises with
no offset, and `new Date('2026-09-21T08:29:23')` in a browser is **local** time, not UTC —
so the desktop shape, which is the SQLite one, showed every time shifted by the viewer's own
offset, and anything ordering timestamps as strings compared two different formats.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from sqlalchemy import select

from curvevision.domain.identity import User
from tests.conftest import ApiActor


async def test_a_timestamp_read_back_from_the_database_is_timezone_aware(
    owner: ApiActor, session: Any
) -> None:
    """The loud failure: a naive value cannot be compared with an aware `utcnow()`."""
    user = (await session.execute(select(User).limit(1))).scalars().first()
    assert user is not None
    assert user.created_at.tzinfo is not None
    assert user.created_at.utcoffset() == UTC.utcoffset(None)
    # Would raise `TypeError: can't compare offset-naive and offset-aware datetimes`.
    assert user.created_at <= datetime.now(UTC)


async def test_the_api_reports_the_same_format_whether_or_not_the_row_was_just_written(
    owner: ApiActor, organization: dict[str, Any]
) -> None:
    """The quiet failure: two formats for one instant, one of which reads as local time."""
    created = await owner.post(
        "/api/v1/projects",
        json={"organization_id": organization["id"], "slug": "stamped", "name": "Stamped"},
    )
    assert created.status_code == 201, created.text
    fresh = created.json()
    reread = (await owner.get(f"/api/v1/projects/{fresh['id']}")).json()

    assert fresh["created_at"] == reread["created_at"]
    for stamp in (fresh["created_at"], reread["created_at"]):
        # Either spelling of UTC is fine; a bare timestamp is not, because that is the one
        # a browser reads as local time.
        assert stamp.endswith("Z") or stamp.endswith("+00:00"), stamp
