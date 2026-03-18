#!/usr/bin/env python3
"""
SmartAssist MCP Server - Exposes the RLHF knowledge base as tools for Claude Code.

Provides tools:
  - rag_search: Semantic search across past lessons, corrections, and feedback
  - rag_dashboard: View current reliability scores and system stats
  - rag_feedback: Record feedback on suggestions
  - create_lesson: Create new project-specific lesson (dual-path: feedback_log + curated)
  - boost_lesson: Increase a lesson's score (V2 per-lesson feedback)
  - demote_lesson: Decrease a lesson's score, auto-retire if warranted
  - merge_lessons: Consolidate overlapping lessons into one

Runs via stdio transport. Entry point: `smartassist serve`
"""

import math
import json
import sys
import time
import subprocess
import logging
from typing import Optional
from datetime import datetime

from mcp.server.fastmcp import FastMCP

from smartassist.config import (
    EMBEDDING_MODEL, EMBEDDING_DIM, get_storage_path, get_db_path,
    atomic_write_json, locked_update_json, spawn_managed,
)
from smartassist.lesson_feedback import (
    load_lesson_scores,
    save_lesson_scores,
    get_or_create_score,
    add_to_curated,
    remove_from_curated,
    log_comparison_entry,
    DEFAULT_BOOST,
    BOOST_INCREMENT,
    DEMOTE_DECREMENT,
    BOOST_CAP,
    BOOST_FLOOR,
    MAX_CURATED_LESSONS,
    ACTION_VERBS,
    GENERIC_STARTS,
)

# Suppress noisy logs from dependencies during MCP startup
logging.getLogger("sentence_transformers").setLevel(logging.WARNING)
logging.getLogger("transformers").setLevel(logging.WARNING)
logging.getLogger("huggingface_hub").setLevel(logging.WARNING)

# ── Lazy-loaded singletons ─────────────────────────────────────────────────
_embedder = None
_db_table = None
_thompson = None
_cross_encoder = None

# Results above this distance are too irrelevant to return.
MAX_DISTANCE = 1.30

VALID_CATEGORIES = {"testing", "code_edit", "git", "architecture", "pr_review", "security", "debugging"}



def _get_storage():
    try:
        return get_storage_path()
    except RuntimeError:
        raise RuntimeError(
            "SmartAssist data directory not found. "
            "Run 'smartassist setup' in your project root, or set "
            "SMARTASSIST_DATA_DIR in your environment."
        )


def _get_db():
    try:
        return get_db_path()
    except RuntimeError:
        raise RuntimeError(
            "SmartAssist database not found. "
            "Run 'smartassist setup' in your project root."
        )


def _usage_log_path():
    return _get_storage() / "usage_log.jsonl"


def _trigger_vectorization(full_rebuild: bool = False):
    """Refresh the vector store after corpus changes."""
    module = (
        "smartassist.tools.cleanup_and_vectorize"
        if full_rebuild
        else "smartassist.hooks.vectorize_learnings"
    )
    try:
        spawn_managed([sys.executable, "-m", module])
    except Exception:
        pass


def _extract_lesson_text(raw_text: str) -> str:
    """Extract the main lesson from raw vectorized text."""
    if raw_text.startswith("["):
        bracket_end = raw_text.find("] ")
        if bracket_end != -1:
            text = raw_text[bracket_end + 2:]
            ctx_idx = text.find(" Context: ")
            if ctx_idx != -1:
                text = text[:ctx_idx]
            return text.strip()

    for line in raw_text.split("\n"):
        stripped = line.strip()
        if stripped.startswith("Correction:"):
            return stripped.replace("Correction:", "").strip()
    for line in raw_text.split("\n"):
        stripped = line.strip()
        if stripped.startswith("Agent Response:"):
            return stripped.replace("Agent Response:", "").strip()
    return ""


def _enhance_query(query: str) -> str:
    """Transform a user query to better match stored document embeddings."""
    query = query.strip()
    if not query:
        return query
    return f"Correction for this project: {query}"


def _compute_relevance(distance: float) -> float:
    """Convert L2 distance to a 0-1 relevance score using sqrt scaling."""
    if distance <= 0:
        return 1.0
    if distance >= MAX_DISTANCE:
        return 0.0
    normalized = distance / MAX_DISTANCE
    return math.sqrt(1 - normalized)


def _log_usage(tool: str, query: str = "", results_count: int = 0, latency_ms: float = 0,
               lessons: list = None, search_meta: dict = None):
    """Append a usage event to the usage log for evidence tracking."""
    try:
        entry = {
            "timestamp": datetime.now().isoformat(),
            "tool": tool,
            "query": query[:200] if query else "",
            "results_count": results_count,
            "latency_ms": round(latency_ms, 1),
        }
        if lessons is not None:
            entry["lessons"] = lessons
        if search_meta is not None:
            entry["search_meta"] = search_meta
        with open(_usage_log_path(), "a", encoding="utf-8") as f:
            f.write(json.dumps(entry) + "\n")
    except Exception:
        pass  # Never let logging break the tool


