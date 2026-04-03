"""Sandbox helpers for SmartAssist QA scenarios."""

from __future__ import annotations

import os
import stat
import sys
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path

from smartassist.claude_config import ensure_project_mcp_server
from smartassist.config import atomic_write_json
from smartassist.store import initialize_store


@dataclass(frozen=True)
class ScenarioSandbox:
    name: str
    repo_root: Path
    workspace_root: Path
    home_dir: Path
    project_root: Path
    data_dir: Path
    storage_path: Path
    lancedb_path: Path
    bin_dir: Path

    @contextmanager
    def activate(self):
        old_cwd = Path.cwd()
        old_env = {
            "HOME": os.environ.get("HOME"),
            "PATH": os.environ.get("PATH"),
            "PYTHONPATH": os.environ.get("PYTHONPATH"),
            "SMARTASSIST_DATA_DIR": os.environ.get("SMARTASSIST_DATA_DIR"),
        }
        merged_pythonpath = str(self.repo_root)
        if old_env["PYTHONPATH"]:
            merged_pythonpath = merged_pythonpath + os.pathsep + old_env["PYTHONPATH"]

        os.environ["HOME"] = str(self.home_dir)
        os.environ["PATH"] = str(self.bin_dir) + os.pathsep + (old_env["PATH"] or "")
        os.environ["PYTHONPATH"] = merged_pythonpath
        os.environ["SMARTASSIST_DATA_DIR"] = str(self.data_dir)
        os.chdir(self.project_root)
        try:
            yield self
        finally:
            os.chdir(old_cwd)
            for key, value in old_env.items():
                if value is None:
                    os.environ.pop(key, None)
                else:
                    os.environ[key] = value

    def install_fake_commands(self, command_names: list[str]) -> list[str]:
        created = []
        self.bin_dir.mkdir(parents=True, exist_ok=True)
        for name in command_names:
            path = self.bin_dir / name
            path.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
            path.chmod(path.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
            created.append(name)
        return created

    def write_hook_settings(self) -> Path:
        from smartassist.cli import HOOK_DEFS

        claude_dir = self.home_dir / ".claude"
        claude_dir.mkdir(parents=True, exist_ok=True)
        hooks: dict[str, list[dict[str, object]]] = {}
        for hook_def in HOOK_DEFS:
            entry: dict[str, object] = {
                "hooks": [{"type": "command", "command": hook_def["command"]}],
            }
            if hook_def["matcher"]:
                entry["matcher"] = hook_def["matcher"]
            hooks.setdefault(hook_def["event"], []).append(entry)

        settings_path = claude_dir / "settings.json"
        atomic_write_json(settings_path, {"hooks": hooks})
        return settings_path

    def write_project_mcp_registration(self) -> Path:
        env = {
            "HOME": str(self.home_dir),
            "PATH": str(self.bin_dir) + os.pathsep + os.environ.get("PATH", ""),
            "PYTHONPATH": str(self.repo_root),
        }
        return ensure_project_mcp_server(
            project_root=self.project_root,
            command=sys.executable,
            args=["-m", "smartassist.mcp_server"],
            env=env,
        )


def create_scenario_sandbox(name: str, workspace_root: Path) -> ScenarioSandbox:
    repo_root = Path(__file__).resolve().parents[2]
    workspace_root = workspace_root.resolve()
    workspace_root.mkdir(parents=True, exist_ok=True)
    home_dir = workspace_root / "home"
    project_root = workspace_root / "project"
    data_dir = project_root / ".claude" / "smartassist"
    storage_path = data_dir / "data"
    lancedb_path = data_dir / "lancedb"
    bin_dir = workspace_root / "bin"

    home_dir.mkdir(parents=True, exist_ok=True)
    project_root.mkdir(parents=True, exist_ok=True)
    bin_dir.mkdir(parents=True, exist_ok=True)
    (home_dir / ".claude").mkdir(parents=True, exist_ok=True)
    storage_path.mkdir(parents=True, exist_ok=True)
    lancedb_path.mkdir(parents=True, exist_ok=True)
    initialize_store(storage_path)
    (project_root / ".gitignore").write_text(".claude/smartassist/\n", encoding="utf-8")

    return ScenarioSandbox(
        name=name,
        repo_root=repo_root.resolve(),
        workspace_root=workspace_root,
        home_dir=home_dir.resolve(),
        project_root=project_root.resolve(),
        data_dir=data_dir.resolve(),
        storage_path=storage_path.resolve(),
        lancedb_path=lancedb_path.resolve(),
        bin_dir=bin_dir.resolve(),
    )
