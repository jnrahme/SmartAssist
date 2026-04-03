#!/usr/bin/env bash
set -euo pipefail

DRY_RUN=0
TIMEOUT=15
REQUIRED_TOOLS="rag_search,rag_feedback,rag_dashboard"

while [[ $# -gt 0 ]]; do
  case "$1" in
    --dry-run) DRY_RUN=1; shift ;;
    --timeout) TIMEOUT="$2"; shift 2 ;;
    --required-tools) REQUIRED_TOOLS="$2"; shift 2 ;;
    *) echo "Unknown option: $1" >&2; exit 2 ;;
  esac
done

echo "[qa_mcp_protocol] Checking MCP protocol health"

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$REPO_ROOT"

if [[ "$DRY_RUN" == "1" ]]; then
  echo "{\"status\":\"pass\",\"mode\":\"dry-run\",\"tools_count\":3}"
  echo "[qa_mcp_protocol] PASS"
  exit 0
fi

if command -v uv >/dev/null 2>&1; then
  UV_CACHE_DIR="${UV_CACHE_DIR:-/tmp/uv-cache}" \
  PYTHONPATH="$REPO_ROOT${PYTHONPATH:+:$PYTHONPATH}" \
    uv run python scripts/qa_mcp_probe.py --timeout "$TIMEOUT" --required-tools "$REQUIRED_TOOLS"
else
  PYTHONPATH="$REPO_ROOT${PYTHONPATH:+:$PYTHONPATH}" \
    python3 scripts/qa_mcp_probe.py --timeout "$TIMEOUT" --required-tools "$REQUIRED_TOOLS"
fi
echo "[qa_mcp_protocol] PASS"
