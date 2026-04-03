#!/usr/bin/env python3
"""
PreToolUse hook for SmartAssist.

Responsibilities:
1. Enforce hard gates before risky Bash/Edit/Write actions execute.
2. Preserve the existing commit-capture side effect for Bash usage.
"""

import sys
import json
import subprocess
import time
import re

from smartassist.config import get_storage_path, get_project_root
from smartassist.gates import build_pretool_hook_output, evaluate_pretool_gate
from smartassist.store import append_feedback_event, ensure_lesson


def get_last_commit_info():
    """Get info about the most recent commit (single git call)."""
    try:
        project_root = get_project_root()
        # Single git call instead of three (L19 perf fix)
        raw = subprocess.check_output(
            ["git", "log", "-1", "--pretty=format:%H%n%s%n%ct"],
            cwd=project_root, text=True,
        ).strip()
        sha, msg, timestamp = raw.split("\n", 2)
        return {"sha": sha, "message": msg, "timestamp": int(timestamp)}
    except Exception:
        return None


def get_commit_diff():
    """Get the diff of the last commit."""
    try:
        project_root = get_project_root()
        diff = subprocess.check_output(
            ["git", "diff", "HEAD~1", "HEAD", "--stat"],
            cwd=project_root, text=True, stderr=subprocess.DEVNULL
        ).strip()
        return diff
    except Exception:
        return ""


def get_changed_files():
    """Get list of files changed in last commit."""
    try:
        project_root = get_project_root()
        files = subprocess.check_output(
            ["git", "diff", "HEAD~1", "HEAD", "--name-only"],
            cwd=project_root, text=True, stderr=subprocess.DEVNULL
        ).strip().split("\n")
        return [f for f in files if f]
    except Exception:
        return []


def get_commit_diff_content():
    """Get actual diff content (limited) for pattern analysis."""
    try:
        project_root = get_project_root()
        diff = subprocess.check_output(
            ["git", "diff", "HEAD~1", "HEAD", "-U3", "--no-color"],
            cwd=project_root, text=True, stderr=subprocess.DEVNULL,
            timeout=5
        )
        # Limit to first 5000 chars to keep analysis fast
        return diff[:5000]
    except Exception:
        return ""


def was_recent_commit():
    """Check if a commit happened in the last 30 seconds."""
    commit_info = get_last_commit_info()
    if not commit_info:
        return False
    return (time.time() - commit_info["timestamp"]) < 30


def already_captured(sha):
    """Check if we already captured lessons for this commit."""
    storage_path = get_storage_path()
    capture_log = storage_path / "commit_captures.json"
    if not capture_log.exists():
        return False
    try:
        data = json.loads(capture_log.read_text())
        return sha in data.get("captured_shas", [])
    except Exception:
        return False


def mark_captured(sha):
    """Mark a commit SHA as captured."""
    storage_path = get_storage_path()
    capture_log = storage_path / "commit_captures.json"
    try:
        data = json.loads(capture_log.read_text()) if capture_log.exists() else {}
    except Exception:
        data = {}
    shas = data.get("captured_shas", [])
    shas.append(sha)
    # Keep only last 100
    data["captured_shas"] = shas[-100:]
    data["last_capture"] = time.time()
    from smartassist.config import atomic_write_json
    atomic_write_json(capture_log, data)


def extract_lessons_from_commit(commit_info, changed_files, diff_content):
    """Analyze commit and extract lessons as feedback entries."""
    from smartassist.feedback_system import FeedbackCategory

    lessons = []
    msg = commit_info["message"]
    branch = ""
    try:
        project_root = get_project_root()
        branch = subprocess.check_output(
            ["git", "rev-parse", "--abbrev-ref", "HEAD"],
            cwd=project_root, text=True
        ).strip()
    except Exception:
        pass

    # ── Detect patterns in changed files ─────────────────────────────
    test_files = [f for f in changed_files if "__tests__" in f or ".test." in f]
    src_files = [f for f in changed_files if f.startswith("src/") and "__tests__" not in f]
    style_files = [f for f in changed_files if "style" in f.lower()]

    # ── Lesson: commit message format ────────────────────────────────
    ticket_match = re.match(r"\[([A-Z]+-\d+)\]", msg)
    if ticket_match:
        lessons.append({
            "signal": "thumbs_up",
            "category": FeedbackCategory.GIT.value,
            "intensity": 3,
            "query": f"Commit on branch {branch}",
            "response": f"Used correct commit format: {msg[:80]}",
            "correction": None,
            "context": f"Branch: {branch}, Files: {len(changed_files)}",
        })
    elif msg and not msg.startswith("Merge"):
        lessons.append({
            "signal": "correction",
            "category": FeedbackCategory.GIT.value,
            "intensity": 3,
            "query": f"Commit on branch {branch}",
            "response": f"Commit message missing ticket: {msg[:80]}",
            "correction": "Use format: [TICKET-XXX] Description",
            "context": f"Branch: {branch}",
        })

    # ── Lesson: tests included with source changes ───────────────────
    if src_files and test_files:
        lessons.append({
            "signal": "thumbs_up",
            "category": FeedbackCategory.TESTING.value,
            "intensity": 4,
            "query": f"Commit changes to {len(src_files)} source files",
            "response": f"Included {len(test_files)} test file(s) with source changes",
            "correction": None,
            "context": f"Good practice: tests updated alongside source code. Files: {', '.join(src_files[:5])}",
        })
    elif src_files and not test_files and len(src_files) > 2:
        lessons.append({
            "signal": "correction",
            "category": FeedbackCategory.TESTING.value,
            "intensity": 2,
            "query": f"Commit changes to {len(src_files)} source files",
            "response": f"No test files included with {len(src_files)} source file changes",
            "correction": "Consider including test updates when modifying multiple source files.",
            "context": f"Files: {', '.join(src_files[:5])}",
        })

    # ── Lesson: detect anti-patterns in diff ─────────────────────────
    if diff_content:
        # Check for debug statement additions (generic across JS/TS/Python)
        debug_stmts = re.findall(
            r"^\+.*(console\.(log|debug|warn)|print\(|debugger\b)",
            diff_content, re.MULTILINE,
        )
        if debug_stmts:
            lessons.append({
                "signal": "correction",
                "category": FeedbackCategory.CODE_EDIT.value,
                "intensity": 3,
                "query": "Commit code changes",
                "response": f"Committed {len(debug_stmts)} debug statement(s)",
                "correction": "Remove debug statements (console.log, print, debugger) before committing.",
                "context": "Clean commits should not contain debug output.",
            })

    # ── Lesson: record what areas of code were touched ───────────────
    areas = set()
    for f in changed_files:
        if "components" in f:
            areas.add("components")
        elif "utils/" in f or "helpers/" in f or "lib/" in f:
            areas.add("utilities")

    if areas:
        lessons.append({
            "signal": "thumbs_up",
            "category": FeedbackCategory.ARCHITECTURE.value,
            "intensity": 2,
            "query": f"Work on {', '.join(areas)}",
            "response": f"Committed changes to: {', '.join(areas)} ({len(changed_files)} files)",
            "correction": None,
            "context": f"Commit: {msg[:60]}. Branch: {branch}.",
        })

    return lessons


