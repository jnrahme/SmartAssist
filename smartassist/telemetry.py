from __future__ import annotations

import hashlib
import json
import sqlite3
import sys
import uuid
from collections import Counter
from datetime import datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import urlparse, urlunparse
from urllib.request import Request, urlopen

from smartassist import __version__
from smartassist.config import atomic_write_json, locked_update_json
from smartassist.store import list_feedback_events, load_reliabilities_dict

TELEMETRY_SCHEMA_VERSION = 1
DEFAULT_COLLECTOR_HOST = "127.0.0.1"
DEFAULT_COLLECTOR_PORT = 8787
WEAK_CATEGORY_THRESHOLD = 0.70
LIFECYCLE_EVENT_NAMES = {
    "install_started",
    "setup_completed",
    "setup_failed",
    "project_initialized",
    "agent_configured",
    "doctor_ready",
    "doctor_not_ready",
    "seed_completed",
    "dashboard_opened",
    "uninstall_requested",
}


def get_telemetry_root() -> Path:
    return Path.home() / ".smartassist" / "telemetry"


def get_telemetry_config_path() -> Path:
    return get_telemetry_root() / "config.json"


def get_telemetry_queue_path() -> Path:
    return get_telemetry_root() / "events.jsonl"


def get_aggregate_db_path() -> Path:
    return get_telemetry_root() / "aggregate.db"


def _now() -> datetime:
    return datetime.now()


def _now_iso() -> str:
    return _now().isoformat()


def _python_version_band() -> str:
    return f"{sys.version_info.major}.{sys.version_info.minor}"


def _os_family() -> str:
    if sys.platform == "darwin":
        return "macos"
    if sys.platform.startswith("linux"):
        return "linux"
    if sys.platform.startswith("win"):
        return "windows"
    return sys.platform


def _default_config() -> dict[str, Any]:
    return {
        "version": TELEMETRY_SCHEMA_VERSION,
        "enabled": False,
        "install_id": "",
        "endpoint": "",
        "known_projects": [],
        "last_enabled_at": None,
        "last_disabled_at": None,
        "last_flush_at": None,
    }


def _normalize_projects(raw: Any) -> list[str]:
    seen: set[str] = set()
    normalized: list[str] = []
    if not isinstance(raw, list):
        return normalized
    for entry in raw:
        value = str(entry or "").strip()
        if not value:
            continue
        resolved = str(Path(value).expanduser())
        if resolved in seen:
            continue
        seen.add(resolved)
        normalized.append(resolved)
    return normalized


def _normalize_config(raw: Any) -> dict[str, Any]:
    payload = _default_config()
    if isinstance(raw, dict):
        payload.update(raw)
    payload["version"] = TELEMETRY_SCHEMA_VERSION
    payload["enabled"] = bool(payload.get("enabled", False))
    payload["install_id"] = str(payload.get("install_id") or "").strip()
    payload["endpoint"] = str(payload.get("endpoint") or "").strip()
    payload["known_projects"] = _normalize_projects(payload.get("known_projects"))
    payload["last_enabled_at"] = payload.get("last_enabled_at")
    payload["last_disabled_at"] = payload.get("last_disabled_at")
    payload["last_flush_at"] = payload.get("last_flush_at")
    return payload


def load_telemetry_config() -> dict[str, Any]:
    path = get_telemetry_config_path()
    if not path.exists():
        return _default_config()
    try:
        return _normalize_config(json.loads(path.read_text()))
    except (OSError, json.JSONDecodeError):
        return _default_config()


def _update_config(updater) -> dict[str, Any]:
    path = get_telemetry_config_path()

    def _wrapped(current: Any) -> dict[str, Any]:
        payload = _normalize_config(current)
        updated = updater(payload)
        return _normalize_config(updated if updated is not None else payload)

    return locked_update_json(path, _wrapped, default=_default_config())


def ensure_install_id() -> str:
    updated = _update_config(
        lambda current: {
            **current,
            "install_id": current.get("install_id") or uuid.uuid4().hex,
        }
    )
    return str(updated.get("install_id") or "")


def enable_telemetry(endpoint: str = "") -> dict[str, Any]:
    timestamp = _now_iso()

    def _updater(current: dict[str, Any]) -> dict[str, Any]:
        current["enabled"] = True
        current["install_id"] = current.get("install_id") or uuid.uuid4().hex
        if endpoint:
            current["endpoint"] = endpoint.strip()
        current["last_enabled_at"] = timestamp
        return current

    return _update_config(_updater)


