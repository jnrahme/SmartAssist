#!/usr/bin/env python3
"""
Session Start Hook - Injects lessons learned at session start.
Called by Claude Code when starting a new session.

Optimized: loads only ThompsonSamplingModel + reads JSONL directly.
Does NOT load embedding model or LanceDB (saves ~4 seconds).
"""

import sys
import json

from smartassist.config import get_storage_path
from smartassist.thompson_sampling import ThompsonSamplingModel


def _get_recent_negative_feedback(storage_path, weak_categories, max_lessons=5):
    """Read recent negative feedback from JSONL for weak categories."""
    feedback_log = storage_path / "feedback_log.jsonl"
    if not feedback_log.exists():
        return []

    negative_signals = {"thumbs_down", "angry", "correction"}
    weak_set = set(weak_categories)

    events = []
    with open(feedback_log, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                event = json.loads(line)
                cat = event.get("category", "")
                sig = event.get("signal", "")
                if cat in weak_set and sig in negative_signals:
                    events.append(event)
            except json.JSONDecodeError:
                continue

    events.sort(key=lambda e: e.get("timestamp", 0), reverse=True)
    return events[:max_lessons]


def format_lessons_for_session():
    """Generate lessons learned context for session start"""
    try:
        storage_path = get_storage_path()
        thompson = ThompsonSamplingModel(str(storage_path))
        weak_categories = thompson.get_weak_categories(threshold=0.70)

        if not weak_categories:
            return ""

        lessons = _get_recent_negative_feedback(storage_path, weak_categories, max_lessons=5)

        if not lessons:
            return ""

        output = []
        output.append("\n" + "=" * 60)
        output.append("LESSONS FROM PAST SESSIONS")
        output.append("=" * 60)

        if weak_categories:
            output.append("\nAreas needing attention (success rate <70%):")
            for cat in weak_categories:
                reliability = thompson.get_reliability(cat)
                output.append(f"  - {cat}: {reliability:.1%}")

        if lessons:
            output.append(f"\nRecent mistakes to avoid ({len(lessons)} lessons):\n")
            for i, lesson in enumerate(lessons, 1):
                cat = lesson.get("category", "unknown").upper()
                output.append(f"[{i}] {cat}")
                response = lesson.get("response", "")
                if response:
                    output.append(f"    Wrong: {response[:100]}...")
                correction = lesson.get("correction", "")
                if correction:
                    output.append(f"    Correct: {correction}")
                output.append("")

        output.append("=" * 60)
        output.append("Apply these lessons in this session!")
        output.append("=" * 60 + "\n")

        return "\n".join(output)

    except Exception:
        return ""


def main():
    lessons = format_lessons_for_session()
    if lessons:
        print(lessons)


if __name__ == "__main__":
    main()
    sys.exit(0)
