#!/usr/bin/env python3

from __future__ import annotations

import argparse
import hashlib
import json
import os
import signal
import subprocess
import time
from datetime import datetime
from pathlib import Path

from smartassist.config import atomic_write_json, get_project_root, get_storage_path

SYNC_STATE_FILE = "codex_sync_state.json"
LEGACY_SYNC_PID_FILE = "codex_sync.pid"
RUNTIME_SYNC_DIR = "smartassist/sync-state"
DEFAULT_POLL_INTERVAL = 1.0
RECENT_SESSION_LIMIT = 8
INITIAL_BACKFILL_WINDOW_SECS = 2 * 60 * 60


def get_codex_home() -> Path:
    override = os.environ.get("SMARTASSIST_CODEX_HOME") or os.environ.get("CODEX_HOME")
    if override:
        return Path(override).expanduser()
    return Path.home() / ".codex"


def _project_root_from_storage(storage_path: Path) -> Path:
    return Path(storage_path).resolve().parent.parent.parent


def _runtime_project_key(project_root: Path) -> str:
    resolved = project_root.resolve()
    slug = (
        "".join(ch.lower() if ch.isalnum() else "-" for ch in resolved.name).strip("-")
        or "project"
    )
    digest = hashlib.sha1(str(resolved).encode("utf-8")).hexdigest()[:12]
    return f"{slug}-{digest}"


def _runtime_sync_dir(codex_home: Path | None = None) -> Path:
    return (codex_home or get_codex_home()) / RUNTIME_SYNC_DIR


def _legacy_sync_state_path(storage_path: Path | None = None) -> Path:
    storage = storage_path or get_storage_path()
    return storage / SYNC_STATE_FILE


def _legacy_sync_pid_path(storage_path: Path | None = None) -> Path:
    storage = storage_path or get_storage_path()
    return storage / LEGACY_SYNC_PID_FILE


def get_sync_state_path(
    storage_path: Path | None = None,
    *,
    project_root: Path | None = None,
    codex_home: Path | None = None,
) -> Path:
    if project_root is None:
        project_root = (
            _project_root_from_storage(storage_path)
            if storage_path is not None
            else get_project_root()
        )
    return _runtime_sync_dir(codex_home) / f"{_runtime_project_key(project_root)}.json"


def load_sync_state(
    storage_path: Path | None = None,
    *,
    project_root: Path | None = None,
    codex_home: Path | None = None,
) -> dict:
    state_path = get_sync_state_path(
        storage_path,
        project_root=project_root,
        codex_home=codex_home,
    )
    default = {"version": 1, "files": {}}

    candidates = [state_path]
    if storage_path is not None:
        candidates.append(_legacy_sync_state_path(storage_path))

    raw = None
    for candidate in candidates:
        if not candidate.exists():
            continue
        try:
            raw = json.loads(candidate.read_text())
            break
        except (OSError, json.JSONDecodeError):
            continue

    if raw is None:
        return default

    files = raw.get("files")
    if not isinstance(files, dict):
        return default

    normalized = {}
    for file_path, entry in files.items():
        if not isinstance(file_path, str) or not isinstance(entry, dict):
            continue
        try:
            offset = max(0, int(entry.get("offset", 0)))
        except (TypeError, ValueError):
            offset = 0
        normalized[file_path] = {
            "offset": offset,
            "session_id": str(entry.get("session_id") or ""),
            "cwd": str(entry.get("cwd") or ""),
            "started_logged": bool(entry.get("started_logged", False)),
        }

    return {"version": 1, "files": normalized}


def save_sync_state(
    state: dict,
    storage_path: Path | None = None,
    *,
    project_root: Path | None = None,
    codex_home: Path | None = None,
) -> None:
    atomic_write_json(
        get_sync_state_path(
            storage_path,
            project_root=project_root,
            codex_home=codex_home,
        ),
        state,
    )
    if storage_path is not None:
        _legacy_sync_state_path(storage_path).unlink(missing_ok=True)


