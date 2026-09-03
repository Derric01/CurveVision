"""The CurveVision Python client.

Design notes:

* **Synchronous.** Scripts, notebooks and CI are the SDK's audience, and they are
  overwhelmingly synchronous. An async variant can wrap the same resource classes later.
* **Typed dataclasses, not dicts.** ``project.id`` beats ``project["id"]`` in an editor,
  and a renamed field then fails at the boundary instead of three functions later.
* **Unknown fields are preserved**, so an SDK built against an older server keeps working
  when the API grows: extra keys land in ``.raw``.
* **Errors carry the server's problem document**, because "422" alone never told anyone
  what was wrong.
"""

from __future__ import annotations

import uuid
from collections.abc import Iterator
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import httpx

DEFAULT_TIMEOUT = 60.0
USER_AGENT = "curvevision-sdk/0.1.0"


class CurveVisionError(Exception):
    """An error returned by the CurveVision API."""

    def __init__(self, status_code: int, problem: dict[str, Any]) -> None:
        detail = problem.get("detail") or problem.get("title") or "Request failed"
        super().__init__(f"[{status_code}] {detail}")
        self.status_code = status_code
        self.problem = problem
        self.detail = detail

    @property
    def errors(self) -> list[dict[str, Any]]:
        """Field-level validation errors, when the server sent any."""
        return list(self.problem.get("errors", []))


@dataclass(slots=True)
class Resource:
    """Base for API objects: typed access to known fields, ``raw`` for everything else."""

    id: uuid.UUID
    raw: dict[str, Any] = field(repr=False, default_factory=dict)

    @classmethod
    def _from(cls, payload: dict[str, Any], **kwargs: Any) -> Any:
        return cls(id=uuid.UUID(str(payload["id"])), raw=payload, **kwargs)

    def __getitem__(self, key: str) -> Any:
        return self.raw[key]


@dataclass(slots=True)
class Organization(Resource):
    slug: str = ""
    name: str = ""


@dataclass(slots=True)
class Project(Resource):
    slug: str = ""
    name: str = ""
    organization_id: uuid.UUID | None = None

    @property
    def labels(self) -> list[dict[str, Any]]:
        return list(self.raw.get("labels", []))

    def label_id(self, name: str) -> uuid.UUID:
        """Look a label up by name -- what scripts actually have to hand."""
        for label in self.labels:
            if label["name"] == name:
                return uuid.UUID(str(label["id"]))
        available = ", ".join(sorted(label["name"] for label in self.labels))
        raise KeyError(f"No label named {name!r} in this project. Available: {available}")


@dataclass(slots=True)
class Task(Resource):
    name: str = ""
    status: str = ""
    frame_count: int = 0
    project_id: uuid.UUID | None = None


@dataclass(slots=True)
class Job(Resource):
    index: int = 0
    state: str = ""
    start_frame: int = 0
    stop_frame: int = 0
    annotation_version: int = 0


