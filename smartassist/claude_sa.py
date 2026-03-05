#!/usr/bin/env python3
"""
claude-sa -- Launch Claude Code with SmartAssist RAG monitor side-by-side.

Layout: Claude Code (left/top) + live RAG monitor (right/bottom).
Uses tmux when available, falls back to AppleScript on macOS.
"""

import os
import shutil
import subprocess
import sys
from pathlib import Path

SESSION_NAME = "claude-sa"
MIN_COLS_FOR_SIDE_BY_SIDE = 100
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


def _has_tty() -> bool:
    try:
        return os.isatty(sys.stdin.fileno())
    except (OSError, ValueError):
        return False


def _inside_tmux() -> bool:
    return "TMUX" in os.environ


def _get_terminal_cols() -> int:
    try:
        return os.get_terminal_size().columns
    except OSError:
        return 200


def _tmux_run(*args) -> int:
    result = subprocess.run(["tmux", *args], capture_output=True)
    return result.returncode


def _tmux(*args):
    subprocess.run(["tmux", *args], check=True)


def _build_tmux_session(log_file: Path, split_flag: str, split_size: str):
    cwd = os.getcwd()

    _tmux_run("kill-session", "-t", SESSION_NAME)

    _tmux("new-session", "-d", "-s", SESSION_NAME)
    _tmux("select-pane", "-T", "Claude Code")

    _tmux("split-window", split_flag, "-t", SESSION_NAME, "-l", split_size)
    _tmux("select-pane", "-T", "RAG Monitor")
    _tmux(
        "send-keys",
        f"export TERM=xterm-256color && clear && tail -f {log_file}",
        "Enter",
    )

    back = "-L" if split_flag == "-h" else "-U"
    _tmux("select-pane", back)
    _tmux("send-keys", f"cd {cwd} && claude", "Enter")


def _compute_monitor_cols(term_cols: int) -> int:
    target = int(term_cols * 0.30)
    return max(MONITOR_MIN_COLS, min(MONITOR_MAX_COLS, target))


def _attach_tmux() -> int:
    if _inside_tmux():
        _tmux("switch-client", "-t", SESSION_NAME)
    else:
        os.execvp("tmux", ["tmux", "attach-session", "-t", SESSION_NAME])
    return 0


def _launch_via_macos_terminal(log_file: Path) -> int:
    cwd = os.getcwd()
    tmux_bin = shutil.which("tmux")

    if tmux_bin:
        cols = _get_terminal_cols()
        split = "-h" if cols >= MIN_COLS_FOR_SIDE_BY_SIDE else "-v"
        size = str(_compute_monitor_cols(cols)) if split == "-h" else "30%"
        _build_tmux_session(log_file, split, size)

        applescript = f'''
tell application "Terminal"
    activate
    do script "{tmux_bin} attach-session -t {SESSION_NAME}" in front window
end tell
'''
    else:
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
    return 0


def main() -> int:
    data_dir = find_data_dir()
    if data_dir is None:
        print("No .claude/smartassist/ found. Run 'smartassist init' first.")
        return 1

    log_file = data_dir / "rag_live.log"
    log_file.touch()
    log_file.write_text("")

    has_tmux = shutil.which("tmux") is not None
    has_tty = _has_tty()

    if has_tmux and has_tty and not _inside_tmux():
        cols = _get_terminal_cols()
        split = "-h" if cols >= MIN_COLS_FOR_SIDE_BY_SIDE else "-v"
        size = str(_compute_monitor_cols(cols)) if split == "-h" else "30%"
        _build_tmux_session(log_file, split, size)
        return _attach_tmux()

    if has_tmux and _inside_tmux():
        cols = _get_terminal_cols()
        split = "-h" if cols >= MIN_COLS_FOR_SIDE_BY_SIDE else "-v"
        size = str(_compute_monitor_cols(cols)) if split == "-h" else "30%"
        _build_tmux_session(log_file, split, size)
        return _attach_tmux()

    if sys.platform == "darwin":
        return _launch_via_macos_terminal(log_file)

    print("Start these in two terminals:")
    print("  Terminal 1: claude")
    print(f"  Terminal 2: tail -f {log_file}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
