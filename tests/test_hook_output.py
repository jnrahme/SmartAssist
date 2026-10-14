"""Tests for hook output size and deduplication."""

from smartassist.hooks import session_start
from smartassist.hooks.prompt_inject import _format_episode, _normalize_lesson


def _identity(text):
    return text


class TestSessionStartOutput:
    def test_protocol_header_is_rendered_once(self, set_data_dir):
        output = session_start.format_lessons_for_session()

        assert output.count("SMARTASSIST FEEDBACK PROTOCOL") == 1


class TestFormatEpisode:
    def test_skips_episode_repeating_a_shown_lesson(self):
        seen = {_normalize_lesson("Run all tests before pushing.")}
        ep = {"category": "git", "text": "[git] Run all tests before pushing"}

        assert _format_episode(ep, seen, _identity) is None

    def test_strips_duplicated_category_prefix_and_echoed_context(self):
        lesson = "Never force-push to stage"
        ep = {"category": "git", "text": f"[git] [git] {lesson} Context: {lesson}"}

        assert _format_episode(ep, set(), _identity) == f"[git] {lesson}"

    def test_keeps_distinct_context(self):
        ep = {"category": "testing", "text": "[testing] Mock SVGs Context: jest.config.js mapper"}

        assert (
            _format_episode(ep, set(), _identity)
            == "[testing] Mock SVGs (context: jest.config.js mapper)"
        )

    def test_skips_second_identical_episode(self):
        seen = set()
        ep = {"category": "git", "text": "[git] Sync with stage first"}

        assert _format_episode(ep, seen, _identity) == "[git] Sync with stage first"
        assert _format_episode(ep, seen, _identity) is None
