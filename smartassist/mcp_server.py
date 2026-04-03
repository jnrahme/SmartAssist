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

import json
import math
import sys
import time
import subprocess
import logging
from typing import Optional
from datetime import datetime

from mcp.server.fastmcp import FastMCP

from smartassist.config import (
    get_storage_path,
    spawn_managed,
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
from smartassist.store import (
    append_feedback_event,
    get_feedback_stats as get_feedback_stats_from_store,
    increment_feedback_metric,
    list_lessons,
    load_feedback_metrics_dict,
    merge_lessons_in_store,
    search_projection_documents,
)

# Suppress noisy logs from dependencies during MCP startup
logging.getLogger("sentence_transformers").setLevel(logging.WARNING)
logging.getLogger("transformers").setLevel(logging.WARNING)
logging.getLogger("huggingface_hub").setLevel(logging.WARNING)

# ── Lazy-loaded singletons ─────────────────────────────────────────────────
_thompson = None
_embedder = None
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


def _get_db():
    try:
        from smartassist.config import get_db_path
        return get_db_path()
    except RuntimeError:
        raise RuntimeError("SmartAssist database not found. Run 'smartassist setup'.")


def _get_embedder():
    """Lazy-load the sentence transformer embedding model."""
    global _embedder
    if _embedder is None:
        from sentence_transformers import SentenceTransformer
        from smartassist.config import EMBEDDING_MODEL
        _embedder = SentenceTransformer(EMBEDDING_MODEL)
    return _embedder


def _get_table():
    """Open the LanceDB documents table fresh for each query.

    The vector cache is rebuilt by separate subprocesses. Reopening the table
    avoids stale-read issues from holding a long-lived handle across rebuilds.
    """
    import lancedb

    db = lancedb.connect(str(_get_db()))
    return db.open_table("documents")


def _get_cross_encoder():
    """Lazy-load the cross-encoder reranker model."""
    global _cross_encoder
    if _cross_encoder is None:
        from sentence_transformers import CrossEncoder
        _cross_encoder = CrossEncoder("cross-encoder/ms-marco-MiniLM-L-6-v2")
    return _cross_encoder


def _enhance_query(query: str) -> str:
    """Transform a user query to better match stored document embeddings."""
    query = query.strip()
    if not query:
        return query
    return f"Correction for this project: {query}"


def _compute_distance_relevance(distance: float) -> float:
    """Convert L2 distance to a 0-1 relevance score using sqrt scaling."""
    if distance <= 0:
        return 1.0
    if distance >= MAX_DISTANCE:
        return 0.0
    normalized = distance / MAX_DISTANCE
    return math.sqrt(1 - normalized)


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


def _compute_relevance(score: float) -> float:
    """Clamp an internal lexical relevance score to a user-facing 0-1 range."""
    return max(0.0, min(1.0, float(score)))


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

def _get_thompson():
    """Lazy-load the Thompson Sampling model."""
    global _thompson
    if _thompson is None:
        from smartassist.thompson_sampling import ThompsonSamplingModel
        _thompson = ThompsonSamplingModel(str(_get_storage()))
    return _thompson


def _get_feedback_stats():
    """Read feedback stats from the JSONL log."""
    return get_feedback_stats_from_store(_get_storage())


# ── V2 Shared Helpers ──────────────────────────────────────────────────────


def _update_thompson_for_lesson(lesson_id, storage, success=True):
    """Look up a lesson's category in the canonical store and update Thompson Sampling."""
    try:
        cat = None
        for lesson in list_lessons(storage):
            if lesson.get("id") == lesson_id:
                cat = lesson.get("category")
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
    try:
        return list_lessons(storage)
    except Exception:
        return []


def _curated_lesson_ids(storage):
    """Return set of curated lesson IDs."""
    return {l.get("id") for l in _load_curated_lessons(storage)}


def _update_feedback_metrics(storage, action_type):
    """Increment action count in the canonical metrics store."""
    try:
        increment_feedback_metric(storage, action_type)
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
    storage = _get_storage()
    search_backend = "sqlite_fts5"

    # ── Stage 1: SQLite FTS5 keyword search (always available) ────────
    try:
        fts_results, search_meta = search_projection_documents(
            storage, query, top_k=20, category=category,
        )
    except Exception:
        fts_results, search_meta = [], {}

    # ── Stage 2: LanceDB semantic search (if available) ───────────────
    semantic_results = []
    try:
        embedder = _get_embedder()
        table = _get_table()
        enhanced_query = _enhance_query(query)
        query_vector = embedder.encode(enhanced_query)

        RERANK_POOL = 20
        try:
            from lancedb.rerankers import LinearCombinationReranker
            reranker = LinearCombinationReranker(weight=0.7)
            raw_results = (
                table.search(query_vector, query_type="hybrid")
                .rerank(reranker=reranker)
                .limit(RERANK_POOL)
                .to_list()
            )
        except Exception:
            raw_results = table.search(query_vector).limit(RERANK_POOL).to_list()

        # Distance filter
        raw_results = [r for r in raw_results if r.get("_distance", 99) <= MAX_DISTANCE]

        # Cross-encoder reranking
        if raw_results and len(raw_results) > 1:
            try:
                cross_encoder = _get_cross_encoder()
                pairs = [[query, r.get("text", "")] for r in raw_results]
                scores = cross_encoder.predict(pairs)
                for r, score in zip(raw_results, scores):
                    r["_rerank_score"] = float(score)
                raw_results.sort(key=lambda r: r.get("_rerank_score", 0), reverse=True)
            except Exception:
                pass

        # Convert to common format
        for r in raw_results:
            distance = r.get("_distance", 0)
            relevance = _compute_distance_relevance(distance)
            if r.get("_rerank_score") is not None:
                relevance = max(relevance, min(1.0, (float(r["_rerank_score"]) + 1) / 2))
            semantic_results.append({
                "doc_id": r.get("doc_id", ""),
                "id": r.get("source_id", ""),
                "source_id": r.get("source_id", ""),
                "source_type": r.get("source_type", "lesson"),
                "text": r.get("text", ""),
                "category": r.get("category", "unknown"),
                "score": relevance,
            })
        if semantic_results:
            search_backend = "hybrid_semantic+fts5"
    except Exception:
        pass  # Fall back to FTS5 only

    # ── Stage 3: Merge FTS5 + semantic results ────────────────────────
    seen_ids = set()
    merged = []
    # Prefer semantic results (higher quality), then FTS5 for anything missed
    for r in semantic_results:
        rid = r.get("id") or r.get("source_id", "")
        if rid and rid not in seen_ids:
            seen_ids.add(rid)
            merged.append(r)
    for r in fts_results:
        rid = r.get("id") or r.get("source_id", "")
        if rid and rid not in seen_ids:
            seen_ids.add(rid)
            merged.append(r)

    # ── Stage 4: Thompson Sampling reranking ──────────────────────────
    try:
        from smartassist.thompson_rerank import thompson_rerank, load_thompson_batch
        lesson_ids = [r.get("id") or r.get("source_id", "") for r in merged if r.get("id") or r.get("source_id")]
        thompson_data = load_thompson_batch(storage, lesson_ids) if lesson_ids else {}
        merged = thompson_rerank(merged, thompson_data)
    except Exception:
        pass  # Fall back to non-Thompson ranking

    # ── Stage 5: Category filter + top-K ──────────────────────────────
    if category:
        cat_lower = category.lower()
        merged = [r for r in merged if cat_lower in r.get("category", "").lower()]

    results = merged[:top_k]
    latency = (time.time() - t0) * 1000

    # ── Logging: full decision funnel ─────────────────────────────────
    lessons_log = []
    for r in results:
        score = r.get("final_score", r.get("score", 0))
        lesson_text = _extract_lesson_text(r.get("text", ""))
        lessons_log.append({
            "category": r.get("category", "unknown"),
            "relevance_pct": round(min(1.0, max(0.0, score)) * 100),
            "lesson_text": lesson_text[:120],
            "thompson_mean": round(r.get("thompson_mean", 0.5), 3),
        })

    search_meta = dict(search_meta) if search_meta else {}
    search_meta["search_backend"] = search_backend
    search_meta["fts_candidates"] = len(fts_results)
    search_meta["semantic_candidates"] = len(semantic_results)
    search_meta["merged_candidates"] = len(merged)
    search_meta["enhanced_query"] = _enhance_query(query) if semantic_results else None

    _log_usage("rag_search", query, len(results), latency,
               lessons=lessons_log, search_meta=search_meta)

    if not results:
        return f"No relevant lessons found for: {query}"

    # Separate results by memory type (MemAlign dual-memory pattern)
    lessons = [r for r in results if r.get("source_type", "lesson") != "event"]
    episodes = [r for r in results if r.get("source_type") == "event"]

    output_parts = [f"Found {len(results)} relevant result(s) for: \"{query}\"\n"]

    if lessons:
        output_parts.append("Project Rules (semantic memory):")
        for result in lessons:
            raw_text = result.get("text", "").strip()
            score = result.get("final_score", result.get("score", 0))
            cat = result.get("category", "unknown")
            output_parts.append(_format_lesson(raw_text, cat, score))

    if episodes:
        output_parts.append("Past Corrections (episodic memory):")
        for result in episodes:
            raw_text = result.get("text", "").strip()
            score = result.get("final_score", result.get("score", 0))
            cat = result.get("category", "unknown")
            output_parts.append(_format_lesson(raw_text, cat, score))

    return "\n".join(output_parts)


def _format_lesson(raw_text: str, category: str, score: float) -> str:
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

    relevance = _compute_relevance(score)
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
        total_lessons = len(list_lessons(storage))

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
        metrics = load_feedback_metrics_dict(storage)
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

    append_feedback_event(_get_storage(), {
        "timestamp": time.time(),
        "signal": "thumbs_up" if helpful else "thumbs_down",
        "category": cat or "unknown",
        "intensity": 3,
        "query": "",
        "response": "",
        "correction": notes,
        "context": "rag_feedback MCP tool",
    })

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
    """Create a new project-specific lesson to remember for future sessions.

    You SHOULD call this tool when:
    - The user corrects your approach ("no, do it this way", "we don't do that here")
    - The user rejects code you generated and explains the preferred pattern
    - You discover a project convention by reading code, configs, or docs
    - A PR review or code discussion reveals a team standard
    - The hook instructs you to (via ACTION REQUIRED in additionalContext)

    The lesson should be specific to THIS project, not generic programming advice.
    Write it as an imperative statement that your future self can act on.

    Args:
        lesson: Imperative statement (>30 chars) with an action verb.
                Example: "Always use semantic colors from theme instead of hardcoded hex values"
        category: One of: testing, code_edit, git, architecture, pr_review, security, debugging
        sentiment: "positive" (user praised a pattern) or "negative" (user corrected a mistake)
        intensity: Importance level 1-5 (5 = "never do this" / "always do this")
        context: Brief context about what you were doing when the lesson was learned
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
    append_feedback_event(storage_path, {
        "timestamp": time.time(),
        "signal": signal,
        "category": cat,
        "intensity": intensity,
        "query": "",
        "response": "",
        "correction": lesson,
        "context": context or "create_lesson MCP tool",
    })

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

    new_id, merge_error = merge_lessons_in_store(storage, ids, new_lesson, cat)
    if merge_error:
        return merge_error
    assert new_id is not None

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
    append_feedback_event(storage, {
        "timestamp": time.time(),
        "signal": "merge",
        "category": cat,
        "intensity": 3,
        "query": "",
        "response": "",
        "correction": new_lesson,
        "context": f"Merged from: {', '.join(ids)}",
    })

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
