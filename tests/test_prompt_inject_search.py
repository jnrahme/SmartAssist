"""Tests for prompt-inject keyword search behavior."""

from smartassist.hooks.prompt_inject import build_idf, search_lessons, tokenize


class TestPromptInjectSearch:
    def test_single_exact_match_is_not_filtered_out(self):
        lessons = [
            {
                "id": "L001",
                "lesson": "Use semantic colors from theme instead of hardcoded hex values",
                "category": "code_edit",
            }
        ]

        query_tokens = tokenize("semantic colors from theme")
        idf = build_idf(lessons)
        results = search_lessons(query_tokens, lessons, idf)

        assert idf["semantic"] > 0
        assert [result["id"] for result in results] == ["L001"]
