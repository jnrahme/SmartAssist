"""SQLite-backed canonical store for SmartAssist.

This module owns the runtime source of truth for:
- lessons and lesson scores
- feedback events
- Thompson reliability state
- prompt/session injection state
- lightweight search projections
- feedback metrics

Legacy JSON/JSONL files remain as compatibility exports during the migration.
Readers may also import legacy files on demand when tests or old code paths
seed state by writing those files directly.
"""

from __future__ import annotations

import hashlib
import json
import os
import sqlite3
import tempfile
import time
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path
from typing import Any, Iterable

from smartassist.config import atomic_write_json, get_storage_path

DEFAULT_BOOST = 1.0
DEFAULT_METRICS = {
    "boosts": 0,
    "demotes": 0,
    "creates": 0,
    "merges": 0,
    "positive_signals": 0,
    "negative_signals": 0,
    "last_updated": None,
}

SEARCH_STOP_WORDS = {
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

SEARCH_SYNONYMS = {
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

_SYNC_LESSONS = "lessons"
_SYNC_SCORES = "scores"
_SYNC_EVENTS = "events"
_SYNC_RELIABILITY = "reliability"
_SYNC_INJECTION = "injection"
_SYNC_SESSION = "session"
_SYNC_METRICS = "metrics"


def _resolve_storage_path(storage_path: Path | str | None = None) -> Path:
    if storage_path is None:
        return get_storage_path()
    return Path(storage_path)


def get_store_db_path(storage_path: Path | str | None = None) -> Path:
    """Return the canonical SQLite database path."""
    return _resolve_storage_path(storage_path) / "smartassist.db"


def _connect(storage_path: Path | str | None = None) -> sqlite3.Connection:
    storage = _resolve_storage_path(storage_path)
    storage.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(get_store_db_path(storage)), timeout=5.0)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA synchronous=NORMAL")
    conn.execute("PRAGMA foreign_keys=OFF")
    _create_schema(conn)
    return conn


@contextmanager
def open_store(
    storage_path: Path | str | None = None,
    *,
    sync: Iterable[str] | None = None,
):
    """Open the canonical store and sync requested legacy surfaces first."""
    storage = _resolve_storage_path(storage_path)
    conn = _connect(storage)
    try:
        if sync:
            _sync_legacy_surfaces(conn, storage, set(sync))
        yield conn
    finally:
        conn.close()


def initialize_store(storage_path: Path | str | None = None) -> Path:
    """Create the store and baseline compatibility exports."""
    storage = _resolve_storage_path(storage_path)
    with open_store(storage) as conn:
        _ensure_empty_exports(conn, storage)
    return get_store_db_path(storage)


def _create_schema(conn: sqlite3.Connection) -> None:
    conn.executescript(
        """
        CREATE TABLE IF NOT EXISTS meta (
            key TEXT PRIMARY KEY,
            value TEXT NOT NULL
        );

        CREATE TABLE IF NOT EXISTS lessons (
            lesson_id TEXT PRIMARY KEY,
            text TEXT NOT NULL,
            category_key TEXT NOT NULL,
            state TEXT NOT NULL DEFAULT 'active',
            origin TEXT NOT NULL DEFAULT 'runtime',
            created_at REAL NOT NULL,
            updated_at REAL NOT NULL,
            superseded_by TEXT,
            retired_reason TEXT,
            retired_at TEXT,
            content_hash TEXT NOT NULL
        );

        CREATE TABLE IF NOT EXISTS lesson_scores (
            lesson_id TEXT PRIMARY KEY,
            boost REAL NOT NULL DEFAULT 1.0,
            ups INTEGER NOT NULL DEFAULT 0,
            downs INTEGER NOT NULL DEFAULT 0,
            blocked INTEGER NOT NULL DEFAULT 0,
            retired INTEGER NOT NULL DEFAULT 0,
            retired_reason TEXT NOT NULL DEFAULT '',
            retired_at TEXT
        );

        CREATE TABLE IF NOT EXISTS feedback_events (
            event_id INTEGER PRIMARY KEY AUTOINCREMENT,
            timestamp REAL NOT NULL,
            signal TEXT NOT NULL,
            category_key TEXT NOT NULL,
            intensity INTEGER NOT NULL DEFAULT 3,
            query TEXT NOT NULL DEFAULT '',
            response TEXT NOT NULL DEFAULT '',
            correction TEXT NOT NULL DEFAULT '',
            context TEXT NOT NULL DEFAULT '',
            session_id TEXT NOT NULL DEFAULT 'default'
        );

        CREATE TABLE IF NOT EXISTS category_reliability (
            category_key TEXT PRIMARY KEY,
            alpha REAL NOT NULL,
            beta REAL NOT NULL,
            last_updated REAL NOT NULL,
            total_samples INTEGER NOT NULL DEFAULT 0
        );

        CREATE TABLE IF NOT EXISTS last_injection_slots (
            slot_key TEXT PRIMARY KEY,
            lesson_id TEXT NOT NULL
        );

        CREATE TABLE IF NOT EXISTS session_state (
            session_id TEXT PRIMARY KEY,
            injected_ids_json TEXT NOT NULL
        );

        CREATE TABLE IF NOT EXISTS feedback_metrics (
            metric_key TEXT PRIMARY KEY,
            metric_value INTEGER,
            metric_text TEXT,
            updated_at TEXT
        );

        CREATE TABLE IF NOT EXISTS search_documents (
            doc_id TEXT PRIMARY KEY,
            source_type TEXT NOT NULL,
            source_id TEXT NOT NULL,
            category_key TEXT NOT NULL,
            text TEXT NOT NULL,
            search_text TEXT NOT NULL,
            active INTEGER NOT NULL DEFAULT 1,
            version INTEGER NOT NULL DEFAULT 1,
            content_hash TEXT NOT NULL,
            updated_at REAL NOT NULL
        );

        CREATE VIRTUAL TABLE IF NOT EXISTS search_documents_fts
        USING fts5(
            doc_id UNINDEXED,
            text,
            search_text,
            category_key,
            tokenize='porter unicode61'
        );

        CREATE INDEX IF NOT EXISTS idx_lessons_state ON lessons(state);
        CREATE INDEX IF NOT EXISTS idx_search_documents_active ON search_documents(active, source_type);
        CREATE INDEX IF NOT EXISTS idx_feedback_events_timestamp ON feedback_events(timestamp);
        CREATE INDEX IF NOT EXISTS idx_feedback_events_category ON feedback_events(category_key);
        """
    )
    conn.commit()


def _get_meta(conn: sqlite3.Connection, key: str, default: str = "") -> str:
    row = conn.execute(
        "SELECT value FROM meta WHERE key = ?",
        (key,),
    ).fetchone()
    if row is None:
        return default
    return str(row["value"])


def _set_meta(conn: sqlite3.Connection, key: str, value: str) -> None:
    conn.execute(
        """
        INSERT INTO meta(key, value) VALUES(?, ?)
        ON CONFLICT(key) DO UPDATE SET value = excluded.value
        """,
        (key, value),
    )


def _file_signature(path: Path) -> str:
    if not path.exists():
        return "missing"
    try:
        data = path.read_bytes()
    except OSError:
        return "unreadable"
    digest = hashlib.sha1(data).hexdigest()
    return f"{len(data)}:{digest}"


def _atomic_write_text(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_path = tempfile.mkstemp(
        dir=path.parent,
        suffix=".tmp",
        prefix=path.stem + "_",
        text=True,
    )
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write(text)
        os.replace(tmp_path, path)
    except BaseException:
        try:
            os.unlink(tmp_path)
        except OSError:
            pass
        raise


def _canonical_hash(text: str, category: str = "") -> str:
    payload = f"{category.strip().lower()}::{text.strip()}"
    return hashlib.sha1(payload.encode("utf-8")).hexdigest()


def _lesson_sort_key(lesson_id: str) -> tuple[int, str]:
    if lesson_id.startswith("L") and lesson_id[1:].isdigit():
        return int(lesson_id[1:]), lesson_id
    return (10**9), lesson_id


def _tokenize_search(text: str) -> list[str]:
    import re

    words = re.findall(r"[a-z][a-z0-9_.]+", text.lower())
    return [word for word in words if word not in SEARCH_STOP_WORDS and len(word) > 2]


def _expand_search_terms(tokens: Iterable[str]) -> set[str]:
    expanded = set(tokens)
    for token in tokens:
        expanded.update(SEARCH_SYNONYMS.get(token, set()))
    return expanded


def _build_search_idf(rows: Iterable[sqlite3.Row]) -> dict[str, float]:
    from math import log

    doc_freq: dict[str, int] = {}
    row_list = list(rows)
    n = len(row_list)
    for row in row_list:
        terms = set(_tokenize_search(str(row["search_text"])))
        for term in terms:
            doc_freq[term] = doc_freq.get(term, 0) + 1
    return {term: 1.0 + log((n + 1) / (df + 1)) for term, df in doc_freq.items()}


def _project_event_lesson(event: dict[str, Any]) -> str | None:
    from smartassist.tools.cleanup_and_vectorize import (
        is_skip_pattern,
        sanitize_to_lesson,
        clean_correction_text,
    )

    correction = str(event.get("correction") or "").strip()
    if not correction or len(correction) < 30:
        return None
    if is_skip_pattern(correction):
        return None

    cleaned = clean_correction_text(event)
    if cleaned:
        return cleaned

    cleaned = sanitize_to_lesson(correction).strip()
    if len(cleaned) < 30 or is_skip_pattern(cleaned):
        return None
    return cleaned


def _rebuild_search_projection(conn: sqlite3.Connection) -> None:
    from smartassist.tools.cleanup_and_vectorize import (
        format_text_for_vector,
        get_dedup_key,
    )

    timestamp = time.time()
    version = int(_get_meta(conn, "projection_version", "0") or "0") + 1
    conn.execute("DELETE FROM search_documents")
    conn.execute("DELETE FROM search_documents_fts")

    rows = conn.execute(
        """
        SELECT l.lesson_id, l.text, l.category_key, l.content_hash,
               COALESCE(s.blocked, 0) AS blocked,
               COALESCE(s.retired, 0) AS retired
          FROM lessons l
          LEFT JOIN lesson_scores s ON s.lesson_id = l.lesson_id
         WHERE l.state = 'active'
         ORDER BY l.lesson_id
        """
    ).fetchall()

    docs: list[tuple[str, str, str, str, str, str, int, int, str, float]] = []
    fts_docs: list[tuple[str, str, str, str]] = []
    seen_keys: set[str] = set()
    for row in rows:
        if int(row["blocked"] or 0) or int(row["retired"] or 0):
            continue
        doc_id = f"lesson:{row['lesson_id']}"
        lesson_text = str(row["text"])
        category = str(row["category_key"])
        dedup_key = f"lesson:{category}:{get_dedup_key(lesson_text)}"
        if dedup_key in seen_keys:
            continue
        seen_keys.add(dedup_key)

        text = format_text_for_vector(category, lesson_text)
        search_text = f"{category} {lesson_text}"
        docs.append(
            (
                doc_id,
                "lesson",
                str(row["lesson_id"]),
                category,
                text,
                search_text,
                1,
                version,
                str(row["content_hash"]),
                timestamp,
            )
        )
        fts_docs.append((doc_id, text, search_text, category))

    event_rows = conn.execute(
        """
        SELECT event_id, timestamp, signal, category_key, intensity, query, response,
               correction, context, session_id
          FROM feedback_events
         ORDER BY event_id
        """
    ).fetchall()
    for row in event_rows:
        event = {
            "timestamp": float(row["timestamp"]),
            "signal": str(row["signal"]),
            "category": str(row["category_key"]),
            "intensity": int(row["intensity"]),
            "query": str(row["query"] or ""),
            "response": str(row["response"] or ""),
            "correction": str(row["correction"] or ""),
            "context": str(row["context"] or ""),
            "session_id": str(row["session_id"] or "default"),
        }
        lesson_text = _project_event_lesson(event)
        if not lesson_text:
            continue

        category = str(row["category_key"])
        dedup_key = f"event:{category}:{get_dedup_key(lesson_text)}"
        if dedup_key in seen_keys:
            continue
        seen_keys.add(dedup_key)

        doc_id = f"event:{row['event_id']}"
        context = str(row["context"] or "").strip()
        text = format_text_for_vector(category, lesson_text)
        if context:
            text += f" Context: {context}"
        search_text = " ".join(
            part for part in (category, lesson_text, context) if part
        )
        docs.append(
            (
                doc_id,
                "event",
                str(row["event_id"]),
                category,
                text,
                search_text,
                1,
                version,
                _canonical_hash(lesson_text, category),
                float(row["timestamp"]),
            )
        )
        fts_docs.append((doc_id, text, search_text, category))

    if docs:
        conn.executemany(
            """
            INSERT INTO search_documents(
                doc_id, source_type, source_id, category_key, text, search_text,
                active, version, content_hash, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            docs,
        )
        conn.executemany(
            """
            INSERT INTO search_documents_fts(doc_id, text, search_text, category_key)
            VALUES (?, ?, ?, ?)
            """,
            fts_docs,
        )

    _set_meta(conn, "projection_version", str(version))


def _sync_legacy_surfaces(
    conn: sqlite3.Connection,
    storage: Path,
    sync: set[str],
) -> None:
    if _SYNC_LESSONS in sync:
        _sync_lessons_from_legacy(conn, storage)
    if _SYNC_SCORES in sync:
        _sync_scores_from_legacy(conn, storage)
    if _SYNC_EVENTS in sync:
        _sync_events_from_legacy(conn, storage)
    if _SYNC_RELIABILITY in sync:
        _sync_reliability_from_legacy(conn, storage)
    if _SYNC_INJECTION in sync:
        _sync_last_injection_from_legacy(conn, storage)
    if _SYNC_SESSION in sync:
        _sync_session_state_from_legacy(conn, storage)
    if _SYNC_METRICS in sync:
        _sync_feedback_metrics_from_legacy(conn, storage)
    conn.commit()


def _sync_lessons_from_legacy(conn: sqlite3.Connection, storage: Path) -> None:
    path = storage / "curated_lessons.json"
    signature = _file_signature(path)
    meta_key = "legacy_sig:curated_lessons"
    if signature == _get_meta(conn, meta_key, ""):
        return

    lessons: list[dict[str, Any]] = []
    if path.exists():
        try:
            raw = json.loads(path.read_text())
            if isinstance(raw, list):
                lessons = [item for item in raw if isinstance(item, dict)]
        except (json.JSONDecodeError, OSError):
            lessons = []

    now = time.time()
    conn.execute("DELETE FROM lessons")
    for lesson in lessons:
        lesson_id = str(lesson.get("id") or "").strip()
        text = str(lesson.get("lesson") or "").strip()
        category = str(lesson.get("category") or "unknown").strip() or "unknown"
        if not lesson_id or not text:
            continue
        conn.execute(
            """
            INSERT INTO lessons(
                lesson_id, text, category_key, state, origin,
                created_at, updated_at, content_hash
            ) VALUES (?, ?, ?, 'active', 'legacy', ?, ?, ?)
            """,
            (
                lesson_id,
                text,
                category,
                now,
                now,
                _canonical_hash(text, category),
            ),
        )
    _rebuild_search_projection(conn)
    _set_meta(conn, meta_key, signature)


def _sync_scores_from_legacy(conn: sqlite3.Connection, storage: Path) -> None:
    path = storage / "lesson_scores.json"
    signature = _file_signature(path)
    meta_key = "legacy_sig:lesson_scores"
    if signature == _get_meta(conn, meta_key, ""):
        return

    data: dict[str, Any] = {}
    if path.exists():
        try:
            raw = json.loads(path.read_text())
            if isinstance(raw, dict):
                data = raw
        except (json.JSONDecodeError, OSError):
            data = {}

    conn.execute("DELETE FROM lesson_scores")
    for lesson_id, payload in data.items():
        if not isinstance(payload, dict):
            continue
        conn.execute(
            """
            INSERT INTO lesson_scores(
                lesson_id, boost, ups, downs, blocked, retired, retired_reason, retired_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                str(lesson_id),
                float(payload.get("boost", DEFAULT_BOOST) or DEFAULT_BOOST),
                int(payload.get("ups", 0) or 0),
                int(payload.get("downs", 0) or 0),
                1 if payload.get("blocked", False) else 0,
                1 if payload.get("retired", False) else 0,
                str(payload.get("retired_reason", "") or ""),
                payload.get("retired_at"),
            ),
        )
    _rebuild_search_projection(conn)
    _set_meta(conn, meta_key, signature)


def _sync_events_from_legacy(conn: sqlite3.Connection, storage: Path) -> None:
    path = storage / "feedback_log.jsonl"
    signature = _file_signature(path)
    meta_key = "legacy_sig:feedback_log"
    if signature == _get_meta(conn, meta_key, ""):
        return

    events: list[dict[str, Any]] = []
    if path.exists():
        try:
            with open(path, "r", encoding="utf-8") as handle:
                for line in handle:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        payload = json.loads(line)
                    except json.JSONDecodeError:
                        continue
                    if isinstance(payload, dict):
                        events.append(payload)
        except OSError:
            events = []

    conn.execute("DELETE FROM feedback_events")
    for event in events:
        conn.execute(
            """
            INSERT INTO feedback_events(
                timestamp, signal, category_key, intensity, query, response,
                correction, context, session_id
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                float(event.get("timestamp", time.time()) or time.time()),
                str(event.get("signal") or "unknown"),
                str(event.get("category") or "unknown"),
                int(event.get("intensity", 0) or 0),
                str(event.get("query") or ""),
                str(event.get("response") or ""),
                str(event.get("correction") or ""),
                str(event.get("context") or ""),
                str(event.get("session_id") or "default"),
            ),
        )
    _rebuild_search_projection(conn)
    _set_meta(conn, meta_key, signature)


def _sync_reliability_from_legacy(conn: sqlite3.Connection, storage: Path) -> None:
    path = storage / "reliability_scores.json"
    signature = _file_signature(path)
    meta_key = "legacy_sig:reliability_scores"
    if signature == _get_meta(conn, meta_key, ""):
        return

    data: dict[str, Any] = {}
    if path.exists():
        try:
            raw = json.loads(path.read_text())
            if isinstance(raw, dict):
                data = raw
        except (json.JSONDecodeError, OSError):
            data = {}

    conn.execute("DELETE FROM category_reliability")
    for category, payload in data.items():
        if not isinstance(payload, dict):
            continue
        conn.execute(
            """
            INSERT INTO category_reliability(
                category_key, alpha, beta, last_updated, total_samples
            ) VALUES (?, ?, ?, ?, ?)
            """,
            (
                str(category),
                float(payload.get("alpha", 1.0) or 1.0),
                float(payload.get("beta", 1.0) or 1.0),
                float(payload.get("last_updated", time.time()) or time.time()),
                int(payload.get("total_samples", 0) or 0),
            ),
        )
    _set_meta(conn, meta_key, signature)


def _sync_last_injection_from_legacy(conn: sqlite3.Connection, storage: Path) -> None:
    path = storage / "last_injection.json"
    signature = _file_signature(path)
    meta_key = "legacy_sig:last_injection"
    if signature == _get_meta(conn, meta_key, ""):
        return

    payload: dict[str, Any] = {}
    if path.exists():
        try:
            raw = json.loads(path.read_text())
            if isinstance(raw, dict):
                payload = raw
        except (json.JSONDecodeError, OSError):
            payload = {}

    conn.execute("DELETE FROM last_injection_slots")
    for key, lesson_id in payload.items():
        if str(key).startswith("_"):
            continue
        conn.execute(
            "INSERT INTO last_injection_slots(slot_key, lesson_id) VALUES(?, ?)",
            (str(key), str(lesson_id)),
        )
    _set_meta(
        conn, "last_injection_timestamp", str(float(payload.get("_timestamp", 0) or 0))
    )
    _set_meta(conn, meta_key, signature)


def _sync_session_state_from_legacy(conn: sqlite3.Connection, storage: Path) -> None:
    path = storage / "rag_session_state.json"
    signature = _file_signature(path)
    meta_key = "legacy_sig:session_state"
    if signature == _get_meta(conn, meta_key, ""):
        return

    payload: dict[str, Any] = {}
    if path.exists():
        try:
            raw = json.loads(path.read_text())
            if isinstance(raw, dict):
                payload = raw
        except (json.JSONDecodeError, OSError):
            payload = {}

    conn.execute("DELETE FROM session_state")
    session_id = str(payload.get("session_id") or "")
    injected_ids = payload.get("injected_ids", [])
    if session_id:
        conn.execute(
            "INSERT INTO session_state(session_id, injected_ids_json) VALUES(?, ?)",
            (session_id, json.dumps(sorted(injected_ids))),
        )
    _set_meta(conn, meta_key, signature)


def _sync_feedback_metrics_from_legacy(conn: sqlite3.Connection, storage: Path) -> None:
    path = storage / "feedback_metrics.json"
    signature = _file_signature(path)
    meta_key = "legacy_sig:feedback_metrics"
    if signature == _get_meta(conn, meta_key, ""):
        return

    payload = dict(DEFAULT_METRICS)
    if path.exists():
        try:
            raw = json.loads(path.read_text())
            if isinstance(raw, dict):
                payload.update(raw)
        except (json.JSONDecodeError, OSError):
            payload = dict(DEFAULT_METRICS)

    conn.execute("DELETE FROM feedback_metrics")
    updated_at = str(payload.get("last_updated") or "")
    for key, value in payload.items():
        if key == "last_updated":
            continue
        conn.execute(
            """
            INSERT INTO feedback_metrics(metric_key, metric_value, metric_text, updated_at)
            VALUES (?, ?, ?, ?)
            """,
            (
                key,
                int(value or 0),
                None,
                updated_at,
            ),
        )
    if updated_at:
        conn.execute(
            """
            INSERT INTO feedback_metrics(metric_key, metric_value, metric_text, updated_at)
            VALUES ('last_updated', NULL, ?, ?)
            """,
            (updated_at, updated_at),
        )
    _set_meta(conn, meta_key, signature)


def _update_signature_meta(conn: sqlite3.Connection, meta_key: str, path: Path) -> None:
    _set_meta(conn, meta_key, _file_signature(path))


def _ensure_empty_exports(conn: sqlite3.Connection, storage: Path) -> None:
    export_lessons(conn, storage)
    export_scores(conn, storage)
    export_feedback_events(conn, storage)
    export_reliability(conn, storage)
    export_last_injection(conn, storage)
    export_session_state(conn, storage)
    export_feedback_metrics(conn, storage)
    export_vectorization_log(storage)
    conn.commit()


def export_lessons(conn: sqlite3.Connection, storage: Path | None = None) -> None:
    storage = _resolve_storage_path(storage)
    path = storage / "curated_lessons.json"
    rows = conn.execute(
        """
        SELECT lesson_id, text, category_key
          FROM lessons
         WHERE state = 'active'
         ORDER BY lesson_id
        """
    ).fetchall()
    data = [
        {
            "id": str(row["lesson_id"]),
            "lesson": str(row["text"]),
            "category": str(row["category_key"]),
        }
        for row in rows
    ]
    atomic_write_json(path, data)
    _update_signature_meta(conn, "legacy_sig:curated_lessons", path)


def export_scores(conn: sqlite3.Connection, storage: Path | None = None) -> None:
    storage = _resolve_storage_path(storage)
    path = storage / "lesson_scores.json"
    rows = conn.execute(
        """
        SELECT lesson_id, boost, ups, downs, blocked, retired, retired_reason, retired_at
          FROM lesson_scores
         ORDER BY lesson_id
        """
    ).fetchall()
    payload = {
        str(row["lesson_id"]): {
            "boost": float(row["boost"]),
            "ups": int(row["ups"]),
            "downs": int(row["downs"]),
            "blocked": bool(row["blocked"]),
            "retired": bool(row["retired"]),
            "retired_reason": str(row["retired_reason"] or ""),
            "retired_at": row["retired_at"],
        }
        for row in rows
    }
    atomic_write_json(path, payload)
    _update_signature_meta(conn, "legacy_sig:lesson_scores", path)


def export_feedback_events(
    conn: sqlite3.Connection, storage: Path | None = None
) -> None:
    storage = _resolve_storage_path(storage)
    path = storage / "feedback_log.jsonl"
    rows = conn.execute(
        """
        SELECT timestamp, signal, category_key, intensity, query, response,
               correction, context, session_id
          FROM feedback_events
         ORDER BY event_id
        """
    ).fetchall()
    lines = []
    for row in rows:
        payload = {
            "timestamp": float(row["timestamp"]),
            "signal": str(row["signal"]),
            "category": str(row["category_key"]),
            "intensity": int(row["intensity"]),
            "query": str(row["query"] or ""),
            "response": str(row["response"] or ""),
            "correction": str(row["correction"] or ""),
            "context": str(row["context"] or ""),
            "session_id": str(row["session_id"] or "default"),
        }
        lines.append(json.dumps(payload))
    _atomic_write_text(path, ("\n".join(lines) + ("\n" if lines else "")))
    _update_signature_meta(conn, "legacy_sig:feedback_log", path)


def export_reliability(conn: sqlite3.Connection, storage: Path | None = None) -> None:
    storage = _resolve_storage_path(storage)
    path = storage / "reliability_scores.json"
    rows = conn.execute(
        """
        SELECT category_key, alpha, beta, last_updated, total_samples
          FROM category_reliability
         ORDER BY category_key
        """
    ).fetchall()
    payload = {
        str(row["category_key"]): {
            "category": str(row["category_key"]),
            "alpha": float(row["alpha"]),
            "beta": float(row["beta"]),
            "last_updated": float(row["last_updated"]),
            "total_samples": int(row["total_samples"]),
        }
        for row in rows
    }
    atomic_write_json(path, payload)
    _update_signature_meta(conn, "legacy_sig:reliability_scores", path)


def export_last_injection(
    conn: sqlite3.Connection, storage: Path | None = None
) -> None:
    storage = _resolve_storage_path(storage)
    path = storage / "last_injection.json"
    rows = conn.execute(
        """
        SELECT slot_key, lesson_id
          FROM last_injection_slots
         ORDER BY CAST(slot_key AS INTEGER)
        """
    ).fetchall()
    payload: dict[str, Any] = {
        str(row["slot_key"]): str(row["lesson_id"]) for row in rows
    }
    payload["_timestamp"] = float(
        _get_meta(conn, "last_injection_timestamp", "0") or "0"
    )
    atomic_write_json(path, payload)
    _update_signature_meta(conn, "legacy_sig:last_injection", path)


def export_session_state(conn: sqlite3.Connection, storage: Path | None = None) -> None:
    storage = _resolve_storage_path(storage)
    path = storage / "rag_session_state.json"
    row = conn.execute(
        """
        SELECT session_id, injected_ids_json
          FROM session_state
         ORDER BY ROWID DESC
         LIMIT 1
        """
    ).fetchone()
    if row is None:
        atomic_write_json(path, {})
    else:
        atomic_write_json(
            path,
            {
                "session_id": str(row["session_id"]),
                "injected_ids": json.loads(str(row["injected_ids_json"])),
            },
        )
    _update_signature_meta(conn, "legacy_sig:session_state", path)


def load_last_rag_results(
    storage_path: Path | str | None = None,
    *,
    max_age: float | None = None,
) -> list[dict[str, Any]]:
    with open_store(storage_path, sync=(_SYNC_LESSONS,)) as conn:
        raw = _get_meta(conn, "last_rag_results_json", "")
        if not raw:
            return []
        try:
            payload = json.loads(raw)
        except json.JSONDecodeError:
            return []

    if not isinstance(payload, dict):
        return []

    timestamp = float(payload.get("timestamp", 0) or 0)
    if max_age is not None and timestamp and (time.time() - timestamp) > max_age:
        return []

    results = payload.get("results", [])
    if not isinstance(results, list):
        return []

    normalized: list[dict[str, Any]] = []
    with open_store(storage_path, sync=(_SYNC_LESSONS,)) as conn:
        for item in results:
            if not isinstance(item, dict):
                continue
            lesson_id = _resolve_active_lesson_id(
                conn, str(item.get("id") or "").strip()
            )
            if not lesson_id:
                continue
            normalized.append(
                {
                    "id": lesson_id,
                    "score": float(item.get("score", 0.5) or 0.5),
                    "injection_timestamp": timestamp,
                }
            )
    return normalized


def save_last_rag_results(
    storage_path: Path | str | None,
    results: Iterable[dict[str, Any]],
) -> None:
    storage = _resolve_storage_path(storage_path)
    payload = {
        "timestamp": time.time(),
        "results": [
            {
                "id": str(item.get("id") or "").strip(),
                "score": float(item.get("score", 0.5) or 0.5),
            }
            for item in results
            if str(item.get("id") or "").strip()
        ],
    }
    with open_store(storage) as conn:
        _set_meta(conn, "last_rag_results_json", json.dumps(payload))
        conn.commit()


def export_feedback_metrics(
    conn: sqlite3.Connection, storage: Path | None = None
) -> None:
    storage = _resolve_storage_path(storage)
    path = storage / "feedback_metrics.json"
    rows = conn.execute(
        "SELECT metric_key, metric_value, metric_text FROM feedback_metrics"
    ).fetchall()
    payload = dict(DEFAULT_METRICS)
    for row in rows:
        key = str(row["metric_key"])
        if key == "last_updated":
            payload[key] = row["metric_text"]
        else:
            payload[key] = int(row["metric_value"] or 0)
    atomic_write_json(path, payload)
    _update_signature_meta(conn, "legacy_sig:feedback_metrics", path)


def export_vectorization_log(
    storage_path: Path | str | None = None,
    *,
    total_vectorized: int | None = None,
    total_in_db: int | None = None,
) -> None:
    storage = _resolve_storage_path(storage_path)
    path = storage / "vectorization_log.json"
    payload = {
        "total_vectorized": int(total_vectorized or 0),
        "last_processed_line": int(total_vectorized or 0),
        "last_vectorization": datetime.now().isoformat(),
    }
    if total_in_db is not None:
        payload["total_documents_in_rag"] = int(total_in_db)
    atomic_write_json(path, payload)


def list_lessons(
    storage_path: Path | str | None = None,
    *,
    include_inactive: bool = False,
) -> list[dict[str, Any]]:
    with open_store(storage_path, sync=(_SYNC_LESSONS, _SYNC_SCORES)) as conn:
        query = """
            SELECT lesson_id, text, category_key, state, origin, created_at,
                   updated_at, superseded_by, retired_reason, retired_at, content_hash
              FROM lessons
        """
        params: tuple[Any, ...] = ()
        if not include_inactive:
            query += " WHERE state = 'active'"
        query += " ORDER BY lesson_id"
        rows = conn.execute(query, params).fetchall()
        return [
            {
                "id": str(row["lesson_id"]),
                "lesson": str(row["text"]),
                "category": str(row["category_key"]),
                "state": str(row["state"]),
                "origin": str(row["origin"]),
                "created_at": float(row["created_at"]),
                "updated_at": float(row["updated_at"]),
                "superseded_by": row["superseded_by"],
                "retired_reason": row["retired_reason"],
                "retired_at": row["retired_at"],
                "content_hash": str(row["content_hash"]),
            }
            for row in rows
        ]


def get_lesson_ids(storage_path: Path | str | None = None) -> set[str]:
    lessons = list_lessons(storage_path)
    return {lesson["id"] for lesson in lessons}


def _resolve_active_lesson_id(conn: sqlite3.Connection, lesson_id: str) -> str:
    current = str(lesson_id or "").strip()
    seen: set[str] = set()
    while current and current not in seen:
        seen.add(current)
        row = conn.execute(
            "SELECT state, superseded_by FROM lessons WHERE lesson_id = ?",
            (current,),
        ).fetchone()
        if row is None:
            return current
        superseded_by = str(row["superseded_by"] or "").strip()
        if str(row["state"] or "") != "superseded" or not superseded_by:
            return current
        current = superseded_by
    return str(lesson_id or "").strip()


def add_lesson(
    storage_path: Path | str | None,
    lesson_text: str,
    category: str,
    *,
    origin: str = "runtime",
    state: str = "active",
) -> tuple[str | None, str | None]:
    storage = _resolve_storage_path(storage_path)
    with open_store(storage, sync=(_SYNC_LESSONS, _SYNC_SCORES)) as conn:
        lesson_count = conn.execute(
            "SELECT COUNT(*) FROM lessons WHERE state = 'active'"
        ).fetchone()[0]
        if lesson_count >= 300:
            return None, "Corpus at capacity (300). Merge or demote lessons first."

        ids = [
            str(row["lesson_id"])
            for row in conn.execute("SELECT lesson_id FROM lessons").fetchall()
        ]
        max_num = 0
        for lesson_id in ids:
            if lesson_id.startswith("L") and lesson_id[1:].isdigit():
                max_num = max(max_num, int(lesson_id[1:]))
        new_id = f"L{max_num + 1:03d}"
        now = time.time()
        conn.execute(
            """
            INSERT INTO lessons(
                lesson_id, text, category_key, state, origin,
                created_at, updated_at, content_hash
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                new_id,
                lesson_text,
                category,
                state,
                origin,
                now,
                now,
                _canonical_hash(lesson_text, category),
            ),
        )
        conn.execute(
            """
            INSERT OR IGNORE INTO lesson_scores(lesson_id, boost, ups, downs, blocked, retired)
            VALUES (?, ?, 0, 0, 0, 0)
            """,
            (new_id, DEFAULT_BOOST),
        )
        _rebuild_search_projection(conn)
        export_lessons(conn, storage)
        conn.commit()
        return new_id, None


def ensure_lesson(
    storage_path: Path | str | None,
    lesson_text: str,
    category: str,
    *,
    origin: str = "runtime",
    state: str = "active",
) -> tuple[str | None, str | None, bool]:
    storage = _resolve_storage_path(storage_path)
    with open_store(storage, sync=(_SYNC_LESSONS, _SYNC_SCORES)) as conn:
        existing = conn.execute(
            """
            SELECT lesson_id
              FROM lessons
             WHERE state = 'active'
               AND text = ?
               AND category_key = ?
             LIMIT 1
            """,
            (lesson_text, category),
        ).fetchone()
        if existing is not None:
            return str(existing["lesson_id"]), None, False

    lesson_id, error = add_lesson(
        storage, lesson_text, category, origin=origin, state=state
    )
    return lesson_id, error, error is None


def retire_lesson(
    storage_path: Path | str | None,
    lesson_id: str,
    *,
    retired_reason: str = "",
) -> None:
    storage = _resolve_storage_path(storage_path)
    with open_store(storage, sync=(_SYNC_LESSONS, _SYNC_SCORES)) as conn:
        conn.execute(
            """
            UPDATE lessons
               SET state = 'retired',
                   retired_reason = ?,
                   retired_at = ?,
                   updated_at = ?
             WHERE lesson_id = ?
            """,
            (
                retired_reason,
                datetime.now().isoformat(),
                time.time(),
                lesson_id,
            ),
        )
        _rebuild_search_projection(conn)
        export_lessons(conn, storage)
        conn.commit()


def merge_lessons_in_store(
    storage_path: Path | str | None,
    lesson_ids: list[str],
    new_lesson: str,
    category: str,
) -> tuple[str | None, str | None]:
    storage = _resolve_storage_path(storage_path)
    with open_store(storage, sync=(_SYNC_LESSONS, _SYNC_SCORES)) as conn:
        existing_ids = {
            str(row["lesson_id"])
            for row in conn.execute(
                "SELECT lesson_id FROM lessons WHERE state = 'active'"
            ).fetchall()
        }
        missing = [
            lesson_id for lesson_id in lesson_ids if lesson_id not in existing_ids
        ]
        if missing:
            return (
                None,
                f"Cannot merge: lesson(s) {', '.join(missing)} not found in curated lessons.",
            )

        ids = [
            str(row["lesson_id"])
            for row in conn.execute("SELECT lesson_id FROM lessons").fetchall()
        ]
        max_num = 0
        for lesson_id in ids:
            if lesson_id.startswith("L") and lesson_id[1:].isdigit():
                max_num = max(max_num, int(lesson_id[1:]))
        new_id = f"L{max_num + 1:03d}"
        now = time.time()
        conn.execute(
            """
            INSERT INTO lessons(
                lesson_id, text, category_key, state, origin,
                created_at, updated_at, content_hash
            ) VALUES (?, ?, ?, 'active', 'merge', ?, ?, ?)
            """,
            (
                new_id,
                new_lesson,
                category,
                now,
                now,
                _canonical_hash(new_lesson, category),
            ),
        )
        conn.execute(
            """
            INSERT OR IGNORE INTO lesson_scores(lesson_id, boost, ups, downs, blocked, retired)
            VALUES (?, ?, 0, 0, 0, 0)
            """,
            (new_id, DEFAULT_BOOST),
        )
        for lesson_id in lesson_ids:
            conn.execute(
                """
                UPDATE lessons
                   SET state = 'superseded',
                       superseded_by = ?,
                       updated_at = ?
                 WHERE lesson_id = ?
                """,
                (new_id, now, lesson_id),
            )
        _merge_superseded_thompson_state(conn, lesson_ids, new_id, now)
        _rebuild_search_projection(conn)
        export_lessons(conn, storage)
        conn.commit()
        return new_id, None


def auto_merge_duplicate_lessons_in_store(
    storage_path: Path | str | None,
    survivor_id: str,
    duplicate_ids: list[str],
) -> tuple[list[str] | None, str | None]:
    storage = _resolve_storage_path(storage_path)
    survivor_id = str(survivor_id or "").strip().upper()
    merge_ids = [
        str(lesson_id or "").strip().upper()
        for lesson_id in duplicate_ids
        if str(lesson_id or "").strip()
        and str(lesson_id or "").strip().upper() != survivor_id
    ]
    if not survivor_id:
        return None, "Cannot auto-merge: survivor lesson ID is required."
    if not merge_ids:
        return [], None

    placeholders = ",".join("?" for _ in merge_ids)
    with open_store(storage, sync=(_SYNC_LESSONS, _SYNC_SCORES)) as conn:
        lesson_rows = conn.execute(
            f"""
            SELECT lesson_id, category_key, state
              FROM lessons
             WHERE lesson_id = ?
                OR lesson_id IN ({placeholders})
            """,
            (survivor_id, *merge_ids),
        ).fetchall()
        row_map = {str(row["lesson_id"]): row for row in lesson_rows}

        if survivor_id not in row_map:
            return None, f"Cannot auto-merge: survivor lesson {survivor_id} not found."
        if str(row_map[survivor_id]["state"] or "") != "active":
            return (
                None,
                f"Cannot auto-merge: survivor lesson {survivor_id} is not active.",
            )

        missing = [lesson_id for lesson_id in merge_ids if lesson_id not in row_map]
        if missing:
            return None, f"Cannot auto-merge: lesson(s) {', '.join(missing)} not found."

        inactive = [
            lesson_id
            for lesson_id in merge_ids
            if str(row_map[lesson_id]["state"] or "") != "active"
        ]
        if inactive:
            return None, (
                f"Cannot auto-merge: lesson(s) {', '.join(inactive)} are not active."
            )

        survivor_category = str(row_map[survivor_id]["category_key"] or "")
        mismatched = [
            lesson_id
            for lesson_id in merge_ids
            if str(row_map[lesson_id]["category_key"] or "") != survivor_category
        ]
        if mismatched:
            return None, (
                "Cannot auto-merge across categories: " + ", ".join(mismatched)
            )

        score_rows = conn.execute(
            f"""
            SELECT lesson_id, boost, ups, downs
              FROM lesson_scores
             WHERE lesson_id = ?
                OR lesson_id IN ({placeholders})
            """,
            (survivor_id, *merge_ids),
        ).fetchall()
        score_map = {
            str(row["lesson_id"]): {
                "boost": float(row["boost"] or DEFAULT_BOOST),
                "ups": int(row["ups"] or 0),
                "downs": int(row["downs"] or 0),
            }
            for row in score_rows
        }

        survivor_score = score_map.get(
            survivor_id,
            {"boost": DEFAULT_BOOST, "ups": 0, "downs": 0},
        )
        combined_ups = survivor_score["ups"]
        combined_downs = survivor_score["downs"]
        max_boost = survivor_score["boost"]
        for lesson_id in merge_ids:
            source_score = score_map.get(
                lesson_id,
                {"boost": DEFAULT_BOOST, "ups": 0, "downs": 0},
            )
            combined_ups += source_score["ups"]
            combined_downs += source_score["downs"]
            max_boost = max(max_boost, source_score["boost"])

        now = time.time()
        retired_at = datetime.now().isoformat()

        conn.execute(
            """
            INSERT OR IGNORE INTO lesson_scores(lesson_id, boost, ups, downs, blocked, retired)
            VALUES (?, ?, 0, 0, 0, 0)
            """,
            (survivor_id, DEFAULT_BOOST),
        )
        conn.execute(
            """
            UPDATE lesson_scores
               SET boost = ?,
                   ups = ?,
                   downs = ?,
                   blocked = 0,
                   retired = 0,
                   retired_reason = '',
                   retired_at = NULL
             WHERE lesson_id = ?
            """,
            (max_boost, combined_ups, combined_downs, survivor_id),
        )
        conn.execute(
            "UPDATE lessons SET updated_at = ? WHERE lesson_id = ?",
            (now, survivor_id),
        )

        for lesson_id in merge_ids:
            conn.execute(
                """
                INSERT OR IGNORE INTO lesson_scores(lesson_id, boost, ups, downs, blocked, retired)
                VALUES (?, ?, 0, 0, 0, 0)
                """,
                (lesson_id, DEFAULT_BOOST),
            )
            conn.execute(
                """
                UPDATE lesson_scores
                   SET blocked = 1,
                       retired = 1,
                       retired_reason = ?,
                       retired_at = ?
                 WHERE lesson_id = ?
                """,
                (f"superseded_by={survivor_id}", retired_at, lesson_id),
            )
            conn.execute(
                """
                UPDATE lessons
                   SET state = 'superseded',
                       superseded_by = ?,
                       updated_at = ?
                 WHERE lesson_id = ?
                """,
                (survivor_id, now, lesson_id),
            )

        _merge_superseded_thompson_state(conn, merge_ids, survivor_id, now)
        _rebuild_search_projection(conn)
        export_lessons(conn, storage)
        export_scores(conn, storage)
        conn.commit()
        return merge_ids, None


def _merge_superseded_thompson_state(
    conn: sqlite3.Connection,
    source_ids: list[str],
    target_id: str,
    now: float,
) -> None:
    if not source_ids or not target_id:
        return

    from smartassist.thompson_rerank import (
        DECAY_FLOOR,
        DECAY_LAMBDA,
        DEFAULT_ALPHA,
        DEFAULT_BETA,
        ensure_thompson_table,
    )
    import math

    ensure_thompson_table(conn)
    placeholders = ",".join("?" for _ in source_ids)
    source_rows = conn.execute(
        f"""
        SELECT lesson_id, alpha, beta, last_updated, injection_count, last_injected
          FROM lesson_thompson
         WHERE lesson_id IN ({placeholders})
           AND context_key = '_global'
        """,
        source_ids,
    ).fetchall()

    if not source_rows:
        return

    def _decayed_state(row: sqlite3.Row) -> tuple[float, float, int, float | None]:
        elapsed = max(0.0, now - float(row["last_updated"] or now))
        decay = max(DECAY_FLOOR, math.exp(-DECAY_LAMBDA * elapsed))
        alpha = max(DECAY_FLOOR, float(row["alpha"] or DEFAULT_ALPHA) * decay)
        beta_val = max(DECAY_FLOOR, float(row["beta"] or DEFAULT_BETA) * decay)
        injection_count = int(row["injection_count"] or 0)
        last_injected = (
            float(row["last_injected"]) if row["last_injected"] is not None else None
        )
        return alpha, beta_val, injection_count, last_injected

    aggregate_alpha = DEFAULT_ALPHA
    aggregate_beta = DEFAULT_BETA
    aggregate_injection_count = 0
    aggregate_last_injected: float | None = None

    target_row = conn.execute(
        """
        SELECT lesson_id, alpha, beta, last_updated, injection_count, last_injected
          FROM lesson_thompson
         WHERE lesson_id = ?
           AND context_key = '_global'
        """,
        (target_id,),
    ).fetchone()

    contributing_rows = list(source_rows)
    if target_row is not None:
        contributing_rows.append(target_row)

    for row in contributing_rows:
        alpha, beta_val, injection_count, last_injected = _decayed_state(row)
        aggregate_alpha += max(0.0, alpha - DEFAULT_ALPHA)
        aggregate_beta += max(0.0, beta_val - DEFAULT_BETA)
        aggregate_injection_count += injection_count
        if last_injected is not None:
            aggregate_last_injected = max(
                aggregate_last_injected or last_injected,
                last_injected,
            )

    conn.execute(
        """
        INSERT INTO lesson_thompson(
            lesson_id, context_key, alpha, beta, last_updated, injection_count, last_injected
        ) VALUES (?, '_global', ?, ?, ?, ?, ?)
        ON CONFLICT(lesson_id, context_key) DO UPDATE SET
            alpha = excluded.alpha,
            beta = excluded.beta,
            last_updated = excluded.last_updated,
            injection_count = excluded.injection_count,
            last_injected = excluded.last_injected
        """,
        (
            target_id,
            aggregate_alpha,
            aggregate_beta,
            now,
            aggregate_injection_count,
            aggregate_last_injected,
        ),
    )


def load_lesson_scores_dict(
    storage_path: Path | str | None = None,
) -> dict[str, dict[str, Any]]:
    with open_store(storage_path, sync=(_SYNC_SCORES,)) as conn:
        rows = conn.execute(
            """
            SELECT lesson_id, boost, ups, downs, blocked, retired, retired_reason, retired_at
              FROM lesson_scores
             ORDER BY lesson_id
            """
        ).fetchall()
        return {
            str(row["lesson_id"]): {
                "boost": float(row["boost"]),
                "ups": int(row["ups"]),
                "downs": int(row["downs"]),
                "blocked": bool(row["blocked"]),
                "retired": bool(row["retired"]),
                "retired_reason": str(row["retired_reason"] or ""),
                "retired_at": row["retired_at"],
            }
            for row in rows
        }


def save_lesson_scores_dict(
    storage_path: Path | str | None,
    data: dict[str, dict[str, Any]],
) -> None:
    storage = _resolve_storage_path(storage_path)
    with open_store(storage, sync=(_SYNC_LESSONS, _SYNC_SCORES)) as conn:
        conn.execute("DELETE FROM lesson_scores")
        for lesson_id, payload in data.items():
            conn.execute(
                """
                INSERT INTO lesson_scores(
                    lesson_id, boost, ups, downs, blocked, retired, retired_reason, retired_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    str(lesson_id),
                    float(payload.get("boost", DEFAULT_BOOST) or DEFAULT_BOOST),
                    int(payload.get("ups", 0) or 0),
                    int(payload.get("downs", 0) or 0),
                    1 if payload.get("blocked", False) else 0,
                    1 if payload.get("retired", False) else 0,
                    str(payload.get("retired_reason", "") or ""),
                    payload.get("retired_at"),
                ),
            )
        _rebuild_search_projection(conn)
        export_scores(conn, storage)
        conn.commit()


def load_last_injection_map(storage_path: Path | str | None = None) -> dict[str, Any]:
    with open_store(storage_path, sync=(_SYNC_INJECTION, _SYNC_LESSONS)) as conn:
        rows = conn.execute(
            "SELECT slot_key, lesson_id FROM last_injection_slots ORDER BY CAST(slot_key AS INTEGER)"
        ).fetchall()
        payload: dict[str, Any] = {
            str(row["slot_key"]): _resolve_active_lesson_id(conn, str(row["lesson_id"]))
            for row in rows
        }
        payload["_timestamp"] = float(
            _get_meta(conn, "last_injection_timestamp", "0") or "0"
        )
        return payload


def save_last_injection_map(
    storage_path: Path | str | None,
    mapping: dict[str, Any],
) -> None:
    storage = _resolve_storage_path(storage_path)
    with open_store(storage, sync=(_SYNC_INJECTION,)) as conn:
        conn.execute("DELETE FROM last_injection_slots")
        for key, value in mapping.items():
            if str(key).startswith("_"):
                continue
            conn.execute(
                "INSERT INTO last_injection_slots(slot_key, lesson_id) VALUES (?, ?)",
                (str(key), str(value)),
            )
        _set_meta(
            conn,
            "last_injection_timestamp",
            str(float(mapping.get("_timestamp", 0) or 0)),
        )
        export_last_injection(conn, storage)
        conn.commit()


def load_session_state_map(
    storage_path: Path | str | None,
    session_id: str,
) -> set[str]:
    with open_store(storage_path, sync=(_SYNC_SESSION, _SYNC_LESSONS)) as conn:
        row = conn.execute(
            "SELECT injected_ids_json FROM session_state WHERE session_id = ?",
            (session_id,),
        ).fetchone()
        if row is None:
            return set()
        return {
            resolved_id
            for lesson_id in json.loads(str(row["injected_ids_json"]))
            for resolved_id in [_resolve_active_lesson_id(conn, str(lesson_id))]
            if resolved_id
        }


def save_session_state_map(
    storage_path: Path | str | None,
    session_id: str,
    injected_ids: Iterable[str],
) -> None:
    storage = _resolve_storage_path(storage_path)
    with open_store(storage, sync=(_SYNC_SESSION,)) as conn:
        conn.execute(
            """
            INSERT INTO session_state(session_id, injected_ids_json)
            VALUES (?, ?)
            ON CONFLICT(session_id) DO UPDATE SET injected_ids_json = excluded.injected_ids_json
            """,
            (session_id, json.dumps(sorted(set(injected_ids)))),
        )
        export_session_state(conn, storage)
        conn.commit()


def append_feedback_event(
    storage_path: Path | str | None,
    event: dict[str, Any],
) -> None:
    storage = _resolve_storage_path(storage_path)
    with open_store(storage, sync=(_SYNC_LESSONS, _SYNC_SCORES, _SYNC_EVENTS)) as conn:
        conn.execute(
            """
            INSERT INTO feedback_events(
                timestamp, signal, category_key, intensity, query, response,
                correction, context, session_id
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                float(event.get("timestamp", time.time()) or time.time()),
                str(event.get("signal") or "unknown"),
                str(event.get("category") or "unknown"),
                int(event.get("intensity", 0) or 0),
                str(event.get("query") or ""),
                str(event.get("response") or ""),
                str(event.get("correction") or ""),
                str(event.get("context") or ""),
                str(event.get("session_id") or "default"),
            ),
        )
        _rebuild_search_projection(conn)
        export_feedback_events(conn, storage)
        conn.commit()


def list_feedback_events(
    storage_path: Path | str | None = None,
) -> list[dict[str, Any]]:
    with open_store(storage_path, sync=(_SYNC_EVENTS,)) as conn:
        rows = conn.execute(
            """
            SELECT timestamp, signal, category_key, intensity, query, response,
                   correction, context, session_id
              FROM feedback_events
             ORDER BY event_id
            """
        ).fetchall()
        return [
            {
                "timestamp": float(row["timestamp"]),
                "signal": str(row["signal"]),
                "category": str(row["category_key"]),
                "intensity": int(row["intensity"]),
                "query": str(row["query"] or ""),
                "response": str(row["response"] or ""),
                "correction": str(row["correction"] or ""),
                "context": str(row["context"] or ""),
                "session_id": str(row["session_id"] or "default"),
            }
            for row in rows
        ]


def get_feedback_stats(storage_path: Path | str | None = None) -> dict[str, Any]:
    with open_store(storage_path, sync=(_SYNC_EVENTS,)) as conn:
        total = conn.execute("SELECT COUNT(*) FROM feedback_events").fetchone()[0]
        by_cat_rows = conn.execute(
            "SELECT category_key, COUNT(*) AS count FROM feedback_events GROUP BY category_key"
        ).fetchall()
        by_sig_rows = conn.execute(
            "SELECT signal, COUNT(*) AS count FROM feedback_events GROUP BY signal"
        ).fetchall()
        return {
            "total_events": int(total),
            "by_category": {
                str(row["category_key"]): int(row["count"]) for row in by_cat_rows
            },
            "by_signal": {str(row["signal"]): int(row["count"]) for row in by_sig_rows},
        }


def load_reliabilities_dict(
    storage_path: Path | str | None = None,
) -> dict[str, dict[str, Any]]:
    with open_store(storage_path, sync=(_SYNC_RELIABILITY,)) as conn:
        rows = conn.execute(
            """
            SELECT category_key, alpha, beta, last_updated, total_samples
              FROM category_reliability
             ORDER BY category_key
            """
        ).fetchall()
        return {
            str(row["category_key"]): {
                "category": str(row["category_key"]),
                "alpha": float(row["alpha"]),
                "beta": float(row["beta"]),
                "last_updated": float(row["last_updated"]),
                "total_samples": int(row["total_samples"]),
            }
            for row in rows
        }


def save_reliabilities_dict(
    storage_path: Path | str | None,
    data: dict[str, dict[str, Any]],
) -> None:
    storage = _resolve_storage_path(storage_path)
    with open_store(storage, sync=(_SYNC_RELIABILITY,)) as conn:
        conn.execute("DELETE FROM category_reliability")
        for category, payload in data.items():
            conn.execute(
                """
                INSERT INTO category_reliability(
                    category_key, alpha, beta, last_updated, total_samples
                ) VALUES (?, ?, ?, ?, ?)
                """,
                (
                    str(category),
                    float(payload.get("alpha", 1.0) or 1.0),
                    float(payload.get("beta", 1.0) or 1.0),
                    float(payload.get("last_updated", time.time()) or time.time()),
                    int(payload.get("total_samples", 0) or 0),
                ),
            )
        export_reliability(conn, storage)
        conn.commit()


def load_feedback_metrics_dict(
    storage_path: Path | str | None = None,
) -> dict[str, Any]:
    with open_store(storage_path, sync=(_SYNC_METRICS,)) as conn:
        rows = conn.execute(
            "SELECT metric_key, metric_value, metric_text FROM feedback_metrics"
        ).fetchall()
        payload = dict(DEFAULT_METRICS)
        for row in rows:
            key = str(row["metric_key"])
            if key == "last_updated":
                payload[key] = row["metric_text"]
            else:
                payload[key] = int(row["metric_value"] or 0)
        return payload


def increment_feedback_metric(
    storage_path: Path | str | None,
    metric_key: str,
) -> None:
    storage = _resolve_storage_path(storage_path)
    with open_store(storage, sync=(_SYNC_METRICS,)) as conn:
        now = datetime.now().isoformat()
        current_row = conn.execute(
            "SELECT metric_value FROM feedback_metrics WHERE metric_key = ?",
            (metric_key,),
        ).fetchone()
        current = int(current_row["metric_value"] or 0) if current_row else 0
        conn.execute(
            """
            INSERT INTO feedback_metrics(metric_key, metric_value, metric_text, updated_at)
            VALUES (?, ?, NULL, ?)
            ON CONFLICT(metric_key) DO UPDATE SET
                metric_value = excluded.metric_value,
                updated_at = excluded.updated_at
            """,
            (metric_key, current + 1, now),
        )
        conn.execute(
            """
            INSERT INTO feedback_metrics(metric_key, metric_value, metric_text, updated_at)
            VALUES ('last_updated', NULL, ?, ?)
            ON CONFLICT(metric_key) DO UPDATE SET
                metric_text = excluded.metric_text,
                updated_at = excluded.updated_at
            """,
            (now, now),
        )
        export_feedback_metrics(conn, storage)
        conn.commit()


def list_search_documents(
    storage_path: Path | str | None = None,
    *,
    active_only: bool = True,
) -> list[dict[str, Any]]:
    with open_store(
        storage_path, sync=(_SYNC_LESSONS, _SYNC_SCORES, _SYNC_EVENTS)
    ) as conn:
        query = """
            SELECT doc_id, source_type, source_id, category_key, text, search_text,
                   active, version, content_hash, updated_at
              FROM search_documents
        """
        if active_only:
            query += " WHERE active = 1"
        query += " ORDER BY source_id"
        rows = conn.execute(query).fetchall()
        return [
            {
                "doc_id": str(row["doc_id"]),
                "source_type": str(row["source_type"]),
                "source_id": str(row["source_id"]),
                "category": str(row["category_key"]),
                "text": str(row["text"]),
                "search_text": str(row["search_text"]),
                "active": bool(row["active"]),
                "version": int(row["version"]),
                "content_hash": str(row["content_hash"]),
                "updated_at": float(row["updated_at"]),
            }
            for row in rows
        ]


def search_projection_documents(
    storage_path: Path | str | None,
    query: str,
    *,
    top_k: int = 5,
    category: str | None = None,
    source_types: Iterable[str] | None = None,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    with open_store(
        storage_path, sync=(_SYNC_LESSONS, _SYNC_SCORES, _SYNC_EVENTS)
    ) as conn:
        rows = conn.execute(
            """
            SELECT d.doc_id, d.source_type, d.source_id, d.category_key, d.text, d.search_text,
                   d.active, d.version, d.content_hash, d.updated_at,
                   COALESCE(s.boost, ?) AS boost
              FROM search_documents d
              LEFT JOIN lesson_scores s
                ON d.source_type = 'lesson' AND s.lesson_id = d.source_id
             WHERE d.active = 1
             ORDER BY d.source_id
            """,
            (DEFAULT_BOOST,),
        ).fetchall()

    raw_count = len(rows)
    category_filter_used = None
    source_type_filter_used = None
    if category:
        category_filter_used = category.lower().strip()
        filtered_rows = [
            row
            for row in rows
            if str(row["category_key"]).lower() == category_filter_used
        ]
    else:
        filtered_rows = list(rows)
    category_filtered = raw_count - len(filtered_rows)

    if source_types:
        source_type_filter_used = sorted(
            {
                str(source_type).lower().strip()
                for source_type in source_types
                if str(source_type).strip()
            }
        )
        filtered_rows = [
            row
            for row in filtered_rows
            if str(row["source_type"]).lower() in source_type_filter_used
        ]
    source_type_filtered = raw_count - category_filtered - len(filtered_rows)

    query_tokens = _tokenize_search(query)
    if not query_tokens:
        return [], {
            "raw_count": raw_count,
            "category_filtered": category_filtered,
            "category_filter_used": category_filter_used,
            "source_type_filtered": source_type_filtered,
            "source_type_filter_used": source_type_filter_used,
            "query_tokens": [],
        }

    idf = _build_search_idf(filtered_rows)
    expanded = _expand_search_terms(query_tokens)
    scored: list[dict[str, Any]] = []

    for row in filtered_rows:
        doc_tokens = set(_tokenize_search(str(row["search_text"])))
        doc_tokens.add(str(row["category_key"]).lower())

        lexical_score = 0.0
        matched: set[str] = set()
        for token in expanded:
            if token in doc_tokens:
                lexical_score += idf.get(token, 1.0)
                matched.add(token)
        if not matched:
            for token in expanded:
                for doc_token in doc_tokens:
                    if len(token) >= 4 and (token in doc_token or doc_token in token):
                        lexical_score += 0.3
                        matched.add(token)
                        break

        if lexical_score < 0.5:
            continue

        relevance = lexical_score / (max(1, len(expanded)) * 1.5)
        boost = float(row["boost"] or DEFAULT_BOOST)
        source_weight = 1.15 if str(row["source_type"]) == "lesson" else 1.0
        relevance *= boost * source_weight

        scored.append(
            {
                "doc_id": str(row["doc_id"]),
                "source_type": str(row["source_type"]),
                "source_id": str(row["source_id"]),
                "category": str(row["category_key"]),
                "text": str(row["text"]),
                "search_text": str(row["search_text"]),
                "score": relevance,
                "matched": matched,
                "boost": boost,
                "updated_at": float(row["updated_at"]),
            }
        )

    scored.sort(key=lambda item: (-item["score"], -item["updated_at"], item["doc_id"]))
    return scored[:top_k], {
        "raw_count": raw_count,
        "category_filtered": category_filtered,
        "category_filter_used": category_filter_used,
        "source_type_filtered": source_type_filtered,
        "source_type_filter_used": source_type_filter_used,
        "query_tokens": sorted(expanded),
    }


def rebuild_search_projection(storage_path: Path | str | None = None) -> None:
    storage = _resolve_storage_path(storage_path)
    with open_store(storage, sync=(_SYNC_LESSONS, _SYNC_SCORES, _SYNC_EVENTS)) as conn:
        _rebuild_search_projection(conn)
        conn.commit()
