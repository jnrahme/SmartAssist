"""PreToolUse gate engine for SmartAssist.

This module evaluates risky tool actions before execution and returns
deterministic allow/ask/deny decisions for the hook wrapper.
"""

from __future__ import annotations

import json
import re
import shlex
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal

from smartassist.config import get_project_root, get_storage_path, locked_update_json

GateAction = Literal["deny", "ask", "warn"]
GateMatcher = Literal[
    "force_push",
    "protected_branch_push",
    "lockfile_reset",
    "sensitive_env_path",
    "sensitive_env_command",
    "regex",
]
GateTarget = Literal["command", "path"]

PROTECTED_BRANCHES = ("main", "master", "develop")
LOCKFILE_NAMES = {
    "package-lock.json",
    "yarn.lock",
    "pnpm-lock.yaml",
    "bun.lockb",
    "npm-shrinkwrap.json",
    "Podfile.lock",
    "Cargo.lock",
    "composer.lock",
    "Gemfile.lock",
}
ENV_TEMPLATE_NAMES = {
    ".env.example",
    ".env.sample",
    ".env.template",
    ".env.dist",
}
PUSH_OPTIONS_WITH_VALUES = {
    "-u",
    "--set-upstream",
    "--repo",
    "--receive-pack",
    "--exec",
    "--upload-pack",
    "-o",
    "--push-option",
}
SEGMENT_SPLIT_RE = re.compile(r"(?:&&|\|\||;|\n)")
ENV_COMMAND_PATH_RE = re.compile(
    r"""(?<![A-Za-z0-9_./-])((?:\./|/)?(?:[^ '"\t\n]+/)?\.env(?:\.[A-Za-z0-9_.-]+)?)(?![A-Za-z0-9_.-])""",
    re.IGNORECASE,
)


@dataclass(frozen=True)
class GateRule:
    """Single gate rule definition."""

    id: str
    action: GateAction
    message: str
    matcher: GateMatcher
    tool_names: tuple[str, ...]
    severity: str = "medium"
    target: GateTarget = "command"
    pattern: str | None = None

    @classmethod
    def from_mapping(cls, raw: dict[str, Any]) -> GateRule | None:
        """Parse a project-defined prevention rule.

        Invalid or incomplete rules are ignored so a bad local file does not
        disable the whole gate engine.
        """

        try:
            gate_id = str(raw["id"]).strip()
            action = str(raw["action"]).strip().lower()
            message = str(raw["message"]).strip()
            matcher = str(raw["matcher"]).strip()
        except (KeyError, TypeError, ValueError):
            return None

        if not gate_id or not message:
            return None
        if action not in {"deny", "ask", "warn"}:
            return None
        if matcher not in {
            "force_push",
            "protected_branch_push",
            "lockfile_reset",
            "sensitive_env_path",
            "sensitive_env_command",
            "regex",
        }:
            return None

        tool_names_raw = raw.get("tool_names", ["Bash"])
        if not isinstance(tool_names_raw, list) or not tool_names_raw:
            return None
        tool_names = tuple(str(name).strip() for name in tool_names_raw if str(name).strip())
        if not tool_names:
            return None

        target = str(raw.get("target", "command")).strip().lower()
        if target not in {"command", "path"}:
            return None

        pattern = raw.get("pattern")
        if matcher == "regex":
            if not isinstance(pattern, str) or not pattern.strip():
                return None
            try:
                re.compile(pattern)
            except re.error:
                return None
            pattern = pattern.strip()
        else:
            pattern = None

        severity = str(raw.get("severity", "medium")).strip().lower() or "medium"
        return cls(
            id=gate_id,
            action=action,  # type: ignore[arg-type]
            message=message,
            matcher=matcher,  # type: ignore[arg-type]
            tool_names=tool_names,
            severity=severity,
            target=target,  # type: ignore[arg-type]
            pattern=pattern,
        )


@dataclass(frozen=True)
class GateDecision:
    """Decision returned from the gate engine."""

    gate_id: str
    action: GateAction
    message: str
    severity: str


DEFAULT_RULES: tuple[GateRule, ...] = (
    GateRule(
        id="deny-force-push",
        action="deny",
        message="Force pushes are blocked. Use a safer branch-based workflow instead.",
        matcher="force_push",
        tool_names=("Bash",),
        severity="critical",
    ),
    GateRule(
        id="ask-protected-branch-push",
        action="ask",
        message="Pushing directly to a protected branch needs explicit confirmation.",
        matcher="protected_branch_push",
        tool_names=("Bash",),
        severity="high",
    ),
    GateRule(
        id="ask-lockfile-reset",
        action="ask",
        message="Lockfile resets can hide dependency drift. Confirm before restoring a lockfile from git.",
        matcher="lockfile_reset",
        tool_names=("Bash",),
        severity="high",
    ),
    GateRule(
        id="ask-sensitive-env-edit",
        action="ask",
        message="Editing a real .env file can expose or overwrite secrets. Confirm before proceeding.",
        matcher="sensitive_env_path",
        tool_names=("Edit", "Write"),
        target="path",
        severity="high",
    ),
    GateRule(
        id="ask-sensitive-env-shell-write",
        action="ask",
        message="Shell writes to a real .env file need confirmation because they may change secrets.",
        matcher="sensitive_env_command",
        tool_names=("Bash",),
        severity="high",
    ),
)


