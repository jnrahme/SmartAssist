"""Per-lesson Thompson Sampling for RAG retrieval reranking.

This module implements the reinforcement learning loop that makes the RAG
database improve over time:

1. Lessons are retrieved by keyword or semantic search (candidates)
2. Each candidate is reranked using Thompson Sampling: sample from Beta(alpha, beta)
3. final_score = retrieval_relevance * thompson_sample
4. Top-K candidates are injected into the prompt
5. User feedback (:) / :() is attributed fractionally to injected lessons
6. alpha/beta update → next retrieval is better

The key insight: Thompson Sampling naturally balances exploration (showing new/untested
lessons) vs exploitation (preferring proven lessons), without any tuning parameters.
"""

import math
import random
import sqlite3
import time
from pathlib import Path
from typing import Any

from smartassist.store import open_store

DEFAULT_ALPHA = 1.0
DEFAULT_BETA = 1.0
DECAY_HALF_LIFE_DAYS = 30
DECAY_LAMBDA = math.log(2) / (DECAY_HALF_LIFE_DAYS * 86400)
DECAY_FLOOR = 0.01
MIN_SAMPLING_EVIDENCE = 3.0
ATTRIBUTION_HALF_LIFE = 300  # 5 minutes for within-session credit decay


# ── Schema ────────────────────────────────────────────────────────────────


def ensure_thompson_table(conn: sqlite3.Connection) -> None:
    """Create the lesson_thompson table if it doesn't exist."""
    conn.execute("""
        CREATE TABLE IF NOT EXISTS lesson_thompson (
            lesson_id TEXT NOT NULL,
            context_key TEXT NOT NULL DEFAULT '_global',
            alpha REAL NOT NULL DEFAULT 1.0,
            beta REAL NOT NULL DEFAULT 1.0,
            last_updated REAL NOT NULL,
            injection_count INTEGER NOT NULL DEFAULT 0,
            last_injected REAL,
            PRIMARY KEY (lesson_id, context_key)
        )
    """)
    conn.execute("""
        CREATE INDEX IF NOT EXISTS idx_lesson_thompson_lesson
            ON lesson_thompson(lesson_id)
    """)


def decayed_counts(state: dict[str, float] | None, now: float) -> tuple[float, float]:
    """Return (alpha, beta) with evidence decayed toward the Beta(1, 1) prior."""
    if not state:
        return DEFAULT_ALPHA, DEFAULT_BETA
    elapsed = max(0.0, now - state.get("last_updated", now))
    decay = max(DECAY_FLOOR, math.exp(-DECAY_LAMBDA * elapsed))
    alpha = DEFAULT_ALPHA + (state.get("alpha", DEFAULT_ALPHA) - DEFAULT_ALPHA) * decay
    beta_val = DEFAULT_BETA + (state.get("beta", DEFAULT_BETA) - DEFAULT_BETA) * decay
    return max(DEFAULT_ALPHA, alpha), max(DEFAULT_BETA, beta_val)


# ── Core: Thompson Reranking ──────────────────────────────────────────────


def thompson_rerank(
    candidates: list[dict[str, Any]],
    thompson_data: dict[str, dict[str, float]],
    now: float | None = None,
) -> list[dict[str, Any]]:
    """Rerank retrieval candidates using per-lesson Thompson Sampling.

    Each candidate gets a score: final_score = retrieval_score * Beta(alpha, beta).sample()

    This naturally balances:
    - Exploitation: lessons with high alpha/(alpha+beta) rank higher on average
    - Exploration: lessons with few observations (wide variance) occasionally rank high

    Args:
        candidates: list of dicts with 'id' (lesson_id) and 'score' (retrieval relevance)
        thompson_data: dict of {lesson_id: {alpha, beta, last_updated}}
        now: current timestamp (for testing)

    Returns:
        candidates sorted by thompson-reranked score, with thompson metadata added
    """
    if now is None:
        now = time.time()

    reranked = []
    for candidate in candidates:
        lesson_id = candidate.get("id") or candidate.get("source_id", "")
        retrieval_score = candidate.get("score", 0.5)
        alpha, beta_val = decayed_counts(thompson_data.get(lesson_id), now)
        evidence = (alpha - DEFAULT_ALPHA) + (beta_val - DEFAULT_BETA)
        mean = alpha / (alpha + beta_val)

        if evidence < MIN_SAMPLING_EVIDENCE:
            # Too little feedback to explore: deterministic, neutral (1.0) with no evidence
            sample = mean
        else:
            sample = random.betavariate(alpha, beta_val)
        # Centered on 1.0 and bounded so feedback re-orders lessons without
        # outweighing relevance
        factor = 0.8 + 0.4 * sample

        reranked.append({
            **candidate,
            "thompson_sample": sample,
            "thompson_alpha": alpha,
            "thompson_beta": beta_val,
            "thompson_mean": mean,
            "final_score": retrieval_score * factor,
        })

    reranked.sort(key=lambda x: -x["final_score"])
    return reranked


# ── Feedback Attribution ──────────────────────────────────────────────────