def clear_sync_state(
    storage_path: Path | None = None,
    *,
    project_root: Path | None = None,
    codex_home: Path | None = None,
) -> None:
    get_sync_state_path(
        storage_path,
        project_root=project_root,
        codex_home=codex_home,
    ).unlink(missing_ok=True)
    if storage_path is not None:
        _legacy_sync_state_path(storage_path).unlink(missing_ok=True)


def _state_snapshot(state: dict) -> str:
    return json.dumps(state, sort_keys=True)


def _read_pid(pid_path: Path) -> int | None:
    try:
        return int(pid_path.read_text().strip())
    except (OSError, ValueError):
        return None


def _read_process_command(pid: int) -> str:
    result = subprocess.run(
        ["ps", "-p", str(pid), "-o", "command="],
        capture_output=True,
        text=True,
        check=False,
    )
    if result.returncode != 0:
        return ""
    return result.stdout.strip()


def _terminate_process(pid: int) -> None:
    try:
        os.kill(pid, signal.SIGTERM)
    except OSError:
        return

    deadline = time.time() + 1.0
    while time.time() < deadline:
        if not _read_process_command(pid):
            return
        time.sleep(0.05)

    try:
        os.kill(pid, signal.SIGKILL)
    except OSError:
        pass


def reap_legacy_project_watcher(storage_path: Path | None = None) -> bool:
    pid_path = _legacy_sync_pid_path(storage_path)
    if not pid_path.exists():
        return False

    pid = _read_pid(pid_path)
    if pid is not None:
        command = _read_process_command(pid)
        if "smartassist.codex_activity" in command and "--watch" in command:
            _terminate_process(pid)

    pid_path.unlink(missing_ok=True)
    return True


def _parse_timestamp(raw: str | None) -> tuple[str, str]:
    if not raw:
        now = datetime.now()
        return now.isoformat(), now.strftime("%H:%M:%S")

    text = str(raw)
    try:
        dt = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        now = datetime.now()
        return now.isoformat(), now.strftime("%H:%M:%S")

    local_dt = dt.astimezone()
    return local_dt.isoformat(), local_dt.strftime("%H:%M:%S")


def _preview_text(text: str, limit: int = 100) -> str:
    collapsed = " ".join(text.split())
    if len(collapsed) <= limit:
        return collapsed
    return collapsed[: limit - 3] + "..."


def _append_usage_event(
    storage_path: Path,
    *,
    tool: str,
    query: str,
    timestamp: str,
    source: str,
    session_id: str,
    cwd: str,
) -> None:
    try:
        entry = {
            "timestamp": timestamp,
            "tool": tool,
            "query": query[:200],
            "results_count": 0,
            "latency_ms": 0,
            "source": source,
            "session_id": session_id,
            "cwd": cwd,
        }
        with open(storage_path / "usage_log.jsonl", "a", encoding="utf-8") as handle:
            handle.write(json.dumps(entry) + "\n")
    except OSError:
        pass


def _write_prompt_to_live_log(
    storage_path: Path,
    *,
    agent: str,
    timestamp: str,
    prompt: str,
    session_id: str,
    cwd: str,
) -> None:
    preview = _preview_text(prompt, limit=90)
    session_label = session_id[:8] if session_id else "unknown"
    workspace = Path(cwd).name or cwd or "unknown"
    lines = [
        "",
        f"\033[90m{'=' * 60}\033[0m",
        f"\033[90m  {timestamp}  |  {agent.title()} Prompt\033[0m",
        "\033[36m\033[1m  PROMPT\033[0m",
        f'  \033[97m[{agent}] "{preview}"\033[0m',
        f"  \033[90mSession: {session_label} | Workspace: {workspace}\033[0m",
        "",
    ]
    try:
        with open(storage_path / "rag_live.log", "a", encoding="utf-8") as handle:
            handle.write("\n".join(lines))
    except OSError:
        pass


