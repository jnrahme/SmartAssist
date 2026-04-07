"""Deep Seed — LLM-powered codebase analysis for lesson generation.

Gathers raw context from the repo (git history, PR review comments, code
structure, config files) and injects it into the LLM's context with an
instruction to call create_lesson for every pattern it finds.

The LLM does ALL the understanding. This module just collects the data.

Usage:
    smartassist seed --deep
"""

import json
import os
import re
import subprocess
import sys
from datetime import datetime, timedelta
from pathlib import Path


def _run(cmd, cwd=None, timeout=30):
    """Run a command and return stdout, or empty string on failure."""
    try:
        result = subprocess.run(
            cmd, capture_output=True, text=True, cwd=cwd, timeout=timeout,
            shell=isinstance(cmd, str),
        )
        return result.stdout.strip()
    except Exception:
        return ""


def gather_git_history(project_root, months=5):
    """Get commit history for the last N months."""
    since = (datetime.now() - timedelta(days=months * 30)).strftime("%Y-%m-%d")

    # Commit messages with stats
    log = _run(
        f'git log --since="{since}" --pretty=format:"%h|%s|%an|%ad" --date=short --shortstat',
        cwd=project_root, timeout=60,
    )

    # Most changed files
    files = _run(
        f'git log --since="{since}" --pretty=format: --name-only | sort | uniq -c | sort -rn | head -30',
        cwd=project_root, timeout=60,
    )

    # Reverted commits (patterns to avoid)
    reverts = _run(
        f'git log --since="{since}" --grep="revert" --pretty=format:"%h %s" -i',
        cwd=project_root, timeout=30,
    )

    return {
        "log": log[:5000] if log else "No git history found",
        "most_changed_files": files[:2000] if files else "",
        "reverts": reverts[:1000] if reverts else "None",
    }


def gather_pr_reviews(project_root, count=30):
    """Get PR review comments via GitHub CLI."""
    # Check if gh is available and we're in a GitHub repo
    gh_check = _run("gh auth status", cwd=project_root)
    if not gh_check and "Logged in" not in _run("gh auth status 2>&1", cwd=project_root):
        return {"available": False, "comments": []}

    # Get recent merged PRs
    prs = _run(
        f'gh pr list --state merged --limit {count} --json number,title,body,reviews,comments',
        cwd=project_root, timeout=60,
    )

    if not prs:
        return {"available": False, "comments": []}

    try:
        pr_data = json.loads(prs)
    except json.JSONDecodeError:
        return {"available": False, "comments": []}

    # Extract review comments
    review_comments = []
    for pr in pr_data:
        pr_num = pr.get("number", "?")
        title = pr.get("title", "")

        for review in pr.get("reviews", []):
            body = review.get("body", "").strip()
            if body and len(body) > 20:
                review_comments.append({
                    "pr": pr_num,
                    "title": title,
                    "comment": body[:500],
                    "state": review.get("state", ""),
                })

        for comment in pr.get("comments", []):
            body = comment.get("body", "").strip()
            if body and len(body) > 20:
                review_comments.append({
                    "pr": pr_num,
                    "title": title,
                    "comment": body[:500],
                })

    return {
        "available": True,
        "pr_count": len(pr_data),
        "comments": review_comments[:100],  # cap at 100
    }