def attribute_feedback(
    sentiment: str,
    injected_lessons: list[dict[str, Any]],
    now: float | None = None,
) -> list[tuple[str, float, float]]:
    """Attribute user feedback to injected lessons using relevance-weighted fractional credit.

    When 3 lessons were injected and user gives :), each lesson gets proportional
    credit based on how relevant it was and how recently it was injected.

    Args:
        sentiment: "positive" or "negative"
        injected_lessons: list of dicts with 'id', 'score' (retrieval relevance),
                          optional 'injection_timestamp'
        now: current timestamp

    Returns:
        list of (lesson_id, alpha_delta, beta_delta) tuples
    """
    if now is None:
        now = time.time()

    if not injected_lessons:
        return []

    attr_lambda = math.log(2) / ATTRIBUTION_HALF_LIFE

    weights = []
    for lesson in injected_lessons:
        elapsed = now - lesson.get("injection_timestamp", now)
        time_weight = math.exp(-attr_lambda * elapsed)
        relevance_weight = max(0.1, lesson.get("score", 0.5))
        weights.append(time_weight * relevance_weight)

    total = sum(weights)
    if total == 0:
        normalized = [1.0 / len(weights)] * len(weights)
    else:
        normalized = [w / total for w in weights]

    results = []
    for lesson, weight in zip(injected_lessons, normalized):
        lesson_id = lesson.get("id") or lesson.get("source_id", "")
        if not lesson_id:
            continue
        if sentiment == "positive":
            results.append((lesson_id, weight, 0.0))
        else:
            results.append((lesson_id, 0.0, weight))

    return results


# ── Database Operations ───────────────────────────────────────────────────


def load_thompson_batch(
    storage_path: Path | str,
    lesson_ids: list[str],
) -> dict[str, dict[str, float]]:
    """Load Thompson state for a batch of lesson IDs."""
    if not lesson_ids:
        return {}

    with open_store(storage_path) as conn:
        ensure_thompson_table(conn)
        placeholders = ",".join("?" for _ in lesson_ids)
        rows = conn.execute(
            f"""
            SELECT lesson_id, alpha, beta, last_updated, injection_count, last_injected
              FROM lesson_thompson
             WHERE lesson_id IN ({placeholders})
               AND context_key = '_global'
            """,
            lesson_ids,
        ).fetchall()

        return {
            str(row["lesson_id"]): {
                "alpha": float(row["alpha"]),
                "beta": float(row["beta"]),
                "last_updated": float(row["last_updated"]),
                "injection_count": int(row["injection_count"]),
                "last_injected": float(row["last_injected"]) if row["last_injected"] else None,
            }
            for row in rows
        }


def update_thompson_batch(
    storage_path: Path | str,
    attributions: list[tuple[str, float, float]],
) -> None:
    """Update Thompson alpha/beta for a batch of lessons from feedback attribution."""
    if not attributions:
        return

    now = time.time()
    with open_store(storage_path) as conn:
        ensure_thompson_table(conn)
        for lesson_id, alpha_delta, beta_delta in attributions:
            existing = conn.execute(
                """
                SELECT alpha, beta, last_updated
                  FROM lesson_thompson
                 WHERE lesson_id = ? AND context_key = '_global'
                """,
                (lesson_id,),
            ).fetchone()

            if existing is None:
                conn.execute(
                    """
                    INSERT INTO lesson_thompson(lesson_id, context_key, alpha, beta, last_updated, injection_count)
                    VALUES (?, '_global', ?, ?, ?, 0)
                    """,
                    (lesson_id, DEFAULT_ALPHA + alpha_delta, DEFAULT_BETA + beta_delta, now),
                )
            else:
                # Apply decay first, then add new observation
                decayed_alpha, decayed_beta = decayed_counts(
                    {
                        "alpha": float(existing["alpha"]),
                        "beta": float(existing["beta"]),
                        "last_updated": float(existing["last_updated"]),
                    },
                    now,
                )
                new_alpha = decayed_alpha + alpha_delta
                new_beta = decayed_beta + beta_delta
                conn.execute(
                    """
                    UPDATE lesson_thompson
                       SET alpha = ?, beta = ?, last_updated = ?
                     WHERE lesson_id = ? AND context_key = '_global'
                    """,
                    (new_alpha, new_beta, now, lesson_id),
                )
        conn.commit()


def record_injection(
    storage_path: Path | str,
    lesson_ids: list[str],
) -> None:
    """Record that lessons were injected (for tracking injection_count and last_injected)."""
    if not lesson_ids:
        return

    now = time.time()
    with open_store(storage_path) as conn:
        ensure_thompson_table(conn)
        for lesson_id in lesson_ids:
            conn.execute(
                """
                INSERT INTO lesson_thompson(lesson_id, context_key, alpha, beta, last_updated, injection_count, last_injected)
                VALUES (?, '_global', ?, ?, ?, 1, ?)
                ON CONFLICT(lesson_id, context_key) DO UPDATE SET
                    injection_count = injection_count + 1,
                    last_injected = ?
                """,
                (lesson_id, DEFAULT_ALPHA, DEFAULT_BETA, now, now, now),
            )
        conn.commit()


def migrate_from_lesson_scores(storage_path: Path | str) -> int:
    """One-time migration: seed lesson_thompson from existing ups/downs in lesson_scores."""
    now = time.time()
    count = 0
    with open_store(storage_path) as conn:
        ensure_thompson_table(conn)
        rows = conn.execute(
            "SELECT lesson_id, ups, downs FROM lesson_scores"
        ).fetchall()

        for row in rows:
            lesson_id = str(row["lesson_id"])
            ups = int(row["ups"] or 0)
            downs = int(row["downs"] or 0)

            existing = conn.execute(
                "SELECT 1 FROM lesson_thompson WHERE lesson_id = ? AND context_key = '_global'",
                (lesson_id,),
            ).fetchone()

            if existing is None:
                conn.execute(
                    """
                    INSERT INTO lesson_thompson(lesson_id, context_key, alpha, beta, last_updated, injection_count)
                    VALUES (?, '_global', ?, ?, ?, ?)
                    """,
                    (lesson_id, DEFAULT_ALPHA + ups, DEFAULT_BETA + downs, now, ups + downs),
                )
                count += 1
        conn.commit()
    return count
