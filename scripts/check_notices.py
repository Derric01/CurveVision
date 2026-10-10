#!/usr/bin/env python3
"""Verify that every declared dependency appears in docs/THIRD_PARTY_NOTICES.md.

A dependency that reaches users without an attribution entry is a licensing problem, not a
documentation nit, so CI fails on it. This checks *coverage*, not license text: it cannot
tell you a license is compatible, only that somebody wrote the entry down.

Run locally with: python scripts/check_notices.py
"""

from __future__ import annotations

import json
import re
import sys
import tomllib
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
NOTICES = ROOT / "docs" / "THIRD_PARTY_NOTICES.md"

#: Dependencies that legitimately need no entry. Keep this list short and justified.
EXEMPT = {
    # Development-only tooling never ships to a user.
    "pytest",
    "pytest-asyncio",
    "ruff",
    "mypy",
    "types-boto3",
    "aiosqlite",
    "vite",
    "vitest",
    "typescript",
    "eslint",
    "autoprefixer",
    "postcss",
    "@types/node",
    "@types/react",
    "@types/react-dom",
    "@types/rbush",
    # Generates web/src/api/schema.ts at development time; nothing of it ships.
    "openapi-typescript",
    "@typescript-eslint/eslint-plugin",
    "@typescript-eslint/parser",
    "@vitejs/plugin-react",
    "eslint-plugin-react-hooks",
    "tailwindcss",
}


def python_dependencies(pyproject: Path) -> set[str]:
    data = tomllib.loads(pyproject.read_text())
    project = data.get("project", {})
    names: set[str] = set()

    for requirement in project.get("dependencies", []):
        names.add(_package_name(requirement))
    for group in project.get("optional-dependencies", {}).values():
        for requirement in group:
            names.add(_package_name(requirement))
    return names


def _package_name(requirement: str) -> str:
    # "fastapi>=0.115" -> "fastapi"; "uvicorn[standard]>=0.30" -> "uvicorn"
    return re.split(r"[<>=!\[;\s]", requirement, maxsplit=1)[0].strip().lower()


def node_dependencies(package_json: Path) -> set[str]:
    data = json.loads(package_json.read_text())
    names: set[str] = set()
    for key in ("dependencies", "devDependencies"):
        names.update(data.get(key, {}))
    return names


def main() -> int:
    if not NOTICES.exists():
        print(f"error: {NOTICES.relative_to(ROOT)} is missing", file=sys.stderr)
        return 1

    notices = NOTICES.read_text().lower()

    declared: set[str] = set()
    declared |= python_dependencies(ROOT / "server" / "pyproject.toml")
    declared |= python_dependencies(ROOT / "sdk" / "python" / "pyproject.toml")
    declared |= node_dependencies(ROOT / "web" / "package.json")

    exempt = {name.lower() for name in EXEMPT}
    missing = sorted(
        name
        for name in declared
        if name.lower() not in exempt and name.lower() not in notices
    )

    if missing:
        print("Dependencies missing from docs/THIRD_PARTY_NOTICES.md:", file=sys.stderr)
        for name in missing:
            print(f"  - {name}", file=sys.stderr)
        print(
            "\nAdd an entry with the package name, its license and its copyright holder.",
            file=sys.stderr,
        )
        return 1

    print(f"All {len(declared)} declared dependencies are covered by the notices file.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
