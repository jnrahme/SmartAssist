#!/usr/bin/env python3
"""
Seed RAG Database from CLAUDE.md and MEMORY.md
Extracts project conventions as baseline lessons for the RLHF system.
Run once (or after major CLAUDE.md updates) to populate the knowledge base.
"""

import sys
import json
import subprocess
from collections import Counter

from smartassist.config import get_storage_path
from smartassist.feedback_system import FeedbackCapture, FeedbackCategory, FeedbackSignal


def create_lessons():
    """Extract lessons from project conventions and return as feedback entries."""
    lessons = []

    # ── Testing lessons ──────────────────────────────────────────────
    lessons.append({
        "signal": "correction",
        "category": FeedbackCategory.TESTING.value,
        "intensity": 5,
        "query": "Run the project tests",
        "response": "Ran 'npm test'",
        "correction": "Use 'yarn test --no-coverage' for quick runs, or 'yarn test' for coverage. Never use npm.",
        "context": "Project uses yarn, not npm. See CLAUDE.md Development Commands.",
    })
    lessons.append({
        "signal": "correction",
        "category": FeedbackCategory.TESTING.value,
        "intensity": 4,
        "query": "Write a unit test for a component",
        "response": "Used snapshot testing: expect(tree.toJSON()).toMatchSnapshot()",
        "correction": "Use behavior testing with toBeVisible(), getByText(), fireEvent. Never use snapshot tests.",
        "context": "Project bans snapshot tests. Use toBeVisible() for visibility checks.",
    })
    lessons.append({
        "signal": "correction",
        "category": FeedbackCategory.TESTING.value,
        "intensity": 4,
        "query": "Mock a dependency in tests",
        "response": "Mocked internal hook: jest.mock('@/queries/savedSearches', ...)",
        "correction": "Mock at HTTP boundary: jest.mock('@/store', ...) with http: { get: jest.fn() }. Never mock internal code.",
        "context": "Project rule: never mock internal hooks or modules, only mock external boundaries.",
    })
    lessons.append({
        "signal": "correction",
        "category": FeedbackCategory.TESTING.value,
        "intensity": 3,
        "query": "Set up test mocks",
        "response": "Put mocks after imports",
        "correction": "Always put jest.mock() calls BEFORE import statements. Mocks must be hoisted.",
        "context": "Project testing guideline: mocks before imports.",
    })
    lessons.append({
        "signal": "thumbs_up",
        "category": FeedbackCategory.TESTING.value,
        "intensity": 5,
        "query": "Run tests before committing",
        "response": "Ran: yarn prettier --write <files> && yarn lint:ci <files> && yarn test --no-coverage --findRelatedTests <files>",
        "correction": None,
        "context": "Pre-commit checklist: prettier, lint, then test with findRelatedTests.",
    })

    # ── Code edit lessons ────────────────────────────────────────────
    lessons.append({
        "signal": "correction",
        "category": FeedbackCategory.CODE_EDIT.value,
        "intensity": 5,
        "query": "Style a component with colors",
        "response": "Used hardcoded color: '#404040'",
        "correction": "Use color tokens from the project theme instead of hardcoded hex values in styles",
        "context": "Project bans hardcoded colors. Always use design system color tokens from theme.",
    })
    lessons.append({
        "signal": "correction",
        "category": FeedbackCategory.CODE_EDIT.value,
        "intensity": 4,
        "query": "Style text in a component",
        "response": "Set font properties manually: fontSize: 16, fontWeight: 'bold'",
        "correction": "Use typography tokens: ...theme.typography.body.medium combined with semantic colors and spacing tokens.",
        "context": "Project uses design tokens for typography, color, and spacing together.",
    })
    lessons.append({
        "signal": "correction",
        "category": FeedbackCategory.CODE_EDIT.value,
        "intensity": 3,
        "query": "Optimize component performance with useMemo",
        "response": "Wrapped constant object in useMemo",
        "correction": "Don't use useMemo for constant objects - just export them directly. Check lodash before writing custom utilities.",
        "context": "Avoid over-engineering. Don't useMemo constants, don't create Context for static data.",
    })
    lessons.append({
        "signal": "thumbs_up",
        "category": FeedbackCategory.CODE_EDIT.value,
        "intensity": 5,
        "query": "Define flexible component props",
        "response": "Used union types: type Description = string | { firstLine: string; secondLine?: string }",
        "correction": None,
        "context": "Union types enable both HTML strings and structured data in the same prop.",
    })

    # ── Git lessons ──────────────────────────────────────────────────
    lessons.append({
        "signal": "correction",
        "category": FeedbackCategory.GIT.value,
        "intensity": 5,
        "query": "Create a commit",
        "response": "Used commit message: 'Fix bug' with Co-Authored-By: Claude",
        "correction": "Format: [TICKET-XXX] Description. Never include Co-Authored-By: Claude attribution.",
        "context": "Commit format must include ticket number. No Claude attribution in commits.",
    })
    lessons.append({
        "signal": "correction",
        "category": FeedbackCategory.GIT.value,
        "intensity": 4,
        "query": "Commit code changes",
        "response": "Committed without running pre-commit checks",
        "correction": "Before every commit: yarn prettier --write <files>, yarn lint:ci <files>, yarn test --no-coverage --findRelatedTests <files>",
        "context": "Pre-commit hook runs ESLint + Prettier + tests. Run checks manually first.",
    })
    lessons.append({
        "signal": "correction",
        "category": FeedbackCategory.GIT.value,
        "intensity": 5,
        "query": "Commit my changes",
        "response": "Committed automatically without asking",
        "correction": "Always ask permission before committing. Never commit without explicit user request.",
        "context": "Git workflow: always ask permission before committing.",
    })
    lessons.append({
        "signal": "correction",
        "category": FeedbackCategory.GIT.value,
        "intensity": 4,
        "query": "Include debug statements in commit",
        "response": "Left console.log statements in committed code",
        "correction": "Never commit console.log debug statements. Remove all before committing.",
        "context": "Never include in commits: console.log, hardcoded colors, snapshot tests.",
    })

    # ── Architecture lessons ─────────────────────────────────────────
    lessons.append({
        "signal": "thumbs_up",
        "category": FeedbackCategory.ARCHITECTURE.value,
        "intensity": 5,
        "query": "What's the project structure for containers?",
        "response": "Containers follow: index.tsx, actions.ts, reducer.ts, types.ts, styles.ts, __tests__/",
        "correction": None,
        "context": "Standard container pattern used across the project.",
    })
    lessons.append({
        "signal": "thumbs_up",
        "category": FeedbackCategory.ARCHITECTURE.value,
        "intensity": 4,
        "query": "Where are shared components?",
        "response": "src/shared/components/ with own styles/types, exported through index.ts",
        "correction": None,
        "context": "Shared components are self-contained with TypeScript interfaces for props.",
    })
    lessons.append({
        "signal": "correction",
        "category": FeedbackCategory.ARCHITECTURE.value,
        "intensity": 4,
        "query": "Import a module",
        "response": "Used relative import: import { foo } from '../../utils/foo'",
        "correction": "Use path aliases: import { foo } from '@/utils/foo'. @ maps to src/, ~ maps to root.",
        "context": "Path aliases configured in tsconfig.json and babel.config.js.",
    })

    # ── PR Review lessons ────────────────────────────────────────────
    lessons.append({
        "signal": "correction",
        "category": FeedbackCategory.PR_REVIEW.value,
        "intensity": 5,
        "query": "Reviewer says 'remove this'",
        "response": "Deleted the code entirely",
        "correction": "Clarify first: 'remove' might mean refactor, not delete. 'Remove tests' might mean update, not delete.",
        "context": "Always clarify 'remove' comments. Ask for clarification before destructive changes.",
    })
    lessons.append({
        "signal": "thumbs_up",
        "category": FeedbackCategory.PR_REVIEW.value,
        "intensity": 5,
        "query": "Respond to code review",
        "response": "Read comment carefully, fixed root cause, tested after fix, updated related code",
        "correction": None,
        "context": "Review response pattern: understand why, fix root cause, test, update related code.",
    })

    # ── Security lessons ─────────────────────────────────────────────
    lessons.append({
        "signal": "correction",
        "category": FeedbackCategory.SECURITY.value,
        "intensity": 5,
        "query": "Call Firebase analytics",
        "response": "Called analytics() directly from component",
        "correction": "Never call analytics() directly except from the centralized analytics utility. Use centralized logging functions.",
        "context": "Firebase analytics must go through centralized utility for type safety and consistency.",
    })

    return lessons


