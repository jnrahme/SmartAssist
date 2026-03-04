#!/usr/bin/env python3
"""
Commit Hook - Captures learnings during git commit process.
Called by Claude Code PreToolUse on Bash commands.
Detects when a commit just happened and extracts lessons from the diff.
"""

import sys
import json
import subprocess
import time
import re

from smartassist.config import get_storage_path, get_project_root


def get_last_commit_info():
    """Get info about the most recent commit."""
    try:
        project_root = get_project_root()
        msg = subprocess.check_output(
            ["git", "log", "-1", "--pretty=format:%s"],
            cwd=project_root, text=True
        ).strip()

        sha = subprocess.check_output(
            ["git", "log", "-1", "--pretty=format:%H"],
            cwd=project_root, text=True
        ).strip()

        timestamp = subprocess.check_output(
            ["git", "log", "-1", "--pretty=format:%ct"],
            cwd=project_root, text=True
        ).strip()

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
    capture_log.write_text(json.dumps(data, indent=2))


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
        # Check for console.log additions
        console_logs = re.findall(r"^\+.*console\.(log|debug|warn)", diff_content, re.MULTILINE)
        if console_logs:
            lessons.append({
                "signal": "correction",
                "category": FeedbackCategory.CODE_EDIT.value,
                "intensity": 4,
                "query": "Commit code changes",
                "response": f"Committed {len(console_logs)} console.log statement(s)",
                "correction": "Remove console.log statements before committing. Use LOGGER for debug output.",
                "context": "Project rule: never commit console.log debug statements.",
            })

        # Check for hardcoded colors
        hardcoded_colors = re.findall(r"^\+.*['\"]#[0-9a-fA-F]{3,8}['\"]", diff_content, re.MULTILINE)
        if hardcoded_colors:
            lessons.append({
                "signal": "correction",
                "category": FeedbackCategory.CODE_EDIT.value,
                "intensity": 4,
                "query": "Style a component",
                "response": f"Used {len(hardcoded_colors)} hardcoded color value(s) in committed code",
                "correction": "Use semantic colors from theme instead of hardcoded hex values.",
                "context": "Project bans hardcoded colors. Always use design tokens.",
            })

        # Check for snapshot tests
        if "toMatchSnapshot" in diff_content or "toMatchInlineSnapshot" in diff_content:
            lessons.append({
                "signal": "correction",
                "category": FeedbackCategory.TESTING.value,
                "intensity": 5,
                "query": "Write component tests",
                "response": "Added snapshot test (toMatchSnapshot)",
                "correction": "Use behavior testing: toBeVisible(), getByText(), fireEvent. No snapshots.",
                "context": "Project bans snapshot tests completely.",
            })

        # Check for direct analytics() calls (outside utility)
        analytics_calls = re.findall(r"^\+.*analytics\(\)", diff_content, re.MULTILINE)
        analytics_in_util = any("firebaseAnalytics" in f for f in changed_files)
        if analytics_calls and not analytics_in_util:
            lessons.append({
                "signal": "correction",
                "category": FeedbackCategory.SECURITY.value,
                "intensity": 4,
                "query": "Add analytics tracking",
                "response": "Called analytics() directly from component",
                "correction": "Use centralized analytics functions from the analytics utility module.",
                "context": "Firebase analytics must go through centralized utility.",
            })

    # ── Lesson: record what areas of code were touched ───────────────
    areas = set()
    for f in changed_files:
        if "shared/components" in f:
            areas.add("shared components")
        elif "utils/" in f:
            areas.add("utilities")
        elif "slices/" in f:
            areas.add("redux slices")

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


def capture_commit_lessons():
    """Main entry: detect recent commit and capture lessons."""
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

    # Write lessons to feedback log
    storage_path = get_storage_path()
    feedback_log = storage_path / "feedback_log.jsonl"
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
        with open(feedback_log, "a") as f:
            f.write(json.dumps(entry) + "\n")

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

    print(f"\nCaptured {len(lessons)} lesson(s) from commit {commit_info['sha'][:8]}")
    for lesson in lessons:
        icon = "+" if lesson["signal"] == "thumbs_up" else "!"
        print(f"   {icon} [{lesson['category']}] {lesson['response'][:70]}")


def main():
    capture_commit_lessons()


if __name__ == "__main__":
    main()
    sys.exit(0)
