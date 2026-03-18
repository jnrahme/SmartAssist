"""Tests for V2 LLM-as-Judge Feedback System — Claude as Lesson Curator.

Covers:
  - V2 feedback signal detection (tuple returns + prefix matching)
  - Rich feedback context building
  - Reconstructing injected lessons
  - boost_lesson, demote_lesson, merge_lessons MCP tools
  - create_lesson dual-path write
  - add_to_curated / remove_from_curated helpers
  - Auto-retire lifecycle
  - Feedback metrics tracking
  - Feedback log rotation
  - prompt_inject main() V2 flow
  - Live log feedback V2
"""

import json
import time
from pathlib import Path
from unittest.mock import patch, MagicMock
from datetime import datetime

import pytest

from smartassist.hooks.prompt_inject import (
    detect_feedback_signal,
    build_rich_feedback_context,
    write_to_live_log_feedback,
    _reconstruct_injected_lessons,
    FEEDBACK_SIGNALS,
    MAX_INJECTION_AGE,
)
from smartassist.mcp_server import (
    create_lesson,
    compare_lesson,
    boost_lesson,
    demote_lesson,
    merge_lessons,
    _update_feedback_metrics,
    VALID_CATEGORIES,
)
from smartassist.lesson_feedback import (
    load_lesson_scores,
    save_lesson_scores,
    get_or_create_score,
    add_to_curated,
    remove_from_curated,
    reinforce_recent_lessons,
    create_lesson_from_feedback,
    log_comparison_entry,
    _context_to_lesson,
    save_last_injection,
    DEFAULT_BOOST,
    BOOST_INCREMENT,
    DEMOTE_DECREMENT,
    BOOST_CAP,
    BOOST_FLOOR,
    MAX_CURATED_LESSONS,
    ACTION_VERBS,
    GENERIC_STARTS,
)


# ── TestFeedbackSignalDetectionV2 ──────────────────────────────────────────


class TestFeedbackSignalDetectionV2:
    """Test V2 signal detection with tuple returns and prefix matching."""

    def test_smiley_positive(self):
        assert detect_feedback_signal(":)") == ("positive", "")

    def test_smiley_negative(self):
        assert detect_feedback_signal(":(") == ("negative", "")

    def test_smiley_with_nose_positive(self):
        assert detect_feedback_signal(":-)") == ("positive", "")

    def test_smiley_with_nose_negative(self):
        assert detect_feedback_signal(":-(") == ("negative", "")

    def test_thumbs_up_underscore(self):
        assert detect_feedback_signal("thumbs_up") == ("positive", "")

    def test_thumbs_up_space(self):
        assert detect_feedback_signal("thumbs up") == ("positive", "")

    def test_thumbs_down_underscore(self):
        assert detect_feedback_signal("thumbs_down") == ("negative", "")

    def test_thumbs_down_space(self):
        assert detect_feedback_signal("thumbs down") == ("negative", "")

    def test_case_insensitive(self):
        assert detect_feedback_signal("Thumbs_Up") == ("positive", "")
        assert detect_feedback_signal("THUMBS DOWN") == ("negative", "")

    def test_whitespace_handling(self):
        assert detect_feedback_signal("  :)  ") == ("positive", "")
        assert detect_feedback_signal("\t:(\n") == ("negative", "")

    def test_rejects_normal_message(self):
        assert detect_feedback_signal("fix the bug in the login flow") == (None, None)

    def test_rejects_empty(self):
        assert detect_feedback_signal("") == (None, None)

    def test_rejects_only_whitespace(self):
        assert detect_feedback_signal("   ") == (None, None)

    def test_rejects_partial_signal(self):
        assert detect_feedback_signal(":") == (None, None)
        assert detect_feedback_signal("thumbs") == (None, None)

    def test_all_signals_covered(self):
        """Every entry in FEEDBACK_SIGNALS should be detected."""
        for signal, expected in FEEDBACK_SIGNALS.items():
            sentiment, context = detect_feedback_signal(signal)
            assert sentiment == expected, f"Failed for signal: {signal}"
            assert context == ""

    # V2: Prefix matching — signal + context
    def test_smiley_with_context(self):
        sentiment, ctx = detect_feedback_signal(":) good use of theme colors")
        assert sentiment == "positive"
        assert "good use of theme colors" in ctx

    def test_frown_with_context(self):
        sentiment, ctx = detect_feedback_signal(":( dont do this to the theme")
        assert sentiment == "negative"
        assert "dont do this to the theme" in ctx

    def test_thumbs_down_with_context(self):
        sentiment, ctx = detect_feedback_signal("thumbs_down bad approach to testing")
        assert sentiment == "negative"
        assert "bad approach to testing" in ctx

    def test_context_preserves_original_case(self):
        sentiment, ctx = detect_feedback_signal(":) Great Use of Semantic Colors")
        assert sentiment == "positive"
        assert ctx == "Great Use of Semantic Colors"

    def test_smiley_in_long_message_no_prefix_match(self):
        """Embedded smileys (not at start) should not match."""
        assert detect_feedback_signal("looks great :) now fix") == (None, None)

    # V2.1: Expanded signal variants
    def test_thumbs_hyphen_positive(self):
        assert detect_feedback_signal("thumbs-up") == ("positive", "")

    def test_thumbs_hyphen_negative(self):
        assert detect_feedback_signal("thumbs-down") == ("negative", "")

    def test_thumb_singular_hyphen_positive(self):
        assert detect_feedback_signal("thumb-up") == ("positive", "")

    def test_thumb_singular_hyphen_negative(self):
        assert detect_feedback_signal("thumb-down") == ("negative", "")

    def test_thumb_singular_space_positive(self):
        assert detect_feedback_signal("thumb up") == ("positive", "")

    def test_thumb_singular_space_negative(self):
        assert detect_feedback_signal("thumb down") == ("negative", "")

    def test_thumb_singular_underscore_positive(self):
        assert detect_feedback_signal("thumb_up") == ("positive", "")

    def test_thumb_singular_underscore_negative(self):
        assert detect_feedback_signal("thumb_down") == ("negative", "")

    def test_emoji_thumbs_up(self):
        assert detect_feedback_signal("👍") == ("positive", "")

    def test_emoji_thumbs_down(self):
        assert detect_feedback_signal("👎") == ("negative", "")

    def test_plus_one(self):
        assert detect_feedback_signal("+1") == ("positive", "")

    def test_minus_one(self):
        assert detect_feedback_signal("-1") == ("negative", "")

    def test_thumb_hyphen_with_context(self):
        sentiment, ctx = detect_feedback_signal("thumbs-up great work on colors")
        assert sentiment == "positive"
        assert "great work on colors" in ctx

    def test_minus_one_boundary(self):
        """-100 should NOT match -1 (word boundary check)."""
        assert detect_feedback_signal("-100") == (None, None)

    def test_plus_one_boundary(self):
        """+100 should NOT match +1 (word boundary check)."""
        assert detect_feedback_signal("+100") == (None, None)


# ── TestReconstructInjectedLessons ──────────────────────────────────────────


class TestReconstructInjectedLessons:
    """Test reconstruction of injected lessons for feedback decisions."""

    def test_empty_when_no_injection(self, set_data_dir):
        storage = set_data_dir / "data"
        result = _reconstruct_injected_lessons(storage)
        assert result == []

    def test_empty_when_stale_injection(self, set_data_dir):
        storage = set_data_dir / "data"
        results = [{"id": "L001"}]
        save_last_injection(results)
        # Backdate
        path = storage / "last_injection.json"
        data = json.loads(path.read_text())
        data["_timestamp"] = time.time() - (MAX_INJECTION_AGE + 100)
        path.write_text(json.dumps(data))

        result = _reconstruct_injected_lessons(storage)
        assert result == []

    def test_reconstructs_with_curated_data(self, set_data_dir):
        storage = set_data_dir / "data"
        curated = [
            {"id": "L001", "lesson": "Use semantic colors", "category": "code_edit"},
            {"id": "L002", "lesson": "Mock HTTP boundary", "category": "testing"},
        ]
        (storage / "curated_lessons.json").write_text(json.dumps(curated))
        save_last_injection([{"id": "L001"}, {"id": "L002"}])

        result = _reconstruct_injected_lessons(storage)
        assert len(result) == 2
        assert result[0]["id"] == "L001"
        assert result[0]["category"] == "code_edit"
        assert result[0]["lesson"] == "Use semantic colors"

    def test_includes_scores(self, set_data_dir):
        storage = set_data_dir / "data"
        curated = [{"id": "L010", "lesson": "test lesson", "category": "testing"}]
        (storage / "curated_lessons.json").write_text(json.dumps(curated))
        scores = {"L010": {"boost": 1.6, "ups": 3, "downs": 1, "blocked": False}}
        save_lesson_scores(scores)
        save_last_injection([{"id": "L010"}])

        result = _reconstruct_injected_lessons(storage)
        assert len(result) == 1
        assert result[0]["boost"] == 1.6
        assert result[0]["ups"] == 3
        assert result[0]["downs"] == 1

    def test_confidence_laplace_smoothing(self, set_data_dir):
        storage = set_data_dir / "data"
        curated = [{"id": "L020", "lesson": "test", "category": "testing"}]
        (storage / "curated_lessons.json").write_text(json.dumps(curated))
        scores = {"L020": {"boost": 1.0, "ups": 5, "downs": 3, "blocked": False}}
        save_lesson_scores(scores)
        save_last_injection([{"id": "L020"}])

        result = _reconstruct_injected_lessons(storage)
        # confidence = 5 / (5 + 3 + 2) = 0.5
        assert abs(result[0]["confidence"] - 0.5) < 0.01

    def test_missing_curated_file(self, set_data_dir):
        storage = set_data_dir / "data"
        save_last_injection([{"id": "L001"}])
        result = _reconstruct_injected_lessons(storage)
        assert len(result) == 1
        assert "not found" in result[0]["lesson"]

    def test_missing_lesson_in_curated(self, set_data_dir):
        storage = set_data_dir / "data"
        curated = [{"id": "L099", "lesson": "other lesson", "category": "git"}]
        (storage / "curated_lessons.json").write_text(json.dumps(curated))
        save_last_injection([{"id": "L001"}])

        result = _reconstruct_injected_lessons(storage)
        assert len(result) == 1
        assert result[0]["category"] == "unknown"

    def test_recent_injection_is_reconstructed(self, set_data_dir):
        storage = set_data_dir / "data"
        curated = [{"id": "L001", "lesson": "test", "category": "testing"}]
        (storage / "curated_lessons.json").write_text(json.dumps(curated))
        save_last_injection([{"id": "L001"}])
        # Backdate to 10 seconds ago (within threshold)
        path = storage / "last_injection.json"
        data = json.loads(path.read_text())
        data["_timestamp"] = time.time() - 10
        path.write_text(json.dumps(data))

        result = _reconstruct_injected_lessons(storage)
        assert len(result) == 1


