"""Facts read from the project itself, used to keep stale lessons out of context.

A lesson that contradicts what the project declares predates a change: a
different package manager (e.g. "Run yarn test" in a pnpm repo), or a
"[TICKET-XXX] Description" commit format in a repo that enforces Conventional
Commits through commitlint. It is excluded from injection and surfaced for
review, never deleted.
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
_COMMITLINT_FILES = (
    "commitlint.config.js", "commitlint.config.cjs", "commitlint.config.mjs",
    "commitlint.config.ts", ".commitlintrc", ".commitlintrc.json",
    ".commitlintrc.yml", ".commitlintrc.yaml", ".commitlintrc.js",
)
# "never use npm", "don't run yarn", "instead of npm": a prohibition agrees with
# the project's package manager rather than contradicting it
_NEGATION_BEFORE = re.compile(
    r"(\bnever|\bdon'?t|\bdo not|\bnot|\bno|\bavoid|\bstop|\binstead of|\brather than|\bwithout)"
    r"(\s+\S+){0,3}\s*$"
)
_BRACKET_TICKET_FORMAT = re.compile(r"\[[a-z]+-x+\]")


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


def detect_commit_convention(project_root):
    """Return "conventional" when the project configures commitlint, else None.

    Without a declared convention the hook does not guess a commit format.
    """
    if project_root is None:
        return None
    root = Path(project_root)
    if any((root / name).exists() for name in _COMMITLINT_FILES):
        return "conventional"
    try:
        if "commitlint" in json.loads((root / "package.json").read_text()):
            return "conventional"
    except (OSError, ValueError):
        pass
    return None


def stale_reason(lesson_text, project_root):
    """Why a lesson contradicts the project, or None if it does not."""
    root = str(project_root) if project_root else None
    text = str(lesson_text).lower()
    if _BRACKET_TICKET_FORMAT.search(text) and _commit_convention(root) == "conventional":
        return "uses [TICKET-XXX] commit format but this project enforces Conventional Commits"
    manager = detect_package_manager(root)
    if manager is None:
        return None
    if _MENTIONS[manager].search(text):
        return None
    others = [
        m for m, pattern in _MENTIONS.items()
        if m != manager and _has_affirmative_mention(pattern, text)
    ]
    if others:
        return f"uses {', '.join(others)} but this project uses {manager}"
    return None


def _has_affirmative_mention(pattern, text):
    """True when some mention of the tool is not under a nearby negation."""
    return any(
        not _NEGATION_BEFORE.search(text[max(0, match.start() - 40):match.start()])
        for match in pattern.finditer(text)
    )


@lru_cache(maxsize=8)
def _commit_convention(project_root):
    return detect_commit_convention(project_root)
