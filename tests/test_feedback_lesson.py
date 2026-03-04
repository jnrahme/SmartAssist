"""Tests for V2 LLM-as-Judge Feedback System — Claude as Lesson Curator.

Covers:
  - V2 feedback signal detection (tuple returns + prefix matching)
  - Rich feedback context building
  - Reconstructing injected lessons
  - boost_lesson, demote_lesson, merge_lessons MCP tools
  - create_lesson dual-path write
  - _add_to_curated / _remove_from_curated helpers
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
    boost_lesson,
    demote_lesson,
    merge_lessons,
    _add_to_curated,
    _remove_from_curated,
    _update_feedback_metrics,
    VALID_CATEGORIES,
    ACTION_VERBS,
    GENERIC_STARTS,
    MAX_CURATED_LESSONS,
)
from smartassist.lesson_feedback import (
    load_lesson_scores,
    save_lesson_scores,
    _get_or_create_score,
    save_last_injection,
    DEFAULT_BOOST,
    BOOST_INCREMENT,
    DEMOTE_DECREMENT,
    BOOST_CAP,
    BOOST_FLOOR,
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


# ── TestBuildRichFeedbackContext ────────────────────────────────────────────


class TestBuildRichFeedbackContext:
    """Test rich feedback context for Claude's per-lesson decisions."""

    def test_positive_context(self, set_data_dir):
        result = build_rich_feedback_context("positive", "", None)
        assert "POSITIVE" in result
        assert "happy" in result.lower()

    def test_negative_context(self, set_data_dir):
        result = build_rich_feedback_context("negative", "", None)
        assert "NEGATIVE" in result
        assert "unhappy" in result.lower()

    def test_includes_user_context(self, set_data_dir):
        result = build_rich_feedback_context("negative", "dont do this to the theme", None)
        assert "dont do this to the theme" in result

    def test_empty_lessons_shows_create_instruction(self, set_data_dir):
        result = build_rich_feedback_context("positive", "", None)
        assert "NO LESSONS WERE RECENTLY INJECTED" in result
        assert "create_lesson" in result

    def test_with_lessons_shows_table(self, set_data_dir):
        storage = set_data_dir / "data"
        curated = [
            {"id": "L001", "lesson": "Use semantic colors from theme tokens", "category": "code_edit"},
        ]
        (storage / "curated_lessons.json").write_text(json.dumps(curated))
        save_last_injection([{"id": "L001"}])

        result = build_rich_feedback_context("positive", "", storage)
        assert "L001" in result
        assert "code_edit" in result
        assert "RECENTLY INJECTED LESSONS" in result

    def test_decision_framework_included(self, set_data_dir):
        storage = set_data_dir / "data"
        curated = [{"id": "L001", "lesson": "test lesson text here", "category": "testing"}]
        (storage / "curated_lessons.json").write_text(json.dumps(curated))
        save_last_injection([{"id": "L001"}])

        result = build_rich_feedback_context("positive", "", storage)
        assert "DECISION FRAMEWORK" in result
        assert "boost_lesson" in result

    def test_negative_decision_framework(self, set_data_dir):
        storage = set_data_dir / "data"
        curated = [{"id": "L001", "lesson": "test lesson text here", "category": "testing"}]
        (storage / "curated_lessons.json").write_text(json.dumps(curated))
        save_last_injection([{"id": "L001"}])

        result = build_rich_feedback_context("negative", "", storage)
        assert "demote_lesson" in result

    def test_constraints_included(self, set_data_dir):
        storage = set_data_dir / "data"
        curated = [{"id": "L001", "lesson": "test", "category": "testing"}]
        (storage / "curated_lessons.json").write_text(json.dumps(curated))
        save_last_injection([{"id": "L001"}])

        result = build_rich_feedback_context("positive", "", storage)
        assert "Max 5 tool calls" in result
        assert "confidence" in result.lower()

    def test_merge_mentioned(self, set_data_dir):
        storage = set_data_dir / "data"
        curated = [{"id": "L001", "lesson": "test", "category": "testing"}]
        (storage / "curated_lessons.json").write_text(json.dumps(curated))
        save_last_injection([{"id": "L001"}])

        result = build_rich_feedback_context("positive", "", storage)
        assert "merge_lessons" in result

    def test_acknowledge_instruction(self, set_data_dir):
        result = build_rich_feedback_context("positive", "", None)
        assert "acknowledge" in result.lower()


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

    def test_shows_lesson_count(self, set_data_dir):
        storage = set_data_dir / "data"
        write_to_live_log_feedback(storage, ":)", "positive")
        content = (storage / "rag_live.log").read_text()
        assert "lesson(s) sent to Claude" in content


