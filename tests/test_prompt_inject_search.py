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

    def test_newer_lesson_wins_equal_score_tie(self):
        lessons = [
            {
                "id": "L026",
                "lesson": "Use semantic theme tokens instead of hardcoded hex values in styles",
                "category": "code_edit",
            },
            {
                "id": "L166",
                "lesson": "Use semantic theme tokens instead of hardcoded hex values in styles",
                "category": "code_edit",
            },
        ]

        query_tokens = tokenize("semantic theme tokens hardcoded hex values styles")
        idf = build_idf(lessons)
        results = search_lessons(query_tokens, lessons, idf, top_k=2)

        assert [result["id"] for result in results] == ["L166", "L026"]

    def test_pronoun_noise_does_not_beat_relevant_style_lesson(self):
        lessons = [
            {
                "id": "L102",
                "lesson": "Testing bdp i like how you figured out which file to test",
                "category": "testing",
            },
            {
                "id": "L166",
                "lesson": "Always use semantic theme tokens instead of hardcoded hex values in styles",
                "category": "code_edit",
            },
        ]

        query_tokens = tokenize(
            "did you follow best practices you have a styles file why put it directly in the component"
        )
        idf = build_idf(lessons)
        results = search_lessons(query_tokens, lessons, idf, top_k=2)

        assert [result["id"] for result in results] == ["L166"]
