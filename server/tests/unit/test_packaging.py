"""What a plain install of the server brings with it.

The desktop installers are built from `pip install "server[media,desktop]"`, and so is
anyone's `curvevision-local`. Until iteration 84 the default database's driver was only a
development dependency: every test passed, because tests install `dev`, and every installer
built on CI shipped a server that could not open its own database.
"""

from __future__ import annotations

import re
import tomllib
from pathlib import Path

import sqlalchemy as sa

from curvevision.core.config import Settings

PYPROJECT = Path(__file__).resolve().parents[2] / "pyproject.toml"


def required() -> set[str]:
    project = tomllib.loads(PYPROJECT.read_text())["project"]
    return {
        re.split(r"[\[<>=!~ ;]", line, maxsplit=1)[0].lower() for line in project["dependencies"]
    }


def test_the_default_databases_driver_is_not_optional() -> None:
    driver = sa.make_url(Settings.model_fields["database_url"].default).get_driver_name()

    assert driver == "aiosqlite"
    assert driver in required()
