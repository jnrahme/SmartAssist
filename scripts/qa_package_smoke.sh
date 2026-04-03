#!/usr/bin/env bash
set -euo pipefail

DRY_RUN=0
DIST_DIR=""

while [[ $# -gt 0 ]]; do
  case "$1" in
    --dry-run) DRY_RUN=1; shift ;;
    --dist-dir) DIST_DIR="$2"; shift 2 ;;
    *) echo "Unknown option: $1" >&2; exit 2 ;;
  esac
done

echo "[qa_package_smoke] Checking package build and install path"

if [[ "$DRY_RUN" == "1" ]]; then
  echo "[qa_package_smoke] Dry run mode enabled"
  echo "[qa_package_smoke] PASS"
  exit 0
fi

if ! command -v uv >/dev/null 2>&1; then
  echo "[qa_package_smoke] FAIL: uv not found" >&2
  exit 1
fi

if ! command -v python3 >/dev/null 2>&1; then
  echo "[qa_package_smoke] FAIL: python3 not found" >&2
  exit 1
fi

if [[ -z "$DIST_DIR" ]]; then
  DIST_DIR="$(mktemp -d /tmp/smartassist-dist-XXXXXX)"
fi
SOURCE_DIR="$(mktemp -d /tmp/smartassist-src-XXXXXX)"
VENV_DIR="$(mktemp -d /tmp/smartassist-venv-XXXXXX)"
export UV_CACHE_DIR="${UV_CACHE_DIR:-/tmp/uv-cache}"
export PIP_CACHE_DIR="${PIP_CACHE_DIR:-/tmp/pip-cache}"
mkdir -p "$UV_CACHE_DIR"
mkdir -p "$PIP_CACHE_DIR"

BUILD_PYTHON="./.venv/bin/python"
if [[ ! -x "$BUILD_PYTHON" ]]; then
  BUILD_PYTHON="python3"
fi

if ! "$BUILD_PYTHON" - <<'PY' >/dev/null 2>&1
from setuptools.build_meta import build_wheel, build_sdist
PY
then
  echo "[qa_package_smoke] FAIL: no Python with setuptools.build_meta available" >&2
  exit 1
fi

cleanup() {
  rm -rf "$SOURCE_DIR"
  rm -rf "$VENV_DIR"
}
trap cleanup EXIT

"$BUILD_PYTHON" - <<'PY' "$PWD" "$SOURCE_DIR" >/dev/null
import shutil
import sys
from pathlib import Path

source_root = Path(sys.argv[1]).resolve()
dest_root = Path(sys.argv[2]).resolve()

ignored_names = {
    ".git",
    ".venv",
    ".pytest_cache",
    "node_modules",
    "build",
    "dist",
    "qa-artifacts",
    "__pycache__",
}

for child in source_root.iterdir():
    if child.name in ignored_names:
        continue
    target = dest_root / child.name
    if child.is_dir():
        shutil.copytree(
            child,
            target,
            ignore=shutil.ignore_patterns("__pycache__", "*.pyc", "*.pyo", ".DS_Store"),
        )
    else:
        shutil.copy2(child, target)
PY

"$BUILD_PYTHON" - <<'PY' "$SOURCE_DIR" "$DIST_DIR" >/dev/null
import os
import sys
from pathlib import Path
from setuptools.build_meta import build_sdist, build_wheel

source_dir = Path(sys.argv[1]).resolve()
dist_dir = Path(sys.argv[2]).resolve()
dist_dir.mkdir(parents=True, exist_ok=True)
os.chdir(source_dir)
build_sdist(str(dist_dir))
build_wheel(str(dist_dir))
PY

WHEEL_PATH="$(ls "$DIST_DIR"/smartassist-*.whl 2>/dev/null | head -n 1 || true)"
if [[ -z "$WHEEL_PATH" ]]; then
  echo "[qa_package_smoke] FAIL: no wheel produced in $DIST_DIR" >&2
  exit 1
fi

python3 -m venv "$VENV_DIR"
"$VENV_DIR/bin/pip" install --no-deps "$WHEEL_PATH" >/dev/null

required_commands=(
  smartassist
  claude-sa
  smartassist-prompt-inject
  smartassist-session-start
  smartassist-session-end
  smartassist-commit-hook
  smartassist-show-lessons
  smartassist-monitor
)

missing=()
for cmd in "${required_commands[@]}"; do
  if [[ ! -x "$VENV_DIR/bin/$cmd" ]]; then
    missing+=("$cmd")
  fi
done

if [[ "${#missing[@]}" -gt 0 ]]; then
  echo "[qa_package_smoke] FAIL: missing console scripts: ${missing[*]}" >&2
  exit 1
fi

version_output="$("$VENV_DIR/bin/smartassist" version)"
if [[ "$version_output" != smartassist* ]]; then
  echo "[qa_package_smoke] FAIL: unexpected version output: $version_output" >&2
  exit 1
fi

echo "[qa_package_smoke] Wheel: $WHEEL_PATH"
echo "[qa_package_smoke] Version: $version_output"
echo "[qa_package_smoke] PASS"
