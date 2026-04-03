"""Install-readiness audit for SmartAssist."""

from __future__ import annotations

import json
import os
from pathlib import Path

from smartassist.claude_config import get_mcp_status
from smartassist.config import get_data_dir
from smartassist.runtime import resolve_cli_invocation

EXPECTED_HOOKS = {
    "UserPromptSubmit": {"command": "smartassist-prompt-inject", "matcher": None},
    "SessionStart": {"command": "smartassist-session-start", "matcher": "startup"},
    "PreToolUse": {"command": "smartassist-commit-hook", "matcher": "Bash|Edit|Write"},
    "PostToolUse": {"command": "smartassist-show-lessons", "matcher": "mcp__smartassist__rag_search"},
    "SessionEnd": {"command": "smartassist-session-end", "matcher": "other"},
}


def _ok(name: str, detail: str) -> dict:
    return {"name": name, "status": "ok", "detail": detail}


def _warn(name: str, detail: str) -> dict:
    return {"name": name, "status": "warn", "detail": detail}


def _fail(name: str, detail: str) -> dict:
    return {"name": name, "status": "fail", "detail": detail}


def _load_settings(path: Path) -> dict:
    try:
        return json.loads(path.read_text())
    except (FileNotFoundError, json.JSONDecodeError):
        return {}


def _collect_hook_status(settings_path: Path) -> dict:
    settings = _load_settings(settings_path)
    hooks = settings.get("hooks", {})

    missing = []
    for event, expected in EXPECTED_HOOKS.items():
        groups = hooks.get(event, [])
        found = False
        for group in groups:
            matcher = group.get("matcher")
            has_command = any(
                inner.get("command") == expected["command"]
                for inner in group.get("hooks", [])
            )
            if not has_command:
                continue
            if expected["matcher"] is not None and matcher != expected["matcher"]:
                continue
            found = True
            break
        if not found:
            if expected["matcher"] is None:
                missing.append(f"{event}:{expected['command']}")
            else:
                missing.append(f"{event}:{expected['command']}@{expected['matcher']}")

    if missing:
        return _fail(
            "Hooks",
            "Missing hook registrations: " + ", ".join(missing),
        )

    return _ok("Hooks", f"All {len(EXPECTED_HOOKS)} SmartAssist hooks are registered")


def _collect_data_status() -> dict:
    try:
        data_dir = get_data_dir()
    except RuntimeError as exc:
        return _fail("Project data", str(exc))

    expected = [
        data_dir / "data",
        data_dir / "lancedb",
        data_dir / "data" / "feedback_log.jsonl",
        data_dir / "data" / "reliability_scores.json",
        data_dir / "data" / "vectorization_log.json",
    ]
    missing = [str(path.relative_to(data_dir)) for path in expected if not path.exists()]
    if missing:
        return _fail(
            "Project data",
            f"{data_dir} is missing required paths: {', '.join(missing)}",
        )

    return _ok("Project data", str(data_dir))


def collect_doctor_report() -> dict:
    """Return a SmartAssist install-readiness report for the current environment."""
    checks: list[dict] = []

    try:
        invocation = resolve_cli_invocation(
            prefer_source_checkout=True,
            start_path=Path.cwd(),
        )
        checks.append(
            _ok("CLI", f"{invocation.mode}: {invocation.label}")
        )
    except RuntimeError as exc:
        checks.append(_fail("CLI", str(exc)))

    claude_dir = Path.home() / ".claude"
    if claude_dir.exists():
        checks.append(_ok("Claude config dir", str(claude_dir)))
    else:
        checks.append(
            _fail("Claude config dir", f"{claude_dir} is missing; install Claude Code first")
        )

    mcp_status = get_mcp_status()
    if mcp_status["registered"]:
        detail = f"{mcp_status['server_name']} via {mcp_status['source_label']}"
        if mcp_status["duplicate_sources"]:
            detail += " (duplicate registrations detected)"
            checks.append(_warn("MCP registration", detail))
        else:
            checks.append(_ok("MCP registration", detail))
    else:
        checks.append(
            _fail(
                "MCP registration",
                "SmartAssist is not registered for this project in .mcp.json, "
                "~/.claude.json, or legacy ~/.claude/mcp.json",
            )
        )

    settings_path = claude_dir / "settings.json"
    if settings_path.exists():
        checks.append(_collect_hook_status(settings_path))
    else:
        checks.append(
            _fail("Hooks", f"{settings_path} is missing; run 'smartassist setup'")
        )

    checks.append(_collect_data_status())

    failing = [check for check in checks if check["status"] == "fail"]
    warning = [check for check in checks if check["status"] == "warn"]
    overall_status = "ready"
    if failing:
        overall_status = "fail"
    elif warning:
        overall_status = "warn"

    return {
        "overall_status": overall_status,
        "cwd": str(Path.cwd()),
        "home": os.environ.get("HOME", str(Path.home())),
        "checks": checks,
    }


def report_to_text(report: dict) -> str:
    """Render the doctor report as human-readable text."""
    lines = ["SMARTASSIST DOCTOR", ""]
    markers = {"ok": "[OK]", "warn": "[!!]", "fail": "[FAIL]"}

    for check in report["checks"]:
        lines.append(f"{markers[check['status']]} {check['name']}: {check['detail']}")

    lines.append("")
    if report["overall_status"] == "ready":
        lines.append("Status: ready")
    elif report["overall_status"] == "warn":
        lines.append("Status: ready with warnings")
    else:
        lines.append("Status: needs attention")

    return "\n".join(lines) + "\n"
