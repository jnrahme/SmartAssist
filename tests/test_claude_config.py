"""Tests for shared Claude Code MCP config helpers."""

import json

from smartassist.claude_config import (
    get_mcp_status,
    get_registered_mcp_entries,
    remove_legacy_mcp_servers,
)


class TestClaudeConfigHelpers:
    def test_prefers_project_local_mcp_over_user_and_legacy(self, monkeypatch, tmp_path):
        monkeypatch.setenv("HOME", str(tmp_path))
        (tmp_path / ".claude").mkdir(exist_ok=True)

        project = tmp_path / "project"
        (project / "src").mkdir(parents=True)
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
        (tmp_path / ".claude" / "mcp.json").write_text(
            json.dumps(
                {
                    "mcpServers": {
                        "smartassist": {
                            "command": "smartassist",
                            "args": ["serve"],
                        }
                    }
                }
            )
        )

        monkeypatch.chdir(project / "src")
        status = get_mcp_status()

        assert status["registered"] is True
        assert status["source"] == "project_local"
        assert status["source_label"] == f"{project / '.mcp.json'} (project)"
        assert "~/.claude.json (user)" in status["duplicate_sources"]
        assert "~/.claude/mcp.json (legacy)" in status["duplicate_sources"]

    def test_detects_project_scoped_registration(self, monkeypatch, tmp_path):
        monkeypatch.setenv("HOME", str(tmp_path))
        project = tmp_path / "project"
        (project / "src").mkdir(parents=True)
        (tmp_path / ".claude.json").write_text(
            json.dumps(
                {
                    "projects": {
                        str(project): {
                            "mcpServers": {
                                "smartassist": {
                                    "command": "python",
                                    "args": ["-m", "smartassist.mcp_server"],
                                }
                            }
                        }
                    }
                }
            )
        )

        entries = get_registered_mcp_entries(start_path=project / "src")

        assert len(entries) == 1
        assert entries[0]["source"] == "project"
        assert entries[0]["source_label"] == f"~/.claude.json (project: {project})"
        assert entries[0]["applies_to_current_context"] is True

    def test_ignores_unrelated_project_scoped_registration(self, monkeypatch, tmp_path):
        monkeypatch.setenv("HOME", str(tmp_path))
        current_project = tmp_path / "current-project"
        current_project.mkdir()
        other_project = tmp_path / "other-project"
        other_project.mkdir()
        (tmp_path / ".claude.json").write_text(
            json.dumps(
                {
                    "projects": {
                        str(other_project): {
                            "mcpServers": {
                                "smartassist": {
                                    "command": "python",
                                    "args": ["-m", "smartassist.mcp_server"],
                                }
                            }
                        }
                    }
                }
            )
        )

        monkeypatch.chdir(current_project)
        status = get_mcp_status()

        assert status["registered"] is False
        assert status["entries"] == []
        assert len(status["all_entries"]) == 1

    def test_remove_legacy_mcp_servers_only_touches_smartassist_entries(
        self,
        monkeypatch,
        tmp_path,
    ):
        monkeypatch.setenv("HOME", str(tmp_path))
        legacy_dir = tmp_path / ".claude"
        legacy_dir.mkdir(exist_ok=True)
        legacy_path = legacy_dir / "mcp.json"
        legacy_path.write_text(
            json.dumps(
                {
                    "mcpServers": {
                        "smartassist": {"command": "smartassist"},
                        "rag-knowledge": {"command": "python"},
                        "playwright": {"command": "playwright"},
                    }
                }
            )
        )

        removed = remove_legacy_mcp_servers()
        updated = json.loads(legacy_path.read_text())

        assert removed is True
        assert "smartassist" not in updated["mcpServers"]
        assert "rag-knowledge" not in updated["mcpServers"]
        assert "playwright" in updated["mcpServers"]
