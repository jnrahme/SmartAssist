"""Shared feedback module for RAG lesson scoring.

Used by both hooks/prompt_inject.py and claude-rag-monitor to manage
per-lesson scores, last injection state, and feedback commands.
"""

import json
import logging
import re
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path

log = logging.getLogger(__name__)

from smartassist.config import (
    get_storage_path,
    atomic_write_json,
    spawn_managed,
)
from smartassist.store import (
    add_lesson,
    append_feedback_event,
    list_lessons,
    load_last_injection_map,
    load_lesson_scores_dict,
    load_session_state_map,
    retire_lesson,
    save_last_injection_map,
    save_lesson_scores_dict,
    save_session_state_map,
)

DEFAULT_BOOST = 1.0
BOOST_INCREMENT = 0.3
DEMOTE_DECREMENT = 0.4
BOOST_CAP = 3.0
BOOST_FLOOR = 0.0
MAX_CURATED_LESSONS = 300


def _scores_path() -> Path:
    return get_storage_path() / "lesson_scores.json"


def _injection_path() -> Path:
    return get_storage_path() / "last_injection.json"


def _session_state_path() -> Path:
    return get_storage_path() / "rag_session_state.json"


def load_lesson_scores():
    """Load lesson scores from disk. Returns dict of {id: {boost, ups, downs, blocked}}."""
    try:
        return load_lesson_scores_dict(get_storage_path())
    except Exception:
        return {}


def save_lesson_scores(data):
    """Write lesson scores to disk."""
    save_lesson_scores_dict(get_storage_path(), data)


def load_last_injection():
    """Load last injection mapping. Returns dict of {"1": "L026", "2": "L094", ...}."""
    try:
        return load_last_injection_map(get_storage_path())
    except Exception:
        return {}


def save_last_injection(results):
    """Write last injection mapping from ranked results list.

    Args:
        results: list of dicts with "id" keys, in display order.
    """
    mapping = {str(i + 1): r["id"] for i, r in enumerate(results)}
    mapping["_timestamp"] = time.time()
    save_last_injection_map(get_storage_path(), mapping)


def load_session_state(session_id):
    """Load session dedup state. Returns set of already-injected lesson IDs.

    Clears state if session_id has changed.
    """
    try:
        return load_session_state_map(get_storage_path(), session_id)
    except Exception:
        return set()


def save_session_state(session_id, injected_ids):
    """Write session dedup state."""
    save_session_state_map(get_storage_path(), session_id, injected_ids)


def get_or_create_score(scores, lesson_id):
    """Get existing score entry or create default.

    New V2 fields (retired, retired_reason, retired_at) are backwards-compatible:
    existing entries without these keys get defaults via .get() in callers.
    """
    if lesson_id not in scores:
        scores[lesson_id] = {
            "boost": DEFAULT_BOOST,
            "ups": 0,
            "downs": 0,
            "blocked": False,
            "retired": False,
            "retired_reason": "",
            "retired_at": None,
        }
    return scores[lesson_id]


