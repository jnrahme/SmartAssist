"""Tests for smartassist.hooks.seed_from_claudemd dynamic CLAUDE.md parsing."""

import textwrap
from types import SimpleNamespace
from pathlib import Path

import pytest

from smartassist.hooks.seed_from_claudemd import (
    find_claudemd,
    parse_markdown_sections,
    extract_bullets,
    extract_code_blocks,
    map_section_to_category,
    is_actionable_bullet,
    estimate_intensity,
    generate_bad_response,
    bullet_to_lesson,
    code_block_to_lesson,
    create_lessons,
    create_hardcoded_lessons,
    seed_database,
    MarkdownSection,
)
from smartassist.feedback_system import FeedbackCategory
from smartassist.store import list_lessons


# ── find_claudemd ────────────────────────────────────────────────────────


class TestFindClaudemd:
    def test_finds_in_current_dir(self, tmp_path):
        (tmp_path / "CLAUDE.md").write_text("# Test")
        result = find_claudemd(str(tmp_path))
        assert result is not None
        assert result.name == "CLAUDE.md"

    def test_walks_up_to_find(self, tmp_path):
        (tmp_path / "CLAUDE.md").write_text("# Test")
        nested = tmp_path / "a" / "b" / "c"
        nested.mkdir(parents=True)
        result = find_claudemd(str(nested))
        assert result is not None
        assert result == tmp_path / "CLAUDE.md"

    def test_returns_none_when_missing(self, tmp_path):
        nested = tmp_path / "empty"
        nested.mkdir()
        result = find_claudemd(str(nested))
        assert result is None

    def test_handles_file_path(self, tmp_path):
        (tmp_path / "CLAUDE.md").write_text("# Test")
        some_file = tmp_path / "foo.txt"
        some_file.write_text("hello")
        result = find_claudemd(str(some_file))
        assert result is not None


# ── extract_bullets ──────────────────────────────────────────────────────


class TestExtractBullets:
    def test_extracts_dash_bullets(self):
        text = "- First bullet\n- Second bullet\n- Third bullet"
        assert extract_bullets(text) == ["First bullet", "Second bullet", "Third bullet"]

    def test_extracts_asterisk_bullets(self):
        text = "* First\n* Second"
        assert extract_bullets(text) == ["First", "Second"]

    def test_handles_backticks(self):
        text = "- Use `yarn test` for testing\n- Run `yarn lint` for linting"
        bullets = extract_bullets(text)
        assert len(bullets) == 2
        assert "`yarn test`" in bullets[0]

    def test_handles_multiline_continuation(self):
        text = "- This is a long bullet\n  that continues on the next line\n- Second bullet"
        bullets = extract_bullets(text)
        assert len(bullets) == 2
        assert "continues" in bullets[0]

    def test_ignores_non_bullets(self):
        text = "Some paragraph text\nMore text\n\n- Actual bullet"
        bullets = extract_bullets(text)
        assert len(bullets) == 1
        assert bullets[0] == "Actual bullet"


# ── extract_code_blocks ──────────────────────────────────────────────────


class TestExtractCodeBlocks:
    def test_extracts_fenced_block(self):
        text = "```bash\nyarn test\n```"
        blocks = extract_code_blocks(text)
        assert len(blocks) == 1
        assert blocks[0]["language"] == "bash"
        assert blocks[0]["code"] == "yarn test"

    def test_extracts_multiple_blocks(self):
        text = "```bash\nyarn test\n```\n\nSome text\n\n```typescript\nconst x = 1;\n```"
        blocks = extract_code_blocks(text)
        assert len(blocks) == 2
        assert blocks[0]["language"] == "bash"
        assert blocks[1]["language"] == "typescript"

    def test_defaults_to_text_language(self):
        text = "```\nsome code\n```"
        blocks = extract_code_blocks(text)
        assert blocks[0]["language"] == "text"

    def test_skips_empty_blocks(self):
        text = "```bash\n\n```"
        blocks = extract_code_blocks(text)
        assert len(blocks) == 0


# ── parse_markdown_sections ──────────────────────────────────────────────