def disable_telemetry() -> dict[str, Any]:
    timestamp = _now_iso()

    def _updater(current: dict[str, Any]) -> dict[str, Any]:
        current["enabled"] = False
        current["last_disabled_at"] = timestamp
        return current

    return _update_config(_updater)


def register_project(storage_path: Path | str) -> dict[str, Any]:
    storage = str(Path(storage_path).expanduser().resolve())

    def _updater(current: dict[str, Any]) -> dict[str, Any]:
        known = _normalize_projects(current.get("known_projects"))
        if storage not in known:
            known.append(storage)
        current["known_projects"] = known
        return current

    return _update_config(_updater)


def _load_queue_events() -> list[dict[str, Any]]:
    path = get_telemetry_queue_path()
    if not path.exists():
        return []
    events: list[dict[str, Any]] = []
    with open(path, "r", encoding="utf-8") as handle:
        for line in handle:
            if not line.strip():
                continue
            try:
                payload = json.loads(line)
            except json.JSONDecodeError:
                continue
            if isinstance(payload, dict):
                events.append(payload)
    return events


def _append_queue_event(event: dict[str, Any]) -> None:
    path = get_telemetry_queue_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "a", encoding="utf-8") as handle:
        handle.write(json.dumps(event, sort_keys=True) + "\n")


def record_lifecycle_event(
    event_name: str,
    *,
    agent_type: str = "",
    metadata: dict[str, Any] | None = None,
    occurred_at: str | None = None,
) -> dict[str, Any] | None:
    if event_name not in LIFECYCLE_EVENT_NAMES:
        raise ValueError(f"Unknown telemetry event '{event_name}'")

    config = load_telemetry_config()
    if not config.get("enabled"):
        return None

    install_id = config.get("install_id") or ensure_install_id()
    event = {
        "schema_version": TELEMETRY_SCHEMA_VERSION,
        "event_id": uuid.uuid4().hex,
        "install_id": install_id,
        "event_name": event_name,
        "occurred_at": occurred_at or _now_iso(),
        "smartassist_version": __version__,
        "os_family": _os_family(),
        "python_version_band": _python_version_band(),
        "agent_type": str(agent_type or "").strip(),
        "metadata": metadata or {},
    }
    _append_queue_event(event)
    return event


def get_telemetry_status() -> dict[str, Any]:
    config = load_telemetry_config()
    known_projects = [
        Path(path)
        for path in _normalize_projects(config.get("known_projects"))
        if Path(path).exists()
    ]
    queue_events = _load_queue_events()
    return {
        "enabled": bool(config.get("enabled")),
        "install_id": str(config.get("install_id") or ""),
        "endpoint": str(config.get("endpoint") or ""),
        "known_projects": len(known_projects),
        "known_project_paths": [str(path) for path in known_projects],
        "queued_events": len(queue_events),
        "queue_path": str(get_telemetry_queue_path()),
        "aggregate_db_path": str(get_aggregate_db_path()),
    }


def _parse_usage_timestamp(raw: Any) -> datetime | None:
    value = str(raw or "").strip()
    if not value:
        return None
    try:
        return datetime.fromisoformat(value)
    except ValueError:
        return None


def _event_in_period(dt: datetime, period: str, now: datetime) -> bool:
    if period == "daily":
        return dt.date() == now.date()
    if period == "weekly":
        return dt.isocalendar()[:2] == now.isocalendar()[:2]
    raise ValueError(f"Unsupported period '{period}'")


def _feedback_in_period(timestamp: Any, period: str, now: datetime) -> bool:
    try:
        dt = datetime.fromtimestamp(float(timestamp or 0))
    except (TypeError, ValueError, OSError):
        return False
    return _event_in_period(dt, period, now)


def _period_key(period: str, now: datetime) -> str:
    if period == "daily":
        return now.date().isoformat()
    if period == "weekly":
        iso = now.isocalendar()
        return f"{iso.year}-W{iso.week:02d}"
    raise ValueError(f"Unsupported period '{period}'")


