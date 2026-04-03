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

echo "[qa_pipx_smoke] Checking pipx install path"

if [[ "$DRY_RUN" == "1" ]]; then
  echo "[qa_pipx_smoke] Dry run mode enabled"
  echo "[qa_pipx_smoke] PASS"
  exit 0
fi

if ! command -v pipx >/dev/null 2>&1; then
  echo "[qa_pipx_smoke] FAIL: pipx not found" >&2
  exit 1
fi

if [[ -z "$DIST_DIR" ]]; then
  DIST_DIR="$(mktemp -d /tmp/smartassist-pipx-dist-XXXXXX)"
fi

bash "$(dirname "$0")/qa_package_smoke.sh" --dist-dir "$DIST_DIR" >/dev/null

WHEEL_PATH="$(ls "$DIST_DIR"/smartassist-*.whl 2>/dev/null | head -n 1 || true)"
if [[ -z "$WHEEL_PATH" ]]; then
  echo "[qa_pipx_smoke] FAIL: no wheel produced in $DIST_DIR" >&2
  exit 1
fi

TMP_ROOT="$(mktemp -d /tmp/smartassist-pipx-XXXXXX)"
export PIPX_HOME="$TMP_ROOT/pipx-home"
export PIPX_BIN_DIR="$TMP_ROOT/bin"
export PIPX_MAN_DIR="$TMP_ROOT/man"
export PIP_CACHE_DIR="${PIP_CACHE_DIR:-/tmp/pip-cache}"
mkdir -p "$PIPX_HOME" "$PIPX_BIN_DIR" "$PIPX_MAN_DIR" "$PIP_CACHE_DIR"

HOME_ROOT="$TMP_ROOT/home"
PROJECT_ROOT_A="$TMP_ROOT/project-a"
PROJECT_ROOT_B="$TMP_ROOT/project-b"
mkdir -p "$HOME_ROOT/.claude" "$PROJECT_ROOT_A" "$PROJECT_ROOT_B"
printf '{}' >"$HOME_ROOT/.claude/settings.json"

export HOME="$HOME_ROOT"
export PATH="$PIPX_BIN_DIR:/usr/local/bin:/usr/bin:/bin:/opt/homebrew/bin:${PATH:-}"

pipx install --force --suffix=-smoke "$WHEEL_PATH" >/dev/null

if [[ ! -x "$PIPX_BIN_DIR/smartassist-smoke" ]]; then
  echo "[qa_pipx_smoke] FAIL: smartassist-smoke not installed by pipx" >&2
  exit 1
fi

for cmd in \
  smartassist \
  claude-sa \
  smartassist-prompt-inject \
  smartassist-session-start \
  smartassist-session-end \
  smartassist-commit-hook \
  smartassist-show-lessons \
  smartassist-monitor
do
  ln -sf "$PIPX_BIN_DIR/${cmd}-smoke" "$PIPX_BIN_DIR/$cmd"
done

version_output="$("$PIPX_BIN_DIR/smartassist-smoke" version)"
if [[ "$version_output" != smartassist* ]]; then
  echo "[qa_pipx_smoke] FAIL: unexpected version output: $version_output" >&2
  exit 1
fi

check_project_ready() {
  local project_root="$1"
  local project_label="$2"

  (
    cd "$project_root"
    "$PIPX_BIN_DIR/smartassist" setup >/dev/null

    if [[ ! -f "$project_root/.mcp.json" ]]; then
      echo "[qa_pipx_smoke] FAIL: $project_label missing project .mcp.json" >&2
      exit 1
    fi

    python3 - <<'PY' "$project_root/.mcp.json" "$project_label"
import json
import sys
from pathlib import Path

config_path = Path(sys.argv[1])
label = sys.argv[2]
config = json.loads(config_path.read_text())
entry = (config.get("mcpServers") or {}).get("smartassist")
if not isinstance(entry, dict):
    raise SystemExit(f"[qa_pipx_smoke] FAIL: {label} missing smartassist entry")
if entry.get("env", {}).get("SMARTASSIST_DATA_DIR"):
    raise SystemExit(f"[qa_pipx_smoke] FAIL: {label} should not hardcode SMARTASSIST_DATA_DIR")
PY

    doctor_output="$("$PIPX_BIN_DIR/smartassist" doctor --json)"
    python3 - <<'PY' "$doctor_output" "$project_label"
import json
import sys

report = json.loads(sys.argv[1])
label = sys.argv[2]
if report.get("overall_status") != "ready":
    raise SystemExit(f"[qa_pipx_smoke] FAIL: {label} doctor status = {report.get('overall_status')}")
PY
  )
}

check_project_ready "$PROJECT_ROOT_A" "project-a"
check_project_ready "$PROJECT_ROOT_B" "project-b"

(
  cd "$PROJECT_ROOT_A"
  doctor_output="$("$PIPX_BIN_DIR/smartassist" doctor --json)"
  python3 - <<'PY' "$doctor_output"
import json
import sys

report = json.loads(sys.argv[1])
if report.get("overall_status") != "ready":
    raise SystemExit("[qa_pipx_smoke] FAIL: project-a regressed after project-b setup")
PY
)

echo "[qa_pipx_smoke] Wheel: $WHEEL_PATH"
echo "[qa_pipx_smoke] Version: $version_output"
echo "[qa_pipx_smoke] Projects: project-a, project-b"
echo "[qa_pipx_smoke] PASS"
