#!/usr/bin/env python3
"""
Session Start Hook - Injects lessons learned at session start.
Called by Claude Code when starting a new session.

Optimized: uses only lightweight JSON + Thompson data.
Does NOT load embedding model or LanceDB (saves ~4 seconds).
"""

import sys

from smartassist.agent_protocol import render_feedback_protocol
from smartassist.boundary_packs import (
    ensure_boundary_pack,
    format_boundary_pack_for_session,
)
from smartassist.config import get_storage_path


def format_lessons_for_session():
    protocol = (
        "\n" + "=" * 60 + "\n"
        "SMARTASSIST FEEDBACK PROTOCOL\n"
        "=" * 60 + "\n"
        "Claude hooks already process feedback automatically. When feedback handling is unavailable, "
        "use `apply_feedback_protocol` with the user's correction or confirmed pattern.\n\n"
        f"{render_feedback_protocol()}\n"
    )
    try:
        storage_path = get_storage_path()
        pack = ensure_boundary_pack(storage_path)
        boundary_text = format_boundary_pack_for_session(pack)
        if boundary_text:
            return boundary_text.rstrip() + "\n" + protocol
        return protocol
    except Exception:
        return protocol


def main():
    lessons = format_lessons_for_session()
    if lessons:
        print(lessons)


if __name__ == "__main__":
    main()
    sys.exit(0)