def _get_embedder():
    """Lazy-load the sentence transformer embedding model."""
    global _embedder
    if _embedder is None:
        from sentence_transformers import SentenceTransformer
        _embedder = SentenceTransformer(EMBEDDING_MODEL)
    return _embedder


def _get_table():
    """Lazy-load the LanceDB documents table."""
    global _db_table
    if _db_table is None:
        import lancedb
        db = lancedb.connect(str(_get_db()))
        _db_table = db.open_table("documents")
    return _db_table


def _get_thompson():
    """Lazy-load the Thompson Sampling model."""
    global _thompson
    if _thompson is None:
        from smartassist.thompson_sampling import ThompsonSamplingModel
        _thompson = ThompsonSamplingModel(str(_get_storage()))
    return _thompson


def _get_cross_encoder():
    """Lazy-load the cross-encoder reranker model."""
    global _cross_encoder
    if _cross_encoder is None:
        from sentence_transformers import CrossEncoder
        _cross_encoder = CrossEncoder("cross-encoder/ms-marco-MiniLM-L-6-v2")
    return _cross_encoder


def _get_feedback_stats():
    """Read feedback stats from the JSONL log."""
    log_file = _get_storage() / "feedback_log.jsonl"
    if not log_file.exists():
        return {"total_events": 0, "by_category": {}, "by_signal": {}}

    total = 0
    by_cat = {}
    by_sig = {}
    with open(log_file, "r", encoding="utf-8") as f:
        for line in f:
            if not line.strip():
                continue
            total += 1
            try:
                entry = json.loads(line)
                cat = entry.get("category", "unknown")
                sig = entry.get("signal", "unknown")
                by_cat[cat] = by_cat.get(cat, 0) + 1
                by_sig[sig] = by_sig.get(sig, 0) + 1
            except json.JSONDecodeError:
                continue

    return {"total_events": total, "by_category": by_cat, "by_signal": by_sig}


# ── V2 Shared Helpers ──────────────────────────────────────────────────────


def _update_thompson_for_lesson(lesson_id, storage, success=True):
    """Look up lesson's category in curated_lessons.json and update Thompson Sampling."""
    curated_path = storage / "curated_lessons.json"
    if not curated_path.exists():
        return
    try:
        lessons = json.loads(curated_path.read_text())
        cat = None
        for l in lessons:
            if l.get("id") == lesson_id:
                cat = l.get("category")
                break
        if not cat:
            return
        thompson = _get_thompson()
        if success:
            thompson.record_success(cat, 3)
        else:
            thompson.record_failure(cat, 3)
    except Exception:
        pass


def _write_to_live_log(storage, action, message):
    """Append a tool action result to rag_live.log for the monitor terminal."""
    try:
        live_log = storage / "rag_live.log"
        now = datetime.now().strftime("%H:%M:%S")
        color = {
            "boost": "\033[32m",    # green
            "demote": "\033[31m",   # red
            "merge": "\033[33m",    # yellow
            "create": "\033[36m",   # cyan
            "retire": "\033[31m",   # red
        }.get(action, "\033[0m")
        line = f"  {color}{action.upper()}: {message}\033[0m\n"
        with open(live_log, "a", encoding="utf-8") as f:
            f.write(line)
    except Exception:
        pass


def _load_curated_lessons(storage):
    """Load curated lessons list; return [] if missing/unreadable."""
    curated_path = storage / "curated_lessons.json"
    if not curated_path.exists():
        return []
    try:
        lessons = json.loads(curated_path.read_text())
        return lessons if isinstance(lessons, list) else []
    except Exception:
        return []


def _curated_lesson_ids(storage):
    """Return set of curated lesson IDs."""
    return {l.get("id") for l in _load_curated_lessons(storage)}


def _update_feedback_metrics(storage, action_type):
    """Increment action count in feedback_metrics.json."""
    metrics_path = storage / "feedback_metrics.json"
    default = {
        "boosts": 0, "demotes": 0, "creates": 0, "merges": 0,
        "positive_signals": 0, "negative_signals": 0,
        "last_updated": None,
    }
    try:
        def _increment(metrics):
            if not isinstance(metrics, dict):
                metrics = dict(default)
            metrics[action_type] = metrics.get(action_type, 0) + 1
            metrics["last_updated"] = datetime.now().isoformat()
            return metrics
        locked_update_json(metrics_path, _increment, default=default)
    except Exception:
        pass


