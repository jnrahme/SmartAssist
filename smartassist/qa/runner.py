"""Scenario runner for SmartAssist QA automation."""

from __future__ import annotations

import shutil
import traceback
from pathlib import Path
from typing import Any

from smartassist.qa.artifacts import copy_if_exists, ensure_run_dir, write_json, write_jsonl
from smartassist.qa.demo import render_demo_site
from smartassist.qa.fixtures import create_scenario_sandbox
from smartassist.qa.scenarios import ScenarioResult, get_scenario_definitions


def list_scenarios() -> list[dict[str, Any]]:
    return [
        {
            "name": scenario.name,
            "description": scenario.description,
            "live_claude": scenario.live_claude,
            "default": True,
        }
        for scenario in get_scenario_definitions()
    ]


def clean_runs(run_dir: Path | str | None = None) -> list[str]:
    target = Path(run_dir) if run_dir is not None else Path("qa-artifacts")
    if not target.exists():
        return []
    shutil.rmtree(target)
    return [str(target)]


def _write_scenario_artifacts(run_dir: Path, result: ScenarioResult) -> None:
    scenario_dir = run_dir / "scenarios" / result.name
    write_json(scenario_dir / "scenario.json", result.to_dict())
    write_json(
        scenario_dir / "assertions.json",
        {"assertions": [item.__dict__ for item in result.assertions]},
    )
    write_jsonl(scenario_dir / "steps.jsonl", result.steps)
    write_json(scenario_dir / "before_state.json", result.before_state)
    write_json(scenario_dir / "after_state.json", result.after_state)
    write_json(scenario_dir / "sqlite_snapshot.json", result.after_state.get("canonical", {}))
    write_json(scenario_dir / "export_snapshot.json", result.after_state.get("exports", {}))

    storage_path_value = str(result.after_state.get("paths", {}).get("storage_path", "")).strip()
    if storage_path_value:
        storage_path = Path(storage_path_value)
        copy_if_exists(storage_path / "rag_live.log", scenario_dir / "rag_live.log")
        copy_if_exists(storage_path / "usage_log.jsonl", scenario_dir / "usage_log.jsonl")


def _build_summary(
    output_dir: Path,
    scenario_defs: list,
    results: list[dict[str, Any]],
    *,
    running: bool,
) -> tuple[dict[str, Any], dict[str, Any]]:
    result_map = {result["name"]: result for result in results}

    scenario_rows: list[dict[str, Any]] = []
    manifest_rows: list[dict[str, Any]] = []
    for scenario in scenario_defs:
        current = result_map.get(scenario.name)
        scenario_rows.append(
            {
                "name": scenario.name,
                "description": scenario.description,
                "live_claude": scenario.live_claude,
                "success": None if current is None and running else current["success"] if current else None,
                "assertion_count": current["assertion_count"] if current else 0,
                "failed_assertions": current["failed_assertions"] if current else [],
                "status": (
                    "pending"
                    if current is None and running
                    else "pass"
                    if current and current["success"]
                    else "fail"
                    if current
                    else "pending"
                ),
            }
        )
        manifest_rows.append(
            {
                "name": scenario.name,
                "description": scenario.description,
                "live_claude": scenario.live_claude,
                "success": None if current is None and running else current["success"] if current else None,
            }
        )

    final_status = "running" if running else "pass" if all(item["success"] for item in results) else "fail"
    summary = {
        "run_id": output_dir.name,
        "run_dir": str(output_dir),
        "final_status": final_status,
        "completed_count": len(results),
        "scenario_count": len(scenario_defs),
        "scenarios": scenario_rows,
    }
    manifest = {
        "run_id": output_dir.name,
        "run_dir": str(output_dir),
        "generated_by": "smartassist qa run",
        "scenarios": manifest_rows,
    }
    return manifest, summary


def _write_run_metadata(
    output_dir: Path,
    scenario_defs: list,
    results: list[dict[str, Any]],
    *,
    running: bool,
) -> dict[str, Any]:
    manifest, summary = _build_summary(output_dir, scenario_defs, results, running=running)
    write_json(output_dir / "manifest.json", manifest)
    write_json(output_dir / "summary.json", summary)

    lines = [
        f"run_id: {summary['run_id']}",
        f"final_status: {summary['final_status']}",
        f"completed_count: {summary['completed_count']}",
        f"scenario_count: {summary['scenario_count']}",
    ]
    for scenario in summary["scenarios"]:
        status = str(scenario["status"]).upper()
        lines.append(f"{status}: {scenario['name']}")

    (output_dir / "summary.txt").write_text("\n".join(lines) + "\n", encoding="utf-8")
    return summary


def run_scenarios(
    *,
    names: list[str] | None = None,
    run_dir: Path | str | None = None,
    render_demo: bool = True,
    open_demo: bool = False,
    watch: bool = False,
) -> dict[str, Any]:
    scenario_defs = get_scenario_definitions(names)
    output_dir = ensure_run_dir(run_dir, clean=True)
    workspace_dir = output_dir / "workspaces"
    workspace_dir.mkdir(parents=True, exist_ok=True)

    results: list[dict[str, Any]] = []

    if render_demo and watch:
        _write_run_metadata(output_dir, scenario_defs, results, running=watch)
        render_demo_site(
            output_dir,
            open_browser=open_demo,
            auto_refresh_seconds=2 if watch else None,
        )

    for scenario in scenario_defs:
        sandbox = create_scenario_sandbox(scenario.name, workspace_dir / scenario.name)
        try:
            result = scenario.runner(sandbox)
        except Exception as exc:
            result = ScenarioResult(
                name=scenario.name,
                description=scenario.description,
                success=False,
                assertions=[],
                steps=[{"title": "exception", "detail": str(exc)}],
                before_state={},
                after_state={},
                extras={"traceback": traceback.format_exc()},
            )

        _write_scenario_artifacts(output_dir, result)
        results.append(
            {
                "name": result.name,
                "description": result.description,
                "success": result.success,
                "assertion_count": len(result.assertions),
                "failed_assertions": [item.name for item in result.assertions if not item.passed],
            }
        )

        if render_demo and watch:
            _write_run_metadata(output_dir, scenario_defs, results, running=True)
            render_demo_site(output_dir, auto_refresh_seconds=2, open_browser=False)

    summary = _write_run_metadata(output_dir, scenario_defs, results, running=False)

    if render_demo:
        render_demo_site(output_dir, auto_refresh_seconds=None, open_browser=open_demo and not watch)

    return summary
