"""Tests for SmartAssist CLI setup and shell cleanup behavior."""

import json
from pathlib import Path

from smartassist.cli import (
    _backup_file,
    _clean_stale_shell_aliases,
    _cleanup_smartassist_gitignore,
    _configure_hooks,
    _ensure_local_git_excludes,
    _relocate_project_backup_noise,
    _setup_amp,
    _setup_codex,
    _setup_opencode,
    cmd_init,
    cmd_setup,
    cmd_uninstall,
)


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


class TestProjectMetadataCleanup:
    def test_ensure_local_git_excludes_uses_git_info_exclude(self, tmp_path):
        project = tmp_path / "project"
        (project / ".git" / "info").mkdir(parents=True)

        exclude_path = _ensure_local_git_excludes(
            project,
            [".claude/smartassist/", ".mcp.json", ".mcp.json.bak*"],
        )

        assert exclude_path is not None
        assert exclude_path == project / ".git" / "info" / "exclude"
        content = exclude_path.read_text()
        assert ".claude/smartassist/" in content
        assert ".mcp.json" in content
        assert ".mcp.json.bak*" in content

    def test_cleanup_smartassist_gitignore_removes_managed_block(self, tmp_path):
        project = tmp_path / "project"
        project.mkdir()
        gitignore = project / ".gitignore"
        gitignore.write_text(
            "node_modules/\n# SmartAssist data\n.claude/smartassist/\n"
        )

        removed = _cleanup_smartassist_gitignore(project)

        assert removed is True
        assert gitignore.read_text() == "node_modules/\n"

    def test_relocate_project_backup_noise_moves_mcp_backups_outside_repo(
        self, monkeypatch, tmp_path
    ):
        home = tmp_path / "home"
        home.mkdir()
        monkeypatch.setenv("HOME", str(home))
        project = tmp_path / "project"
        project.mkdir()
        backup = project / ".mcp.json.bak.20260407_000433"
        backup.write_text("{}")

        moved = _relocate_project_backup_noise(project)

        assert len(moved) == 1
        assert moved[0].exists()
        assert not backup.exists()

    def test_backup_file_can_write_to_external_directory(self, tmp_path):
        source = tmp_path / ".mcp.json"
        source.write_text("{}")
        backup_dir = tmp_path / "backups"
        summary = []

        _backup_file(source, summary, backup_dir=backup_dir)

        backups = list(backup_dir.glob(".mcp.json.bak.*"))
        assert len(backups) == 1
        assert backups[0].read_text() == "{}"