# ── TestReinforceRecentLessons ─────────────────────────────────────────────


class TestReinforceRecentLessons:
    """Test hook-level auto-reinforcement of recently injected lessons."""

    def test_boosts_all_recent_positive(self, set_data_dir):
        """All recently injected lessons get +0.3 on positive signal."""
        save_last_injection([{"id": "L001"}, {"id": "L002"}, {"id": "L003"}])
        results = reinforce_recent_lessons("positive")
        assert len(results) == 3
        for lid, old_b, new_b, retired in results:
            assert old_b == DEFAULT_BOOST
            assert new_b == DEFAULT_BOOST + BOOST_INCREMENT
            assert retired is False
        scores = load_lesson_scores()
        assert scores["L001"]["ups"] == 1
        assert scores["L002"]["ups"] == 1
        assert scores["L003"]["ups"] == 1

    def test_demotes_all_recent_negative(self, set_data_dir):
        """All recently injected lessons get -0.4 on negative signal."""
        save_last_injection([{"id": "L001"}, {"id": "L002"}])
        results = reinforce_recent_lessons("negative")
        assert len(results) == 2
        for lid, old_b, new_b, retired in results:
            assert old_b == DEFAULT_BOOST
            assert new_b == max(DEFAULT_BOOST - DEMOTE_DECREMENT, BOOST_FLOOR)
        scores = load_lesson_scores()
        assert scores["L001"]["downs"] == 1

    def test_skips_stale_injection(self, set_data_dir):
        """Returns [] if injection is older than max_age."""
        storage = set_data_dir / "data"
        save_last_injection([{"id": "L001"}])
        # Backdate timestamp
        path = storage / "last_injection.json"
        data = json.loads(path.read_text())
        data["_timestamp"] = time.time() - 1000
        path.write_text(json.dumps(data))

        results = reinforce_recent_lessons("positive", max_age=900)
        assert results == []

    def test_skips_blocked_lessons(self, set_data_dir):
        """Blocked/retired lessons are untouched."""
        save_last_injection([{"id": "L001"}, {"id": "L002"}])
        scores = {
            "L001": {"boost": 0.0, "ups": 0, "downs": 5, "blocked": True,
                     "retired": True, "retired_reason": "test", "retired_at": None},
        }
        save_lesson_scores(scores)
        results = reinforce_recent_lessons("positive")
        # Only L002 should be reinforced
        assert len(results) == 1
        assert results[0][0] == "L002"

    def test_auto_retires_on_demote(self, set_data_dir):
        """Lesson at boost=0.3 with 0 ups gets retired on demote."""
        storage = set_data_dir / "data"
        curated = [
            {"id": "L001", "lesson": "bad lesson", "category": "testing"},
            {"id": "L002", "lesson": "good lesson", "category": "code_edit"},
        ]
        (storage / "curated_lessons.json").write_text(json.dumps(curated))
        save_last_injection([{"id": "L001"}, {"id": "L002"}])
        scores = {
            "L001": {"boost": 0.3, "ups": 0, "downs": 2, "blocked": False,
                     "retired": False, "retired_reason": "", "retired_at": None},
        }
        save_lesson_scores(scores)

        results = reinforce_recent_lessons("negative")
        # L001 should be retired, L002 just demoted
        l001_result = [r for r in results if r[0] == "L001"][0]
        assert l001_result[3] is True  # retired
        assert l001_result[2] == BOOST_FLOOR

        l002_result = [r for r in results if r[0] == "L002"][0]
        assert l002_result[3] is False  # not retired

        # Verify curated was updated
        curated_after = json.loads((storage / "curated_lessons.json").read_text())
        ids = [l["id"] for l in curated_after]
        assert "L001" not in ids
        assert "L002" in ids

    @patch("smartassist.lesson_feedback.spawn_managed")
    def test_auto_retire_triggers_full_revectorization(self, mock_spawn, set_data_dir):
        storage = set_data_dir / "data"
        (storage / "curated_lessons.json").write_text(json.dumps([
            {"id": "L001", "lesson": "bad lesson", "category": "testing"},
        ]))
        save_last_injection([{"id": "L001"}])
        save_lesson_scores({
            "L001": {
                "boost": 0.3,
                "ups": 0,
                "downs": 2,
                "blocked": False,
                "retired": False,
                "retired_reason": "",
                "retired_at": None,
            }
        })

        reinforce_recent_lessons("negative")

        mock_spawn.assert_called_once()
        cmd = mock_spawn.call_args.args[0]
        assert cmd[-1] == "smartassist.tools.cleanup_and_vectorize"

    def test_returns_empty_no_injection(self, set_data_dir):
        """No last_injection.json → []."""
        results = reinforce_recent_lessons("positive")
        assert results == []

    def test_boost_capped(self, set_data_dir):
        """Boost cannot exceed BOOST_CAP."""
        save_last_injection([{"id": "L001"}])
        scores = {"L001": {"boost": BOOST_CAP - 0.1, "ups": 9, "downs": 0, "blocked": False,
                           "retired": False, "retired_reason": "", "retired_at": None}}
        save_lesson_scores(scores)

        results = reinforce_recent_lessons("positive")
        assert results[0][2] <= BOOST_CAP

    def test_demote_floored(self, set_data_dir):
        """Boost cannot go below BOOST_FLOOR."""
        save_last_injection([{"id": "L001"}])
        scores = {"L001": {"boost": 0.1, "ups": 3, "downs": 5, "blocked": False,
                           "retired": False, "retired_reason": "", "retired_at": None}}
        save_lesson_scores(scores)

        results = reinforce_recent_lessons("negative")
        assert results[0][2] >= BOOST_FLOOR


# ── TestContextToLesson ────────────────────────────────────────────────────


class TestContextToLesson:
    """Test converting user feedback context to imperative lesson text."""

    def test_strips_good_use_of_prefix(self):
        result = _context_to_lesson("good use of pre-commit checks and behavior testing")
        assert result == "Use pre-commit checks and behavior testing"

    def test_strips_great_use_of(self):
        result = _context_to_lesson("great use of semantic theme colors in styling components")
        assert result == "Use semantic theme colors in styling components"

    def test_strips_i_like_how_you(self):
        # "used" (past tense) is not in ACTION_VERBS → None
        result = _context_to_lesson("i like how you used toBeVisible for testing")
        assert result is None

    def test_preserves_already_imperative(self):
        assert _context_to_lesson("always use semantic colors from theme tokens") == \
            "Always use semantic colors from theme tokens"

    def test_returns_none_for_too_short(self):
        assert _context_to_lesson("good job") is None
        assert _context_to_lesson("nice") is None
        assert _context_to_lesson("good use of theme colors") is None  # 16 chars after transform

    def test_capitalizes_first_letter(self):
        result = _context_to_lesson("dont hardcode colors in any component styling")
        assert result is not None
        assert result[0].isupper()

    def test_preserves_long_context_with_verb(self):
        ctx = "always check pre-commit hooks and behavior testing with toBeVisible assertions"
        result = _context_to_lesson(ctx)
        assert result == "Always check pre-commit hooks and behavior testing with toBeVisible assertions"

    def test_rejects_long_context_without_verb(self):
        ctx = "pre-commit checks and behavior testing with toBeVisible assertions"
        result = _context_to_lesson(ctx)
        assert result is None  # no action verb

    def test_normalizes_contractions(self):
        result = _context_to_lesson("dont hardcode colors in component styles ever")
        assert result == "Don't hardcode colors in component styles ever"

    def test_rejects_non_imperative(self):
        result = _context_to_lesson("nice error handling approach with the try-catch blocks")
        assert result is None  # no action verb after stripping "nice "

    def test_sanitizes_conversational_prefix(self):
        result = _context_to_lesson("i think we should always use semantic colors from the theme")
        assert result == "Always use semantic colors from the theme"

    def test_rejects_generic_start(self):
        result = _context_to_lesson("good job on the implementation work here")
        assert result is None  # too short after stripping, or no verb

    def test_normalizes_cant_contraction(self):
        result = _context_to_lesson("cant use hardcoded values in the component styles here")
        assert result == "Can't use hardcoded values in the component styles here"

    def test_strips_excellent_use_of(self):
        result = _context_to_lesson("excellent use of design tokens instead of hardcoded hex values")
        assert result == "Use design tokens instead of hardcoded hex values"