class TestParseMarkdownSections:
    def test_atx_headers(self):
        content = textwrap.dedent("""\
        ### Testing

        - Always mock before imports
        - Use `toBeVisible` matcher

        ### Git

        - Never commit without asking
        """)
        sections = parse_markdown_sections(content)
        assert len(sections) == 2
        assert sections[0].header == "Testing"
        assert sections[0].level == 3
        assert len(sections[0].bullets) == 2
        assert sections[1].header == "Git"

    def test_setext_headers(self):
        content = textwrap.dedent("""\
        Development Commands
        --------------------

        - `yarn test` - Run tests

        Architecture
        ============

        - Project uses React Native
        """)
        sections = parse_markdown_sections(content)
        assert len(sections) == 2
        assert sections[0].header == "Development Commands"
        assert sections[0].level == 2
        assert sections[1].header == "Architecture"
        assert sections[1].level == 1

    def test_parent_tracking(self):
        content = textwrap.dedent("""\
        ## Testing

        ### Unit Testing Guidelines

        - Always put mocks before imports

        ### E2E Testing

        - Use Detox for E2E
        """)
        sections = parse_markdown_sections(content)
        assert len(sections) == 3
        assert sections[0].header == "Testing"
        assert sections[1].header == "Unit Testing Guidelines"
        assert sections[1].parent_header == "Testing"
        assert sections[2].header == "E2E Testing"
        assert sections[2].parent_header == "Testing"

    def test_code_blocks_in_sections(self):
        content = textwrap.dedent("""\
        ### Setup

        ```bash
        yarn install
        yarn start
        ```
        """)
        sections = parse_markdown_sections(content)
        assert len(sections) == 1
        assert len(sections[0].code_blocks) == 1
        assert sections[0].code_blocks[0]["language"] == "bash"

    def test_mixed_header_styles(self):
        content = textwrap.dedent("""\
        CLAUDE.md
        =========

        ## Development Commands

        ### Building and Running

        - `yarn ios` - Run on iOS

        ### Testing

        - `yarn test` - Run tests
        """)
        sections = parse_markdown_sections(content)
        headers = [s.header for s in sections]
        assert "CLAUDE.md" in headers
        assert "Development Commands" in headers
        assert "Building and Running" in headers
        assert "Testing" in headers


# ── map_section_to_category ──────────────────────────────────────────────


class TestMapSectionToCategory:
    def _section(self, header, parent=None):
        return MarkdownSection(
            header=header, level=3, parent_header=parent,
            body="", bullets=[], code_blocks=[],
        )

    def test_testing_keywords(self):
        assert map_section_to_category(self._section("Unit Testing Guidelines")) == FeedbackCategory.TESTING
        assert map_section_to_category(self._section("Jest Configuration")) == FeedbackCategory.TESTING

    def test_git_keywords(self):
        assert map_section_to_category(self._section("Git Workflow")) == FeedbackCategory.GIT
        assert map_section_to_category(self._section("Commit Rules")) == FeedbackCategory.GIT

    def test_architecture_keywords(self):
        assert map_section_to_category(self._section("Project Structure")) == FeedbackCategory.ARCHITECTURE

    def test_security_keywords(self):
        assert map_section_to_category(self._section("Firebase Analytics")) == FeedbackCategory.SECURITY
        assert map_section_to_category(self._section("Authentication")) == FeedbackCategory.SECURITY

    def test_parent_fallback(self):
        section = self._section("Best Practices", parent="Testing")
        assert map_section_to_category(section) == FeedbackCategory.TESTING

    def test_defaults_to_code_edit(self):
        assert map_section_to_category(self._section("Random Notes")) == FeedbackCategory.CODE_EDIT

    def test_pr_review(self):
        assert map_section_to_category(self._section("PR Review Workflow")) == FeedbackCategory.PR_REVIEW

    def test_debugging(self):
        assert map_section_to_category(self._section("Error Handling")) == FeedbackCategory.DEBUGGING


# ── is_actionable_bullet ─────────────────────────────────────────────────


