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

if ! command -v smartassist >/dev/null 2>&1; then
  echo "[qa_preflight] FAIL: smartassist CLI not found in PATH" >&2
  exit 1
fi

if [[ ! -f ".mcp.json" ]] && [[ ! -f "$HOME/.claude/mcp.json" ]]; then
  echo "[qa_preflight] FAIL: missing .mcp.json and ~/.claude/mcp.json" >&2
  exit 1
fi

python3 - <<'PY'
import json
from pathlib import Path

configs = [Path(".mcp.json"), Path.home() / ".claude" / "mcp.json"]
errors = []
found = False

for path in configs:
    if not path.exists():
        continue
    try:
        data = json.loads(path.read_text())
    except Exception as e:
        errors.append(f"{path}: invalid JSON ({e})")
        continue

    servers = data.get("mcpServers", {})
    if "smartassist" in servers:
        found = True
        server = servers["smartassist"]
        cmd = server.get("command")
        if not cmd:
            errors.append(f"{path}: smartassist server missing 'command'")

if not found:
    errors.append("No smartassist entry under mcpServers in .mcp.json or ~/.claude/mcp.json")

if errors:
    raise SystemExit("[qa_preflight] FAIL:\n- " + "\n- ".join(errors))

print("[qa_preflight] Config validation passed")
PY

echo "[qa_preflight] PASS"
