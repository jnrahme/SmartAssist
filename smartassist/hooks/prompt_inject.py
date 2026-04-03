#!/usr/bin/env python3
"""
UserPromptSubmit Hook — Search RAG lessons and inject into Claude's context.

Searches curated_lessons.json using fast keyword matching (pure stdlib, <50ms).
1. Writes matched lessons to rag_live.log (for the claude-rag monitor terminal)
2. Injects relevant lessons into Claude's prompt as <rag-context>
3. Applies per-lesson scoring (boost/block from feedback)
4. Deduplicates lessons already injected in the same session

V2: Claude-as-curator feedback — per-lesson decisions via MCP tools.
"""

import sys
import json
import re
import time
from math import log
from datetime import datetime

from smartassist.config import get_storage_path
from smartassist.lesson_feedback import (
    load_lesson_scores,
    load_last_injection,
    save_last_injection,
    load_session_state,
    save_session_state,
    reinforce_recent_lessons,
    create_lesson_from_feedback,
    log_comparison_entry,
    DEFAULT_BOOST,
)
from smartassist.store import list_lessons

# ── Skip trivial prompts ─────────────────────────────────────────────────
SKIP_PATTERNS = [
    r"^(yes|no|ok|okay|sure|yep|yea|nah|nope|k|y|n)\s*[.!?]*$",
    r"^(thanks|thank you|thx|ty)\b",
    r"^(hi|hello|hey|sup|yo)\b",
    r"^(done|cancel|stop|quit|exit)\s*$",
    r"^/",  # slash commands
]

# ── Feedback signals ─────────────────────────────────────────────────
FEEDBACK_SIGNALS = {
    ":)": "positive", ":-)": "positive",
    ":(": "negative", ":-(": "negative",
    "thumbs_up": "positive", "thumbs up": "positive",
    "thumbs_down": "negative", "thumbs down": "negative",
    "thumbs-up": "positive", "thumbs-down": "negative",
    "thumb-up": "positive", "thumb-down": "negative",
    "thumb up": "positive", "thumb down": "negative",
    "thumb_up": "positive", "thumb_down": "negative",
    "👍": "positive", "👎": "negative",
    "+1": "positive", "-1": "negative",
}

# ── Stop words ───────────────────────────────────────────────────────────
STOP_WORDS = {
    "the", "a", "an", "is", "are", "was", "were", "be", "been", "being",
    "have", "has", "had", "do", "does", "did", "will", "would", "could",
    "should", "may", "might", "can", "shall", "this", "that", "these",
    "those", "me", "my", "your", "his", "its", "our", "their", "what",
    "which", "who", "how", "when", "where", "why", "in", "on", "at",
    "to", "for", "with", "by", "from", "of", "and", "or", "but", "not",
    "so", "if", "as", "up", "out", "about", "into", "through", "all",
    "some", "any", "other", "more", "most", "very", "just", "also",
    "now", "here", "there", "please", "make", "let", "get", "see",
    "look", "need", "want", "going", "using", "like", "new", "file",
    "thing", "way", "lot", "really", "stuff", "right", "something",
    "everything", "much", "still", "even", "than", "too", "been",
}

# ── Synonym expansion ────────────────────────────────────────────────────
SYNONYMS = {
    "test": {"testing", "tests", "mock", "mocks", "assertion"},
    "tests": {"testing", "test", "mock"},
    "testing": {"test", "tests", "mock"},
    "style": {"styles", "styling", "color", "colors", "theme"},
    "styles": {"style", "styling", "color", "theme"},
    "color": {"colors", "theme", "hex", "style"},
    "component": {"components", "render", "module"},
    "git": {"commit", "branch", "merge", "push"},
    "commit": {"git", "message", "branch"},
    "import": {"imports", "export", "module", "require"},
    "type": {"types", "interface", "generics"},
    "error": {"errors", "catch", "throw", "exception", "handling"},
    "mock": {"mocks", "testing", "spy"},
    "api": {"fetch", "request", "response", "endpoint", "http"},
    "auth": {"authentication", "login", "token", "session"},
    "config": {"configuration", "settings", "options", "env"},
    "deploy": {"deployment", "release", "ci", "cd", "pipeline"},
    "performance": {"optimize", "cache", "latency", "benchmark"},
}

