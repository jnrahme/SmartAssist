"""Runtime contract tests for the SmartAssist QA scenario runner."""

from pathlib import Path

from smartassist.qa.runner import list_scenarios, run_scenarios


def test_runtime_contract_suite_passes(tmp_path):
    summary = run_scenarios(run_dir=tmp_path / "qa-run", render_demo=False)
    expected_names = [scenario["name"] for scenario in list_scenarios()]

    assert summary["final_status"] == "pass"
    assert summary["scenario_count"] == len(expected_names)
    assert summary["completed_count"] == len(expected_names)

    run_dir = Path(summary["run_dir"])
    assert (run_dir / "summary.json").exists()
    assert (run_dir / "manifest.json").exists()

    for scenario in summary["scenarios"]:
        scenario_dir = run_dir / "scenarios" / scenario["name"]
        assert scenario["success"] is True
        assert scenario["status"] == "pass"
        assert (scenario_dir / "scenario.json").exists()
        assert (scenario_dir / "assertions.json").exists()
        assert (scenario_dir / "steps.jsonl").exists()
        assert (scenario_dir / "before_state.json").exists()
        assert (scenario_dir / "after_state.json").exists()