def _write_session_start_to_live_log(
    storage_path: Path,
    *,
    agent: str,
    timestamp: str,
    session_id: str,
    cwd: str,
) -> None:
    session_label = session_id[:8] if session_id else "unknown"
    workspace = _preview_text(cwd, limit=70)
    lines = [
        "",
        f"\033[90m{'=' * 60}\033[0m",
        f"\033[90m  {timestamp}  |  {agent.title()} Session\033[0m",
        f"\033[35m\033[1m  {agent.upper()} SESSION START\033[0m",
        f"  \033[90mSession: {session_label}\033[0m",
        f"  \033[90mWorkspace: {workspace}\033[0m",
        "",
    ]
    try:
        with open(storage_path / "rag_live.log", "a", encoding="utf-8") as handle:
            handle.write("\n".join(lines))
    except OSError:
        pass


def _read_session_meta(path: Path) -> dict:
    try:
        with open(path, "r", encoding="utf-8") as handle:
            for _ in range(10):
                line = handle.readline()
                if not line:
                    break
                try:
                    entry = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if entry.get("type") != "session_meta":
                    continue
                payload = entry.get("payload") or {}
                return {
                    "session_id": str(payload.get("id") or ""),
                    "cwd": str(payload.get("cwd") or ""),
                    "timestamp": str(
                        entry.get("timestamp") or payload.get("timestamp") or ""
                    ),
                }
    except OSError:
        return {}
    return {}


def _belongs_to_project(cwd: str, project_root: Path) -> bool:
    if not cwd:
        return False

    try:
        session_root = Path(cwd).expanduser().resolve()
        project = project_root.resolve()
    except OSError:
        return False

    try:
        session_root.relative_to(project)
        return True
    except ValueError:
        return False


def _candidate_session_files(
    *,
    codex_home: Path,
    project_root: Path,
    state: dict,
) -> list[Path]:
    tracked = [Path(path) for path in state.get("files", {})]
    candidates = {path for path in tracked if path.exists()}

    sessions_dir = codex_home / "sessions"
    if not sessions_dir.exists():
        return sorted(candidates)

    files = sorted(
        sessions_dir.rglob("rollout-*.jsonl"),
        key=lambda path: path.stat().st_mtime,
        reverse=True,
    )

    for path in files[:RECENT_SESSION_LIMIT]:
        if path in candidates:
            continue
        meta = _read_session_meta(path)
        if _belongs_to_project(meta.get("cwd", ""), project_root):
            candidates.add(path)

    return sorted(candidates)


