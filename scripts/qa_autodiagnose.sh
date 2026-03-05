#!/usr/bin/env bash
set -euo pipefail

MAX_ATTEMPTS=3
DRY_RUN=0
RUN_DIR=""
E2E_CMD="${QA_E2E_CMD:-uv run pytest -q tests/test_feedback_lesson.py -k TestPromptInjectMainFeedback}"

while [[ $# -gt 0 ]]; do
  case "$1" in
    --max-attempts) MAX_ATTEMPTS="$2"; shift 2 ;;
    --dry-run) DRY_RUN=1; shift ;;
    --run-dir) RUN_DIR="$2"; shift 2 ;;
    --e2e-cmd) E2E_CMD="$2"; shift 2 ;;
    *) echo "Unknown option: $1" >&2; exit 2 ;;
  esac
done

TS="$(date +%Y%m%d_%H%M%S)"
if [[ -z "$RUN_DIR" ]]; then
  RUN_DIR="qa-artifacts/qa-${TS}"
fi
mkdir -p "$RUN_DIR"
METRICS_JSONL="$RUN_DIR/metrics.jsonl"
SUMMARY_JSON="$RUN_DIR/summary.json"
SUMMARY_TXT="$RUN_DIR/summary.txt"
touch "$METRICS_JSONL"

record_metric() {
  local attempt="$1" stage="$2" status="$3" duration_ms="$4" message="$5"
  python3 - "$METRICS_JSONL" "$attempt" "$stage" "$status" "$duration_ms" "$message" <<'PY'
import json, sys, time
path, attempt, stage, status, duration_ms, message = sys.argv[1:]
event = {
    "ts": int(time.time()),
    "attempt": int(attempt),
    "stage": stage,
    "status": status,
    "duration_ms": int(duration_ms),
    "message": message[:400],
}
with open(path, "a", encoding="utf-8") as f:
    f.write(json.dumps(event) + "\n")
PY
}

diagnose_failure() {
  local stage="$1"
  case "$stage" in
    preflight)
      echo "Configuration issue: verify mcp json, PATH, and smartassist install."
      ;;
    mcp_protocol)
      echo "MCP protocol issue: server startup or tools/list registration failure."
      ;;
    claude_smoke)
      echo "Claude integration issue: headless CLI, credentials, or MCP visibility."
      ;;
    e2e)
      echo "Behavior regression: failing end-to-end assertions or flaky tests."
      ;;
    *)
      echo "Unknown failure stage."
      ;;
  esac
}

run_stage() {
  local attempt="$1" stage="$2" cmd="$3"
  local stage_log="$RUN_DIR/attempt-${attempt}-${stage}.log"
  local started ended duration_ms message
  started="$(python3 - <<'PY'
import time
print(int(time.time() * 1000))
PY
)"

  if bash -lc "$cmd" >"$stage_log" 2>&1; then
    ended="$(python3 - <<'PY'
import time
print(int(time.time() * 1000))
PY
)"
    duration_ms=$((ended - started))
    record_metric "$attempt" "$stage" "pass" "$duration_ms" "ok"
    return 0
  fi

  ended="$(python3 - <<'PY'
import time
print(int(time.time() * 1000))
PY
)"
  duration_ms=$((ended - started))
  message="$(tail -n 1 "$stage_log" | tr -d '\r')"
  record_metric "$attempt" "$stage" "fail" "$duration_ms" "${message:-stage failed}"
  return 1
}

echo "[qa_autodiagnose] Run directory: $RUN_DIR"
echo "[qa_autodiagnose] Max attempts: $MAX_ATTEMPTS"

if [[ "$DRY_RUN" == "1" ]]; then
  PRE="bash scripts/qa_preflight.sh --dry-run"
  MCP="bash scripts/qa_mcp_protocol.sh --dry-run"
  SMOKE="bash scripts/qa_claude_headless_smoke.sh --dry-run"
  E2E="bash scripts/qa_preflight.sh --dry-run"
else
  PRE="bash scripts/qa_preflight.sh"
  MCP="bash scripts/qa_mcp_protocol.sh"
  SMOKE="bash scripts/qa_claude_headless_smoke.sh"
  E2E="$E2E_CMD"
fi

final_status="fail"
attempt=1
while [[ "$attempt" -le "$MAX_ATTEMPTS" ]]; do
  echo "[qa_autodiagnose] Attempt ${attempt}/${MAX_ATTEMPTS}"
  failed_stage=""

  run_stage "$attempt" "preflight" "$PRE" || failed_stage="preflight"
  if [[ -z "$failed_stage" ]]; then
    run_stage "$attempt" "mcp_protocol" "$MCP" || failed_stage="mcp_protocol"
  fi
  if [[ -z "$failed_stage" ]]; then
    run_stage "$attempt" "claude_smoke" "$SMOKE" || failed_stage="claude_smoke"
  fi
  if [[ -z "$failed_stage" ]]; then
    run_stage "$attempt" "e2e" "$E2E" || failed_stage="e2e"
  fi

  if [[ -z "$failed_stage" ]]; then
    final_status="pass"
    echo "[qa_autodiagnose] PASS on attempt $attempt"
    break
  fi

  diagnosis="$(diagnose_failure "$failed_stage")"
  echo "[qa_autodiagnose] FAIL at stage '$failed_stage': $diagnosis"
  record_metric "$attempt" "diagnosis" "info" 0 "$diagnosis"
  if [[ "$attempt" -lt "$MAX_ATTEMPTS" ]]; then
    echo "[qa_autodiagnose] Retrying after diagnosis..."
    sleep 1
  fi
  attempt=$((attempt + 1))
done

python3 - "$METRICS_JSONL" "$SUMMARY_JSON" "$SUMMARY_TXT" "$final_status" "$MAX_ATTEMPTS" <<'PY'
import json, sys
from pathlib import Path

metrics_path = Path(sys.argv[1])
summary_json = Path(sys.argv[2])
summary_txt = Path(sys.argv[3])
final_status = sys.argv[4]
max_attempts = int(sys.argv[5])

events = [json.loads(line) for line in metrics_path.read_text().splitlines() if line.strip()]
stage_events = [e for e in events if e["stage"] not in {"diagnosis"}]
passed = sum(1 for e in stage_events if e["status"] == "pass")
failed = sum(1 for e in stage_events if e["status"] == "fail")
total_duration_ms = sum(e.get("duration_ms", 0) for e in stage_events)
attempts_observed = sorted({e["attempt"] for e in stage_events})

summary = {
    "final_status": final_status,
    "attempts_configured": max_attempts,
    "attempts_observed": attempts_observed,
    "stages_passed": passed,
    "stages_failed": failed,
    "total_duration_ms": total_duration_ms,
    "metrics_file": str(metrics_path),
}
summary_json.write_text(json.dumps(summary, indent=2))
summary_txt.write_text(
    "\n".join(
        [
            f"final_status: {summary['final_status']}",
            f"attempts_observed: {attempts_observed}",
            f"stages_passed: {passed}",
            f"stages_failed: {failed}",
            f"total_duration_ms: {total_duration_ms}",
            f"metrics_file: {metrics_path}",
        ]
    )
    + "\n"
)
PY

echo "[qa_autodiagnose] Summary: $SUMMARY_JSON"
echo "[qa_autodiagnose] Metrics: $METRICS_JSONL"

if [[ "$final_status" != "pass" ]]; then
  exit 1
fi