def apply_feedback(command):
    """Parse and apply a feedback command. Returns (success, message).

    Commands:
        +N  -- promote lesson #N from last injection (boost += 0.3, cap 3.0)
        -N  -- demote lesson #N (boost -= 0.4, floor 0.0)
        xN  -- block lesson #N permanently
        u ID -- unblock a lesson by ID (e.g., u L026)
    """
    command = command.strip()
    if not command:
        return False, "Empty command"

    scores = load_lesson_scores()
    last = load_last_injection()

    # Unblock command: u L026
    if command.startswith("u "):
        lesson_id = command[2:].strip().upper()
        if lesson_id in scores and scores[lesson_id].get("blocked"):
            scores[lesson_id]["blocked"] = False
            scores[lesson_id]["boost"] = DEFAULT_BOOST
            save_lesson_scores(scores)
            return True, f"Unblocked {lesson_id} (boost reset to {DEFAULT_BOOST})"
        return False, f"{lesson_id} is not blocked"

    # +N, -N, xN commands
    if len(command) < 2:
        return False, f"Unknown command: {command}"

    action = command[0]
    if action not in ("+", "-", "x"):
        return False, f"Unknown command: {command}"

    try:
        slot = command[1:]
        slot_num = int(slot)
    except ValueError:
        return False, f"Invalid slot number: {command}"

    slot_key = str(slot_num)
    if slot_key not in last:
        visible_slots = sum(1 for key in last if not key.startswith("_"))
        return False, (
            f"No lesson at slot #{slot_num} (last injection had {visible_slots} lessons)"
        )

    lesson_id = last[slot_key]
    entry = get_or_create_score(scores, lesson_id)

    if action == "+":
        entry["ups"] += 1
        entry["boost"] = min(entry["boost"] + BOOST_INCREMENT, BOOST_CAP)
        save_lesson_scores(scores)
        return True, f"Promoted {lesson_id} (boost: {entry['boost']:.1f}x, ups: {entry['ups']})"

    if action == "-":
        entry["downs"] += 1
        entry["boost"] = max(entry["boost"] - DEMOTE_DECREMENT, BOOST_FLOOR)
        save_lesson_scores(scores)
        return True, f"Demoted {lesson_id} (boost: {entry['boost']:.1f}x, downs: {entry['downs']})"

    if action == "x":
        entry["blocked"] = True
        entry["boost"] = 0.0
        save_lesson_scores(scores)
        return True, f"Blocked {lesson_id} permanently (use 'u {lesson_id}' to unblock)"

    return False, f"Unknown command: {command}"


def remove_from_curated(storage_path, lesson_id):
    """Retire a lesson from the active corpus. No-op if missing."""
    try:
        retire_lesson(storage_path, lesson_id)
    except Exception as exc:
        log.warning("Failed to retire %s from the active corpus: %s", lesson_id, exc)


def _trigger_full_revectorization():
    """Rebuild LanceDB from the curated lesson corpus after structural changes."""
    try:
        spawn_managed([sys.executable, "-m", "smartassist.tools.cleanup_and_vectorize"])
    except Exception:
        pass


def reinforce_recent_lessons(sentiment, max_age=900):
    """Auto-boost or demote all recently injected lessons.

    Called directly by the hook — no Claude involvement needed.

    Returns list of (lesson_id, old_boost, new_boost, retired) tuples.
    Returns [] if no recent injection or stale.
    """
    last = load_last_injection()
    if not last:
        return []

    # Staleness check
    injection_time = last.get("_timestamp", 0)
    if injection_time and (time.time() - injection_time) > max_age:
        return []

    scores = load_lesson_scores()
    storage_path = get_storage_path()
    results = []
    needs_rebuild = False

    for key, lesson_id in last.items():
        if key.startswith("_"):
            continue

        entry = get_or_create_score(scores, lesson_id)

        # Skip blocked/retired lessons
        if entry.get("blocked", False) or entry.get("retired", False):
            continue

        old_boost = entry["boost"]

        if sentiment == "positive":
            entry["ups"] += 1
            entry["boost"] = min(entry["boost"] + BOOST_INCREMENT, BOOST_CAP)
            retired = False
        else:
            entry["downs"] += 1
            entry["boost"] = max(entry["boost"] - DEMOTE_DECREMENT, BOOST_FLOOR)
            # Auto-retire: boost at 0.0 AND never been helpful
            retired = False
            if entry["boost"] <= BOOST_FLOOR and entry.get("ups", 0) == 0:
                entry["blocked"] = True
                entry["retired"] = True
                entry["retired_reason"] = "auto-retired: boost 0.0 with 0 positive feedback"
                entry["retired_at"] = datetime.now().isoformat()
                retired = True
                remove_from_curated(storage_path, lesson_id)
                needs_rebuild = True

        results.append((lesson_id, old_boost, entry["boost"], retired))

    save_lesson_scores(scores)
    if needs_rebuild:
        _trigger_full_revectorization()
    return results


def add_to_curated(storage_path, lesson_text, category):
    """Append a lesson to the active corpus. Returns (new_id, None) or (None, error_msg)."""
    return add_lesson(storage_path, lesson_text, category, origin="lesson_feedback")


# ── Shared quality-gate constants (used by both hook path and MCP tools) ──

ACTION_VERBS = {
    # existing 31 verbs
    "use", "never", "always", "avoid", "prefer", "check", "run", "ensure",
    "include", "test", "verify", "add", "remove", "replace", "apply", "call",
    "import", "export", "wrap", "split", "move", "keep", "delete", "update",
    "follow", "mock", "assert", "validate", "configure", "set", "create",
    # new additions
    "don't", "do", "handle", "implement", "extract", "document", "define",
    "initialize",
}

