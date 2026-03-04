#!/bin/bash
# prompt-inject.sh — Wrapper for smartassist-prompt-inject hook.
# Passes stdin through to the command, or passes through unchanged if unavailable.

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "$SCRIPT_DIR/check-deps.sh"

resolve_command "smartassist-prompt-inject"

if [ "$SMARTASSIST_AVAILABLE" -eq 1 ]; then
    $SMARTASSIST_CMD
else
    # Pass stdin through unchanged
    cat
fi