# ── TestCreateLessonFromFeedback ──────────────────────────────────────────


class TestCreateLessonFromFeedback:
    """Test hook-level lesson creation from user feedback context."""

    def test_creates_lesson_with_sufficient_context(self, set_data_dir):
        storage = set_data_dir / "data"
        curated = [{"id": "L001", "lesson": "test", "category": "testing"}]
        (storage / "curated_lessons.json").write_text(json.dumps(curated))

        results = [("L001", 1.0, 1.3, False)]
        new_id, lesson_text = create_lesson_from_feedback(
            "always use pre-commit checks and behavior testing", "positive", results,
        )
        assert new_id is not None
        assert lesson_text is not None

        curated_after = json.loads((storage / "curated_lessons.json").read_text())
        assert len(curated_after) == 2
        assert curated_after[-1]["id"] == new_id

    def test_returns_none_for_vague_context(self, set_data_dir):
        new_id, lesson_text = create_lesson_from_feedback("good job", "positive", [])
        assert new_id is None
        assert lesson_text is None

    def test_infers_category_from_boosted_lessons(self, set_data_dir):
        storage = set_data_dir / "data"
        curated = [
            {"id": "L001", "lesson": "t1", "category": "testing"},
            {"id": "L002", "lesson": "t2", "category": "testing"},
            {"id": "L003", "lesson": "t3", "category": "code_edit"},
        ]
        (storage / "curated_lessons.json").write_text(json.dumps(curated))

        results = [("L001", 1.0, 1.3, False), ("L002", 1.0, 1.3, False), ("L003", 1.0, 1.3, False)]
        new_id, _ = create_lesson_from_feedback(
            "always use toBeVisible assertions for behavior testing", "positive", results,
        )
        curated_after = json.loads((storage / "curated_lessons.json").read_text())
        new_lesson = [l for l in curated_after if l["id"] == new_id][0]
        assert new_lesson["category"] == "testing"  # majority vote

    def test_writes_to_feedback_log(self, set_data_dir):
        storage = set_data_dir / "data"
        results = []
        create_lesson_from_feedback(
            "always use semantic colors from the design system", "positive", results,
        )
        feedback_log = storage / "feedback_log.jsonl"
        assert feedback_log.exists()
        entry = json.loads(feedback_log.read_text().strip())
        assert "hook-created" in entry["context"]

    def test_defaults_category_when_no_boosted(self, set_data_dir):
        storage = set_data_dir / "data"
        new_id, _ = create_lesson_from_feedback(
            "always use semantic colors from the design system", "positive", [],
        )
        curated = json.loads((storage / "curated_lessons.json").read_text())
        assert curated[0]["category"] == "code_edit"  # default


# ── TestComparisonLogging ────────────────────────────────────────────────────


class TestComparisonLogging:
    """Test the log_comparison_entry helper."""

    def test_log_comparison_entry_hook(self, set_data_dir):
        storage = set_data_dir / "data"
        log_comparison_entry(
            storage, "hook", "positive", "good use of theme colors",
            "Use theme colors", True,
        )
        log_path = storage / "lesson_comparison.jsonl"
        assert log_path.exists()
        entry = json.loads(log_path.read_text().strip())
        assert entry["source"] == "hook"
        assert entry["sentiment"] == "positive"
        assert entry["feedback_context"] == "good use of theme colors"
        assert entry["lesson_text"] == "Use theme colors"
        assert entry["passed_gates"] is True
        assert "timestamp" in entry

    def test_log_comparison_entry_claude(self, set_data_dir):
        storage = set_data_dir / "data"
        log_comparison_entry(
            storage, "claude", "negative", "dont hardcode colors",
            "Don't hardcode colors in components", True,
        )
        entry = json.loads((storage / "lesson_comparison.jsonl").read_text().strip())
        assert entry["source"] == "claude"
        assert entry["sentiment"] == "negative"

    def test_entries_pair_by_context(self, set_data_dir):
        storage = set_data_dir / "data"
        ctx = "good use of semantic colors instead of hex values"
        log_comparison_entry(storage, "hook", "positive", ctx, "Use semantic colors", True)
        log_comparison_entry(storage, "claude", "positive", ctx, "Use semantic color tokens from the theme", True)
        lines = (storage / "lesson_comparison.jsonl").read_text().strip().split("\n")
        entries = [json.loads(l) for l in lines]
        assert len(entries) == 2
        assert entries[0]["feedback_context"] == entries[1]["feedback_context"]
        assert entries[0]["source"] == "hook"
        assert entries[1]["source"] == "claude"

    def test_log_comparison_entry_failed_gates(self, set_data_dir):
        storage = set_data_dir / "data"
        log_comparison_entry(storage, "hook", "positive", "good job", None, False)
        entry = json.loads((storage / "lesson_comparison.jsonl").read_text().strip())
        assert entry["lesson_text"] is None
        assert entry["passed_gates"] is False


# ── TestCompareLessonTool ────────────────────────────────────────────────────


class TestCompareLessonTool:
    """Test the compare_lesson MCP tool — logs but does NOT store."""

    VALID_LESSON = "Always use semantic colors from theme instead of hardcoded hex values"

    def test_logs_to_comparison_file(self, set_data_dir):
        storage = set_data_dir / "data"
        result = compare_lesson(
            lesson=self.VALID_LESSON,
            category="code_edit",
            sentiment="positive",
            context="good use of theme colors instead of hex values",
        )
        assert "Comparison logged" in result
        log_path = storage / "lesson_comparison.jsonl"
        assert log_path.exists()
        entry = json.loads(log_path.read_text().strip())
        assert entry["source"] == "claude"
        assert entry["passed_gates"] is True
        assert entry["lesson_text"] == self.VALID_LESSON

    def test_does_not_store_to_curated(self, set_data_dir):
        storage = set_data_dir / "data"
        compare_lesson(
            lesson=self.VALID_LESSON,
            category="code_edit",
            context="test context for comparison",
        )
        curated_path = storage / "curated_lessons.json"
        assert not curated_path.exists()

    def test_applies_quality_gates_short(self, set_data_dir):
        result = compare_lesson(lesson="Too short", category="code_edit")
        assert "too short" in result.lower()

    def test_applies_quality_gates_generic_start(self, set_data_dir):
        result = compare_lesson(
            lesson="Good job using semantic colors from the theme tokens for styling",
            category="code_edit",
        )
        assert "generic" in result.lower()

    def test_applies_quality_gates_no_verb(self, set_data_dir):
        result = compare_lesson(
            lesson="Semantic colors are better than hardcoded hex values in all cases",
            category="code_edit",
        )
        assert "action verb" in result.lower()

    def test_applies_quality_gates_invalid_category(self, set_data_dir):
        result = compare_lesson(
            lesson=self.VALID_LESSON,
            category="invalid_cat",
        )
        assert "invalid category" in result.lower()

    def test_returns_confirmation(self, set_data_dir):
        result = compare_lesson(
            lesson=self.VALID_LESSON,
            category="code_edit",
        )
        assert "Comparison logged" in result
        assert "not stored" in result.lower()

    def test_rejects_invalid_sentiment(self, set_data_dir):
        result = compare_lesson(
            lesson=self.VALID_LESSON,
            category="code_edit",
            sentiment="neutral",
        )
        assert "invalid sentiment" in result.lower()

    def test_logs_failed_gate_to_comparison(self, set_data_dir):
        storage = set_data_dir / "data"
        compare_lesson(
            lesson="Too short",
            category="code_edit",
            context="some context here",
        )
        log_path = storage / "lesson_comparison.jsonl"
        assert log_path.exists()
        entry = json.loads(log_path.read_text().strip())
        assert entry["source"] == "claude"
        assert entry["passed_gates"] is False


# ── TestBuildRichFeedbackContext ────────────────────────────────────────────


