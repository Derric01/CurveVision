#!/usr/bin/env bash
# Everything CI runs, in one command. Run this before opening a pull request.
set -euo pipefail
cd "$(dirname "$0")/.."

failed=0
step() {
  echo ""
  echo "── $1 ──────────────────────────────────────────────"
  shift
  if "$@"; then echo "   ok"; else echo "   FAILED"; failed=1; fi
}

PY=server/.venv/bin
step "ruff (server)"    "$PY/ruff" check server
step "ruff format"      "$PY/ruff" format --check server sdk/python
step "mypy (server)"    "$PY/mypy" --config-file server/pyproject.toml server/curvevision
step "pytest (server)"  "$PY/python" -m pytest server/tests -q
step "pytest (sdk)"     "$PY/python" -m pytest sdk/python/tests -q
step "eslint (web)"     npm --prefix web run lint
step "tsc (web)"        npm --prefix web run typecheck
step "vitest (web)"     npm --prefix web run test

echo ""
if [ "$failed" -eq 0 ]; then echo "All checks passed."; else echo "Some checks failed."; fi
exit "$failed"
