#!/bin/bash
# session-start.sh — Wrapper for smartassist-session-start hook.
# Prints install hint if package is missing.

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "$SCRIPT_DIR/check-deps.sh"

resolve_command "smartassist-session-start"

if [ "$SMARTASSIST_AVAILABLE" -eq 1 ]; then
    $SMARTASSIST_CMD "$@"
else
    echo "[SmartAssist] Package not installed. Run: pipx install smartassist" >&2
fi
