#!/usr/bin/env python3
"""
Seed RAG Database from CLAUDE.md and MEMORY.md
Extracts project conventions as baseline lessons for the RLHF system.
Run once (or after major CLAUDE.md updates) to populate the knowledge base.
"""

import os
import re
import sys
import json
import subprocess
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path
from typing import List, Optional

from smartassist.config import get_storage_path
from smartassist.feedback_system import FeedbackCapture, FeedbackCategory, FeedbackSignal


# ── Markdown parsing ─────────────────────────────────────────────────────

@dataclass
class MarkdownSection:
    """A parsed section from a markdown file."""
    header: str
    level: int
    parent_header: Optional[str]
    body: str
    bullets: List[str] = field(default_factory=list)
    code_blocks: List[dict] = field(default_factory=list)


def find_claudemd(start_path: Optional[str] = None) -> Optional[Path]:
    """Walk up from start_path (or cwd) to find CLAUDE.md."""
    current = Path(start_path) if start_path else Path.cwd()
    # Ensure we start from a directory
    if current.is_file():
        current = current.parent
    for _ in range(50):  # safety limit
        candidate = current / "CLAUDE.md"
        if candidate.is_file():
            return candidate
        parent = current.parent
        if parent == current:
            break
        current = parent
    return None


def extract_bullets(text: str) -> List[str]:
    """Extract bullet lines (- or *) from text, handling multi-line continuations."""
    bullets = []
    current_bullet = None
    for line in text.split("\n"):
        stripped = line.rstrip()
        # New bullet
        match = re.match(r"^[\s]*[-*]\s+(.+)", stripped)
        if match:
            if current_bullet is not None:
                bullets.append(current_bullet.strip())
            current_bullet = match.group(1)
        elif current_bullet is not None and stripped and not stripped.startswith("#"):
            # Continuation line (indented or just text following a bullet)
            if re.match(r"^\s{2,}", line) and not re.match(r"^\s*[-*]\s", stripped):
                current_bullet += " " + stripped.strip()
            else:
                bullets.append(current_bullet.strip())
                current_bullet = None
        elif current_bullet is not None and not stripped:
            # Blank line ends current bullet
            bullets.append(current_bullet.strip())
            current_bullet = None
    if current_bullet is not None:
        bullets.append(current_bullet.strip())
    return bullets


def extract_code_blocks(text: str) -> List[dict]:
    """Extract fenced code blocks with language tags."""
    blocks = []
    pattern = re.compile(r"```(\w*)\n(.*?)```", re.DOTALL)
    for match in pattern.finditer(text):
        lang = match.group(1) or "text"
        code = match.group(2).strip()
        if code:
            blocks.append({"language": lang, "code": code})
    return blocks


def parse_markdown_sections(content: str) -> List[MarkdownSection]:
    """Split markdown by ATX (###) and setext (===/---) headers.

    Returns list of MarkdownSection with header, level, parent tracking,
    bullets, and code blocks.
    """
    lines = content.split("\n")
    sections: List[MarkdownSection] = []
    # Stack of (level, header) for parent tracking
    header_stack: List[tuple] = []

    i = 0
    current_header = None
    current_level = 0
    current_body_lines: List[str] = []

    def _flush():
        nonlocal current_header, current_body_lines
        if current_header is not None:
            body = "\n".join(current_body_lines)
            # Find parent: walk stack for nearest lower level
            parent = None
            for lvl, hdr in reversed(header_stack):
                if lvl < current_level:
                    parent = hdr
                    break
            section = MarkdownSection(
                header=current_header,
                level=current_level,
                parent_header=parent,
                body=body,
                bullets=extract_bullets(body),
                code_blocks=extract_code_blocks(body),
            )
            sections.append(section)

    while i < len(lines):
        line = lines[i]

        # Check for setext header (next line is === or ---)
        if i + 1 < len(lines):
            next_line = lines[i + 1].rstrip()
            if re.match(r"^={3,}\s*$", next_line) and line.strip():
                _flush()
                current_header = line.strip()
                current_level = 1
                header_stack.append((current_level, current_header))
                current_body_lines = []
                i += 2
                continue
            if re.match(r"^-{3,}\s*$", next_line) and line.strip():
                _flush()
                current_header = line.strip()
                current_level = 2
                header_stack.append((current_level, current_header))
                current_body_lines = []
                i += 2
                continue

        # Check for ATX header
        atx = re.match(r"^(#{1,6})\s+(.+)", line)
        if atx:
            _flush()
            current_level = len(atx.group(1))
            current_header = atx.group(2).strip()
            header_stack.append((current_level, current_header))
            current_body_lines = []
            i += 1
            continue

        # Regular body line
        if current_header is not None:
            current_body_lines.append(line)
        i += 1

    _flush()
    return sections


