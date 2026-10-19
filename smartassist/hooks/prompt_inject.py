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
    find_recurring_lessons,
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
    ":)": "positive",
    ":-)": "positive",
    ":(": "negative",
    ":-(": "negative",
    "thumbs_up": "positive",
    "thumbs up": "positive",
    "thumbs_down": "negative",
    "thumbs down": "negative",
    "thumbs-up": "positive",
    "thumbs-down": "negative",
    "thumb-up": "positive",
    "thumb-down": "negative",
    "thumb up": "positive",
    "thumb down": "negative",
    "thumb_up": "positive",
    "thumb_down": "negative",
    "👍": "positive",
    "👎": "negative",
    "+1": "positive",
    "-1": "negative",
}

STANDALONE_ONLY_SIGNALS = {"+1", "-1"}

# ── Stop words ───────────────────────────────────────────────────────────
STOP_WORDS = {
    "the",
    "a",
    "an",
    "is",
    "are",
    "was",
    "were",
    "be",
    "been",
    "being",
    "have",
    "has",
    "had",
    "do",
    "does",
    "did",
    "will",
    "would",
    "could",
    "should",
    "may",
    "might",
    "can",
    "shall",
    "this",
    "that",
    "these",
    "those",
    "me",
    "my",
    "you",
    "your",
    "his",
    "its",
    "our",
    "their",
    "what",
    "which",
    "who",
    "how",
    "when",
    "where",
    "why",
    "in",
    "on",
    "at",
    "to",
    "for",
    "with",
    "by",
    "from",
    "of",
    "and",
    "or",
    "but",
    "not",
    "so",
    "if",
    "as",
    "up",
    "out",
    "about",
    "into",
    "through",
    "all",
    "some",
    "any",
    "other",
    "more",
    "most",
    "very",
    "just",
    "also",
    "now",
    "here",
    "there",
    "please",
    "make",
    "let",
    "get",
    "see",
    "look",
    "need",
    "want",
    "going",
    "using",
    "like",
    "new",
    "file",
    "thing",
    "way",
    "lot",
    "really",
    "stuff",
    "right",
    "something",
    "everything",
    "much",
    "still",
    "even",
    "than",
    "too",
    "been",
}

# ── Synonym expansion ────────────────────────────────────────────────────
# Concept groups for query expansion. Keys and values are raw words; both are
# normalized with stem() at load time so inflections need no separate entries.
_SYNONYM_GROUPS = {
    "test": {"mock", "assertion", "jest", "spec"},
    "style": {"color", "theme", "styling", "stylesheet"},
    "component": {"render", "jsx"},
    "git": {"commit", "branch", "merge", "push"},
    "commit": {"git", "message", "lefthook", "commitlint"},
    "import": {"export", "module", "require"},
    "type": {"interface", "generic", "typescript"},
    "error": {"catch", "throw", "exception"},
    "mock": {"test", "spy", "jest"},
    "api": {"fetch", "request", "endpoint", "backend", "server"},
    "endpoint": {"api", "backend", "server"},
    "auth": {"authentication", "login", "token", "session"},
    "config": {"configuration", "settings", "env", "environment"},
    "env": {"environment", "config", "dotenv"},
    "deploy": {"deployment", "release", "pipeline"},
    "performance": {"optimize", "cache", "latency"},
    "analytics": {"firebase", "event", "tracking"},
    "visible": {"visibility", "tobevisible"},
}
SYNONYM_WEIGHT = 0.4

# ── Max age for injection staleness ──────────────────────────────────────
MAX_INJECTION_AGE = 900  # 15 minutes
CANDIDATE_DEPTH = 15
MAX_INJECTED_LESSONS = 3
INJECTION_CHAR_BUDGET = 1200
# Seeded lessons duplicate files the agent already reads (e.g. CLAUDE.md)
ORIGIN_WEIGHTS = {"seed": 0.6}
# Feedback re-orders relevant lessons; it must not outweigh relevance itself
BOOST_INFLUENCE = 0.1
BOOST_FACTOR_RANGE = (0.8, 1.2)
# Hybrid retrieval (ADR 0002): cosine + LEXICAL_BONUS * min(lexical, 1) >= threshold
SEMANTIC_THRESHOLD = 0.60
LEXICAL_BONUS = 0.15
# Drop candidates far below the best match: same recall, fewer filler tokens
RELATIVE_CUTOFF = 0.85
# A past correction must share at least two distinct terms with the prompt and
# score within half of the best match, so one common word never rides along.
EPISODE_MIN_TERMS = 2
EPISODE_RELATIVE_CUTOFF = 0.5


