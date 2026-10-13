#!/usr/bin/env python3
"""Run a command against a real CurveVision server, started for it and stopped after.

    python scripts/with_server.py -- npm --prefix sdk/typescript test

The server shape on SQLite (`verify_workspace.start_web_server`), with one registered
account. The command gets `CURVEVISION_TEST_SERVER_URL` and `CURVEVISION_TEST_TOKEN` (an
API token of that account) in its environment, and this script exits with its status.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

from verify_workspace import start_web_server


def post(base: str, path: str, payload: object, token: str | None = None) -> dict:
    request = urllib.request.Request(
        f"{base}/api/v1{path}", data=json.dumps(payload).encode(), method="POST",
        headers={"Content-Type": "application/json",
                 **({"Authorization": f"Bearer {token}"} if token else {})},
    )
    with urllib.request.urlopen(request, timeout=30) as response:
        return dict(json.loads(response.read()))


def main() -> int:
    command = sys.argv[sys.argv.index("--") + 1:] if "--" in sys.argv else sys.argv[1:]
    if not command:
        print(__doc__)
        return 2
    with tempfile.TemporaryDirectory(prefix="curvevision-server-") as workdir:
        # The API alone: a client's tests need no web application, which need not be built.
        process, base = start_web_server(Path(workdir), extra_env={"CURVEVISION_WEB_ROOT": ""})
        try:
            password = "with-server-password-1"
            post(base, "/auth/register",
                 {"email": "sdk@example.com", "username": "sdk", "password": password})
            session = post(base, "/auth/login", {"identifier": "sdk", "password": password})
            minted = post(base, "/auth/tokens", {"name": "with_server"},
                          token=session["access_token"])
            post(base, "/organizations", {"slug": "sdk", "name": "SDK"},
                 token=session["access_token"])
            env = {**os.environ, "CURVEVISION_TEST_SERVER_URL": base,
                   "CURVEVISION_TEST_TOKEN": str(minted["token"])}
            return subprocess.run(command, env=env, check=False).returncode
        finally:
            process.terminate()
            process.wait(timeout=20)


if __name__ == "__main__":
    sys.exit(main())
