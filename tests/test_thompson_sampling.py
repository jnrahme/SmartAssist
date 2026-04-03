"""Tests for smartassist.thompson_sampling."""

import time

import pytest

from smartassist.thompson_sampling import CategoryReliability, ThompsonSamplingModel


class TestThompsonSampling:
    """Test Beta-Bernoulli Thompson Sampling model."""

    def test_initial_reliability_is_neutral(self, set_data_dir):
        storage = str(set_data_dir / "data")
        model = ThompsonSamplingModel(storage)
        # With no data, reliability should be around 0.5 (prior)
        score = model.get_reliability("testing")
        assert 0.4 <= score <= 0.6

    def test_record_success_increases_reliability(self, set_data_dir):
        storage = str(set_data_dir / "data")
        model = ThompsonSamplingModel(storage)
        before = model.get_reliability("testing")
        model.record_success("testing", intensity=5)
        after = model.get_reliability("testing")
        assert after > before

    def test_record_failure_decreases_reliability(self, set_data_dir):
        storage = str(set_data_dir / "data")
        model = ThompsonSamplingModel(storage)
        # Start with some successes so there's room to decrease
        model.record_success("testing", intensity=5)
        model.record_success("testing", intensity=5)
        before = model.get_reliability("testing")
        model.record_failure("testing", intensity=5)
        after = model.get_reliability("testing")
        assert after < before

    def test_get_weak_categories_empty_initially(self, set_data_dir):
        storage = str(set_data_dir / "data")
        model = ThompsonSamplingModel(storage)
        weak = model.get_weak_categories(threshold=0.70)
        assert weak == []

    def test_get_weak_categories_detects_failures(self, set_data_dir):
        storage = str(set_data_dir / "data")
        model = ThompsonSamplingModel(storage)
        # Record many failures to push below threshold
        for _ in range(10):
            model.record_failure("testing", intensity=5)
        weak = model.get_weak_categories(threshold=0.70)
        assert "testing" in weak

    def test_get_all_reliabilities(self, set_data_dir):
        storage = str(set_data_dir / "data")
        model = ThompsonSamplingModel(storage)
        model.record_success("testing", intensity=3)
        model.record_failure("git", intensity=3)
        scores = model.get_all_reliabilities()
        assert "testing" in scores
        assert "git" in scores
        assert scores["testing"] > scores["git"]

    def test_persistence(self, set_data_dir):
        storage = str(set_data_dir / "data")
        model1 = ThompsonSamplingModel(storage)
        model1.record_success("testing", intensity=5)
        score1 = model1.get_reliability("testing")

        # Create new instance, should load saved scores
        model2 = ThompsonSamplingModel(storage)
        score2 = model2.get_reliability("testing")
        assert abs(score1 - score2) < 1e-10

    def test_corrupted_reliability_file_recovers(self, set_data_dir):
        storage = set_data_dir / "data"
        (set_data_dir / "data" / "reliability_scores.json").write_text("{bad json")

        model = ThompsonSamplingModel(storage)

        assert model.reliabilities == {}
        assert model.get_reliability("testing") == 0.5

    def test_get_reliability_applies_decay_and_persists_state(self, set_data_dir):
        storage = str(set_data_dir / "data")
        model = ThompsonSamplingModel(storage)
        model.reliabilities["testing"] = CategoryReliability(
            category="testing",
            alpha=10.0,
            beta=2.0,
            last_updated=time.time() - 86400 * 30,
        )

        before = (
            model.reliabilities["testing"].alpha,
            model.reliabilities["testing"].beta,
            model.reliabilities["testing"].last_updated,
        )

        score = model.get_reliability("testing")

        after = (
            model.reliabilities["testing"].alpha,
            model.reliabilities["testing"].beta,
            model.reliabilities["testing"].last_updated,
        )

        assert score == pytest.approx(10.0 / 12.0)
        assert after[0] < before[0]
        assert after[1] < before[1]
        assert after[2] > before[2]

        reloaded = ThompsonSamplingModel(storage)
        assert reloaded.reliabilities["testing"].alpha == after[0]
        assert reloaded.reliabilities["testing"].beta == after[1]
