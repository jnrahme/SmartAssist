#!/usr/bin/env bash
set -euo pipefail

DRY_RUN=0

while [[ $# -gt 0 ]]; do
  case "$1" in
    --dry-run) DRY_RUN=1; shift ;;
    *) echo "Unknown option: $1" >&2; exit 2 ;;
  esac
done

echo "[qa_preflight] Starting preflight checks"

if [[ "$DRY_RUN" == "1" ]]; then
  echo "[qa_preflight] Dry run mode enabled"
  echo "[qa_preflight] PASS"
  exit 0
fi

if ! command -v python3 >/dev/null 2>&1; then
  echo "[qa_preflight] FAIL: python3 not found" >&2
  exit 1
fi

PYTHON_BIN="${SMARTASSIST_PYTHON:-python3}"

is_compatible_python() {
  local candidate="$1"
  "$candidate" - <<'PY' >/dev/null 2>&1
import sys
raise SystemExit(0 if sys.version_info >= (3, 10) else 1)
PY
}

if ! is_compatible_python "$PYTHON_BIN"; then
  for candidate in python3.14 python3.13 python3.12 python3.11 python3.10; do
    if command -v "$candidate" >/dev/null 2>&1 && is_compatible_python "$candidate"; then
      PYTHON_BIN="$candidate"
      break
    fi
  done
fi

if ! is_compatible_python "$PYTHON_BIN"; then
  echo "[qa_preflight] FAIL: Python >= 3.10 required to import SmartAssist" >&2
  exit 1
fi

"$PYTHON_BIN" - <<'PY'
import sys
from pathlib import Path

sys.path.insert(0, str(Path.cwd()))

from smartassist.claude_config import get_mcp_status
from smartassist.runtime import resolve_cli_invocation

errors = []

claude_dir = Path.home() / ".claude"
if not claude_dir.exists():
    errors.append(f"{claude_dir} missing")

try:
    resolve_cli_invocation(prefer_source_checkout=True, start_path=Path.cwd())
except Exception as exc:
    errors.append(str(exc))

status = get_mcp_status()
if not status["registered"]:
    errors.append(
        "No SmartAssist MCP registration found for this project in .mcp.json, "
        "~/.claude.json, or legacy ~/.claude/mcp.json"
    )

if errors:
    raise SystemExit("[qa_preflight] FAIL:\n- " + "\n- ".join(errors))

print("[qa_preflight] Config validation passed")
PY

echo "[qa_preflight] PASS"