def seed_database():
    """Write lessons to feedback log and vectorize."""
    storage_path = get_storage_path()
    feedback_log = storage_path / "feedback_log.jsonl"
    vectorization_log = storage_path / "vectorization_log.json"
    lessons_dir = storage_path / "lessons_learned"

    # Clear old seed data
    print("Clearing old seed data...")
    feedback_log.write_text("")
    vectorization_log.write_text(json.dumps({
        "total_vectorized": 0,
        "last_vectorization": None,
        "total_documents_in_rag": 0,
    }))

    # Reset reliability scores
    reliability_file = storage_path / "reliability_scores.json"
    reliability_file.write_text("{}")

    # Clear old lesson files
    lessons_dir.mkdir(exist_ok=True)
    for f in lessons_dir.glob("*.md"):
        f.unlink()

    # Generate lessons
    lessons = create_lessons()
    print(f"\nGenerated {len(lessons)} lessons from CLAUDE.md conventions\n")

    # Write via FeedbackCapture so lesson files are also created
    fb = FeedbackCapture(str(storage_path))

    for lesson in lessons:
        cat = FeedbackCategory(lesson["category"])
        sig = lesson["signal"]
        if sig == "thumbs_up":
            fb.capture_thumbs_up(
                query=lesson["query"],
                response=lesson["response"],
                category=cat,
                intensity=lesson["intensity"],
                context=lesson["context"],
            )
        elif sig == "thumbs_down":
            fb.capture_thumbs_down(
                query=lesson["query"],
                response=lesson["response"],
                category=cat,
                intensity=lesson["intensity"],
                context=lesson["context"],
                correction=lesson.get("correction"),
            )
        elif sig == "correction":
            fb.capture_correction(
                query=lesson["query"],
                response=lesson["response"],
                correction=lesson.get("correction", ""),
                category=cat,
                intensity=lesson["intensity"],
                context=lesson["context"],
            )
        elif sig == "angry":
            fb.capture_angry(
                query=lesson["query"],
                response=lesson["response"],
                category=cat,
                intensity=lesson["intensity"],
                context=lesson["context"],
            )

    print(f"\nWrote {len(lessons)} entries to {feedback_log}")
    lesson_files = list(lessons_dir.glob("*.md"))
    print(f"Created {len(lesson_files)} lesson files in {lessons_dir}")

    # Update Thompson Sampling scores from the seed data
    from smartassist.thompson_sampling import ThompsonSamplingModel
    thompson = ThompsonSamplingModel(str(storage_path))

    for lesson in lessons:
        if lesson["signal"] in ("thumbs_up", "happy"):
            thompson.record_success(lesson["category"], lesson["intensity"])
        elif lesson["signal"] in ("thumbs_down", "angry", "sad"):
            thompson.record_failure(lesson["category"], lesson["intensity"])
        elif lesson["signal"] == "correction":
            thompson.record_failure(lesson["category"], lesson["intensity"] // 2)

    print("Updated Thompson Sampling reliability scores")

    # Vectorize into LanceDB
    print("\nVectorizing into RAG database...")
    result = subprocess.run(
        [sys.executable, "-m", "smartassist.hooks.vectorize_learnings"],
        capture_output=True,
        text=True,
    )
    print(result.stdout)
    if result.stderr:
        print(result.stderr)

    # Summary
    print("=" * 60)
    print("SEED COMPLETE")
    print("=" * 60)

    by_cat = Counter(l["category"] for l in lessons)
    by_signal = Counter(l["signal"] for l in lessons)

    print(f"\nTotal lessons: {len(lessons)}")
    print("\nBy category:")
    for cat, count in sorted(by_cat.items()):
        print(f"  {cat}: {count}")
    print("\nBy signal type:")
    for sig, count in sorted(by_signal.items()):
        print(f"  {sig}: {count}")
    print("=" * 60)


if __name__ == "__main__":
    seed_database()
    sys.exit(0)
