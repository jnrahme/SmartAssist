#!/usr/bin/env python3
"""
codex-sa -- Launch Codex with SmartAssist dashboard and live log tail.

Left pane:  Codex (interactive)
Right pane: Live SmartAssist activity log (auto-scrolling)

Also starts a lightweight bridge that mirrors Codex session activity from
~/.codex/sessions into SmartAssist's dashboard logs.
"""

import os
import shlex
import shutil
import subprocess
import sys
from pathlib import Path

from smartassist.dashboard_runtime import ensure_dashboard_running

SESSION_NAME = "codex-sa"
MONITOR_COLS = 30


def find_data_dir() -> Path | None:
    current = Path.cwd()
    while current != current.parent:
        candidate = current / ".claude" / "smartassist" / "data"
        if candidate.is_dir():
            return candidate
        current = current.parent
    return None


def _auto_setup() -> bool:
    try:
        subprocess.run(
            ["smartassist", "init"],
            check=True,
            timeout=60,
        )
        subprocess.run(
            ["smartassist", "setup-agent", "codex"],
            check=False,
            timeout=60,
        )
        if find_data_dir() is None:
            return False
        subprocess.run(
            ["smartassist", "seed"],
            check=False,
            timeout=60,
        )
        return True
    except (
        subprocess.CalledProcessError,
        subprocess.TimeoutExpired,
        FileNotFoundError,
    ):
        return False


def _tmux_session_exists() -> bool:
    return (
        subprocess.run(
            ["tmux", "has-session", "-t", SESSION_NAME],
            capture_output=True,
        ).returncode
        == 0
    )


def _escape_applescript(text: str) -> str:
    return text.replace("\\", "\\\\").replace('"', '\\"')


def _sync_pid_path(data_dir: Path) -> Path:
    return data_dir / "codex_sync.pid"


def _pid_running(pid: int) -> bool:
    try:
        os.kill(int(pid), 0)
    except (ProcessLookupError, ValueError, PermissionError, OSError):
        return False
    return True


def _start_codex_sync(data_dir: Path) -> int | None:
    pid_path = _sync_pid_path(data_dir)
    if pid_path.exists():
        try:
            existing = int(pid_path.read_text().strip())
        except (OSError, ValueError):
            existing = None
        if existing and _pid_running(existing):
            return existing
        pid_path.unlink(missing_ok=True)

    try:
        proc = subprocess.Popen(
            [sys.executable, "-m", "smartassist.codex_activity", "--watch"],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            start_new_session=True,
        )
    except OSError:
        return None

    pid_path.write_text(str(proc.pid))
    return proc.pid


def _launch_tmux(log_file: Path) -> int:
    cwd = os.getcwd()
    quoted_cwd = shlex.quote(cwd)

    if _tmux_session_exists():
        os.execvp("tmux", ["tmux", "attach-session", "-t", SESSION_NAME])
        return 0

    subprocess.run(
        [
            "tmux",
            "new-session",
            "-d",
            "-s",
            SESSION_NAME,
            "-x",
            "200",
            "-y",
            "50",
        ],
        check=True,
    )

    subprocess.run(
        [
            "tmux",
            "send-keys",
            "-t",
            SESSION_NAME,
            f"cd {quoted_cwd} && codex --no-alt-screen",
            "Enter",
        ],
        check=True,
    )

    subprocess.run(
        [
            "tmux",
            "split-window",
            "-h",
            "-t",
            SESSION_NAME,
            "-l",
            str(MONITOR_COLS),
        ],
        check=True,
    )

    monitor_cmd = f"tail -f {shlex.quote(str(log_file))}"
    subprocess.run(
        [
            "tmux",
            "send-keys",
            "-t",
            SESSION_NAME,
            monitor_cmd,
            "Enter",
        ],
        check=True,
    )

    subprocess.run(
        ["tmux", "select-pane", "-t", SESSION_NAME, "-L"],
        check=True,
    )

    os.execvp("tmux", ["tmux", "attach-session", "-t", SESSION_NAME])
    return 0


def _launch_fallback(log_file: Path) -> int:
    if sys.platform == "darwin":
        cwd = os.getcwd()
        codex_cmd = _escape_applescript(
            f"cd {shlex.quote(cwd)} && clear && codex --no-alt-screen"
        )
        monitor_cmd = _escape_applescript(
            "clear && echo 'SmartAssist Codex Monitor' && "
            "echo '━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━' && echo '' && "
            f"tail -f {shlex.quote(str(log_file))}"
        )
        applescript = f"""
tell application \"Terminal\"
    activate
    do script \"{codex_cmd}\" in front window
    tell application \"System Events\" to keystroke \"t\" using command down
    delay 0.3
    do script \"{monitor_cmd}\" in front window
    tell application \"System Events\" to keystroke \"[\" using command down
end tell
"""
        subprocess.run(["osascript", "-e", applescript], check=False)
    else:
        print("Start these in two terminals:")
        print("  Terminal 1: codex --no-alt-screen")
        print(f"  Terminal 2: tail -f {log_file}")
    return 0


def _start_dashboard() -> str | None:
    try:
        dashboard = ensure_dashboard_running(open_browser=True)
    except Exception:
        return None

    if dashboard is None:
        return None
    return str(dashboard.get("url") or "") or None


def main() -> int:
    data_dir = find_data_dir()
    if data_dir is None:
        print("Running first-time setup...")
        if not _auto_setup():
            print(
                "Setup failed. Run 'smartassist init' and 'smartassist setup-agent codex'."
            )
            return 1
        data_dir = find_data_dir()
        if data_dir is None:
            print("Setup completed but no data directory found.")
            return 1

    log_file = data_dir / "rag_live.log"
    log_file.touch()
    _start_codex_sync(data_dir)

    dashboard_url = _start_dashboard() or "http://localhost:3000"
    print(f"Dashboard: {dashboard_url}")

    if shutil.which("tmux"):
        return _launch_tmux(log_file)
    return _launch_fallback(log_file)


if __name__ == "__main__":
    sys.exit(main())
