from __future__ import annotations

import contextlib
import fcntl
import json
import os
import signal
import subprocess
import sys
import time
import webbrowser
from pathlib import Path
from urllib import error, request

from smartassist.config import atomic_write_json, get_storage_path

DEFAULT_DASHBOARD_PORT = 3000
DASHBOARD_PORT_ATTEMPTS = 10
DASHBOARD_STARTUP_TIMEOUT = 10.0
DASHBOARD_HEALTH_TIMEOUT = 0.5


def dashboard_url(port: int) -> str:
    return f"http://localhost:{int(port)}"


def get_dashboard_state_path() -> Path:
    return get_storage_path() / "dashboard_state.json"


def get_dashboard_pid_path() -> Path:
    return get_storage_path() / "dashboard.pid"


def get_dashboard_lock_path() -> Path:
    return get_storage_path() / "dashboard.lock"


@contextlib.contextmanager
def dashboard_lock():
    lock_path = get_dashboard_lock_path()
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    with open(lock_path, "w") as lock_file:
        fcntl.flock(lock_file, fcntl.LOCK_EX)
        try:
            yield
        finally:
            fcntl.flock(lock_file, fcntl.LOCK_UN)


def read_dashboard_state() -> dict | None:
    state_path = get_dashboard_state_path()
    if not state_path.exists():
        return None

    try:
        state = json.loads(state_path.read_text())
    except (OSError, json.JSONDecodeError):
        return None

    if not isinstance(state, dict):
        return None

    pid = state.get("pid")
    port = state.get("port")
    try:
        if pid is not None:
            pid = int(pid)
        if port is not None:
            port = int(port)
    except (TypeError, ValueError):
        return None

    if pid is None:
        return None

    state = dict(state)
    state["pid"] = pid
    if port is not None:
        state["port"] = port
    state["url"] = str(
        state.get("url") or dashboard_url(port or DEFAULT_DASHBOARD_PORT)
    )
    state["status"] = str(state.get("status") or "running")
    return state


def write_dashboard_state(state: dict) -> None:
    normalized = dict(state)
    normalized["pid"] = int(normalized["pid"])
    normalized["port"] = int(normalized["port"])
    normalized["url"] = str(normalized.get("url") or dashboard_url(normalized["port"]))
    normalized["status"] = str(normalized.get("status") or "running")
    atomic_write_json(get_dashboard_state_path(), normalized)
    get_dashboard_pid_path().write_text(str(normalized["pid"]))


def clear_dashboard_state() -> None:
    get_dashboard_state_path().unlink(missing_ok=True)
    get_dashboard_pid_path().unlink(missing_ok=True)


def is_pid_running(pid: int) -> bool:
    try:
        os.kill(int(pid), 0)
    except (ProcessLookupError, ValueError, PermissionError, OSError):
        return False
    return True


def fetch_dashboard_status(
    url: str, timeout: float = DASHBOARD_HEALTH_TIMEOUT
) -> dict | None:
    status_url = f"{url.rstrip('/')}/api/status"
    req = request.Request(status_url, headers={"Accept": "application/json"})
    try:
        with request.urlopen(req, timeout=timeout) as response:
            status_code = getattr(response, "status", response.getcode())
            if status_code != 200:
                return None
            payload = json.loads(response.read().decode("utf-8"))
    except (
        OSError,
        TimeoutError,
        ValueError,
        error.URLError,
        error.HTTPError,
        json.JSONDecodeError,
    ):
        return None

    if not isinstance(payload, dict):
        return None
    return payload


def _discover_dashboard_from_pid(pid: int) -> dict | None:
    for port in range(
        DEFAULT_DASHBOARD_PORT, DEFAULT_DASHBOARD_PORT + DASHBOARD_PORT_ATTEMPTS
    ):
        url = dashboard_url(port)
        status = fetch_dashboard_status(url)
        if not status:
            continue
        status_pid_raw = status.get("pid")
        if status_pid_raw is None:
            continue
        try:
            status_pid = int(status_pid_raw)
        except (TypeError, ValueError):
            continue
        if status_pid != pid:
            continue

        discovered = {
            "pid": pid,
            "port": int(status.get("port", port)),
            "url": str(status.get("url") or url),
            "status": "running",
            "started_at": status.get("started_at"),
        }
        write_dashboard_state(discovered)
        return discovered | {"active_clients": int(status.get("active_clients", 0))}
    return None


def get_running_dashboard() -> dict | None:
    state = read_dashboard_state()
    if state is None:
        pid_path = get_dashboard_pid_path()
        if not pid_path.exists():
            return None
        try:
            legacy_pid = int(pid_path.read_text().strip())
        except (OSError, ValueError):
            return None
        if not is_pid_running(legacy_pid):
            clear_dashboard_state()
            return None
        return _discover_dashboard_from_pid(legacy_pid)

    if not is_pid_running(state["pid"]):
        clear_dashboard_state()
        return None

    status = fetch_dashboard_status(state["url"])
    if not status:
        return None

    status_pid_raw = status.get("pid")
    if status_pid_raw is None:
        return None
    try:
        status_pid = int(status_pid_raw)
    except (TypeError, ValueError):
        return None
    if status_pid != state["pid"]:
        return None

    state = dict(state)
    state["port"] = int(status.get("port", state["port"]))
    state["url"] = str(status.get("url") or state["url"])
    state["active_clients"] = int(status.get("active_clients", 0))
    state["status"] = str(status.get("status") or state.get("status") or "running")
    return state


def ensure_dashboard_running(
    preferred_port: int = DEFAULT_DASHBOARD_PORT,
    open_browser: bool = True,
    startup_timeout: float = DASHBOARD_STARTUP_TIMEOUT,
) -> dict | None:
    dashboard = get_running_dashboard()
    if dashboard is not None:
        if open_browser:
            webbrowser.open(dashboard["url"])
        return dashboard

    with dashboard_lock():
        dashboard = get_running_dashboard()
        if dashboard is not None:
            if open_browser:
                webbrowser.open(dashboard["url"])
            return dashboard

        pending = read_dashboard_state()
        if pending is None or not is_pid_running(pending["pid"]):
            proc = subprocess.Popen(
                [
                    sys.executable,
                    "-m",
                    "smartassist.tools.generate_dashboard",
                    "--serve",
                    "--port",
                    str(preferred_port),
                    "--no-browser",
                ],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                start_new_session=True,
            )
            write_dashboard_state(
                {
                    "pid": proc.pid,
                    "port": preferred_port,
                    "url": dashboard_url(preferred_port),
                    "status": "starting",
                    "started_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
                }
            )

    deadline = time.time() + startup_timeout
    while time.time() < deadline:
        dashboard = get_running_dashboard()
        if dashboard is not None:
            if open_browser:
                webbrowser.open(dashboard["url"])
            return dashboard
        time.sleep(0.1)

    return None


def stop_dashboard() -> tuple[bool, str]:
    state = read_dashboard_state()
    pid = None
    if state is not None:
        pid = state["pid"]
    else:
        pid_path = get_dashboard_pid_path()
        if pid_path.exists():
            try:
                pid = int(pid_path.read_text().strip())
            except (OSError, ValueError):
                pid = None

    if pid is None:
        clear_dashboard_state()
        return False, "No dashboard running."

    if not is_pid_running(pid):
        clear_dashboard_state()
        return False, "Dashboard was not running."

    os.kill(pid, signal.SIGTERM)

    deadline = time.time() + 5
    while time.time() < deadline:
        if not is_pid_running(pid):
            break
        time.sleep(0.1)

    clear_dashboard_state()
    return True, "Dashboard stopped."
