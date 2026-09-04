# Development

## Requirements

* Python 3.11+
* Node 20+
* Docker (only for PostgreSQL, Redis and MinIO; the test suites need none of them)

## Quick start

```bash
git clone https://github.com/Derric01/CurveVision.git
cd CurveVision
./scripts/dev.sh
```

That starts the infrastructure containers, creates the Python virtualenv, applies
migrations, and runs the API on `:8000` and the web app on `:5173` with hot reload.

Open <http://localhost:5173>. **The first account you register becomes the instance
administrator** — a fresh self-hosted instance has to be administrable without shell
access.

## Running the pieces separately

### Server

```bash
cd server
python3 -m venv .venv && source .venv/bin/activate
pip install -e ".[dev]"

alembic upgrade head
uvicorn curvevision.main:app --reload
```

Interactive API docs: <http://localhost:8000/api/docs>.

With no configuration the server uses SQLite, local-filesystem storage and the inline job
queue, so it runs with nothing else installed. Point it at real infrastructure with
environment variables — every one is listed in [`.env.example`](../.env.example).

### Web

```bash
cd web
npm install
npm run dev
```

Vite proxies `/api` to `localhost:8000`, so there is no second base URL or CORS setup to
think about in development.

### Worker

Only needed when `CURVEVISION_JOB_QUEUE_BACKEND=dramatiq`. In development the inline queue
runs jobs in-process, which is why import/export and inference work with no broker.

```bash
cd server
dramatiq curvevision.jobs.dramatiq_app --queues default media import export inference
```

## Tests

```bash
./scripts/check.sh     # everything CI runs
```

Individually:

```bash
cd server && pytest                      # ~130 tests, no services required
cd server && pytest -m benchmark -s      # annotation-scale benchmarks
cd sdk/python && pytest                  # SDK against a real uvicorn server
cd web && npm test                       # ~107 engine and interpolation tests
cd web && npm run bench                  # canvas engine benchmarks
```

**The server suite runs with zero external services** — SQLite, local storage, inline
queue. That is a design constraint, not a convenience: a contributor should be able to
clone the repo and run the tests. The PostgreSQL-specific paths are covered by a separate
CI job.

## Code style

| | Tool | Command |
| --- | --- | --- |
| Python lint + format | ruff | `ruff check . && ruff format .` |
| Python types | mypy (strict-ish) | `mypy curvevision` |
| TypeScript lint | eslint | `npm run lint` |
| TypeScript types | tsc | `npm run typecheck` |

All four run in CI and must be clean.

## Layering rules

These are enforced by review, and breaking one is the fastest way to make the codebase hard
to change:

| Layer | May import | Must not |
| --- | --- | --- |
| `api/` | `schemas/`, `services/`, `core/` | hold business logic, or touch the ORM directly |
| `services/` | `domain/`, `core/`, the seam packages | import FastAPI or request schemas |
| `domain/` | `core/db` | import services or API |
| `storage/`, `formats/`, `ml/`, `jobs/` | `core/` | import `domain/` models |

The seam packages take plain data on purpose: that is what lets them be unit-tested with no
database, and swapped without touching the domain.

On the frontend, `src/canvas/` must not import from React or from `src/features/`. It is a
plain-TypeScript engine that React mounts.

## Adding things

### A dataset format

1. Implement `DatasetFormat` in `server/curvevision/formats/`.
2. Declare `FormatCapabilities` **honestly** — what the format drops is surfaced to users
   before an export runs, so an optimistic declaration produces silently broken datasets.
3. `register(YourFormat())` at module scope, and import the module in `formats/__init__.py`.
4. Add a round-trip test: export, re-import, compare. Every built-in format has one.

Third-party formats can register through the `curvevision.formats` entry-point group
without touching this repository.

### A model provider

1. Implement `ModelProvider` in `server/curvevision/ml/`.
2. `register_provider(YourProvider())`.

Most people do not need this: the built-in `http` provider talks to anything speaking the
documented inference contract, which is a 30-line FastAPI script. See
[API.md](./API.md#ai-assisted-annotation).

### A database change

1. Edit the model in `server/curvevision/domain/`.
2. `alembic revision --autogenerate -m "what changed"`.
3. **Read the generated migration.** Autogenerate is a good first draft, not an answer —
   it does not see data migrations, and it gets constraint renames wrong.
4. Test both directions: `alembic upgrade head && alembic downgrade base && alembic upgrade head`.

### An API endpoint

1. Request and response models in `schemas/`.
2. Business logic in `services/`, taking a session and returning domain objects or plain
   data. No HTTP types.
3. A router in `api/v1/` that resolves the resource, calls `scope.authorize(...)`, calls the
   service, and commits.
4. Tests: the happy path, the permission denial, and the validation failure.

## Debugging

```bash
CURVEVISION_LOG_FORMAT=console CURVEVISION_LOG_LEVEL=DEBUG uvicorn curvevision.main:app --reload
CURVEVISION_DATABASE_ECHO=true ...     # log every SQL statement
```

Every log line carries the `request_id` also returned in the `X-Request-ID` response
header, so a user-reported failure can be traced from the browser to the query that caused
it.

## Common problems

**`MissingGreenlet` in a test.** Something serialised a lazily-loaded relationship. Async
SQLAlchemy cannot lazy-load during serialisation; add a `selectinload` to the query, or set
`lazy="selectin"` on the relationship if it is always wanted.

**`no such table` when running the SDK tests.** The server binds its engine from the
`Settings` passed to `create_app`, in the lifespan. `httpx.ASGITransport` does not run
lifespan, which is why the SDK tests use a real uvicorn server.

**A migration works on SQLite and fails on PostgreSQL.** Check `core/types.py`: `GUID`,
`EnumString` and `JSONDocument` are the only places the two are allowed to differ.