# ── Category mapping ─────────────────────────────────────────────────────

_CATEGORY_KEYWORDS = {
    FeedbackCategory.TESTING: ["test", "jest", "mock", "e2e", "coverage", "detox"],
    FeedbackCategory.GIT: ["git", "commit", "branch"],
    FeedbackCategory.CODE_EDIT: ["style", "lint", "format", "component", "code quality",
                                  "code edit", "pattern", "import"],
    FeedbackCategory.ARCHITECTURE: ["architecture", "structure", "directory", "project structure"],
    FeedbackCategory.SECURITY: ["security", "auth", "credential", "firebase"],
    FeedbackCategory.DEBUGGING: ["debug", "error", "crash", "crashlytics"],
    FeedbackCategory.PR_REVIEW: ["pr", "review", "pull request"],
}


def _keyword_matches(text: str, keywords: list) -> bool:
    """Check if any keyword matches in text using word-boundary-aware matching."""
    lower = text.lower()
    for kw in keywords:
        # Multi-word keywords: simple substring match
        if " " in kw:
            if kw in lower:
                return True
        elif len(kw) <= 3:
            # Very short keywords: require full word boundary to avoid
            # false positives (e.g. "pr" matching "practices")
            if re.search(rf"\b{re.escape(kw)}\b", lower):
                return True
        else:
            # Longer keywords: word boundary at start, allow prefix match
            # e.g. "test" matches "Testing", "auth" matches "Authentication"
            if re.search(rf"\b{re.escape(kw)}", lower):
                return True
    return False


def map_section_to_category(section: MarkdownSection) -> FeedbackCategory:
    """Map a section to a FeedbackCategory by checking header keywords."""
    # Check section header first, then parent header
    for text in [section.header, section.parent_header or ""]:
        for cat, keywords in _CATEGORY_KEYWORDS.items():
            if _keyword_matches(text, keywords):
                return cat
    return FeedbackCategory.CODE_EDIT


# ── Lesson generation ────────────────────────────────────────────────────

_ACTION_VERBS = re.compile(
    r"\b(use|never|always|avoid|run|prefer|ensure|don't|do not|must|should"
    r"|check|install|import|export|follow|include|exclude|set|add|remove"
    r"|replace|create|delete|keep|place|put|wrap|call|mock|test)\b",
    re.IGNORECASE,
)

_INTENSITY_HIGH = re.compile(r"\b(never|always|must|critical|important|banned?)\b", re.IGNORECASE)
_INTENSITY_MED = re.compile(r"\b(should|avoid|prefer|recommended)\b", re.IGNORECASE)


def is_actionable_bullet(text: str) -> bool:
    """Return True if bullet contains actionable instruction."""
    # Filter short text
    if len(text) < 30:
        return False
    # Filter version info lines like "React Native 0.77.1"
    if re.match(r"^\*\*\w+\*\*\s+\d+\.\d+", text):
        return False
    if re.match(r"^\*\*[\w\s]+\*\*\s*([-:]|for\s)", text):
        # Description-style: "**Thing** - description" or "**Thing**: description"
        # Only keep if it also has action verbs
        if not _ACTION_VERBS.search(text):
            return False
    # Has action verbs or inline code
    if _ACTION_VERBS.search(text):
        return True
    if "`" in text:
        return True
    return False