def _existing_storage_paths(config: dict[str, Any] | None = None) -> list[Path]:
    payload = config or load_telemetry_config()
    existing: list[Path] = []
    missing: list[str] = []
    for raw in _normalize_projects(payload.get("known_projects")):
        path = Path(raw).expanduser()
        if path.exists():
            existing.append(path)
        else:
            missing.append(str(path))
    if missing:
        missing_set = set(missing)
        _update_config(
            lambda current: {
                **current,
                "known_projects": [
                    path
                    for path in _normalize_projects(current.get("known_projects"))
                    if path not in missing_set
                ],
            }
        )
    return existing


def _load_usage_events(storage_path: Path) -> list[dict[str, Any]]:
    usage_log = storage_path / "usage_log.jsonl"
    if not usage_log.exists():
        return []
    events: list[dict[str, Any]] = []
    with open(usage_log, "r", encoding="utf-8") as handle:
        for line in handle:
            if not line.strip():
                continue
            try:
                payload = json.loads(line)
            except json.JSONDecodeError:
                continue
            if isinstance(payload, dict):
                events.append(payload)
    return events


def _rollup_period_metrics(period: str, now: datetime) -> dict[str, Any]:
    storage_paths = _existing_storage_paths()
    lifecycle_counts = {name: 0 for name in sorted(LIFECYCLE_EVENT_NAMES)}
    agent_counts: Counter[str] = Counter()
    weak_categories: Counter[str] = Counter()
    searches = 0
    searches_with_results = 0
    rag_dashboards = 0
    positive_feedback = 0
    negative_feedback = 0

    for event in _load_queue_events():
        occurred_at = _parse_usage_timestamp(event.get("occurred_at"))
        if occurred_at is None or not _event_in_period(occurred_at, period, now):
            continue
        event_name = str(event.get("event_name") or "")
        if event_name in lifecycle_counts:
            lifecycle_counts[event_name] += 1
        agent = str(event.get("agent_type") or "").strip()
        if agent:
            agent_counts[agent] += 1

    for storage in storage_paths:
        for event in _load_usage_events(storage):
            timestamp = _parse_usage_timestamp(event.get("timestamp"))
            if timestamp is None or not _event_in_period(timestamp, period, now):
                continue
            tool = str(event.get("tool") or "")
            if tool == "rag_search":
                searches += 1
                if int(event.get("results_count", 0) or 0) > 0:
                    searches_with_results += 1
            elif tool == "rag_dashboard":
                rag_dashboards += 1

        for event in list_feedback_events(storage):
            if not _feedback_in_period(event.get("timestamp"), period, now):
                continue
            signal = str(event.get("signal") or "")
            if signal == "thumbs_up":
                positive_feedback += 1
            elif signal in {"thumbs_down", "correction"}:
                negative_feedback += 1

        reliabilities = load_reliabilities_dict(storage)
        for category, payload in reliabilities.items():
            alpha = float(payload.get("alpha", 1.0) or 1.0)
            beta = float(payload.get("beta", 1.0) or 1.0)
            mean = alpha / (alpha + beta) if (alpha + beta) else 0.5
            if mean < WEAK_CATEGORY_THRESHOLD:
                weak_categories[str(category)] += 1

    return {
        "known_projects": len(storage_paths),
        **lifecycle_counts,
        "searches": searches,
        "searches_with_results": searches_with_results,
        "rag_dashboards": rag_dashboards,
        "positive_feedback": positive_feedback,
        "negative_feedback": negative_feedback,
        "agent_counts": dict(
            sorted(agent_counts.items(), key=lambda item: (-item[1], item[0]))
        ),
        "weak_categories": dict(
            sorted(weak_categories.items(), key=lambda item: (-item[1], item[0]))
        ),
    }


def build_rollup(period: str, now: datetime | None = None) -> dict[str, Any]:
    if period not in {"daily", "weekly"}:
        raise ValueError(f"Unsupported rollup period '{period}'")
    current = now or _now()
    install_id = ensure_install_id()
    metrics = _rollup_period_metrics(period, current)
    period_key = _period_key(period, current)
    rollup_id = hashlib.sha1(
        f"{install_id}:{period}:{period_key}:{__version__}".encode("utf-8")
    ).hexdigest()
    return {
        "schema_version": TELEMETRY_SCHEMA_VERSION,
        "rollup_kind": period,
        "rollup_id": rollup_id,
        "period_key": period_key,
        "install_id": install_id,
        "smartassist_version": __version__,
        "os_family": _os_family(),
        "python_version_band": _python_version_band(),
        **metrics,
        "generated_at": current.isoformat(),
    }


