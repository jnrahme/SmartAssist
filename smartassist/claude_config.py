"""Shared Claude Code MCP registration helpers."""

import json
from pathlib import Path

from smartassist.config import atomic_write_json

SERVER_NAMES = ("smartassist", "rag-knowledge")


def _load_json(path: Path) -> dict | None:
    try:
        return json.loads(path.read_text())
    except (FileNotFoundError, json.JSONDecodeError):
        return None


def _describe_entry(entry: dict) -> str:
    args = entry.get("args", [])
    if args:
        return args[-1]
    command = entry.get("command", "")
    return command or "?"


def _source_label(
    source: str,
    project_name: str | None = None,
    path: Path | None = None,
) -> str:
    if source == "project_local":
        return f"{path or '.mcp.json'} (project)"
    if source == "user":
        return "~/.claude.json (user)"
    if source == "project":
        suffix = f": {project_name}" if project_name else ""
        return f"~/.claude.json (project{suffix})"
    if source == "legacy":
        return "~/.claude/mcp.json (legacy)"
    return source


def _collect_entries(
    servers: dict | None,
    path: Path,
    source: str,
    project_name: str | None = None,
    applies_to_current_context: bool = True,
) -> list[dict]:
    if not isinstance(servers, dict):
        return []

    entries = []
    for server_name in SERVER_NAMES:
        entry = servers.get(server_name)
        if not isinstance(entry, dict):
            continue
        entries.append(
            {
                "server_name": server_name,
                "source": source,
                "project_name": project_name,
                "path": path,
                "entry": entry,
                "applies_to_current_context": applies_to_current_context,
                "entry_label": _describe_entry(entry),
                "source_label": _source_label(source, project_name, path),
            }
        )
    return entries


def _find_project_mcp_path(start_path: Path | None = None) -> Path | None:
    current = (start_path or Path.cwd()).resolve()
    for parent in [current, *current.parents]:
        candidate = parent / ".mcp.json"
        if candidate.is_file():
            return candidate
        if parent == parent.parent:
            break
    return None


def _project_config_matches_path(project_name: str, start_path: Path) -> bool:
    try:
        project_path = Path(project_name).expanduser().resolve()
        current = start_path.resolve()
        return current == project_path or project_path in current.parents
    except (OSError, RuntimeError, ValueError):
        return False


def get_registered_mcp_entries(start_path: Path | None = None) -> list[dict]:
    """Return all SmartAssist MCP registrations across modern and legacy configs."""
    entries: list[dict] = []
    current_path = (start_path or Path.cwd()).resolve()

    local_project_path = _find_project_mcp_path(current_path)
    if local_project_path is not None:
        project_config = _load_json(local_project_path)
        if isinstance(project_config, dict):
            entries.extend(
                _collect_entries(
                    project_config.get("mcpServers"),
                    local_project_path,
                    "project_local",
                    project_name=str(local_project_path.parent),
                )
            )

    claude_json_path = Path.home() / ".claude.json"
    claude_json = _load_json(claude_json_path)
    if isinstance(claude_json, dict):
        entries.extend(
            _collect_entries(
                claude_json.get("mcpServers"),
                claude_json_path,
                "user",
            )
        )
        projects = claude_json.get("projects", {})
        if isinstance(projects, dict):
            for project_name, project_config in projects.items():
                if not isinstance(project_config, dict):
                    continue
                applies_to_current_context = _project_config_matches_path(
                    project_name,
                    current_path,
                )
                entries.extend(
                    _collect_entries(
                        project_config.get("mcpServers"),
                        claude_json_path,
                        "project",
                        project_name,
                        applies_to_current_context=applies_to_current_context,
                    )
                )

    legacy_path = Path.home() / ".claude" / "mcp.json"
    legacy_config = _load_json(legacy_path)
    if isinstance(legacy_config, dict):
        entries.extend(
            _collect_entries(
                legacy_config.get("mcpServers"),
                legacy_path,
                "legacy",
            )
        )

    return entries


