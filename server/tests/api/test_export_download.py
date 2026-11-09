"""An export too large to buffer: run in the background, downloaded by a signed link.

The project page fetched every export into the page before saving it, and an export with
10,000 photographs is tens of gigabytes. Now an export with images runs as a background
task, and the browser's own download manager streams it from a short-lived signed link,
which it can follow without an auth header.
"""

from __future__ import annotations

import io
import time
import uuid
import zipfile
from typing import Any

import pytest

from curvevision.core.signing import sign
from tests.conftest import ApiActor, register

Image = pytest.importorskip("PIL.Image")


async def with_images(owner: ApiActor, project: dict[str, Any], count: int = 3) -> None:
    task = (
        await owner.post("/api/v1/tasks", json={"project_id": project["id"], "name": "Photos"})
    ).json()
    files = []
    for index in range(count):
        buffer = io.BytesIO()
        Image.new("RGB", (32, 24), (index * 60, 30, 90)).save(buffer, format="PNG")
        files.append(("files", (f"p{index}.png", buffer.getvalue(), "image/png")))
    assert (await owner.post(f"/api/v1/tasks/{task['id']}/assets", files=files)).is_success


async def finished_export(owner: ApiActor, project: dict[str, Any]) -> dict[str, Any]:
    started = await owner.post(
        f"/api/v1/projects/{project['id']}/exports",
        json={"format": "coco", "include_images": True},
    )
    assert started.status_code == 202, started.text
    task = (await owner.get(f"/api/v1/background-tasks/{started.json()['id']}")).json()
    assert task["state"] == "succeeded", task
    return dict(task)


async def test_an_export_is_downloaded_whole_from_a_link_that_needs_no_auth_header(
    owner: ApiActor, project: dict[str, Any]
) -> None:
    await with_images(owner, project)
    export = await finished_export(owner, project)

    link = await owner.post(f"/api/v1/exports/{export['id']}/link")
    assert link.status_code == 200, link.text
    # The browser follows it as it is: no Authorization header.
    download = await owner.client.get(link.json()["url"])

    assert download.status_code == 200, download.text
    assert f'filename="{project["slug"]}-coco.zip"' in download.headers["content-disposition"]
    assert int(download.headers["content-length"]) == len(download.content)
    with zipfile.ZipFile(io.BytesIO(download.content)) as archive:
        names = archive.namelist()
    assert "annotations/instances_default.json" in names
    assert sum(name.endswith(".png") for name in names) == 3


async def test_a_link_that_was_altered_or_has_expired_opens_nothing(
    owner: ApiActor, project: dict[str, Any], settings: Any
) -> None:
    export = await finished_export(owner, project)
    url = (await owner.post(f"/api/v1/exports/{export['id']}/link")).json()["url"]

    tampered = await owner.client.get(url[:-4] + "0000")
    assert tampered.status_code == 403

    past = int(time.time()) - 1
    expired = sign(settings.secret_key, "export-download", export["id"], past)
    stale = await owner.client.get(
        f"/api/v1/exports/{export['id']}/archive?expires={past}&signature={expired}"
    )
    assert stale.status_code == 403

    # A signature for one export opens no other.
    other = str(uuid.uuid4())
    query = url.split("?", 1)[1]
    assert (await owner.client.get(f"/api/v1/exports/{other}/archive?{query}")).status_code == 403


async def test_only_the_person_who_ran_an_export_can_have_a_link_to_it(
    owner: ApiActor, project: dict[str, Any]
) -> None:
    export = await finished_export(owner, project)
    stranger = await register(owner.client, "stranger")

    refused = await stranger.post(f"/api/v1/exports/{export['id']}/link")

    assert refused.status_code == 403


async def test_a_task_that_is_not_an_export_has_no_link(
    owner: ApiActor, project: dict[str, Any]
) -> None:
    assert (await owner.post(f"/api/v1/exports/{uuid.uuid4()}/link")).status_code == 404
