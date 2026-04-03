"""Tests for SmartAssist QA demo generation."""

from pathlib import Path
from unittest.mock import patch

from smartassist.cli import main as cli_main
from smartassist.qa.demo import render_demo_site
from smartassist.qa.runner import run_scenarios


def test_render_demo_site_from_real_run(tmp_path):
    summary = run_scenarios(
        names=["feedback_creates_active_lesson"],
        run_dir=tmp_path / "demo-run",
        render_demo=False,
    )

    destination = render_demo_site(summary["run_dir"], auto_refresh_seconds=2)
    html_text = destination.read_text(encoding="utf-8")

    assert destination.exists()
    assert "SmartAssist QA Demo" in html_text
    assert "feedback_creates_active_lesson" in html_text
    assert "PASS" in html_text
    assert "summary.json" in html_text
    assert 'http-equiv="refresh"' in html_text


def test_cli_qa_demo_renders_html(tmp_path, capsys):
    summary = run_scenarios(
        names=["doctor_rejects_false_ready"],
        run_dir=tmp_path / "cli-demo-run",
        render_demo=False,
    )
    output = tmp_path / "custom-demo.html"

    with patch(
        "sys.argv",
        ["smartassist", "qa", "demo", "--run-dir", summary["run_dir"], "--output", str(output)],
    ):
        rc = cli_main()

    assert rc == 0
    assert output.exists()
    assert str(output) in capsys.readouterr().out


def test_demo_page_supports_pending_cards(tmp_path):
    run_dir = tmp_path / "pending-demo"
    run_dir.mkdir(parents=True)
    (run_dir / "summary.json").write_text(
        '{"run_id":"qa-pending","run_dir":"pending","final_status":"running","completed_count":0,"scenario_count":1,"scenarios":[{"name":"feedback_creates_active_lesson","description":"demo","live_claude":false,"success":null,"assertion_count":0,"failed_assertions":[],"status":"pending"}]}',
        encoding="utf-8",
    )
    (run_dir / "manifest.json").write_text(
        '{"run_id":"qa-pending","run_dir":"pending","generated_by":"smartassist qa run","scenarios":[{"name":"feedback_creates_active_lesson","description":"demo","live_claude":false,"success":null}]}',
        encoding="utf-8",
    )

    destination = render_demo_site(run_dir)
    html_text = destination.read_text(encoding="utf-8")

    assert "PENDING" in html_text
    assert "Scenario has not run yet." in html_text