STEM_LENGTH = 6
MIN_COVERAGE = 0.35


def stem(word):
    """Light suffix stripping so 'events'/'adding'/'switched' match 'event'/'add'/'switch'."""
    if "." in word or "_" in word:
        return word
    for suffix in ("ation", "ion", "ity", "ing", "ed", "es", "s"):
        if word.endswith(suffix) and len(word) - len(suffix) >= 3:
            if suffix == "es" and not word.endswith(("ches", "shes", "xes", "sses")):
                continue
            if suffix == "s" and word.endswith("ss"):
                continue
            word = word[: -len(suffix)]
            if suffix in ("ing", "ed") and len(word) > 3 and word[-1] == word[-2]:
                word = word[:-1]
            break
    if word.endswith("e") and len(word) > 4:
        word = word[:-1]
    # Truncation folds remaining inflections (components/componentize) onto one key
    return word[:STEM_LENGTH]


def tokenize(text):
    words = re.findall(r"[a-z][a-z0-9_.]+", text.lower())
    return [stem(w) for w in words if w not in STOP_WORDS and len(w) > 2]


SYNONYMS = {
    stem(key): {stem(v) for v in values} - {stem(key)}
    for key, values in _SYNONYM_GROUPS.items()
}


def expand_query(tokens):
    """Map each search term to its weight: 1.0 for query terms, lower for synonyms."""
    expanded = {t: 1.0 for t in tokens}
    for t in tokens:
        for synonym in SYNONYMS.get(t, ()):
            expanded.setdefault(synonym, SYNONYM_WEIGHT)
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


def boost_factor(boost):
    """Map a stored boost (0..3) to a bounded ranking nudge (0.8..1.2)."""
    low, high = BOOST_FACTOR_RANGE
    return min(max(1.0 + BOOST_INFLUENCE * (boost - DEFAULT_BOOST), low), high)


def current_lessons(storage_path):
    """Active lessons minus ones the project itself contradicts (e.g. yarn in a pnpm repo)."""
    from smartassist.project_facts import stale_reason
    from smartassist.store import project_root_for_storage

    root = project_root_for_storage(storage_path)
    return [l for l in list_lessons(storage_path) if not stale_reason(l.get("lesson", ""), root)]


def semantic_search(user_message, query_tokens, lessons, idf, lesson_scores, storage_path):
    """Hybrid semantic + lexical ranking, or None to keep lexical-only results.

    Opt-in (ADR 0002). Returns None whenever the embedding service is disabled,
    not running yet, or too slow, so a prompt never waits on the model.
    """
    try:
        from smartassist.semantic import client
    except ImportError:
        return None
    if not client.is_enabled(storage_path):
        return None
    candidates = [l for l in lessons if not lesson_scores.get(l.get("id", ""), {}).get("blocked")]
    cosines = client.similarity_scores(user_message, candidates)
    if cosines is None:
        return None
    lexical = {
        r["id"]: r
        for r in search_lessons(
            query_tokens, candidates, idf, top_k=len(candidates),
            lesson_scores=lesson_scores, min_coverage=0.0,
        )
    }
    ranked = []
    for lesson in candidates:
        lesson_id = lesson.get("id", "")
        lex = lexical.get(lesson_id)
        fused = cosines.get(lesson_id, 0.0) + LEXICAL_BONUS * min(lex["score"] if lex else 0.0, 1.0)
        if fused < SEMANTIC_THRESHOLD:
            continue
        boost = lesson_scores.get(lesson_id, {}).get("boost", DEFAULT_BOOST)
        ranked.append({
            "id": lesson_id,
            "lesson": lesson["lesson"],
            "category": lesson["category"],
            "score": fused * boost_factor(boost) * ORIGIN_WEIGHTS.get(lesson.get("origin", ""), 1.0),
            "matched": lex["matched"] if lex else set(),
            "retrieval": "semantic",
        })
    ranked.sort(key=lambda r: -r["score"])
    if ranked:
        floor = RELATIVE_CUTOFF * ranked[0]["score"]
        ranked = [r for r in ranked if r["score"] >= floor]
    return ranked[:CANDIDATE_DEPTH]


