"""Facts read from the project itself, used to keep stale lessons out of context.

A lesson that tells the agent to use a different package manager than the one
the project declares (e.g. "Run yarn test" in a pnpm repo) predates a migration.
It is excluded from injection and surfaced for review, never deleted.
"""

import json
import re
from functools import lru_cache
from pathlib import Path

_LOCKFILES = (
    ("pnpm-lock.yaml", "pnpm"),
    ("yarn.lock", "yarn"),
    ("package-lock.json", "npm"),
    ("bun.lockb", "bun"),
)
_MENTIONS = {
    "pnpm": re.compile(r"\bpnpm\b|pnpm-lock\.yaml"),
    "yarn": re.compile(r"\byarn\b|yarn\.lock"),
    "npm": re.compile(r"\bnpm\b|\bnpx\b|package-lock\.json"),
    "bun": re.compile(r"\bbun\b|\bbunx\b|bun\.lockb"),
}


@lru_cache(maxsize=8)
def detect_package_manager(project_root):
    """Return "pnpm" / "yarn" / "npm" / "bun" for a JS project, else None."""
    if project_root is None:
        return None
    root = Path(project_root)
    try:
        declared = json.loads((root / "package.json").read_text()).get("packageManager", "")
    except (OSError, ValueError, AttributeError):
        declared = ""
    name = str(declared).split("@", 1)[0]
    if name in _MENTIONS:
        return name
    for lockfile, manager in _LOCKFILES:
        if (root / lockfile).exists():
            return manager
    return None


def stale_reason(lesson_text, project_root):
    """Why a lesson contradicts the project, or None if it does not."""
    manager = detect_package_manager(str(project_root) if project_root else None)
    if manager is None:
        return None
    text = str(lesson_text).lower()
    if _MENTIONS[manager].search(text):
        return None
    others = [m for m, pattern in _MENTIONS.items() if m != manager and pattern.search(text)]
    if others:
        return f"uses {', '.join(others)} but this project uses {manager}"
    return None