def estimate_intensity(text: str) -> int:
    """Estimate lesson intensity from 1-5."""
    if _INTENSITY_HIGH.search(text):
        return 5
    if _INTENSITY_MED.search(text):
        return 4
    return 3


def generate_bad_response(bullet: str) -> str:
    """Generate a plausible bad response from a bullet instruction."""
    lower = bullet.lower()

    # "instead of X" pattern
    match = re.search(r"instead of\s+(.+?)(?:\.|$)", lower)
    if match:
        return match.group(1).strip().capitalize()

    # "not X" / "don't X" pattern
    match = re.search(r"(?:not|don't|do not|never)\s+(.+?)(?:\.|,|$)", lower)
    if match:
        bad = match.group(1).strip()
        # Turn "use npm" into "Used npm"
        if bad.startswith("use "):
            return "Used " + bad[4:]
        return bad.capitalize()

    return "Did not follow project conventions"


def bullet_to_lesson(
    bullet: str,
    section: MarkdownSection,
    category: FeedbackCategory,
) -> Optional[dict]:
    """Convert an actionable bullet into a lesson dict."""
    if not is_actionable_bullet(bullet):
        return None

    # Clean bullet text — strip leading bold markers for cleaner lesson text
    clean = re.sub(r"^\*\*[\w\s]+\*\*\s*[-:]\s*", "", bullet).strip()
    if not clean:
        clean = bullet

    return {
        "signal": "correction",
        "category": category.value,
        "intensity": estimate_intensity(bullet),
        "query": f"Working on: {section.header}",
        "response": generate_bad_response(bullet),
        "correction": clean,
        "context": f"From CLAUDE.md section: {section.header}"
                   + (f" > {section.parent_header}" if section.parent_header else ""),
    }


def code_block_to_lesson(
    language: str,
    code: str,
    section: MarkdownSection,
    category: FeedbackCategory,
) -> Optional[dict]:
    """Convert bash/shell command blocks to lessons."""
    if language not in ("bash", "shell", "sh", "zsh"):
        return None
    # Extract the actual commands (skip comments)
    commands = [
        line.strip()
        for line in code.split("\n")
        if line.strip() and not line.strip().startswith("#")
    ]
    if not commands:
        return None

    command_text = " && ".join(commands[:3])  # limit to first 3 commands
    return {
        "signal": "correction",
        "category": category.value,
        "intensity": 3,
        "query": f"Run commands for: {section.header}",
        "response": "Used wrong command or skipped this step",
        "correction": f"Use: {command_text}",
        "context": f"From CLAUDE.md section: {section.header}",
    }


# ── Main entry points ────────────────────────────────────────────────────

def create_hardcoded_lessons():
    """Original hardcoded lessons for bt-mobile-app (fallback)."""
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


def create_lessons() -> list:
    """Dynamically parse CLAUDE.md if found, else fall back to hardcoded lessons."""
    claudemd = find_claudemd()
    if claudemd is None:
        print("No CLAUDE.md found, using hardcoded lessons")
        return create_hardcoded_lessons()

    print(f"Parsing CLAUDE.md: {claudemd}")
    content = claudemd.read_text(encoding="utf-8")
    sections = parse_markdown_sections(content)

    lessons = []
    seen_corrections = set()  # dedup by correction text

    for section in sections:
        category = map_section_to_category(section)

        # Process bullets
        for bullet in section.bullets:
            lesson = bullet_to_lesson(bullet, section, category)
            if lesson and lesson["correction"] not in seen_corrections:
                seen_corrections.add(lesson["correction"])
                lessons.append(lesson)

        # Process code blocks
        for block in section.code_blocks:
            lesson = code_block_to_lesson(
                block["language"], block["code"], section, category,
            )
            if lesson and lesson["correction"] not in seen_corrections:
                seen_corrections.add(lesson["correction"])
                lessons.append(lesson)

    if not lessons:
        print("No actionable lessons found in CLAUDE.md, using hardcoded lessons")
        return create_hardcoded_lessons()

    print(f"Extracted {len(lessons)} lessons from CLAUDE.md")
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
