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


def _source_label(source: str, project_name: str | None = None) -> str:
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
                "entry_label": _describe_entry(entry),
                "source_label": _source_label(source, project_name),
            }
        )
    return entries


def get_registered_mcp_entries() -> list[dict]:
    """Return all SmartAssist MCP registrations across modern and legacy configs."""
    entries: list[dict] = []

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
                entries.extend(
                    _collect_entries(
                        project_config.get("mcpServers"),
                        claude_json_path,
                        "project",
                        project_name,
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


def get_mcp_status() -> dict:
    """Return a normalized SmartAssist MCP registration summary."""
    entries = get_registered_mcp_entries()
    if not entries:
        return {
            "registered": False,
            "entries": [],
            "preferred": None,
            "duplicate_sources": [],
        }

    priority = {"user": 0, "project": 1, "legacy": 2}
    preferred = sorted(
        entries,
        key=lambda item: (
            priority.get(item["source"], 99),
            item["server_name"] != "smartassist",
        ),
    )[0]

    duplicates = [
        entry["source_label"]
        for entry in entries
        if entry is not preferred
    ]

    return {
        "registered": True,
        "entries": entries,
        "preferred": preferred,
        "server_name": preferred["server_name"],
        "entry": preferred["entry_label"],
        "source": preferred["source"],
        "source_label": preferred["source_label"],
        "duplicate_sources": duplicates,
    }


def remove_legacy_mcp_servers() -> bool:
    """Remove SmartAssist MCP registrations from the legacy ~/.claude/mcp.json file."""
    legacy_path = Path.home() / ".claude" / "mcp.json"
    config = _load_json(legacy_path)
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
        atomic_write_json(legacy_path, config)

    return removed