# ── MCP Server ──────────────────────────────────────────────────────────────

mcp = FastMCP("smartassist")


@mcp.tool()
def rag_search(
    query: str,
    top_k: int = 5,
    category: Optional[str] = None,
) -> str:
    """Search the project knowledge base for relevant lessons, past corrections, and best practices.

    Use BEFORE making decisions about testing, git, code editing, PR reviews,
    architecture, or security. Proactively check for project-specific rules
    rather than waiting for the user to ask.

    Use this tool when:
    - About to write tests, style components, make git commits, or edit code
    - The user asks about project conventions or best practices
    - You need to check for past mistakes or corrections on a topic
    - Before any action that might have project-specific rules

    Do NOT use for:
    - Simple confirmations ("yes", "ok", "do it")
    - Reading specific files (use Read tool instead)
    - General programming questions unrelated to this project

    Args:
        query: Natural language description of what you're looking for.
               Examples: "how to style components", "testing best practices",
               "git commit format", "what went wrong with analytics"
        top_k: Number of results to return (1-10, default 5)
        category: Optional filter by category. One of:
                  testing, code_edit, git, architecture, pr_review, security
    """
    top_k = max(1, min(top_k, 10))
    t0 = time.time()

    try:
        embedder = _get_embedder()
        table = _get_table()
    except Exception:
        _log_usage("rag_search", query, 0, 0)
        return "Error initializing search. Run 'smartassist health' to diagnose."

    enhanced_query = _enhance_query(query)
    query_vector = embedder.encode(enhanced_query)

    RERANK_POOL = 20
    try:
        from lancedb.rerankers import LinearCombinationReranker
        reranker = LinearCombinationReranker(weight=0.7)
        results = (
            table.search(query_vector, query_type="hybrid")
            .rerank(reranker=reranker)
            .limit(RERANK_POOL)
            .to_list()
        )
    except Exception:
        results = table.search(query_vector).limit(RERANK_POOL).to_list()

    raw_count = len(results)

    results = [r for r in results if r.get("_distance", 99) <= MAX_DISTANCE]
    distance_filtered = raw_count - len(results)

    if results and len(results) > 1:
        try:
            cross_encoder = _get_cross_encoder()
            pairs = [[query, r.get("text", "")] for r in results]
            scores = cross_encoder.predict(pairs)
            for r, score in zip(results, scores):
                r["_rerank_score"] = float(score)
            results.sort(key=lambda r: r.get("_rerank_score", 0), reverse=True)
        except Exception:
            pass

    category_filter_used = None
    category_filtered = 0
    if category:
        category_lower = category.lower()
        category_filter_used = category_lower
        before_cat = len(results)
        results = [
            r for r in results
            if category_lower in r.get("category", "").lower()
            or category_lower in r.get("text", "").lower()
        ]
        category_filtered = before_cat - len(results)

    results = results[:top_k]
    latency = (time.time() - t0) * 1000

    lessons_log = []
    for r in results:
        distance = r.get("_distance", 0)
        relevance = _compute_relevance(distance)
        lesson_text = _extract_lesson_text(r.get("text", ""))
        lessons_log.append({
            "category": r.get("category", "unknown"),
            "relevance_pct": round(relevance * 100),
            "lesson_text": lesson_text[:120],
        })

    search_meta = {
        "raw_count": raw_count,
        "distance_filtered": distance_filtered,
        "category_filtered": category_filtered,
        "category_filter_used": category_filter_used,
        "enhanced_query": enhanced_query,
    }

    _log_usage("rag_search", query, len(results), latency,
               lessons=lessons_log, search_meta=search_meta)

    if not results:
        return f"No relevant lessons found for: {query}"

    output_parts = [f"Found {len(results)} relevant lesson(s) for: \"{query}\"\n"]

    for i, result in enumerate(results, 1):
        raw_text = result.get("text", "").strip()
        distance = result.get("_distance", 0)
        cat = result.get("category", "unknown")
        lesson = _format_lesson(raw_text, cat, distance)
        output_parts.append(lesson)

    return "\n".join(output_parts)


def _format_lesson(raw_text: str, category: str, distance: float) -> str:
    """Extract actionable content from raw vectorized text."""
    lesson_text = _extract_lesson_text(raw_text)
    context = ""

    if raw_text.startswith("["):
        ctx_idx = raw_text.find(" Context: ")
        if ctx_idx != -1:
            bracket_end = raw_text.find("] ")
            full_text = raw_text[bracket_end + 2:] if bracket_end != -1 else raw_text
            ctx_idx2 = full_text.find(" Context: ")
            if ctx_idx2 != -1:
                context = full_text[ctx_idx2 + 10:].strip()
    else:
        for line in raw_text.split("\n"):
            stripped = line.strip()
            if stripped.startswith("Context:"):
                context = stripped.replace("Context:", "").strip()

    relevance = _compute_relevance(distance)
    parts = [f"  [{category}] (relevance: {relevance:.0%})"]

    if lesson_text:
        parts.append(f"    Lesson: {lesson_text[:300]}")
    if context:
        parts.append(f"    Context: {context[:150]}")
    parts.append("")

    return "\n".join(parts)