def build_export_bundle(now: datetime | None = None) -> dict[str, Any]:
    current = now or _now()
    config = load_telemetry_config()
    install_id = config.get("install_id") or ensure_install_id()
    daily_rollup = build_rollup("daily", current)
    weekly_rollup = build_rollup("weekly", current)
    events = _load_queue_events()
    bundle_id = hashlib.sha1(
        json.dumps(
            {
                "install_id": install_id,
                "events": [event.get("event_id") for event in events],
                "daily": daily_rollup.get("rollup_id"),
                "weekly": weekly_rollup.get("rollup_id"),
            },
            sort_keys=True,
        ).encode("utf-8")
    ).hexdigest()
    return {
        "schema_version": TELEMETRY_SCHEMA_VERSION,
        "bundle_id": bundle_id,
        "generated_at": current.isoformat(),
        "install_id": install_id,
        "client": {
            "smartassist_version": __version__,
            "os_family": _os_family(),
            "python_version_band": _python_version_band(),
            "enabled": bool(config.get("enabled")),
        },
        "lifecycle_events": events,
        "daily_rollups": [daily_rollup],
        "weekly_rollups": [weekly_rollup],
    }


def export_bundle(output_path: Path | str | None = None) -> tuple[Path, dict[str, Any]]:
    payload = build_export_bundle()
    if output_path is None:
        timestamp = _now().strftime("%Y%m%d_%H%M%S")
        destination = Path.cwd().resolve() / f"smartassist-telemetry-{timestamp}.json"
    else:
        destination = Path(output_path).expanduser().resolve()
    atomic_write_json(destination, payload)
    return destination, payload


def _normalize_endpoint(raw: str) -> str:
    value = str(raw or "").strip()
    if not value:
        return ""
    parsed = urlparse(value)
    path = parsed.path or ""
    if not path or path == "/":
        path = "/ingest"
    return urlunparse(parsed._replace(path=path))