def gather_code_structure(project_root):
    """Analyze the codebase structure."""
    root = Path(project_root)

    # Detect framework/language
    indicators = {
        "package.json": "Node.js",
        "tsconfig.json": "TypeScript",
        "pyproject.toml": "Python",
        "Cargo.toml": "Rust",
        "go.mod": "Go",
        "build.gradle": "Java/Kotlin",
        "Podfile": "iOS",
        "Gemfile": "Ruby",
    }

    detected = []
    for file, tech in indicators.items():
        if (root / file).exists():
            detected.append(tech)

    # Read key config files
    configs = {}
    config_files = [
        "package.json", "tsconfig.json", "pyproject.toml",
        ".eslintrc.json", ".eslintrc.js", ".prettierrc",
        "jest.config.js", "jest.config.ts",
        "CLAUDE.md", "CONTRIBUTING.md", ".editorconfig",
    ]

    for cf in config_files:
        path = root / cf
        if path.exists():
            try:
                content = path.read_text(errors="ignore")
                configs[cf] = content[:3000]  # cap each file
            except Exception:
                pass

    # Directory structure (top 2 levels)
    structure = _run(
        "find . -maxdepth 2 -type d -not -path '*/node_modules/*' -not -path '*/.git/*' -not -path '*/.claude/*' | sort | head -50",
        cwd=project_root,
    )

    # File type distribution
    file_types = _run(
        "find . -type f -not -path '*/node_modules/*' -not -path '*/.git/*' | sed 's/.*\\.//' | sort | uniq -c | sort -rn | head -15",
        cwd=project_root,
    )

    # Recent test files (naming patterns)
    test_files = _run(
        "find . -type f \\( -name '*.test.*' -o -name '*.spec.*' -o -name 'test_*' \\) -not -path '*/node_modules/*' | head -20",
        cwd=project_root,
    )

    return {
        "technologies": detected,
        "configs": configs,
        "structure": structure[:2000] if structure else "",
        "file_types": file_types[:500] if file_types else "",
        "test_files": test_files[:1000] if test_files else "",
    }


def gather_recent_patterns(project_root):
    """Analyze the most recent code patterns."""
    # Last 20 commits with diffs (just file names and stats)
    recent = _run(
        'git log -20 --pretty=format:"--- %s ---" --stat',
        cwd=project_root, timeout=30,
    )

    # Common import patterns
    imports = _run(
        "grep -rh '^import\\|^from\\|^const.*require' --include='*.ts' --include='*.tsx' --include='*.js' --include='*.py' . 2>/dev/null | sort | uniq -c | sort -rn | head -30",
        cwd=project_root, timeout=30,
    )

    # Error handling patterns
    error_patterns = _run(
        "grep -rn 'catch\\|throw\\|try {\\|except\\|raise\\|Error(\\|.error(' --include='*.ts' --include='*.tsx' --include='*.js' --include='*.py' . 2>/dev/null | head -20",
        cwd=project_root, timeout=30,
    )

    # Test utility files (custom wrappers, fixtures)
    test_utils = ""
    for pattern in ["**/test-utils*", "**/testUtils*", "**/test_utils*", "**/conftest*", "**/setup*test*"]:
        found = _run(f"find . -path '{pattern}' -not -path '*/node_modules/*' 2>/dev/null", cwd=project_root)
        if found:
            test_utils += found + "\n"
            # Read the first test util file
            first_file = found.split("\n")[0].strip()
            if first_file:
                content = _run(f"head -50 '{first_file}'", cwd=project_root)
                if content:
                    test_utils += f"\n--- {first_file} ---\n{content}\n"

    # CI/CD config
    ci_config = ""
    for ci_file in [".github/workflows/ci.yml", ".github/workflows/main.yml", ".github/workflows/pr-checks.yml",
                     ".circleci/config.yml", "Jenkinsfile", ".gitlab-ci.yml"]:
        path = Path(project_root) / ci_file
        if path.exists():
            try:
                ci_config += f"\n--- {ci_file} ---\n{path.read_text(errors='ignore')[:2000]}\n"
            except Exception:
                pass

    # README setup instructions
    readme = ""
    for readme_name in ["README.md", "README.rst", "README"]:
        path = Path(project_root) / readme_name
        if path.exists():
            try:
                readme = path.read_text(errors="ignore")[:3000]
            except Exception:
                pass
            break

    return {
        "recent_commits": recent[:3000] if recent else "",
        "common_imports": imports[:2000] if imports else "",
        "error_patterns": error_patterns[:1500] if error_patterns else "",
        "test_utils": test_utils[:2000] if test_utils else "",
        "ci_config": ci_config[:3000] if ci_config else "",
        "readme": readme[:2000] if readme else "",
    }