class TestBuildRichFeedbackContext:
    """Test rich feedback context — two modes: with/without user context."""

    def test_with_context_instructs_compare(self, set_data_dir):
        """User context >= 15 chars → instructs Claude to call compare_lesson."""
        results = [("L001", 1.0, 1.3, False), ("L002", 1.0, 1.3, False)]
        context = build_rich_feedback_context(
            "positive", "good use of theme colors instead of hardcoded values", results,
        )
        assert "compare_lesson" in context
        assert "MUST" in context
        assert "good use of theme colors" in context
        assert "Auto-reinforced 2 lesson(s)" in context

    def test_without_context_acknowledges(self, set_data_dir):
        """No user context → acknowledge, no compare instruction."""
        results = [("L001", 1.0, 1.3, False)]
        context = build_rich_feedback_context("positive", "", results)
        assert "acknowledge" in context.lower()
        assert "compare_lesson" not in context

    def test_short_context_acknowledges(self, set_data_dir):
        """User context < 15 chars → acknowledge, no compare instruction."""
        results = [("L001", 1.0, 1.3, False)]
        context = build_rich_feedback_context("positive", "good job", results)
        assert "acknowledge" in context.lower()
        assert "compare_lesson" not in context

    def test_no_context_gives_summary(self, set_data_dir):
        """No user context → summary only."""
        results = [("L001", 1.0, 1.3, False), ("L002", 1.3, 1.6, False)]
        context = build_rich_feedback_context("positive", "", results)
        assert "Auto-reinforced 2 lesson(s)" in context
        assert "acknowledge" in context.lower()

    def test_negative_with_context_instructs_compare(self, set_data_dir):
        results = [("L001", 1.0, 0.6, False)]
        context = build_rich_feedback_context(
            "negative", "dont do this to the theme ever again", results,
        )
        assert "compare_lesson" in context
        assert "demoted" in context

    def test_empty_results_no_context(self, set_data_dir):
        context = build_rich_feedback_context("positive", "", [])
        assert "0 lesson(s)" in context
        assert "acknowledge" in context.lower()

    def test_empty_results_with_long_context(self, set_data_dir):
        context = build_rich_feedback_context(
            "positive", "good use of semantic colors instead of hex", [],
        )
        assert "compare_lesson" in context
        assert "0 lesson(s)" in context

    def test_shows_retired_in_summary(self, set_data_dir):
        results = [("L001", 0.3, 0.0, True)]
        context = build_rich_feedback_context("negative", "", results)
        assert "RETIRED" in context

    def test_includes_per_lesson_boost_values(self, set_data_dir):
        results = [("L022", 1.0, 1.3, False), ("L020", 1.3, 1.6, False)]
        context = build_rich_feedback_context("positive", "", results)
        assert "1.0→1.3x" in context
        assert "1.3→1.6x" in context


# ── TestWriteToLiveLogFeedback ───────────────────────────────────────────


class TestWriteToLiveLogFeedback:
    """Test V2 feedback logging to rag_live.log."""

    def test_writes_to_log(self, set_data_dir):
        storage = set_data_dir / "data"
        write_to_live_log_feedback(storage, ":)", "positive")
        log_file = storage / "rag_live.log"
        assert log_file.exists()
        content = log_file.read_text()
        assert "FEEDBACK DETECTED" in content
        assert ":)" in content

    def test_positive_sentiment_color(self, set_data_dir):
        storage = set_data_dir / "data"
        write_to_live_log_feedback(storage, ":)", "positive")
        content = (storage / "rag_live.log").read_text()
        assert "\033[32m" in content

    def test_negative_sentiment_color(self, set_data_dir):
        storage = set_data_dir / "data"
        write_to_live_log_feedback(storage, ":(", "negative")
        content = (storage / "rag_live.log").read_text()
        assert "\033[31m" in content

    def test_increments_prompt_counter(self, set_data_dir):
        storage = set_data_dir / "data"
        write_to_live_log_feedback(storage, ":)", "positive")
        counter_file = storage / "rag_prompt_counter.json"
        assert counter_file.exists()
        data = json.loads(counter_file.read_text())
        assert data["prompt_count"] == 1

    def test_includes_user_context(self, set_data_dir):
        storage = set_data_dir / "data"
        write_to_live_log_feedback(storage, ":( bad theme", "negative", user_context="bad theme")
        content = (storage / "rag_live.log").read_text()
        assert "bad theme" in content

    def test_shows_per_lesson_results(self, set_data_dir):
        storage = set_data_dir / "data"
        results = [("L001", 1.0, 1.3, False), ("L002", 1.3, 1.6, False)]
        write_to_live_log_feedback(storage, ":)", "positive", reinforcement_results=results)
        content = (storage / "rag_live.log").read_text()
        assert "BOOST: L001" in content
        assert "BOOST: L002" in content
        assert "1.0x" in content

    def test_shows_zero_when_no_results(self, set_data_dir):
        storage = set_data_dir / "data"
        write_to_live_log_feedback(storage, ":)", "positive", reinforcement_results=[])
        content = (storage / "rag_live.log").read_text()
        assert "0 lesson(s) reinforced" in content

    def test_shows_demote_for_negative(self, set_data_dir):
        storage = set_data_dir / "data"
        results = [("L001", 1.0, 0.6, False)]
        write_to_live_log_feedback(storage, ":(", "negative", reinforcement_results=results)
        content = (storage / "rag_live.log").read_text()
        assert "DEMOTE: L001" in content


# ── TestBoostLessonTool ─────────────────────────────────────────────────


class TestBoostLessonTool:
    """Test the boost_lesson MCP tool."""

    def test_boosts_score(self, set_data_dir):
        storage = set_data_dir / "data"
        (storage / "curated_lessons.json").write_text(json.dumps([
            {"id": "L001", "lesson": "test lesson", "category": "testing"},
        ]))
        result = boost_lesson("L001")
        assert "Boosted" in result
        scores = load_lesson_scores()
        assert scores["L001"]["ups"] == 1
        assert scores["L001"]["boost"] == DEFAULT_BOOST + BOOST_INCREMENT

    def test_boost_capped_at_max(self, set_data_dir):
        storage = set_data_dir / "data"
        (storage / "curated_lessons.json").write_text(json.dumps([
            {"id": "L001", "lesson": "test lesson", "category": "testing"},
        ]))
        # Pre-set near cap
        scores = {"L001": {"boost": BOOST_CAP - 0.1, "ups": 9, "downs": 0, "blocked": False,
                           "retired": False, "retired_reason": "", "retired_at": None}}
        save_lesson_scores(scores)
        result = boost_lesson("L001")
        scores = load_lesson_scores()
        assert scores["L001"]["boost"] <= BOOST_CAP

    def test_rejects_blocked_lesson(self, set_data_dir):
        scores = {"L001": {"boost": 0.0, "ups": 0, "downs": 5, "blocked": True,
                           "retired": False, "retired_reason": "", "retired_at": None}}
        save_lesson_scores(scores)
        result = boost_lesson("L001")
        assert "blocked" in result.lower()

    def test_rejects_retired_lesson(self, set_data_dir):
        scores = {"L001": {"boost": 0.0, "ups": 0, "downs": 5, "blocked": True,
                           "retired": True, "retired_reason": "test", "retired_at": None}}
        save_lesson_scores(scores)
        result = boost_lesson("L001")
        assert "retired" in result.lower()

    def test_warns_when_not_in_curated(self, set_data_dir):
        storage = set_data_dir / "data"
        (storage / "curated_lessons.json").write_text("[]")
        result = boost_lesson("L001")
        assert "cannot boost" in result.lower()
        assert "not found in curated" in result.lower()

    def test_updates_thompson(self, set_data_dir):
        storage = set_data_dir / "data"
        curated = [{"id": "L001", "lesson": "test lesson", "category": "testing"}]
        (storage / "curated_lessons.json").write_text(json.dumps(curated))

        with patch("smartassist.mcp_server._get_thompson") as mock_get:
            mock_thompson = MagicMock()
            mock_get.return_value = mock_thompson
            boost_lesson("L001")
            mock_thompson.record_success.assert_called_once_with("testing", 3)

    def test_updates_metrics(self, set_data_dir):
        storage = set_data_dir / "data"
        (storage / "curated_lessons.json").write_text(json.dumps([
            {"id": "L001", "lesson": "test lesson", "category": "testing"},
        ]))
        boost_lesson("L001")
        metrics_path = storage / "feedback_metrics.json"
        assert metrics_path.exists()
        metrics = json.loads(metrics_path.read_text())
        assert metrics["boosts"] == 1

    def test_case_insensitive_id(self, set_data_dir):
        storage = set_data_dir / "data"
        (storage / "curated_lessons.json").write_text(json.dumps([
            {"id": "L001", "lesson": "test lesson", "category": "testing"},
        ]))
        result = boost_lesson("l001")
        assert "L001" in result


# ── TestDemoteLessonTool ─────────────────────────────────────────────────


