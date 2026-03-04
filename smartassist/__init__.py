"""SmartAssist — Portable RAG learning system for Claude Code."""

from importlib.metadata import version, PackageNotFoundError

try:
    __version__ = version("smartassist")
except PackageNotFoundError:
    __version__ = "1.0.0"
