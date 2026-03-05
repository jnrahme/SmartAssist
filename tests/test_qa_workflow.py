import json
import subprocess
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]


def _run(cmd: str):
    return subprocess.run(
        cmd,
        shell=True,
        cwd=REPO_ROOT,
        text=True,
        capture_output=True,
        check=False,
    )


def test_qa_preflight_dry_run_passes():
    result = _run("bash scripts/qa_preflight.sh --dry-run")
    assert result.returncode == 0, result.stderr
    assert "[qa_preflight] PASS" in result.stdout


def test_qa_mcp_protocol_dry_run_passes():
    result = _run("bash scripts/qa_mcp_protocol.sh --dry-run")
    assert result.returncode == 0, result.stderr
    assert "[qa_mcp_protocol] PASS" in result.stdout


def test_qa_claude_smoke_dry_run_passes():
    result = _run("bash scripts/qa_claude_headless_smoke.sh --dry-run")
    assert result.returncode == 0, result.stderr
    assert "[qa_claude_headless_smoke] PASS" in result.stdout


def test_qa_autodiagnose_dry_run_generates_metrics(tmp_path):
    run_dir = tmp_path / "qa-run"
    result = _run(f"bash scripts/qa_autodiagnose.sh --dry-run --max-attempts 2 --run-dir {run_dir}")
    assert result.returncode == 0, result.stderr

    metrics = run_dir / "metrics.jsonl"
    summary = run_dir / "summary.json"
    summary_txt = run_dir / "summary.txt"

    assert metrics.exists()
    assert summary.exists()
    assert summary_txt.exists()

    entries = [json.loads(line) for line in metrics.read_text().splitlines() if line.strip()]
    assert entries, "metrics.jsonl should contain entries"
    assert all("stage" in entry for entry in entries)

    data = json.loads(summary.read_text())
    assert data["final_status"] == "pass"
    assert data["stages_failed"] == 0