class TestDemoteLessonTool:
    """Test the demote_lesson MCP tool."""

    def test_demotes_score(self, set_data_dir):
        storage = set_data_dir / "data"
        (storage / "curated_lessons.json").write_text(json.dumps([
            {"id": "L001", "lesson": "test lesson", "category": "testing"},
        ]))
        result = demote_lesson("L001")
        assert "Demoted" in result
        scores = load_lesson_scores()
        assert scores["L001"]["downs"] == 1
        assert scores["L001"]["boost"] == max(DEFAULT_BOOST - DEMOTE_DECREMENT, BOOST_FLOOR)

    def test_demote_floored_at_zero(self, set_data_dir):
        storage = set_data_dir / "data"
        (storage / "curated_lessons.json").write_text(json.dumps([
            {"id": "L001", "lesson": "test lesson", "category": "testing"},
        ]))
        scores = {"L001": {"boost": 0.1, "ups": 3, "downs": 5, "blocked": False,
                           "retired": False, "retired_reason": "", "retired_at": None}}
        save_lesson_scores(scores)
        demote_lesson("L001")
        scores = load_lesson_scores()
        assert scores["L001"]["boost"] >= BOOST_FLOOR

    def test_auto_retire_zero_ups(self, set_data_dir):
        """Lesson with 0 ups that hits 0.0 boost should be auto-retired."""
        storage = set_data_dir / "data"
        curated = [{"id": "L001", "lesson": "bad lesson", "category": "testing"}]
        (storage / "curated_lessons.json").write_text(json.dumps(curated))
        # Start at low boost, 0 ups
        scores = {"L001": {"boost": 0.3, "ups": 0, "downs": 2, "blocked": False,
                           "retired": False, "retired_reason": "", "retired_at": None}}
        save_lesson_scores(scores)

        result = demote_lesson("L001")
        assert "RETIRED" in result

        scores = load_lesson_scores()
        assert scores["L001"]["blocked"] is True
        assert scores["L001"]["retired"] is True
        assert "auto-retired" in scores["L001"]["retired_reason"]

    def test_auto_retire_removes_from_curated(self, set_data_dir):
        storage = set_data_dir / "data"
        curated = [
            {"id": "L001", "lesson": "bad lesson", "category": "testing"},
            {"id": "L002", "lesson": "good lesson", "category": "code_edit"},
        ]
        (storage / "curated_lessons.json").write_text(json.dumps(curated))
        scores = {"L001": {"boost": 0.3, "ups": 0, "downs": 2, "blocked": False,
                           "retired": False, "retired_reason": "", "retired_at": None}}
        save_lesson_scores(scores)

        demote_lesson("L001")

        curated_after = json.loads((storage / "curated_lessons.json").read_text())
        ids = [l["id"] for l in curated_after]
        assert "L001" not in ids
        assert "L002" in ids

    @patch("smartassist.mcp_server.spawn_managed")
    def test_auto_retire_triggers_full_revectorization(self, mock_spawn, set_data_dir):
        storage = set_data_dir / "data"
        (storage / "curated_lessons.json").write_text(json.dumps([
            {"id": "L001", "lesson": "bad lesson", "category": "testing"},
        ]))
        scores = {"L001": {"boost": 0.3, "ups": 0, "downs": 2, "blocked": False,
                           "retired": False, "retired_reason": "", "retired_at": None}}
        save_lesson_scores(scores)

        demote_lesson("L001")

        mock_spawn.assert_called_once()
        cmd = mock_spawn.call_args.args[0]
        assert cmd[-1] == "smartassist.tools.cleanup_and_vectorize"

    def test_no_auto_retire_with_ups(self, set_data_dir):
        """Lesson with ups > 0 should NOT be auto-retired even at 0.0 boost."""
        storage = set_data_dir / "data"
        (storage / "curated_lessons.json").write_text(json.dumps([
            {"id": "L001", "lesson": "test lesson", "category": "testing"},
        ]))
        scores = {"L001": {"boost": 0.3, "ups": 3, "downs": 8, "blocked": False,
                           "retired": False, "retired_reason": "", "retired_at": None}}
        save_lesson_scores(scores)
        result = demote_lesson("L001")
        assert "RETIRED" not in result
        scores = load_lesson_scores()
        assert scores["L001"].get("retired", False) is False

    def test_warns_strong_positive_history(self, set_data_dir):
        storage = set_data_dir / "data"
        (storage / "curated_lessons.json").write_text(json.dumps([
            {"id": "L001", "lesson": "test lesson", "category": "testing"},
        ]))
        scores = {"L001": {"boost": 1.0, "ups": 5, "downs": 0, "blocked": False,
                           "retired": False, "retired_reason": "", "retired_at": None}}
        save_lesson_scores(scores)
        result = demote_lesson("L001")
        assert "Warning" in result
        assert "5 positive" in result

    def test_rejects_blocked_lesson(self, set_data_dir):
        scores = {"L001": {"boost": 0.0, "ups": 0, "downs": 5, "blocked": True,
                           "retired": False, "retired_reason": "", "retired_at": None}}
        save_lesson_scores(scores)
        result = demote_lesson("L001")
        assert "already blocked" in result.lower()

    def test_updates_thompson(self, set_data_dir):
        storage = set_data_dir / "data"
        curated = [{"id": "L001", "lesson": "test lesson", "category": "testing"}]
        (storage / "curated_lessons.json").write_text(json.dumps(curated))

        with patch("smartassist.mcp_server._get_thompson") as mock_get:
            mock_thompson = MagicMock()
            mock_get.return_value = mock_thompson
            demote_lesson("L001")
            mock_thompson.record_failure.assert_called_once_with("testing", 3)

    def test_updates_metrics(self, set_data_dir):
        storage = set_data_dir / "data"
        (storage / "curated_lessons.json").write_text(json.dumps([
            {"id": "L001", "lesson": "test lesson", "category": "testing"},
        ]))
        demote_lesson("L001")
        metrics_path = storage / "feedback_metrics.json"
        metrics = json.loads(metrics_path.read_text())
        assert metrics["demotes"] == 1

    def test_rejects_when_not_in_curated(self, set_data_dir):
        storage = set_data_dir / "data"
        (storage / "curated_lessons.json").write_text("[]")
        result = demote_lesson("L001")
        assert "cannot demote" in result.lower()
        assert "not found in curated" in result.lower()


# ── TestMergeLessonsTool ─────────────────────────────────────────────────


class TestMergeLessonsTool:
    """Test the merge_lessons MCP tool."""

    VALID_MERGED = "Always use semantic design tokens from the theme for all color values"

    def _seed_curated(self, set_data_dir, lessons):
        storage = set_data_dir / "data"
        (storage / "curated_lessons.json").write_text(json.dumps(lessons))
        return storage

    @patch("smartassist.mcp_server.spawn_managed")
    def test_merge_two_lessons(self, mock_spawn, set_data_dir):
        curated = [
            {"id": "L001", "lesson": "Use semantic colors", "category": "code_edit"},
            {"id": "L002", "lesson": "Avoid hardcoded hex values", "category": "code_edit"},
        ]
        self._seed_curated(set_data_dir, curated)

        result = merge_lessons("L001,L002", self.VALID_MERGED, "code_edit")
        assert "Merged" in result
        assert "L001" in result
        assert "L002" in result

    @patch("smartassist.mcp_server.spawn_managed")
    def test_merge_removes_sources_from_curated(self, mock_spawn, set_data_dir):
        storage = self._seed_curated(set_data_dir, [
            {"id": "L001", "lesson": "Use semantic colors", "category": "code_edit"},
            {"id": "L002", "lesson": "Avoid hardcoded hex values", "category": "code_edit"},
            {"id": "L003", "lesson": "Keep other lesson", "category": "testing"},
        ])

        merge_lessons("L001,L002", self.VALID_MERGED, "code_edit")

        curated = json.loads((storage / "curated_lessons.json").read_text())
        ids = [l["id"] for l in curated]
        assert "L001" not in ids
        assert "L002" not in ids
        assert "L003" in ids

    @patch("smartassist.mcp_server.spawn_managed")
    def test_merge_adds_new_lesson_to_curated(self, mock_spawn, set_data_dir):
        storage = self._seed_curated(set_data_dir, [
            {"id": "L001", "lesson": "Use semantic colors", "category": "code_edit"},
            {"id": "L002", "lesson": "Avoid hardcoded hex values", "category": "code_edit"},
        ])

        merge_lessons("L001,L002", self.VALID_MERGED, "code_edit")

        curated = json.loads((storage / "curated_lessons.json").read_text())
        assert any(self.VALID_MERGED in l["lesson"] for l in curated)

    @patch("smartassist.mcp_server.spawn_managed")
    def test_merge_combines_scores(self, mock_spawn, set_data_dir):
        self._seed_curated(set_data_dir, [
            {"id": "L001", "lesson": "Use semantic colors", "category": "code_edit"},
            {"id": "L002", "lesson": "Avoid hardcoded hex values", "category": "code_edit"},
        ])
        scores = {
            "L001": {"boost": 2.0, "ups": 5, "downs": 1, "blocked": False,
                     "retired": False, "retired_reason": "", "retired_at": None},
            "L002": {"boost": 1.5, "ups": 3, "downs": 0, "blocked": False,
                     "retired": False, "retired_reason": "", "retired_at": None},
        }
        save_lesson_scores(scores)

        merge_lessons("L001,L002", self.VALID_MERGED, "code_edit")

        scores = load_lesson_scores()
        # Find new lesson ID
        new_ids = [k for k in scores if k not in ("L001", "L002")]
        assert len(new_ids) == 1
        new_score = scores[new_ids[0]]
        assert new_score["ups"] == 8  # 5 + 3
        assert new_score["boost"] == 2.0  # max(2.0, 1.5)

    @patch("smartassist.mcp_server.spawn_managed")
    def test_merge_marks_sources_superseded(self, mock_spawn, set_data_dir):
        self._seed_curated(set_data_dir, [
            {"id": "L001", "lesson": "Use semantic colors", "category": "code_edit"},
            {"id": "L002", "lesson": "Avoid hardcoded hex values", "category": "code_edit"},
        ])

        merge_lessons("L001,L002", self.VALID_MERGED, "code_edit")

        scores = load_lesson_scores()
        assert scores["L001"]["blocked"] is True
        assert scores["L001"]["retired"] is True
        assert "superseded_by" in scores["L001"]["retired_reason"]
        assert scores["L002"]["blocked"] is True

    def test_merge_requires_two_ids(self, set_data_dir):
        result = merge_lessons("L001", self.VALID_MERGED, "code_edit")
        assert "at least 2" in result

    def test_merge_rejects_duplicate_ids(self, set_data_dir):
        self._seed_curated(set_data_dir, [
            {"id": "L001", "lesson": "a", "category": "code_edit"},
            {"id": "L002", "lesson": "b", "category": "code_edit"},
        ])
        result = merge_lessons("L001,L001", self.VALID_MERGED, "code_edit")
        assert "unique lesson ids" in result.lower()

    @patch("smartassist.mcp_server.spawn_managed")
    def test_merge_works_at_capacity(self, mock_spawn, set_data_dir):
        storage = set_data_dir / "data"
        lessons = [{"id": f"L{i:03d}", "lesson": f"lesson {i}", "category": "testing"}
                   for i in range(1, MAX_CURATED_LESSONS + 1)]
        (storage / "curated_lessons.json").write_text(json.dumps(lessons))
        result = merge_lessons("L001,L002", self.VALID_MERGED, "testing")
        assert "Merged L001, L002" in result
        curated = json.loads((storage / "curated_lessons.json").read_text())
        assert len(curated) == MAX_CURATED_LESSONS - 1

    def test_merge_rejects_missing_ids(self, set_data_dir):
        storage = set_data_dir / "data"
        (storage / "curated_lessons.json").write_text(json.dumps([
            {"id": "L001", "lesson": "test", "category": "testing"},
        ]))
        result = merge_lessons("L001,L999", self.VALID_MERGED, "code_edit")
        assert "L999" in result
        assert "not found" in result.lower()

    def test_merge_rejects_short_lesson(self, set_data_dir):
        self._seed_curated(set_data_dir, [
            {"id": "L001", "lesson": "a", "category": "code_edit"},
            {"id": "L002", "lesson": "b", "category": "code_edit"},
        ])
        result = merge_lessons("L001,L002", "Too short", "code_edit")
        assert "too short" in result.lower()

    def test_merge_rejects_invalid_category(self, set_data_dir):
        self._seed_curated(set_data_dir, [
            {"id": "L001", "lesson": "a", "category": "code_edit"},
            {"id": "L002", "lesson": "b", "category": "code_edit"},
        ])
        result = merge_lessons("L001,L002", self.VALID_MERGED, "invalid")
        assert "invalid category" in result.lower()

    def test_merge_rejects_generic_start(self, set_data_dir):
        self._seed_curated(set_data_dir, [
            {"id": "L001", "lesson": "a", "category": "code_edit"},
            {"id": "L002", "lesson": "b", "category": "code_edit"},
        ])
        result = merge_lessons("L001,L002", "Remember to use semantic colors in all components", "code_edit")
        assert "generic" in result.lower()

    def test_merge_rejects_no_action_verb(self, set_data_dir):
        self._seed_curated(set_data_dir, [
            {"id": "L001", "lesson": "a", "category": "code_edit"},
            {"id": "L002", "lesson": "b", "category": "code_edit"},
        ])
        result = merge_lessons("L001,L002",
                               "Semantic colors are better than hex values in all situations",
                               "code_edit")
        assert "action verb" in result.lower()

    @patch("smartassist.mcp_server.spawn_managed")
    def test_merge_writes_to_feedback_log(self, mock_spawn, set_data_dir):
        storage = self._seed_curated(set_data_dir, [
            {"id": "L001", "lesson": "Use semantic colors", "category": "code_edit"},
            {"id": "L002", "lesson": "Avoid hardcoded hex values", "category": "code_edit"},
        ])
        merge_lessons("L001,L002", self.VALID_MERGED, "code_edit")

        feedback_log = storage / "feedback_log.jsonl"
        assert feedback_log.exists()
        entry = json.loads(feedback_log.read_text().strip())
        assert entry["signal"] == "merge"
        assert "Merged from" in entry["context"]

    @patch("smartassist.mcp_server.spawn_managed")
    def test_merge_fires_vectorization(self, mock_spawn, set_data_dir):
        self._seed_curated(set_data_dir, [
            {"id": "L001", "lesson": "Use semantic colors", "category": "code_edit"},
            {"id": "L002", "lesson": "Avoid hardcoded hex values", "category": "code_edit"},
        ])
        merge_lessons("L001,L002", self.VALID_MERGED, "code_edit")
        mock_spawn.assert_called_once()
        cmd = mock_spawn.call_args.args[0]
        assert cmd[-1] == "smartassist.tools.cleanup_and_vectorize"

    @patch("smartassist.mcp_server.spawn_managed")
    def test_merge_updates_metrics(self, mock_spawn, set_data_dir):
        storage = self._seed_curated(set_data_dir, [
            {"id": "L001", "lesson": "Use semantic colors", "category": "code_edit"},
            {"id": "L002", "lesson": "Avoid hardcoded hex values", "category": "code_edit"},
        ])
        merge_lessons("L001,L002", self.VALID_MERGED, "code_edit")
        metrics = json.loads((storage / "feedback_metrics.json").read_text())
        assert metrics["merges"] == 1


