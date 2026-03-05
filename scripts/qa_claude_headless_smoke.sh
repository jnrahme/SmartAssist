#!/usr/bin/env bash
set -euo pipefail

DRY_RUN=0
TIMEOUT=45
MODEL="${QA_CLAUDE_MODEL:-}"
EXTRA_ARGS="${QA_CLAUDE_EXTRA_ARGS:-}"

while [[ $# -gt 0 ]]; do
  case "$1" in
    --dry-run) DRY_RUN=1; shift ;;
    --timeout) TIMEOUT="$2"; shift 2 ;;
    --model) MODEL="$2"; shift 2 ;;
    *) echo "Unknown option: $1" >&2; exit 2 ;;
  esac
done

echo "[qa_claude_headless_smoke] Running Claude headless smoke test"

if [[ "$DRY_RUN" == "1" ]]; then
  echo "{\"status\":\"pass\",\"mode\":\"dry-run\",\"tools\":[\"mcp__smartassist__rag_search\"]}"
  echo "[qa_claude_headless_smoke] PASS"
  exit 0
fi

if ! command -v claude >/dev/null 2>&1; then
  echo "[qa_claude_headless_smoke] FAIL: claude CLI not found in PATH" >&2
  exit 1
fi

python3 - "$TIMEOUT" "$MODEL" "$EXTRA_ARGS" <<'PY'
import json
import re
import shlex
import subprocess
import sys

timeout = int(sys.argv[1])
model = sys.argv[2].strip()
extra = sys.argv[3].strip()

prompt = (
    "List every available tool that contains 'smartassist' in its name. "
    "Return JSON only with shape: {\"tools\": [\"...\"]}."
)
cmd = ["claude", "-p", "--output-format", "json", "--max-turns", "2", "--no-session-persistence"]
if model:
    cmd += ["--model", model]
if extra:
    cmd += shlex.split(extra)
cmd.append(prompt)

try:
    proc = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
except subprocess.TimeoutExpired:
    print(json.dumps({"status": "fail", "error": f"Timed out after {timeout}s"}))
    sys.exit(1)

if proc.returncode != 0:
    print(json.dumps({
        "status": "fail",
        "error": f"claude exited {proc.returncode}",
        "stderr": proc.stderr.strip()[:600],
    }))
    sys.exit(1)

output = proc.stdout.strip()
if not output:
    print(json.dumps({"status": "fail", "error": "claude returned empty output"}))
    sys.exit(1)

try:
    data = json.loads(output)
except Exception:
    print(json.dumps({
        "status": "fail",
        "error": "claude output is not valid JSON",
        "sample": output[:600],
    }))
    sys.exit(1)

def extract_tools(payload):
    if isinstance(payload, dict) and isinstance(payload.get("tools"), list):
        return payload["tools"]
    if isinstance(payload, dict) and isinstance(payload.get("result"), str):
        text = payload["result"]
        m = re.search(r"\{[\s\S]*\}", text)
        if m:
            try:
                parsed = json.loads(m.group(0))
                if isinstance(parsed.get("tools"), list):
                    return parsed["tools"]
            except Exception:
                pass
    return None

tools = extract_tools(data)
if tools is None:
    print(json.dumps({
        "status": "fail",
        "error": "Unable to parse tools array from Claude output",
        "sample": str(data)[:600],
    }))
    sys.exit(1)

matching = [t for t in tools if isinstance(t, str) and "smartassist" in t.lower()]
if not matching:
    print(json.dumps({
        "status": "fail",
        "error": "No smartassist tools detected in Claude output",
        "tools": tools,
    }))
    sys.exit(1)

print(json.dumps({"status": "pass", "tools": matching, "sample": data}))
PY

echo "[qa_claude_headless_smoke] PASS"
