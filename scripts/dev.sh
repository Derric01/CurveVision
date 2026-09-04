#!/usr/bin/env bash
# Start CurveVision for local development: infrastructure in Docker, app processes on the
# host so both reload on save.
set -euo pipefail

cd "$(dirname "$0")/.."

echo "→ starting postgres, redis and minio"
docker compose up -d postgres redis minio minio-init

export CURVEVISION_ENVIRONMENT=development
export CURVEVISION_DATABASE_URL="postgresql+asyncpg://curvevision:curvevision@localhost:5432/curvevision"
export CURVEVISION_REDIS_URL="redis://localhost:6379/0"
export CURVEVISION_STORAGE_BACKEND=local
export CURVEVISION_STORAGE_LOCAL_ROOT="./data/storage"
export CURVEVISION_LOG_FORMAT=console

if [ ! -d server/.venv ]; then
  echo "→ creating the server virtualenv"
  (cd server && python3 -m venv .venv && ./.venv/bin/pip install -q -e ".[dev,postgres]")
fi

echo "→ applying migrations"
(cd server && ./.venv/bin/alembic upgrade head)

echo "→ api on http://localhost:8000  (docs at /api/docs)"
(cd server && ./.venv/bin/uvicorn curvevision.main:app --reload --port 8000) &
API_PID=$!

if [ ! -d web/node_modules ]; then
  echo "→ installing web dependencies"
  (cd web && npm install --no-audit --no-fund)
fi

echo "→ web on http://localhost:5173"
(cd web && npm run dev) &
WEB_PID=$!

trap 'kill $API_PID $WEB_PID 2>/dev/null || true' INT TERM
wait
