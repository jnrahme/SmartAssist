"""Tests for smartassist.tools.cleanup_and_vectorize filtering logic."""

from smartassist.tools.cleanup_and_vectorize import (
    normalize_text,
    is_skip_pattern,
    get_dedup_key,
    clean_correction_text,
    format_text_for_vector,
    is_pure_question,
    is_code_fragment,
    is_at_mention_noise,
    convert_question_to_lesson,
    sanitize_to_lesson,
    is_screenshot_or_image,
    is_conversational_noise,
    is_defensive_explanation,
    is_acknowledgment,
    is_why_question,
    is_narrative_or_status,
    is_observation_not_lesson,
    is_scope_discussion,
    is_negative_opinion,
    is_non_imperative_comment,
)


class TestNormalization:
    def test_strips_whitespace_and_lowercases(self):
        assert normalize_text("  Hello  World  ") == "hello world"

    def test_removes_punctuation(self):
        assert normalize_text("Use `yarn test`!") == "use yarn test"


class TestSkipPatterns:
    def test_matches_done(self):
        assert is_skip_pattern("Done - fixed in PR #123")

    def test_matches_lgtm(self):
        assert is_skip_pattern("LGTM")

    def test_matches_addressed(self):
        assert is_skip_pattern("This has been addressed already")

    def test_does_not_match_real_lesson(self):
        assert not is_skip_pattern("Use semantic colors instead of hardcoded hex values")

    def test_matches_already_fixed(self):
        assert is_skip_pattern("Already fixed - renamed the variable")

    def test_matches_ack_patterns(self):
        assert is_skip_pattern("Good point, there isn't a follow-up ticket yet")
        assert is_skip_pattern("I'll move it out to its own useEffect")
        assert is_skip_pattern("Will fix this in the next commit")

    def test_matches_new_patterns(self):
        assert is_skip_pattern("Good call, that's a better approach")
        assert is_skip_pattern("Fair point about the naming convention")
        assert is_skip_pattern("You're right about that one")
        assert is_skip_pattern("Agreed, let's do it that way")


class TestDedupKey:
    def test_same_for_similar_text(self):
        k1 = get_dedup_key("Use semantic colors from theme")
        k2 = get_dedup_key("use semantic colors from theme")
        assert k1 == k2

    def test_different_for_different_text(self):
        k1 = get_dedup_key("Use semantic colors from theme")
        k2 = get_dedup_key("Never use snapshot tests")
        assert k1 != k2


class TestCleanCorrectionText:
    def test_returns_none_for_empty(self):
        assert clean_correction_text({"correction": ""}) is None

    def test_returns_none_for_short(self):
        assert clean_correction_text({"correction": "ok fix"}) is None

    def test_returns_none_for_skip_pattern(self):
        assert clean_correction_text({"correction": "Done - addressed in commit abc"}) is None

    def test_returns_text_for_valid(self):
        result = clean_correction_text({
            "correction": "Use color tokens from the theme instead of hardcoded hex values in styles"
        })
        assert result is not None
        assert "color" in result

    def test_filters_screenshot(self):
        assert clean_correction_text({"correction": '<img src="screenshot.png" />'}) is None

    def test_filters_defensive(self):
        result = clean_correction_text({
            "correction": "This is expected behavior for the modal component, it should dismiss on back press"
        })
        assert result is None

    def test_applies_sanitize(self):
        result = clean_correction_text({
            "correction": "I think we should use semantic colors from the theme instead of hardcoded hex values"
        })
        assert result is not None
        assert result.startswith("Use semantic colors")

    def test_filters_why_questions(self):
        result = clean_correction_text({
            "correction": "Why are we doing all this calculation if we can just pass a renderProp?"
        })
        assert result is None

    def test_filters_i_narrative(self):
        result = clean_correction_text({
            "correction": "I tried many times but tests were failing, will revisit later with a fresh approach"
        })
        assert result is None


class TestFormatText:
    def test_has_category_and_lesson(self):
        text = format_text_for_vector("code_edit", "Use semantic colors instead of hardcoded values")
        assert text.startswith("[code_edit]")
        assert "Use semantic colors" in text

    def test_different_category(self):
        text = format_text_for_vector("testing", "Always place jest.mock() before imports")
        assert text == "[testing] Always place jest.mock() before imports"


