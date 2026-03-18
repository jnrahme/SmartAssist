"""Tests for SmartAssist CLI setup and shell cleanup behavior."""

import json

from smartassist.cli import _clean_stale_shell_aliases, cmd_setup, cmd_uninstall


def _which_all(name: str) -> str | None:
    paths = {
        "smartassist": "/usr/local/bin/smartassist",
        "claude": "/usr/local/bin/claude",
        "claude-sa": "/usr/local/bin/claude-sa",
        "smartassist-prompt-inject": "/usr/local/bin/smartassist-prompt-inject",
        "smartassist-session-start": "/usr/local/bin/smartassist-session-start",
        "smartassist-session-end": "/usr/local/bin/smartassist-session-end",
        "smartassist-commit-hook": "/usr/local/bin/smartassist-commit-hook",
        "smartassist-show-lessons": "/usr/local/bin/smartassist-show-lessons",
    }
    return paths.get(name)


class TestShellCleanup:
    def test_preserves_unrelated_comments(self, monkeypatch, tmp_path):
        monkeypatch.setenv("HOME", str(tmp_path))
        rc_path = tmp_path / ".zshrc"
        rc_path.write_text("# SmartAssist notes for myself\nexport FOO=bar\n")

        removed = _clean_stale_shell_aliases()

        assert removed == []
        assert rc_path.read_text() == "# SmartAssist notes for myself\nexport FOO=bar\n"


class TestCliSetupLifecycle:
    def test_setup_registers_without_hardcoded_data_dir(
        self,
        monkeypatch,
        tmp_path,
    ):
        monkeypatch.setenv("HOME", str(tmp_path))
        claude_dir = tmp_path / ".claude"
        claude_dir.mkdir(exist_ok=True)
        (claude_dir / "settings.json").write_text("{}")
        (claude_dir / "mcp.json").write_text(
            json.dumps(
                {"mcpServers": {"smartassist": {"command": "smartassist", "args": ["serve"]}}}
            )
        )

        project_dir = tmp_path / "project"
        project_dir.mkdir()
        monkeypatch.chdir(project_dir)
        monkeypatch.setattr("smartassist.cli.shutil.which", _which_all)

        calls = []

        def fake_run(cmd, capture_output=False, text=False):
            calls.append(cmd)

            class Result:
                returncode = 0
                stderr = ""

            return Result()

        monkeypatch.setattr("smartassist.cli.subprocess.run", fake_run)

        rc = cmd_setup()

        add_call = next(cmd for cmd in calls if cmd[:4] == ["/usr/local/bin/claude", "mcp", "add", "smartassist"])
        assert rc == 0
        assert not any("SMARTASSIST_DATA_DIR=" in part for part in add_call)
        legacy_config = json.loads((claude_dir / "mcp.json").read_text())
        assert "smartassist" not in legacy_config.get("mcpServers", {})

    def test_uninstall_removes_legacy_registration(self, monkeypatch, tmp_path):
        monkeypatch.setenv("HOME", str(tmp_path))
        claude_dir = tmp_path / ".claude"
        claude_dir.mkdir(exist_ok=True)
        (tmp_path / ".claude.json").write_text(
            json.dumps(
                {
                    "mcpServers": {
                        "smartassist": {"command": "python", "args": ["-m", "smartassist.mcp_server"]}
                    }
                }
            )
        )
        legacy_path = claude_dir / "mcp.json"
        legacy_path.write_text(
            json.dumps(
                {
                    "mcpServers": {
                        "smartassist": {"command": "smartassist"},
                        "playwright": {"command": "playwright"},
                    }
                }
            )
        )
        (claude_dir / "settings.json").write_text("{}")
        monkeypatch.setattr("smartassist.cli.shutil.which", _which_all)

        calls = []

        def fake_run(cmd, capture_output=False, text=False):
            calls.append(cmd)

            class Result:
                returncode = 0
                stderr = ""

            return Result()

        monkeypatch.setattr("smartassist.cli.subprocess.run", fake_run)

        rc = cmd_uninstall()

        assert rc == 0
        assert any(cmd[:4] == ["/usr/local/bin/claude", "mcp", "remove", "smartassist"] for cmd in calls)
        legacy_config = json.loads(legacy_path.read_text())
        assert "smartassist" not in legacy_config.get("mcpServers", {})
        assert "playwright" in legacy_config.get("mcpServers", {})
