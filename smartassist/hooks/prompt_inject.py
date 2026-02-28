#!/usr/bin/env python3
"""
UserPromptSubmit Hook — Search RAG lessons and inject into Claude's context.

Searches curated_lessons.json using fast keyword matching (pure stdlib, <50ms).
1. Writes matched lessons to rag_live.log (for the claude-rag monitor terminal)
2. Injects relevant lessons into Claude's prompt as <rag-context>
3. Applies per-lesson scoring (boost/block from feedback)
4. Deduplicates lessons already injected in the same session
"""

import sys
import json
import re
from math import log
from datetime import datetime

from smartassist.config import get_storage_path
from smartassist.lesson_feedback import (
    load_lesson_scores,
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


def main():
    try:
        hook_input = json.load(sys.stdin)
    except (json.JSONDecodeError, EOFError):
        return

    user_message = hook_input.get("prompt", "")

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

    injection_lessons = [f"[{r['category']}] {r['lesson']}" for r in results]
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