def get_prevention_rules_path(storage_path: Path | None = None) -> Path | None:
    """Return the structured prevention-rules file path."""
    storage = _resolve_storage_path(storage_path)
    if storage is None:
        return None
    return storage / "prevention_rules.json"


def get_gate_stats_path(storage_path: Path | None = None) -> Path | None:
    """Return the gate-stats file path."""
    storage = _resolve_storage_path(storage_path)
    if storage is None:
        return None
    return storage / "gate_stats.json"


def load_prevention_rules(storage_path: Path | None = None) -> list[GateRule]:
    """Load structured project-specific prevention rules."""
    rules_path = get_prevention_rules_path(storage_path)
    if rules_path is None or not rules_path.exists():
        return []

    try:
        raw = json.loads(rules_path.read_text())
    except (OSError, json.JSONDecodeError):
        return []

    if isinstance(raw, dict):
        raw_rules = raw.get("rules", [])
    elif isinstance(raw, list):
        raw_rules = raw
    else:
        return []

    if not isinstance(raw_rules, list):
        return []

    parsed: list[GateRule] = []
    for item in raw_rules:
        if not isinstance(item, dict):
            continue
        rule = GateRule.from_mapping(item)
        if rule is not None:
            parsed.append(rule)
    return parsed


def load_gate_stats(storage_path: Path | None = None) -> dict[str, Any]:
    """Load current gate statistics, returning a normalized shape."""
    stats_path = get_gate_stats_path(storage_path)
    if stats_path is None or not stats_path.exists():
        return _empty_gate_stats()
    try:
        raw = json.loads(stats_path.read_text())
    except (OSError, json.JSONDecodeError):
        return _empty_gate_stats()

    stats = _empty_gate_stats()
    if not isinstance(raw, dict):
        return stats

    for key in ("blocked", "asked", "warned", "passed"):
        value = raw.get(key, 0)
        if isinstance(value, int) and value >= 0:
            stats[key] = value

    by_gate = raw.get("by_gate", {})
    if isinstance(by_gate, dict):
        for gate_id, counts in by_gate.items():
            if not isinstance(counts, dict):
                continue
            stats["by_gate"][gate_id] = {
                "deny": int(counts.get("deny", 0) or 0),
                "ask": int(counts.get("ask", 0) or 0),
                "warn": int(counts.get("warn", 0) or 0),
            }

    return stats


def evaluate_pretool_gate(
    tool_name: str,
    tool_input: dict[str, Any] | None,
    *,
    storage_path: Path | None = None,
    project_root: Path | None = None,
) -> GateDecision | None:
    """Evaluate built-in and project-specific rules for one tool action."""
    normalized_tool = str(tool_name or "").strip()
    payload = tool_input if isinstance(tool_input, dict) else {}
    storage = _resolve_storage_path(storage_path)
    rules = [*DEFAULT_RULES, *load_prevention_rules(storage)]

    for rule in rules:
        if normalized_tool not in rule.tool_names:
            continue
        if _matches_rule(rule, payload, project_root=project_root):
            if storage is not None:
                record_gate_stat(storage, rule.id, rule.action)
                write_gate_event(storage, GateDecision(rule.id, rule.action, rule.message, rule.severity), normalized_tool, payload)
            return GateDecision(
                gate_id=rule.id,
                action=rule.action,
                message=rule.message,
                severity=rule.severity,
            )

    if storage is not None:
        record_gate_pass(storage)
    return None


def build_pretool_hook_output(decision: GateDecision) -> dict[str, Any]:
    """Render a gate decision into Claude Code's PreToolUse JSON contract."""
    hook_output: dict[str, Any] = {
        "hookSpecificOutput": {
            "hookEventName": "PreToolUse",
            "additionalContext": (
                f"SmartAssist gate [{decision.gate_id}] {decision.action.upper()}: "
                f"{decision.message}"
            ),
        }
    }

    if decision.action in {"deny", "ask"}:
        hook_output["hookSpecificOutput"]["permissionDecision"] = decision.action
        hook_output["hookSpecificOutput"]["permissionDecisionReason"] = (
            f"[SmartAssist:{decision.gate_id}] {decision.message}"
        )

    return hook_output


