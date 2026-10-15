"""Regression tests for feedback signal detection and per-session scoping."""

import pytest

from smartassist.hooks.prompt_inject import detect_feedback_signal
from smartassist.lesson_feedback import (
    _infer_category,
    find_recurring_lessons,
    load_last_injection,
    reinforce_recent_lessons,
    save_last_injection,
)
from smartassist.store import add_lesson


@pytest.mark.parametrize(
    "message",
    [
        "-1 is the default value here, add a test for it",
        "+1 to the retry count when the request fails",
    ],
)
def test_numeric_prefix_is_not_feedback(message):
    assert detect_feedback_signal(message) == (None, None)


@pytest.mark.parametrize(
    "message,expected",
    [
        ("+1", ("positive", "")),
        ("-1", ("negative", "")),
        (":( dont use yarn here", ("negative", "dont use yarn here")),
    ],
)
def test_real_feedback_is_still_detected(message, expected):
    assert detect_feedback_signal(message) == expected


class TestSessionScoping:
    def test_feedback_from_another_session_ignores_injection(self, set_data_dir):
        save_last_injection([{"id": "L001"}], session_id="session-a")

        assert load_last_injection("session-b") == {}
        assert reinforce_recent_lessons("negative", session_id="session-b") == []

    def test_feedback_from_same_session_reinforces(self, set_data_dir):
        save_last_injection([{"id": "L001"}], session_id="session-a")

        results = reinforce_recent_lessons("positive", session_id="session-a")

        assert [r[0] for r in results] == ["L001"]

    def test_unscoped_caller_keeps_legacy_behavior(self, set_data_dir):
        save_last_injection([{"id": "L001"}], session_id="session-a")

        assert load_last_injection()["1"] == "L001"


def test_category_comes_from_feedback_text_before_injected_lessons(set_data_dir):
    storage = set_data_dir / "data"
    lesson_id, _ = add_lesson(storage, "Use semantic theme colors for every new style", "security")
    reinforced = [(lesson_id, 1.0, 0.8, False)]

    category = _infer_category(reinforced, storage, "dont commit to the stage branch directly")

    assert category == "git"


class TestRecurringMistakes:
    LESSON = "When switching branches with uncommitted changes, do NOT stash them; keep the changes"

    def _inject(self, storage, session_id="A"):
        lesson_id, _ = add_lesson(storage, self.LESSON, "git")
        save_last_injection([{"id": lesson_id}], session_id=session_id)
        return lesson_id

    def test_frown_restating_injected_lesson_is_a_recurrence(self, set_data_dir):
        lesson_id = self._inject(set_data_dir / "data")

        assert find_recurring_lessons("never stash, keep the changes", session_id="A") == [lesson_id]

    def test_sharing_only_common_words_is_not_a_recurrence(self, set_data_dir):
        storage = set_data_dir / "data"
        stash_id = self._inject(storage)
        scope_id, _ = add_lesson(storage, "Keep PRs scoped to the ticket and drop unrelated changes", "git")
        for filler in ("Keep tests fast", "Keep changes small", "Review changes before merging"):
            add_lesson(storage, filler, "git")
        save_last_injection([{"id": stash_id}, {"id": scope_id}], session_id="A")

        assert find_recurring_lessons("never stash, keep the changes", session_id="A") == [stash_id]

    def test_unrelated_frown_is_not_a_recurrence(self, set_data_dir):
        self._inject(set_data_dir / "data")

        assert find_recurring_lessons("dont use yarn, this repo uses pnpm", session_id="A") == []

    def test_recurrence_strengthens_lesson_and_creates_no_duplicate(self, set_data_dir, capsys):
        import io
        import json
        import sys

        from smartassist.hooks import prompt_inject
        from smartassist.lesson_feedback import load_lesson_scores
        from smartassist.store import list_lessons

        storage = set_data_dir / "data"
        lesson_id = self._inject(storage)
        before = len(list_lessons(storage))
        payload = json.dumps({"prompt": ":( never stash, keep the changes", "session_id": "A"})

        sys.stdin = io.StringIO(payload)
        try:
            prompt_inject.main()
        finally:
            sys.stdin = sys.__stdin__

        context = json.loads(capsys.readouterr().out)["hookSpecificOutput"]["additionalContext"]
        assert context.startswith("RECURRING MISTAKE")
        assert f"[{lesson_id}]" in context
        assert len(list_lessons(storage)) == before
        assert load_lesson_scores()[lesson_id]["boost"] > 1.0


@pytest.mark.parametrize(
    "created,asks_to_create",
    [(("L178", "Don't use yarn, this repo uses pnpm"), False), ((None, None), True)],
)
def test_feedback_context_has_one_creation_path(created, asks_to_create):
    from smartassist.hooks.prompt_inject import build_rich_feedback_context

    context = build_rich_feedback_context(
        "negative", "dont use yarn, this repo uses pnpm", [], created_lesson=created
    )

    assert ("ACTION REQUIRED" in context) is asks_to_create
    if not asks_to_create:
        assert "[L178]" in context and "Do not create another" in context