@mcp.tool()
def rag_dashboard() -> str:
    """View current RLHF system reliability scores, corpus stats, and feedback metrics.

    Shows performance metrics across all tracked categories (testing, git,
    code_edit, etc.) with Thompson Sampling reliability scores. Also shows
    corpus capacity and feedback action breakdown.
    """
    t0 = time.time()
    try:
        thompson = _get_thompson()
    except Exception as e:
        _log_usage("rag_dashboard")
        return f"Error loading Thompson model: {e}"

    scores_data = thompson.get_all_reliabilities()
    weak = thompson.get_weak_categories(threshold=0.70)
    stats = _get_feedback_stats()
    _log_usage("rag_dashboard", "", 0, (time.time() - t0) * 1000)

    lines = ["RLHF System Dashboard", "=" * 40, ""]

    lines.append("Reliability by Category:")
    for cat, score in sorted(scores_data.items(), key=lambda x: x[1]):
        status = "WEAK" if cat in weak else "OK"
        bar = "#" * int(score * 20) + "." * (20 - int(score * 20))
        lines.append(f"  {cat:15s} [{bar}] {score:.1%}  ({status})")

    lines.append("")
    lines.append(f"Weak categories (<70%): {', '.join(weak) if weak else 'None'}")

    # V2: Corpus stats
    try:
        storage = _get_storage()
        curated_path = storage / "curated_lessons.json"
        if curated_path.exists():
            curated = json.loads(curated_path.read_text())
            total_lessons = len(curated)
        else:
            total_lessons = 0

        lesson_scores = load_lesson_scores()
        active_count = total_lessons
        retired_count = sum(1 for s in lesson_scores.values() if s.get("retired", False))

        pct = int((total_lessons / MAX_CURATED_LESSONS) * 100) if MAX_CURATED_LESSONS > 0 else 0
        lines.append("")
        lines.append(f"Corpus: {total_lessons}/{MAX_CURATED_LESSONS} lessons ({pct}% capacity)")
        lines.append(f"  Active: {active_count}  |  Retired: {retired_count}")
    except Exception:
        pass

    # V2: Feedback metrics
    try:
        metrics_path = storage / "feedback_metrics.json"
        if metrics_path.exists():
            metrics = json.loads(metrics_path.read_text())
            lines.append("")
            lines.append("Feedback Actions:")
            lines.append(f"  Boosts: {metrics.get('boosts', 0)}  |  Demotes: {metrics.get('demotes', 0)}  |  "
                         f"Creates: {metrics.get('creates', 0)}  |  Merges: {metrics.get('merges', 0)}")
            pos = metrics.get("positive_signals", 0)
            neg = metrics.get("negative_signals", 0)
            lines.append(f"  Total signals: {pos + neg} (positive: {pos}, negative: {neg})")
    except Exception:
        pass

    lines.append("")
    lines.append(f"Total feedback events: {stats['total_events']}")
    if stats["by_signal"]:
        lines.append("By signal:")
        for sig, count in sorted(stats["by_signal"].items(), key=lambda x: -x[1]):
            lines.append(f"  {sig}: {count}")

    return "\n".join(lines)


@mcp.tool()
def rag_feedback(
    helpful: bool,
    category: str = "",
    notes: str = "",
) -> str:
    """Record whether the last RAG suggestion or action was helpful.

    Call this when the user gives quality feedback:
    - "that was wrong" / "don't do that" -> helpful=False
    - "good job" / "that's correct" -> helpful=True
    - Any explicit quality signal about a suggestion

    Args:
        helpful: True if the suggestion/action was helpful, False if not
        category: Category of the feedback (testing, code_edit, git,
                  architecture, pr_review, security). Optional.
        notes: Optional notes about what was right/wrong
    """
    t0 = time.time()
    try:
        thompson = _get_thompson()
    except Exception as e:
        return f"Error loading feedback system: {e}"

    valid_categories = VALID_CATEGORIES
    cat = category.lower().strip() if category else ""

    if cat and cat not in valid_categories:
        return f"Unknown category '{category}'. Valid: {', '.join(sorted(valid_categories))}"

    if cat:
        if helpful:
            thompson.record_success(cat, intensity=3)
        else:
            thompson.record_failure(cat, intensity=3)

    feedback_entry = {
        "timestamp": time.time(),
        "signal": "thumbs_up" if helpful else "thumbs_down",
        "category": cat or "unknown",
        "intensity": 3,
        "query": "",
        "response": "",
        "correction": notes,
        "context": "rag_feedback MCP tool",
    }

    feedback_log = _get_storage() / "feedback_log.jsonl"
    try:
        with open(feedback_log, "a", encoding="utf-8") as f:
            f.write(json.dumps(feedback_entry) + "\n")
    except Exception:
        pass

    latency = (time.time() - t0) * 1000
    _log_usage("rag_feedback", f"helpful={helpful} cat={cat}", 0, latency)

    if cat:
        score = thompson.get_reliability(cat)
        return f"Feedback recorded: {'helpful' if helpful else 'not helpful'} for {cat} (reliability: {score:.1%})"
    else:
        return f"Feedback recorded: {'helpful' if helpful else 'not helpful'}"