class TestFilterFunctions:
    def test_is_pure_question_detects_questions(self):
        assert is_pure_question("why are we doing this?")
        assert is_pure_question("Is this working?")

    def test_is_pure_question_keeps_suggestions(self):
        assert not is_pure_question("can we use colors from the theme?")
        assert not is_pure_question("shouldn't this be in constants?")

    def test_is_code_fragment_detects_backticks(self):
        assert is_code_fragment("```suggestion\n}")
        assert not is_code_fragment("Use ```semantic colors``` from theme")

    def test_is_at_mention_noise_detects_short(self):
        assert is_at_mention_noise("@marcos please check")
        assert not is_at_mention_noise(
            "When using @react-navigation/native, always wrap screens in "
            "NavigationContainer for proper lifecycle management"
        )

    def test_convert_question_to_lesson(self):
        assert convert_question_to_lesson("can we use colors from the theme?") == "Use colors from the theme"

    def test_is_screenshot_detects_img_tag(self):
        assert is_screenshot_or_image('<img src="foo.png" />')
        assert is_screenshot_or_image('![screenshot](img.png)')

    def test_is_conversational_noise_detects_filler(self):
        assert is_conversational_noise("hmm I'm not sure about that approach at all")
        assert not is_conversational_noise("Use semantic colors from the theme")

    def test_is_defensive_explanation_detects_defenses(self):
        assert is_defensive_explanation("This is expected behavior for the modal component")
        assert not is_defensive_explanation("Use semantic colors from the theme")

    def test_is_acknowledgment_detects_ack(self):
        assert is_acknowledgment("Thank you for review, all changes look great")
        assert not is_acknowledgment("Use semantic colors from the theme")

    def test_is_why_question(self):
        assert is_why_question("Why are we using random here?")
        assert not is_why_question("Use semantic colors from the theme")

    def test_is_narrative(self):
        assert is_narrative_or_status("I removed useNormalizedLikedBoats, so it's irrelevant")
        assert not is_narrative_or_status("Use semantic colors from the theme")

    def test_is_observation(self):
        assert is_observation_not_lesson("Looks like props drilling :D")
        assert not is_observation_not_lesson("Use semantic colors from the theme")

    def test_is_scope_discussion(self):
        assert is_scope_discussion("This is out of scope for this ticket")
        assert not is_scope_discussion("Use semantic colors from the theme")

    def test_is_negative_opinion(self):
        assert is_negative_opinion("I don't think we need to define all mocks")
        assert not is_negative_opinion("Use semantic colors from the theme")


class TestNonImperativeFilter:
    def test_filters_i_statements(self):
        assert is_non_imperative_comment("I tried many times but tests were failing")
        assert is_non_imperative_comment("I removed the local state from this component")

    def test_filters_questions(self):
        assert is_non_imperative_comment("Do we need this console.error in production?")
        assert is_non_imperative_comment("Are we storing the whole boat in async storage?")

    def test_keeps_prescriptive_this(self):
        assert not is_non_imperative_comment(
            "This useMemo is not necessary because it doesn't apply any of these rules"
        )

    def test_filters_this_without_advice(self):
        assert is_non_imperative_comment("This was just to make tests pass Husky")

    def test_filters_trailing_question(self):
        assert is_non_imperative_comment("Bootsplash only needs sharp and it comes with different version?")


class TestSanitizeToLesson:
    def test_strips_hedged_suggestion(self):
        assert sanitize_to_lesson("I think we should use semantic colors") == "Use semantic colors"

    def test_strips_please(self):
        assert sanitize_to_lesson("please don't iterate over arrays") == "Don't iterate over arrays"

    def test_strips_yeah_but(self):
        assert sanitize_to_lesson("yeah but we need to check config.params") == "Check config.params"

    def test_strips_github_urls(self):
        result = sanitize_to_lesson("See https://github.com/org/repo/pull/1 for details on using theme colors")
        assert "github.com" not in result
        assert "theme colors" in result

    def test_capitalizes_first_letter(self):
        result = sanitize_to_lesson("also, use theme tokens for spacing")
        assert result[0].isupper()

    def test_preserves_already_imperative(self):
        result = sanitize_to_lesson("Use semantic colors from the theme instead of hardcoded hex values")
        assert result == "Use semantic colors from the theme instead of hardcoded hex values"