def relevant_episodes(ep_results, evidence, root, stale_reason, query_tokens, limit=2):
    """User-evidenced, non-stale past corrections that share several prompt terms.

    Only words the user typed count; synonym expansion alone never qualifies.
    """
    if not ep_results:
        return []
    prompt_stems = {stem(t) for t in query_tokens}
    floor = EPISODE_RELATIVE_CUTOFF * max(float(ep.get("score") or 0.0) for ep in ep_results)
    return [
        ep for ep in ep_results
        if float(ep.get("score") or 0.0) >= floor
        and len({stem(t) for t in ep.get("matched", ())} & prompt_stems) >= EPISODE_MIN_TERMS
        and ep.get("source_id") in evidence
        and not stale_reason(ep.get("text", ""), root)
    ][:limit]


def apply_injection_budget(results, limit=MAX_INJECTED_LESSONS, char_budget=INJECTION_CHAR_BUDGET):
    """Keep the top lessons that fit the count and character budget, in rank order."""
    kept, used = [], 0
    for r in results:
        size = len(r.get("lesson", ""))
        if kept and used + size > char_budget:
            continue
        kept.append(r)
        used += size
        if len(kept) >= limit:
            break
    return kept


def search_lessons(query_tokens, lessons, idf, top_k=5, lesson_scores=None, min_coverage=None):
    """Rank lessons by IDF coverage: the share of the query's information they match.

    Terms the corpus has never seen get the maximum IDF, so a prompt that is mostly
    about something else cannot ride on one shared common word.
    """
    if not query_tokens:
        return []
    if lesson_scores is None:
        lesson_scores = {}
    max_idf = max(idf.values(), default=1.0)
    query_weight = sum(idf.get(t, max_idf) for t in set(query_tokens))
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
        for qt, term_weight in expanded.items():
            if qt in lesson_tokens:
                score += idf.get(qt, max_idf) * term_weight
                matched.add(qt)
        coverage = score / query_weight
        if coverage < (MIN_COVERAGE if min_coverage is None else min_coverage):
            continue
        relevance = coverage

        relevance *= boost_factor(score_entry.get("boost", DEFAULT_BOOST))
        relevance *= ORIGIN_WEIGHTS.get(lesson.get("origin", ""), 1.0)

        scored.append(
            {
                "id": lesson_id,
                "lesson": lesson["lesson"],
                "category": lesson["category"],
                "score": relevance,
                "matched": matched,
            }
        )

    def _sort_key(item):
        lesson_id = str(item.get("id", ""))
        try:
            lesson_num = (
                int(lesson_id[1:]) if lesson_id.startswith("L") else int(lesson_id)
            )
        except ValueError:
            lesson_num = 0
        return (-item["score"], -len(item["matched"]), -lesson_num)

    scored.sort(key=_sort_key)
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

        atomic_write_json(
            counter_file,
            {
                "prompt_count": prompt_count,
                "inject_count": inject_count,
            },
        )
    except Exception:
        pass


_MAX_LIVE_LOG_BYTES = 512 * 1024  # 512 KB


