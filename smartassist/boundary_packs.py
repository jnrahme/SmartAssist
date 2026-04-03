"""Boundary-pack assembly from repeated negative feedback.

This module turns repeated bad feedback into bounded startup context so the
next Claude session begins with the most important project-specific mistakes
already loaded.
"""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from typing import Any

from smartassist.config import atomic_write_json, get_storage_path, locked_update_json
from smartassist.gates import get_prevention_rules_path
from smartassist.thompson_sampling import ThompsonSamplingModel
from smartassist.tools.cleanup_and_vectorize import clean_correction_text, get_dedup_key

BOUNDARY_PACK_VERSION = 1
NEGATIVE_SIGNALS = {"thumbs_down", "angry", "correction", "sad"}
PROMOTION_THRESHOLD = 2
MAX_PROMOTED_BOUNDARIES = 5
MAX_RECENT_MISTAKES = 5
SAMPLE_TEXT_LIMIT = 240


def get_boundary_pack_path(storage_path: Path | None = None) -> Path | None:
    """Return the boundary-pack file path."""
    storage = _resolve_storage_path(storage_path)
    if storage is None:
        return None
    return storage / "boundary_pack.json"


def load_boundary_pack(storage_path: Path | None = None) -> dict[str, Any] | None:
    """Load a stored boundary pack."""
    pack_path = get_boundary_pack_path(storage_path)
    if pack_path is None or not pack_path.exists():
        return None

    try:
        raw = json.loads(pack_path.read_text())
    except (OSError, json.JSONDecodeError):
        return None

    if not isinstance(raw, dict):
        return None
    return raw


def ensure_boundary_pack(storage_path: Path | None = None) -> dict[str, Any]:
    """Load a fresh boundary pack, rebuilding it when missing or stale."""
    storage = _resolve_storage_path(storage_path)
    if storage is None:
        return _empty_boundary_pack()

    pack_path = storage / "boundary_pack.json"
    if not pack_path.exists():
        return refresh_boundary_pack(storage)

    if _pack_is_stale(storage, pack_path):
        return refresh_boundary_pack(storage)

    pack = load_boundary_pack(storage)
    if pack is None:
        return refresh_boundary_pack(storage)

    return pack


def refresh_boundary_pack(
    storage_path: Path | None = None,
    *,
    threshold: float = 0.70,
    promotion_threshold: int = PROMOTION_THRESHOLD,
    max_promoted: int = MAX_PROMOTED_BOUNDARIES,
    max_recent: int = MAX_RECENT_MISTAKES,
) -> dict[str, Any]:
    """Rebuild prevention metadata and write the latest boundary pack."""
    storage = _resolve_storage_path(storage_path)
    if storage is None:
        return _empty_boundary_pack()

    pack = build_boundary_pack(
        storage,
        threshold=threshold,
        promotion_threshold=promotion_threshold,
        max_promoted=max_promoted,
        max_recent=max_recent,
    )
    _update_prevention_rules(storage, pack["promoted_boundaries"])
    atomic_write_json(storage / "boundary_pack.json", pack)
    return pack


def build_boundary_pack(
    storage_path: Path,
    *,
    threshold: float = 0.70,
    promotion_threshold: int = PROMOTION_THRESHOLD,
    max_promoted: int = MAX_PROMOTED_BOUNDARIES,
    max_recent: int = MAX_RECENT_MISTAKES,
) -> dict[str, Any]:
    """Build a bounded pack of high-salience boundaries and recent mistakes."""
    events = _load_feedback_events(storage_path)
    thompson = ThompsonSamplingModel(str(storage_path))
    weak_categories = thompson.get_weak_categories(threshold=threshold)
    reliability_scores = thompson.get_all_reliabilities()

    promoted = build_promoted_boundaries(
        events,
        weak_categories=weak_categories,
        promotion_threshold=promotion_threshold,
    )
    recent = build_recent_mistakes(
        events,
        weak_categories=weak_categories,
        limit=max_recent,
    )

    return {
        "version": BOUNDARY_PACK_VERSION,
        "generated_at": datetime.now().isoformat(),
        "weak_categories": [
            {
                "category": category,
                "reliability": reliability_scores.get(category, 0.5),
            }
            for category in weak_categories
        ],
        "promoted_boundaries": promoted[:max_promoted],
        "recent_mistakes": recent,
        "stats": {
            "feedback_events": len(events),
            "negative_feedback_events": sum(
                1 for event in events if str(event.get("signal") or "") in NEGATIVE_SIGNALS
            ),
            "promoted_boundary_count": len(promoted),
        },
    }


