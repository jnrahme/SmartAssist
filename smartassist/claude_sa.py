#!/usr/bin/env python3
"""
claude-sa -- Launch Claude Code with SmartAssist RAG monitor in a tmux split.

Left pane:  Claude Code (interactive, gets focus)
Right pane: Live RAG injection log (auto-scrolling)

Adapts layout to terminal size. Falls back gracefully without tmux.
"""

import os
import shutil
import subprocess
import sys
from pathlib import Path

SESSION_NAME = "claude-sa"
MIN_COLS_FOR_SPLIT = 100
MONITOR_MIN_COLS = 40
MONITOR_MAX_COLS = 80


def find_data_dir() -> Path | None:
    d = Path.cwd()
    while d != d.parent:
        candidate = d / ".claude" / "smartassist" / "data"
        if candidate.is_dir():
            return candidate
        d = d.parent
    return None


def _get_terminal_size() -> tuple[int, int]:
    try:
        cols, rows = os.get_terminal_size()
    except OSError:
        cols, rows = 200, 50
    return cols, rows


def _compute_monitor_cols(term_cols: int) -> int:
    target = int(term_cols * 0.30)
    return max(MONITOR_MIN_COLS, min(MONITOR_MAX_COLS, target))


def _tmux(*args):
    subprocess.run(["tmux", *args], check=True)


def _setup_session(log_file: Path, split_flag: str, split_size: str) -> None:
    """Create tmux session with Claude left, monitor right (or bottom).

    Uses relative pane targeting — works regardless of base-index setting.
    After new-session, active pane = left/top (Claude).
    After split-window, active pane = right/bottom (monitor).
    """
    cwd = os.getcwd()

    subprocess.run(
        ["tmux", "kill-session", "-t", SESSION_NAME],
        capture_output=True,
    )

    _tmux("new-session", "-d", "-s", SESSION_NAME)
    _tmux("select-pane", "-T", "Claude Code")

    _tmux("split-window", split_flag, "-t", SESSION_NAME, "-l", split_size)
    _tmux("select-pane", "-T", "RAG Monitor")

    monitor_cmd = f"export TERM=xterm-256color && clear && tail -f {log_file}"
    _tmux("send-keys", monitor_cmd, "Enter")

    back_flag = "-L" if split_flag == "-h" else "-U"
    _tmux("select-pane", back_flag)
    _tmux("send-keys", f"cd {cwd} && claude", "Enter")


def _launch_tmux(log_file: Path) -> int:
    cols, _ = _get_terminal_size()
    monitor_cols = _compute_monitor_cols(cols)
    _setup_session(log_file, "-h", str(monitor_cols))
    os.execvp("tmux", ["tmux", "attach-session", "-t", SESSION_NAME])
    return 0


def _launch_stacked(log_file: Path) -> int:
    _setup_session(log_file, "-v", "30%")
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
    do script "clear && tail -f {log_file}" in front window
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

    if not shutil.which("tmux"):
        return _launch_fallback(log_file)

    cols, _ = _get_terminal_size()

    if cols >= MIN_COLS_FOR_SPLIT:
        return _launch_tmux(log_file)
    else:
        return _launch_stacked(log_file)


if __name__ == "__main__":
    sys.exit(main())