class TestIsActionableBullet:
    def test_command_bullet(self):
        assert is_actionable_bullet("Use `yarn test` for running unit tests with coverage")

    def test_rule_bullet(self):
        assert is_actionable_bullet("Never use snapshot tests - always prefer toBeVisible() for visibility checks")

    def test_should_bullet(self):
        assert is_actionable_bullet("Always put jest.mock() calls BEFORE import statements in test files")

    def test_filters_version_info(self):
        assert not is_actionable_bullet("**React Native** 0.77.1 with React 18.3.1")

    def test_filters_short_text(self):
        assert not is_actionable_bullet("Uses Yarn")

    def test_description_without_verbs(self):
        assert not is_actionable_bullet("**TypeScript** - statically typed JavaScript superset")

    def test_description_with_verbs(self):
        assert is_actionable_bullet("**ESLint** - Use the project's ESLint configuration for consistent code style enforcement")


# ── estimate_intensity ───────────────────────────────────────────────────


class TestEstimateIntensity:
    def test_never_is_5(self):
        assert estimate_intensity("Never use snapshot tests") == 5

    def test_always_is_5(self):
        assert estimate_intensity("Always put mocks before imports") == 5

    def test_should_is_4(self):
        assert estimate_intensity("You should use yarn instead") == 4

    def test_avoid_is_4(self):
        assert estimate_intensity("Avoid using relative imports when possible") == 4

    def test_default_is_3(self):
        assert estimate_intensity("Use `yarn test` for running tests") == 3


# ── generate_bad_response ────────────────────────────────────────────────


class TestGenerateBadResponse:
    def test_instead_of_pattern(self):
        result = generate_bad_response("Use yarn instead of npm for package management")
        assert "npm" in result.lower()

    def test_never_pattern(self):
        result = generate_bad_response("Never use snapshot tests for component testing")
        assert "snapshot" in result.lower()

    def test_dont_pattern(self):
        result = generate_bad_response("Don't use hardcoded colors in your component styles")
        assert "hardcoded" in result.lower()

    def test_fallback(self):
        result = generate_bad_response("Use yarn test for running all the unit tests")
        assert result  # just ensure non-empty


# ── bullet_to_lesson ─────────────────────────────────────────────────────


class TestBulletToLesson:
    def _section(self, header="Testing"):
        return MarkdownSection(
            header=header, level=3, parent_header=None,
            body="", bullets=[], code_blocks=[],
        )

    def test_produces_valid_lesson(self):
        bullet = "Never use snapshot tests - always use toBeVisible() for element assertions"
        lesson = bullet_to_lesson(bullet, self._section(), FeedbackCategory.TESTING)
        assert lesson is not None
        assert lesson["signal"] == "correction"
        assert lesson["category"] == "testing"
        assert lesson["correction"] == bullet
        assert lesson["intensity"] == 5  # "never"
        assert "Testing" in lesson["context"]

    def test_returns_none_for_descriptive(self):
        bullet = "React Native framework"
        lesson = bullet_to_lesson(bullet, self._section(), FeedbackCategory.CODE_EDIT)
        assert lesson is None

    def test_includes_parent_in_context(self):
        section = MarkdownSection(
            header="Guidelines", level=3, parent_header="Testing",
            body="", bullets=[], code_blocks=[],
        )
        bullet = "Always put mocks before imports in all your test files"
        lesson = bullet_to_lesson(bullet, section, FeedbackCategory.TESTING)
        assert "Testing" in lesson["context"]


# ── code_block_to_lesson ─────────────────────────────────────────────────


class TestCodeBlockToLesson:
    def _section(self, header="Setup"):
        return MarkdownSection(
            header=header, level=3, parent_header=None,
            body="", bullets=[], code_blocks=[],
        )

    def test_bash_block(self):
        lesson = code_block_to_lesson(
            "bash", "yarn install\nyarn start", self._section(), FeedbackCategory.CODE_EDIT,
        )
        assert lesson is not None
        assert "yarn install" in lesson["correction"]
        assert lesson["signal"] == "correction"

    def test_skips_non_shell(self):
        lesson = code_block_to_lesson(
            "typescript", "const x = 1;", self._section(), FeedbackCategory.CODE_EDIT,
        )
        assert lesson is None

    def test_skips_comments_only(self):
        lesson = code_block_to_lesson(
            "bash", "# just a comment\n# another comment",
            self._section(), FeedbackCategory.CODE_EDIT,
        )
        assert lesson is None


