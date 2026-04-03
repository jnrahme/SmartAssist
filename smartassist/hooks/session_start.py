#!/usr/bin/env python3
"""
Session Start Hook - Injects lessons learned at session start.
Called by Claude Code when starting a new session.

Optimized: uses only lightweight JSON + Thompson data.
Does NOT load embedding model or LanceDB (saves ~4 seconds).
"""

import sys

from smartassist.boundary_packs import ensure_boundary_pack, format_boundary_pack_for_session
from smartassist.config import get_storage_path


def format_lessons_for_session():
    """Generate session-start boundary context."""
    try:
        storage_path = get_storage_path()
        pack = ensure_boundary_pack(storage_path)
        return format_boundary_pack_for_session(pack)
    except Exception:
        return ""


def main():
    lessons = format_lessons_for_session()
    if lessons:
        print(lessons)


if __name__ == "__main__":
    main()
    sys.exit(0)
