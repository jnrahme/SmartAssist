import json
from datetime import datetime
from pathlib import Path
from unittest.mock import patch

from smartassist.cli import cmd_init, cmd_telemetry
from smartassist.store import (
    append_feedback_event,
    initialize_store,
    save_reliabilities_dict,
)
from smartassist.telemetry import (
    build_export_bundle,
    enable_telemetry,
    flush_bundle,
    get_aggregate_summary,
    get_telemetry_status,
    ingest_bundle,
    record_lifecycle_event,
    register_project,
)
from smartassist.tools.generate_telemetry_dashboard import generate_dashboard


def _write_usage_events(storage_path: Path, events: list[dict]) -> None:
    usage_log = storage_path / "usage_log.jsonl"
    usage_log.parent.mkdir(parents=True, exist_ok=True)
    usage_log.write_text("\n".join(json.dumps(event) for event in events) + "\n")


def _make_storage(tmp_path: Path, name: str) -> Path:
    storage = tmp_path / name / ".claude" / "smartassist" / "data"
    initialize_store(storage)
    return storage


def test_enable_status_and_register_project(monkeypatch, tmp_path):
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("HOME", str(home))

    storage = _make_storage(tmp_path, "project-one")
    config = enable_telemetry("http://127.0.0.1:8787")
    register_project(storage)
    record_lifecycle_event("install_started", agent_type="claude")

    status = get_telemetry_status()

    assert status["enabled"] is True
    assert status["install_id"] == config["install_id"]
    assert status["endpoint"] == "http://127.0.0.1:8787"
    assert status["known_projects"] == 1
    assert status["queued_events"] == 1


def test_build_export_bundle_aggregates_known_projects(monkeypatch, tmp_path):
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("HOME", str(home))

    fixed_now = datetime(2026, 4, 7, 12, 0, 0)
    storage_a = _make_storage(tmp_path, "project-a")
    storage_b = _make_storage(tmp_path, "project-b")

    _write_usage_events(
        storage_a,
        [
            {
                "timestamp": fixed_now.isoformat(),
                "tool": "rag_search",
                "query": "theme tokens",
                "results_count": 2,
                "latency_ms": 11,
            },
            {
                "timestamp": fixed_now.isoformat(),
                "tool": "rag_dashboard",
                "query": "",
                "results_count": 0,
                "latency_ms": 8,
            },
        ],
    )
    _write_usage_events(
        storage_b,
        [
            {
                "timestamp": fixed_now.isoformat(),
                "tool": "rag_search",
                "query": "git hooks",
                "results_count": 0,
                "latency_ms": 9,
            },
            {
                "timestamp": fixed_now.isoformat(),
                "tool": "rag_search",
                "query": "doctor readiness",
                "results_count": 1,
                "latency_ms": 14,
            },
        ],
    )

    append_feedback_event(
        storage_a,
        {
            "timestamp": fixed_now.timestamp(),
            "signal": "thumbs_up",
            "category": "code_edit",
            "intensity": 3,
        },
    )
    append_feedback_event(
        storage_b,
        {
            "timestamp": fixed_now.timestamp(),
            "signal": "thumbs_down",
            "category": "git",
            "intensity": 3,
        },
    )
    append_feedback_event(
        storage_b,
        {
            "timestamp": fixed_now.timestamp(),
            "signal": "correction",
            "category": "testing",
            "intensity": 4,
        },
    )

    save_reliabilities_dict(
        storage_a,
        {
            "testing": {
                "alpha": 1.0,
                "beta": 3.0,
                "last_updated": 1.0,
                "total_samples": 4,
            },
            "code_edit": {
                "alpha": 4.0,
                "beta": 1.0,
                "last_updated": 1.0,
                "total_samples": 5,
            },
        },
    )
    save_reliabilities_dict(
        storage_b,
        {
            "git": {"alpha": 2.0, "beta": 4.0, "last_updated": 1.0, "total_samples": 6},
        },
    )

    enable_telemetry()
    register_project(storage_a)
    register_project(storage_b)
    record_lifecycle_event(
        "install_started",
        agent_type="claude",
        occurred_at=fixed_now.isoformat(),
    )
    record_lifecycle_event(
        "setup_completed",
        agent_type="claude",
        occurred_at=fixed_now.isoformat(),
    )
    record_lifecycle_event(
        "doctor_ready",
        agent_type="claude",
        occurred_at=fixed_now.isoformat(),
    )
    record_lifecycle_event(
        "agent_configured",
        agent_type="codex",
        occurred_at=fixed_now.isoformat(),
    )

    bundle = build_export_bundle(now=fixed_now)
    weekly = bundle["weekly_rollups"][0]

    assert weekly["known_projects"] == 2
    assert weekly["searches"] == 3
    assert weekly["searches_with_results"] == 2
    assert weekly["rag_dashboards"] == 1
    assert weekly["positive_feedback"] == 1
    assert weekly["negative_feedback"] == 2
    assert weekly["install_started"] == 1
    assert weekly["setup_completed"] == 1
    assert weekly["doctor_ready"] == 1
    assert weekly["agent_counts"]["claude"] == 3
    assert weekly["agent_counts"]["codex"] == 1
    assert weekly["weak_categories"]["testing"] == 1
    assert weekly["weak_categories"]["git"] == 1


