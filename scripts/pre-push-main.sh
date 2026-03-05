#!/usr/bin/env bash
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$REPO_ROOT"

echo "[SmartAssist] Pre-push gate: compile + tests"

echo "[1/2] Python compile check"
python3 -m compileall -q smartassist tests

echo "[2/2] Test suite"
if command -v uv >/dev/null 2>&1; then
  uv run pytest -q
else
  python3 -m pytest -q
fi

if [[ "${RUN_RUFF:-0}" == "1" ]]; then
  echo "[optional] Ruff check"
  if command -v uv >/dev/null 2>&1; then
    uv run ruff check .
  else
    ruff check .
  fi
fi

echo "[SmartAssist] Pre-push gate passed."