# ── TestBoostLessonTool ─────────────────────────────────────────────────


class TestBoostLessonTool:
    """Test the boost_lesson MCP tool."""

    def test_boosts_score(self, set_data_dir):
        result = boost_lesson("L001")
        assert "Boosted" in result
        scores = load_lesson_scores()
        assert scores["L001"]["ups"] == 1
        assert scores["L001"]["boost"] == DEFAULT_BOOST + BOOST_INCREMENT

    def test_boost_capped_at_max(self, set_data_dir):
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
        boost_lesson("L001")
        metrics_path = storage / "feedback_metrics.json"
        assert metrics_path.exists()
        metrics = json.loads(metrics_path.read_text())
        assert metrics["boosts"] == 1

    def test_case_insensitive_id(self, set_data_dir):
        result = boost_lesson("l001")
        assert "L001" in result


# ── TestDemoteLessonTool ─────────────────────────────────────────────────


class TestDemoteLessonTool:
    """Test the demote_lesson MCP tool."""

    def test_demotes_score(self, set_data_dir):
        result = demote_lesson("L001")
        assert "Demoted" in result
        scores = load_lesson_scores()
        assert scores["L001"]["downs"] == 1
        assert scores["L001"]["boost"] == max(DEFAULT_BOOST - DEMOTE_DECREMENT, BOOST_FLOOR)

    def test_demote_floored_at_zero(self, set_data_dir):
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

    def test_no_auto_retire_with_ups(self, set_data_dir):
        """Lesson with ups > 0 should NOT be auto-retired even at 0.0 boost."""
        scores = {"L001": {"boost": 0.3, "ups": 3, "downs": 8, "blocked": False,
                           "retired": False, "retired_reason": "", "retired_at": None}}
        save_lesson_scores(scores)
        result = demote_lesson("L001")
        assert "RETIRED" not in result
        scores = load_lesson_scores()
        assert scores["L001"].get("retired", False) is False

    def test_warns_strong_positive_history(self, set_data_dir):
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
        demote_lesson("L001")
        metrics_path = storage / "feedback_metrics.json"
        metrics = json.loads(metrics_path.read_text())
        assert metrics["demotes"] == 1


# ── TestMergeLessonsTool ─────────────────────────────────────────────────