def test_flush_bundle_posts_to_collector(monkeypatch, tmp_path):
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("HOME", str(home))
    enable_telemetry("http://localhost:9999")

    captured = {}

    class _Response:
        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):
            return False

        def read(self):
            return b'{"status":"accepted"}'

    def _fake_urlopen(request, timeout=0):
        captured["url"] = request.full_url
        captured["payload"] = json.loads(request.data.decode("utf-8"))
        captured["timeout"] = timeout
        return _Response()

    monkeypatch.setattr("smartassist.telemetry.urlopen", _fake_urlopen)

    ok, result = flush_bundle()

    assert ok is True
    assert isinstance(result, dict)
    assert captured["url"] == "http://localhost:9999/ingest"
    assert captured["payload"]["schema_version"] == 1
    assert result["response"]["status"] == "accepted"


def test_ingest_bundle_and_generate_dashboard(monkeypatch, tmp_path):
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("HOME", str(home))
    fixed_now = datetime(2026, 4, 7, 12, 0, 0)
    storage = _make_storage(tmp_path, "project-dashboard")

    _write_usage_events(
        storage,
        [
            {
                "timestamp": fixed_now.isoformat(),
                "tool": "rag_search",
                "query": "semantic colors",
                "results_count": 1,
                "latency_ms": 7,
            }
        ],
    )
    append_feedback_event(
        storage,
        {
            "timestamp": fixed_now.timestamp(),
            "signal": "thumbs_up",
            "category": "code_edit",
            "intensity": 3,
        },
    )

    enable_telemetry()
    register_project(storage)
    record_lifecycle_event(
        "install_started",
        agent_type="claude",
        occurred_at=fixed_now.isoformat(),
    )
    record_lifecycle_event(
        "setup_completed",
        agent_type="claude",
        occurred_at=fixed_now.isoformat(),
    )
    record_lifecycle_event(
        "doctor_ready",
        agent_type="claude",
        occurred_at=fixed_now.isoformat(),
    )

    bundle = build_export_bundle(now=fixed_now)
    db_path = tmp_path / "aggregate.db"
    ingest_result = ingest_bundle(db_path, bundle)
    summary = get_aggregate_summary(db_path)
    output = generate_dashboard(db_path, output_path=tmp_path / "aggregate.html")

    assert ingest_result["events_inserted"] >= 3
    assert summary["installs_total"] == 1
    assert summary["active_installs_latest_week"] == 1
    assert summary["setup_conversion"] == 1.0
    assert summary["ready_rate"] == 1.0
    assert summary["search_success_rate"] == 1.0
    assert output.exists()
    html = output.read_text()
    assert "SmartAssist Aggregate KPI Dashboard" in html
    assert "Current Week Funnel" in html
    assert "Version Comparison" in html


def test_cmd_init_registers_telemetry_project(monkeypatch, tmp_path):
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("HOME", str(home))
    enable_telemetry()

    project_dir = tmp_path / "project"
    project_dir.mkdir()
    monkeypatch.chdir(project_dir)
    monkeypatch.setattr(
        "smartassist.cli._resolve_mcp_server_command",
        lambda: ("python", ["-m", "smartassist.mcp_server"]),
    )
    monkeypatch.setattr(
        "smartassist.cli.shutil.which",
        lambda name: "/usr/local/bin/smartassist" if name == "smartassist" else None,
    )

    rc = cmd_init()
    status = get_telemetry_status()

    assert rc == 0
    assert status["known_projects"] == 1
    assert status["queued_events"] >= 1


def test_cmd_telemetry_enable_and_status(monkeypatch, tmp_path, capsys):
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("HOME", str(home))

    with patch(
        "sys.argv",
        [
            "smartassist",
            "telemetry",
            "enable",
            "--endpoint",
            "http://127.0.0.1:8787",
        ],
    ):
        assert cmd_telemetry() == 0

    with patch("sys.argv", ["smartassist", "telemetry", "status"]):
        assert cmd_telemetry() == 0

    output = capsys.readouterr().out
    assert "Anonymous telemetry enabled." in output
    assert "Telemetry enabled: yes" in output
    assert "http://127.0.0.1:8787" in output