def flush_bundle(endpoint: str = "") -> tuple[bool, dict[str, Any] | str]:
    config = load_telemetry_config()
    url = _normalize_endpoint(endpoint or str(config.get("endpoint") or ""))
    if not url:
        return (
            False,
            "No telemetry endpoint configured. Use 'smartassist telemetry enable --endpoint URL' or pass --endpoint.",
        )

    payload = build_export_bundle()
    request = Request(
        url,
        data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urlopen(request, timeout=30) as response:
            body = response.read().decode("utf-8")
    except HTTPError as exc:
        return False, f"Collector rejected telemetry upload: HTTP {exc.code}"
    except URLError as exc:
        return False, f"Collector request failed: {exc.reason}"

    try:
        parsed = json.loads(body) if body else {}
    except json.JSONDecodeError:
        parsed = {"raw": body}

    _update_config(lambda current: {**current, "last_flush_at": _now_iso()})
    return True, {"endpoint": url, "response": parsed, "bundle": payload}


def _connect_aggregate_db(db_path: Path | str) -> sqlite3.Connection:
    path = Path(db_path).expanduser().resolve()
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(path))
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA synchronous=NORMAL")
    conn.executescript(
        """
        CREATE TABLE IF NOT EXISTS telemetry_events_raw (
            event_id TEXT PRIMARY KEY,
            install_id TEXT NOT NULL,
            event_name TEXT NOT NULL,
            occurred_at TEXT NOT NULL,
            smartassist_version TEXT NOT NULL,
            os_family TEXT NOT NULL,
            python_version_band TEXT NOT NULL,
            agent_type TEXT NOT NULL,
            metadata_json TEXT NOT NULL,
            received_at TEXT NOT NULL
        );

        CREATE TABLE IF NOT EXISTS telemetry_daily_rollups (
            rollup_id TEXT PRIMARY KEY,
            install_id TEXT NOT NULL,
            period_key TEXT NOT NULL,
            smartassist_version TEXT NOT NULL,
            os_family TEXT NOT NULL,
            python_version_band TEXT NOT NULL,
            known_projects INTEGER NOT NULL,
            install_started INTEGER NOT NULL,
            setup_completed INTEGER NOT NULL,
            setup_failed INTEGER NOT NULL,
            project_initialized INTEGER NOT NULL,
            agent_configured INTEGER NOT NULL,
            doctor_ready INTEGER NOT NULL,
            doctor_not_ready INTEGER NOT NULL,
            seed_completed INTEGER NOT NULL,
            dashboard_opened INTEGER NOT NULL,
            uninstall_requested INTEGER NOT NULL,
            searches INTEGER NOT NULL,
            searches_with_results INTEGER NOT NULL,
            rag_dashboards INTEGER NOT NULL,
            positive_feedback INTEGER NOT NULL,
            negative_feedback INTEGER NOT NULL,
            agent_counts_json TEXT NOT NULL,
            weak_categories_json TEXT NOT NULL,
            generated_at TEXT NOT NULL,
            payload_json TEXT NOT NULL
        );

        CREATE TABLE IF NOT EXISTS telemetry_weekly_rollups (
            rollup_id TEXT PRIMARY KEY,
            install_id TEXT NOT NULL,
            period_key TEXT NOT NULL,
            smartassist_version TEXT NOT NULL,
            os_family TEXT NOT NULL,
            python_version_band TEXT NOT NULL,
            known_projects INTEGER NOT NULL,
            install_started INTEGER NOT NULL,
            setup_completed INTEGER NOT NULL,
            setup_failed INTEGER NOT NULL,
            project_initialized INTEGER NOT NULL,
            agent_configured INTEGER NOT NULL,
            doctor_ready INTEGER NOT NULL,
            doctor_not_ready INTEGER NOT NULL,
            seed_completed INTEGER NOT NULL,
            dashboard_opened INTEGER NOT NULL,
            uninstall_requested INTEGER NOT NULL,
            searches INTEGER NOT NULL,
            searches_with_results INTEGER NOT NULL,
            rag_dashboards INTEGER NOT NULL,
            positive_feedback INTEGER NOT NULL,
            negative_feedback INTEGER NOT NULL,
            agent_counts_json TEXT NOT NULL,
            weak_categories_json TEXT NOT NULL,
            generated_at TEXT NOT NULL,
            payload_json TEXT NOT NULL
        );

        CREATE TABLE IF NOT EXISTS telemetry_release_rollups (
            smartassist_version TEXT PRIMARY KEY,
            installs INTEGER NOT NULL,
            install_started INTEGER NOT NULL,
            setup_completed INTEGER NOT NULL,
            doctor_ready INTEGER NOT NULL,
            doctor_not_ready INTEGER NOT NULL,
            searches INTEGER NOT NULL,
            searches_with_results INTEGER NOT NULL,
            positive_feedback INTEGER NOT NULL,
            negative_feedback INTEGER NOT NULL,
            uninstall_requested INTEGER NOT NULL,
            latest_period TEXT NOT NULL,
            refreshed_at TEXT NOT NULL
        );

        CREATE INDEX IF NOT EXISTS idx_telemetry_events_name ON telemetry_events_raw(event_name);
        CREATE INDEX IF NOT EXISTS idx_telemetry_events_when ON telemetry_events_raw(occurred_at);
        CREATE INDEX IF NOT EXISTS idx_daily_rollups_period ON telemetry_daily_rollups(period_key);
        CREATE INDEX IF NOT EXISTS idx_weekly_rollups_period ON telemetry_weekly_rollups(period_key);
        """
    )
    return conn


def initialize_aggregate_store(db_path: Path | str) -> Path:
    path = Path(db_path).expanduser().resolve()
    with _connect_aggregate_db(path) as conn:
        conn.commit()
    return path


def _rollup_columns() -> tuple[str, ...]:
    return (
        "rollup_id",
        "install_id",
        "period_key",
        "smartassist_version",
        "os_family",
        "python_version_band",
        "known_projects",
        "install_started",
        "setup_completed",
        "setup_failed",
        "project_initialized",
        "agent_configured",
        "doctor_ready",
        "doctor_not_ready",
        "seed_completed",
        "dashboard_opened",
        "uninstall_requested",
        "searches",
        "searches_with_results",
        "rag_dashboards",
        "positive_feedback",
        "negative_feedback",
        "agent_counts_json",
        "weak_categories_json",
        "generated_at",
        "payload_json",
    )


