#!/bin/bash
# show-lessons.sh — Wrapper for smartassist-show-lessons hook.
# Exits gracefully if package is missing.

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "$SCRIPT_DIR/check-deps.sh"

resolve_command "smartassist-show-lessons"

if [ "$SMARTASSIST_AVAILABLE" -eq 1 ]; then
    $SMARTASSIST_CMD "$@"
fi