class TestMergeLessonsTool:
    """Test the merge_lessons MCP tool."""

    VALID_MERGED = "Always use semantic design tokens from the theme for all color values"

    def _seed_curated(self, set_data_dir, lessons):
        storage = set_data_dir / "data"
        (storage / "curated_lessons.json").write_text(json.dumps(lessons))
        return storage

    @patch("smartassist.mcp_server.subprocess.Popen")
    def test_merge_two_lessons(self, mock_popen, set_data_dir):
        curated = [
            {"id": "L001", "lesson": "Use semantic colors", "category": "code_edit"},
            {"id": "L002", "lesson": "Avoid hardcoded hex values", "category": "code_edit"},
        ]
        self._seed_curated(set_data_dir, curated)

        result = merge_lessons("L001,L002", self.VALID_MERGED, "code_edit")
        assert "Merged" in result
        assert "L001" in result
        assert "L002" in result

    @patch("smartassist.mcp_server.subprocess.Popen")
    def test_merge_removes_sources_from_curated(self, mock_popen, set_data_dir):
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

    @patch("smartassist.mcp_server.subprocess.Popen")
    def test_merge_adds_new_lesson_to_curated(self, mock_popen, set_data_dir):
        storage = self._seed_curated(set_data_dir, [
            {"id": "L001", "lesson": "Use semantic colors", "category": "code_edit"},
            {"id": "L002", "lesson": "Avoid hardcoded hex values", "category": "code_edit"},
        ])

        merge_lessons("L001,L002", self.VALID_MERGED, "code_edit")

        curated = json.loads((storage / "curated_lessons.json").read_text())
        assert any(self.VALID_MERGED in l["lesson"] for l in curated)

    @patch("smartassist.mcp_server.subprocess.Popen")
    def test_merge_combines_scores(self, mock_popen, set_data_dir):
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

    @patch("smartassist.mcp_server.subprocess.Popen")
    def test_merge_marks_sources_superseded(self, mock_popen, set_data_dir):
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

    @patch("smartassist.mcp_server.subprocess.Popen")
    def test_merge_writes_to_feedback_log(self, mock_popen, set_data_dir):
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

    @patch("smartassist.mcp_server.subprocess.Popen")
    def test_merge_fires_vectorization(self, mock_popen, set_data_dir):
        self._seed_curated(set_data_dir, [
            {"id": "L001", "lesson": "Use semantic colors", "category": "code_edit"},
            {"id": "L002", "lesson": "Avoid hardcoded hex values", "category": "code_edit"},
        ])
        merge_lessons("L001,L002", self.VALID_MERGED, "code_edit")
        mock_popen.assert_called_once()

    @patch("smartassist.mcp_server.subprocess.Popen")
    def test_merge_updates_metrics(self, mock_popen, set_data_dir):
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

    @patch("smartassist.mcp_server.subprocess.Popen")
    def test_dual_path_write(self, mock_popen, set_data_dir):
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

    @patch("smartassist.mcp_server.subprocess.Popen")
    def test_auto_generates_id(self, mock_popen, set_data_dir):
        storage = set_data_dir / "data"
        create_lesson(lesson=self.VALID_LESSON, category=self.VALID_CATEGORY)
        curated = json.loads((storage / "curated_lessons.json").read_text())
        assert curated[0]["id"] == "L001"

    @patch("smartassist.mcp_server.subprocess.Popen")
    def test_increments_id(self, mock_popen, set_data_dir):
        storage = set_data_dir / "data"
        (storage / "curated_lessons.json").write_text(json.dumps([
            {"id": "L005", "lesson": "existing", "category": "testing"},
        ]))
        create_lesson(lesson=self.VALID_LESSON, category=self.VALID_CATEGORY)
        curated = json.loads((storage / "curated_lessons.json").read_text())
        assert curated[-1]["id"] == "L006"

    @patch("smartassist.mcp_server.subprocess.Popen")
    def test_cap_enforcement(self, mock_popen, set_data_dir):
        storage = set_data_dir / "data"
        # Seed with MAX_CURATED_LESSONS lessons
        lessons = [{"id": f"L{i:03d}", "lesson": f"lesson {i}", "category": "testing"}
                   for i in range(1, MAX_CURATED_LESSONS + 1)]
        (storage / "curated_lessons.json").write_text(json.dumps(lessons))

        result = create_lesson(lesson=self.VALID_LESSON, category=self.VALID_CATEGORY)
        assert "capacity" in result.lower()

    @patch("smartassist.mcp_server.subprocess.Popen")
    def test_updates_feedback_metrics(self, mock_popen, set_data_dir):
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

    @patch("smartassist.mcp_server.subprocess.Popen")
    def test_stores_to_feedback_log(self, mock_popen, set_data_dir):
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

    @patch("smartassist.mcp_server.subprocess.Popen")
    def test_fires_vectorization(self, mock_popen, set_data_dir):
        create_lesson(lesson=self.VALID_LESSON, category=self.VALID_CATEGORY)
        mock_popen.assert_called_once()

    @patch("smartassist.mcp_server.subprocess.Popen")
    def test_updates_thompson_positive(self, mock_popen, set_data_dir):
        with patch("smartassist.mcp_server._get_thompson") as mock_get:
            mock_thompson = MagicMock()
            mock_get.return_value = mock_thompson
            create_lesson(lesson=self.VALID_LESSON, category=self.VALID_CATEGORY, sentiment="positive", intensity=4)
            mock_thompson.record_success.assert_called_once_with("code_edit", 4)

    @patch("smartassist.mcp_server.subprocess.Popen")
    def test_updates_thompson_negative(self, mock_popen, set_data_dir):
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
    """Test the _add_to_curated helper."""

    def test_creates_file_if_missing(self, set_data_dir):
        storage = set_data_dir / "data"
        new_id, error = _add_to_curated(storage, "Test lesson text here", "testing")
        assert new_id == "L001"
        assert error is None
        assert (storage / "curated_lessons.json").exists()

    def test_appends_to_existing(self, set_data_dir):
        storage = set_data_dir / "data"
        (storage / "curated_lessons.json").write_text(json.dumps([
            {"id": "L001", "lesson": "existing", "category": "testing"},
        ]))
        new_id, error = _add_to_curated(storage, "New lesson here", "code_edit")
        assert new_id == "L002"
        curated = json.loads((storage / "curated_lessons.json").read_text())
        assert len(curated) == 2

    def test_cap_at_max(self, set_data_dir):
        storage = set_data_dir / "data"
        lessons = [{"id": f"L{i:03d}", "lesson": f"l{i}", "category": "testing"}
                   for i in range(1, MAX_CURATED_LESSONS + 1)]
        (storage / "curated_lessons.json").write_text(json.dumps(lessons))

        new_id, error = _add_to_curated(storage, "Over capacity lesson", "testing")
        assert new_id is None
        assert "capacity" in error.lower()

    def test_generates_correct_id_sequence(self, set_data_dir):
        storage = set_data_dir / "data"
        (storage / "curated_lessons.json").write_text(json.dumps([
            {"id": "L010", "lesson": "existing", "category": "testing"},
            {"id": "L003", "lesson": "existing", "category": "testing"},
        ]))
        new_id, _ = _add_to_curated(storage, "New lesson", "testing")
        assert new_id == "L011"

    def test_handles_empty_file(self, set_data_dir):
        storage = set_data_dir / "data"
        (storage / "curated_lessons.json").write_text("[]")
        new_id, _ = _add_to_curated(storage, "First lesson", "testing")
        assert new_id == "L001"