def sync_codex_activity(
    *,
    storage_path: Path | None = None,
    codex_home: Path | None = None,
    project_root: Path | None = None,
) -> dict:
    storage = storage_path or get_storage_path()
    codex_dir = codex_home or get_codex_home()
    project = project_root or get_project_root()
    reap_legacy_project_watcher(storage)
    state_path = get_sync_state_path(
        storage,
        project_root=project,
        codex_home=codex_dir,
    )
    legacy_state_path = _legacy_sync_state_path(storage)
    legacy_state_exists = legacy_state_path.exists()
    state = load_sync_state(
        storage,
        project_root=project,
        codex_home=codex_dir,
    )
    state_before = _state_snapshot(state)
    now = time.time()
    prompts_logged = 0
    sessions_logged = 0

    files = state.setdefault("files", {})
    next_known = {
        str(path)
        for path in _candidate_session_files(
            codex_home=codex_dir,
            project_root=project,
            state=state,
        )
    }

    for known_path in list(files):
        if known_path not in next_known and not Path(known_path).exists():
            files.pop(known_path, None)

    for file_path in next_known:
        path = Path(file_path)
        if not path.exists():
            continue

        entry = files.get(file_path)
        if entry is None:
            initial_offset = 0
            meta = _read_session_meta(path)
            try:
                if now - path.stat().st_mtime > INITIAL_BACKFILL_WINDOW_SECS:
                    initial_offset = path.stat().st_size
            except OSError:
                initial_offset = 0
            entry = {
                "offset": initial_offset,
                "session_id": str(meta.get("session_id") or ""),
                "cwd": str(meta.get("cwd") or ""),
                "started_logged": False,
            }
            files[file_path] = entry
        elif not entry.get("session_id") or not entry.get("cwd"):
            meta = _read_session_meta(path)
            if meta.get("session_id"):
                entry["session_id"] = str(meta["session_id"])
            if meta.get("cwd"):
                entry["cwd"] = str(meta["cwd"])

        try:
            with open(path, "r", encoding="utf-8") as handle:
                handle.seek(int(entry.get("offset", 0)))
                while True:
                    line = handle.readline()
                    if not line:
                        entry["offset"] = handle.tell()
                        break

                    try:
                        event = json.loads(line)
                    except json.JSONDecodeError:
                        entry["offset"] = handle.tell()
                        continue

                    timestamp_iso, timestamp_hms = _parse_timestamp(
                        event.get("timestamp")
                    )

                    if event.get("type") == "session_meta":
                        payload = event.get("payload") or {}
                        session_id = str(
                            payload.get("id") or entry.get("session_id") or ""
                        )
                        cwd = str(payload.get("cwd") or entry.get("cwd") or "")
                        entry["session_id"] = session_id
                        entry["cwd"] = cwd
                        if (
                            session_id
                            and cwd
                            and _belongs_to_project(cwd, project)
                            and not entry.get("started_logged", False)
                        ):
                            _write_session_start_to_live_log(
                                storage,
                                agent="codex",
                                timestamp=timestamp_hms,
                                session_id=session_id,
                                cwd=cwd,
                            )
                            _append_usage_event(
                                storage,
                                tool="codex_session_start",
                                query=session_id,
                                timestamp=timestamp_iso,
                                source="codex",
                                session_id=session_id,
                                cwd=cwd,
                            )
                            entry["started_logged"] = True
                            sessions_logged += 1

                        entry["offset"] = handle.tell()
                        continue

                    cwd = str(entry.get("cwd") or "")
                    session_id = str(entry.get("session_id") or "")
                    if not session_id or not _belongs_to_project(cwd, project):
                        entry["offset"] = handle.tell()
                        continue

                    if event.get("type") != "event_msg":
                        entry["offset"] = handle.tell()
                        continue

                    payload = event.get("payload") or {}
                    if payload.get("type") != "user_message":
                        entry["offset"] = handle.tell()
                        continue

                    message = str(payload.get("message") or "").strip()
                    if not message or message.startswith("<turn_aborted>"):
                        entry["offset"] = handle.tell()
                        continue

                    _write_prompt_to_live_log(
                        storage,
                        agent="codex",
                        timestamp=timestamp_hms,
                        prompt=message,
                        session_id=session_id,
                        cwd=cwd,
                    )
                    _append_usage_event(
                        storage,
                        tool="codex_prompt",
                        query=message,
                        timestamp=timestamp_iso,
                        source="codex",
                        session_id=session_id,
                        cwd=cwd,
                    )
                    prompts_logged += 1
                    entry["offset"] = handle.tell()
        except OSError:
            continue

    state_after = _state_snapshot(state)
    if files:
        if (
            state_after != state_before
            or legacy_state_exists
            or not state_path.exists()
        ):
            save_sync_state(
                state,
                storage,
                project_root=project,
                codex_home=codex_dir,
            )
    else:
        clear_sync_state(
            storage,
            project_root=project,
            codex_home=codex_dir,
        )

    return {
        "prompts_logged": prompts_logged,
        "sessions_logged": sessions_logged,
        "tracked_files": len(files),
    }


def watch_codex_activity(
    *,
    poll_interval: float = DEFAULT_POLL_INTERVAL,
    storage_path: Path | None = None,
    codex_home: Path | None = None,
    project_root: Path | None = None,
) -> None:
    while True:
        sync_codex_activity(
            storage_path=storage_path,
            codex_home=codex_home,
            project_root=project_root,
        )
        time.sleep(max(0.2, poll_interval))


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Mirror Codex session activity into SmartAssist dashboard logs.",
    )
    parser.add_argument("--watch", action="store_true", help="Poll continuously")
    parser.add_argument(
        "--poll-interval",
        type=float,
        default=DEFAULT_POLL_INTERVAL,
        help="Polling interval in seconds when --watch is enabled",
    )
    args = parser.parse_args()

    if args.watch:
        watch_codex_activity(poll_interval=args.poll_interval)
        return 0

    result = sync_codex_activity()
    print(json.dumps(result))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
