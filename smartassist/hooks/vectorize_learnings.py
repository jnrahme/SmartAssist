#!/usr/bin/env python3
"""
Vectorize Learnings Hook - refreshes the optional LanceDB cache.

The canonical truth now lives in smartassist.db. This hook rebuilds the
derived embedding cache from the current SQLite search projection so the
background path and the explicit full rebuild path converge on the same
document set.
"""

import json
import sys

from smartassist.config import get_storage_path, get_db_path
from smartassist.store import get_feedback_stats
from smartassist.tools.cleanup_and_vectorize import rebuild_vector_cache


def vectorize_new_learnings() -> bool:
    """Refresh the derived LanceDB cache from the canonical store."""
    storage_path = get_storage_path()
    db_path = get_db_path()

    try:
        summary = rebuild_vector_cache(storage_path, db_path, dry_run=False)
        feedback_stats = get_feedback_stats(storage_path)
        print(json.dumps({
            "status": "vectorized",
            "total_events": int(feedback_stats.get("total_events", 0)),
            "documents": int(summary.get("document_count", 0)),
            "total_in_db": int(summary.get("total_in_db", 0)),
            "by_source_type": summary.get("by_source_type", {}),
        }))
        return True
    except Exception as exc:
        print(json.dumps({
            "status": "error",
            "error": str(exc),
        }))
        return False


if __name__ == "__main__":
    success = vectorize_new_learnings()
    sys.exit(0 if success else 1)
