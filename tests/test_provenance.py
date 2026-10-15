"""Tests for event provenance and evidence filtering in the boundary pack."""

import json
import sqlite3

import pytest

from smartassist.boundary_packs import evidence_events
from smartassist.store import (
    add_lesson,
    append_feedback_event,
    event_source,
    get_store_db_path,
    list_feedback_events,
    open_store,
    retire_lesson,
)


def _event(correction, context="", **extra):
    return {"signal": "correction", "category": "git", "correction": correction, "context": context, **extra}


@pytest.mark.parametrize(
    "context,expected",
    [
        ("From CLAUDE.md section: Testing", "seed"),
        ("deep-seed LLM-generated lesson", "seed"),
        ("Branch: BTMAPP-1895", "commit_hook"),
        ("Commit: Merge remote-tracking branch", "commit_hook"),
        ("Good practice: tests updated", "commit_hook"),
        ("create_lesson MCP tool", "user"),
        ("", "user"),
    ],
)
def test_legacy_events_get_inferred_source(context, expected):
    assert event_source({"context": context}) == expected


def test_commit_hook_session_marks_legacy_event():
    assert event_source({"context": "Files: src/a.ts", "session_id": "commit_60b0e9d6"}) == "commit_hook"


def test_recorded_source_wins_over_inference():
    assert event_source({"context": "Branch: X", "provenance": {"source": "mcp"}}) == "mcp"


def test_provenance_survives_export_and_resync(set_data_dir):
    storage = set_data_dir / "data"
    append_feedback_event(storage, _event("Never force-push", provenance={"source": "mcp"}))

    log = storage / "feedback_log.jsonl"
    exported = json.loads(log.read_text().splitlines()[-1])
    assert exported["provenance"]["source"] == "mcp"
    assert exported["provenance"]["project_root"] == str(set_data_dir.parent.parent.resolve())

    log.write_text(log.read_text() + "\n")
    with open_store(storage, sync=("events",)):
        pass
    events = list_feedback_events(storage)
    assert events[-1]["provenance"]["source"] == "mcp"


def test_old_database_gains_provenance_column(set_data_dir):
    storage = set_data_dir / "data"
    append_feedback_event(storage, _event("Keep commits small"))
    db = get_store_db_path(storage)
    conn = sqlite3.connect(db)
    conn.execute("ALTER TABLE feedback_events DROP COLUMN provenance")
    conn.commit()
    conn.close()

    append_feedback_event(storage, _event("Run lint before push"))

    assert [e["correction"] for e in list_feedback_events(storage)][-1] == "Run lint before push"


class TestEvidenceEvents:
    def test_drops_synthetic_other_project_and_retired(self, set_data_dir):
        storage = set_data_dir / "data"
        retired_id, _ = add_lesson(storage, "Use yarn for every install command", "git")
        retire_lesson(storage, retired_id)
        events = [
            _event("Real user correction about stashing"),
            _event("Seeded bullet", context="From CLAUDE.md section: Git"),
            _event("Commit heuristic", context="Branch: BTMAPP-1"),
            _event("Wedding site lesson", provenance={"project_root": "/somewhere/else"}),
            _event("Use yarn for every install command"),
        ]

        kept = evidence_events(events, storage)

        assert [e["correction"] for e in kept] == ["Real user correction about stashing"]

    def test_keeps_events_recorded_for_this_project(self, set_data_dir):
        storage = set_data_dir / "data"
        event = _event("Sync stage first", provenance={"project_root": str(set_data_dir.parent.parent.resolve())})

        assert evidence_events([event], storage) == [event]


def _add_in_subprocess(args):
    storage, index = args
    from smartassist.store import add_lesson as add

    return add(storage, f"Concurrent lesson number {index} about distinct topic {index}", "git")


def test_concurrent_writers_get_unique_lesson_ids(set_data_dir):
    import multiprocessing

    storage = str(set_data_dir / "data")
    add_lesson(storage, "Seed lesson so the store exists", "git")
    with multiprocessing.get_context("spawn").Pool(16) as pool:
        results = pool.map(_add_in_subprocess, [(storage, i) for i in range(16)])

    ids = [lesson_id for lesson_id, error in results]
    assert all(error is None for _, error in results)
    assert len(set(ids)) == 16
    # No writer's lesson may be dropped by another writer's legacy resync
    from smartassist.store import list_lessons

    assert set(ids) <= {lesson["id"] for lesson in list_lessons(storage)}


def test_duplicate_text_in_another_category_is_rejected(set_data_dir):
    storage = set_data_dir / "data"
    first, _ = add_lesson(storage, "Use semantic colors instead of hardcoded values", "code_edit")

    second, error = add_lesson(storage, "use semantic colors instead of hardcoded values.", "testing")

    assert second is None
    assert first in error