def write_gate_event(
    storage_path: Path,
    decision: GateDecision,
    tool_name: str,
    tool_input: dict[str, Any] | None,
) -> None:
    """Append a human-readable gate event to the live log for monitoring."""
    payload = tool_input if isinstance(tool_input, dict) else {}
    target = payload.get("command") or payload.get("file_path") or payload.get("path") or ""
    live_log = storage_path / "rag_live.log"
    lines = [
        "",
        f"{'=' * 60}",
        f"GATE {decision.action.upper()} [{decision.gate_id}]",
        f"Tool: {tool_name}",
        f"Target: {target}",
        f"Reason: {decision.message}",
    ]
    try:
        with open(live_log, "a", encoding="utf-8") as handle:
            handle.write("\n".join(lines) + "\n")
    except OSError:
        pass


def record_gate_stat(storage_path: Path, gate_id: str, action: GateAction) -> None:
    """Increment deny/ask/warn counters for a gate."""
    stats_path = storage_path / "gate_stats.json"

    def _update(current: dict[str, Any] | None) -> dict[str, Any]:
        stats = _empty_gate_stats()
        if isinstance(current, dict):
            stats.update({key: current.get(key, stats[key]) for key in ("blocked", "asked", "warned", "passed")})
            by_gate = current.get("by_gate", {})
            if isinstance(by_gate, dict):
                stats["by_gate"].update(by_gate)

        key = {"deny": "blocked", "ask": "asked", "warn": "warned"}[action]
        stats[key] = int(stats.get(key, 0) or 0) + 1
        gate_counts = stats["by_gate"].setdefault(gate_id, {"deny": 0, "ask": 0, "warn": 0})
        gate_counts[action] = int(gate_counts.get(action, 0) or 0) + 1
        return stats

    locked_update_json(stats_path, _update, default=_empty_gate_stats())


def record_gate_pass(storage_path: Path) -> None:
    """Increment the pass-through counter when no rule matched."""
    stats_path = storage_path / "gate_stats.json"

    def _update(current: dict[str, Any] | None) -> dict[str, Any]:
        stats = _empty_gate_stats()
        if isinstance(current, dict):
            stats.update({key: current.get(key, stats[key]) for key in ("blocked", "asked", "warned", "passed")})
            by_gate = current.get("by_gate", {})
            if isinstance(by_gate, dict):
                stats["by_gate"].update(by_gate)
        stats["passed"] = int(stats.get("passed", 0) or 0) + 1
        return stats

    locked_update_json(stats_path, _update, default=_empty_gate_stats())


def _empty_gate_stats() -> dict[str, Any]:
    return {
        "blocked": 0,
        "asked": 0,
        "warned": 0,
        "passed": 0,
        "by_gate": {},
    }


def _resolve_storage_path(storage_path: Path | None) -> Path | None:
    if storage_path is not None:
        return storage_path
    try:
        return get_storage_path()
    except RuntimeError:
        return None


def _matches_rule(rule: GateRule, tool_input: dict[str, Any], *, project_root: Path | None = None) -> bool:
    command = str(tool_input.get("command") or "")
    file_path = str(tool_input.get("file_path") or tool_input.get("path") or "")

    if rule.matcher == "force_push":
        return _is_force_push_command(command)
    if rule.matcher == "protected_branch_push":
        return _is_protected_branch_push(command, project_root=project_root)
    if rule.matcher == "lockfile_reset":
        return _is_lockfile_reset(command)
    if rule.matcher == "sensitive_env_path":
        return _is_sensitive_env_path(file_path)
    if rule.matcher == "sensitive_env_command":
        return _is_sensitive_env_command(command)
    if rule.matcher == "regex" and rule.pattern:
        target = command if rule.target == "command" else file_path
        try:
            return re.search(rule.pattern, target) is not None
        except re.error:
            return False
    return False


def _iter_command_tokens(command: str) -> list[list[str]]:
    segments = [segment.strip() for segment in SEGMENT_SPLIT_RE.split(command or "") if segment.strip()]
    out: list[list[str]] = []
    for segment in segments:
        try:
            tokens = shlex.split(segment, posix=True)
        except ValueError:
            tokens = segment.split()
        if tokens:
            out.append(tokens)
    return out


def _is_force_push_command(command: str) -> bool:
    for tokens in _iter_command_tokens(command):
        lowered = [token.lower() for token in tokens]
        if len(lowered) < 2 or lowered[0] != "git" or lowered[1] != "push":
            continue
        if any(
            token in {"-f", "--force", "--force-with-lease"} or token.startswith("--force-with-lease=")
            for token in lowered[2:]
        ):
            return True
    return False