def build_promoted_boundaries(
    events: list[dict[str, Any]],
    *,
    weak_categories: list[str] | set[str] | None = None,
    promotion_threshold: int = PROMOTION_THRESHOLD,
) -> list[dict[str, Any]]:
    """Promote repeated actionable corrections into reusable prevention rules."""
    weak_set = set(weak_categories or [])
    grouped: dict[str, dict[str, Any]] = {}

    for event in events:
        if str(event.get("signal") or "") not in NEGATIVE_SIGNALS:
            continue

        lesson = clean_correction_text(event)
        if not lesson:
            continue

        category = _normalized_category(event)
        timestamp = _coerce_float(event.get("timestamp"))
        intensity = _coerce_int(event.get("intensity"), default=0)
        dedup_key = get_dedup_key(lesson)
        group_key = f"{category}:{dedup_key}"

        current = grouped.setdefault(
            group_key,
            {
                "id": f"boundary-{category}-{dedup_key[:12]}",
                "category": category,
                "lesson": lesson,
                "count": 0,
                "last_seen": timestamp,
                "max_intensity": intensity,
                "weak_category": category in weak_set,
                "signal_counts": {},
                "sample_query": _truncate_text(str(event.get('query') or "")),
                "sample_context": _truncate_text(str(event.get('context') or "")),
            },
        )

        current["count"] += 1
        previous_last_seen = float(current["last_seen"])
        current["last_seen"] = max(previous_last_seen, timestamp)
        current["max_intensity"] = max(current["max_intensity"], intensity)

        signal = str(event.get("signal") or "")
        signal_counts = current["signal_counts"]
        signal_counts[signal] = int(signal_counts.get(signal, 0) or 0) + 1

        if timestamp >= previous_last_seen:
            current["sample_query"] = _truncate_text(str(event.get("query") or ""))
            current["sample_context"] = _truncate_text(str(event.get("context") or ""))

    promoted = []
    for entry in grouped.values():
        if entry["count"] < promotion_threshold:
            continue
        item = dict(entry)
        item["last_seen_iso"] = _format_timestamp(entry["last_seen"])
        promoted.append(item)

    promoted.sort(
        key=lambda item: (
            not bool(item.get("weak_category")),
            -int(item.get("count", 0) or 0),
            -float(item.get("last_seen", 0) or 0),
            -int(item.get("max_intensity", 0) or 0),
            str(item.get("lesson") or ""),
        )
    )
    return promoted


def build_recent_mistakes(
    events: list[dict[str, Any]],
    *,
    weak_categories: list[str] | set[str] | None = None,
    limit: int = MAX_RECENT_MISTAKES,
) -> list[dict[str, Any]]:
    """Return the newest unique actionable mistakes, prioritizing weak areas."""
    weak_set = set(weak_categories or [])
    candidates = []
    for event in events:
        if str(event.get("signal") or "") not in NEGATIVE_SIGNALS:
            continue

        lesson = clean_correction_text(event)
        if not lesson:
            continue

        category = _normalized_category(event)
        candidates.append(
            {
                "category": category,
                "lesson": lesson,
                "signal": str(event.get("signal") or ""),
                "timestamp": _coerce_float(event.get("timestamp")),
                "timestamp_iso": _format_timestamp(_coerce_float(event.get("timestamp"))),
                "intensity": _coerce_int(event.get("intensity"), default=0),
                "weak_category": category in weak_set,
            }
        )

    candidates.sort(key=lambda item: item["timestamp"], reverse=True)

    selected: list[dict[str, Any]] = []
    seen: set[str] = set()
    preferred = [True, False] if weak_set else [False]

    for prefer_weak in preferred:
        for item in candidates:
            if weak_set:
                if bool(item["weak_category"]) is not prefer_weak:
                    continue
            key = f"{item['category']}:{get_dedup_key(item['lesson'])}"
            if key in seen:
                continue
            selected.append(item)
            seen.add(key)
            if len(selected) >= limit:
                return selected

    return selected[:limit]