@mcp.tool()
def create_lesson(
    lesson: str,
    category: str,
    sentiment: str = "positive",
    intensity: int = 3,
    context: str = "",
) -> str:
    """Create a new project-specific lesson from user feedback.

    Called by Claude after detecting a feedback signal (:), :(, thumbs_up, etc.).
    The lesson should be specific to this project, not generic programming advice.

    Args:
        lesson: Imperative statement (>30 chars) with an action verb.
                Example: "Always use semantic colors from theme instead of hardcoded hex values"
        category: One of: testing, code_edit, git, architecture, pr_review, security, debugging
        sentiment: "positive" or "negative"
        intensity: Importance level 1-5 (clamped)
        context: Brief context about what Claude was doing when feedback was given
    """
    t0 = time.time()

    # ── Quality gate: category ────────────────────────────────────────
    cat = category.lower().strip() if category else ""
    if cat not in VALID_CATEGORIES:
        return f"Invalid category '{category}'. Must be one of: {', '.join(sorted(VALID_CATEGORIES))}"

    # ── Quality gate: sentiment ───────────────────────────────────────
    sentiment = sentiment.lower().strip() if sentiment else ""
    if sentiment not in ("positive", "negative"):
        return f"Invalid sentiment '{sentiment}'. Must be 'positive' or 'negative'."

    # ── Quality gate: intensity ───────────────────────────────────────
    try:
        intensity = int(intensity)
    except (TypeError, ValueError):
        return f"Invalid intensity '{intensity}'. Must be an integer from 1 to 5."
    intensity = max(1, min(5, intensity))

    # ── Quality gate: lesson length ───────────────────────────────────
    lesson = lesson.strip() if lesson else ""
    if len(lesson) < 30:
        return f"Lesson too short ({len(lesson)} chars). Must be at least 30 characters. Be specific and actionable."

    # ── Quality gate: reject generic starts ───────────────────────────
    lesson_lower = lesson.lower()
    for generic in GENERIC_STARTS:
        if lesson_lower.startswith(generic):
            return f"Lesson starts with generic phrase '{generic}'. Be project-specific and actionable."

    # ── Quality gate: must contain action verb ────────────────────────
    has_verb = any(f" {verb} " in f" {lesson_lower} " or lesson_lower.startswith(f"{verb} ")
                    for verb in ACTION_VERBS)
    if not has_verb:
        return (
            f"Lesson must contain an action verb. Include one of: "
            f"{', '.join(sorted(list(ACTION_VERBS)[:10]))}..."
        )

    # ── Store to feedback_log.jsonl ───────────────────────────────────
    try:
        storage_path = _get_storage()
    except Exception as e:
        return f"Error accessing storage: {e}"

    # ── V2: Dual-path write — add to curated first to avoid partial state ─
    new_id, cap_error = add_to_curated(storage_path, lesson, cat)
    if cap_error:
        return f"Cannot create lesson: {cap_error}"

    signal = "thumbs_up" if sentiment == "positive" else "correction"
    feedback_entry = {
        "timestamp": time.time(),
        "signal": signal,
        "category": cat,
        "intensity": intensity,
        "query": "",
        "response": "",
        "correction": lesson,
        "context": context or "create_lesson MCP tool",
    }
    feedback_log = storage_path / "feedback_log.jsonl"
    try:
        with open(feedback_log, "a", encoding="utf-8") as f:
            f.write(json.dumps(feedback_entry) + "\n")
    except Exception as e:
        # Roll back curated write if feedback log append fails.
        remove_from_curated(storage_path, new_id)
        return f"Error writing feedback: {e}"

    curated_msg = f" [ID: {new_id}]"

    # ── Thompson Sampling update ──────────────────────────────────────
    try:
        thompson = _get_thompson()
        if sentiment == "positive":
            thompson.record_success(cat, intensity)
        else:
            thompson.record_failure(cat, intensity)
    except Exception:
        pass  # Don't fail if Thompson model has issues

    # ── Fire-and-forget vectorization ─────────────────────────────────
    _trigger_vectorization()

    # ── Update feedback metrics ───────────────────────────────────────
    _update_feedback_metrics(storage_path, "creates")
    signal_key = "positive_signals" if sentiment == "positive" else "negative_signals"
    _update_feedback_metrics(storage_path, signal_key)

    latency = (time.time() - t0) * 1000
    _log_usage("create_lesson", lesson[:100], 1, latency)

    icon = "+" if sentiment == "positive" else "!"
    result = f"Lesson recorded {icon} [{cat}] {lesson[:80]}{'...' if len(lesson) > 80 else ''}{curated_msg}"
    try:
        _write_to_live_log(storage_path, "create", result)
    except Exception:
        pass
    return result