GENERIC_STARTS = {
    "good job", "be careful", "remember to", "nice work", "well done",
    "great job", "bad job", "try to", "make sure to",
}

# ── Feedback transforms: (prefix, replacement) ───────────────────────────
# Matched longest-first. Only one prefix stripped per input.

_FEEDBACK_TRANSFORMS = [
    # "use of X" patterns — preserve the verb as "Use X"
    ("good use of ",      "Use "),
    ("great use of ",     "Use "),
    ("nice use of ",      "Use "),
    ("excellent use of ", "Use "),
    # "job on X" patterns — strip completely
    ("good job on ",  ""),
    ("great job on ", ""),
    ("nice job on ",  ""),
    ("good job ",     ""),
    ("great job ",    ""),
    ("nice job ",     ""),
    # "i like/love" patterns — strip, remaining text has the verb
    ("i like how you ",  ""),
    ("i liked how you ", ""),
    ("i like the ",      ""),
    ("i liked the ",     ""),
    ("love how you ",    ""),
    ("love the ",        ""),
    # bare adjectives — strip
    ("good ",      ""),
    ("great ",     ""),
    ("nice ",      ""),
    ("excellent ", ""),
    ("bad ",       ""),
    ("poor ",      ""),
    ("terrible ",  ""),
]

# ── Contraction normalization ─────────────────────────────────────────────

_CONTRACTIONS = {
    "dont":     "don't",
    "cant":     "can't",
    "shouldnt": "shouldn't",
    "wouldnt":  "wouldn't",
    "isnt":     "isn't",
    "hasnt":    "hasn't",
    "didnt":    "didn't",
    "doesnt":   "doesn't",
    "couldnt":  "couldn't",
    "wont":     "won't",
}


def _context_to_lesson(user_context):
    """Convert user feedback context to an imperative lesson statement.

    Pipeline:
      1. Strip praise/complaint prefixes (preserving verbs where possible)
      2. Normalize contractions ("dont" → "don't")
      3. Sanitize conversational wrapping + capitalize + strip emojis
      4. Quality gate: length >= 30
      5. Quality gate: no generic starts
      6. Quality gate: must contain action verb

    Returns None if the result fails any quality gate.
    """
    from smartassist.tools.cleanup_and_vectorize import sanitize_to_lesson

    text = user_context.strip()
    lower = text.lower()

    # 1. Apply praise/complaint transforms (longest prefix first)
    for prefix, replacement in sorted(_FEEDBACK_TRANSFORMS, key=lambda t: len(t[0]), reverse=True):
        if lower.startswith(prefix):
            text = replacement + text[len(prefix):]
            break

    # 2. Normalize contractions ("dont" → "don't")
    for informal, formal in _CONTRACTIONS.items():
        text = re.sub(r'\b' + informal + r'\b', formal, text, flags=re.IGNORECASE)

    # 3. Strip conversational wrapping + capitalize + strip emojis
    text = sanitize_to_lesson(text)

    # 4. Quality gate: length
    if len(text) < 30:
        return None

    # 5. Quality gate: no generic starts
    text_lower = text.lower()
    for generic in GENERIC_STARTS:
        if text_lower.startswith(generic):
            return None

    # 6. Quality gate: must contain action verb
    has_verb = any(
        f" {verb} " in f" {text_lower} " or text_lower.startswith(f"{verb} ")
        for verb in ACTION_VERBS
    )
    if not has_verb:
        return None

    return text


_CATEGORY_KEYWORDS = {
    "testing": ["test", "jest", "mock", "e2e", "coverage", "detox", "assertion", "spec"],
    "git": ["git", "commit", "branch", "merge", "push", "rebase", "ticket"],
    "code_edit": ["style", "lint", "format", "component", "import", "color", "theme",
                  "pattern", "refactor"],
    "architecture": ["architecture", "structure", "directory", "module", "folder"],
    "security": ["security", "auth", "credential", "token", "secret", "env"],
    "debugging": ["debug", "error", "crash", "log", "stack trace"],
    "pr_review": ["pr", "review", "pull request", "approval"],
}


