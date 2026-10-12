"""The migration that repairs skeleton joints stored before iteration 60.

A joint is stored as `{label_id, points, occluded, outside}`, `label_id` one of its skeleton
label's joints. Before iteration 60 three paths stored something else, and those rows are
still in any database that took one:

* a COCO import stored each joint by **name**, with no `label_id`;
* a native-format import carried **another project's** joint ids;
* an import that created its skeleton label created it **with no joints at all**.

The test writes rows those ways, on a real migrated installation, then runs the migration
the way an upgrade does — down past it and up again — and reads the result back through the
API, which since this migration types every joint and would refuse anything else.
"""

from __future__ import annotations

import asyncio
import io
import os
import uuid
from pathlib import Path
from typing import Any

import httpx
import pytest
import sqlalchemy as sa

from curvevision import desktop
from curvevision.core import db as db_module
from curvevision.core.config import configure_settings
from curvevision.core.types import GUID, JSONDocument

pytest.importorskip("PIL", reason="frames need real images")

#: A PostgreSQL database this test may wipe, e.g.
#: `postgresql+asyncpg://curvevision:curvevision@localhost:5432/curvevision_test`. CI sets
#: it; without it only SQLite runs. Native `uuid` and `JSONB` are where a data migration
#: written against SQLite's strings breaks.
POSTGRES = os.environ.get("CURVEVISION_TEST_POSTGRES_URL")

_shapes = sa.table(
    "shapes",
    sa.column("id", GUID()),
    sa.column("label_id", GUID()),
    sa.column("elements", JSONDocument),
)
_track_shapes = sa.table(
    "track_shapes", sa.column("track_id", GUID()), sa.column("elements", JSONDocument)
)

#: The revision before the repair: migrating down to here and back up runs it.
BEFORE_REPAIR = "b5e0c72f18d4"


def png() -> bytes:
    from PIL import Image

    buffer = io.BytesIO()
    Image.new("RGB", (64, 48), "#334155").save(buffer, format="PNG")
    return buffer.getvalue()


def sync_engine(settings: Any) -> Any:
    url = sa.make_url(settings.database_url)
    driver = "postgresql+psycopg" if url.drivername.startswith("postgresql") else "sqlite"
    return sa.create_engine(url.set(drivername=driver))


@pytest.fixture(params=["sqlite", "postgresql"])
async def installation(request: pytest.FixtureRequest, tmp_path: Path) -> Any:
    """A migrated, provisioned installation on each database the server supports."""
    if request.param == "sqlite":
        settings, token = await asyncio.to_thread(desktop.bootstrap, tmp_path / "CurveVision")
    else:
        if not POSTGRES:
            pytest.skip("set CURVEVISION_TEST_POSTGRES_URL to run against PostgreSQL")
        settings = desktop.local_settings(tmp_path / "CurveVision").model_copy(
            update={"database_url": POSTGRES}
        )
        engine = sync_engine(settings)
        with engine.begin() as connection:
            connection.execute(sa.text("DROP SCHEMA public CASCADE"))
            connection.execute(sa.text("CREATE SCHEMA public"))
        engine.dispose()
        configure_settings(settings)
        await asyncio.to_thread(desktop.migrate, settings)
        token = await desktop.provision(settings)
    yield settings, token
    await db_module.dispose_engine()
    configure_settings(None)


async def seed(client: httpx.AsyncClient) -> dict[str, Any]:
    """A project with a skeleton label and a jointless one, and skeletons written properly."""
    workspace = (await client.get("/api/v1/organizations")).json()[0]
    project = (
        await client.post(
            "/api/v1/projects",
            json={
                "organization_id": workspace["id"],
                "slug": "poses",
                "name": "Poses",
                "labels": [
                    {"name": "person", "children": [{"name": "nose"}, {"name": "tail"}]},
                    {"name": "animal"},
                ],
            },
        )
    ).json()
    person = next(label for label in project["labels"] if label["name"] == "person")
    nose, tail = (child["id"] for child in person["children"])
    task = (
        await client.post("/api/v1/tasks", json={"project_id": project["id"], "name": "Clip"})
    ).json()
    await client.post(
        f"/api/v1/tasks/{task['id']}/assets",
        files=[("files", (f"{i}.png", png(), "image/png")) for i in range(2)],
    )
    job = (await client.get(f"/api/v1/tasks/{task['id']}/jobs")).json()[0]

    def skeleton(frame: int, nose_at: list[float], tail_at: list[float]) -> dict[str, Any]:
        return {
            "label_id": person["id"],
            "frame": frame,
            "shape_type": "skeleton",
            "points": [],
            "elements": [
                {"label_id": nose, "points": nose_at},
                {"label_id": tail, "points": tail_at},
            ],
        }

    written = await client.patch(
        f"/api/v1/jobs/{job['id']}/annotations",
        json={
            "created_shapes": [
                skeleton(0, [1, 1], [2, 2]),  # becomes a by-name row
                skeleton(0, [3, 3], [4, 4]),  # becomes a jointless label's row
                skeleton(1, [5, 5], [6, 6]),  # stays as it is
            ],
            "created_tracks": [
                {
                    "label_id": person["id"],
                    "shape_type": "skeleton",
                    "shapes": [
                        {k: v for k, v in skeleton(0, [7, 7], [8, 8]).items() if k != "label_id"},
                    ],
                }
            ],
        },
    )
    assert written.status_code == 200, written.text
    document = (await client.get(f"/api/v1/jobs/{job['id']}/annotations")).json()
    return {"project": project, "job": job, "nose": nose, "tail": tail, "document": document}