# ── Max age for injection staleness ──────────────────────────────────────
MAX_INJECTION_AGE = 900  # 15 minutes


def tokenize(text):
    words = re.findall(r'[a-z][a-z0-9_.]+', text.lower())
    return [w for w in words if w not in STOP_WORDS and len(w) > 2]


def expand_query(tokens):
    expanded = set(tokens)
    for t in tokens:
        if t in SYNONYMS:
            expanded.update(SYNONYMS[t])
    return expanded


def build_idf(lessons):
    doc_freq = {}
    n = len(lessons)
    for lesson in lessons:
        terms = set(tokenize(lesson["lesson"] + " " + lesson["category"]))
        for t in terms:
            doc_freq[t] = doc_freq.get(t, 0) + 1
    # Add-one smoothing keeps exact matches searchable even in tiny corpora.
    return {t: 1.0 + log((n + 1) / (df + 1)) for t, df in doc_freq.items()}


def search_lessons(query_tokens, lessons, idf, top_k=5, lesson_scores=None):
    if not query_tokens:
        return []
    if lesson_scores is None:
        lesson_scores = {}
    expanded = expand_query(query_tokens)
    scored = []
    for lesson in lessons:
        lesson_id = lesson.get("id", "")
        score_entry = lesson_scores.get(lesson_id, {})

        if score_entry.get("blocked", False):
            continue

        lesson_text = lesson["lesson"].lower() + " " + lesson["category"]
        lesson_tokens = set(tokenize(lesson_text))
        lesson_tokens.add(lesson["category"])
        score = 0.0
        matched = set()
        for qt in expanded:
            if qt in lesson_tokens:
                weight = idf.get(qt, 1.0)
                score += weight
                matched.add(qt)
        if not matched:
            for qt in expanded:
                for lt in lesson_tokens:
                    if len(qt) >= 4 and (qt in lt or lt in qt):
                        score += 0.3
                        matched.add(qt)
                        break
        if score < 0.5:
            continue
        relevance = score / (len(expanded) * 1.5)

        boost = score_entry.get("boost", DEFAULT_BOOST)
        relevance *= boost

        scored.append({
            "id": lesson_id,
            "lesson": lesson["lesson"],
            "category": lesson["category"],
            "score": relevance,
            "matched": matched,
        })
    scored.sort(key=lambda x: -x["score"])
    return scored[:top_k]


def read_counter(storage_path):
    counter_file = storage_path / "rag_prompt_counter.json"
    try:
        data = json.loads(counter_file.read_text())
        return data.get("prompt_count", 0), data.get("inject_count", 0)
    except Exception:
        return 0, 0


def write_counter(storage_path, prompt_count, inject_count):
    counter_file = storage_path / "rag_prompt_counter.json"
    try:
        from smartassist.config import atomic_write_json
        atomic_write_json(counter_file, {
            "prompt_count": prompt_count,
            "inject_count": inject_count,
        })
    except Exception:
        pass


_MAX_LIVE_LOG_BYTES = 512 * 1024  # 512 KB