def _rotate_live_log(live_log):
    try:
        if live_log.exists() and live_log.stat().st_size > _MAX_LIVE_LOG_BYTES:
            data = live_log.read_bytes()
            keep = data[len(data) // 2 :]
            tmp = live_log.with_suffix(".log.tmp")
            tmp.write_bytes(keep)
            tmp.replace(live_log)
    except OSError:
        pass


def write_to_live_log(
    storage_path,
    user_message,
    results,
    query_tokens=None,
    expanded_tokens=None,
    deduped_results=None,
):
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

    preview = user_message.strip().replace("\n", " ")[:80]
    if len(user_message.strip()) > 80:
        preview += "..."
    lines.append(f'  \033[97m"{preview}"\033[0m')

    if query_tokens:
        tokens_str = ", ".join(sorted(query_tokens))
        if expanded_tokens:
            added = sorted(set(expanded_tokens) - set(query_tokens))
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
            lines.append(
                f"  #{idx:<2} {score_color}{lid}  {pct:>3}%\033[0m  {cc}{cat:<14}\033[0m  {text}{suffix}"
            )

    lines.append("")
    lines.append(
        f"  \033[90mStats: {prompt_count} prompts | {inject_count} injected | {hit_rate}% hit rate\033[0m"
    )
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

    # Prefix match — signal at start, rest is context. Numeric votes only count
    # standalone: "-1 is the default value" is a request, not feedback.
    for signal, sentiment in sorted(FEEDBACK_SIGNALS.items(), key=lambda x: -len(x[0])):
        if signal in STANDALONE_ONLY_SIGNALS:
            continue
        if stripped.startswith(signal) and len(stripped) > len(signal):
            # Word boundary check: char after signal must be whitespace
            char_after = stripped[len(signal)]
            if char_after not in (" ", "\t", "\n"):
                continue
            rest = stripped[len(signal) :].strip()
            if rest:
                # Preserve original case for context
                return sentiment, message.strip()[len(signal) :].strip()

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
        lesson.get("id", ""): lesson for lesson in list_lessons(storage_path)
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

        reconstructed.append(
            {
                "id": lesson_id,
                "category": curated.get("category", "unknown"),
                "lesson": curated.get(
                    "lesson", f"[lesson text not found for {lesson_id}]"
                ),
                "boost": boost,
                "ups": ups,
                "downs": downs,
                "confidence": confidence,
            }
        )

    return reconstructed


def build_rich_feedback_context(sentiment, user_context, reinforcement_results, created_lesson=None):
    """Build context for Claude after feedback.

    One creation path per feedback: when the hook already recorded a lesson
    (``created_lesson`` = (id, text)) the agent is told about it instead of being
    asked to create another one; otherwise it is asked to write the lesson.
    """
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

    if created_lesson and created_lesson[0]:
        lesson_id, lesson_text = created_lesson
        parts.append(f'User {sentiment} feedback: "{user_context}"')
        parts.append(
            f"Recorded as lesson [{lesson_id}]: {lesson_text}. Do not create another "
            "lesson for this feedback. Apply it now and acknowledge briefly."
        )
    elif user_context and len(user_context.strip()) >= 15:
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


def storage_path_or_none():
    try:
        return get_storage_path()
    except RuntimeError:
        return None


def emit_recurrence_context(storage_path, user_context, recurring_ids):
    """Tell the agent it repeated a mistake it had already been warned about."""
    texts = {}
    if storage_path is not None:
        texts = {lesson.get("id", ""): lesson.get("lesson", "") for lesson in list_lessons(storage_path)}
        write_to_live_log_feedback(storage_path, user_context, "recurrence", user_context=user_context)
    lines = [
        "RECURRING MISTAKE: the user's correction repeats a lesson you were already shown "
        "this session. The lesson was strengthened; no new lesson is needed.",
    ]
    lines += [f"- [{lid}] {texts.get(lid, '')}".rstrip() for lid in recurring_ids]
    lines.append(
        f'User feedback: "{user_context}". Apply the lesson above now, '
        "say briefly what you will do differently, and continue."
    )
    print(
        json.dumps(
            {
                "hookSpecificOutput": {
                    "hookEventName": "UserPromptSubmit",
                    "additionalContext": "\n".join(lines),
                }
            }
        )
    )


def write_to_live_log_feedback(
    storage_path, signal_text, sentiment, user_context="", reinforcement_results=None
):
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
        lines.append(f'  Context: "{user_context}"')

    if reinforcement_results:
        action_label = "BOOST" if sentiment == "positive" else "DEMOTE"
        for lid, old_b, new_b, retired in reinforcement_results:
            suffix = " → RETIRED" if retired else ""
            action_color = "\033[32m" if sentiment == "positive" else "\033[31m"
            lines.append(
                f"  {action_color}{action_label}: {lid} {old_b:.1f}x → {new_b:.1f}x{suffix}\033[0m"
            )
    else:
        lines.append(f"  \033[90m0 lesson(s) reinforced\033[0m")

    if user_context and len(user_context.strip()) >= 15:
        lines.append(
            f"  \033[36m→ Feedback context captured: Claude will be asked to draft via create_lesson\033[0m"
        )

    lines.append("")

    try:
        with open(live_log, "a", encoding="utf-8") as f:
            f.write("\n".join(lines) + "\n")
    except Exception:
        pass


def _normalize_lesson(text):
    return " ".join(str(text).lower().split()).rstrip(".")


def _format_episode(ep, seen_lessons, sanitize):
    """Render one episode as `[category] lesson (context)`, or None if it repeats a shown lesson."""
    cat = ep.get("category", "")
    text = sanitize(ep.get("text", ""))
    prefix = f"[{cat}] "
    while text.startswith(prefix):
        text = text[len(prefix):]
    lesson, _, context = text.partition(" Context: ")
    lesson, context = lesson.strip(), context.strip()
    key = _normalize_lesson(lesson)
    if not key or key in seen_lessons:
        return None
    seen_lessons.add(key)
    norm_context = _normalize_lesson(context)
    if context and norm_context not in key and key not in norm_context:
        return f"[{cat}] {lesson} (context: {context})"
    return f"[{cat}] {lesson}"


def main():
    try:
        hook_input = json.load(sys.stdin)
    except (json.JSONDecodeError, EOFError):
        return

    user_message = hook_input.get("prompt", "")

    # ── Check for feedback signals before length filter ──────────────
    sentiment, user_context = (
        detect_feedback_signal(user_message) if user_message else (None, None)
    )
    session_id = hook_input.get("session_id", "")
    if sentiment:
        user_context = user_context or ""
        recurring = (
            find_recurring_lessons(user_context, session_id=session_id)
            if sentiment == "negative" and user_context
            else []
        )
        if recurring:
            # The lesson was right and was ignored: strengthen it, don't demote it
            reinforce_recent_lessons("positive", session_id=session_id, only_ids=set(recurring))
            emit_recurrence_context(storage_path_or_none(), user_context, recurring)
            return
        # Hook-level reinforcement — no Claude involvement needed
        reinforcement_results = reinforce_recent_lessons(sentiment, session_id=session_id)

        # Per-lesson Thompson attribution — the RLHF reinforcement loop
        try:
            storage_path_for_thompson = get_storage_path()
            from smartassist.thompson_rerank import (
                attribute_feedback,
                update_thompson_batch,
            )

            last = load_last_injection(session_id)
            if last:
                injected_for_attribution = []
                for key, lesson_id in last.items():
                    if key.startswith("_"):
                        continue
                    injected_for_attribution.append(
                        {
                            "id": lesson_id,
                            "score": 0.5,  # default relevance weight
                            "injection_timestamp": last.get("_timestamp", time.time()),
                        }
                    )
                if injected_for_attribution:
                    attributions = attribute_feedback(
                        sentiment, injected_for_attribution
                    )
                    update_thompson_batch(storage_path_for_thompson, attributions)
        except Exception:
            pass  # Never break the hook over Thompson updates

        # Immediate lesson creation — guaranteed, no LLM dependency
        created_id, created_lesson = None, None
        if user_context:
            created_id, created_lesson = create_lesson_from_feedback(
                user_context,
                sentiment,
                reinforcement_results,
            )
            if created_id:
                # Fire-and-forget: make lesson searchable via rag_search
                from smartassist.config import spawn_background_job

                spawn_background_job("smartassist.hooks.vectorize_learnings", lock_name="vectorize")

        try:
            storage_path = get_storage_path()

            # Log hook's result for A/B comparison
            if user_context and len(user_context.strip()) >= 15:
                log_comparison_entry(
                    storage_path,
                    "hook",
                    sentiment,
                    user_context,
                    created_lesson,
                    created_lesson is not None,
                )

            write_to_live_log_feedback(
                storage_path,
                user_message.strip(),
                sentiment,
                user_context=user_context,
                reinforcement_results=reinforcement_results,
            )
        except RuntimeError:
            storage_path = None

        context = build_rich_feedback_context(
            sentiment,
            user_context,
            reinforcement_results,
            created_lesson=(created_id, created_lesson),
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
        lessons = current_lessons(storage_path)
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
    results = search_lessons(
        query_tokens, lessons, idf, top_k=CANDIDATE_DEPTH, lesson_scores=lesson_scores
    )
    semantic = semantic_search(user_message, query_tokens, lessons, idf, lesson_scores, storage_path)
    if semantic is not None:
        results = semantic
    results = [r for r in results if r["score"] >= 0.15]

    # Thompson Sampling reranking — the RLHF reinforcement loop
    try:
        from smartassist.thompson_rerank import (
            thompson_rerank,
            load_thompson_batch,
            record_injection,
        )

        lesson_ids = [r.get("id", "") for r in results if r.get("id")]
        if lesson_ids:
            thompson_data = load_thompson_batch(storage_path, lesson_ids)
            results = thompson_rerank(results, thompson_data)
    except Exception:
        pass  # Fall back to non-Thompson ranking

    # Session deduplication
    already_injected = set()
    deduped_results = []
    if session_id:
        already_injected = load_session_state(session_id)
        deduped_results = [r for r in results if r.get("id") in already_injected]
        results = [r for r in results if r.get("id") not in already_injected]

    write_to_live_log(
        storage_path,
        user_message,
        results,
        query_tokens,
        expanded,
        deduped_results=deduped_results,
    )

    results = apply_injection_budget(results)

    all_candidates = list(results) + list(deduped_results)
    if all_candidates:
        save_last_injection(all_candidates, session_id=session_id)

    if not results:
        return

    # Record injection for Thompson tracking
    try:
        from smartassist.thompson_rerank import record_injection

        injected_ids = [str(r["id"]) for r in results if r.get("id")]
        if injected_ids:
            record_injection(storage_path, injected_ids)
    except Exception:
        pass

    if session_id:
        new_ids = {str(r["id"]) for r in results if r.get("id")}
        already_injected.update(new_ids)
        save_session_state(session_id, already_injected)

    # ── MemAlign dual-memory injection ─────────────────────────────────
    # Semantic memory (lessons/principles) + Episodic memory (past corrections/examples)
    def _sanitize(text):
        """Strip characters that could be interpreted as prompt directives."""
        for prefix in ("ignore ", "disregard ", "forget "):
            if text.lower().startswith(prefix + "all ") or text.lower().startswith(
                prefix + "previous "
            ):
                text = text[len(prefix) :]
        return text.replace("\n", " ").replace("\r", " ").strip()

    # Retrieve episodic memory (recent corrections/feedback events matching this query)
    episodes = []
    try:
        from smartassist.store import search_projection_documents

        from smartassist.project_facts import stale_reason
        from smartassist.store import evidence_event_ids, project_root_for_storage

        ep_results, _ = search_projection_documents(
            storage_path,
            user_message,
            top_k=6,
            category=None,
            source_types=["event"],
        )
        # Past corrections must be the user's, and still true for this project
        evidence = evidence_event_ids(storage_path, [ep.get("source_id", "") for ep in ep_results])
        root = project_root_for_storage(storage_path)
        episodes.extend(relevant_episodes(ep_results, evidence, root, stale_reason, query_tokens))
    except Exception:
        pass

    # Split results into lessons (semantic memory) and any events that came through
    semantic_lessons = [
        r for r in results if r.get("source_type", "lesson") == "lesson" or "id" in r
    ]

    # Format semantic memory (principles)
    parts = []
    if semantic_lessons:
        lesson_lines = [
            f"[{r.get('id', '?')}] [{r['category']}] {_sanitize(r['lesson'])}"
            for r in semantic_lessons
        ]
        parts.append(
            "Project-specific rules (apply these):\n"
            + "\n".join(f"- {l}" for l in lesson_lines)
        )

    # Format episodic memory (past corrections relevant to this query)
    seen_lessons = {_normalize_lesson(r.get("lesson", "")) for r in semantic_lessons}
    episode_lines = []
    for ep in episodes[:2]:  # max 2 episodes to avoid context bloat
        line = _format_episode(ep, seen_lessons, _sanitize)
        if line:
            episode_lines.append(line)
    if episode_lines:
        parts.append(
            "Past corrections on similar work:\n"
            + "\n".join(f"- {l}" for l in episode_lines)
        )

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
