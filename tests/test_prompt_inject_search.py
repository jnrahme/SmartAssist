"""Tests for prompt-inject keyword search behavior."""

import pytest

from smartassist.hooks.prompt_inject import (
    apply_injection_budget,
    build_idf,
    expand_query,
    search_lessons,
    stem,
    tokenize,
)


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

        assert idf[tokenize("semantic")[0]] > 0
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

        assert "L102" not in [result["id"] for result in results]


@pytest.mark.parametrize(
    "variants",
    [
        ("event", "events"),
        ("component", "components"),
        ("style", "styles", "styled", "styling"),
        ("assert", "assertion", "asserting"),
        ("switch", "switched", "switching"),
    ],
)
def test_stem_maps_inflections_to_one_key(variants):
    assert len({stem(v) for v in variants}) == 1


def test_file_like_tokens_are_not_stemmed():
    assert stem("firebaseanalytics.constants.ts") == "firebaseanalytics.constants.ts"


def test_synonyms_weigh_less_than_query_terms():
    expanded = expand_query(tokenize("write tests"))

    assert expanded[stem("test")] == 1.0
    assert expanded[stem("mock")] < 1.0


def test_direct_match_beats_synonym_only_match():
    lessons = [
        {"id": "L001", "lesson": "Do not duplicate mocks between mock directories", "category": "testing"},
        {"id": "L002", "lesson": "Use toBeVisible for element visibility assertions", "category": "testing"},
    ]

    results = search_lessons(
        tokenize("assert the button is visible in tests"), lessons, build_idf(lessons)
    )

    assert results[0]["id"] == "L002"


@pytest.mark.parametrize(
    "sizes,limit,budget,expected",
    [
        ([100, 100, 100, 100], 3, 1200, 3),
        ([900, 900, 100], 3, 1200, 2),
        ([2000], 3, 1200, 1),
    ],
)
def test_injection_budget_caps_count_and_chars(sizes, limit, budget, expected):
    results = [{"id": f"L{i}", "lesson": "x" * n} for i, n in enumerate(sizes)]

    kept = apply_injection_budget(results, limit=limit, char_budget=budget)

    assert len(kept) == expected
    assert kept[0]["id"] == "L0"


@pytest.mark.parametrize(
    "prompt",
    ["what's a good name for my boat", "explain how photosynthesis works"],
)
def test_one_shared_common_word_does_not_match(prompt):
    lessons = [
        {"id": "L001", "lesson": "Ensure test names accurately reflect what the test asserts", "category": "testing"},
        {"id": "L002", "lesson": "Remove comments when the code is self-explanatory and works", "category": "pr_review"},
        {"id": "L003", "lesson": "Use semantic theme colors instead of hex values", "category": "code_edit"},
    ]

    assert search_lessons(tokenize(prompt), lessons, build_idf(lessons)) == []

