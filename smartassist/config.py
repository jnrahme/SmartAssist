"""
Centralized configuration for SmartAssist.

All path resolution and embedding model references live here.
Every module imports from this file — it is the architectural keystone.
"""

import atexit
import fcntl
import json
import os
import subprocess
import tempfile
from pathlib import Path

# BGE-M3: 8K context, 1024 dims, open-source, supports dense+sparse+multi-vector
EMBEDDING_MODEL = "BAAI/bge-m3"
EMBEDDING_DIM = 1024


def _find_project_data_dir(start_path: Path | None = None) -> Path | None:
    """Walk up from a starting path and find .claude/smartassist if present."""
    current = (start_path or Path.cwd()).resolve()
    for parent in [current, *current.parents]:
        candidate = parent / ".claude" / "smartassist"
        if candidate.is_dir():
            return candidate
        if parent == parent.parent:
            break
    return None


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
        p = Path(env_dir).expanduser()
        project_candidate = p / ".claude" / "smartassist"
        if project_candidate.exists():
            p = project_candidate
        if p.is_dir():
            return p
        # Allow env var to point to a dir that will be created
        return p

    # 2. Walk up from cwd to find .claude/smartassist/
    cwd_candidate = _find_project_data_dir()
    if cwd_candidate is not None:
        return cwd_candidate

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
    """The active project root.

    Prefer the cwd-discovered project when available so custom data-dir overrides
    do not break git/path operations that need the live workspace root.
    """
    cwd_candidate = _find_project_data_dir()
    if cwd_candidate is not None:
        return cwd_candidate.parent.parent

    data_dir = get_data_dir()
    if data_dir.name == "smartassist" and data_dir.parent.name == ".claude":
        return data_dir.parent.parent

    return Path.cwd()


# ── Atomic / locked JSON I/O ────────────────────────────────────────────────


def atomic_write_json(path: Path, data, indent: int = 2):
    """Write JSON atomically via temp file + os.replace().

    Prevents data corruption on crash (C2 fix).
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_path = tempfile.mkstemp(
        dir=path.parent, suffix=".tmp", prefix=path.stem + "_"
    )
    try:
        with os.fdopen(fd, "w") as f:
            json.dump(data, f, indent=indent)
        os.replace(tmp_path, path)
    except BaseException:
        try:
            os.unlink(tmp_path)
        except OSError:
            pass
        raise


def locked_update_json(path: Path, updater, default=None):
    """Read-modify-write a JSON file under an advisory lock.

    Prevents concurrent update races (C3 fix).

    Args:
        path: The JSON file to update.
        updater: Callable that receives the current data and returns the new data.
                 If updater returns None the write is skipped.
        default: Value passed to updater when the file is missing or corrupt.

    Returns:
        The value returned by updater (i.e. the data that was written).
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    lock_path = path.with_suffix(path.suffix + ".lock")

    with open(lock_path, "w") as lock_fd:
        fcntl.flock(lock_fd, fcntl.LOCK_EX)
        try:
            current = json.loads(path.read_text()) if path.exists() else default
        except (json.JSONDecodeError, OSError):
            current = default

        result = updater(current)
        if result is not None:
            atomic_write_json(path, result)

    return result


# ── Managed subprocess spawning ──────────────────────────────────────────────

_CHILD_PROCS: list = []
_MAX_CHILDREN = 3


def _reap_children():
    """Remove finished child processes from the tracking list."""
    still_running = []
    for proc in _CHILD_PROCS:
        if proc.poll() is None:
            still_running.append(proc)
    _CHILD_PROCS[:] = still_running


def _cleanup_children():
    """Wait on all remaining child processes at interpreter shutdown."""
    for proc in _CHILD_PROCS:
        try:
            proc.wait(timeout=10)
        except Exception:
            proc.kill()


atexit.register(_cleanup_children)


def spawn_managed(args, **kwargs):
    """Spawn a subprocess with tracking to prevent zombie accumulation (H5 fix).

    Reaps finished children first, limits concurrent children to _MAX_CHILDREN,
    and registers an atexit handler so no child is orphaned.

    Returns the Popen object (or None if the limit is reached).
    """
    _reap_children()

    if len(_CHILD_PROCS) >= _MAX_CHILDREN:
        return None

    kwargs.setdefault("stdout", subprocess.DEVNULL)
    kwargs.setdefault("stderr", subprocess.DEVNULL)
    proc = subprocess.Popen(args, **kwargs)
    _CHILD_PROCS.append(proc)
    return proc
