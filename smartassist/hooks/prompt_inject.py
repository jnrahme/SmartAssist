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
    DEFAULT_BOOST,
)

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
    "test": {"testing", "tests", "jest", "mock", "mocks", "assertion"},
    "tests": {"testing", "test", "jest", "mock"},
    "testing": {"test", "tests", "jest", "mock"},
    "style": {"styles", "styling", "color", "colors", "theme", "semantic"},
    "styles": {"style", "styling", "color", "theme"},
    "color": {"colors", "semantic", "theme", "hex", "style"},
    "component": {"components", "react", "render", "jsx"},
    "redux": {"store", "dispatch", "selector", "slice", "state"},
    "git": {"commit", "branch", "merge", "push"},
    "commit": {"git", "message", "branch"},
    "import": {"imports", "export", "module", "require"},
    "type": {"types", "typescript", "interface", "generics"},
    "typescript": {"types", "type", "interface"},
    "error": {"errors", "catch", "throw", "exception", "handling"},
    "mock": {"mocks", "jest", "testing", "spy"},
    "hook": {"hooks", "useeffect", "usestate", "custom"},
    "navigation": {"navigate", "route", "routes", "router", "screen"},
    "api": {"fetch", "request", "response", "endpoint", "http"},
    "auth": {"authentication", "login", "cognito", "token", "session"},
    "firebase": {"analytics", "crashlytics", "remoteconfig"},
    "performance": {"optimize", "memo", "usememo", "usecallback", "flashlist"},
}

# ── Max age for injection staleness ──────────────────────────────────────
MAX_INJECTION_AGE = 300  # 5 minutes


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
    return {t: log(n / df) for t, df in doc_freq.items()}


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
        counter_file.write_text(json.dumps({
            "prompt_count": prompt_count,
            "inject_count": inject_count,
        }))
    except Exception:
        pass


def write_to_live_log(storage_path, user_message, results, query_tokens=None,
                      expanded_tokens=None, deduped_results=None):
    """Write formatted output to rag_live.log for the monitor terminal."""
    live_log = storage_path / "rag_live.log"
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
        with open(live_log, "a") as f:
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

    # Load curated lessons for full text + category
    curated_path = storage_path / "curated_lessons.json"
    curated_map = {}
    if curated_path.exists():
        try:
            lessons = json.loads(curated_path.read_text())
            curated_map = {l.get("id", ""): l for l in lessons}
        except Exception:
            pass

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


def build_rich_feedback_context(sentiment, user_context, storage_path):
    """Build rich context for Claude to make per-lesson feedback decisions.

    Replaces the old build_lesson_instructions() with a structured decision framework
    that shows Claude the actual lessons, their scores, and lets it decide per-lesson.
    """
    lessons = []
    if storage_path:
        lessons = _reconstruct_injected_lessons(storage_path)

    if sentiment == "positive":
        mood = "POSITIVE"
        guidance = "The user is happy with your recent actions. Reinforce what worked."
    else:
        mood = "NEGATIVE"
        guidance = "The user is unhappy with your recent actions. Identify what went wrong."

    parts = [f"FEEDBACK SIGNAL: {mood}", guidance, ""]

    if user_context:
        parts.append(f'User context: "{user_context}"')
        parts.append("")

    if lessons:
        parts.append("RECENTLY INJECTED LESSONS:")
        parts.append(f"{'ID':<6} {'Category':<14} {'Boost':>6} {'Ups':>4} {'Downs':>5} {'Conf':>6}  Lesson")
        parts.append(f"{'-'*6} {'-'*14} {'-'*6} {'-'*4} {'-'*5} {'-'*6}  {'-'*40}")
        for l in lessons:
            parts.append(
                f"{l['id']:<6} {l['category']:<14} {l['boost']:>5.1f}x {l['ups']:>4} {l['downs']:>5} "
                f"{l['confidence']:>5.0%}  {l['lesson'][:60]}"
            )
        parts.append("")
        parts.append("DECISION FRAMEWORK — evaluate EACH lesson above:")
        parts.append(f"  RULE 1: If lesson was {'helpful' if sentiment == 'positive' else 'harmful/irrelevant'} → "
                     f"call `{'boost_lesson' if sentiment == 'positive' else 'demote_lesson'}(lesson_id)`")
        parts.append(f"  RULE 2: If lesson was NOT relevant to what just happened → SKIP (do nothing)")
        parts.append(f"  RULE 3: If two+ lessons overlap or say the same thing → "
                     f"call `merge_lessons(lesson_ids, new_lesson, category)`")
        parts.append(f"  RULE 4: If the feedback is about something NOT covered by any lesson above → "
                     f"call `create_lesson(lesson, category, sentiment, intensity, context)`")
        parts.append("")
        parts.append("CONSTRAINTS:")
        parts.append("  - Max 5 tool calls total for this feedback round")
        parts.append("  - Lessons with confidence <20% are unreliable — consider demoting or merging")
        parts.append("  - Lessons with boost >=2.5x are already highly valued — only boost if clearly relevant")
    else:
        parts.append("NO LESSONS WERE RECENTLY INJECTED — create a new lesson instead.")
        parts.append("")
        parts.append("Call `create_lesson` with:")
        parts.append("  - lesson: Imperative statement (>30 chars) with action verb, project-specific")
        parts.append("  - category: One of: testing, code_edit, git, architecture, pr_review, security, debugging")
        parts.append(f'  - sentiment: "{sentiment}"')
        parts.append("  - intensity: 1-5")
        parts.append("  - context: Brief context about what happened")

    parts.append("")
    parts.append("After making your tool calls, briefly acknowledge the feedback to the user.")

    return "\n".join(parts)