@mcp.tool()
def compare_lesson(
    lesson: str,
    category: str,
    sentiment: str = "positive",
    context: str = "",
) -> str:
    """Draft a lesson for quality comparison (does NOT store to knowledge base).

    Called when the hook detects feedback with context. Your lesson will be
    compared against the hook's automated version to evaluate which path
    produces better lessons.

    Same requirements as create_lesson — imperative, project-specific, >30 chars.

    Args:
        lesson: Imperative statement (>30 chars) with an action verb.
        category: One of: testing, code_edit, git, architecture, pr_review, security, debugging
        sentiment: "positive" or "negative"
        context: The user's original feedback context (passed through from the hook)
    """
    # ── Quality gate: category ────────────────────────────────────────
    cat = category.lower().strip() if category else ""
    if cat not in VALID_CATEGORIES:
        return f"Invalid category '{category}'. Must be one of: {', '.join(sorted(VALID_CATEGORIES))}"

    # ── Quality gate: sentiment ───────────────────────────────────────
    sentiment = sentiment.lower().strip() if sentiment else ""
    if sentiment not in ("positive", "negative"):
        return f"Invalid sentiment '{sentiment}'. Must be 'positive' or 'negative'."

    # ── Quality gate: lesson length ───────────────────────────────────
    lesson = lesson.strip() if lesson else ""
    if len(lesson) < 30:
        try:
            storage = _get_storage()
            log_comparison_entry(storage, "claude", sentiment, context, lesson, False)
        except Exception:
            pass
        return f"Lesson too short ({len(lesson)} chars). Must be at least 30 characters."

    # ── Quality gate: reject generic starts ───────────────────────────
    lesson_lower = lesson.lower()
    for generic in GENERIC_STARTS:
        if lesson_lower.startswith(generic):
            try:
                storage = _get_storage()
                log_comparison_entry(storage, "claude", sentiment, context, lesson, False)
            except Exception:
                pass
            return f"Lesson starts with generic phrase '{generic}'."

    # ── Quality gate: must contain action verb ────────────────────────
    has_verb = any(f" {verb} " in f" {lesson_lower} " or lesson_lower.startswith(f"{verb} ")
                    for verb in ACTION_VERBS)
    if not has_verb:
        try:
            storage = _get_storage()
            log_comparison_entry(storage, "claude", sentiment, context, lesson, False)
        except Exception:
            pass
        return (
            f"Lesson must contain an action verb. Include one of: "
            f"{', '.join(sorted(list(ACTION_VERBS)[:10]))}..."
        )

    # ── Log to comparison file (no curated write, no vectorization) ───
    try:
        storage = _get_storage()
        log_comparison_entry(storage, "claude", sentiment, context, lesson, True)
    except Exception:
        pass

    return f"Comparison logged [{cat}] {lesson[:80]}{'...' if len(lesson) > 80 else ''} (not stored — A/B only)"


@mcp.tool()
def boost_lesson(lesson_id: str) -> str:
    """Boost a lesson's score after positive feedback.

    Call this when a specific injected lesson was helpful and should be
    prioritized more in future injections.

    Args:
        lesson_id: The lesson ID (e.g., "L001") to boost
    """
    t0 = time.time()
    lesson_id = lesson_id.strip().upper()

    scores = load_lesson_scores()
    existing = scores.get(lesson_id, {})

    if existing.get("retired", False):
        return f"Cannot boost {lesson_id}: lesson is retired."

    if existing.get("blocked", False):
        return f"Cannot boost {lesson_id}: lesson is blocked. Unblock first."

    storage = _get_storage()
    if lesson_id not in _curated_lesson_ids(storage):
        return f"Cannot boost {lesson_id}: lesson not found in curated lessons."

    entry = get_or_create_score(scores, lesson_id)

    entry["ups"] += 1
    old_boost = entry["boost"]
    entry["boost"] = min(entry["boost"] + BOOST_INCREMENT, BOOST_CAP)
    save_lesson_scores(scores)

    # Thompson Sampling update
    try:
        _update_thompson_for_lesson(lesson_id, storage, success=True)
        _update_feedback_metrics(storage, "boosts")
        _update_feedback_metrics(storage, "positive_signals")
    except Exception:
        pass

    latency = (time.time() - t0) * 1000
    _log_usage("boost_lesson", lesson_id, 0, latency)

    result = (f"Boosted {lesson_id}: {old_boost:.1f}x → {entry['boost']:.1f}x "
              f"(ups: {entry['ups']}, downs: {entry['downs']})")
    try:
        _write_to_live_log(_get_storage(), "boost", result)
    except Exception:
        pass
    return result