# ── create_lessons (integration) ─────────────────────────────────────────


class TestCreateLessons:
    def test_falls_back_when_no_claudemd(self, tmp_path, monkeypatch):
        """With no CLAUDE.md anywhere, should return hardcoded lessons."""
        monkeypatch.chdir(tmp_path)
        lessons = create_lessons()
        assert len(lessons) == len(create_hardcoded_lessons())

    def test_parses_real_claudemd(self, tmp_path, monkeypatch):
        """With a sample CLAUDE.md, should extract dynamic lessons."""
        sample = textwrap.dedent("""\
        CLAUDE.md
        =========

        ## Testing

        ### Unit Testing Guidelines

        - Always put jest.mock() calls BEFORE import statements for proper hoisting behavior
        - Use `toBeVisible()` matcher instead of `toBeTruthy()` for checking element visibility
        - Never use snapshot tests - always prefer behavioral testing with assertions
        - Coverage thresholds: branches 79%, functions 84%, lines 89%, statements 89%

        ### Commands

        ```bash
        yarn test
        yarn lint
        ```

        ## Git Workflow

        - Always ask permission before committing code to the repository
        - Commit format: `[TICKET-XXX] Description` - must include ticket number
        - Never include Co-Authored-By: Claude attribution in any commit messages
        - Never commit console.log debug statements to the codebase

        ## Code Quality

        - Use semantic color tokens from the theme instead of hardcoded hex values
        - Use path aliases: `@/` maps to `src/` and `~/` maps to the project root
        - Avoid over-engineering - don't use `useMemo` for constant objects that never change

        ## Architecture

        - Containers follow pattern: `index.tsx`, `actions.ts`, `reducer.ts`, `types.ts`, `styles.ts`
        """)

        (tmp_path / "CLAUDE.md").write_text(sample)
        monkeypatch.chdir(tmp_path)
        lessons = create_lessons()

        assert len(lessons) >= 8  # Should get at least 8 actionable bullets
        categories = {l["category"] for l in lessons}
        assert "testing" in categories
        assert "git" in categories

        # All should be corrections (dynamic parser only produces corrections)
        assert all(l["signal"] == "correction" for l in lessons)

    def test_deduplicates_lessons(self, tmp_path, monkeypatch):
        """Same bullet in two sections should only appear once."""
        sample = textwrap.dedent("""\
        ## Section A

        - Never use snapshot tests for testing components in this codebase

        ## Section B

        - Never use snapshot tests for testing components in this codebase
        """)
        (tmp_path / "CLAUDE.md").write_text(sample)
        monkeypatch.chdir(tmp_path)
        lessons = create_lessons()
        corrections = [l["correction"] for l in lessons]
        assert len(corrections) == len(set(corrections))


class TestHardcodedLessons:
    def test_returns_expected_count(self):
        lessons = create_hardcoded_lessons()
        assert len(lessons) == 19

    def test_all_have_required_fields(self):
        for lesson in create_hardcoded_lessons():
            assert "signal" in lesson
            assert "category" in lesson
            assert "intensity" in lesson
            assert "query" in lesson
            assert "response" in lesson
            assert "context" in lesson


class TestSeedDatabase:
    def test_seed_promotes_active_lessons(self, tmp_path, monkeypatch, set_data_dir):
        sample = textwrap.dedent("""\
        ## Testing

        - Always use renderWithProviders instead of plain render in tests

        ## Git

        - Never force push to main or release branches
        """)
        (tmp_path / "CLAUDE.md").write_text(sample)
        monkeypatch.chdir(tmp_path)
        monkeypatch.setattr(
            "smartassist.hooks.seed_from_claudemd.subprocess.run",
            lambda *args, **kwargs: SimpleNamespace(stdout='{"status":"vectorized"}', stderr=""),
        )

        seed_database()

        lessons = list_lessons(set_data_dir / "data")
        lesson_texts = {lesson["lesson"] for lesson in lessons}
        assert "Always use renderWithProviders instead of plain render in tests" in lesson_texts
        assert "Never force push to main or release branches" in lesson_texts
