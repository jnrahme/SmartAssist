import io
import json
import time
from unittest.mock import patch

from smartassist.store import (
    add_lesson,
    append_feedback_event,
    search_projection_documents,
)


class TestSearchProjectionDocuments:
    def test_source_type_filter_returns_only_events(self, set_data_dir):
        storage = set_data_dir / "data"
        add_lesson(
            storage,
            "Always use semantic theme tokens instead of hardcoded hex values",
            "code_edit",
        )
        append_feedback_event(
            storage,
            {
                "timestamp": time.time(),
                "signal": "correction",
                "category": "code_edit",
                "intensity": 4,
                "correction": "Use semantic theme tokens instead of hardcoded hex values",
                "context": "Last time hardcoded styles broke theme switching",
            },
        )

        results, meta = search_projection_documents(
            storage,
            "semantic theme tokens hardcoded hex values",
            top_k=5,
            source_types=["event"],
        )

        assert results
        assert all(result["source_type"] == "event" for result in results)
        assert meta["source_type_filter_used"] == ["event"]


class TestPromptInjectDualMemory:
    def test_hook_includes_episodic_memory_even_when_lessons_dominate(
        self, set_data_dir, capsys
    ):
        from smartassist.hooks.prompt_inject import main

        storage = set_data_dir / "data"
        add_lesson(
            storage,
            "Always use semantic theme tokens instead of hardcoded hex values in styles",
            "code_edit",
        )
        add_lesson(
            storage,
            "Use semantic color tokens from the theme in all styles",
            "code_edit",
        )
        add_lesson(
            storage,
            "Keep style constants in a dedicated styles.ts file",
            "code_edit",
        )
        append_feedback_event(
            storage,
            {
                "timestamp": time.time(),
                "signal": "correction",
                "category": "code_edit",
                "intensity": 4,
                "correction": "Use semantic theme tokens instead of hardcoded hex values",
                "context": "Last time hardcoded styles broke theme switching",
            },
        )

        hook_input = json.dumps(
            {
                "prompt": "did you use semantic theme tokens in the styles file or hardcoded hex values",
                "session_id": "memalign-test",
            }
        )
        with patch("sys.stdin", io.StringIO(hook_input)):
            main()

        captured = capsys.readouterr()
        output = json.loads(captured.out.strip())
        context = output["hookSpecificOutput"]["additionalContext"]
        assert "Project-specific rules (apply these):" in context
        assert "Past corrections on similar work:" in context
        assert "theme switching" in context
