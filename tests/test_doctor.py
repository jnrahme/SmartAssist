"""Tests for the SmartAssist doctor command helpers."""

import json

from smartassist.tools.doctor import collect_doctor_report


def _hook_settings():
    hooks = {}
    commands = {
        "UserPromptSubmit": ("smartassist-prompt-inject", None),
        "SessionStart": ("smartassist-session-start", "startup"),
        "PreToolUse": ("smartassist-commit-hook", "Bash|Edit|Write"),
        "PostToolUse": ("smartassist-show-lessons", "mcp__smartassist__rag_search"),
        "SessionEnd": ("smartassist-session-end", "other"),
    }
    for event, (command, matcher) in commands.items():
        entry = {"hooks": [{"type": "command", "command": command}]}
        if matcher:
            entry["matcher"] = matcher
        hooks[event] = [entry]
    return {"hooks": hooks}


class TestDoctorReport:
    def test_collect_doctor_report_ready(self, monkeypatch, tmp_path):
        monkeypatch.setenv("HOME", str(tmp_path))

        claude_dir = tmp_path / ".claude"
        claude_dir.mkdir(exist_ok=True)
        (claude_dir / "settings.json").write_text(json.dumps(_hook_settings()))

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
        data_dir = project / ".claude" / "smartassist"
        (data_dir / "data").mkdir(parents=True)
        (data_dir / "lancedb").mkdir()
        (data_dir / "data" / "feedback_log.jsonl").write_text("")
        (data_dir / "data" / "reliability_scores.json").write_text("{}")
        (data_dir / "data" / "vectorization_log.json").write_text(
            json.dumps({"total_vectorized": 0, "last_vectorization": None})
        )

        monkeypatch.setenv("SMARTASSIST_DATA_DIR", str(data_dir))
        monkeypatch.chdir(project)
        report = collect_doctor_report()

        assert report["overall_status"] == "ready"
        assert all(check["status"] == "ok" for check in report["checks"])

    def test_collect_doctor_report_fails_when_hooks_missing(self, monkeypatch, tmp_path):
        monkeypatch.setenv("HOME", str(tmp_path))

        claude_dir = tmp_path / ".claude"
        claude_dir.mkdir(exist_ok=True)
        (claude_dir / "settings.json").write_text("{}")
        (tmp_path / ".claude.json").write_text(
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

        project = tmp_path / "project"
        data_dir = project / ".claude" / "smartassist"
        (data_dir / "data").mkdir(parents=True)
        (data_dir / "lancedb").mkdir()
        (data_dir / "data" / "feedback_log.jsonl").write_text("")
        (data_dir / "data" / "reliability_scores.json").write_text("{}")
        (data_dir / "data" / "vectorization_log.json").write_text(
            json.dumps({"total_vectorized": 0, "last_vectorization": None})
        )

        monkeypatch.setenv("SMARTASSIST_DATA_DIR", str(data_dir))
        monkeypatch.chdir(project)
        report = collect_doctor_report()

        assert report["overall_status"] == "fail"
        assert any(
            check["name"] == "Hooks" and check["status"] == "fail"
            for check in report["checks"]
        )

    def test_collect_doctor_report_fails_when_pretool_matcher_is_stale(self, monkeypatch, tmp_path):
        monkeypatch.setenv("HOME", str(tmp_path))

        claude_dir = tmp_path / ".claude"
        claude_dir.mkdir(exist_ok=True)
        stale_settings = _hook_settings()
        stale_settings["hooks"]["PreToolUse"][0]["matcher"] = "Bash"
        (claude_dir / "settings.json").write_text(json.dumps(stale_settings))
        (tmp_path / ".claude.json").write_text(
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

        project = tmp_path / "project"
        data_dir = project / ".claude" / "smartassist"
        (data_dir / "data").mkdir(parents=True)
        (data_dir / "lancedb").mkdir()
        (data_dir / "data" / "feedback_log.jsonl").write_text("")
        (data_dir / "data" / "reliability_scores.json").write_text("{}")
        (data_dir / "data" / "vectorization_log.json").write_text(
            json.dumps({"total_vectorized": 0, "last_vectorization": None})
        )

        monkeypatch.setenv("SMARTASSIST_DATA_DIR", str(data_dir))
        monkeypatch.chdir(project)
        report = collect_doctor_report()

        assert report["overall_status"] == "fail"
        assert any(
            check["name"] == "Hooks"
            and "Bash|Edit|Write" in check["detail"]
            for check in report["checks"]
        )
