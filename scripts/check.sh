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

# Prefer the project virtualenv, but fall back to whatever is on PATH so this script
# works in CI, in a container, and for a contributor whose environment lives elsewhere.
if [ -x "server/.venv/bin/python" ]; then
  PY="server/.venv/bin/python"
else
  PY="$(command -v python3 || command -v python)"
fi
run_py() { "$PY" -m "$@"; }

step "ruff (server)"    run_py ruff check server
step "ruff (sdk)"       run_py ruff check sdk/python
# Lint only, not format: these harness scripts use a deliberate, hand-aligned multi-line
# call style `ruff format` would rewrite wholesale.
step "ruff (scripts)"   run_py ruff check scripts
step "ruff format"      run_py ruff format --check server sdk/python
step "mypy (server)"    run_py mypy --config-file server/pyproject.toml server/curvevision
step "mypy (sdk)"       run_py mypy --config-file sdk/python/pyproject.toml sdk/python/curvevision_sdk
step "pytest (server)"  run_py pytest server/tests -q
step "pytest (sdk)"     run_py pytest sdk/python/tests -q
step "notices"          "$PY" scripts/check_notices.py
step "eslint (web)"     npm --prefix web run lint
step "tsc (web)"        npm --prefix web run typecheck
step "vitest (web)"     npm --prefix web run test

echo ""
if [ "$failed" -eq 0 ]; then echo "All checks passed."; else echo "Some checks failed."; fi
exit "$failed"
