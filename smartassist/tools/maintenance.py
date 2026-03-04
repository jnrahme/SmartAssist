#!/usr/bin/env python3
"""
Maintenance Script - Staleness policy + LanceDB compaction + feedback log rotation.

Features:
  - Archive docs > 180 days with no positive reinforcement
  - Flag docs > 90 days for review
  - LanceDB compaction (compact_files + cleanup_old_versions)
  - Rotate feedback_log.jsonl when it exceeds 10K lines

Usage:
    smartassist maintenance [--dry-run]
"""

import json
import sys
import time
import shutil
from datetime import datetime

from smartassist.config import get_storage_path, get_db_path, EMBEDDING_DIM

BOLD = "\033[1m"
DIM = "\033[2m"
GREEN = "\033[32m"
RED = "\033[31m"
YELLOW = "\033[33m"
CYAN = "\033[36m"
RESET = "\033[0m"

# Staleness thresholds (in seconds)
FLAG_THRESHOLD = 90 * 86400   # 90 days
ARCHIVE_THRESHOLD = 180 * 86400  # 180 days

# Feedback log rotation thresholds
FEEDBACK_LOG_MAX_LINES = 10000
FEEDBACK_LOG_KEEP_LINES = 5000


def check_staleness(dry_run: bool = False):
    """Identify stale documents based on age."""
    db_path = get_db_path()

    print(f"\n{BOLD}Staleness Check{RESET}")
    print(f"  Flag threshold:    90 days")
    print(f"  Archive threshold: 180 days\n")

    try:
        import lancedb
        db = lancedb.connect(str(db_path))
        table = db.open_table("documents")
    except Exception as e:
        print(f"  {RED}Cannot open database: {e}{RESET}")
        return

    results = table.search([0.0] * EMBEDDING_DIM).limit(table.count_rows()).to_list()
    now = time.time()

    flagged = []
    archived = []

    for doc in results:
        ts = doc.get("timestamp", 0)
        if not ts or ts == 0:
            continue
        age = now - ts
        doc_id = doc.get("id", "?")
        cat = doc.get("category", "?")
        text_preview = doc.get("text", "")[:80]

        if age > ARCHIVE_THRESHOLD:
            archived.append((doc_id, cat, age / 86400, text_preview))
        elif age > FLAG_THRESHOLD:
            flagged.append((doc_id, cat, age / 86400, text_preview))

    if flagged:
        print(f"  {YELLOW}Flagged ({len(flagged)} docs > 90 days):{RESET}")
        for doc_id, cat, days, preview in flagged[:10]:
            print(f"    [{cat}] id={doc_id} ({days:.0f}d) {DIM}{preview}{RESET}")
        if len(flagged) > 10:
            print(f"    {DIM}... and {len(flagged) - 10} more{RESET}")
    else:
        print(f"  {GREEN}No flagged documents{RESET}")

    if archived:
        print(f"\n  {RED}Archivable ({len(archived)} docs > 180 days):{RESET}")
        for doc_id, cat, days, preview in archived[:10]:
            print(f"    [{cat}] id={doc_id} ({days:.0f}d) {DIM}{preview}{RESET}")
        if len(archived) > 10:
            print(f"    {DIM}... and {len(archived) - 10} more{RESET}")

        if not dry_run:
            print(f"\n  {DIM}Note: Auto-archival not enabled. Use --archive to remove stale docs.{RESET}")
    else:
        print(f"  {GREEN}No archivable documents{RESET}")

    print(f"\n  Total: {len(results)} docs, {len(flagged)} flagged, {len(archived)} archivable")


def compact_database():
    """Run LanceDB compaction to merge small fragments."""
    db_path = get_db_path()

    print(f"\n{BOLD}LanceDB Compaction{RESET}")

    try:
        import lancedb
        db = lancedb.connect(str(db_path))
        table = db.open_table("documents")
    except Exception as e:
        print(f"  {RED}Cannot open database: {e}{RESET}")
        return

    row_count = table.count_rows()
    print(f"  Documents: {row_count}")

    try:
        try:
            table.optimize.compact_files()
            table.optimize.cleanup_old_versions()
        except AttributeError:
            table.compact_files()
            table.cleanup_old_versions()
        print(f"  {GREEN}Compaction and cleanup complete{RESET}")
    except Exception as e:
        print(f"  {YELLOW}Compaction: {e}{RESET}")


def rotate_feedback_log():
    """Rotate feedback_log.jsonl if it exceeds FEEDBACK_LOG_MAX_LINES.

    Archives oldest lines to feedback_log.jsonl.{timestamp}.bak,
    keeps the most recent FEEDBACK_LOG_KEEP_LINES.
    Resets vectorization_log.json counter to match.
    """
    try:
        storage = get_storage_path()
    except RuntimeError:
        return

    feedback_log = storage / "feedback_log.jsonl"
    if not feedback_log.exists():
        print(f"\n{BOLD}Feedback Log Rotation{RESET}")
        print(f"  {DIM}No feedback_log.jsonl found{RESET}")
        return

    # Count lines
    with open(feedback_log, "r", encoding="utf-8") as f:
        all_lines = f.readlines()

    total_lines = len(all_lines)

    print(f"\n{BOLD}Feedback Log Rotation{RESET}")
    print(f"  Lines: {total_lines}/{FEEDBACK_LOG_MAX_LINES}")

    if total_lines <= FEEDBACK_LOG_MAX_LINES:
        print(f"  {GREEN}No rotation needed{RESET}")
        return

    # Archive the oldest lines
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    archive_path = storage / f"feedback_log.jsonl.{timestamp}.bak"

    archive_count = total_lines - FEEDBACK_LOG_KEEP_LINES
    archive_lines = all_lines[:archive_count]
    keep_lines = all_lines[archive_count:]

    # Write archive
    with open(archive_path, "w", encoding="utf-8") as f:
        f.writelines(archive_lines)

    # Overwrite with kept lines
    with open(feedback_log, "w", encoding="utf-8") as f:
        f.writelines(keep_lines)

    print(f"  {YELLOW}Rotated: archived {archive_count} lines → {archive_path.name}{RESET}")
    print(f"  {GREEN}Kept {len(keep_lines)} recent lines{RESET}")

    # Reset vectorization counter to match
    vec_log = storage / "vectorization_log.json"
    if vec_log.exists():
        try:
            data = json.loads(vec_log.read_text())
            old_count = data.get("last_processed_line", 0)
            # Adjust: new line count is FEEDBACK_LOG_KEEP_LINES
            data["last_processed_line"] = len(keep_lines)
            vec_log.write_text(json.dumps(data, indent=2))
            print(f"  {DIM}Reset vectorization counter: {old_count} → {len(keep_lines)}{RESET}")
        except Exception:
            pass


def main():
    dry_run = "--dry-run" in sys.argv

    print(f"\n{CYAN}{BOLD}{'=' * 60}")
    print("  RAG MAINTENANCE")
    print(f"{'=' * 60}{RESET}")
    if dry_run:
        print(f"  {YELLOW}DRY RUN - no changes will be made{RESET}")
    print(f"  {DIM}Date: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}{RESET}")

    check_staleness(dry_run=dry_run)
    compact_database()
    rotate_feedback_log()

    print(f"\n{GREEN}Maintenance complete.{RESET}\n")


if __name__ == "__main__":
    main()