def _is_protected_branch_push(command: str, *, project_root: Path | None = None) -> bool:
    for tokens in _iter_command_tokens(command):
        lowered = [token.lower() for token in tokens]
        if len(lowered) < 2 or lowered[0] != "git" or lowered[1] != "push":
            continue

        refs = _extract_git_push_refs(tokens[2:])
        current_branch: str | None = None

        def _load_current_branch() -> str | None:
            nonlocal current_branch
            if current_branch is None:
                current_branch = _get_current_branch(project_root=project_root)
            return current_branch

        if not refs:
            branch = _load_current_branch()
            if branch in PROTECTED_BRANCHES:
                return True
            continue

        for ref in refs:
            normalized = _normalize_branch_name(ref)
            if normalized in PROTECTED_BRANCHES:
                return True
            if normalized == "HEAD":
                branch = _load_current_branch()
                if branch in PROTECTED_BRANCHES:
                    return True

    return False


def _extract_git_push_refs(tokens: list[str]) -> list[str]:
    positional: list[str] = []
    i = 0
    while i < len(tokens):
        token = tokens[i]
        if token == "--":
            positional.extend(tokens[i + 1 :])
            break
        if token in PUSH_OPTIONS_WITH_VALUES:
            i += 2
            continue
        if token.startswith("--") and "=" in token:
            i += 1
            continue
        if token.startswith("-"):
            i += 1
            continue
        positional.append(token)
        i += 1

    if len(positional) >= 2:
        return positional[1:]
    if len(positional) == 1 and _looks_like_refspec(positional[0]):
        return positional
    return []


def _looks_like_refspec(token: str) -> bool:
    normalized = token.lstrip("+")
    return (
        ":" in normalized
        or normalized in PROTECTED_BRANCHES
        or normalized.startswith("refs/")
        or normalized == "HEAD"
    )


def _normalize_branch_name(token: str) -> str:
    branch = token.strip().lstrip("+")
    if ":" in branch:
        branch = branch.split(":", 1)[1]
    branch = branch.removeprefix("refs/heads/")
    branch = branch.removeprefix("refs/remotes/origin/")
    return branch


def _get_current_branch(*, project_root: Path | None = None) -> str | None:
    try:
        raw = subprocess.check_output(
            ["git", "rev-parse", "--abbrev-ref", "HEAD"],
            cwd=project_root or get_project_root(),
            text=True,
            stderr=subprocess.DEVNULL,
            timeout=2,
        ).strip()
    except Exception:
        return None
    return raw or None


def _is_lockfile_reset(command: str) -> bool:
    for tokens in _iter_command_tokens(command):
        lowered = [token.lower() for token in tokens]
        if len(lowered) < 2 or lowered[0] != "git":
            continue

        if lowered[1] == "checkout":
            paths = _extract_checkout_paths(tokens[2:])
        elif lowered[1] == "restore":
            paths = _extract_restore_paths(tokens[2:])
        else:
            continue

        if any(Path(path).name in LOCKFILE_NAMES for path in paths):
            return True

    return False


def _extract_checkout_paths(tokens: list[str]) -> list[str]:
    if "--" in tokens:
        idx = tokens.index("--")
        return [token for token in tokens[idx + 1 :] if token and not token.startswith("-")]

    positional = [token for token in tokens if token and not token.startswith("-")]
    if len(positional) >= 2:
        return positional[1:]
    if len(positional) == 1:
        return positional
    return []


def _extract_restore_paths(tokens: list[str]) -> list[str]:
    if "--" in tokens:
        idx = tokens.index("--")
        return [token for token in tokens[idx + 1 :] if token and not token.startswith("-")]

    paths: list[str] = []
    i = 0
    while i < len(tokens):
        token = tokens[i]
        if token in {"--source", "--staged", "--worktree"}:
            if token == "--source":
                i += 2
            else:
                i += 1
            continue
        if token.startswith("--source="):
            i += 1
            continue
        if token.startswith("-"):
            i += 1
            continue
        paths.append(token)
        i += 1
    return paths


def _is_sensitive_env_path(path_str: str) -> bool:
    if not path_str:
        return False
    name = Path(path_str).name.lower()
    if name in ENV_TEMPLATE_NAMES:
        return False
    return name == ".env" or name.startswith(".env.")


def _is_sensitive_env_command(command: str) -> bool:
    if not command:
        return False

    paths = [
        candidate
        for candidate in ENV_COMMAND_PATH_RE.findall(command)
        if _is_sensitive_env_path(candidate)
    ]
    if not paths:
        return False

    lowered = command.lower()
    mutates_env = any(
        (
            ">" in command,
            ">>" in command,
            " tee " in f" {lowered} ",
            re.search(r"\bsed\b.*\s-i(?:\s|$)", lowered) is not None,
            re.search(r"\bperl\b.*-pi(?:\s|$)", lowered) is not None,
            re.search(r"(?:^|[;&|]\s*)(?:cp|mv|install|touch|nano|vim|vi)\b", lowered) is not None,
        )
    )
    return bool(mutates_env)