def _infer_category_from_text(text):
    """Infer category by keyword matching against the feedback text."""
    lower = text.lower()
    for cat, keywords in _CATEGORY_KEYWORDS.items():
        for kw in keywords:
            if " " in kw:
                if kw in lower:
                    return cat
            elif len(kw) <= 3:
                if re.search(rf"\b{re.escape(kw)}\b", lower):
                    return cat
            else:
                if re.search(rf"\b{re.escape(kw)}", lower):
                    return cat
    return None


def _infer_category(reinforcement_results, storage_path, user_context=""):
    """Infer lesson category from boosted lessons, then feedback text, then default."""
    # 1. Majority vote from reinforced lessons
    if reinforcement_results:
        curated_map = {
            lesson.get("id", ""): lesson.get("category", "")
            for lesson in list_lessons(storage_path)
        }

        counts = {}
        for lid, _, _, _ in reinforcement_results:
            cat = curated_map.get(lid, "")
            if cat:
                counts[cat] = counts.get(cat, 0) + 1

        if counts:
            return max(counts, key=counts.get)

    # 2. Keyword match against the user's feedback text
    if user_context:
        matched = _infer_category_from_text(user_context)
        if matched:
            return matched

    return "code_edit"


def create_lesson_from_feedback(user_context, sentiment, reinforcement_results):
    """Create a lesson directly from user feedback context. No Claude involvement.

    Returns (new_id, lesson_text) or (None, None) if context is too vague.
    """
    lesson_text = _context_to_lesson(user_context)
    if not lesson_text:
        return None, None

    storage_path = get_storage_path()
    category = _infer_category(reinforcement_results, storage_path, user_context)

    new_id, error = add_to_curated(storage_path, lesson_text, category)
    if error:
        return None, None

    # Update Thompson Sampling for the new lesson's category
    try:
        from smartassist.thompson_sampling import ThompsonSamplingModel
        thompson = ThompsonSamplingModel(str(storage_path))
        if sentiment == "positive":
            thompson.record_success(category, 3)
        else:
            thompson.record_failure(category, 3)
    except Exception:
        pass

    signal = "thumbs_up" if sentiment == "positive" else "correction"
    append_feedback_event(storage_path, {
        "timestamp": time.time(),
        "signal": signal,
        "category": category,
        "intensity": 3,
        "query": "",
        "response": "",
        "correction": lesson_text,
        "context": f"hook-created from feedback: {user_context}",
    })

    return new_id, lesson_text


def log_comparison_entry(storage_path, source, sentiment, feedback_context,
                         lesson_text, passed_gates):
    """Log a lesson draft to the comparison file for A/B analysis.

    Args:
        storage_path: Path to the data directory.
        source: "hook" or "claude"
        sentiment: "positive" or "negative"
        feedback_context: The user's original feedback context string.
        lesson_text: The lesson text (or None if gates failed).
        passed_gates: Whether it passed quality gates.
    """
    entry = {
        "timestamp": time.time(),
        "source": source,
        "sentiment": sentiment,
        "feedback_context": feedback_context,
        "lesson_text": lesson_text,
        "passed_gates": passed_gates,
    }
    comparison_log = storage_path / "lesson_comparison.jsonl"
    try:
        with open(comparison_log, "a", encoding="utf-8") as f:
            f.write(json.dumps(entry) + "\n")
    except Exception:
        pass


def format_scores_table(scores=None):
    """Format all lesson scores as a readable table."""
    if scores is None:
        scores = load_lesson_scores()
    if not scores:
        return "No lesson scores recorded yet."

    lines = []
    lines.append(f"  {'ID':<6} {'Boost':>6} {'Ups':>4} {'Downs':>5} {'Status':<10}")
    lines.append(f"  {chr(9472)*6} {chr(9472)*6} {chr(9472)*4} {chr(9472)*5} {chr(9472)*10}")
    for lid in sorted(scores.keys()):
        s = scores[lid]
        status = "\033[31mBLOCKED\033[0m" if s.get("blocked") else "active"
        boost = f"{s.get('boost', 1.0):.1f}x"
        lines.append(f"  {lid:<6} {boost:>6} {s.get('ups', 0):>4} {s.get('downs', 0):>5} {status:<10}")
    return "\n".join(lines)