def capture_commit_lessons(*, verbose: bool = True):
    """Detect a recent commit and promote commit-based lessons.

    When running inside Claude's PreToolUse lifecycle we keep this silent unless
    invoked manually, because hook stdout must stay valid JSON when gates fire.
    """
    if not was_recent_commit():
        return

    commit_info = get_last_commit_info()
    if not commit_info or already_captured(commit_info["sha"]):
        return

    changed_files = get_changed_files()
    if not changed_files:
        return

    diff_content = get_commit_diff_content()
    lessons = extract_lessons_from_commit(commit_info, changed_files, diff_content)

    if not lessons:
        mark_captured(commit_info["sha"])
        return

    storage_path = get_storage_path()
    promoted = 0
    for lesson in lessons:
        entry = {
            "signal": lesson["signal"],
            "intensity": lesson["intensity"],
            "category": lesson["category"],
            "context": lesson["context"],
            "query": lesson["query"],
            "response": lesson["response"],
            "correction": lesson["correction"],
            "timestamp": time.time(),
            "session_id": f"commit_{commit_info['sha'][:8]}",
        }
        append_feedback_event(storage_path, entry)

        correction = (lesson.get("correction") or "").strip()
        if lesson["signal"] == "correction" and correction:
            _lesson_id, _error, created = ensure_lesson(
                storage_path,
                correction,
                lesson["category"],
                origin="commit_hook",
            )
            if created:
                promoted += 1

    # Update Thompson Sampling
    from smartassist.thompson_sampling import ThompsonSamplingModel
    thompson = ThompsonSamplingModel(str(storage_path))
    for lesson in lessons:
        if lesson["signal"] in ("thumbs_up", "happy"):
            thompson.record_success(lesson["category"], lesson["intensity"])
        elif lesson["signal"] in ("thumbs_down", "angry", "sad"):
            thompson.record_failure(lesson["category"], lesson["intensity"])
        elif lesson["signal"] == "correction":
            thompson.record_failure(lesson["category"], lesson["intensity"] // 2)

    mark_captured(commit_info["sha"])

    # Vectorize
    try:
        subprocess.run(
            [sys.executable, "-m", "smartassist.hooks.vectorize_learnings"],
            capture_output=True, timeout=15
        )
    except Exception:
        pass

    if verbose:
        print(f"\nCaptured {len(lessons)} lesson(s) from commit {commit_info['sha'][:8]}")
        if promoted:
            print(f"Promoted {promoted} correction(s) into the active lesson corpus")
        for lesson in lessons:
            icon = "+" if lesson["signal"] == "thumbs_up" else "!"
            print(f"   {icon} [{lesson['category']}] {lesson['response'][:70]}")


def _load_hook_input():
    """Read Claude hook JSON from stdin when available."""
    try:
        if sys.stdin.isatty():
            return None
    except Exception:
        return None

    try:
        payload = json.load(sys.stdin)
    except (json.JSONDecodeError, EOFError):
        return None

    return payload if isinstance(payload, dict) else None


def handle_pre_tool_use(hook_input: dict) -> None:
    """Evaluate gates and then run any silent side effects."""
    tool_name = str(hook_input.get("tool_name") or "").strip()
    tool_input = hook_input.get("tool_input")
    decision = evaluate_pretool_gate(tool_name, tool_input)

    if decision is not None:
        print(json.dumps(build_pretool_hook_output(decision)))
        return

    if tool_name == "Bash":
        capture_commit_lessons(verbose=False)


def main():
    hook_input = _load_hook_input()
    if hook_input is not None:
        handle_pre_tool_use(hook_input)
        return

    capture_commit_lessons(verbose=True)


if __name__ == "__main__":
    main()
    sys.exit(0)