def _upsert_rollup(
    conn: sqlite3.Connection, table: str, rollup: dict[str, Any]
) -> None:
    if table not in {"telemetry_daily_rollups", "telemetry_weekly_rollups"}:
        raise ValueError(f"Unsupported rollup table '{table}'")
    columns = _rollup_columns()
    payload_json = json.dumps(rollup, sort_keys=True)
    values = (
        str(rollup.get("rollup_id") or ""),
        str(rollup.get("install_id") or ""),
        str(rollup.get("period_key") or ""),
        str(rollup.get("smartassist_version") or ""),
        str(rollup.get("os_family") or ""),
        str(rollup.get("python_version_band") or ""),
        int(rollup.get("known_projects", 0) or 0),
        int(rollup.get("install_started", 0) or 0),
        int(rollup.get("setup_completed", 0) or 0),
        int(rollup.get("setup_failed", 0) or 0),
        int(rollup.get("project_initialized", 0) or 0),
        int(rollup.get("agent_configured", 0) or 0),
        int(rollup.get("doctor_ready", 0) or 0),
        int(rollup.get("doctor_not_ready", 0) or 0),
        int(rollup.get("seed_completed", 0) or 0),
        int(rollup.get("dashboard_opened", 0) or 0),
        int(rollup.get("uninstall_requested", 0) or 0),
        int(rollup.get("searches", 0) or 0),
        int(rollup.get("searches_with_results", 0) or 0),
        int(rollup.get("rag_dashboards", 0) or 0),
        int(rollup.get("positive_feedback", 0) or 0),
        int(rollup.get("negative_feedback", 0) or 0),
        json.dumps(rollup.get("agent_counts", {}), sort_keys=True),
        json.dumps(rollup.get("weak_categories", {}), sort_keys=True),
        str(rollup.get("generated_at") or ""),
        payload_json,
    )
    assignments = ", ".join(
        f"{column}=excluded.{column}" for column in columns if column != "rollup_id"
    )
    placeholders = ", ".join("?" for _ in columns)
    conn.execute(
        f"""
        INSERT INTO {table} ({", ".join(columns)})
        VALUES ({placeholders})
        ON CONFLICT(rollup_id) DO UPDATE SET {assignments}
        """,
        values,
    )


def _refresh_release_rollups(conn: sqlite3.Connection) -> None:
    refreshed_at = _now_iso()
    conn.execute("DELETE FROM telemetry_release_rollups")
    conn.execute(
        """
        INSERT INTO telemetry_release_rollups(
            smartassist_version,
            installs,
            install_started,
            setup_completed,
            doctor_ready,
            doctor_not_ready,
            searches,
            searches_with_results,
            positive_feedback,
            negative_feedback,
            uninstall_requested,
            latest_period,
            refreshed_at
        )
        SELECT
            smartassist_version,
            COUNT(DISTINCT install_id),
            SUM(install_started),
            SUM(setup_completed),
            SUM(doctor_ready),
            SUM(doctor_not_ready),
            SUM(searches),
            SUM(searches_with_results),
            SUM(positive_feedback),
            SUM(negative_feedback),
            SUM(uninstall_requested),
            MAX(period_key),
            ?
          FROM telemetry_weekly_rollups
         GROUP BY smartassist_version
        """,
        (refreshed_at,),
    )


