#!/bin/bash
# check-deps.sh — Shared dependency check sourced by all hook wrappers.
# Sets SMARTASSIST_CMD to the resolved command, or SMARTASSIST_AVAILABLE=0 if missing.

SMARTASSIST_AVAILABLE=0
SMARTASSIST_CMD=""

resolve_command() {
    local cmd_name="$1"

    # Fast path: command installed directly (pip/pipx)
    if command -v "$cmd_name" &>/dev/null; then
        SMARTASSIST_AVAILABLE=1
        SMARTASSIST_CMD="$cmd_name"
        return 0
    fi

    # Fallback: run via uv
    if command -v uv &>/dev/null; then
        SMARTASSIST_AVAILABLE=1
        SMARTASSIST_CMD="uv run --with smartassist $cmd_name"
        return 0
    fi

    # Not available
    SMARTASSIST_AVAILABLE=0
    return 1
}
