"""
Thompson Sampling Model - Beta-Bernoulli with Exponential Decay
Implements reliability scoring per category with 30-day half-life
"""

import json
import time
import math
import logging
from pathlib import Path
from typing import Dict
from dataclasses import dataclass, asdict

from smartassist.config import get_storage_path

log = logging.getLogger(__name__)


@dataclass
class CategoryReliability:
    """Reliability tracking for a category using Beta-Bernoulli model"""
    category: str
    alpha: float  # Successes (thumbs up, corrections applied)
    beta: float  # Failures (thumbs down, angry)
    last_updated: float
    total_samples: int = 0

    def get_success_rate(self) -> float:
        """Expected success rate (mean of Beta distribution)"""
        if self.alpha + self.beta == 0:
            return 0.5  # Uninformed prior
        return self.alpha / (self.alpha + self.beta)

    def is_weak(self, threshold: float = 0.70) -> bool:
        """Check if category is weak (<70% success)"""
        return self.get_success_rate() < threshold


class ThompsonSamplingModel:
    """
    Thompson Sampling with 30-day exponential decay
    Floor at 1% to prevent zero-weighting
    """

    def __init__(
        self,
        storage_path: str = None,
        half_life_days: int = 30,
        floor_weight: float = 0.01
    ):
        if storage_path is None:
            self.storage_path = get_storage_path()
        else:
            self.storage_path = Path(storage_path)
        self.storage_path.mkdir(exist_ok=True)

        self.reliability_file = self.storage_path / "reliability_scores.json"
        self.half_life_days = half_life_days
        self.floor_weight = floor_weight

        # Lambda for exponential decay
        self.decay_lambda = math.log(2) / (half_life_days * 86400)  # seconds

        # Load or initialize reliabilities
        self.reliabilities: Dict[str, CategoryReliability] = self._load_reliabilities()

        log.info(
            "Thompson Sampling initialized: half_life=%dd, floor=%.1f%%, categories=%d",
            half_life_days, floor_weight * 100, len(self.reliabilities),
        )

    def _load_reliabilities(self) -> Dict[str, CategoryReliability]:
        """Load reliability scores from disk"""
        if not self.reliability_file.exists():
            return {}

        with open(self.reliability_file, 'r') as f:
            data = json.load(f)

        reliabilities = {}
        for category, values in data.items():
            reliabilities[category] = CategoryReliability(**values)

        return reliabilities

    def _save_reliabilities(self):
        """Save reliability scores to disk"""
        data = {}
        for category, rel in self.reliabilities.items():
            data[category] = asdict(rel)

        with open(self.reliability_file, 'w') as f:
            json.dump(data, f, indent=2)

    def _apply_decay(self, category: str):
        """Apply exponential decay to reliability scores"""
        if category not in self.reliabilities:
            return

        rel = self.reliabilities[category]
        time_elapsed = time.time() - rel.last_updated

        # Calculate decay weight
        decay_weight = math.exp(-self.decay_lambda * time_elapsed)

        # Apply floor
        decay_weight = max(decay_weight, self.floor_weight)

        # Apply decay to alpha and beta (use max to prevent zero, not additive)
        rel.alpha = max(self.floor_weight, rel.alpha * decay_weight)
        rel.beta = max(self.floor_weight, rel.beta * decay_weight)

    def record_success(self, category: str, intensity: int = 5):
        """
        Record a success (thumbs up, correction applied)
        Intensity 1-5 determines how much to increment alpha
        """
        # Apply decay before updating
        if category in self.reliabilities:
            self._apply_decay(category)
        else:
            # Initialize new category with uninformed prior
            self.reliabilities[category] = CategoryReliability(
                category=category,
                alpha=1.0,
                beta=1.0,
                last_updated=time.time()
            )

        # Update alpha based on intensity
        rel = self.reliabilities[category]
        rel.alpha += intensity
        rel.total_samples += 1
        rel.last_updated = time.time()

        self._save_reliabilities()

        success_rate = rel.get_success_rate()
        log.info("Success recorded for %s: %.1f%% reliability", category, success_rate * 100)

    def record_failure(self, category: str, intensity: int = 3):
        """
        Record a failure (thumbs down, angry)
        Intensity 1-5 determines how much to increment beta
        """
        # Apply decay before updating
        if category in self.reliabilities:
            self._apply_decay(category)
        else:
            # Initialize new category
            self.reliabilities[category] = CategoryReliability(
                category=category,
                alpha=1.0,
                beta=1.0,
                last_updated=time.time()
            )

        # Update beta based on intensity
        rel = self.reliabilities[category]
        rel.beta += intensity
        rel.total_samples += 1
        rel.last_updated = time.time()

        self._save_reliabilities()

        success_rate = rel.get_success_rate()
        log.info("Failure recorded for %s: %.1f%% reliability", category, success_rate * 100)

    def get_reliability(self, category: str) -> float:
        """Get current reliability score for category"""
        if category not in self.reliabilities:
            return 0.5  # Uninformed prior

        self._apply_decay(category)
        return self.reliabilities[category].get_success_rate()

    def get_weak_categories(self, threshold: float = 0.70) -> list[str]:
        """Get categories with success rate below threshold"""
        weak = []
        for category, rel in self.reliabilities.items():
            self._apply_decay(category)
            if rel.is_weak(threshold):
                weak.append(category)
        return weak

    def get_all_reliabilities(self) -> Dict[str, float]:
        """Get reliability scores for all categories"""
        scores = {}
        for category in self.reliabilities.keys():
            scores[category] = self.get_reliability(category)
        return scores

    def get_stats(self) -> Dict:
        """Get Thompson Sampling statistics"""
        all_scores = self.get_all_reliabilities()

        if not all_scores:
            return {
                'total_categories': 0,
                'avg_reliability': 0,
                'weak_categories': []
            }

        return {
            'total_categories': len(all_scores),
            'avg_reliability': sum(all_scores.values()) / len(all_scores),
            'weak_categories': self.get_weak_categories(),
            'reliability_by_category': all_scores
        }