@mcp.tool()
def demote_lesson(lesson_id: str) -> str:
    """Demote a lesson's score after negative feedback.

    Call this when a specific injected lesson was harmful, irrelevant, or wrong.
    If the lesson has never been helpful (0 ups) and reaches 0.0 boost, it will
    be automatically retired and removed from the corpus.

    Args:
        lesson_id: The lesson ID (e.g., "L001") to demote
    """
    t0 = time.time()
    lesson_id = lesson_id.strip().upper()

    scores = load_lesson_scores()
    existing = scores.get(lesson_id, {})

    if existing.get("blocked", False):
        return f"Cannot demote {lesson_id}: lesson is already blocked."

    if existing.get("retired", False):
        return f"Cannot demote {lesson_id}: lesson is already retired."

    storage = _get_storage()
    if lesson_id not in _curated_lesson_ids(storage):
        return f"Cannot demote {lesson_id}: lesson not found in curated lessons."

    entry = get_or_create_score(scores, lesson_id)

    entry["downs"] += 1
    old_boost = entry["boost"]
    entry["boost"] = max(entry["boost"] - DEMOTE_DECREMENT, BOOST_FLOOR)

    # Auto-retire check: boost at 0.0 AND never been helpful
    auto_retired = False
    if entry["boost"] <= BOOST_FLOOR and entry.get("ups", 0) == 0:
        entry["blocked"] = True
        entry["retired"] = True
        entry["retired_reason"] = "auto-retired: boost 0.0 with 0 positive feedback"
        entry["retired_at"] = datetime.now().isoformat()
        auto_retired = True

    save_lesson_scores(scores)

    # Remove from curated if auto-retired
    if auto_retired:
        try:
            remove_from_curated(storage, lesson_id)
        except Exception:
            pass
        _trigger_vectorization(full_rebuild=True)

    # Thompson Sampling update
    try:
        _update_thompson_for_lesson(lesson_id, storage, success=False)
        _update_feedback_metrics(storage, "demotes")
        _update_feedback_metrics(storage, "negative_signals")
    except Exception:
        pass

    latency = (time.time() - t0) * 1000
    _log_usage("demote_lesson", lesson_id, 0, latency)

    # Warning for lessons with strong positive history
    warning = ""
    if entry.get("ups", 0) >= 5:
        warning = f" (Warning: this lesson has {entry['ups']} positive feedbacks — consider merging instead)"

    if auto_retired:
        result = (f"RETIRED {lesson_id}: {old_boost:.1f}x → {entry['boost']:.1f}x "
                  f"(ups: {entry['ups']}, downs: {entry['downs']}) — "
                  f"removed from corpus (never had positive feedback)")
        try:
            _write_to_live_log(_get_storage(), "retire", result)
        except Exception:
            pass
        return result
    else:
        result = (f"Demoted {lesson_id}: {old_boost:.1f}x → {entry['boost']:.1f}x "
                  f"(ups: {entry['ups']}, downs: {entry['downs']}){warning}")
        try:
            _write_to_live_log(_get_storage(), "demote", result)
        except Exception:
            pass
        return result


