#!/usr/bin/env python3
"""
claude-sa -- Launch Claude Code with SmartAssist RAG monitor in a tmux split.

Left pane:  Claude Code (interactive)
Right pane: Live RAG injection log (auto-scrolling)

Falls back to separate terminals if tmux is unavailable.
"""

import os
import shutil
import subprocess
import sys
from pathlib import Path

SESSION_NAME = "claude-sa"
MONITOR_WIDTH_PCT = 35


def find_data_dir() -> Path | None:
    d = Path.cwd()
    while d != d.parent:
        candidate = d / ".claude" / "smartassist" / "data"
        if candidate.is_dir():
            return candidate
        d = d.parent
    return None


def _kill_existing_session():
    subprocess.run(
        ["tmux", "kill-session", "-t", SESSION_NAME],
        capture_output=True,
    )


def _launch_tmux(log_file: Path) -> int:
    cwd = os.getcwd()
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
            f"cd {cwd} && claude",
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
            f"{MONITOR_WIDTH_PCT}%",
        ],
        check=True,
    )

    monitor_cmd = (
        f"clear && "
        f"printf '\\033[1;36m SmartAssist RAG Monitor \\033[0m\\n' && "
        f"printf '\\033[90m━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\\033[0m\\n\\n' && "
        f"tail -f {log_file}"
    )
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
        applescript = f"""
tell application "Terminal"
    activate
    do script "cd {cwd} && clear && claude" in front window
    tell application "System Events" to keystroke "t" using command down
    delay 0.3
    do script "clear && echo 'SmartAssist RAG Monitor' && echo '━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━' && echo '' && tail -f {log_file}" in front window
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
        print("No .claude/smartassist/ found. Run 'smartassist init' first.")
        return 1

    log_file = data_dir / "rag_live.log"
    log_file.touch()
    log_file.write_text("")

    if shutil.which("tmux"):
        return _launch_tmux(log_file)
    else:
        return _launch_fallback(log_file)


if __name__ == "__main__":
    sys.exit(main())
