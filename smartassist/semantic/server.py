"""Local embedding service: keeps the model warm for the per-prompt hook.

Protocol: one JSON request per connection over a Unix socket, answered with one
JSON response, then the connection closes.

    {"op": "ping"}
    {"op": "similarity", "query": "...", "lessons": [["L001", "text"], ...]}
        -> {"ok": true, "scores": {"L001": 0.71, ...}}

Lesson vectors are cached by text hash in memory and on disk, so only new or
edited lessons are embedded. The process exits after IDLE_SECONDS without
requests.
"""

import hashlib
import json
import os
import socket
import sys
import time

from smartassist.semantic import IDLE_SECONDS, MODEL_NAME, QUERY_INSTRUCTION, runtime_dir, socket_path

MAX_REQUEST_BYTES = 8 * 1024 * 1024


def _text_key(text):
    return hashlib.sha1(text.encode("utf-8")).hexdigest()


class EmbeddingCache:
    """Vectors keyed by text hash, persisted as JSON (no pickle on disk)."""

    def __init__(self, path):
        self.path = path
        self.vectors = {}
        try:
            self.vectors = json.loads(path.read_text())
        except (OSError, ValueError):
            self.vectors = {}

    def missing(self, texts):
        return [t for t in texts if _text_key(t) not in self.vectors]

    def add(self, texts, vectors):
        for text, vector in zip(texts, vectors, strict=True):
            self.vectors[_text_key(text)] = [round(float(v), 5) for v in vector]
        tmp = self.path.with_suffix(".tmp")
        tmp.write_text(json.dumps(self.vectors))
        os.replace(tmp, self.path)

    def get(self, text):
        return self.vectors[_text_key(text)]


class Embedder:
    def __init__(self, model_name=MODEL_NAME):
        from sentence_transformers import SentenceTransformer

        self.model = SentenceTransformer(model_name)
        safe = model_name.replace("/", "--")
        self.cache = EmbeddingCache(runtime_dir() / f"embeddings-{safe}.json")

    def encode(self, texts):
        return self.model.encode(texts, normalize_embeddings=True).tolist()

    def similarity(self, query, lessons):
        texts = [text for _, text in lessons]
        new = self.cache.missing(texts)
        if new:
            self.cache.add(new, self.encode(new))
        q = self.encode([QUERY_INSTRUCTION + query])[0]
        scores = {}
        for lesson_id, text in lessons:
            vector = self.cache.get(text)
            scores[lesson_id] = sum(a * b for a, b in zip(q, vector, strict=True))
        return scores


def handle(embedder, request):
    op = request.get("op")
    if op == "ping":
        return {"ok": True, "model": MODEL_NAME}
    if op == "similarity":
        lessons = [(str(i), str(t)) for i, t in request.get("lessons", [])]
        return {"ok": True, "scores": embedder.similarity(str(request.get("query", "")), lessons)}
    return {"ok": False, "error": f"unknown op: {op}"}


def _read_request(conn):
    chunks, size = [], 0
    while True:
        chunk = conn.recv(65536)
        if not chunk:
            break
        chunks.append(chunk)
        size += len(chunk)
        if size > MAX_REQUEST_BYTES:
            raise ValueError("request too large")
        if chunk.endswith(b"\n"):
            break
    return json.loads(b"".join(chunks) or b"{}")


def serve(embedder, path, idle_seconds=IDLE_SECONDS):
    if path.exists():
        path.unlink()
    server = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    server.bind(str(path))
    os.chmod(path, 0o600)
    server.listen(8)
    server.settimeout(min(60, idle_seconds))
    last_request = time.monotonic()
    try:
        while time.monotonic() - last_request < idle_seconds:
            try:
                conn, _ = server.accept()
            except socket.timeout:
                continue
            last_request = time.monotonic()
            with conn:
                conn.settimeout(10)
                try:
                    response = handle(embedder, _read_request(conn))
                except Exception as exc:  # report, keep serving
                    response = {"ok": False, "error": str(exc)}
                try:
                    conn.sendall(json.dumps(response).encode("utf-8") + b"\n")
                except OSError:
                    pass  # client gave up (cold cache); the work is cached for next time
    finally:
        server.close()
        if path.exists():
            path.unlink()


def main():
    serve(Embedder(), socket_path())
    return 0


if __name__ == "__main__":
    sys.exit(main())
