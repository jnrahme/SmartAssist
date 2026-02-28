"""Tests for smartassist.thompson_sampling."""

import json
from smartassist.thompson_sampling import ThompsonSamplingModel


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
