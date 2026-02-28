"""
Centralized configuration for SmartAssist.

All path resolution and embedding model references live here.
Every module imports from this file — it is the architectural keystone.
"""

import os
from pathlib import Path

# BGE-M3: 8K context, 1024 dims, open-source, supports dense+sparse+multi-vector
EMBEDDING_MODEL = "BAAI/bge-m3"
EMBEDDING_DIM = 1024


def get_data_dir() -> Path:
    """Resolve the SmartAssist data directory.

    Resolution order:
    1. SMARTASSIST_DATA_DIR env var (set by MCP config or tests)
    2. Walk up from cwd to find .claude/smartassist/
    3. Raise RuntimeError with helpful message
    """
    # 1. Environment variable (highest priority — tests and explicit config)
    env_dir = os.environ.get("SMARTASSIST_DATA_DIR")
    if env_dir:
        p = Path(env_dir)
        if p.is_dir():
            return p
        # Allow env var to point to a dir that will be created
        return p

    # 2. Walk up from cwd to find .claude/smartassist/
    cwd = Path.cwd()
    for parent in [cwd, *cwd.parents]:
        candidate = parent / ".claude" / "smartassist"
        if candidate.is_dir():
            return candidate
        # Stop at filesystem root
        if parent == parent.parent:
            break

    raise RuntimeError(
        "SmartAssist data directory not found.\n"
        "Run 'smartassist init' in your project root, or set SMARTASSIST_DATA_DIR."
    )


def get_storage_path() -> Path:
    """.claude/smartassist/data/ — feedback logs, scores, lessons."""
    return get_data_dir() / "data"


def get_db_path() -> Path:
    """.claude/smartassist/lancedb/ — vector database."""
    return get_data_dir() / "lancedb"


def get_project_root() -> Path:
    """The project root — parent of .claude/."""
    return get_data_dir().parent.parent
