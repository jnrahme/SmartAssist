#!/usr/bin/env python3
"""
claude-sa -- Launch Claude Code with SmartAssist RAG monitor in a tmux split.

Left pane:  Claude Code (interactive)
Right pane: Live RAG injection log (auto-scrolling)

Falls back to separate terminals if tmux is unavailable.
"""

import os
import shlex
import shutil
import subprocess
import sys
from pathlib import Path

SESSION_NAME = "claude-sa"
MONITOR_COLS = 30


def find_data_dir() -> Path | None:
    d = Path.cwd()
    while d != d.parent:
        candidate = d / ".claude" / "smartassist" / "data"
        if candidate.is_dir():
            return candidate
        d = d.parent
    return None


def _auto_setup() -> bool:
    try:
        subprocess.run(
            ["smartassist", "setup"],
            check=True,
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


def _kill_existing_session():
    subprocess.run(
        ["tmux", "kill-session", "-t", SESSION_NAME],
        capture_output=True,
    )


def _escape_applescript(text: str) -> str:
    return text.replace("\\", "\\\\").replace('"', '\\"')


def _launch_tmux(log_file: Path) -> int:
    cwd = os.getcwd()
    quoted_cwd = shlex.quote(cwd)
    quoted_session = shlex.quote(SESSION_NAME)
    _kill_existing_session()

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
            f"cd {quoted_cwd} && claude; tmux kill-session -t {quoted_session}",
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

    monitor_cmd = f"smartassist-monitor {log_file}"
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
        claude_cmd = _escape_applescript(
            f"cd {shlex.quote(cwd)} && clear && claude"
        )
        monitor_cmd = _escape_applescript(
            "clear && echo 'SmartAssist RAG Monitor' && "
            "echo '━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━' && echo '' && "
            f"tail -f {shlex.quote(str(log_file))}"
        )
        applescript = f"""
tell application "Terminal"
    activate
    do script "{claude_cmd}" in front window
    tell application "System Events" to keystroke "t" using command down
    delay 0.3
    do script "{monitor_cmd}" in front window
    tell application "System Events" to keystroke "[" using command down
end tell
"""
        subprocess.run(["osascript", "-e", applescript], check=False)
    else:
        print("Start these in two terminals:")
        print(f"  Terminal 1: claude")
        print(f"  Terminal 2: tail -f {log_file}")
    return 0


def main() -> int:
    data_dir = find_data_dir()
    if data_dir is None:
        print("Running first-time setup...")
        if not _auto_setup():
            print("Setup failed. Run 'smartassist setup' manually.")
            return 1
        data_dir = find_data_dir()
        if data_dir is None:
            print("Setup completed but no data directory found.")
            return 1

    log_file = data_dir / "rag_live.log"
    log_file.touch()

    if shutil.which("tmux"):
        return _launch_tmux(log_file)
    else:
        return _launch_fallback(log_file)


if __name__ == "__main__":
    sys.exit(main())
