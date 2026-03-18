"""Tests for shared Claude Code MCP config helpers."""

import json

from smartassist.claude_config import (
    get_mcp_status,
    get_registered_mcp_entries,
    remove_legacy_mcp_servers,
)


class TestClaudeConfigHelpers:
    def test_prefers_modern_claude_json_over_legacy(self, monkeypatch, tmp_path):
        monkeypatch.setenv("HOME", str(tmp_path))
        (tmp_path / ".claude").mkdir(exist_ok=True)

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

        status = get_mcp_status()

        assert status["registered"] is True
        assert status["source"] == "user"
        assert status["source_label"] == "~/.claude.json (user)"
        assert "~/.claude/mcp.json (legacy)" in status["duplicate_sources"]

    def test_detects_project_scoped_registration(self, monkeypatch, tmp_path):
        monkeypatch.setenv("HOME", str(tmp_path))
        (tmp_path / ".claude.json").write_text(
            json.dumps(
                {
                    "projects": {
                        "/tmp/project": {
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

        entries = get_registered_mcp_entries()

        assert len(entries) == 1
        assert entries[0]["source"] == "project"
        assert entries[0]["source_label"] == "~/.claude.json (project: /tmp/project)"

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
