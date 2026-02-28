#!/usr/bin/env python3
"""
Vectorize Learnings Hook - Ensures RAG database stays fully vectorized.
Ingests new feedback into the vector database after commits/sessions.
"""

import sys
import json
import re
from datetime import datetime
from typing import List, Dict, Tuple, Optional

from smartassist.config import EMBEDDING_MODEL, get_storage_path, get_db_path

# Minimum correction length to be useful for vectorization
MIN_CORRECTION_LEN = 30

# Patterns that indicate a non-actionable lesson
SKIP_PATTERNS = [
    r"^this has been addressed",
    r"^done\b",
    r"^fixed\b",
    r"^resolved\b",
    r"^acknowledged\b",
    r"^nit:",
    r"^lgtm",
    r"^good catch",
    r"^thanks",
    r"^ok\b",
    r"^yes\b",
]


def is_skip_pattern(text: str) -> bool:
    """Check if text matches a non-actionable pattern."""
    lower = text.lower().strip()
    for pattern in SKIP_PATTERNS:
        if re.match(pattern, lower):
            return True
    return False


def get_unvectorized_feedback() -> Tuple[List[Dict], int]:
    """Get feedback events that haven't been vectorized yet."""
    storage_path = get_storage_path()
    vectorization_log = storage_path / "vectorization_log.json"
    feedback_log = storage_path / "feedback_log.jsonl"

    if vectorization_log.exists():
        with open(vectorization_log, "r") as f:
            vectorized_data = json.load(f)
            last_vectorized_count = vectorized_data.get("total_vectorized", 0)
    else:
        last_vectorized_count = 0

    all_feedback = []
    if feedback_log.exists():
        with open(feedback_log, "r") as f:
            for line in f:
                line = line.strip()
                if line:
                    try:
                        all_feedback.append(json.loads(line))
                    except json.JSONDecodeError:
                        continue

    new_feedback = all_feedback[last_vectorized_count:]
    return new_feedback, len(all_feedback)


def should_vectorize(event: Dict) -> bool:
    """Check if an event is worth vectorizing."""
    correction = event.get("correction", "")
    if not correction or len(correction.strip()) < MIN_CORRECTION_LEN:
        return False
    if is_skip_pattern(correction):
        return False
    return True


def format_text_for_vector(event: Dict) -> str:
    """Create a dense text blob for vectorization."""
    category = event.get("category", "unknown")
    correction = event.get("correction", "").strip()
    context = event.get("context", "").strip()

    text = f"[{category}] {correction}"
    if context:
        text += f" Context: {context}"
    return text


def vectorize_new_learnings() -> bool:
    """Vectorize new feedback into RAG database."""
    storage_path = get_storage_path()
    db_path = get_db_path()
    vectorization_log = storage_path / "vectorization_log.json"

    try:
        new_feedback, total_feedback = get_unvectorized_feedback()

        if not new_feedback:
            print(json.dumps({
                "status": "up_to_date",
                "total_events": total_feedback,
                "new_events": 0,
            }))
            return True

        worthy = [e for e in new_feedback if should_vectorize(e)]

        if not worthy:
            _update_vectorization_log(vectorization_log, total_feedback, None)
            print(json.dumps({
                "status": "skipped",
                "total_events": total_feedback,
                "new_events": len(new_feedback),
                "skipped": len(new_feedback),
                "reason": "all_filtered",
            }))
            return True

        # Lazy-load heavy dependencies
        import lancedb
        from sentence_transformers import SentenceTransformer

        model = SentenceTransformer(EMBEDDING_MODEL)
        db = lancedb.connect(str(db_path))

        texts = [format_text_for_vector(e) for e in worthy]
        embeddings = model.encode(texts, batch_size=64)

        try:
            table = db.open_table("documents")
            next_id = table.count_rows() + 1
        except Exception:
            next_id = 1

        DEDUP_DISTANCE = 0.40
        data = []
        dedup_skipped = 0
        for i, (event, text, emb) in enumerate(zip(worthy, texts, embeddings)):
            try:
                table = db.open_table("documents")
                existing = table.search(emb.tolist()).limit(1).to_list()
                if existing and existing[0].get("_distance", 99) < DEDUP_DISTANCE:
                    dedup_skipped += 1
                    continue
            except Exception:
                pass

            data.append({
                "id": next_id + i,
                "text": text,
                "vector": emb.tolist(),
                "category": event.get("category", "unknown"),
                "timestamp": event.get("timestamp", 0),
            })

        if not data:
            _update_vectorization_log(vectorization_log, total_feedback, None)
            print(json.dumps({
                "status": "skipped",
                "total_events": total_feedback,
                "new_events": len(new_feedback),
                "skipped": len(new_feedback),
                "dedup_skipped": dedup_skipped,
                "reason": "all_deduplicated",
            }))
            return True

        try:
            table = db.open_table("documents")
            table.add(data)
        except Exception:
            table = db.create_table("documents", data=data, mode="overwrite")

        _update_vectorization_log(vectorization_log, total_feedback, table.count_rows())

        print(json.dumps({
            "status": "vectorized",
            "total_events": total_feedback,
            "new_events": len(new_feedback),
            "vectorized": len(data),
            "skipped": len(new_feedback) - len(worthy),
            "dedup_skipped": dedup_skipped,
            "total_in_db": table.count_rows(),
        }))

        return True

    except Exception as e:
        print(json.dumps({
            "status": "error",
            "error": str(e),
        }))
        return False


def _update_vectorization_log(log_path, total_feedback: int, total_in_db: Optional[int]):
    """Update the vectorization progress log."""
    log_data = {
        "total_vectorized": total_feedback,
        "last_vectorization": datetime.now().isoformat(),
    }
    if total_in_db is not None:
        log_data["total_documents_in_rag"] = total_in_db

    with open(log_path, "w") as f:
        json.dump(log_data, f, indent=2)


if __name__ == "__main__":
    success = vectorize_new_learnings()
    sys.exit(0 if success else 1)
