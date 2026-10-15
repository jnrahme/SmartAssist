"""Opt-in semantic retrieval through a warm local embedding service (ADR 0002)."""

import hashlib
import os
from pathlib import Path

MODEL_NAME = "BAAI/bge-small-en-v1.5"
# BGE models are trained to embed retrieval queries with this instruction
# (passages/lessons are embedded without it)
QUERY_INSTRUCTION = "Represent this sentence for searching relevant passages: "
IDLE_SECONDS = 30 * 60


def runtime_dir() -> Path:
    """User-only directory for the service socket, lock and embedding cache."""
    base = os.environ.get("SMARTASSIST_RUNTIME_DIR") or os.path.join(
        os.path.expanduser("~"), ".cache", "smartassist"
    )
    path = Path(base)
    path.mkdir(parents=True, exist_ok=True)
    os.chmod(path, 0o700)
    return path


# AF_UNIX paths are capped at 104 bytes on macOS (108 on Linux)
MAX_SOCKET_PATH = 100


def socket_path() -> Path:
    path = runtime_dir() / "embed.sock"
    if len(str(path)) <= MAX_SOCKET_PATH:
        return path
    digest = hashlib.sha1(str(path).encode("utf-8")).hexdigest()[:12]
    short_dir = Path("/tmp") / f"smartassist-{os.getuid()}"
    short_dir.mkdir(mode=0o700, exist_ok=True)
    os.chmod(short_dir, 0o700)
    return short_dir / f"{digest}.sock"