class CurveVision:
    """A CurveVision API client.

    ```python
    from curvevision_sdk import CurveVision

    cv = CurveVision("https://curvevision.example.com", token="cv_...")
    project = cv.create_project(org.id, slug="street", name="Street Scenes",
                                labels=[{"name": "car"}])
    task = cv.create_task(project.id, name="Batch 1")
    cv.upload(task.id, ["a.jpg", "b.jpg"])
    ```
    """

    def __init__(
        self,
        base_url: str,
        *,
        token: str | None = None,
        timeout: float = DEFAULT_TIMEOUT,
        transport: httpx.BaseTransport | None = None,
        client: httpx.Client | None = None,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        headers = {"user-agent": USER_AGENT}
        if token:
            headers["authorization"] = f"Bearer {token}"
        self._client = client or httpx.Client(
            base_url=self.base_url, headers=headers, timeout=timeout, transport=transport
        )
        self._owns_client = client is None

    # ------------------------------------------------------------------ plumbing

    def _request(self, method: str, path: str, **kwargs: Any) -> httpx.Response:
        response = self._client.request(method, f"/api/v1{path}", **kwargs)
        if response.status_code >= 400:
            try:
                problem = response.json()
            except ValueError:
                problem = {"detail": response.text[:500]}
            raise CurveVisionError(response.status_code, problem)
        return response

    def _json(self, method: str, path: str, **kwargs: Any) -> Any:
        response = self._request(method, path, **kwargs)
        return None if response.status_code == 204 else response.json()

    def close(self) -> None:
        if self._owns_client:
            self._client.close()

    def __enter__(self) -> CurveVision:
        return self

    def __exit__(self, *_exc: object) -> None:
        self.close()

    # ------------------------------------------------------------------------ auth

    @classmethod
    def login(
        cls,
        base_url: str,
        identifier: str,
        password: str,
        *,
        transport: httpx.BaseTransport | None = None,
    ) -> CurveVision:
        """Authenticate with a password and return a ready client.

        Prefer an API token for anything unattended: tokens are revocable individually and
        do not expire in the middle of a long job.
        """
        client = cls(base_url, transport=transport)
        tokens = client._json(
            "POST", "/auth/login", json={"identifier": identifier, "password": password}
        )
        client._client.headers["authorization"] = f"Bearer {tokens['access_token']}"
        return client

    def whoami(self) -> dict[str, Any]:
        return dict(self._json("GET", "/auth/me"))

    def create_token(self, name: str) -> str:
        """Mint an API token. The plaintext is returned once and is not recoverable."""
        return str(self._json("POST", "/auth/tokens", json={"name": name})["token"])

    def health(self) -> dict[str, Any]:
        return dict(self._json("GET", "/health"))

    # ---------------------------------------------------------------- organizations

    def organizations(self) -> list[Organization]:
        return [
            Organization._from(item, slug=item["slug"], name=item["name"])
            for item in self._json("GET", "/organizations")
        ]

    def create_organization(self, slug: str, name: str, **kwargs: Any) -> Organization:
        payload = self._json("POST", "/organizations", json={"slug": slug, "name": name, **kwargs})
        return Organization._from(payload, slug=payload["slug"], name=payload["name"])

    def add_member(self, organization_id: uuid.UUID, identifier: str, role: str) -> dict[str, Any]:
        return dict(
            self._json(
                "POST",
                f"/organizations/{organization_id}/members",
                json={"identifier": identifier, "role": role},
            )
        )

    # --------------------------------------------------------------------- projects

    def projects(self, **params: Any) -> list[Project]:
        page = self._json("GET", "/projects", params=params)
        return [self._project(item) for item in page["results"]]

    def project(self, project_id: uuid.UUID) -> Project:
        return self._project(self._json("GET", f"/projects/{project_id}"))

    def create_project(
        self,
        organization_id: uuid.UUID,
        *,
        slug: str,
        name: str,
        labels: list[dict[str, Any]] | None = None,
        **kwargs: Any,
    ) -> Project:
        payload = self._json(
            "POST",
            "/projects",
            json={
                "organization_id": str(organization_id),
                "slug": slug,
                "name": name,
                "labels": labels or [],
                **kwargs,
            },
        )
        return self._project(payload)

    def project_statistics(self, project_id: uuid.UUID) -> dict[str, Any]:
        return dict(self._json("GET", f"/projects/{project_id}/statistics"))

    @staticmethod
    def _project(payload: dict[str, Any]) -> Project:
        return Project._from(
            payload,
            slug=payload["slug"],
            name=payload["name"],
            organization_id=uuid.UUID(str(payload["organization_id"])),
        )

    # ------------------------------------------------------------------------ tasks

    def tasks(self, **params: Any) -> list[Task]:
        page = self._json("GET", "/tasks", params=params)
        return [self._task(item) for item in page["results"]]

    def task(self, task_id: uuid.UUID) -> Task:
        return self._task(self._json("GET", f"/tasks/{task_id}"))

    def create_task(
        self, project_id: uuid.UUID, *, name: str, segment_size: int = 0, **kwargs: Any
    ) -> Task:
        payload = self._json(
            "POST",
            "/tasks",
            json={
                "project_id": str(project_id),
                "name": name,
                "segment_size": segment_size,
                **kwargs,
            },
        )
        return self._task(payload)

    def upload(
        self, task_id: uuid.UUID, paths: list[str | Path], *, batch_size: int = 20
    ) -> list[dict[str, Any]]:
        """Upload media files to a task.

        Batched rather than one request per file: a 5,000-image dataset would otherwise be
        5,000 round trips, and batching is the difference between seconds and minutes.
        """
        uploaded: list[dict[str, Any]] = []
        files = [Path(path) for path in paths]
        for start in range(0, len(files), batch_size):
            batch = files[start : start + batch_size]
            handles = [("files", (path.name, path.read_bytes())) for path in batch]
            uploaded.extend(self._json("POST", f"/tasks/{task_id}/assets", files=handles))
        return uploaded

    def task_progress(self, task_id: uuid.UUID) -> dict[str, Any]:
        return dict(self._json("GET", f"/tasks/{task_id}/progress"))

    @staticmethod
    def _task(payload: dict[str, Any]) -> Task:
        return Task._from(
            payload,
            name=payload["name"],
            status=payload["status"],
            frame_count=payload["frame_count"],
            project_id=uuid.UUID(str(payload["project_id"])),
        )

    # ------------------------------------------------------------------------- jobs

    def jobs(self, task_id: uuid.UUID | None = None, **params: Any) -> list[Job]:
        if task_id is not None:
            items = self._json("GET", f"/tasks/{task_id}/jobs")
        else:
            items = self._json("GET", "/jobs", params=params)["results"]
        return [self._job(item) for item in items]

    def job(self, job_id: uuid.UUID) -> Job:
        return self._job(self._json("GET", f"/jobs/{job_id}"))

    def update_job(self, job_id: uuid.UUID, **changes: Any) -> Job:
        return self._job(self._json("PATCH", f"/jobs/{job_id}", json=changes))

    def review_job(self, job_id: uuid.UUID, *, accepted: bool, comment: str | None = None) -> Job:
        return self._job(
            self._json(
                "POST",
                f"/jobs/{job_id}/review",
                json={"accepted": accepted, "comment": comment},
            )
        )

    @staticmethod
    def _job(payload: dict[str, Any]) -> Job:
        return Job._from(
            payload,
            index=payload["index"],
            state=payload["state"],
            start_frame=payload["start_frame"],
            stop_frame=payload["stop_frame"],
            annotation_version=payload["annotation_version"],
        )

    # ------------------------------------------------------------------ annotations

    def annotations(self, job_id: uuid.UUID, **params: Any) -> dict[str, Any]:
        return dict(self._json("GET", f"/jobs/{job_id}/annotations", params=params))

    def write_annotations(self, job_id: uuid.UUID, **batch: Any) -> dict[str, Any]:
        """Apply one annotation batch.

        Pass ``annotation_version`` to get optimistic-concurrency protection; omit it to
        write unconditionally (appropriate for a script that owns the job).
        """
        return dict(self._json("PATCH", f"/jobs/{job_id}/annotations", json=batch))

    def create_shapes(
        self, job_id: uuid.UUID, shapes: list[dict[str, Any]], **kwargs: Any
    ) -> dict[str, Any]:
        return self.write_annotations(job_id, created_shapes=shapes, **kwargs)

    def frame_annotations(self, job_id: uuid.UUID, frame: int) -> dict[str, Any]:
        """Everything visible on one frame, with tracks interpolated server-side."""
        return dict(self._json("GET", f"/jobs/{job_id}/frames/{frame}/annotations"))

    def clear_annotations(self, job_id: uuid.UUID) -> None:
        self._json("DELETE", f"/jobs/{job_id}/annotations")

    # ----------------------------------------------------------------- import/export

    def formats(self) -> list[dict[str, Any]]:
        """Available dataset formats, each declaring what it can and cannot represent."""
        return list(self._json("GET", "/formats"))

    def export(
        self,
        project_id: uuid.UUID,
        *,
        format: str = "coco",
        destination: str | Path | None = None,
        **options: Any,
    ) -> bytes:
        """Export a project's annotations. Returns the archive; optionally writes it out."""
        response = self._request(
            "POST", f"/projects/{project_id}/export", json={"format": format, **options}
        )
        if warning := response.headers.get("x-curvevision-warnings"):
            # Surfaced rather than swallowed: this is where "my dataset is missing
            # annotations" is diagnosed.
            import warnings as _warnings

            _warnings.warn(f"CurveVision export: {warning}", stacklevel=2)
        if destination is not None:
            Path(destination).write_bytes(response.content)
        return response.content

    def import_annotations(
        self,
        task_id: uuid.UUID,
        archive: str | Path | bytes,
        *,
        format: str = "coco",
        conflict_policy: str = "append",
        create_missing_labels: bool = True,
    ) -> dict[str, Any]:
        data = archive if isinstance(archive, bytes) else Path(archive).read_bytes()
        return dict(
            self._json(
                "POST",
                f"/tasks/{task_id}/import",
                params={
                    "format": format,
                    "conflict_policy": conflict_policy,
                    "create_missing_labels": create_missing_labels,
                },
                files={"file": ("import.zip", data, "application/zip")},
            )
        )

    # --------------------------------------------------------------------- datasets

    def dataset_versions(self, project_id: uuid.UUID) -> list[dict[str, Any]]:
        return list(self._json("GET", f"/projects/{project_id}/versions"))

    def create_dataset_version(
        self, project_id: uuid.UUID, name: str, **kwargs: Any
    ) -> dict[str, Any]:
        return dict(
            self._json("POST", f"/projects/{project_id}/versions", json={"name": name, **kwargs})
        )

    def release_dataset_version(
        self, project_id: uuid.UUID, version_id: uuid.UUID
    ) -> dict[str, Any]:
        return dict(self._json("POST", f"/projects/{project_id}/versions/{version_id}/release"))

    # ---------------------------------------------------------------------- models

    def models(self, **params: Any) -> list[dict[str, Any]]:
        return list(self._json("GET", "/models", params=params))

    def register_model(
        self, organization_id: uuid.UUID, *, slug: str, name: str, kind: str, **kwargs: Any
    ) -> dict[str, Any]:
        return dict(
            self._json(
                "POST",
                "/models",
                params={"organization_id": str(organization_id)},
                json={"slug": slug, "name": name, "kind": kind, **kwargs},
            )
        )

    def run_inference(
        self, job_id: uuid.UUID, model_id: uuid.UUID, **options: Any
    ) -> dict[str, Any]:
        return dict(
            self._json(
                "POST",
                f"/jobs/{job_id}/inference",
                json={"model_id": str(model_id), "job_id": str(job_id), **options},
            )
        )

    def decide_suggestions(
        self, job_id: uuid.UUID, *, accepted: bool, **ids: Any
    ) -> dict[str, int]:
        return dict(
            self._json("POST", f"/jobs/{job_id}/suggestions", json={"accepted": accepted, **ids})
        )

    # -------------------------------------------------------------------- iteration

    def iter_pages(self, path: str, **params: Any) -> Iterator[dict[str, Any]]:
        """Walk every page of a listing endpoint.

        Exists so callers never hand-roll offset arithmetic, which is where "we only
        processed the first 50" bugs come from.
        """
        offset = 0
        limit = int(params.pop("limit", 100))
        while True:
            page = self._json("GET", path, params={**params, "limit": limit, "offset": offset})
            yield from page["results"]
            offset += len(page["results"])
            if offset >= page["count"] or not page["results"]:
                return
