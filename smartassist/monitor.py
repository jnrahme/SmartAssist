#!/usr/bin/env python3
import json
import os
import sys
from pathlib import Path

HOOKS_FILE = Path.home() / ".claude" / "settings.json"
EXPECTED_HOOKS = {
    "UserPromptSubmit": "smartassist-prompt-inject",
    "SessionStart": "smartassist-session-start",
    "PreToolUse": "smartassist-commit-hook",
    "PostToolUse": "smartassist-show-lessons",
    "SessionEnd": "smartassist-session-end",
}

CYAN = "\033[1;36m"
GREEN = "\033[32m"
RED = "\033[31m"
DIM = "\033[90m"
RESET = "\033[0m"
BOLD = "\033[1m"


def _check_hooks() -> dict[str, bool]:
    results = {}
    try:
        settings = json.loads(HOOKS_FILE.read_text())
        hooks = settings.get("hooks", {})
    except (FileNotFoundError, json.JSONDecodeError):
        return {name: False for name in EXPECTED_HOOKS}

    for event, cmd_fragment in EXPECTED_HOOKS.items():
        found = False
        for entry in hooks.get(event, []):
            for hook in entry.get("hooks", []):
                if cmd_fragment in hook.get("command", ""):
                    found = True
                    break
        results[event] = found
    return results


def _check_mcp() -> bool:
    claude_json = Path.home() / ".claude.json"
    try:
        data = json.loads(claude_json.read_text())
        if "smartassist" in data.get("mcpServers", {}):
            return True
        for project in data.get("projects", {}).values():
            if "smartassist" in project.get("mcpServers", {}):
                return True
    except (FileNotFoundError, json.JSONDecodeError):
        pass
    return False


def main() -> int:
    log_file = None
    if len(sys.argv) > 1:
        log_file = sys.argv[1]

    ok = f"{GREEN}✓{RESET}"
    fail = f"{RED}✗{RESET}"

    print(f"\n{CYAN} SmartAssist Monitor{RESET}")
    print(f"{DIM}{'━' * 28}{RESET}")

    mcp_ok = _check_mcp()
    print(f" MCP: {ok if mcp_ok else fail} {'connected' if mcp_ok else 'missing'}")
    print(f"{DIM}{'━' * 28}{RESET}")

    hook_results = _check_hooks()
    print(f" {BOLD}Hooks:{RESET}")
    for event, active in hook_results.items():
        print(f"  {ok if active else fail} {event}")
    print(f"{DIM}{'━' * 28}{RESET}")

    if log_file:
        print(f" {BOLD}Live Log:{RESET}\n")
        sys.stdout.flush()
        os.execvp("tail", ["tail", "-f", log_file])

    return 0


if __name__ == "__main__":
    sys.exit(main())