# ── TestCreateLessonV2 ─────────────────────────────────────────────────


class TestCreateLessonV2:
    """Test create_lesson with V2 dual-path write."""

    VALID_LESSON = "Always use semantic colors from theme instead of hardcoded hex values"
    VALID_CATEGORY = "code_edit"

    @patch("smartassist.mcp_server.spawn_managed")
    def test_dual_path_write(self, mock_spawn, set_data_dir):
        """create_lesson should write to both feedback_log AND curated_lessons."""
        storage = set_data_dir / "data"
        result = create_lesson(
            lesson=self.VALID_LESSON,
            category=self.VALID_CATEGORY,
        )
        assert "recorded" in result.lower()
        assert "[ID:" in result or "ID:" in result

        # Check curated
        curated_path = storage / "curated_lessons.json"
        assert curated_path.exists()
        curated = json.loads(curated_path.read_text())
        assert len(curated) == 1
        assert curated[0]["lesson"] == self.VALID_LESSON

    @patch("smartassist.mcp_server.spawn_managed")
    def test_auto_generates_id(self, mock_spawn, set_data_dir):
        storage = set_data_dir / "data"
        create_lesson(lesson=self.VALID_LESSON, category=self.VALID_CATEGORY)
        curated = json.loads((storage / "curated_lessons.json").read_text())
        assert curated[0]["id"] == "L001"

    @patch("smartassist.mcp_server.spawn_managed")
    def test_increments_id(self, mock_spawn, set_data_dir):
        storage = set_data_dir / "data"
        (storage / "curated_lessons.json").write_text(json.dumps([
            {"id": "L005", "lesson": "existing", "category": "testing"},
        ]))
        create_lesson(lesson=self.VALID_LESSON, category=self.VALID_CATEGORY)
        curated = json.loads((storage / "curated_lessons.json").read_text())
        assert curated[-1]["id"] == "L006"

    @patch("smartassist.mcp_server.spawn_managed")
    def test_cap_enforcement(self, mock_spawn, set_data_dir):
        storage = set_data_dir / "data"
        # Seed with MAX_CURATED_LESSONS lessons
        lessons = [{"id": f"L{i:03d}", "lesson": f"lesson {i}", "category": "testing"}
                   for i in range(1, MAX_CURATED_LESSONS + 1)]
        (storage / "curated_lessons.json").write_text(json.dumps(lessons))

        result = create_lesson(lesson=self.VALID_LESSON, category=self.VALID_CATEGORY)
        assert "capacity" in result.lower()
        feedback_log = storage / "feedback_log.jsonl"
        assert not feedback_log.exists() or feedback_log.read_text().strip() == ""

    @patch("smartassist.mcp_server.spawn_managed")
    def test_updates_feedback_metrics(self, mock_spawn, set_data_dir):
        storage = set_data_dir / "data"
        create_lesson(lesson=self.VALID_LESSON, category=self.VALID_CATEGORY)
        metrics = json.loads((storage / "feedback_metrics.json").read_text())
        assert metrics["creates"] == 1

    # Existing quality gate tests that must still pass
    def test_rejects_short_lesson(self, set_data_dir):
        result = create_lesson(lesson="Use theme colors", category="code_edit")
        assert "too short" in result.lower()

    def test_rejects_invalid_category(self, set_data_dir):
        result = create_lesson(lesson=self.VALID_LESSON, category="invalid_cat")
        assert "invalid category" in result.lower()

    def test_rejects_invalid_sentiment(self, set_data_dir):
        result = create_lesson(lesson=self.VALID_LESSON, category="code_edit", sentiment="neutral")
        assert "invalid sentiment" in result.lower()

    def test_rejects_invalid_intensity(self, set_data_dir):
        result = create_lesson(lesson=self.VALID_LESSON, category="code_edit", intensity="high")
        assert "invalid intensity" in result.lower()

    def test_rejects_no_action_verb(self, set_data_dir):
        result = create_lesson(
            lesson="Semantic colors are better than hardcoded hex values in all cases",
            category="code_edit",
        )
        assert "action verb" in result.lower()

    def test_rejects_generic_start(self, set_data_dir):
        result = create_lesson(
            lesson="Good job using semantic colors from the theme tokens for styling",
            category="code_edit",
        )
        assert "generic" in result.lower()

    @patch("smartassist.mcp_server.spawn_managed")
    def test_stores_to_feedback_log(self, mock_spawn, set_data_dir):
        storage = set_data_dir / "data"
        create_lesson(
            lesson=self.VALID_LESSON,
            category=self.VALID_CATEGORY,
            sentiment="positive",
            intensity=3,
            context="test context",
        )
        feedback_log = storage / "feedback_log.jsonl"
        assert feedback_log.exists()
        entries = [json.loads(line) for line in feedback_log.read_text().strip().split("\n")]
        assert len(entries) == 1
        assert entries[0]["correction"] == self.VALID_LESSON

    @patch("smartassist.mcp_server.spawn_managed")
    def test_fires_vectorization(self, mock_spawn, set_data_dir):
        create_lesson(lesson=self.VALID_LESSON, category=self.VALID_CATEGORY)
        mock_spawn.assert_called_once()

    @patch("smartassist.mcp_server.spawn_managed")
    def test_updates_thompson_positive(self, mock_spawn, set_data_dir):
        with patch("smartassist.mcp_server._get_thompson") as mock_get:
            mock_thompson = MagicMock()
            mock_get.return_value = mock_thompson
            create_lesson(lesson=self.VALID_LESSON, category=self.VALID_CATEGORY, sentiment="positive", intensity=4)
            mock_thompson.record_success.assert_called_once_with("code_edit", 4)

    @patch("smartassist.mcp_server.spawn_managed")
    def test_updates_thompson_negative(self, mock_spawn, set_data_dir):
        with patch("smartassist.mcp_server._get_thompson") as mock_get:
            mock_thompson = MagicMock()
            mock_get.return_value = mock_thompson
            create_lesson(
                lesson="Never use snapshot tests, always prefer toBeVisible behavior assertions",
                category="testing", sentiment="negative", intensity=5,
            )
            mock_thompson.record_failure.assert_called_once_with("testing", 5)