def _rotate_live_log(live_log):
    """Truncate rag_live.log when it exceeds _MAX_LIVE_LOG_BYTES."""
    try:
        if live_log.exists() and live_log.stat().st_size > _MAX_LIVE_LOG_BYTES:
            # Keep the last half
            data = live_log.read_bytes()
            live_log.write_bytes(data[len(data) // 2:])
    except OSError:
        pass


def write_to_live_log(storage_path, user_message, results, query_tokens=None,
                      expanded_tokens=None, deduped_results=None):
    """Write formatted output to rag_live.log for the monitor terminal."""
    live_log = storage_path / "rag_live.log"
    _rotate_live_log(live_log)
    now = datetime.now().strftime("%H:%M:%S")

    prompt_count, inject_count = read_counter(storage_path)
    prompt_count += 1
    if results:
        inject_count += 1
    write_counter(storage_path, prompt_count, inject_count)

    hit_rate = int((inject_count / prompt_count) * 100) if prompt_count > 0 else 0

    if deduped_results is None:
        deduped_results = []
    deduped_ids = {r["id"] for r in deduped_results}

    lines = []
    lines.append("")
    lines.append(f"\033[90m{'=' * 60}\033[0m")
    lines.append(f"\033[90m  {now}  |  Prompt #{prompt_count}\033[0m")
    lines.append(f"\033[36m\033[1m  PROMPT\033[0m")

    preview = user_message.strip().replace('\n', ' ')[:80]
    if len(user_message.strip()) > 80:
        preview += "..."
    lines.append(f"  \033[97m\"{preview}\"\033[0m")

    if query_tokens:
        tokens_str = ", ".join(sorted(query_tokens))
        if expanded_tokens:
            added = sorted(expanded_tokens - set(query_tokens))
            if added:
                tokens_str += f" \033[90m-> +{', '.join(added)}\033[0m"
        lines.append(f"  \033[90mKeywords: {tokens_str}\033[0m")

    lines.append("")

    all_display = list(results) + list(deduped_results)

    if not all_display:
        lines.append(f"  \033[90m  No relevant lessons found for this prompt\033[0m")
    else:
        new_count = len(results)
        dedup_count = len(deduped_results)
        inject_msg = f"INJECTED {new_count} LESSON(S)"
        if dedup_count > 0:
            inject_msg += f" ({dedup_count} already in context)"
        lines.append(f"\033[32m\033[1m  {inject_msg}\033[0m")
        lines.append("")

        cat_colors = {
            "testing": "\033[32m",
            "code_edit": "\033[36m",
            "architecture": "\033[33m",
            "pr_review": "\033[35m",
            "git": "\033[33m",
            "security": "\033[31m",
        }

        for idx, r in enumerate(all_display, 1):
            pct = min(int(r["score"] * 100), 99)
            cat = r["category"]
            lid = r.get("id", "???")
            text = r["lesson"][:80]
            is_deduped = lid in deduped_ids

            if is_deduped:
                score_color = "\033[90m"
                suffix = " \033[90m[already in context]\033[0m"
            elif pct >= 40:
                score_color = "\033[32m"
                suffix = ""
            elif pct >= 25:
                score_color = "\033[33m"
                suffix = ""
            else:
                score_color = "\033[90m"
                suffix = ""

            cc = cat_colors.get(cat, "\033[37m")
            lines.append(f"  #{idx:<2} {score_color}{lid}  {pct:>3}%\033[0m  {cc}{cat:<14}\033[0m  {text}{suffix}")

    lines.append("")
    lines.append(f"  \033[90mStats: {prompt_count} prompts | {inject_count} injected | {hit_rate}% hit rate\033[0m")
    lines.append(f"  \033[90mFeedback: +N promote | -N demote | xN block\033[0m")
    lines.append("")

    try:
        with open(live_log, "a", encoding="utf-8") as f:
            f.write("\n".join(lines) + "\n")
    except Exception:
        pass


# ── V2 Feedback System ────────────────────────────────────────────────────


def detect_feedback_signal(message):
    """Detect feedback signal. Supports standalone or signal+context.

    Returns (sentiment, user_context) tuple or (None, None).

    Examples:
        ":)" → ("positive", "")
        ":( dont do this to the theme" → ("negative", "dont do this to the theme")
        "fix the bug" → (None, None)
    """
    stripped = message.strip().lower()
    if not stripped:
        return None, None

    # Exact match — standalone signal
    if stripped in FEEDBACK_SIGNALS:
        return FEEDBACK_SIGNALS[stripped], ""

    # Prefix match — signal at start, rest is context
    for signal, sentiment in sorted(FEEDBACK_SIGNALS.items(), key=lambda x: -len(x[0])):
        if stripped.startswith(signal) and len(stripped) > len(signal):
            # Word boundary check: char after signal must be whitespace
            char_after = stripped[len(signal)]
            if char_after not in (' ', '\t', '\n'):
                continue
            rest = stripped[len(signal):].strip()
            if rest:
                # Preserve original case for context
                return sentiment, message.strip()[len(signal):].strip()

    return None, None


def _reconstruct_injected_lessons(storage_path):
    """Build full picture of what Claude last saw for feedback decisions.

    Loads last_injection.json + curated_lessons.json + lesson_scores.json.
    Returns list of dicts with id, category, lesson, boost, ups, downs, confidence.
    Returns [] if stale (>MAX_INJECTION_AGE) or missing.
    """
    last = load_last_injection()
    if not last:
        return []

    # Check staleness
    injection_time = last.get("_timestamp", 0)
    if injection_time and (time.time() - injection_time) > MAX_INJECTION_AGE:
        return []

    curated_map = {
        lesson.get("id", ""): lesson
        for lesson in list_lessons(storage_path)
    }

    # Load scores
    scores = load_lesson_scores()

    reconstructed = []
    for key, lesson_id in last.items():
        if key.startswith("_"):
            continue
        curated = curated_map.get(lesson_id, {})
        score_entry = scores.get(lesson_id, {})
        ups = score_entry.get("ups", 0)
        downs = score_entry.get("downs", 0)
        boost = score_entry.get("boost", DEFAULT_BOOST)
        # Laplace smoothing: confidence = ups / (ups + downs + 2)
        confidence = ups / (ups + downs + 2)

        reconstructed.append({
            "id": lesson_id,
            "category": curated.get("category", "unknown"),
            "lesson": curated.get("lesson", f"[lesson text not found for {lesson_id}]"),
            "boost": boost,
            "ups": ups,
            "downs": downs,
            "confidence": confidence,
        })

    return reconstructed


def build_rich_feedback_context(sentiment, user_context, reinforcement_results):
    """Build context for Claude — instruct compare_lesson when context is available."""
    action = "boosted" if sentiment == "positive" else "demoted"
    if reinforcement_results:
        id_summaries = []
        for lid, old_b, new_b, retired in reinforcement_results:
            suffix = " RETIRED" if retired else ""
            id_summaries.append(f"{lid} {old_b:.1f}→{new_b:.1f}x{suffix}")
        summary = f"Auto-reinforced {len(reinforcement_results)} lesson(s) ({action} {', '.join(id_summaries)})."
    else:
        summary = f"No recent lessons to reinforce (0 lesson(s) {action})."

    parts = [summary]

    if user_context and len(user_context.strip()) >= 15:
        parts.append(f'User {sentiment} feedback: "{user_context}"')
        parts.append(
            "ACTION REQUIRED: Before responding to the user, you MUST call the "
            "`create_lesson` tool (mcp__smartassist__create_lesson). "
            "Analyze your recent conversation context and the feedback above. "
            "Write a clear, imperative, project-specific lesson (>30 chars, "
            "with an action verb) that captures what should be done differently. "
            "Be specific — reference the actual pattern, file, or convention involved. "
            "Parameters: lesson=<your lesson>, category=<best fit>, "
            f'sentiment="{sentiment}", context="{user_context}". '
            "After calling the tool, acknowledge the feedback briefly."
        )
    else:
        if user_context:
            parts.append(f'User feedback: "{user_context}"')
        parts.append("No action needed from you — just acknowledge briefly.")

    return "\n".join(parts)


def write_to_live_log_feedback(storage_path, signal_text, sentiment,
                               user_context="", reinforcement_results=None):
    """Write feedback detection event to rag_live.log for the monitor terminal."""
    live_log = storage_path / "rag_live.log"
    now = datetime.now().strftime("%H:%M:%S")

    prompt_count, inject_count = read_counter(storage_path)
    prompt_count += 1
    write_counter(storage_path, prompt_count, inject_count)

    lines = []
    lines.append("")
    lines.append(f"\033[90m{'=' * 60}\033[0m")
    lines.append(f"\033[90m  {now}  |  Prompt #{prompt_count}\033[0m")

    color = "\033[32m" if sentiment == "positive" else "\033[31m"
    lines.append(f"{color}\033[1m  FEEDBACK DETECTED {signal_text}\033[0m")
    lines.append(f"  Sentiment: {sentiment}")

    if user_context:
        lines.append(f"  Context: \"{user_context}\"")

    if reinforcement_results:
        action_label = "BOOST" if sentiment == "positive" else "DEMOTE"
        for lid, old_b, new_b, retired in reinforcement_results:
            suffix = " → RETIRED" if retired else ""
            action_color = "\033[32m" if sentiment == "positive" else "\033[31m"
            lines.append(f"  {action_color}{action_label}: {lid} {old_b:.1f}x → {new_b:.1f}x{suffix}\033[0m")
    else:
        lines.append(f"  \033[90m0 lesson(s) reinforced\033[0m")

    if user_context and len(user_context.strip()) >= 15:
        lines.append(f"  \033[36m→ A/B comparison: hook logged, Claude will draft via compare_lesson\033[0m")

    lines.append("")

    try:
        with open(live_log, "a", encoding="utf-8") as f:
            f.write("\n".join(lines) + "\n")
    except Exception:
        pass


def main():
    try:
        hook_input = json.load(sys.stdin)
    except (json.JSONDecodeError, EOFError):
        return

    user_message = hook_input.get("prompt", "")

    # ── Check for feedback signals before length filter ──────────────
    sentiment, user_context = detect_feedback_signal(user_message) if user_message else (None, None)
    if sentiment:
        # Hook-level reinforcement — no Claude involvement needed
        reinforcement_results = reinforce_recent_lessons(sentiment)

        # Per-lesson Thompson attribution — the RLHF reinforcement loop
        try:
            storage_path_for_thompson = get_storage_path()
            from smartassist.thompson_rerank import attribute_feedback, update_thompson_batch
            last = load_last_injection()
            if last:
                injected_for_attribution = []
                for key, lesson_id in last.items():
                    if key.startswith("_"):
                        continue
                    injected_for_attribution.append({
                        "id": lesson_id,
                        "score": 0.5,  # default relevance weight
                        "injection_timestamp": last.get("_timestamp", time.time()),
                    })
                if injected_for_attribution:
                    attributions = attribute_feedback(sentiment, injected_for_attribution)
                    update_thompson_batch(storage_path_for_thompson, attributions)
        except Exception:
            pass  # Never break the hook over Thompson updates

        # Immediate lesson creation — guaranteed, no LLM dependency
        created_id, created_lesson = None, None
        if user_context:
            created_id, created_lesson = create_lesson_from_feedback(
                user_context, sentiment, reinforcement_results,
            )
            if created_id:
                # Fire-and-forget: make lesson searchable via rag_search
                try:
                    from smartassist.config import spawn_managed
                    spawn_managed([sys.executable, "-m", "smartassist.hooks.vectorize_learnings"])
                except Exception:
                    pass

        try:
            storage_path = get_storage_path()

            # Log hook's result for A/B comparison
            if user_context and len(user_context.strip()) >= 15:
                log_comparison_entry(
                    storage_path, "hook", sentiment, user_context,
                    created_lesson, created_lesson is not None,
                )

            write_to_live_log_feedback(
                storage_path, user_message.strip(), sentiment,
                user_context=user_context,
                reinforcement_results=reinforcement_results,
            )
        except RuntimeError:
            storage_path = None

        # ALSO tell the LLM to create a better version with full context
        context = build_rich_feedback_context(
            sentiment, user_context, reinforcement_results,
        )
        output = {
            "hookSpecificOutput": {
                "hookEventName": "UserPromptSubmit",
                "additionalContext": context,
            }
        }
        print(json.dumps(output))
        return

    if not user_message or len(user_message.strip()) < 12:
        return

    lower = user_message.lower().strip()
    for pattern in SKIP_PATTERNS:
        if re.match(pattern, lower):
            return

    try:
        storage_path = get_storage_path()
    except RuntimeError:
        return

    try:
        lessons = list_lessons(storage_path)
    except Exception:
        return

    if not lessons:
        return

    query_tokens = tokenize(user_message)
    if not query_tokens:
        return

    lesson_scores = load_lesson_scores()

    expanded = expand_query(query_tokens)
    idf = build_idf(lessons)
    results = search_lessons(query_tokens, lessons, idf, lesson_scores=lesson_scores)
    results = [r for r in results if r["score"] >= 0.15]

    # Thompson Sampling reranking — the RLHF reinforcement loop
    try:
        from smartassist.thompson_rerank import thompson_rerank, load_thompson_batch, record_injection
        lesson_ids = [r.get("id", "") for r in results if r.get("id")]
        if lesson_ids:
            thompson_data = load_thompson_batch(storage_path, lesson_ids)
            results = thompson_rerank(results, thompson_data)
            results = [r for r in results if r.get("final_score", r.get("score", 0)) >= 0.10]
    except Exception:
        pass  # Fall back to non-Thompson ranking

    # Session deduplication
    session_id = hook_input.get("session_id", "")
    already_injected = set()
    deduped_results = []
    if session_id:
        already_injected = load_session_state(session_id)
        deduped_results = [r for r in results if r.get("id") in already_injected]
        results = [r for r in results if r.get("id") not in already_injected]

    write_to_live_log(storage_path, user_message, results, query_tokens, expanded,
                      deduped_results=deduped_results)

    all_candidates = list(results) + list(deduped_results)
    if all_candidates:
        save_last_injection(all_candidates)

    if not results:
        return

    # Record injection for Thompson tracking
    try:
        from smartassist.thompson_rerank import record_injection
        injected_ids = [r.get("id") for r in results if r.get("id")]
        if injected_ids:
            record_injection(storage_path, injected_ids)
    except Exception:
        pass

    if session_id:
        new_ids = {r.get("id") for r in results if r.get("id")}
        already_injected.update(new_ids)
        save_session_state(session_id, already_injected)

    # ── MemAlign dual-memory injection ─────────────────────────────────
    # Semantic memory (lessons/principles) + Episodic memory (past corrections/examples)
    def _sanitize(text):
        """Strip characters that could be interpreted as prompt directives."""
        for prefix in ("ignore ", "disregard ", "forget "):
            if text.lower().startswith(prefix + "all ") or text.lower().startswith(prefix + "previous "):
                text = text[len(prefix):]
        return text.replace("\n", " ").replace("\r", " ").strip()

    # Retrieve episodic memory (recent corrections/feedback events matching this query)
    episodes = []
    try:
        from smartassist.store import search_projection_documents
        ep_results, _ = search_projection_documents(
            storage_path, user_message, top_k=3, category=None,
        )
        for ep in ep_results:
            if ep.get("source_type") == "event":
                episodes.append(ep)
    except Exception:
        pass

    # Split results into lessons (semantic memory) and any events that came through
    semantic_lessons = [r for r in results if r.get("source_type", "lesson") == "lesson" or "id" in r]

    # Format semantic memory (principles)
    parts = []
    if semantic_lessons:
        lesson_lines = [
            f"[{r.get('id', '?')}] [{r['category']}] {_sanitize(r['lesson'])}"
            for r in semantic_lessons
        ]
        parts.append("Project-specific rules (apply these):\n" + "\n".join(f"- {l}" for l in lesson_lines))

    # Format episodic memory (past corrections relevant to this query)
    if episodes:
        episode_lines = []
        for ep in episodes[:2]:  # max 2 episodes to avoid context bloat
            text = _sanitize(ep.get("text", ""))
            cat = ep.get("category", "")
            episode_lines.append(f"[{cat}] {text}")
        parts.append("Past corrections on similar work:\n" + "\n".join(f"- {l}" for l in episode_lines))

    if not parts:
        return

    context = "\n\n".join(parts)

    output = {
        "hookSpecificOutput": {
            "hookEventName": "UserPromptSubmit",
            "additionalContext": context,
        }
    }
    print(json.dumps(output))


if __name__ == "__main__":
    main()