def ingest_bundle(db_path: Path | str, bundle: dict[str, Any]) -> dict[str, Any]:
    path = initialize_aggregate_store(db_path)
    inserted_events = 0
    daily_count = 0
    weekly_count = 0
    with _connect_aggregate_db(path) as conn:
        for event in bundle.get("lifecycle_events", []):
            cursor = conn.execute(
                """
                INSERT OR IGNORE INTO telemetry_events_raw(
                    event_id,
                    install_id,
                    event_name,
                    occurred_at,
                    smartassist_version,
                    os_family,
                    python_version_band,
                    agent_type,
                    metadata_json,
                    received_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    str(event.get("event_id") or ""),
                    str(event.get("install_id") or bundle.get("install_id") or ""),
                    str(event.get("event_name") or ""),
                    str(event.get("occurred_at") or ""),
                    str(
                        event.get("smartassist_version")
                        or bundle.get("client", {}).get("smartassist_version")
                        or ""
                    ),
                    str(
                        event.get("os_family")
                        or bundle.get("client", {}).get("os_family")
                        or ""
                    ),
                    str(
                        event.get("python_version_band")
                        or bundle.get("client", {}).get("python_version_band")
                        or ""
                    ),
                    str(event.get("agent_type") or ""),
                    json.dumps(event.get("metadata", {}), sort_keys=True),
                    _now_iso(),
                ),
            )
            inserted_events += int(cursor.rowcount or 0)

        for rollup in bundle.get("daily_rollups", []):
            _upsert_rollup(conn, "telemetry_daily_rollups", rollup)
            daily_count += 1
        for rollup in bundle.get("weekly_rollups", []):
            _upsert_rollup(conn, "telemetry_weekly_rollups", rollup)
            weekly_count += 1

        _refresh_release_rollups(conn)
        conn.commit()

    return {
        "db_path": str(path),
        "events_inserted": inserted_events,
        "events_received": len(bundle.get("lifecycle_events", [])),
        "daily_rollups": daily_count,
        "weekly_rollups": weekly_count,
    }


def _parse_counter_json(raw: Any) -> Counter[str]:
    try:
        payload = json.loads(raw or "{}")
    except json.JSONDecodeError:
        return Counter()
    counter: Counter[str] = Counter()
    if isinstance(payload, dict):
        for key, value in payload.items():
            counter[str(key)] += int(value or 0)
    return counter


def get_aggregate_summary(db_path: Path | str) -> dict[str, Any]:
    path = initialize_aggregate_store(db_path)
    with _connect_aggregate_db(path) as conn:
        weekly_rows = [
            dict(row)
            for row in conn.execute(
                "SELECT * FROM telemetry_weekly_rollups ORDER BY period_key, install_id"
            ).fetchall()
        ]
        daily_rows = [
            dict(row)
            for row in conn.execute(
                "SELECT * FROM telemetry_daily_rollups ORDER BY period_key, install_id"
            ).fetchall()
        ]
        release_rows = [
            dict(row)
            for row in conn.execute(
                "SELECT * FROM telemetry_release_rollups ORDER BY smartassist_version"
            ).fetchall()
        ]
        event_rows = [
            dict(row)
            for row in conn.execute(
                "SELECT * FROM telemetry_events_raw ORDER BY occurred_at DESC"
            ).fetchall()
        ]

    latest_week = max((row["period_key"] for row in weekly_rows), default="")
    latest_week_rows = [row for row in weekly_rows if row["period_key"] == latest_week]
    active_installs = len({row["install_id"] for row in latest_week_rows})

    def _sum(rows: list[dict[str, Any]], field: str) -> int:
        return sum(int(row.get(field, 0) or 0) for row in rows)

    current_install_started = _sum(latest_week_rows, "install_started")
    current_setup_completed = _sum(latest_week_rows, "setup_completed")
    current_doctor_ready = _sum(latest_week_rows, "doctor_ready")
    current_searches = _sum(latest_week_rows, "searches")
    current_search_hits = _sum(latest_week_rows, "searches_with_results")
    current_positive = _sum(latest_week_rows, "positive_feedback")
    current_negative = _sum(latest_week_rows, "negative_feedback")

    setup_conversion = (
        current_setup_completed / current_install_started
        if current_install_started
        else 0.0
    )
    ready_rate = (
        current_doctor_ready / current_setup_completed
        if current_setup_completed
        else 0.0
    )
    search_success_rate = (
        current_search_hits / current_searches if current_searches else 0.0
    )
    feedback_total = current_positive + current_negative
    satisfaction_ratio = current_positive / feedback_total if feedback_total else 0.0

    agent_counter: Counter[str] = Counter()
    weak_counter: Counter[str] = Counter()
    for row in latest_week_rows:
        agent_counter.update(_parse_counter_json(row.get("agent_counts_json")))
        weak_counter.update(_parse_counter_json(row.get("weak_categories_json")))

    installs_by_day: dict[str, tuple[str, str]] = {}
    for row in daily_rows:
        install_id = str(row.get("install_id") or "")
        day = str(row.get("period_key") or "")
        if not install_id or not day:
            continue
        current = installs_by_day.get(install_id)
        if current is None:
            installs_by_day[install_id] = (day, day)
        else:
            installs_by_day[install_id] = (min(current[0], day), max(current[1], day))

    retention_d7 = 0
    retention_d30 = 0
    for first_day, last_day in installs_by_day.values():
        try:
            first = datetime.fromisoformat(first_day)
            last = datetime.fromisoformat(last_day)
        except ValueError:
            continue
        delta_days = (last.date() - first.date()).days
        if delta_days >= 7:
            retention_d7 += 1
        if delta_days >= 30:
            retention_d30 += 1

    failure_counter: Counter[str] = Counter()
    for row in event_rows:
        event_name = str(row.get("event_name") or "")
        if event_name not in {
            "setup_failed",
            "doctor_not_ready",
            "uninstall_requested",
        }:
            continue
        try:
            metadata = json.loads(row.get("metadata_json") or "{}")
        except json.JSONDecodeError:
            metadata = {}
        stage = str(metadata.get("stage") or metadata.get("status") or event_name)
        failure_counter[f"{event_name}:{stage}"] += 1

    versions: list[dict[str, Any]] = []
    for row in release_rows:
        installs = int(row.get("installs", 0) or 0)
        install_started = int(row.get("install_started", 0) or 0)
        setup_completed = int(row.get("setup_completed", 0) or 0)
        doctor_ready = int(row.get("doctor_ready", 0) or 0)
        searches = int(row.get("searches", 0) or 0)
        search_hits = int(row.get("searches_with_results", 0) or 0)
        positive = int(row.get("positive_feedback", 0) or 0)
        negative = int(row.get("negative_feedback", 0) or 0)
        versions.append(
            {
                "version": str(row.get("smartassist_version") or "?"),
                "installs": installs,
                "setup_conversion": setup_completed / install_started
                if install_started
                else 0.0,
                "ready_rate": doctor_ready / setup_completed
                if setup_completed
                else 0.0,
                "search_success_rate": search_hits / searches if searches else 0.0,
                "satisfaction_ratio": positive / (positive + negative)
                if (positive + negative)
                else 0.0,
                "uninstall_requested": int(row.get("uninstall_requested", 0) or 0),
                "latest_period": str(row.get("latest_period") or ""),
            }
        )

    versions.sort(key=lambda row: row["version"], reverse=True)
    return {
        "db_path": str(path),
        "generated_at": _now_iso(),
        "latest_week": latest_week or "n/a",
        "installs_total": len({row["install_id"] for row in weekly_rows}),
        "active_installs_latest_week": active_installs,
        "setup_conversion": setup_conversion,
        "ready_rate": ready_rate,
        "search_success_rate": search_success_rate,
        "satisfaction_ratio": satisfaction_ratio,
        "funnel": {
            "install_started": current_install_started,
            "setup_completed": current_setup_completed,
            "setup_failed": _sum(latest_week_rows, "setup_failed"),
            "doctor_ready": current_doctor_ready,
            "doctor_not_ready": _sum(latest_week_rows, "doctor_not_ready"),
            "seed_completed": _sum(latest_week_rows, "seed_completed"),
            "uninstall_requested": _sum(latest_week_rows, "uninstall_requested"),
        },
        "retention": {
            "d7": retention_d7,
            "d30": retention_d30,
        },
        "agent_activation": agent_counter.most_common(10),
        "weak_categories": weak_counter.most_common(10),
        "failure_clusters": failure_counter.most_common(10),
        "versions": versions,
        "raw_events": len(event_rows),
    }


def serve_collector(
    db_path: Path | str,
    *,
    host: str = DEFAULT_COLLECTOR_HOST,
    port: int = DEFAULT_COLLECTOR_PORT,
) -> int:
    aggregate_db = initialize_aggregate_store(db_path)

    class _Handler(BaseHTTPRequestHandler):
        def _write_json(self, status: int, payload: dict[str, Any]) -> None:
            body = json.dumps(payload).encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def do_GET(self) -> None:  # noqa: N802
            if self.path != "/health":
                self._write_json(404, {"error": "not_found"})
                return
            self._write_json(200, {"status": "ok", "db_path": str(aggregate_db)})

        def do_POST(self) -> None:  # noqa: N802
            if self.path != "/ingest":
                self._write_json(404, {"error": "not_found"})
                return
            try:
                length = int(self.headers.get("Content-Length", "0") or "0")
            except ValueError:
                self._write_json(400, {"error": "invalid_content_length"})
                return
            raw = self.rfile.read(length)
            try:
                payload = json.loads(raw.decode("utf-8"))
            except json.JSONDecodeError:
                self._write_json(400, {"error": "invalid_json"})
                return
            result = ingest_bundle(aggregate_db, payload)
            self._write_json(202, {"status": "accepted", **result})

        def log_message(self, format: str, *args: Any) -> None:  # noqa: A003
            return

    server = ThreadingHTTPServer((host, port), _Handler)
    print(f"Telemetry collector listening on http://{host}:{port}")
    print(f"Aggregate DB: {aggregate_db}")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
    return 0
