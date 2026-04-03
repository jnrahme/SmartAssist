"""Tests for per-lesson Thompson Sampling reranking and MemAlign episodic memory."""

import time
import pytest

from smartassist.thompson_rerank import (
    thompson_rerank,
    attribute_feedback,
    load_thompson_batch,
    update_thompson_batch,
    record_injection,
    migrate_from_lesson_scores,
    ensure_thompson_table,
)
from smartassist.store import add_lesson, open_store, list_lessons


def _storage(set_data_dir):
    return set_data_dir / "data"


class TestThompsonRerank:
    """Core Thompson Sampling reranking logic."""

    def test_new_lessons_pass_through_at_full_score(self, set_data_dir):
        """Lessons with no Thompson history should not be penalized."""
        candidates = [
            {"id": "L001", "score": 0.8, "category": "testing", "lesson": "test lesson"},
            {"id": "L002", "score": 0.5, "category": "git", "lesson": "git lesson"},
        ]
        # No Thompson data — all lessons are new
        result = thompson_rerank(candidates, {})

        # New lessons should keep their retrieval score (thompson_sample = 1.0)
        assert result[0]["final_score"] == 0.8
        assert result[0]["thompson_sample"] == 1.0
        assert result[1]["final_score"] == 0.5

    def test_proven_lessons_rank_higher(self, set_data_dir):
        """Lessons with high alpha should rank higher than low alpha."""
        candidates = [
            {"id": "L001", "score": 0.5, "category": "testing", "lesson": "bad lesson"},
            {"id": "L002", "score": 0.5, "category": "testing", "lesson": "good lesson"},
        ]
        now = time.time()
        thompson_data = {
            "L001": {"alpha": 1.0, "beta": 10.0, "last_updated": now, "injection_count": 20},
            "L002": {"alpha": 10.0, "beta": 1.0, "last_updated": now, "injection_count": 20},
        }

        # Run many times and check that L002 wins most of the time
        l002_wins = 0
        for _ in range(100):
            result = thompson_rerank(candidates, thompson_data, now=now)
            if result[0]["id"] == "L002":
                l002_wins += 1

        # L002 (alpha=10, beta=1) should win most of the time
        assert l002_wins > 70, f"L002 only won {l002_wins}/100 times"

    def test_few_observations_use_mean_not_sample(self, set_data_dir):
        """Lessons with <3 injections use Thompson mean, not random sample."""
        candidates = [
            {"id": "L001", "score": 0.6, "category": "testing", "lesson": "test"},
        ]
        now = time.time()
        thompson_data = {
            "L001": {"alpha": 2.0, "beta": 1.0, "last_updated": now, "injection_count": 2},
        }

        # With <3 observations, should use mean (2/3 ≈ 0.667) not random sample
        scores = set()
        for _ in range(10):
            result = thompson_rerank(candidates, thompson_data, now=now)
            scores.add(round(result[0]["final_score"], 4))

        # Mean-based: all scores should be identical (deterministic)
        assert len(scores) == 1, f"Expected deterministic score, got {scores}"

    def test_relevance_zero_stays_zero(self, set_data_dir):
        """Irrelevant lessons should stay at zero regardless of Thompson score."""
        candidates = [
            {"id": "L001", "score": 0.0, "category": "testing", "lesson": "irrelevant"},
        ]
        now = time.time()
        thompson_data = {
            "L001": {"alpha": 100.0, "beta": 1.0, "last_updated": now, "injection_count": 100},
        }
        result = thompson_rerank(candidates, thompson_data, now=now)
        assert result[0]["final_score"] == 0.0


class TestFeedbackAttribution:
    """Fractional credit attribution from user feedback."""

    def test_single_lesson_gets_full_credit(self, set_data_dir):
        """One injected lesson gets all the credit."""
        injected = [{"id": "L001", "score": 0.8, "injection_timestamp": time.time()}]
        result = attribute_feedback("positive", injected)

        assert len(result) == 1
        assert result[0][0] == "L001"
        assert result[0][1] > 0.9  # alpha_delta ≈ 1.0
        assert result[0][2] == 0.0  # beta_delta = 0

    def test_negative_feedback_increases_beta(self, set_data_dir):
        """Negative feedback should increase beta, not alpha."""
        injected = [{"id": "L001", "score": 0.8, "injection_timestamp": time.time()}]
        result = attribute_feedback("negative", injected)

        assert result[0][1] == 0.0  # alpha_delta = 0
        assert result[0][2] > 0.9  # beta_delta ≈ 1.0

    def test_credit_distributed_by_relevance(self, set_data_dir):
        """Higher relevance lessons get more credit."""
        now = time.time()
        injected = [
            {"id": "L001", "score": 0.9, "injection_timestamp": now},
            {"id": "L002", "score": 0.1, "injection_timestamp": now},
        ]
        result = attribute_feedback("positive", injected, now=now)

        l001_credit = result[0][1]  # alpha_delta for L001
        l002_credit = result[1][1]  # alpha_delta for L002
        assert l001_credit > l002_credit, "Higher relevance should get more credit"

    def test_older_injections_get_less_credit(self, set_data_dir):
        """Lessons injected longer ago get less feedback credit."""
        now = time.time()
        injected = [
            {"id": "L001", "score": 0.5, "injection_timestamp": now},           # just now
            {"id": "L002", "score": 0.5, "injection_timestamp": now - 600},      # 10 minutes ago
        ]
        result = attribute_feedback("positive", injected, now=now)

        recent_credit = result[0][1]
        old_credit = result[1][1]
        assert recent_credit > old_credit, "Recent injection should get more credit"


