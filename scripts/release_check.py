#!/usr/bin/env python3
"""Every version this repository declares matches the release being made.

    python scripts/release_check.py v1.0.0     # or 1.0.0

A release built with a stale version somewhere ships an installer whose About box, update
check (`updates.rs` compares versions) or Helm chart says something else. Run by
`.github/workflows/release.yml` before anything is built, so a mismatch stops the release
instead of reaching a download page.
"""

from __future__ import annotations

import json
import re
import sys
import tomllib
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def declared() -> dict[str, str]:
    def text(path: str) -> str:
        return (ROOT / path).read_text()

    def toml(path: str, *keys: str) -> str:
        value: object = tomllib.loads(text(path))
        for key in keys:
            value = value[key]  # type: ignore[index]
        return str(value)

    def package_json(path: str) -> str:
        return str(json.loads(text(path))["version"])

    def found(pattern: str, path: str) -> str:
        match = re.search(pattern, text(path), re.MULTILINE)
        return match[1] if match else "(not found)"

    chart = "deploy/helm/curvevision/Chart.yaml"
    version_line = r'__version__ = "([^"]+)"'
    return {
        "server/pyproject.toml": toml("server/pyproject.toml", "project", "version"),
        "server/curvevision/__init__.py": found(version_line, "server/curvevision/__init__.py"),
        "sdk/python/pyproject.toml": toml("sdk/python/pyproject.toml", "project", "version"),
        "sdk/python/curvevision_sdk/__init__.py": found(
            version_line, "sdk/python/curvevision_sdk/__init__.py"
        ),
        "sdk/python/curvevision_sdk/client.py (User-Agent)": found(
            r'curvevision-sdk/([^"]+)"', "sdk/python/curvevision_sdk/client.py"
        ),
        "web/package.json": package_json("web/package.json"),
        "sdk/typescript/package.json": package_json("sdk/typescript/package.json"),
        "desktop/shell/src-tauri/tauri.conf.json": package_json(
            "desktop/shell/src-tauri/tauri.conf.json"
        ),
        "desktop/shell/src-tauri/Cargo.toml": toml(
            "desktop/shell/src-tauri/Cargo.toml", "package", "version"
        ),
        f"{chart} (version)": found(r"^version: (\S+)", chart),
        f"{chart} (appVersion)": found(r'^appVersion: "?([^"\s]+)"?', chart),
    }


def main() -> int:
    if len(sys.argv) != 2:
        raise SystemExit(__doc__)
    wanted = sys.argv[1].removeprefix("v")
    wrong = {where: found for where, found in declared().items() if found != wanted}
    if wrong:
        for where, found in wrong.items():
            print(f"  {where}: {found}, not {wanted}", file=sys.stderr)
        print(f"{len(wrong)} version(s) do not match {sys.argv[1]}.", file=sys.stderr)
        return 1
    print(f"every declared version is {wanted}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