# ── TestAddToCurated ──────────────────────────────────────────────────────


class TestAddToCurated:
    """Test the add_to_curated helper."""

    def test_creates_file_if_missing(self, set_data_dir):
        storage = set_data_dir / "data"
        new_id, error = add_to_curated(storage, "Test lesson text here", "testing")
        assert new_id == "L001"
        assert error is None
        assert (storage / "curated_lessons.json").exists()

    def test_appends_to_existing(self, set_data_dir):
        storage = set_data_dir / "data"
        (storage / "curated_lessons.json").write_text(json.dumps([
            {"id": "L001", "lesson": "existing", "category": "testing"},
        ]))
        new_id, error = add_to_curated(storage, "New lesson here", "code_edit")
        assert new_id == "L002"
        curated = json.loads((storage / "curated_lessons.json").read_text())
        assert len(curated) == 2

    def test_cap_at_max(self, set_data_dir):
        storage = set_data_dir / "data"
        lessons = [{"id": f"L{i:03d}", "lesson": f"l{i}", "category": "testing"}
                   for i in range(1, MAX_CURATED_LESSONS + 1)]
        (storage / "curated_lessons.json").write_text(json.dumps(lessons))

        new_id, error = add_to_curated(storage, "Over capacity lesson", "testing")
        assert new_id is None
        assert "capacity" in error.lower()

    def test_generates_correct_id_sequence(self, set_data_dir):
        storage = set_data_dir / "data"
        (storage / "curated_lessons.json").write_text(json.dumps([
            {"id": "L010", "lesson": "existing", "category": "testing"},
            {"id": "L003", "lesson": "existing", "category": "testing"},
        ]))
        new_id, _ = add_to_curated(storage, "New lesson", "testing")
        assert new_id == "L011"

    def test_handles_empty_file(self, set_data_dir):
        storage = set_data_dir / "data"
        (storage / "curated_lessons.json").write_text("[]")
        new_id, _ = add_to_curated(storage, "First lesson", "testing")
        assert new_id == "L001"

    def test_recovers_from_invalid_json(self, set_data_dir):
        """Corrupted JSON is treated as empty list and recovered (C3/C4 fix)."""
        storage = set_data_dir / "data"
        (storage / "curated_lessons.json").write_text("{invalid")
        new_id, error = add_to_curated(storage, "First lesson that is long enough to pass the quality gate", "testing")
        assert new_id == "L001"
        assert error is None


# ── TestRemoveFromCurated ─────────────────────────────────────────────────


class TestRemoveFromCurated:
    """Test the remove_from_curated helper."""

    def test_removes_lesson(self, set_data_dir):
        storage = set_data_dir / "data"
        (storage / "curated_lessons.json").write_text(json.dumps([
            {"id": "L001", "lesson": "to remove", "category": "testing"},
            {"id": "L002", "lesson": "to keep", "category": "testing"},
        ]))
        remove_from_curated(storage, "L001")
        curated = json.loads((storage / "curated_lessons.json").read_text())
        assert len(curated) == 1
        assert curated[0]["id"] == "L002"

    def test_noop_when_file_missing(self, set_data_dir):
        storage = set_data_dir / "data"
        # Should not raise
        remove_from_curated(storage, "L001")

    def test_noop_when_id_missing(self, set_data_dir):
        storage = set_data_dir / "data"
        (storage / "curated_lessons.json").write_text(json.dumps([
            {"id": "L001", "lesson": "keep this", "category": "testing"},
        ]))
        remove_from_curated(storage, "L999")
        curated = json.loads((storage / "curated_lessons.json").read_text())
        assert len(curated) == 1


# ── TestFeedbackMetrics ───────────────────────────────────────────────────


class TestFeedbackMetrics:
    """Test the _update_feedback_metrics helper."""

    def test_creates_metrics_file(self, set_data_dir):
        storage = set_data_dir / "data"
        _update_feedback_metrics(storage, "boosts")
        metrics_path = storage / "feedback_metrics.json"
        assert metrics_path.exists()
        metrics = json.loads(metrics_path.read_text())
        assert metrics["boosts"] == 1

    def test_increments_existing(self, set_data_dir):
        storage = set_data_dir / "data"
        _update_feedback_metrics(storage, "boosts")
        _update_feedback_metrics(storage, "boosts")
        metrics = json.loads((storage / "feedback_metrics.json").read_text())
        assert metrics["boosts"] == 2

    def test_multiple_action_types(self, set_data_dir):
        storage = set_data_dir / "data"
        _update_feedback_metrics(storage, "boosts")
        _update_feedback_metrics(storage, "demotes")
        _update_feedback_metrics(storage, "creates")
        metrics = json.loads((storage / "feedback_metrics.json").read_text())
        assert metrics["boosts"] == 1
        assert metrics["demotes"] == 1
        assert metrics["creates"] == 1

    def test_includes_timestamp(self, set_data_dir):
        storage = set_data_dir / "data"
        _update_feedback_metrics(storage, "boosts")
        metrics = json.loads((storage / "feedback_metrics.json").read_text())
        assert metrics["last_updated"] is not None


# ── TestAutoRetire ────────────────────────────────────────────────────────


class TestAutoRetire:
    """Test auto-retire lifecycle for lessons."""

    def test_retire_conditions_met(self, set_data_dir):
        """boost at 0.0 AND ups == 0 → retired."""
        storage = set_data_dir / "data"
        curated = [{"id": "L001", "lesson": "bad", "category": "testing"}]
        (storage / "curated_lessons.json").write_text(json.dumps(curated))
        scores = {"L001": {"boost": 0.3, "ups": 0, "downs": 2, "blocked": False,
                           "retired": False, "retired_reason": "", "retired_at": None}}
        save_lesson_scores(scores)

        demote_lesson("L001")
        scores = load_lesson_scores()
        assert scores["L001"]["retired"] is True
        assert scores["L001"]["blocked"] is True

    def test_retire_preserves_audit_trail(self, set_data_dir):
        """Retired lessons keep their score entry for audit."""
        storage = set_data_dir / "data"
        curated = [{"id": "L001", "lesson": "bad", "category": "testing"}]
        (storage / "curated_lessons.json").write_text(json.dumps(curated))
        scores = {"L001": {"boost": 0.3, "ups": 0, "downs": 2, "blocked": False,
                           "retired": False, "retired_reason": "", "retired_at": None}}
        save_lesson_scores(scores)

        demote_lesson("L001")
        scores = load_lesson_scores()
        assert "L001" in scores
        assert scores["L001"]["retired_at"] is not None
        assert "auto-retired" in scores["L001"]["retired_reason"]

    def test_no_retire_with_positive_feedback(self, set_data_dir):
        """Even at 0.0 boost, if ups > 0, don't retire."""
        scores = {"L001": {"boost": 0.4, "ups": 1, "downs": 5, "blocked": False,
                           "retired": False, "retired_reason": "", "retired_at": None}}
        save_lesson_scores(scores)
        demote_lesson("L001")
        scores = load_lesson_scores()
        assert scores["L001"]["retired"] is False

    def test_curated_removal_on_retire(self, set_data_dir):
        storage = set_data_dir / "data"
        curated = [
            {"id": "L001", "lesson": "bad", "category": "testing"},
            {"id": "L002", "lesson": "good", "category": "code_edit"},
        ]
        (storage / "curated_lessons.json").write_text(json.dumps(curated))
        scores = {"L001": {"boost": 0.3, "ups": 0, "downs": 2, "blocked": False,
                           "retired": False, "retired_reason": "", "retired_at": None}}
        save_lesson_scores(scores)

        demote_lesson("L001")
        curated = json.loads((storage / "curated_lessons.json").read_text())
        ids = [l["id"] for l in curated]
        assert "L001" not in ids
        assert "L002" in ids


# ── TestFeedbackLogRotation ──────────────────────────────────────────────


