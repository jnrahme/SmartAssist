"""Hook-side client for the embedding service. Never blocks a prompt on the model."""

import json
import os
import socket

from smartassist.semantic import runtime_dir, socket_path

DEFAULT_TIMEOUT = 0.4


def is_enabled(storage_path):
    if os.environ.get("SMARTASSIST_SEMANTIC") == "1":
        return True
    try:
        settings = json.loads((storage_path / "settings.json").read_text())
    except (OSError, ValueError, TypeError):
        return False
    return bool(isinstance(settings, dict) and settings.get("semantic_retrieval"))


def request(payload, timeout=DEFAULT_TIMEOUT, path=None):
    """Send one request; return the decoded response, or None if unavailable."""
    path = path or socket_path()
    try:
        with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as conn:
            conn.settimeout(timeout)
            conn.connect(str(path))
            conn.sendall(json.dumps(payload).encode("utf-8") + b"\n")
            chunks = []
            while True:
                chunk = conn.recv(65536)
                if not chunk:
                    break
                chunks.append(chunk)
                if chunk.endswith(b"\n"):
                    break
        response = json.loads(b"".join(chunks))
    except (OSError, ValueError):
        return None
    return response if isinstance(response, dict) and response.get("ok") else None


def ensure_running():
    """Start the service in the background unless one is already running."""
    from smartassist.config import spawn_background_job

    spawn_background_job(
        "smartassist.semantic.server", lock_name="embed-server", skip=True, lock_dir=runtime_dir()
    )


def similarity_scores(query, lessons, timeout=DEFAULT_TIMEOUT):
    """Return {lesson_id: cosine}, or None (and start the service) when unavailable."""
    response = request(
        {"op": "similarity", "query": query, "lessons": [[l["id"], l["lesson"]] for l in lessons]},
        timeout=timeout,
    )
    if response is None:
        ensure_running()
        return None
    return response.get("scores")
