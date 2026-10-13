#!/usr/bin/env python3
"""Use a CurveVision installed by the Helm chart, as a person would, through its web service.

    python scripts/helm_smoke.py http://127.0.0.1:8080

Run by CI's `helm-install` job against a `kind` cluster, through a port-forward to the web
service — so every request crosses the web pod's nginx, the API Service, the API pods, and
PostgreSQL, Redis and MinIO behind them. Register (the first account), make a workspace, a
project with a label, a task; upload an image and read its bytes back from object storage;
save an annotation; export the project. Exits non-zero, naming the step, on the first
failure.
"""

from __future__ import annotations

import io
import json
import struct
import sys
import time
import urllib.error
import urllib.request
import uuid
import zipfile
import zlib


def png(width: int = 48, height: int = 32) -> bytes:
    """A small solid PNG, without Pillow."""

    def chunk(kind: bytes, data: bytes) -> bytes:
        checksum = struct.pack(">I", zlib.crc32(kind + data))
        return struct.pack(">I", len(data)) + kind + data + checksum

    rows = b"".join(b"\x00" + b"\x2d\xd4\xbf" * width for _ in range(height))
    header = struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0)
    return (b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", header) + chunk(b"IDAT", zlib.compress(rows))
            + chunk(b"IEND", b""))


class Api:
    def __init__(self, base: str) -> None:
        self.base = base.rstrip("/")
        self.token: str | None = None

    def call(self, method: str, path: str, body: object = None, *, raw: bytes | None = None,
             content_type: str = "application/json") -> tuple[int, bytes]:
        data = raw if raw is not None else (json.dumps(body).encode() if body is not None else None)
        request = urllib.request.Request(f"{self.base}/api/v1{path}", data=data, method=method)
        if data is not None:
            request.add_header("Content-Type", content_type)
        if self.token:
            request.add_header("Authorization", f"Bearer {self.token}")
        try:
            with urllib.request.urlopen(request, timeout=60) as response:
                return int(response.status), response.read()
        except urllib.error.HTTPError as exc:
            return int(exc.code), exc.read()

    def json(self, method: str, path: str, body: object = None, expect: int = 200) -> object:
        status, payload = self.call(method, path, body)
        if status != expect:
            raise SystemExit(f"FAIL {method} {path}: {status} {payload[:500]!r}")
        return json.loads(payload) if payload else None


def main() -> int:
    base = sys.argv[1] if len(sys.argv) > 1 else "http://127.0.0.1:8080"
    api = Api(base)
    for _ in range(60):
        if api.call("GET", "/health")[0] == 200:
            break
        time.sleep(2)
    else:
        raise SystemExit("FAIL the API never answered its health check through the web service")
    print("ok   the API answers through the web service")

    password = "helm-smoke-password-1"
    api.json("POST", "/auth/register",
             {"email": "smoke@example.com", "username": "smoke", "password": password}, 201)
    tokens = api.json("POST", "/auth/login", {"identifier": "smoke", "password": password})
    api.token = tokens["access_token"]  # type: ignore[index]
    print("ok   registered and signed in")

    workspace = api.json("POST", "/organizations", {"slug": "smoke", "name": "Smoke"}, 201)
    project = api.json("POST", "/projects", {
        "organization_id": workspace["id"], "slug": "street", "name": "Street",  # type: ignore[index]
        "labels": [{"name": "car"}],
    }, 201)
    task = api.json("POST", "/tasks", {"project_id": project["id"], "name": "Clip"}, 201)  # type: ignore[index]
    print("ok   workspace, project and task created")

    image = png()
    boundary = uuid.uuid4().hex
    form = (f"--{boundary}\r\nContent-Disposition: form-data; name=\"files\"; "
            f"filename=\"street.png\"\r\nContent-Type: image/png\r\n\r\n").encode() + image + (
            f"\r\n--{boundary}--\r\n").encode()
    status, payload = api.call("POST", f"/tasks/{task['id']}/assets", raw=form,  # type: ignore[index]
                               content_type=f"multipart/form-data; boundary={boundary}")
    if status not in (200, 201):
        raise SystemExit(f"FAIL upload: {status} {payload[:500]!r}")
    status, served = api.call("GET", f"/tasks/{task['id']}/frames/0/data")  # type: ignore[index]
    if status != 200 or served != image:
        raise SystemExit(f"FAIL the uploaded image did not come back from storage ({status})")
    print("ok   an image uploaded to object storage comes back byte for byte")

    job = api.json("GET", f"/tasks/{task['id']}/jobs")[0]  # type: ignore[index]
    car = project["labels"][0]["id"]  # type: ignore[index]
    api.json("PATCH", f"/jobs/{job['id']}/annotations", {
        "created_shapes": [{"label_id": car, "frame": 0, "shape_type": "rectangle",
                            "points": [4, 4, 20, 20]}],
    })
    status, archive = api.call("POST", f"/projects/{project['id']}/export",  # type: ignore[index]
                               {"format": "coco"})
    if status != 200:
        raise SystemExit(f"FAIL export: {status} {archive[:500]!r}")
    with zipfile.ZipFile(io.BytesIO(archive)) as exported:
        names = exported.namelist()
        document = json.loads(exported.read(next(n for n in names if n.endswith(".json"))))
    if len(document.get("annotations", [])) != 1:
        raise SystemExit(f"FAIL the COCO export holds {document.get('annotations')}")
    print("ok   an annotation is saved and exported")
    print("\nevery check passed")
    return 0


if __name__ == "__main__":
    sys.exit(main())