def write_to_live_log_feedback(storage_path, signal_text, sentiment, user_context=""):
    """Write feedback detection event to rag_live.log for the monitor terminal."""
    live_log = storage_path / "rag_live.log"
    now = datetime.now().strftime("%H:%M:%S")

    prompt_count, inject_count = read_counter(storage_path)
    prompt_count += 1
    write_counter(storage_path, prompt_count, inject_count)

    # Count lessons for display
    lessons = _reconstruct_injected_lessons(storage_path)
    lesson_count = len(lessons)

    lines = []
    lines.append("")
    lines.append(f"\033[90m{'=' * 60}\033[0m")
    lines.append(f"\033[90m  {now}  |  Prompt #{prompt_count}\033[0m")

    color = "\033[32m" if sentiment == "positive" else "\033[31m"
    lines.append(f"{color}\033[1m  FEEDBACK DETECTED {signal_text}\033[0m")
    lines.append(f"  Sentiment: {sentiment}")

    if user_context:
        lines.append(f"  Context: \"{user_context}\"")

    lines.append(f"  \033[90m{lesson_count} lesson(s) sent to Claude for per-lesson decisions\033[0m")
    lines.append("")

    try:
        with open(live_log, "a") as f:
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
        try:
            storage_path = get_storage_path()
            write_to_live_log_feedback(
                storage_path, user_message.strip(), sentiment,
                user_context=user_context,
            )
        except RuntimeError:
            storage_path = None

        context = build_rich_feedback_context(sentiment, user_context, storage_path)
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

    curated_path = storage_path / "curated_lessons.json"
    if not curated_path.exists():
        return

    try:
        lessons = json.loads(curated_path.read_text())
    except Exception:
        return

    query_tokens = tokenize(user_message)
    if not query_tokens:
        return

    lesson_scores = load_lesson_scores()

    expanded = expand_query(query_tokens)
    idf = build_idf(lessons)
    results = search_lessons(query_tokens, lessons, idf, lesson_scores=lesson_scores)
    results = [r for r in results if r["score"] >= 0.20]

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

    if session_id:
        new_ids = {r.get("id") for r in results if r.get("id")}
        already_injected.update(new_ids)
        save_session_state(session_id, already_injected)

    # V2: Include lesson IDs in injection format
    injection_lessons = [f"[{r.get('id', '?')}] [{r['category']}] {r['lesson']}" for r in results]
    context = "Project-specific lessons from our RAG knowledge base:\n"
    context += "\n".join(f"- {l}" for l in injection_lessons)

    output = {
        "hookSpecificOutput": {
            "hookEventName": "UserPromptSubmit",
            "additionalContext": context,
        }
    }
    print(json.dumps(output))


if __name__ == "__main__":
    main()