def build_deep_seed_prompt(project_root):
    """Gather all context and build the instruction prompt for the LLM."""
    print("Gathering codebase context...\n")

    print("  [1/4] Git history (last 5 months)...")
    git = gather_git_history(project_root)

    print("  [2/4] PR review comments...")
    prs = gather_pr_reviews(project_root)

    print("  [3/4] Code structure + config files...")
    structure = gather_code_structure(project_root)

    print("  [4/4] Recent patterns...")
    patterns = gather_recent_patterns(project_root)

    # Build the prompt
    sections = []

    sections.append("# CODEBASE ANALYSIS FOR LESSON CREATION\n")
    sections.append("You are analyzing this codebase to create 50-100 project-specific lessons.")
    sections.append("Each lesson should be an actionable rule that prevents mistakes and encodes best practices.\n")

    # Technologies
    if structure["technologies"]:
        sections.append(f"## Technologies Detected\n{', '.join(structure['technologies'])}\n")

    # Config files
    if structure["configs"]:
        sections.append("## Configuration Files\n")
        for name, content in structure["configs"].items():
            sections.append(f"### {name}\n```\n{content[:1500]}\n```\n")

    # Directory structure
    if structure["structure"]:
        sections.append(f"## Directory Structure\n```\n{structure['structure']}\n```\n")

    # File types
    if structure["file_types"]:
        sections.append(f"## File Type Distribution\n```\n{structure['file_types']}\n```\n")

    # Test patterns
    if structure["test_files"]:
        sections.append(f"## Test File Patterns\n```\n{structure['test_files']}\n```\n")

    # Git history
    sections.append(f"## Git History (Last 5 Months)\n```\n{git['log'][:3000]}\n```\n")

    if git["most_changed_files"]:
        sections.append(f"## Most Changed Files\n```\n{git['most_changed_files']}\n```\n")

    if git["reverts"] and git["reverts"] != "None":
        sections.append(f"## Reverted Commits (Mistakes to Learn From)\n```\n{git['reverts']}\n```\n")

    # PR review comments
    if prs["available"] and prs["comments"]:
        sections.append(f"## PR Review Comments ({len(prs['comments'])} comments from {prs['pr_count']} PRs)\n")
        for c in prs["comments"][:50]:
            sections.append(f"- PR #{c['pr']} ({c['title']}): {c['comment'][:200]}")
        sections.append("")

    # Recent patterns
    if patterns["recent_commits"]:
        sections.append(f"## Recent Commits (Last 20)\n```\n{patterns['recent_commits'][:2000]}\n```\n")

    if patterns["common_imports"]:
        sections.append(f"## Common Import Patterns\n```\n{patterns['common_imports'][:1000]}\n```\n")

    # Error handling
    if patterns.get("error_patterns"):
        sections.append(f"## Error Handling Patterns (sample from codebase)\n```\n{patterns['error_patterns']}\n```\n")

    # Test utilities
    if patterns.get("test_utils"):
        sections.append(f"## Test Utility Files (custom wrappers, fixtures)\n```\n{patterns['test_utils']}\n```\n")

    # CI/CD config
    if patterns.get("ci_config"):
        sections.append(f"## CI/CD Configuration\n```\n{patterns['ci_config'][:2000]}\n```\n")

    # README
    if patterns.get("readme"):
        sections.append(f"## README (setup instructions)\n```\n{patterns['readme'][:1500]}\n```\n")

    # THE ARCHITECT-LEVEL INSTRUCTION
    sections.append("""
## YOUR TASK — THINK LIKE A SENIOR ARCHITECT

You are a senior architect onboarding a new developer to this codebase. Create 50-100
lessons by calling `create_lesson` for each one. Every lesson must pass this test:
**"Would an AI agent make a mistake without this lesson?"** If removing the lesson
wouldn't change behavior, don't create it.

### WHAT TO CREATE (in priority order)

**1. TRIBAL KNOWLEDGE — things that look wrong but are intentional (10-15 lessons)**
- Workarounds that look like bugs but exist for a reason
- Historical decisions that aren't documented ("we use X instead of Y because...")
- Deprecated patterns that still exist in the code but shouldn't be replicated
- Edge cases where the "obvious" approach breaks

**2. BUILD / TEST / DEPLOY COMMANDS — the exact commands, not generic (5-10 lessons)**
- The specific test command with the right flags for this project
- How to run tests for a single file vs full suite
- Pre-commit checks that must pass
- Environment variables that must be set
- CI-specific gotchas (what passes locally but fails in CI)

**3. IMPORT AND MODULE BOUNDARIES — what imports from where (10-15 lessons)**
- Wrapper utilities that MUST be used instead of direct imports (e.g., test-utils instead of @testing-library/react)
- Path alias conventions (@/ or ~/ vs relative)
- Which modules are allowed to import from which others
- Shared vs private exports

**4. ERROR HANDLING AND API PATTERNS (5-10 lessons)**
- How this project handles errors (throw? return shape? result types?)
- API response shapes and contracts
- Authentication/authorization middleware patterns
- Where error boundaries are and what they catch

**5. TESTING ARCHITECTURE — not "write tests" but HOW (10-15 lessons)**
- The test wrapper/provider pattern for this project
- Mock strategies (what to mock, what to let run real)
- Fixture and test data conventions
- Which tests are integration vs unit and what each is allowed to do
- Test file naming and co-location patterns

**6. PR REVIEW PATTERNS — from actual review comments (5-10 lessons)**
- Convert every meaningful PR review comment into a lesson
- These are REAL corrections from REAL reviewers — they're gold
- Focus on comments that say "don't do X, do Y instead"

**7. GIT AND WORKFLOW CONVENTIONS (5-10 lessons)**
- Commit message format (ticket prefix, style)
- Branch naming conventions
- What to never push directly to main
- Code review requirements

**8. FRAMEWORK-SPECIFIC PATTERNS — for THIS setup specifically (5-10 lessons)**
- State management conventions
- Component patterns (container/presentational, hooks, etc.)
- Navigation/routing patterns
- Styling approach (CSS modules, styled-components, theme tokens, etc.)

### HOW TO WRITE EACH LESSON

BAD (junior-level, useless):
- "Use TypeScript" — Claude already knows this
- "Write clean code" — not actionable
- "Handle errors properly" — vague

GOOD (architect-level, saves time):
- "Use `renderWithProviders()` from `src/test-utils` instead of plain `render()` — it wraps components with Redux store, ThemeProvider, and NavigationContainer that most components need"
- "Never import from `@testing-library/react` directly in test files — always import from `src/test-utils` which re-exports everything plus custom render"
- "Commit messages must start with [BTMAPP-XXXX] Jira ticket — CI rejects commits without it"
- "The `useAnalytics()` hook in `src/hooks/useAnalytics.ts` wraps Firebase — never call Firebase analytics directly, always go through this hook"
- "Run `yarn test:noCoverage -- --testPathPattern=<file>` to test a single file — `yarn test` runs full suite with coverage which takes 4 minutes"

### RULES

- Category must be one of: testing, code_edit, git, architecture, pr_review, security, debugging
- Each lesson must be >30 characters
- Each lesson must start with an action verb
- Reference actual file paths, commands, and patterns from the analysis above
- If you're unsure about something, skip it — wrong lessons are worse than no lessons
- Call `create_lesson` for EACH lesson. Do NOT just list them.
- Use `sentiment: "negative"` for corrections (don't do X) and `sentiment: "positive"` for best practices (always do Y)
- Set `intensity: 5` for critical rules (never/always), `intensity: 3` for conventions, `intensity: 1` for preferences
""")

    prompt = "\n".join(sections)

    # Stats
    pr_count = len(prs.get("comments", []))
    config_count = len(structure.get("configs", {}))
    tech_count = len(structure.get("technologies", []))

    print(f"\nContext gathered:")
    print(f"  Technologies: {tech_count}")
    print(f"  Config files: {config_count}")
    print(f"  PR review comments: {pr_count}")
    print(f"  Prompt size: {len(prompt):,} characters")

    return prompt


def run_deep_seed():
    """Execute the deep seed — gather context and inject into the LLM."""
    project_root = os.getcwd()

    # Verify we're in a git repo
    if not Path(project_root, ".git").exists():
        print("Error: Not a git repository. Run this from your project root.")
        return 1

    prompt = build_deep_seed_prompt(project_root)

    # Output as hook-style injection for the LLM to consume
    print(f"\n{'='*60}")
    print("DEEP SEED READY")
    print(f"{'='*60}")
    print("\nThe codebase analysis has been prepared.")
    print("Copy the content below and paste it into your Claude or Codex session,")
    print("or pipe it directly:\n")
    print("  smartassist seed --deep | pbcopy")
    print("\nThe LLM will read the analysis and call create_lesson for each pattern.\n")
    print(f"{'='*60}\n")

    # Print the prompt to stdout for piping
    print(prompt)

    return 0


if __name__ == "__main__":
    run_deep_seed()
