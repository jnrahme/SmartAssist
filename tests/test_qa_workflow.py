import json
import os
import subprocess
import sys
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


def test_qa_preflight_accepts_modern_claude_json_registration(tmp_path):
    home = tmp_path / "home"
    home.mkdir()
    (home / ".claude").mkdir()
    (home / ".claude.json").write_text(
        json.dumps(
            {
                "mcpServers": {
                    "smartassist": {
                        "command": "python",
                        "args": ["-m", "smartassist.mcp_server"],
                    }
                }
            }
        )
    )

    result = subprocess.run(
        "bash scripts/qa_preflight.sh",
        shell=True,
        cwd=REPO_ROOT,
        text=True,
        capture_output=True,
        check=False,
        env={
            "HOME": str(home),
            "PATH": os.environ.get("PATH", ""),
        },
    )

    assert result.returncode == 0, result.stderr
    assert "[qa_preflight] Config validation passed" in result.stdout


def test_qa_preflight_accepts_project_local_registration(tmp_path):
    home = tmp_path / "home"
    home.mkdir()
    (home / ".claude").mkdir()
    project = tmp_path / "project"
    project.mkdir()
    (project / ".mcp.json").write_text(
        json.dumps(
            {
                "mcpServers": {
                    "smartassist": {
                        "command": "python",
                        "args": ["-m", "smartassist.mcp_server"],
                    }
                }
            }
        )
    )

    result = subprocess.run(
        f"bash {REPO_ROOT / 'scripts/qa_preflight.sh'}",
        shell=True,
        cwd=project,
        text=True,
        capture_output=True,
        check=False,
        env={
            "HOME": str(home),
            "PATH": os.environ.get("PATH", ""),
            "PYTHONPATH": str(REPO_ROOT),
        },
    )

    assert result.returncode == 0, result.stderr
    assert "[qa_preflight] Config validation passed" in result.stdout


def test_qa_mcp_protocol_dry_run_passes():
    result = _run("bash scripts/qa_mcp_protocol.sh --dry-run")
    assert result.returncode == 0, result.stderr
    assert "[qa_mcp_protocol] PASS" in result.stdout


def test_qa_package_smoke_dry_run_passes():
    result = _run("bash scripts/qa_package_smoke.sh --dry-run")
    assert result.returncode == 0, result.stderr
    assert "[qa_package_smoke] PASS" in result.stdout


def test_qa_pipx_smoke_dry_run_passes():
    result = _run("bash scripts/qa_pipx_smoke.sh --dry-run")
    assert result.returncode == 0, result.stderr
    assert "[qa_pipx_smoke] PASS" in result.stdout


def test_qa_mcp_probe_runs_from_source_checkout():
    result = subprocess.run(
        [sys.executable, "scripts/qa_mcp_probe.py", "--timeout", "2"],
        cwd=REPO_ROOT,
        text=True,
        capture_output=True,
        check=False,
        env={
            "HOME": str(REPO_ROOT),
            "PATH": os.environ.get("PATH", ""),
            "PYTHONPATH": str(REPO_ROOT),
        },
    )

    assert result.returncode == 0, result.stderr
    payload = json.loads(result.stdout)
    assert payload["status"] == "pass"
    assert payload["command"].endswith("-m smartassist.cli")


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