class TestFeedbackLogRotation:
    """Test feedback_log.jsonl rotation in maintenance."""

    def test_no_rotation_under_threshold(self, set_data_dir):
        from smartassist.tools.maintenance import rotate_feedback_log, FEEDBACK_LOG_MAX_LINES
        storage = set_data_dir / "data"
        # Write 100 lines
        feedback_log = storage / "feedback_log.jsonl"
        with open(feedback_log, "w") as f:
            for i in range(100):
                f.write(json.dumps({"line": i}) + "\n")

        rotate_feedback_log()
        # Should still have 100 lines
        with open(feedback_log) as f:
            assert len(f.readlines()) == 100

    def test_rotation_over_threshold(self, set_data_dir):
        from smartassist.tools.maintenance import (
            rotate_feedback_log, FEEDBACK_LOG_MAX_LINES, FEEDBACK_LOG_KEEP_LINES,
        )
        storage = set_data_dir / "data"
        feedback_log = storage / "feedback_log.jsonl"
        total = FEEDBACK_LOG_MAX_LINES + 500
        with open(feedback_log, "w") as f:
            for i in range(total):
                f.write(json.dumps({"line": i}) + "\n")

        rotate_feedback_log()

        # Should keep FEEDBACK_LOG_KEEP_LINES
        with open(feedback_log) as f:
            remaining = f.readlines()
        assert len(remaining) == FEEDBACK_LOG_KEEP_LINES

        # Archive should exist
        import glob
        archives = list(storage.glob("feedback_log.jsonl.*.bak"))
        assert len(archives) == 1

    def test_rotation_resets_vectorization_counter(self, set_data_dir):
        from smartassist.tools.maintenance import (
            rotate_feedback_log, FEEDBACK_LOG_MAX_LINES, FEEDBACK_LOG_KEEP_LINES,
        )
        storage = set_data_dir / "data"
        feedback_log = storage / "feedback_log.jsonl"
        total = FEEDBACK_LOG_MAX_LINES + 100
        with open(feedback_log, "w") as f:
            for i in range(total):
                f.write(json.dumps({"line": i}) + "\n")

        # Seed vectorization counter
        vec_log = storage / "vectorization_log.json"
        vec_log.write_text(json.dumps({"total_vectorized": total}))

        rotate_feedback_log()

        vec_data = json.loads(vec_log.read_text())
        assert vec_data["total_vectorized"] == FEEDBACK_LOG_KEEP_LINES
        assert vec_data["last_processed_line"] == FEEDBACK_LOG_KEEP_LINES

    def test_rotation_remaps_partial_processed_count(self, set_data_dir):
        from smartassist.tools.maintenance import (
            rotate_feedback_log, FEEDBACK_LOG_MAX_LINES, FEEDBACK_LOG_KEEP_LINES,
        )
        storage = set_data_dir / "data"
        feedback_log = storage / "feedback_log.jsonl"
        total = FEEDBACK_LOG_MAX_LINES + 100
        with open(feedback_log, "w") as f:
            for i in range(total):
                f.write(json.dumps({"line": i}) + "\n")

        old_processed = FEEDBACK_LOG_MAX_LINES - 5
        vec_log = storage / "vectorization_log.json"
        vec_log.write_text(json.dumps({"total_vectorized": old_processed}))

        rotate_feedback_log()

        archived = total - FEEDBACK_LOG_KEEP_LINES
        expected = max(0, min(FEEDBACK_LOG_KEEP_LINES, old_processed - archived))
        vec_data = json.loads(vec_log.read_text())
        assert vec_data["total_vectorized"] == expected

    def test_no_crash_when_log_missing(self, set_data_dir):
        from smartassist.tools.maintenance import rotate_feedback_log
        # Should not raise
        rotate_feedback_log()


# ── TestPromptInjectMainFeedback ─────────────────────────────────────────


class TestPromptInjectMainFeedback:
    """Test that main() correctly intercepts feedback signals and calls reinforcement."""

    def test_smiley_produces_hook_output(self, set_data_dir, capsys):
        from smartassist.hooks.prompt_inject import main
        import io

        hook_input = json.dumps({"prompt": ":)", "session_id": "test123"})
        with patch("sys.stdin", io.StringIO(hook_input)):
            main()

        captured = capsys.readouterr()
        assert captured.out.strip()
        output = json.loads(captured.out.strip())
        assert "hookSpecificOutput" in output
        context = output["hookSpecificOutput"]["additionalContext"]
        # V3: no longer injects POSITIVE/NEGATIVE mood labels
        assert "acknowledge" in context.lower() or "create_lesson" in context.lower()

    def test_frown_produces_context(self, set_data_dir, capsys):
        from smartassist.hooks.prompt_inject import main
        import io

        hook_input = json.dumps({"prompt": ":(", "session_id": "test123"})
        with patch("sys.stdin", io.StringIO(hook_input)):
            main()

        captured = capsys.readouterr()
        output = json.loads(captured.out.strip())
        context = output["hookSpecificOutput"]["additionalContext"]
        assert "demoted" in context.lower() or "0 lesson(s)" in context

    def test_signal_with_context_instructs_compare(self, set_data_dir, capsys):
        from smartassist.hooks.prompt_inject import main
        import io

        hook_input = json.dumps({"prompt": ":( dont hardcode colors in components", "session_id": "test123"})
        with patch("sys.stdin", io.StringIO(hook_input)):
            main()

        captured = capsys.readouterr()
        output = json.loads(captured.out.strip())
        context = output["hookSpecificOutput"]["additionalContext"]

        # Claude should be instructed to call compare_lesson
        assert "compare_lesson" in context
        # Hook's lesson text should NOT be in context (unbiased)
        assert "Don't hardcode colors" not in context

        # But hook still created its lesson in curated (production unchanged)
        storage = set_data_dir / "data"
        curated = json.loads((storage / "curated_lessons.json").read_text())
        assert len(curated) == 1
        assert "hardcode colors" in curated[0]["lesson"].lower()

        # And hook's result was logged to comparison file
        comparison_log = storage / "lesson_comparison.jsonl"
        assert comparison_log.exists()
        entry = json.loads(comparison_log.read_text().strip())
        assert entry["source"] == "hook"
        assert entry["passed_gates"] is True

    def test_normal_short_message_still_skipped(self, set_data_dir, capsys):
        from smartassist.hooks.prompt_inject import main
        import io

        hook_input = json.dumps({"prompt": "ok", "session_id": "test123"})
        with patch("sys.stdin", io.StringIO(hook_input)):
            main()

        captured = capsys.readouterr()
        assert captured.out.strip() == ""

    def test_smiley_calls_reinforce(self, set_data_dir, capsys):
        """:) triggers reinforce_recent_lessons("positive")."""
        from smartassist.hooks.prompt_inject import main
        import io

        with patch("smartassist.hooks.prompt_inject.reinforce_recent_lessons",
                    return_value=[("L001", 1.0, 1.3, False)]) as mock_reinforce:
            hook_input = json.dumps({"prompt": ":)", "session_id": "test123"})
            with patch("sys.stdin", io.StringIO(hook_input)):
                main()
            mock_reinforce.assert_called_once_with("positive")

    def test_frown_calls_reinforce(self, set_data_dir, capsys):
        """:( triggers reinforce_recent_lessons("negative")."""
        from smartassist.hooks.prompt_inject import main
        import io

        with patch("smartassist.hooks.prompt_inject.reinforce_recent_lessons",
                    return_value=[("L001", 1.0, 0.6, False)]) as mock_reinforce:
            hook_input = json.dumps({"prompt": ":(", "session_id": "test123"})
            with patch("sys.stdin", io.StringIO(hook_input)):
                main()
            mock_reinforce.assert_called_once_with("negative")

    def test_reinforce_results_in_context(self, set_data_dir, capsys):
        """Reinforcement results are reflected in the injected context."""
        from smartassist.hooks.prompt_inject import main
        import io

        with patch("smartassist.hooks.prompt_inject.reinforce_recent_lessons",
                    return_value=[("L022", 1.0, 1.3, False), ("L020", 1.0, 1.3, False)]):
            hook_input = json.dumps({"prompt": ":)", "session_id": "test123"})
            with patch("sys.stdin", io.StringIO(hook_input)):
                main()

        captured = capsys.readouterr()
        output = json.loads(captured.out.strip())
        context = output["hookSpecificOutput"]["additionalContext"]
        assert "Auto-reinforced 2 lesson(s)" in context

    def test_injection_includes_lesson_ids(self, set_data_dir, capsys):
        """Injected lessons should include IDs in the format."""
        from smartassist.hooks.prompt_inject import main
        import io

        storage = set_data_dir / "data"
        curated = [
            {"id": "L001", "lesson": "Always use semantic colors from theme tokens for styling",
             "category": "code_edit"},
        ]
        (storage / "curated_lessons.json").write_text(json.dumps(curated))

        hook_input = json.dumps({
            "prompt": "I need to style this component with semantic theme colors and tokens",
            "session_id": "test123",
        })
        with patch("sys.stdin", io.StringIO(hook_input)):
            main()

        captured = capsys.readouterr()
        if captured.out.strip():
            output = json.loads(captured.out.strip())
            context = output["hookSpecificOutput"]["additionalContext"]
            if "L001" in context:
                assert "[L001]" in context


# ── TestScoreSchemaV2 ────────────────────────────────────────────────────


class TestScoreSchemaV2:
    """Test V2 score schema with retired fields."""

    def test_new_score_has_retired_fields(self, set_data_dir):
        scores = {}
        entry = get_or_create_score(scores, "L001")
        assert entry["retired"] is False
        assert entry["retired_reason"] == ""
        assert entry["retired_at"] is None

    def test_existing_scores_backward_compatible(self, set_data_dir):
        """Old scores without retired fields should still work via .get()."""
        scores = {"L001": {"boost": 1.3, "ups": 5, "downs": 2, "blocked": False}}
        # Old format without retired fields
        entry = scores["L001"]
        assert entry.get("retired", False) is False
        assert entry.get("retired_reason", "") == ""
        assert entry.get("retired_at", None) is None
