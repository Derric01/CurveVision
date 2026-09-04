# CurveVision Server

The CurveVision backend: a FastAPI application exposing the whole `/api/v1` surface, plus a
Dramatiq worker entrypoint that runs the same code with a different command.

```bash
uv venv .venv && source .venv/bin/activate
uv pip install -e ".[dev]"
alembic upgrade head
uvicorn curvevision.main:app --reload
```

The test suite needs no services at all — it runs on SQLite with the inline job queue and
local-filesystem storage:

```bash
pytest
```

See [`../docs/DEVELOPMENT.md`](../docs/DEVELOPMENT.md) for the full development guide and
[`../docs/ARCHITECTURE.md`](../docs/ARCHITECTURE.md) for the design.
