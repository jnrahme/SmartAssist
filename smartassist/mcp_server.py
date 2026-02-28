#!/usr/bin/env python3
"""
SmartAssist MCP Server - Exposes the RLHF knowledge base as tools for Claude Code.

Provides three tools:
  - rag_search: Semantic search across past lessons, corrections, and feedback
  - rag_dashboard: View current reliability scores and system stats
  - rag_feedback: Record feedback on suggestions

Runs via stdio transport. Entry point: `smartassist serve`
"""

import math
import json
import time
import logging
from typing import Optional
from datetime import datetime

from mcp.server.fastmcp import FastMCP

from smartassist.config import EMBEDDING_MODEL, EMBEDDING_DIM, get_storage_path, get_db_path

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


def _get_storage():
    return get_storage_path()


def _get_db():
    return get_db_path()


def _usage_log_path():
    return _get_storage() / "usage_log.jsonl"


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
    except Exception as e:
        _log_usage("rag_search", query, 0, 0)
        return f"Error initializing search: {e}"

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
    """View current RLHF system reliability scores and statistics.

    Shows performance metrics across all tracked categories (testing, git,
    code_edit, etc.) with Thompson Sampling reliability scores. Useful for
    understanding which areas need attention.
    """
    t0 = time.time()
    try:
        thompson = _get_thompson()
    except Exception as e:
        _log_usage("rag_dashboard")
        return f"Error loading Thompson model: {e}"

    scores = thompson.get_all_reliabilities()
    weak = thompson.get_weak_categories(threshold=0.70)
    stats = _get_feedback_stats()
    _log_usage("rag_dashboard", "", 0, (time.time() - t0) * 1000)

    lines = ["RLHF System Dashboard", "=" * 40, ""]

    lines.append("Reliability by Category:")
    for cat, score in sorted(scores.items(), key=lambda x: x[1]):
        status = "WEAK" if cat in weak else "OK"
        bar = "#" * int(score * 20) + "." * (20 - int(score * 20))
        lines.append(f"  {cat:15s} [{bar}] {score:.1%}  ({status})")

    lines.append("")
    lines.append(f"Weak categories (<70%): {', '.join(weak) if weak else 'None'}")

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

    valid_categories = {"testing", "code_edit", "git", "architecture", "pr_review", "security"}
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


def serve():
    """Entry point for `smartassist serve`."""
    mcp.run(transport="stdio")


if __name__ == "__main__":
    serve()
