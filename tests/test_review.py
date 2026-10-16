"""Tests for the review tool and non-destructive legacy lesson sync."""

import json
import time

import pytest

from smartassist.project_facts import detect_package_manager
from smartassist.store import (
    add_lesson,
    append_feedback_event,
    list_lessons,
    open_store,
    restore_lesson,
    retire_lesson,
)
from smartassist.tools import review


@pytest.fixture
def pnpm_store(set_data_dir):
    root = set_data_dir.parent.parent
    (root / "package.json").write_text(json.dumps({"packageManager": "pnpm@11.0.0"}))
    detect_package_manager.cache_clear()
    return set_data_dir / "data"


def _insert_legacy_duplicate(storage, text, category="code_edit"):
    lesson_id, _ = add_lesson(storage, f"placeholder {time.time()}", category)
    from smartassist.store import export_lessons

    with open_store(storage) as conn:
        conn.execute("UPDATE lessons SET text = ? WHERE lesson_id = ?", (text, lesson_id))
        export_lessons(conn, storage)
        conn.commit()
    return lesson_id


def test_findings_report_stale_and_duplicates_keeping_lowest_id(pnpm_store):
    keep, _ = add_lesson(pnpm_store, "Use semantic colors instead of hardcoded values", "code_edit")
    dup = _insert_legacy_duplicate(pnpm_store, "Use semantic colors instead of hardcoded values")
    stale, _ = add_lesson(pnpm_store, "`yarn test:ci` - Run tests for CI", "testing")
    fine, _ = add_lesson(pnpm_store, "Run `pnpm dlx lefthook install` before committing", "git")

    findings = dict(review.find_findings(pnpm_store))

    assert findings[dup] == f"duplicate of {keep}"
    assert findings[stale].startswith("stale:")
    assert keep not in findings and fine not in findings


def test_dry_run_changes_nothing(pnpm_store, capsys):
    add_lesson(pnpm_store, "`yarn lint` - Run ESLint", "testing")

    review.main([])

    assert len(list_lessons(pnpm_store)) == 1
    assert "Dry run" in capsys.readouterr().out


def test_apply_retires_with_backup_and_restore_undoes(pnpm_store):
    stale, _ = add_lesson(pnpm_store, "`yarn lint` - Run ESLint", "testing")

    review.main(["--apply"])

    assert list_lessons(pnpm_store) == []
    assert any((pnpm_store / "backups").iterdir())
    assert restore_lesson(pnpm_store, stale)
    assert [l["id"] for l in list_lessons(pnpm_store)] == [stale]


def test_rebuild_reliability_ignores_commit_hook_events(pnpm_store):
    now = time.time()
    for _ in range(5):
        append_feedback_event(pnpm_store, {"signal": "correction", "category": "git", "intensity": 3,
                                           "session_id": "commit_abc12345", "timestamp": now})
    append_feedback_event(pnpm_store, {"signal": "thumbs_up", "category": "git", "intensity": 5,
                                       "timestamp": now, "provenance": {"source": "user"}})

    alpha, beta, samples = review.rebuild_reliability(pnpm_store, now=now)["git"]

    assert samples == 1
    assert alpha / (alpha + beta) > 0.8


class TestNonDestructiveLegacySync:
    def _edit_json(self, storage, mutate):
        path = storage / "curated_lessons.json"
        data = json.loads(path.read_text())
        path.write_text(json.dumps(mutate(data)))

    def test_lesson_removed_from_json_is_retired_not_deleted(self, set_data_dir):
        storage = set_data_dir / "data"
        a, _ = add_lesson(storage, "Keep lesson A around", "git")
        b, _ = add_lesson(storage, "Keep lesson B around", "git")

        self._edit_json(storage, lambda data: [l for l in data if l["id"] != b])

        assert [l["id"] for l in list_lessons(storage)] == [a]
        retired = {l["id"]: l for l in list_lessons(storage, include_inactive=True)}[b]
        assert retired["state"] == "retired"

    def test_retired_lesson_survives_external_edit_and_can_be_restored(self, set_data_dir):
        storage = set_data_dir / "data"
        a, _ = add_lesson(storage, "Keep lesson A around", "git")
        b, _ = add_lesson(storage, "Lesson B to retire", "git")
        retire_lesson(storage, b, retired_reason="test")

        self._edit_json(storage, lambda data: data + [{"id": "L999", "lesson": "Hand added", "category": "git"}])

        assert restore_lesson(storage, b)
        assert {l["id"] for l in list_lessons(storage)} == {a, b, "L999"}

    def test_corrupt_json_does_not_wipe_lessons(self, set_data_dir):
        storage = set_data_dir / "data"
        add_lesson(storage, "Precious lesson", "git")

        (storage / "curated_lessons.json").write_text("{not json")

        assert [l["lesson"] for l in list_lessons(storage)] == ["Precious lesson"]
