"""Tests for LLM-powered deep seed and complexity analysis."""

import json
import os
import time

import pytest

from smartassist.tools.deep_seed import analyze_complexity, gather_code_structure
from smartassist.tools.llm_seed import (
    _parse_lesson_lines,
    detect_available_llm,
    store_lessons,
    SUPPORTED_LLMS,
)
from smartassist.store import list_lessons, load_lesson_scores_dict


class TestParseLessonLines:
    """Test JSON lesson parsing from LLM output."""

    def test_parses_clean_json_lines(self):
        text = '{"lesson": "Always use yarn", "category": "code_edit", "sentiment": "negative", "intensity": 3}\n{"lesson": "Run tests before push", "category": "testing", "sentiment": "positive", "intensity": 4}'
        result = _parse_lesson_lines(text)
        assert len(result) == 2
        assert result[0]["lesson"] == "Always use yarn"
        assert result[1]["category"] == "testing"

    def test_handles_markdown_code_fences(self):
        text = '```json\n{"lesson": "Use theme colors always", "category": "code_edit"}\n```'
        result = _parse_lesson_lines(text)
        assert len(result) == 1
        assert result[0]["lesson"] == "Use theme colors always"

    def test_skips_non_json_lines(self):
        text = 'Here are the lessons:\n{"lesson": "Use yarn", "category": "git"}\nThat was lesson 1.\n{"lesson": "Run lint", "category": "code_edit"}'
        result = _parse_lesson_lines(text)
        assert len(result) == 2

    def test_extracts_json_from_noisy_lines(self):
        text = '1. {"lesson": "Always test", "category": "testing"}'
        result = _parse_lesson_lines(text)
        assert len(result) == 1

    def test_rejects_incomplete_json(self):
        text = '{"lesson": "Missing category"}\n{"category": "missing lesson"}\n{"lesson": "Valid", "category": "git"}'
        result = _parse_lesson_lines(text)
        assert len(result) == 1
        assert result[0]["lesson"] == "Valid"

    def test_empty_input(self):
        assert _parse_lesson_lines("") == []
        assert _parse_lesson_lines("\n\n\n") == []

    def test_handles_mixed_code_blocks(self):
        text = """Some explanation here.

```json
{"lesson": "Use semantic tokens from theme for all colors", "category": "code_edit", "sentiment": "negative", "intensity": 4}
{"lesson": "Run yarn check:app before creating PRs", "category": "pr_review", "sentiment": "positive", "intensity": 3}
```

And some more text after."""
        result = _parse_lesson_lines(text)
        assert len(result) == 2


class TestDetectAvailableLLM:
    """Test auto-detection of LLM backends."""

    def test_detects_something(self):
        """Should detect at least one backend on a dev machine."""
        result = detect_available_llm()
        # On a machine with Claude Code installed, should detect 'claude'
        assert result is None or result in SUPPORTED_LLMS

    def test_supported_llms_list(self):
        assert "claude" in SUPPORTED_LLMS
        assert "codex" in SUPPORTED_LLMS
        assert "anthropic" in SUPPORTED_LLMS
        assert "openai" in SUPPORTED_LLMS
        assert "ollama" in SUPPORTED_LLMS
        assert "custom" in SUPPORTED_LLMS


class TestStoreLessons:
    """Test storing parsed lessons into SmartAssist DB."""

    def test_stores_valid_lessons(self, set_data_dir):
        storage = set_data_dir / "data"
        lessons = [
            {"lesson": "Always use yarn instead of npm for package management in this project", "category": "code_edit", "sentiment": "negative", "intensity": 3},
            {"lesson": "Run yarn check:app before creating pull requests to catch lint and type errors", "category": "pr_review", "sentiment": "positive", "intensity": 4},
        ]
        stored, failed = store_lessons(lessons, ".")
        assert stored == 2
        assert failed == 0

        db_lessons = list_lessons(storage)
        assert len(db_lessons) >= 2

    def test_rejects_short_lessons(self, set_data_dir):
        lessons = [
            {"lesson": "Use yarn", "category": "code_edit"},  # too short (<30 chars)
            {"lesson": "Always run the full test suite with coverage before pushing changes to the remote", "category": "testing", "sentiment": "negative", "intensity": 4},
        ]
        stored, failed = store_lessons(lessons, ".")
        assert stored == 1
        assert failed == 1

    def test_initializes_lesson_scores(self, set_data_dir):
        storage = set_data_dir / "data"
        lessons = [
            {"lesson": "Use renderWithProviders wrapper from test-utils instead of plain render in all test files", "category": "testing", "sentiment": "negative", "intensity": 4},
        ]
        stored, _ = store_lessons(lessons, ".")
        assert stored == 1

        scores = load_lesson_scores_dict(storage)
        assert len(scores) >= 1
        for lid, score in scores.items():
            assert score["boost"] == 1.0

    def test_updates_thompson_on_store(self, set_data_dir):
        from smartassist.thompson_sampling import ThompsonSamplingModel
        storage = set_data_dir / "data"

        lessons = [
            {"lesson": "Never commit console.log statements to production code in this repository", "category": "code_edit", "sentiment": "negative", "intensity": 4},
        ]
        store_lessons(lessons, ".")

        thompson = ThompsonSamplingModel(str(storage))
        reliability = thompson.get_reliability("code_edit")
        # Should have recorded a failure (negative sentiment)
        assert reliability < 0.5  # below neutral


class TestComplexityAnalysis:
    """Test dynamic lesson count calculation."""

    def test_small_project(self, set_data_dir, tmp_path):
        """Small project should recommend fewer lessons."""
        structure = {"technologies": ["Python"], "configs": {"pyproject.toml": "..."}, "structure": ".\n./src", "test_files": ""}
        git = {"log": "abc|fix|dev|2025-01-01\ndef|add|dev|2025-01-02"}
        prs = {"available": False, "comments": []}
        patterns = {}

        # Create a minimal git repo for the analysis
        os.makedirs(tmp_path / ".git", exist_ok=True)
        result = analyze_complexity(str(tmp_path), structure, git, prs, patterns)

        assert result["total_recommended"] > 15
        assert result["total_recommended"] < 40

    def test_large_project_gets_more_lessons(self, set_data_dir, tmp_path):
        """Large project should recommend more lessons."""
        structure = {
            "technologies": ["Node.js", "TypeScript", "Ruby"],
            "configs": {"package.json": "...", "tsconfig.json": "...", ".eslintrc.json": "...", "jest.config.js": "...", "CLAUDE.md": "..."},
            "structure": "\n".join([f"./dir{i}" for i in range(30)]),
            "test_files": "\n".join([f"./src/test{i}.test.ts" for i in range(50)]),
        }
        git = {"log": "\n".join([f"abc{i}|commit {i}|dev|2025-01-01" for i in range(300)])}
        prs = {"available": True, "comments": [{"pr": i, "title": f"PR {i}", "comment": f"Comment {i}"} for i in range(20)]}
        patterns = {"ci_config": "name: CI\non: push", "test_utils": "./src/test-utils.ts\nexport function render..."}

        os.makedirs(tmp_path / ".git", exist_ok=True)
        result = analyze_complexity(str(tmp_path), structure, git, prs, patterns)

        assert result["total_recommended"] > 35
        assert result["recommendations"]["testing"] > result["recommendations"]["git"]
