#!/usr/bin/env python3
"""
claude-sa — Launch Claude Code with SmartAssist RAG monitor side-by-side.

Opens a split terminal: left = Claude Code, right = live RAG injection log.
On macOS, uses osascript to open a new Terminal tab.
On other platforms, prints instructions for manual setup.
"""

import os
import subprocess
import sys
from pathlib import Path


def find_data_dir() -> Path | None:
    """Walk up from cwd looking for .claude/smartassist/data/."""
    d = Path.cwd()
    while d != d.parent:
        candidate = d / ".claude" / "smartassist" / "data"
        if candidate.is_dir():
            return candidate
        d = d.parent
    return None


def main() -> int:
    data_dir = find_data_dir()
    if data_dir is None:
        print("No .claude/smartassist/ found. Run 'smartassist init' first.")
        return 1

    log_file = data_dir / "rag_live.log"
    log_file.touch()
    log_file.write_text("")  # Clear previous session log

    cwd = os.getcwd()

    if sys.platform == "darwin":
        applescript = f'''
tell application "Terminal"
    activate
    do script "cd {cwd} && clear && claude" in front window
    tell application "System Events" to keystroke "t" using command down
    delay 0.3
    do script "clear && echo 'SmartAssist RAG Monitor' && echo '━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━' && echo '' && tail -f {log_file}" in front window
    tell application "System Events" to keystroke "[" using command down
end tell
'''
        subprocess.run(["osascript", "-e", applescript], check=False)
    else:
        print("Start these in two terminals:")
        print(f"  Terminal 1: claude")
        print(f"  Terminal 2: tail -f {log_file}")

    return 0


if __name__ == "__main__":
    sys.exit(main())