class TestCliSetupLifecycle:
    def test_configure_hooks_uses_expanded_pretool_matcher(self):
        settings = {}
        summary = []

        _configure_hooks(settings, summary)

        pretool = settings["hooks"]["PreToolUse"][0]
        assert pretool["matcher"] == "Bash|Edit|Write"
        assert pretool["hooks"][0]["command"] == "smartassist-commit-hook"

    def test_setup_registers_project_scope_without_hardcoded_data_dir(
        self,
        monkeypatch,
        tmp_path,
    ):
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
        (claude_dir / "mcp.json").write_text(
            json.dumps(
                {
                    "mcpServers": {
                        "smartassist": {"command": "smartassist", "args": ["serve"]}
                    }
                }
            )
        )

        project_dir = tmp_path / "project"
        (project_dir / ".git" / "info").mkdir(parents=True)
        (project_dir / ".mcp.json").write_text(
            json.dumps({"mcpServers": {"smartassist": {"command": "python"}}})
        )
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

        add_call = next(
            cmd
            for cmd in calls
            if cmd[:6]
            == ["/usr/local/bin/claude", "mcp", "add", "smartassist", "-s", "project"]
        )
        assert rc == 0
        assert not any("SMARTASSIST_DATA_DIR=" in part for part in add_call)
        user_config = json.loads((tmp_path / ".claude.json").read_text())
        assert "smartassist" not in user_config.get("mcpServers", {})
        legacy_config = json.loads((claude_dir / "mcp.json").read_text())
        assert "smartassist" not in legacy_config.get("mcpServers", {})
        assert not list(project_dir.glob(".mcp.json.bak.*"))
        assert list((tmp_path / ".smartassist" / "backups").rglob(".mcp.json.bak.*"))
        exclude_text = (project_dir / ".git" / "info" / "exclude").read_text()
        assert ".mcp.json" in exclude_text

    def test_setup_keeps_two_projects_ready_without_reconfiguration(
        self,
        monkeypatch,
        tmp_path,
    ):
        home = tmp_path / "home"
        home.mkdir()
        claude_dir = home / ".claude"
        claude_dir.mkdir(exist_ok=True)
        (claude_dir / "settings.json").write_text("{}")
        monkeypatch.setenv("HOME", str(home))
        monkeypatch.setattr("smartassist.cli.shutil.which", _which_all)

        def fake_run(cmd, capture_output=False, text=False):
            if cmd[:6] == [
                "/usr/local/bin/claude",
                "mcp",
                "add",
                "smartassist",
                "-s",
                "project",
            ]:
                env = {}
                idx = 6
                while idx < len(cmd):
                    token = cmd[idx]
                    if token == "-e":
                        key, value = cmd[idx + 1].split("=", 1)
                        env[key] = value
                        idx += 2
                        continue
                    if token == "--":
                        idx += 1
                        break
                    idx += 1

                command = cmd[idx]
                args = cmd[idx + 1 :]
                mcp_path = Path.cwd() / ".mcp.json"
                mcp_path.write_text(
                    json.dumps(
                        {
                            "mcpServers": {
                                "smartassist": {
                                    "type": "stdio",
                                    "command": command,
                                    "args": args,
                                    "env": env,
                                }
                            }
                        }
                    )
                )

            class Result:
                returncode = 0
                stderr = ""

            return Result()

        monkeypatch.setattr("smartassist.cli.subprocess.run", fake_run)

        project_a = tmp_path / "project-a"
        project_b = tmp_path / "project-b"
        project_a.mkdir()
        project_b.mkdir()

        monkeypatch.chdir(project_a)
        assert cmd_setup() == 0
        config_a = json.loads((project_a / ".mcp.json").read_text())
        assert "smartassist" in config_a["mcpServers"]
        assert "SMARTASSIST_DATA_DIR" not in config_a["mcpServers"]["smartassist"].get(
            "env", {}
        )

        monkeypatch.chdir(project_b)
        assert cmd_setup() == 0
        config_b = json.loads((project_b / ".mcp.json").read_text())
        assert "smartassist" in config_b["mcpServers"]
        assert "SMARTASSIST_DATA_DIR" not in config_b["mcpServers"]["smartassist"].get(
            "env", {}
        )

        config_a_after = json.loads((project_a / ".mcp.json").read_text())
        assert config_a_after == config_a

    def test_init_writes_project_mcp_registration_when_claude_cli_unavailable(
        self,
        monkeypatch,
        tmp_path,
    ):
        monkeypatch.setenv("HOME", str(tmp_path))
        project_dir = tmp_path / "project"
        project_dir.mkdir()
        monkeypatch.chdir(project_dir)
        monkeypatch.setattr(
            "smartassist.cli._resolve_mcp_server_command",
            lambda: ("python", ["-m", "smartassist.mcp_server"]),
        )
        monkeypatch.setattr(
            "smartassist.cli.shutil.which",
            lambda name: (
                "/usr/local/bin/smartassist" if name == "smartassist" else None
            ),
        )

        rc = cmd_init()

        assert rc == 0
        mcp_config = json.loads((project_dir / ".mcp.json").read_text())
        entry = mcp_config["mcpServers"]["smartassist"]
        assert entry["type"] == "stdio"
        assert entry["command"] == "python"
        assert entry["args"] == ["-m", "smartassist.mcp_server"]
        assert "SMARTASSIST_DATA_DIR" not in entry.get("env", {})

    def test_init_prefers_git_info_exclude_and_cleans_old_smartassist_gitignore(
        self,
        monkeypatch,
        tmp_path,
    ):
        monkeypatch.setenv("HOME", str(tmp_path))
        project_dir = tmp_path / "project"
        (project_dir / ".git" / "info").mkdir(parents=True)
        (project_dir / ".gitignore").write_text(
            "node_modules/\n# SmartAssist data\n.claude/smartassist/\n"
        )
        monkeypatch.chdir(project_dir)
        monkeypatch.setattr(
            "smartassist.cli._resolve_mcp_server_command",
            lambda: ("python", ["-m", "smartassist.mcp_server"]),
        )
        monkeypatch.setattr(
            "smartassist.cli.shutil.which",
            lambda name: (
                "/usr/local/bin/smartassist" if name == "smartassist" else None
            ),
        )

        rc = cmd_init()

        assert rc == 0
        exclude_text = (project_dir / ".git" / "info" / "exclude").read_text()
        assert ".claude/smartassist/" in exclude_text
        assert ".mcp.json" in exclude_text
        assert ".mcp.json.bak*" in exclude_text
        assert (project_dir / ".gitignore").read_text() == "node_modules/\n"

    def test_uninstall_removes_project_and_legacy_registration(
        self, monkeypatch, tmp_path
    ):
        monkeypatch.setenv("HOME", str(tmp_path))
        claude_dir = tmp_path / ".claude"
        claude_dir.mkdir(exist_ok=True)
        project_dir = tmp_path / "project"
        project_dir.mkdir()
        monkeypatch.chdir(project_dir)
        (tmp_path / ".claude.json").write_text(
            json.dumps(
                {
                    "mcpServers": {
                        "smartassist": {
                            "command": "python",
                            "args": ["-m", "smartassist.mcp_server"],
                        }
                    },
                    "projects": {
                        str(project_dir): {
                            "mcpServers": {
                                "smartassist": {
                                    "command": "python",
                                    "args": ["-m", "smartassist.mcp_server"],
                                },
                                "playwright": {"command": "playwright"},
                            }
                        }
                    },
                }
            )
        )
        (project_dir / ".mcp.json").write_text(
            json.dumps(
                {
                    "mcpServers": {
                        "smartassist": {"command": "python"},
                        "playwright": {"command": "playwright"},
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

        rc = cmd_uninstall()

        assert rc == 0
        project_config = json.loads((project_dir / ".mcp.json").read_text())
        assert "smartassist" not in project_config.get("mcpServers", {})
        assert "playwright" in project_config.get("mcpServers", {})
        user_config = json.loads((tmp_path / ".claude.json").read_text())
        assert "smartassist" not in user_config.get("mcpServers", {})
        assert "playwright" in user_config["projects"][str(project_dir)]["mcpServers"]
        assert (
            "smartassist" not in user_config["projects"][str(project_dir)]["mcpServers"]
        )
        legacy_config = json.loads(legacy_path.read_text())
        assert "smartassist" not in legacy_config.get("mcpServers", {})
        assert "playwright" in legacy_config.get("mcpServers", {})


class TestSetupAgentArtifacts:
    def test_setup_codex_writes_global_guidance(self, monkeypatch, tmp_path):
        monkeypatch.setenv("HOME", str(tmp_path))

        def _raise_missing(*args, **kwargs):
            raise FileNotFoundError()

        monkeypatch.setattr("smartassist.cli.subprocess.run", _raise_missing)
        monkeypatch.setattr(
            "smartassist.cli._resolve_mcp_server_command",
            lambda: ("/tmp/smartassist-python", ["-m", "smartassist.mcp_server"]),
        )

        _setup_codex()

        agents_path = tmp_path / ".codex" / "AGENTS.md"
        config_path = tmp_path / ".codex" / "config.toml"
        assert agents_path.exists()
        assert "apply_feedback_protocol" in agents_path.read_text()
        assert (
            ".agents/skills/smartassist-memory-architect/SKILL.md"
            in agents_path.read_text()
        )
        config_text = config_path.read_text()
        assert "project_doc_fallback_filenames" in config_text
        assert config_text.index("project_doc_fallback_filenames") < config_text.index(
            "[mcp_servers.smartassist]"
        )
        assert 'command = "/tmp/smartassist-python"' in config_text
        assert '"smartassist.mcp_server"' in config_text

    def test_setup_amp_writes_workspace_skill(self, monkeypatch, tmp_path):
        project_dir = tmp_path / "project"
        project_dir.mkdir()
        monkeypatch.chdir(project_dir)

        _setup_amp()

        skill_path = (
            project_dir / ".agents" / "skills" / "smartassist-memory" / "SKILL.md"
        )
        assert skill_path.exists()
        assert "apply_feedback_protocol" in skill_path.read_text()

    def test_setup_opencode_writes_instruction_file(self, monkeypatch, tmp_path):
        project_dir = tmp_path / "project"
        project_dir.mkdir()
        monkeypatch.chdir(project_dir)
        monkeypatch.setattr(
            "smartassist.cli._resolve_mcp_server_command",
            lambda: ("/tmp/smartassist-python", ["-m", "smartassist.mcp_server"]),
        )

        _setup_opencode()

        config = json.loads((project_dir / "opencode.json").read_text())
        assert config["model"] == "anthropic/claude-sonnet-4-5"
        assert config["small_model"] == "anthropic/claude-haiku-4-5"
        assert ".smartassist/opencode-instructions.md" in config["instructions"]
        assert config["mcp"]["smartassist"]["command"] == [
            "/tmp/smartassist-python",
            "-m",
            "smartassist.mcp_server",
        ]
        instructions_path = project_dir / ".smartassist" / "opencode-instructions.md"
        assert instructions_path.exists()
        assert "apply_feedback_protocol" in instructions_path.read_text()

    def test_setup_opencode_preserves_existing_models(self, monkeypatch, tmp_path):
        project_dir = tmp_path / "project"
        project_dir.mkdir()
        monkeypatch.chdir(project_dir)
        (project_dir / "opencode.json").write_text(
            json.dumps(
                {
                    "model": "openai/gpt-5",
                    "small_model": "openai/gpt-5-mini",
                }
            )
        )
        monkeypatch.setattr(
            "smartassist.cli._resolve_mcp_server_command",
            lambda: ("/tmp/smartassist-python", ["-m", "smartassist.mcp_server"]),
        )

        _setup_opencode()

        config = json.loads((project_dir / "opencode.json").read_text())
        assert config["model"] == "openai/gpt-5"
        assert config["small_model"] == "openai/gpt-5-mini"
        assert config["mcp"]["smartassist"]["enabled"] is True