# ── TestRemoveFromCurated ─────────────────────────────────────────────────


class TestRemoveFromCurated:
    """Test the _remove_from_curated helper."""

    def test_removes_lesson(self, set_data_dir):
        storage = set_data_dir / "data"
        (storage / "curated_lessons.json").write_text(json.dumps([
            {"id": "L001", "lesson": "to remove", "category": "testing"},
            {"id": "L002", "lesson": "to keep", "category": "testing"},
        ]))
        _remove_from_curated(storage, "L001")
        curated = json.loads((storage / "curated_lessons.json").read_text())
        assert len(curated) == 1
        assert curated[0]["id"] == "L002"

    def test_noop_when_file_missing(self, set_data_dir):
        storage = set_data_dir / "data"
        # Should not raise
        _remove_from_curated(storage, "L001")

    def test_noop_when_id_missing(self, set_data_dir):
        storage = set_data_dir / "data"
        (storage / "curated_lessons.json").write_text(json.dumps([
            {"id": "L001", "lesson": "keep this", "category": "testing"},
        ]))
        _remove_from_curated(storage, "L999")
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
        vec_log.write_text(json.dumps({"last_processed_line": total}))

        rotate_feedback_log()

        vec_data = json.loads(vec_log.read_text())
        assert vec_data["last_processed_line"] == FEEDBACK_LOG_KEEP_LINES

    def test_no_crash_when_log_missing(self, set_data_dir):
        from smartassist.tools.maintenance import rotate_feedback_log
        # Should not raise
        rotate_feedback_log()


# ── TestPromptInjectMainFeedback ─────────────────────────────────────────


class TestPromptInjectMainFeedback:
    """Test that main() correctly intercepts V2 feedback signals."""

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
        assert "POSITIVE" in context

    def test_frown_produces_negative_context(self, set_data_dir, capsys):
        from smartassist.hooks.prompt_inject import main
        import io

        hook_input = json.dumps({"prompt": ":(", "session_id": "test123"})
        with patch("sys.stdin", io.StringIO(hook_input)):
            main()

        captured = capsys.readouterr()
        output = json.loads(captured.out.strip())
        context = output["hookSpecificOutput"]["additionalContext"]
        assert "NEGATIVE" in context

    def test_signal_with_context_passes_through(self, set_data_dir, capsys):
        from smartassist.hooks.prompt_inject import main
        import io

        hook_input = json.dumps({"prompt": ":( dont hardcode colors", "session_id": "test123"})
        with patch("sys.stdin", io.StringIO(hook_input)):
            main()

        captured = capsys.readouterr()
        output = json.loads(captured.out.strip())
        context = output["hookSpecificOutput"]["additionalContext"]
        assert "dont hardcode colors" in context

    def test_normal_short_message_still_skipped(self, set_data_dir, capsys):
        from smartassist.hooks.prompt_inject import main
        import io

        hook_input = json.dumps({"prompt": "ok", "session_id": "test123"})
        with patch("sys.stdin", io.StringIO(hook_input)):
            main()

        captured = capsys.readouterr()
        assert captured.out.strip() == ""

    def test_no_reinforce_called(self, set_data_dir, capsys):
        """V2: main() should NOT call reinforce_injected_lessons (removed)."""
        from smartassist.hooks.prompt_inject import main
        import io

        hook_input = json.dumps({"prompt": ":)", "session_id": "test123"})
        with patch("sys.stdin", io.StringIO(hook_input)):
            # Verify reinforce_injected_lessons doesn't exist as import
            import smartassist.hooks.prompt_inject as pi
            assert not hasattr(pi, "reinforce_injected_lessons")

    def test_injection_includes_lesson_ids(self, set_data_dir, capsys):
        """V2: Injected lessons should include IDs in the format."""
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
        entry = _get_or_create_score(scores, "L001")
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