class TestThompsonDatabaseOps:
    """Database operations for per-lesson Thompson state."""

    def test_load_empty_returns_empty(self, set_data_dir):
        storage = _storage(set_data_dir)
        data = load_thompson_batch(storage, ["L001", "L002"])
        assert data == {}

    def test_update_then_load(self, set_data_dir):
        storage = _storage(set_data_dir)
        update_thompson_batch(storage, [("L001", 1.0, 0.0)])  # positive

        data = load_thompson_batch(storage, ["L001"])
        assert "L001" in data
        assert data["L001"]["alpha"] > 1.0  # default 1.0 + 1.0 feedback
        assert data["L001"]["beta"] == 1.0  # default, no negative

    def test_multiple_updates_accumulate(self, set_data_dir):
        storage = _storage(set_data_dir)
        update_thompson_batch(storage, [("L001", 1.0, 0.0)])  # positive
        update_thompson_batch(storage, [("L001", 1.0, 0.0)])  # positive again
        update_thompson_batch(storage, [("L001", 0.0, 1.0)])  # negative

        data = load_thompson_batch(storage, ["L001"])
        assert data["L001"]["alpha"] > 2.0  # two positives
        assert data["L001"]["beta"] > 1.0   # one negative

    def test_record_injection_tracks_count(self, set_data_dir):
        storage = _storage(set_data_dir)
        record_injection(storage, ["L001", "L002"])
        record_injection(storage, ["L001"])  # L001 injected twice

        data = load_thompson_batch(storage, ["L001", "L002"])
        assert data["L001"]["injection_count"] == 2
        assert data["L002"]["injection_count"] == 1

    def test_migrate_from_lesson_scores(self, set_data_dir):
        storage = _storage(set_data_dir)
        # Create a lesson with some feedback history
        add_lesson(storage, "Always run tests before pushing", "testing")

        # Manually set ups/downs in lesson_scores
        with open_store(storage) as conn:
            conn.execute(
                "UPDATE lesson_scores SET ups = 5, downs = 2 WHERE lesson_id = 'L001'"
            )
            conn.commit()

        count = migrate_from_lesson_scores(storage)
        assert count == 1

        data = load_thompson_batch(storage, ["L001"])
        assert data["L001"]["alpha"] == 6.0  # 1.0 default + 5 ups
        assert data["L001"]["beta"] == 3.0   # 1.0 default + 2 downs


class TestEndToEndReinforcementLoop:
    """Full RLHF cycle: create → inject → feedback → improved ranking."""

    def test_feedback_improves_ranking(self, set_data_dir):
        """Positive feedback on a lesson should make it rank higher next time."""
        storage = _storage(set_data_dir)

        # Create two lessons
        add_lesson(storage, "Always use semantic color tokens from the theme", "code_edit")
        add_lesson(storage, "Never commit console.log statements to production", "code_edit")

        # Simulate: both get injected
        record_injection(storage, ["L001", "L002"])

        # Simulate: user gives positive feedback, attributed more to L001
        update_thompson_batch(storage, [("L001", 1.0, 0.0), ("L002", 0.2, 0.0)])

        # Repeat a few times to build signal
        for _ in range(5):
            record_injection(storage, ["L001", "L002"])
            update_thompson_batch(storage, [("L001", 1.0, 0.0), ("L002", 0.1, 0.0)])

        # Now check: L001 should have higher Thompson mean than L002
        data = load_thompson_batch(storage, ["L001", "L002"])
        l001_mean = data["L001"]["alpha"] / (data["L001"]["alpha"] + data["L001"]["beta"])
        l002_mean = data["L002"]["alpha"] / (data["L002"]["alpha"] + data["L002"]["beta"])

        assert l001_mean > l002_mean, f"L001 ({l001_mean:.2f}) should rank higher than L002 ({l002_mean:.2f})"

    def test_negative_feedback_demotes_lesson(self, set_data_dir):
        """Negative feedback should reduce a lesson's Thompson ranking."""
        storage = _storage(set_data_dir)
        add_lesson(storage, "Always use snapshot tests for UI components", "testing")

        record_injection(storage, ["L001"])
        # User keeps saying this lesson is wrong
        for _ in range(5):
            update_thompson_batch(storage, [("L001", 0.0, 1.0)])

        data = load_thompson_batch(storage, ["L001"])
        mean = data["L001"]["alpha"] / (data["L001"]["alpha"] + data["L001"]["beta"])
        assert mean < 0.3, f"Heavily demoted lesson should have low mean, got {mean:.2f}"
