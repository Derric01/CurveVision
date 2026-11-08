#!/usr/bin/env python3
"""Every dependency the server declares has an exact version in server/constraints.txt.

    python scripts/check_constraints.py

Installs that ship or are tested use the constraints file (scripts/update_constraints.sh
says why). A dependency added to pyproject.toml without regenerating it would install at
whatever version is newest that day, which is the failure the file exists to prevent, so
`./scripts/check.sh` and CI refuse it. The `datumaro` extra is deliberately unpinned.
"""

from __future__ import annotations

import re
import sys
import tomllib
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
UNPINNED_EXTRAS = {"datumaro"}


def canonical(name: str) -> str:
    return re.sub(r"[-_.]+", "-", name).lower()


def requirement_name(requirement: str) -> str:
    return canonical(re.split(r"[\s\[<>=!~;]", requirement.strip(), maxsplit=1)[0])


def main() -> int:
    project = tomllib.loads((ROOT / "server" / "pyproject.toml").read_text())["project"]
    declared = {requirement_name(r) for r in project["dependencies"]}
    for extra, requirements in project.get("optional-dependencies", {}).items():
        if extra not in UNPINNED_EXTRAS:
            declared |= {requirement_name(r) for r in requirements}

    pinned = {}
    for line in (ROOT / "server" / "constraints.txt").read_text().splitlines():
        line = line.strip()
        if line and not line.startswith("#"):
            name, _, version = line.partition("==")
            pinned[canonical(name)] = version

    missing = sorted(name for name in declared if name not in pinned)
    if missing:
        print(
            "server/constraints.txt pins no version for: " + ", ".join(missing) + "\n"
            "Run ./scripts/update_constraints.sh, then ./scripts/check.sh.",
            file=sys.stderr,
        )
        return 1
    print(f"server/constraints.txt pins all {len(declared)} declared dependencies.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