def get_mcp_status(start_path: Path | None = None) -> dict:
    """Return a normalized SmartAssist MCP registration summary."""
    entries = get_registered_mcp_entries(start_path=start_path)
    active_entries = [
        entry for entry in entries if entry.get("applies_to_current_context", True)
    ]

    if not active_entries:
        return {
            "registered": False,
            "entries": [],
            "all_entries": entries,
            "preferred": None,
            "duplicate_sources": [],
        }

    priority = {"project_local": 0, "project": 1, "user": 2, "legacy": 3}
    preferred = sorted(
        active_entries,
        key=lambda item: (
            priority.get(item["source"], 99),
            item["server_name"] != "smartassist",
        ),
    )[0]

    duplicates = [
        entry["source_label"]
        for entry in active_entries
        if entry is not preferred
    ]

    return {
        "registered": True,
        "entries": active_entries,
        "all_entries": entries,
        "preferred": preferred,
        "server_name": preferred["server_name"],
        "entry": preferred["entry_label"],
        "source": preferred["source"],
        "source_label": preferred["source_label"],
        "duplicate_sources": duplicates,
    }


def _remove_mcp_servers(path: Path, config: dict | None = None) -> bool:
    config = config if config is not None else _load_json(path)
    if not isinstance(config, dict):
        return False

    servers = config.get("mcpServers")
    if not isinstance(servers, dict):
        return False

    removed = False
    for server_name in SERVER_NAMES:
        if server_name in servers:
            del servers[server_name]
            removed = True

    if removed:
        config["mcpServers"] = servers
        atomic_write_json(path, config)

    return removed


def _remove_mcp_servers_in_place(config: dict) -> bool:
    if not isinstance(config, dict):
        return False

    servers = config.get("mcpServers")
    if not isinstance(servers, dict):
        return False

    removed = False
    for server_name in SERVER_NAMES:
        if server_name in servers:
            del servers[server_name]
            removed = True

    if removed:
        config["mcpServers"] = servers

    return removed


def ensure_project_mcp_server(
    *,
    project_root: Path,
    command: str,
    args: list[str],
    env: dict[str, str] | None = None,
) -> Path:
    """Write or update the project-local .mcp.json SmartAssist registration."""
    mcp_path = project_root.resolve() / ".mcp.json"
    config = _load_json(mcp_path) or {}
    servers = config.setdefault("mcpServers", {})

    for server_name in SERVER_NAMES:
        servers.pop(server_name, None)

    servers["smartassist"] = {
        "type": "stdio",
        "command": command,
        "args": args,
        "env": env or {},
    }
    config["mcpServers"] = servers
    atomic_write_json(mcp_path, config)
    return mcp_path


def remove_project_mcp_servers(project_root: Path | None = None) -> bool:
    """Remove SmartAssist MCP registrations from the current project's .mcp.json."""
    path = (project_root or Path.cwd()).resolve() / ".mcp.json"
    return _remove_mcp_servers(path)


def remove_project_state_mcp_servers(project_root: Path | None = None) -> bool:
    """Remove SmartAssist MCP registrations from ~/.claude.json project state."""
    claude_json_path = Path.home() / ".claude.json"
    config = _load_json(claude_json_path)
    if not isinstance(config, dict):
        return False

    projects = config.get("projects")
    if not isinstance(projects, dict):
        return False

    current_project = str((project_root or Path.cwd()).resolve())
    project_config = projects.get(current_project)
    if not isinstance(project_config, dict):
        return False

    removed = _remove_mcp_servers_in_place(project_config)
    if removed:
        projects[current_project] = project_config
        config["projects"] = projects
        atomic_write_json(claude_json_path, config)
    return removed


def remove_user_mcp_servers() -> bool:
    """Remove SmartAssist MCP registrations from ~/.claude.json user config."""
    claude_json_path = Path.home() / ".claude.json"
    return _remove_mcp_servers(claude_json_path)


def remove_legacy_mcp_servers() -> bool:
    """Remove SmartAssist MCP registrations from the legacy ~/.claude/mcp.json file."""
    legacy_path = Path.home() / ".claude" / "mcp.json"
    return _remove_mcp_servers(legacy_path)
