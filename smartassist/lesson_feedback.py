"""Shared feedback module for RAG lesson scoring.

Used by both hooks/prompt_inject.py and claude-rag-monitor to manage
per-lesson scores, last injection state, and feedback commands.
"""

import json
from pathlib import Path

from smartassist.config import get_storage_path

DEFAULT_BOOST = 1.0
BOOST_INCREMENT = 0.3
DEMOTE_DECREMENT = 0.4
BOOST_CAP = 3.0
BOOST_FLOOR = 0.0


def _scores_path() -> Path:
    return get_storage_path() / "lesson_scores.json"


def _injection_path() -> Path:
    return get_storage_path() / "last_injection.json"


def _session_state_path() -> Path:
    return get_storage_path() / "rag_session_state.json"


def load_lesson_scores():
    """Load lesson scores from disk. Returns dict of {id: {boost, ups, downs, blocked}}."""
    try:
        return json.loads(_scores_path().read_text())
    except Exception:
        return {}


def save_lesson_scores(data):
    """Write lesson scores to disk."""
    _scores_path().write_text(json.dumps(data, indent=2))


def load_last_injection():
    """Load last injection mapping. Returns dict of {"1": "L026", "2": "L094", ...}."""
    try:
        return json.loads(_injection_path().read_text())
    except Exception:
        return {}


def save_last_injection(results):
    """Write last injection mapping from ranked results list.

    Args:
        results: list of dicts with "id" keys, in display order.
    """
    mapping = {str(i + 1): r["id"] for i, r in enumerate(results)}
    _injection_path().write_text(json.dumps(mapping, indent=2))


def load_session_state(session_id):
    """Load session dedup state. Returns set of already-injected lesson IDs.

    Clears state if session_id has changed.
    """
    try:
        data = json.loads(_session_state_path().read_text())
        if data.get("session_id") == session_id:
            return set(data.get("injected_ids", []))
    except Exception:
        pass
    return set()


def save_session_state(session_id, injected_ids):
    """Write session dedup state."""
    _session_state_path().write_text(json.dumps({
        "session_id": session_id,
        "injected_ids": sorted(injected_ids),
    }, indent=2))


def _get_or_create_score(scores, lesson_id):
    """Get existing score entry or create default."""
    if lesson_id not in scores:
        scores[lesson_id] = {
            "boost": DEFAULT_BOOST,
            "ups": 0,
            "downs": 0,
            "blocked": False,
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
        return False, f"No lesson at slot #{slot_num} (last injection had {len(last)} lessons)"

    lesson_id = last[slot_key]
    entry = _get_or_create_score(scores, lesson_id)

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