async def test_old_joints_are_repaired_and_read_back_typed(installation: Any) -> None:
    from alembic import command

    from curvevision.main import create_app

    settings, token = installation
    app = create_app(settings)
    transport = httpx.ASGITransport(app=app)
    headers = {"Authorization": f"Bearer {token}"}
    async with (
        httpx.AsyncClient(transport=transport, base_url="http://desktop", headers=headers) as c,
        app.router.lifespan_context(app),
    ):
        seeded = await seed(c)
    await db_module.dispose_engine()

    by_name, jointless, untouched = sorted(
        seeded["document"]["shapes"], key=lambda s: (s["frame"], s["elements"][0]["points"])
    )
    [track] = seeded["document"]["tracks"]
    animal = next(label for label in seeded["project"]["labels"] if label["name"] == "animal")
    foreign = [str(uuid.uuid4()), str(uuid.uuid4())]

    # --- the rows as the old paths wrote them
    engine = sync_engine(settings)
    with engine.begin() as connection:
        # By name, out of order, the second joint occluded.
        connection.execute(
            _shapes.update()
            .where(_shapes.c.id == uuid.UUID(by_name["id"]))
            .values(
                elements=[
                    {"name": "tail", "points": [20.0, 21.0]},
                    {"name": "nose", "points": [10.0, 11.0], "occluded": True},
                ]
            )
        )
        # On a label with no joints.
        connection.execute(
            _shapes.update()
            .where(_shapes.c.id == uuid.UUID(jointless["id"]))
            .values(
                label_id=uuid.UUID(animal["id"]),
                elements=[
                    {"name": "head", "points": [30.0, 31.0]},
                    {"name": "paw", "points": [0.0, 0.0], "outside": True},
                ],
            )
        )
        # Another project's ids, no names: matched by position.
        connection.execute(
            _track_shapes.update()
            .where(_track_shapes.c.track_id == uuid.UUID(track["id"]))
            .values(
                elements=[
                    {"label_id": foreign[0], "points": [70.0, 71.0]},
                    {"label_id": foreign[1], "points": [80.0, 81.0]},
                ]
            )
        )
    engine.dispose()

    # --- migrate down past the repair and up again, as an upgrade runs it
    config = desktop.alembic_config(settings)
    await asyncio.to_thread(command.downgrade, config, BEFORE_REPAIR)
    await asyncio.to_thread(command.upgrade, config, "head")
    db_module.configure_engine(settings)

    app = create_app(settings)
    transport = httpx.ASGITransport(app=app)
    async with (
        httpx.AsyncClient(transport=transport, base_url="http://desktop", headers=headers) as c,
        app.router.lifespan_context(app),
    ):
        document = (await c.get(f"/api/v1/jobs/{seeded['job']['id']}/annotations")).json()
        labels = (await c.get(f"/api/v1/projects/{seeded['project']['id']}/labels")).json()
    await db_module.dispose_engine()

    nose, tail = seeded["nose"], seeded["tail"]
    shapes = {shape["id"]: shape for shape in document["shapes"]}

    assert shapes[by_name["id"]]["elements"] == [
        {"label_id": nose, "points": [10.0, 11.0], "occluded": True, "outside": False},
        {"label_id": tail, "points": [20.0, 21.0], "occluded": False, "outside": False},
    ]

    # The jointless label gains the joints its skeletons named, in the order they named
    # them, and the skeleton is stored against them — nothing deleted, nothing guessed.
    animal_after = next(label for label in labels if label["name"] == "animal")
    head, paw = ([c["name"], c["id"]] for c in animal_after["children"])
    assert [head[0], paw[0]] == ["head", "paw"]
    assert shapes[jointless["id"]]["elements"] == [
        {"label_id": head[1], "points": [30.0, 31.0], "occluded": False, "outside": False},
        {"label_id": paw[1], "points": [0.0, 0.0], "occluded": False, "outside": True},
    ]

    [repaired_track] = document["tracks"]
    assert [(e["label_id"], e["points"]) for e in repaired_track["shapes"][0]["elements"]] == [
        (nose, [70.0, 71.0]),
        (tail, [80.0, 81.0]),
    ]

    assert shapes[untouched["id"]]["elements"] == untouched["elements"]