def format_boundary_pack_for_session(pack: dict[str, Any]) -> str:
    """Render the boundary pack as session-start context."""
    weak_categories = pack.get("weak_categories", [])
    promoted = pack.get("promoted_boundaries", [])
    recent = pack.get("recent_mistakes", [])

    if not weak_categories and not promoted and not recent:
        return ""

    lines = []
    lines.append("\n" + "=" * 60)
    lines.append("SMARTASSIST BOUNDARY PACK")
    lines.append("=" * 60)

    if weak_categories:
        lines.append("\nAreas needing attention (success rate <70%):")
        for item in weak_categories:
            category = str(item.get("category") or "unknown")
            reliability = _coerce_float(item.get("reliability"), default=0.5)
            lines.append(f"  - {category}: {reliability:.1%}")

    if promoted:
        lines.append("\nPromoted prevention rules:\n")
        for index, item in enumerate(promoted, 1):
            badge = " [weak]" if item.get("weak_category") else ""
            count = int(item.get("count", 0) or 0)
            category = str(item.get("category") or "unknown").upper()
            lesson = str(item.get("lesson") or "").strip()
            lines.append(f"[{index}] {category} x{count}{badge}")
            if lesson:
                lines.append(f"    Rule: {lesson}")
            lines.append("")

    if recent:
        lines.append("Recent mistakes to avoid:\n")
        for index, item in enumerate(recent, 1):
            category = str(item.get("category") or "unknown").upper()
            signal = str(item.get("signal") or "feedback")
            lesson = str(item.get("lesson") or "").strip()
            lines.append(f"[{index}] {category} [{signal}]")
            if lesson:
                lines.append(f"    Lesson: {lesson}")
            lines.append("")

    lines.append("=" * 60)
    lines.append("Apply these boundaries before your first action.")
    lines.append("=" * 60 + "\n")
    return "\n".join(lines)


def _update_prevention_rules(storage_path: Path, promoted_boundaries: list[dict[str, Any]]) -> None:
    rules_path = get_prevention_rules_path(storage_path)
    if rules_path is None:
        return

    def _update(current: dict[str, Any] | list[Any] | None) -> dict[str, Any]:
        document: dict[str, Any]
        rules: list[Any]

        if isinstance(current, dict):
            document = dict(current)
            raw_rules = current.get("rules", [])
            rules = raw_rules if isinstance(raw_rules, list) else []
        elif isinstance(current, list):
            document = {}
            rules = current
        else:
            document = {}
            rules = []

        document["version"] = BOUNDARY_PACK_VERSION
        document["updated_at"] = datetime.now().isoformat()
        document["rules"] = rules
        document["promoted_boundaries"] = promoted_boundaries
        return document

    locked_update_json(rules_path, _update, default={"version": BOUNDARY_PACK_VERSION, "rules": []})


def _pack_is_stale(storage_path: Path, pack_path: Path) -> bool:
    try:
        pack_mtime = pack_path.stat().st_mtime
    except OSError:
        return True

    dependencies = [
        storage_path / "feedback_log.jsonl",
        storage_path / "reliability_scores.json",
    ]
    for dependency in dependencies:
        try:
            if dependency.exists() and dependency.stat().st_mtime > pack_mtime:
                return True
        except OSError:
            return True
    return False


def _load_feedback_events(storage_path: Path) -> list[dict[str, Any]]:
    feedback_log = storage_path / "feedback_log.jsonl"
    if not feedback_log.exists():
        return []

    events: list[dict[str, Any]] = []
    try:
        with open(feedback_log, "r", encoding="utf-8") as handle:
            for line in handle:
                line = line.strip()
                if not line:
                    continue
                try:
                    event = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if isinstance(event, dict):
                    events.append(event)
    except OSError:
        return []

    events.sort(key=lambda event: _coerce_float(event.get("timestamp")), reverse=True)
    return events


def _empty_boundary_pack() -> dict[str, Any]:
    return {
        "version": BOUNDARY_PACK_VERSION,
        "generated_at": datetime.now().isoformat(),
        "weak_categories": [],
        "promoted_boundaries": [],
        "recent_mistakes": [],
        "stats": {
            "feedback_events": 0,
            "negative_feedback_events": 0,
            "promoted_boundary_count": 0,
        },
    }


def _resolve_storage_path(storage_path: Path | None) -> Path | None:
    if storage_path is not None:
        return storage_path
    try:
        return get_storage_path()
    except RuntimeError:
        return None


def _normalized_category(event: dict[str, Any]) -> str:
    category = str(event.get("category") or "").strip()
    return category or "unknown"


def _coerce_float(value: Any, *, default: float = 0.0) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _coerce_int(value: Any, *, default: int = 0) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _truncate_text(text: str, limit: int = SAMPLE_TEXT_LIMIT) -> str:
    collapsed = " ".join(text.split())
    if len(collapsed) <= limit:
        return collapsed
    return collapsed[: limit - 3].rstrip() + "..."


def _format_timestamp(timestamp: float) -> str | None:
    if timestamp <= 0:
        return None
    try:
        return datetime.fromtimestamp(timestamp).isoformat()
    except (OSError, OverflowError, ValueError):
        return None
