"""Tests for the SmartAssist QA runner and CLI surface."""
from unittest.mock import patch

from smartassist.cli import main as cli_main
from smartassist.qa.runner import clean_runs, list_scenarios, run_scenarios


def test_list_scenarios_includes_core_contract_suite():
    names = [scenario["name"] for scenario in list_scenarios()]

    assert "hook_mcp_retrieval_consistency" in names
    assert "feedback_creates_active_lesson" in names
    assert "compare_lesson_logs_without_storage" in names
    assert "session_dedup_prevents_repeat_injection" in names
    assert "projection_rebuild_converges" in names
    assert "capacity_enforcement_at_300" in names
    assert "commit_correction_promotes_active_lesson" in names
    assert "doctor_rejects_false_ready" in names


def test_run_single_scenario_writes_expected_artifacts(tmp_path):
    summary = run_scenarios(
        names=["feedback_creates_active_lesson"],
        run_dir=tmp_path / "single-scenario",
        render_demo=False,
    )

    assert summary["final_status"] == "pass"
    assert summary["scenario_count"] == 1
    scenario = summary["scenarios"][0]
    assert scenario["name"] == "feedback_creates_active_lesson"
    assert scenario["failed_assertions"] == []


def test_cli_qa_list_scenarios_outputs_registry(capsys):
    with patch("sys.argv", ["smartassist", "qa", "list-scenarios"]):
        rc = cli_main()

    out = capsys.readouterr().out
    assert rc == 0
    assert "hook_mcp_retrieval_consistency" in out
    assert "feedback_creates_active_lesson" in out
    assert "doctor_rejects_false_ready" in out


def test_clean_runs_removes_artifacts(tmp_path):
    run_dir = tmp_path / "qa-run"
    (run_dir / "summary.json").parent.mkdir(parents=True, exist_ok=True)
    (run_dir / "summary.json").write_text("{}", encoding="utf-8")

    removed = clean_runs(run_dir)

    assert removed == [str(run_dir)]
    assert not run_dir.exists()


def test_cli_qa_clean_removes_default_artifacts(tmp_path, monkeypatch, capsys):
    qa_root = tmp_path / "qa-artifacts"
    (qa_root / "example").mkdir(parents=True, exist_ok=True)
    monkeypatch.chdir(tmp_path)

    with patch("sys.argv", ["smartassist", "qa", "clean"]):
        rc = cli_main()

    out = capsys.readouterr().out
    assert rc == 0
    assert "Removed" in out
    assert not qa_root.exists()