@mcp.tool()
def merge_lessons(lesson_ids: str, new_lesson: str, category: str) -> str:
    """Merge multiple overlapping lessons into a single consolidated lesson.

    Call this when two or more injected lessons cover the same topic and should
    be combined into one stronger, clearer lesson.

    Args:
        lesson_ids: Comma-separated lesson IDs to merge (e.g., "L001,L002,L003")
        new_lesson: The consolidated lesson text (>30 chars, imperative, with action verb)
        category: Category for the new lesson (testing, code_edit, git, etc.)
    """
    t0 = time.time()

    # Parse IDs
    ids = [lid.strip().upper() for lid in lesson_ids.split(",") if lid.strip()]
    if len(ids) < 2:
        return "Merge requires at least 2 lesson IDs (comma-separated)."
    if len(set(ids)) != len(ids):
        return "Merge requires unique lesson IDs (no duplicates)."

    # Validate category
    cat = category.lower().strip() if category else ""
    if cat not in VALID_CATEGORIES:
        return f"Invalid category '{category}'. Must be one of: {', '.join(sorted(VALID_CATEGORIES))}"

    # Quality gates on new_lesson (same as create_lesson)
    new_lesson = new_lesson.strip() if new_lesson else ""
    if len(new_lesson) < 30:
        return f"New lesson too short ({len(new_lesson)} chars). Must be at least 30 characters."

    lesson_lower = new_lesson.lower()
    for generic in GENERIC_STARTS:
        if lesson_lower.startswith(generic):
            return f"Lesson starts with generic phrase '{generic}'. Be project-specific and actionable."

    has_verb = any(f" {verb} " in f" {lesson_lower} " or lesson_lower.startswith(f"{verb} ")
                    for verb in ACTION_VERBS)
    if not has_verb:
        return (
            f"Lesson must contain an action verb. Include one of: "
            f"{', '.join(sorted(list(ACTION_VERBS)[:10]))}..."
        )

    try:
        storage = _get_storage()
    except Exception as e:
        return f"Error accessing storage: {e}"

    # Verify source lessons exist in curated and perform merge under lock
    curated_path = storage / "curated_lessons.json"
    if not curated_path.exists():
        return f"curated_lessons.json not found. Cannot merge."

    merge_result = {}

    def _do_merge(curated):
        if not isinstance(curated, list):
            merge_result["error"] = "Error reading curated_lessons.json."
            return None

        curated_ids = {l.get("id") for l in curated}
        missing = [lid for lid in ids if lid not in curated_ids]
        if missing:
            merge_result["error"] = f"Cannot merge: lesson(s) {', '.join(missing)} not found in curated lessons."
            return None

        max_num = 0
        for lid in curated_ids:
            if isinstance(lid, str) and lid.startswith("L") and lid[1:].isdigit():
                max_num = max(max_num, int(lid[1:]))
        merge_result["new_id"] = f"L{max_num + 1:03d}"

        merged = [l for l in curated if l.get("id") not in ids]
        if len(merged) >= MAX_CURATED_LESSONS:
            merge_result["error"] = f"Cannot merge: Corpus at capacity ({MAX_CURATED_LESSONS}). Merge or demote lessons first."
            return None

        merged.append({
            "id": merge_result["new_id"],
            "lesson": new_lesson,
            "category": cat,
        })
        return merged

    try:
        locked_update_json(curated_path, _do_merge, default=[])
    except Exception:
        return "Error reading curated_lessons.json."

    if "error" in merge_result:
        return merge_result["error"]

    new_id = merge_result["new_id"]

    # Combine scores from sources
    scores = load_lesson_scores()
    combined_ups = 0
    max_boost = DEFAULT_BOOST
    for lid in ids:
        entry = scores.get(lid, {})
        combined_ups += entry.get("ups", 0)
        max_boost = max(max_boost, entry.get("boost", DEFAULT_BOOST))

    # Set score for new lesson
    new_entry = get_or_create_score(scores, new_id)
    new_entry["ups"] = combined_ups
    new_entry["boost"] = max_boost

    # Mark sources as superseded
    for lid in ids:
        source_entry = get_or_create_score(scores, lid)
        source_entry["blocked"] = True
        source_entry["retired"] = True
        source_entry["retired_reason"] = f"superseded_by={new_id}"
        source_entry["retired_at"] = datetime.now().isoformat()

    save_lesson_scores(scores)

    # Write to feedback_log for vectorization
    feedback_entry = {
        "timestamp": time.time(),
        "signal": "merge",
        "category": cat,
        "intensity": 3,
        "query": "",
        "response": "",
        "correction": new_lesson,
        "context": f"Merged from: {', '.join(ids)}",
    }
    feedback_log = storage / "feedback_log.jsonl"
    try:
        with open(feedback_log, "a", encoding="utf-8") as f:
            f.write(json.dumps(feedback_entry) + "\n")
    except Exception:
        pass

    # Rebuild from curated lessons so superseded source lessons disappear from search.
    _trigger_vectorization(full_rebuild=True)

    # Update metrics
    _update_feedback_metrics(storage, "merges")

    latency = (time.time() - t0) * 1000
    _log_usage("merge_lessons", f"{ids} -> {new_id}", 0, latency)

    result = (f"Merged {', '.join(ids)} → {new_id} [{cat}] "
              f"{new_lesson[:60]}{'...' if len(new_lesson) > 60 else ''} "
              f"(ups: {combined_ups}, boost: {max_boost:.1f}x)")
    try:
        _write_to_live_log(storage, "merge", result)
    except Exception:
        pass
    return result


def serve():
    """Entry point for `smartassist serve`."""
    mcp.run(transport="stdio")


if __name__ == "__main__":
    serve()
