"""Deterministic QA scenarios for SmartAssist runtime contracts."""

from __future__ import annotations

import io
import json
import os
import re
import subprocess
import sys
import time
from contextlib import redirect_stdout
from dataclasses import asdict, dataclass, field
from pathlib import Path
from types import SimpleNamespace
from typing import Any, Callable
from unittest.mock import patch

from smartassist import mcp_server
from smartassist.boundary_packs import get_boundary_pack_path
from smartassist.config import atomic_write_json
from smartassist.gates import (
    build_pretool_hook_output,
    evaluate_pretool_gate,
    get_prevention_rules_path,
)
from smartassist.hooks import commit_hook, prompt_inject, seed_from_claudemd, session_end, session_start
from smartassist.lesson_feedback import create_lesson_from_feedback
from smartassist.qa.artifacts import read_jsonl_if_exists, snapshot_storage
from smartassist.qa.fixtures import ScenarioSandbox
from smartassist.store import (
    append_feedback_event,
    get_store_db_path,
    list_lessons,
    save_reliabilities_dict,
    search_projection_documents,
)
from smartassist.tools import cleanup_and_vectorize
from smartassist.tools.doctor import collect_doctor_report


@dataclass
class ScenarioAssertion:
    name: str
    passed: bool
    detail: str


@dataclass
class ScenarioResult:
    name: str
    description: str
    success: bool
    assertions: list[ScenarioAssertion] = field(default_factory=list)
    steps: list[dict[str, Any]] = field(default_factory=list)
    before_state: dict[str, Any] = field(default_factory=dict)
    after_state: dict[str, Any] = field(default_factory=dict)
    extras: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "description": self.description,
            "success": self.success,
            "assertions": [asdict(item) for item in self.assertions],
            "steps": self.steps,
            "before_state": self.before_state,
            "after_state": self.after_state,
            "extras": self.extras,
        }


@dataclass(frozen=True)
class ScenarioDefinition:
    name: str
    description: str
    live_claude: bool
    runner: Callable[[ScenarioSandbox], ScenarioResult]


class _FakeEmbedding:
    def __init__(self, value: float) -> None:
        self.value = value

    def tolist(self) -> list[float]:
        return [self.value, self.value + 0.5, self.value + 1.0]


class _FakeSentenceTransformer:
    def __init__(self, model_name: str) -> None:
        self.model_name = model_name

    def encode(self, texts, show_progress_bar: bool = False, batch_size: int = 64):  # noqa: ARG002
        if isinstance(texts, str):
            return _FakeEmbedding(float(len(texts)))
        return [_FakeEmbedding(float(index + 1)) for index, _ in enumerate(texts)]


class _FakeLanceTable:
    def __init__(self, rows: list[dict[str, Any]] | None = None) -> None:
        self.rows = list(rows or [])

    def count_rows(self) -> int:
        return len(self.rows)

    def create_fts_index(self, *args, **kwargs) -> None:  # noqa: ARG002
        return None

    def search(self, vector) -> _FakeLanceTable:  # noqa: ARG002
        return self

    def limit(self, n: int) -> _FakeLanceTable:  # noqa: ARG002
        return self

    def to_list(self) -> list[dict[str, Any]]:
        return list(self.rows)


class _FakeLanceDB:
    def __init__(self) -> None:
        self.rows: list[dict[str, Any]] = []
        self._table = _FakeLanceTable([])

    def create_table(self, name: str, data, mode: str = "overwrite") -> _FakeLanceTable:  # noqa: ARG002
        if hasattr(data, "to_pylist"):
            rows = data.to_pylist()
        else:
            rows = list(data)
        self.rows = rows
        self._table = _FakeLanceTable(rows)
        return self._table

    def open_table(self, name: str) -> _FakeLanceTable:  # noqa: ARG002
        return self._table


def _record_step(steps: list[dict[str, Any]], title: str, detail: str, **data: Any) -> None:
    entry: dict[str, Any] = {"title": title, "detail": detail}
    if data:
        entry["data"] = data
    steps.append(entry)


def _expect(
    assertions: list[ScenarioAssertion],
    name: str,
    condition: bool,
    detail: str,
    failure_detail: str | None = None,
) -> None:
    assertions.append(
        ScenarioAssertion(
            name=name,
            passed=bool(condition),
            detail=detail if condition or failure_detail is None else failure_detail,
        )
    )


def _invoke_prompt_hook(payload: dict[str, Any]) -> dict[str, Any] | None:
    stream_in = io.StringIO(json.dumps(payload))
    stream_in.isatty = lambda: False  # type: ignore[attr-defined]
    stream_out = io.StringIO()
    with patch("sys.stdin", stream_in):
        with redirect_stdout(stream_out):
            prompt_inject.main()
    output = stream_out.getvalue().strip()
    return json.loads(output) if output else None


def _git(*args: str, cwd: Path) -> None:
    subprocess.run(["git", *args], cwd=cwd, check=True, capture_output=True, text=True)


def _capture_stdout(func: Callable[..., Any], *args: Any, **kwargs: Any) -> tuple[Any, str]:
    stream = io.StringIO()
    with redirect_stdout(stream):
        result = func(*args, **kwargs)
    return result, stream.getvalue()


def _hook_context(hook_output: dict[str, Any] | None) -> str:
    if not hook_output:
        return ""
    return str(hook_output.get("hookSpecificOutput", {}).get("additionalContext", "") or "")


def _parse_hook_context(context: str) -> dict[str, Any]:
    parsed: dict[str, list[dict[str, Any]]] = {"semantic": [], "episodic": []}
    section: str | None = None

    for line in context.splitlines():
        stripped = line.strip()
        if stripped == "Project-specific rules (apply these):":
            section = "semantic"
            continue
        if stripped == "Past corrections on similar work:":
            section = "episodic"
            continue
        if not stripped.startswith("- "):
            continue

        item_text = stripped[2:].strip()
        if section == "semantic":
            match = re.match(r"\[(?P<id>[^\]]+)\] \[(?P<category>[^\]]+)\] (?P<lesson>.+)", item_text)
            if match:
                parsed["semantic"].append(
                    {
                        "id": match.group("id"),
                        "category": match.group("category"),
                        "lesson": match.group("lesson"),
                    }
                )
        elif section == "episodic":
            match = re.match(r"\[(?P<category>[^\]]+)\] (?P<lesson>.+)", item_text)
            if match:
                parsed["episodic"].append(
                    {
                        "category": match.group("category"),
                        "lesson": match.group("lesson"),
                    }
                )

    return {"semantic": parsed["semantic"], "episodic": parsed["episodic"], "raw": context}


def _parse_mcp_output(output: str) -> dict[str, Any]:
    parsed: dict[str, list[dict[str, Any]]] = {"semantic": [], "episodic": []}
    section: str | None = None
    current: dict[str, Any] | None = None

    def _flush_current() -> None:
        nonlocal current
        if current and section in parsed:
            parsed[section].append(current)
        current = None

    for line in output.splitlines():
        stripped = line.strip()
        if stripped == "Project Rules (semantic memory):":
            _flush_current()
            section = "semantic"
            continue
        if stripped == "Past Corrections (episodic memory):":
            _flush_current()
            section = "episodic"
            continue

        match = re.match(r"\[(?P<category>[^\]]+)\] \(relevance: (?P<relevance>\d+)%\)", stripped)
        if match:
            _flush_current()
            current = {
                "category": match.group("category"),
                "relevance_pct": int(match.group("relevance")),
            }
            continue

        if current is None:
            continue

        if stripped.startswith("Lesson:"):
            current["lesson"] = stripped.removeprefix("Lesson:").strip()
        elif stripped.startswith("Context:"):
            current["context"] = stripped.removeprefix("Context:").strip()

    _flush_current()
    return {"semantic": parsed["semantic"], "episodic": parsed["episodic"], "raw": output}


def _build_search_playground(storage_path: Path, *, active_lessons: list[dict[str, Any]]) -> dict[str, Any]:
    sample_queries = [
        {
            "id": "theme_tokens",
            "label": "Theme tokens",
            "query": "semantic ocean tokens for dashboard button styles",
            "focus": "Shows the theme/style lesson surfacing as soon as the query is specific enough.",
        },
        {
            "id": "validator_tests",
            "label": "Validator tests",
            "query": "table driven tests for api validator edge cases",
            "focus": "Shows the testing lesson taking over once API-validator wording appears.",
        },
        {
            "id": "commit_hygiene",
            "label": "Commit hygiene",
            "query": "remove debug statements before commit",
            "focus": "Shows a git/code-hygiene lesson instead of the style/testing lessons.",
        },
        {
            "id": "no_match",
            "label": "No match",
            "query": "quantum physics dark matter theory",
            "focus": "Shows the empty state when a prompt has no meaningful project overlap.",
        },
    ]

    def _trace_for_prefix(sample_id: str, prefix: str, index: int) -> dict[str, Any]:
        hook_output = _invoke_prompt_hook(
            {
                "prompt": prefix,
                "session_id": f"qa-playground-{sample_id}-{index}",
            }
        )
        hook_context = _hook_context(hook_output)
        mcp_output = mcp_server.rag_search(prefix, top_k=3)
        projection_results, search_meta = search_projection_documents(storage_path, prefix, top_k=3)
        return {
            "prefix": prefix,
            "prefix_lower": prefix.strip().lower(),
            "hook": _parse_hook_context(hook_context),
            "mcp": _parse_mcp_output(mcp_output),
            "projection": {
                "count": len(projection_results),
                "search_backend": search_meta.get("search_backend"),
            },
        }

    samples = []
    for sample in sample_queries:
        traces = [
            {
                "prefix": "",
                "prefix_lower": "",
                "hook": {"semantic": [], "episodic": [], "raw": ""},
                "mcp": {"semantic": [], "episodic": [], "raw": ""},
                "projection": {"count": 0, "search_backend": None},
            }
        ]
        for index in range(1, len(sample["query"]) + 1):
            traces.append(_trace_for_prefix(sample["id"], sample["query"][:index], index))

        samples.append(
            {
                **sample,
                "query_lower": sample["query"].lower(),
                "traces": traces,
            }
        )

    corpus = [
        {
            "id": lesson.get("id", ""),
            "category": lesson.get("category", "unknown"),
            "lesson": lesson.get("lesson", ""),
        }
        for lesson in active_lessons
    ]

    return {
        "title": "Search Playground",
        "description": (
            "Type through deterministic sample prompts and watch the exact Hook and MCP retrieval lanes "
            "pick up project rules and past corrections from the same QA artifact bundle."
        ),
        "instructions": "Pick a sample query, then type through it to replay the captured Hook and MCP results side by side.",
        "default_sample_id": sample_queries[0]["id"],
        "samples": samples,
        "corpus": corpus,
    }


def _read_comparison_entries(storage_path: Path) -> list[dict[str, Any]]:
    return [row for row in read_jsonl_if_exists(storage_path / "lesson_comparison.jsonl") if isinstance(row, dict)]


def _write_legacy_json(path: Path, payload: Any) -> None:
    atomic_write_json(path, payload)


def _scenario_hook_mcp_retrieval_consistency(sandbox: ScenarioSandbox) -> ScenarioResult:
    steps: list[dict[str, Any]] = []
    assertions: list[ScenarioAssertion] = []

    with sandbox.activate():
        before_state = snapshot_storage(sandbox.storage_path)
        _record_step(steps, "snapshot_before", "Captured baseline runtime state")

        primary_id, primary_text = create_lesson_from_feedback(
            "Always use semantic ocean tokens instead of hardcoded hex values in dashboard button styles",
            "negative",
            [],
        )
        secondary_id, secondary_text = create_lesson_from_feedback(
            "Always write table-driven tests for API validator edge cases before merging",
            "negative",
            [],
        )
        tertiary_id, tertiary_text = create_lesson_from_feedback(
            "Always remove debug statements before commit and use [TICKET-XXX] prefixes in commit messages",
            "negative",
            [],
        )
        _record_step(
            steps,
            "seed_lessons",
            "Created three distinct active lessons through the shared feedback path",
            primary_id=primary_id,
            secondary_id=secondary_id,
            tertiary_id=tertiary_id,
        )

        query = "semantic ocean tokens for dashboard button styles"
        hook_output = _invoke_prompt_hook({"prompt": query, "session_id": "qa-consistency"})
        rag_output = mcp_server.rag_search(query, top_k=3)
        search_results, search_meta = search_projection_documents(sandbox.storage_path, query, top_k=3)
        active_lessons = list_lessons(sandbox.storage_path)
        search_playground = _build_search_playground(
            sandbox.storage_path,
            active_lessons=active_lessons,
        )
        _record_step(
            steps,
            "query_runtime_paths",
            "Queried hook injection, MCP retrieval, and canonical search projection with the same query",
            hook_output=hook_output,
            rag_output=rag_output,
            search_meta=search_meta,
        )

        after_state = snapshot_storage(sandbox.storage_path)

    active_lessons = after_state["canonical"]["active_lessons"]
    hook_context = _hook_context(hook_output)

    _expect(
        assertions,
        "primary_lesson_active",
        any(lesson["id"] == primary_id for lesson in active_lessons),
        "Primary lesson exists in the active corpus",
        "Primary lesson is missing from the active corpus",
    )
    _expect(
        assertions,
        "hook_returns_primary_lesson",
        bool(primary_id and primary_text and primary_id in hook_context and primary_text in hook_context),
        "Prompt hook injected the primary lesson",
        "Prompt hook did not inject the primary lesson",
    )
    _expect(
        assertions,
        "mcp_returns_primary_lesson",
        bool(primary_text and primary_text in rag_output),
        "MCP rag_search returned the same primary lesson",
        "MCP rag_search did not return the expected lesson text",
    )
    _expect(
        assertions,
        "projection_returns_primary_lesson",
        any(result["source_type"] == "lesson" and result["source_id"] == primary_id for result in search_results),
        "Canonical search projection includes the primary lesson",
        "Canonical search projection is missing the primary lesson",
    )
    _expect(
        assertions,
        "hook_omits_unrelated_lesson",
        bool(secondary_text and secondary_text not in hook_context),
        "Hook retrieval stays focused on the relevant lesson",
        "Hook retrieval included an unrelated lesson for the query",
    )

    return ScenarioResult(
        name="hook_mcp_retrieval_consistency",
        description="Hook injection and MCP retrieval surface the same active lesson for the same query.",
        success=all(item.passed for item in assertions),
        assertions=assertions,
        steps=steps,
        before_state=before_state,
        after_state=after_state,
        extras={
            "hook_output": hook_output,
            "rag_output": rag_output,
            "search_meta": search_meta,
            "search_playground": search_playground,
        },
    )


def _scenario_feedback_creates_active_lesson(sandbox: ScenarioSandbox) -> ScenarioResult:
    steps: list[dict[str, Any]] = []
    assertions: list[ScenarioAssertion] = []

    with sandbox.activate():
        before_state = snapshot_storage(sandbox.storage_path)
        _record_step(steps, "snapshot_before", "Captured baseline runtime state")

        lesson_id, lesson_text = create_lesson_from_feedback(
            "Use semantic theme tokens instead of hardcoded colors in components",
            "negative",
            [],
        )
        _record_step(
            steps,
            "create_feedback_lesson",
            "Created a feedback-derived lesson through the shared lesson path",
            lesson_id=lesson_id,
            lesson_text=lesson_text,
        )

        search_results, search_meta = search_projection_documents(
            sandbox.storage_path,
            "semantic theme colors",
        )
        hook_output = _invoke_prompt_hook(
            {"prompt": "Please refactor the theme colors to use semantic tokens", "session_id": "qa-feedback"}
        )
        _record_step(
            steps,
            "query_runtime_paths",
            "Ran the prompt hook and canonical search projection against the new lesson",
            hook_output=hook_output,
            search_meta=search_meta,
        )

        after_state = snapshot_storage(sandbox.storage_path)

    active_lessons = after_state["canonical"]["active_lessons"]
    feedback_events = after_state["canonical"]["feedback_events"]
    additional_context = _hook_context(hook_output)

    _expect(assertions, "lesson_created", lesson_id is not None, "Feedback lesson was created", "Feedback lesson was not created")
    _expect(
        assertions,
        "lesson_is_active",
        any(lesson["id"] == lesson_id for lesson in active_lessons),
        "Created lesson exists in the active corpus",
        "Created lesson is missing from the active corpus",
    )
    _expect(
        assertions,
        "feedback_event_recorded",
        any(event["correction"] == lesson_text for event in feedback_events),
        "Feedback event was recorded in canonical history",
        "Feedback event was not recorded in canonical history",
    )
    _expect(
        assertions,
        "search_projection_updated",
        any(result["source_type"] == "lesson" and result["source_id"] == lesson_id for result in search_results),
        "Search projection returns the created lesson",
        "Search projection did not return the created lesson",
    )
    _expect(
        assertions,
        "prompt_injection_uses_new_lesson",
        bool(lesson_id and lesson_id in additional_context and lesson_text and lesson_text in additional_context),
        "Prompt injection includes the created lesson",
        "Prompt injection did not include the created lesson",
    )

    return ScenarioResult(
        name="feedback_creates_active_lesson",
        description="Feedback-derived lessons become active, searchable, and injectable.",
        success=all(item.passed for item in assertions),
        assertions=assertions,
        steps=steps,
        before_state=before_state,
        after_state=after_state,
        extras={"hook_output": hook_output, "search_meta": search_meta},
    )


def _scenario_compare_lesson_logs_without_storage(sandbox: ScenarioSandbox) -> ScenarioResult:
    steps: list[dict[str, Any]] = []
    assertions: list[ScenarioAssertion] = []

    with sandbox.activate():
        before_state = snapshot_storage(sandbox.storage_path)
        before_active_count = len(before_state["canonical"]["active_lessons"])
        _record_step(steps, "snapshot_before", "Captured baseline runtime state", active_lessons=before_active_count)

        lesson_text = (
            "Always route analytics events through the centralized telemetry helper so metadata stays consistent"
        )
        result = mcp_server.compare_lesson(
            lesson=lesson_text,
            category="code_edit",
            sentiment="negative",
            context="User asked for centralized telemetry helpers",
        )
        comparison_entries = _read_comparison_entries(sandbox.storage_path)
        search_results, _search_meta = search_projection_documents(
            sandbox.storage_path,
            "centralized telemetry helper metadata consistent",
            top_k=3,
        )
        _record_step(
            steps,
            "compare_lesson",
            "Logged a Claude comparison lesson without storing it in the active knowledge base",
            result=result,
            comparison_entries=len(comparison_entries),
        )

        after_state = snapshot_storage(sandbox.storage_path)

    after_active_count = len(after_state["canonical"]["active_lessons"])
    last_entry = comparison_entries[-1] if comparison_entries else {}

    _expect(
        assertions,
        "comparison_acknowledged_not_stored",
        "not stored" in result.lower(),
        "compare_lesson explicitly reports that the draft was not stored",
        "compare_lesson did not report the expected not-stored A/B status",
    )
    _expect(
        assertions,
        "active_lesson_count_unchanged",
        after_active_count == before_active_count,
        "Active lesson count stayed unchanged after compare_lesson",
        "compare_lesson unexpectedly changed the active lesson count",
    )
    _expect(
        assertions,
        "comparison_file_recorded_entry",
        bool(last_entry and last_entry.get("source") == "claude" and last_entry.get("lesson_text") == lesson_text),
        "Comparison file recorded the Claude draft",
        "Comparison file did not record the expected Claude draft",
    )
    _expect(
        assertions,
        "comparison_draft_not_searchable",
        all(result_row.get("source_type") != "lesson" for result_row in search_results),
        "Comparison-only draft did not enter searchable runtime state",
        "Comparison-only draft leaked into searchable runtime state",
    )

    return ScenarioResult(
        name="compare_lesson_logs_without_storage",
        description="compare_lesson records A/B artifacts without mutating active retrieval state.",
        success=all(item.passed for item in assertions),
        assertions=assertions,
        steps=steps,
        before_state=before_state,
        after_state=after_state,
        extras={"result": result, "comparison_entries": comparison_entries},
    )


def _scenario_commit_correction_promotes_active_lesson(sandbox: ScenarioSandbox) -> ScenarioResult:
    steps: list[dict[str, Any]] = []
    assertions: list[ScenarioAssertion] = []

    with sandbox.activate():
        before_state = snapshot_storage(sandbox.storage_path)
        _record_step(steps, "snapshot_before", "Captured baseline runtime state")

        _git("init", cwd=sandbox.project_root)
        _git("config", "user.email", "qa@example.com", cwd=sandbox.project_root)
        _git("config", "user.name", "SmartAssist QA", cwd=sandbox.project_root)
        _record_step(steps, "git_init", "Initialized isolated git repository")

        src_dir = sandbox.project_root / "src"
        src_dir.mkdir(parents=True, exist_ok=True)
        (src_dir / "app.ts").write_text(
            "export function renderTheme() {\n  return '#000';\n}\n",
            encoding="utf-8",
        )
        _git("add", "src/app.ts", cwd=sandbox.project_root)
        _git("commit", "-m", "[QA-001] baseline theme", cwd=sandbox.project_root)
        _record_step(steps, "baseline_commit", "Created a baseline commit so HEAD~1 diffs exist")

        (src_dir / "app.ts").write_text(
            "export function renderTheme() {\n  console.log('debug theme');\n  return '#fff';\n}\n",
            encoding="utf-8",
        )
        _git("add", "src/app.ts", cwd=sandbox.project_root)
        _git("commit", "-m", "fix theme colors", cwd=sandbox.project_root)
        _record_step(steps, "git_commit", "Committed a change with a missing ticket and debug statement")

        real_run = subprocess.run

        def _run_without_vectorizer(command, *args, **kwargs):
            if (
                isinstance(command, list)
                and len(command) >= 3
                and command[0] == sys.executable
                and command[1:3] == ["-m", "smartassist.hooks.vectorize_learnings"]
            ):
                return subprocess.CompletedProcess(command, 0, "", "")
            return real_run(command, *args, **kwargs)

        with patch(
            "smartassist.hooks.commit_hook.subprocess.run",
            side_effect=_run_without_vectorizer,
        ):
            commit_hook.capture_commit_lessons(verbose=False)
        _record_step(steps, "capture_commit", "Captured commit lessons through the real commit hook")

        lessons = list_lessons(sandbox.storage_path)
        hook_output = _invoke_prompt_hook(
            {"prompt": "Remove debug statements before committing code", "session_id": "qa-commit"}
        )
        _record_step(steps, "prompt_hook", "Ran the prompt hook against commit-promoted lessons", hook_output=hook_output)

        after_state = snapshot_storage(sandbox.storage_path)

    active_lessons = after_state["canonical"]["active_lessons"]
    feedback_events = after_state["canonical"]["feedback_events"]
    additional_context = _hook_context(hook_output)
    promoted_texts = [lesson["lesson"] for lesson in active_lessons]

    _expect(
        assertions,
        "commit_feedback_recorded",
        len(feedback_events) >= 1,
        "Commit hook recorded feedback events",
        "Commit hook did not record feedback events",
    )
    _expect(
        assertions,
        "commit_promoted_active_lesson",
        any("Remove debug statements" in text or "Use format: [TICKET-XXX] Description" in text for text in promoted_texts),
        "Commit-derived correction was promoted into the active corpus",
        "No expected commit-derived correction was promoted into the active corpus",
    )
    _expect(
        assertions,
        "commit_prompt_injection",
        "Remove debug statements" in additional_context or "Use format: [TICKET-XXX] Description" in additional_context,
        "Prompt injection can see commit-promoted lessons",
        "Prompt injection could not see the promoted commit lesson",
    )
    _expect(
        assertions,
        "store_exists",
        get_store_db_path(sandbox.storage_path).exists(),
        "Canonical SQLite store exists for commit scenario",
        "Canonical SQLite store is missing for commit scenario",
    )

    return ScenarioResult(
        name="commit_correction_promotes_active_lesson",
        description="Commit-derived corrections become active lessons visible to future prompts.",
        success=all(item.passed for item in assertions),
        assertions=assertions,
        steps=steps,
        before_state=before_state,
        after_state=after_state,
        extras={"active_lessons": lessons, "hook_output": hook_output},
    )


def _scenario_session_dedup_prevents_repeat_injection(sandbox: ScenarioSandbox) -> ScenarioResult:
    steps: list[dict[str, Any]] = []
    assertions: list[ScenarioAssertion] = []

    with sandbox.activate():
        lesson_id, lesson_text = create_lesson_from_feedback(
            "Always use semantic status tokens instead of hardcoded colors in dashboard alerts",
            "negative",
            [],
        )
        before_state = snapshot_storage(sandbox.storage_path)
        _record_step(steps, "snapshot_before", "Seeded one lesson and captured baseline state", lesson_id=lesson_id)

        payload = {"prompt": "Use semantic status tokens in dashboard alerts", "session_id": "qa-dedup"}
        first_output = _invoke_prompt_hook(payload)
        second_output = _invoke_prompt_hook(payload)
        _record_step(
            steps,
            "repeat_prompt_same_session",
            "Ran the same prompt twice in the same session to verify session deduplication",
            first_output=first_output,
            second_output=second_output,
        )

        after_state = snapshot_storage(sandbox.storage_path)

    session_export = after_state["exports"].get("rag_session_state.json") or {}
    first_context = _hook_context(first_output)

    _expect(
        assertions,
        "first_prompt_injected_lesson",
        bool(lesson_id and lesson_text and lesson_id in first_context and lesson_text in first_context),
        "First prompt injection included the lesson",
        "First prompt injection did not include the expected lesson",
    )
    _expect(
        assertions,
        "second_prompt_suppressed",
        second_output is None,
        "Second prompt in the same session was suppressed by deduplication",
        "Second prompt still emitted injection output for the same session",
    )
    _expect(
        assertions,
        "session_state_recorded_injected_lesson",
        bool(lesson_id and session_export.get("session_id") == "qa-dedup" and lesson_id in session_export.get("injected_ids", [])),
        "Session state tracked the injected lesson ID",
        "Session state did not record the injected lesson ID",
    )

    return ScenarioResult(
        name="session_dedup_prevents_repeat_injection",
        description="Prompt injection does not re-inject the same lesson twice in one session.",
        success=all(item.passed for item in assertions),
        assertions=assertions,
        steps=steps,
        before_state=before_state,
        after_state=after_state,
        extras={"first_output": first_output, "second_output": second_output},
    )


def _scenario_demote_retires_lesson_everywhere(sandbox: ScenarioSandbox) -> ScenarioResult:
    steps: list[dict[str, Any]] = []
    assertions: list[ScenarioAssertion] = []

    with sandbox.activate():
        lesson_id, lesson_text = create_lesson_from_feedback(
            "Always use semantic theme tokens instead of hardcoded hex values in dashboard headers",
            "negative",
            [],
        )
        before_state = snapshot_storage(sandbox.storage_path)
        _record_step(steps, "snapshot_before", "Seeded one lesson and captured baseline state", lesson_id=lesson_id)

        with patch("smartassist.mcp_server.spawn_managed", lambda *args, **kwargs: None):
            demotion_results = [mcp_server.demote_lesson(lesson_id) for _ in range(3)]
        rag_output = mcp_server.rag_search("semantic theme tokens dashboard headers", top_k=3)
        hook_output = _invoke_prompt_hook(
            {"prompt": "Refactor dashboard headers to use theme tokens", "session_id": "qa-retire"}
        )
        search_results, _search_meta = search_projection_documents(
            sandbox.storage_path,
            "semantic theme tokens dashboard headers",
            top_k=3,
        )
        _record_step(
            steps,
            "demote_until_retired",
            "Demoted the lesson until auto-retirement removed it from active retrieval",
            demotion_results=demotion_results,
            rag_output=rag_output,
            hook_output=hook_output,
        )

        after_state = snapshot_storage(sandbox.storage_path)

    lesson_scores = after_state["canonical"]["lesson_scores"].get(lesson_id, {})
    inactive_record = next((lesson for lesson in after_state["canonical"]["lessons"] if lesson["id"] == lesson_id), {})
    active_ids = {lesson["id"] for lesson in after_state["canonical"]["active_lessons"]}

    _expect(
        assertions,
        "final_demote_reports_retirement",
        any("retired" in result.lower() for result in demotion_results),
        "Final demotion reported auto-retirement",
        "Demotion path never reported the expected retirement outcome",
    )
    _expect(
        assertions,
        "lesson_removed_from_active_corpus",
        lesson_id not in active_ids,
        "Retired lesson is no longer active",
        "Retired lesson still appears in the active corpus",
    )
    _expect(
        assertions,
        "lesson_score_marked_retired",
        bool(lesson_scores.get("retired") and lesson_scores.get("blocked")),
        "Lesson score reflects blocked + retired state",
        "Lesson score did not reflect retirement",
    )
    _expect(
        assertions,
        "lesson_row_retired",
        inactive_record.get("state") == "retired",
        "Lesson row state is retired",
        "Lesson row state did not change to retired",
    )
    _expect(
        assertions,
        "search_projection_excludes_retired_lesson",
        all(result["source_id"] != lesson_id for result in search_results),
        "Canonical search projection excludes the retired lesson",
        "Canonical search projection still contains the retired lesson",
    )
    _expect(
        assertions,
        "hook_stops_returning_retired_lesson",
        lesson_text not in _hook_context(hook_output),
        "Prompt hook stopped returning the retired active lesson",
        "Prompt hook still returned the retired active lesson",
    )
    _expect(
        assertions,
        "mcp_uses_event_memory_after_retire",
        bool(search_results and all(result["source_type"] == "event" for result in search_results)),
        "MCP retrieval falls back to historical event memory instead of the retired active lesson",
        "MCP retrieval did not use the expected event-memory fallback after retirement",
    )

    return ScenarioResult(
        name="demote_retires_lesson_everywhere",
        description="Repeated demotion retires a lesson and removes it from active retrieval everywhere.",
        success=all(item.passed for item in assertions),
        assertions=assertions,
        steps=steps,
        before_state=before_state,
        after_state=after_state,
        extras={"demotion_results": demotion_results, "rag_output": rag_output, "hook_output": hook_output},
    )


def _scenario_seed_creates_active_conventions(sandbox: ScenarioSandbox) -> ScenarioResult:
    steps: list[dict[str, Any]] = []
    assertions: list[ScenarioAssertion] = []

    sample = """
## Testing

- Always use renderWithProviders instead of plain render in tests

## Git

- Never force push to main or release branches

## Code Quality

- Use semantic color tokens from the theme instead of hardcoded hex values
""".strip()

    with sandbox.activate():
        (sandbox.project_root / "CLAUDE.md").write_text(sample, encoding="utf-8")
        before_state = snapshot_storage(sandbox.storage_path)
        _record_step(steps, "snapshot_before", "Wrote CLAUDE.md fixtures and captured baseline state")

        fake_vectorizer = lambda *args, **kwargs: SimpleNamespace(stdout='{"status":"vectorized"}', stderr="")
        with patch("smartassist.hooks.seed_from_claudemd.subprocess.run", side_effect=fake_vectorizer):
            _result, seed_output = _capture_stdout(seed_from_claudemd.seed_database)

        hook_output = _invoke_prompt_hook(
            {"prompt": "Replace hardcoded hex values with theme tokens in button styles", "session_id": "qa-seed"}
        )
        _record_step(
            steps,
            "seed_database",
            "Seeded SmartAssist from CLAUDE.md and queried the prompt hook",
            seed_output=seed_output,
            hook_output=hook_output,
        )

        after_state = snapshot_storage(sandbox.storage_path)

    lesson_texts = {lesson["lesson"] for lesson in after_state["canonical"]["active_lessons"]}
    hook_context = _hook_context(hook_output)

    _expect(
        assertions,
        "seed_promoted_active_lessons",
        len(after_state["canonical"]["active_lessons"]) >= 3,
        "Seeding promoted active lessons into the canonical store",
        "Seeding did not promote the expected active lessons",
    )
    _expect(
        assertions,
        "seeded_theme_rule_active",
        "Use semantic color tokens from the theme instead of hardcoded hex values" in lesson_texts,
        "Seeded theme convention became active immediately",
        "Expected seeded theme convention is missing from the active corpus",
    )
    _expect(
        assertions,
        "seeded_rule_injectable",
        "Use semantic color tokens from the theme instead of hardcoded hex values" in hook_context,
        "Prompt hook can inject seeded conventions immediately",
        "Prompt hook did not inject the seeded theme convention",
    )
    _expect(
        assertions,
        "seed_updates_feedback_history",
        len(after_state["canonical"]["feedback_events"]) >= 3,
        "Seeding also recorded feedback-history evidence",
        "Seeding did not record the expected feedback-history events",
    )

    return ScenarioResult(
        name="seed_creates_active_conventions",
        description="Seeding from CLAUDE.md creates active conventions that are immediately injectable.",
        success=all(item.passed for item in assertions),
        assertions=assertions,
        steps=steps,
        before_state=before_state,
        after_state=after_state,
        extras={"seed_output": seed_output, "hook_output": hook_output},
    )


def _scenario_gate_statistics_accumulate(sandbox: ScenarioSandbox) -> ScenarioResult:
    steps: list[dict[str, Any]] = []
    assertions: list[ScenarioAssertion] = []

    with sandbox.activate():
        before_state = snapshot_storage(sandbox.storage_path)
        _record_step(steps, "snapshot_before", "Captured baseline runtime state")

        deny_decision = evaluate_pretool_gate(
            "Bash",
            {"command": "git push --force origin HEAD"},
            storage_path=sandbox.storage_path,
            project_root=sandbox.project_root,
        )
        ask_decision = evaluate_pretool_gate(
            "Bash",
            {"command": "git push origin main"},
            storage_path=sandbox.storage_path,
            project_root=sandbox.project_root,
        )
        pass_decision = evaluate_pretool_gate(
            "Bash",
            {"command": "git status"},
            storage_path=sandbox.storage_path,
            project_root=sandbox.project_root,
        )
        deny_output = build_pretool_hook_output(deny_decision) if deny_decision else {}
        _record_step(
            steps,
            "gate_evaluations",
            "Evaluated deny, ask, and pass gate decisions through the real gate engine",
            deny_decision=deny_decision.__dict__ if deny_decision else None,
            ask_decision=ask_decision.__dict__ if ask_decision else None,
            pass_decision=pass_decision.__dict__ if pass_decision else None,
            deny_output=deny_output,
        )

        after_state = snapshot_storage(sandbox.storage_path)

    gate_stats = after_state["canonical"]["gate_stats"]

    _expect(
        assertions,
        "deny_rule_fired",
        bool(deny_decision and deny_decision.action == "deny" and deny_decision.gate_id == "deny-force-push"),
        "Force push was denied by the gate engine",
        "Force push was not denied by the expected gate rule",
    )
    _expect(
        assertions,
        "ask_rule_fired",
        bool(ask_decision and ask_decision.action == "ask" and ask_decision.gate_id == "ask-protected-branch-push"),
        "Protected-branch push was converted into an explicit ask decision",
        "Protected-branch push did not trigger the expected ask decision",
    )
    _expect(
        assertions,
        "safe_command_passed",
        pass_decision is None,
        "Safe command passed through the gate engine",
        "Safe command unexpectedly triggered a gate decision",
    )
    _expect(
        assertions,
        "gate_stats_accumulated",
        bool(gate_stats.get("blocked") == 1 and gate_stats.get("asked") == 1 and gate_stats.get("passed") == 1),
        "Gate stats accumulated deny, ask, and pass counts correctly",
        f"Unexpected gate stats: {gate_stats}",
    )
    _expect(
        assertions,
        "hook_output_includes_permission_decision",
        deny_output.get("hookSpecificOutput", {}).get("permissionDecision") == "deny",
        "Gate hook output renders a deny permission decision",
        "Gate hook output did not include the expected deny permission decision",
    )

    return ScenarioResult(
        name="gate_statistics_accumulate",
        description="PreToolUse gate decisions accumulate stats and produce the expected deny/ask behavior.",
        success=all(item.passed for item in assertions),
        assertions=assertions,
        steps=steps,
        before_state=before_state,
        after_state=after_state,
        extras={"deny_output": deny_output},
    )


def _scenario_boundary_pack_refreshes_after_repeated_negative_feedback(sandbox: ScenarioSandbox) -> ScenarioResult:
    steps: list[dict[str, Any]] = []
    assertions: list[ScenarioAssertion] = []

    repeated = (
        "Use feature branches and open a PR instead of pushing directly to main when working on repository changes."
    )

    with sandbox.activate():
        append_feedback_event(
            sandbox.storage_path,
            {
                "timestamp": 100.0,
                "signal": "correction",
                "category": "git",
                "intensity": 4,
                "correction": repeated,
                "context": "Repeated git safety mistake",
            },
        )
        append_feedback_event(
            sandbox.storage_path,
            {
                "timestamp": 200.0,
                "signal": "correction",
                "category": "git",
                "intensity": 4,
                "correction": repeated,
                "context": "Repeated git safety mistake",
            },
        )
        save_reliabilities_dict(
            sandbox.storage_path,
            {
                "git": {
                    "alpha": 1.0,
                    "beta": 4.0,
                    "last_updated": time.time(),
                    "total_samples": 5,
                }
            },
        )
        before_state = snapshot_storage(sandbox.storage_path)
        _record_step(steps, "snapshot_before", "Seeded repeated negative feedback and weak reliability state")

        with patch("smartassist.hooks.session_end.subprocess.run", return_value=None):
            _result, end_output = _capture_stdout(session_end.capture_session_learning)
        start_output = session_start.format_lessons_for_session()
        pack_path = get_boundary_pack_path(sandbox.storage_path)
        rules_path = get_prevention_rules_path(sandbox.storage_path)
        pack = json.loads(pack_path.read_text(encoding="utf-8")) if pack_path and pack_path.exists() else {}
        rules = json.loads(rules_path.read_text(encoding="utf-8")) if rules_path and rules_path.exists() else {}
        _record_step(
            steps,
            "boundary_pack_refresh",
            "Ran SessionEnd refresh and SessionStart rendering against repeated feedback",
            end_output=end_output,
            start_output=start_output,
        )

        after_state = snapshot_storage(sandbox.storage_path)

    promoted = pack.get("promoted_boundaries", [])

    _expect(
        assertions,
        "boundary_pack_written",
        bool(pack_path and pack_path.exists() and promoted),
        "Boundary pack was written with promoted boundaries",
        "Boundary pack was not written or contained no promoted boundaries",
    )
    _expect(
        assertions,
        "session_end_reports_refresh",
        "Updated boundary pack:" in end_output,
        "SessionEnd reported the boundary-pack refresh",
        "SessionEnd output did not report the boundary-pack refresh",
    )
    _expect(
        assertions,
        "session_start_surfaces_boundary",
        repeated in start_output,
        "SessionStart surfaced the promoted boundary text",
        "SessionStart output did not include the promoted boundary",
    )
    _expect(
        assertions,
        "prevention_rules_capture_promotion",
        repeated in json.dumps(rules),
        "prevention_rules.json recorded the promoted boundary metadata",
        "prevention_rules.json did not capture the promoted boundary metadata",
    )

    return ScenarioResult(
        name="boundary_pack_refreshes_after_repeated_negative_feedback",
        description="Repeated negative feedback refreshes the boundary pack and carries promoted boundaries into startup context.",
        success=all(item.passed for item in assertions),
        assertions=assertions,
        steps=steps,
        before_state=before_state,
        after_state=after_state,
        extras={"boundary_pack": pack, "prevention_rules": rules, "start_output": start_output, "end_output": end_output},
    )


def _scenario_projection_rebuild_converges(sandbox: ScenarioSandbox) -> ScenarioResult:
    steps: list[dict[str, Any]] = []
    assertions: list[ScenarioAssertion] = []

    with sandbox.activate():
        lesson_id, lesson_text = create_lesson_from_feedback(
            "Always use semantic status tokens instead of raw hex values in dashboard badges",
            "negative",
            [],
        )
        append_feedback_event(
            sandbox.storage_path,
            {
                "timestamp": time.time(),
                "signal": "correction",
                "category": "testing",
                "intensity": 3,
                "correction": "Always validate API error branches with explicit assertions before merging feature work",
                "context": "Projection convergence QA scenario",
            },
        )
        before_state = snapshot_storage(sandbox.storage_path)
        _record_step(steps, "snapshot_before", "Seeded one active lesson and one event-only projection document", lesson_id=lesson_id)

        dry_run_summary = cleanup_and_vectorize.rebuild_vector_cache(
            sandbox.storage_path,
            sandbox.lancedb_path,
            dry_run=True,
        )
        fake_db = _FakeLanceDB()
        fake_modules = {
            "lancedb": SimpleNamespace(connect=lambda _path: fake_db),
            "sentence_transformers": SimpleNamespace(SentenceTransformer=_FakeSentenceTransformer),
        }
        with patch.dict(sys.modules, fake_modules):
            rebuild_summary = cleanup_and_vectorize.rebuild_vector_cache(
                sandbox.storage_path,
                sandbox.lancedb_path,
                dry_run=False,
            )
        _record_step(
            steps,
            "rebuild_projection_cache",
            "Rebuilt the LanceDB cache from the canonical SQLite projection using fake local modules",
            dry_run_document_count=dry_run_summary["document_count"],
            rebuilt_document_count=rebuild_summary["document_count"],
        )

        after_state = snapshot_storage(sandbox.storage_path)

    projection_docs = after_state["canonical"]["search_documents"]
    projection_doc_ids = {doc["doc_id"] for doc in projection_docs}
    fake_doc_ids = {row["doc_id"] for row in fake_db.rows}
    vectorization_log = after_state["exports"].get("vectorization_log.json") or {}

    _expect(
        assertions,
        "projection_contains_multiple_source_types",
        {doc["source_type"] for doc in projection_docs} >= {"lesson", "event"},
        "Canonical projection contains both lesson and event documents",
        "Canonical projection did not contain both lesson and event source types",
    )
    _expect(
        assertions,
        "dry_run_matches_projection_count",
        dry_run_summary["document_count"] == len(projection_doc_ids),
        "Dry-run rebuild counted the same documents as the canonical projection",
        "Dry-run rebuild count did not match the canonical projection",
    )
    _expect(
        assertions,
        "rebuild_rows_match_projection_ids",
        fake_doc_ids == projection_doc_ids,
        "Rebuilt LanceDB rows matched the canonical projection document IDs",
        "Rebuilt LanceDB rows did not match the canonical projection document IDs",
    )
    _expect(
        assertions,
        "vectorization_log_matches_rebuild",
        int(vectorization_log.get("total_documents_in_rag", -1)) == len(fake_db.rows),
        "Vectorization log reflects the rebuilt cache row count",
        "Vectorization log did not match the rebuilt cache row count",
    )

    return ScenarioResult(
        name="projection_rebuild_converges",
        description="Full cache rebuild converges to the canonical SQLite search projection.",
        success=all(item.passed for item in assertions),
        assertions=assertions,
        steps=steps,
        before_state=before_state,
        after_state=after_state,
        extras={"dry_run_summary": dry_run_summary, "rebuild_summary": rebuild_summary, "rebuilt_rows": fake_db.rows},
    )


def _scenario_capacity_enforcement_at_300(sandbox: ScenarioSandbox) -> ScenarioResult:
    steps: list[dict[str, Any]] = []
    assertions: list[ScenarioAssertion] = []

    baseline_lessons = [
        {
            "id": f"L{i:03d}",
            "lesson": f"Always keep QA fixture {i} isolated by resetting shared state before every run",
            "category": "testing",
        }
        for i in range(1, 301)
    ]

    with sandbox.activate():
        _write_legacy_json(sandbox.storage_path / "curated_lessons.json", baseline_lessons)
        before_state = snapshot_storage(sandbox.storage_path)
        _record_step(steps, "snapshot_before", "Seeded the active corpus to the 300-lesson capacity limit")

        result = mcp_server.create_lesson(
            lesson="Always use semantic color tokens from the theme instead of hardcoded hex values in cards",
            category="code_edit",
            sentiment="negative",
            intensity=3,
            context="Capacity enforcement QA scenario",
        )
        _record_step(steps, "create_lesson_at_capacity", "Attempted to create a new lesson at corpus capacity", result=result)

        after_state = snapshot_storage(sandbox.storage_path)

    active_lessons = after_state["canonical"]["active_lessons"]

    _expect(
        assertions,
        "create_lesson_reports_capacity_error",
        "capacity" in result.lower(),
        "create_lesson rejected writes when the active corpus was already full",
        "create_lesson did not report the expected capacity error",
    )
    _expect(
        assertions,
        "active_count_stays_at_300",
        len(active_lessons) == 300,
        "Active lesson count stayed at the hard 300-lesson cap",
        f"Active lesson count changed unexpectedly: {len(active_lessons)}",
    )
    _expect(
        assertions,
        "overflow_lesson_not_added",
        all("semantic color tokens from the theme" not in lesson["lesson"] for lesson in active_lessons),
        "Overflow lesson was not added to the active corpus",
        "Overflow lesson was added despite the capacity guardrail",
    )

    return ScenarioResult(
        name="capacity_enforcement_at_300",
        description="The active corpus hard-stops at 300 lessons and rejects overflow writes.",
        success=all(item.passed for item in assertions),
        assertions=assertions,
        steps=steps,
        before_state=before_state,
        after_state=after_state,
        extras={"result": result},
    )


def _scenario_merge_lessons_consolidates_scores(sandbox: ScenarioSandbox) -> ScenarioResult:
    steps: list[dict[str, Any]] = []
    assertions: list[ScenarioAssertion] = []

    merged_text = "Use semantic theme tokens instead of hardcoded hex values in component styles"

    with sandbox.activate():
        _write_legacy_json(
            sandbox.storage_path / "curated_lessons.json",
            [
                {"id": "L001", "lesson": "Use semantic colors from the theme", "category": "code_edit"},
                {"id": "L002", "lesson": "Avoid hardcoded hex values in component styles", "category": "code_edit"},
            ],
        )
        _write_legacy_json(
            sandbox.storage_path / "lesson_scores.json",
            {
                "L001": {"boost": 2.1, "ups": 3, "downs": 0, "blocked": False, "retired": False, "retired_reason": "", "retired_at": None},
                "L002": {"boost": 1.4, "ups": 1, "downs": 0, "blocked": False, "retired": False, "retired_reason": "", "retired_at": None},
            },
        )
        before_state = snapshot_storage(sandbox.storage_path)
        _record_step(steps, "snapshot_before", "Seeded overlapping lessons and existing scores")

        with patch("smartassist.mcp_server.spawn_managed", lambda *args, **kwargs: None):
            result = mcp_server.merge_lessons("L001,L002", merged_text, "code_edit")
        _record_step(steps, "merge_lessons", "Merged two overlapping lessons into one consolidated lesson", result=result)

        after_state = snapshot_storage(sandbox.storage_path)

    active_lessons = after_state["canonical"]["active_lessons"]
    all_lessons = after_state["canonical"]["lessons"]
    scores = after_state["canonical"]["lesson_scores"]
    feedback_events = after_state["canonical"]["feedback_events"]
    merged_lesson = next((lesson for lesson in active_lessons if lesson["lesson"] == merged_text), None)
    merged_id = merged_lesson["id"] if merged_lesson else None

    _expect(
        assertions,
        "merged_lesson_created",
        merged_lesson is not None,
        "Merge created one new consolidated active lesson",
        "Merge did not create the consolidated active lesson",
    )
    _expect(
        assertions,
        "source_lessons_superseded",
        all(
            next((lesson for lesson in all_lessons if lesson["id"] == lesson_id), {}).get("state") == "superseded"
            for lesson_id in ("L001", "L002")
        ),
        "Source lessons were marked as superseded",
        "One or more source lessons were not marked as superseded",
    )
    _expect(
        assertions,
        "merged_score_consolidated",
        bool(
            merged_id
            and scores.get(merged_id, {}).get("ups") == 4
            and abs(float(scores.get(merged_id, {}).get("boost", 0.0)) - 2.1) < 1e-6
        ),
        "Merged lesson consolidated the source ups and max boost",
        "Merged lesson score did not consolidate the source metrics correctly",
    )
    _expect(
        assertions,
        "merge_event_recorded",
        any(event["signal"] == "merge" and event["correction"] == merged_text for event in feedback_events),
        "Merge wrote a canonical feedback event",
        "Merge did not write the expected feedback event",
    )

    return ScenarioResult(
        name="merge_lessons_consolidates_scores",
        description="Merging lessons consolidates source scores and supersedes the old active rows.",
        success=all(item.passed for item in assertions),
        assertions=assertions,
        steps=steps,
        before_state=before_state,
        after_state=after_state,
        extras={"result": result, "merged_id": merged_id},
    )


def _scenario_doctor_rejects_false_ready(sandbox: ScenarioSandbox) -> ScenarioResult:
    steps: list[dict[str, Any]] = []
    assertions: list[ScenarioAssertion] = []

    with sandbox.activate():
        sandbox.write_hook_settings()
        sandbox.write_project_mcp_registration()
        before_state = snapshot_storage(sandbox.storage_path)

        original_path = os.environ.get("PATH", "")
        os.environ["PATH"] = str(sandbox.bin_dir)
        initial_report = collect_doctor_report()
        _record_step(
            steps,
            "doctor_without_commands",
            "Collected doctor report before hook commands were installed on PATH",
            overall_status=initial_report["overall_status"],
        )

        sandbox.install_fake_commands(
            [
                "smartassist-prompt-inject",
                "smartassist-session-start",
                "smartassist-session-end",
                "smartassist-commit-hook",
                "smartassist-show-lessons",
            ]
        )
        os.environ["PATH"] = str(sandbox.bin_dir)
        ready_report = collect_doctor_report()
        _record_step(
            steps,
            "doctor_with_commands",
            "Collected doctor report after hook commands were installed on PATH",
            overall_status=ready_report["overall_status"],
        )
        os.environ["PATH"] = original_path

        after_state = snapshot_storage(sandbox.storage_path)

    _expect(
        assertions,
        "doctor_fails_without_path_commands",
        initial_report["overall_status"] == "fail",
        "Doctor rejects false-ready state when hook commands are unavailable",
        f"Doctor returned {initial_report['overall_status']} instead of fail when hook commands were missing",
    )
    _expect(
        assertions,
        "doctor_reports_hook_commands_check",
        any(check["name"] == "Hook commands" and check["status"] == "fail" for check in initial_report["checks"]),
        "Doctor reports hook-command executability separately",
        "Doctor did not report a failing hook-command executability check",
    )
    _expect(
        assertions,
        "doctor_recovers_when_commands_exist",
        ready_report["overall_status"] == "ready",
        "Doctor becomes ready once commands are executable",
        f"Doctor remained {ready_report['overall_status']} even after hook commands were installed",
    )

    return ScenarioResult(
        name="doctor_rejects_false_ready",
        description="Doctor must fail when SmartAssist is configured but the hook commands are not executable.",
        success=all(item.passed for item in assertions),
        assertions=assertions,
        steps=steps,
        before_state=before_state,
        after_state=after_state,
        extras={"initial_report": initial_report, "ready_report": ready_report},
    )


SCENARIOS: dict[str, ScenarioDefinition] = {
    "hook_mcp_retrieval_consistency": ScenarioDefinition(
        name="hook_mcp_retrieval_consistency",
        description="Hook injection and MCP retrieval share the same active knowledge base.",
        live_claude=False,
        runner=_scenario_hook_mcp_retrieval_consistency,
    ),
    "feedback_creates_active_lesson": ScenarioDefinition(
        name="feedback_creates_active_lesson",
        description="Feedback-derived lessons become active, searchable, and injectable.",
        live_claude=False,
        runner=_scenario_feedback_creates_active_lesson,
    ),
    "compare_lesson_logs_without_storage": ScenarioDefinition(
        name="compare_lesson_logs_without_storage",
        description="compare_lesson writes A/B artifacts without mutating runtime knowledge.",
        live_claude=False,
        runner=_scenario_compare_lesson_logs_without_storage,
    ),
    "commit_correction_promotes_active_lesson": ScenarioDefinition(
        name="commit_correction_promotes_active_lesson",
        description="Commit corrections are promoted into the active lesson corpus.",
        live_claude=False,
        runner=_scenario_commit_correction_promotes_active_lesson,
    ),
    "session_dedup_prevents_repeat_injection": ScenarioDefinition(
        name="session_dedup_prevents_repeat_injection",
        description="Session dedup prevents repeated prompt injection in the same session.",
        live_claude=False,
        runner=_scenario_session_dedup_prevents_repeat_injection,
    ),
    "demote_retires_lesson_everywhere": ScenarioDefinition(
        name="demote_retires_lesson_everywhere",
        description="Demotion retires weak lessons and removes them from active retrieval.",
        live_claude=False,
        runner=_scenario_demote_retires_lesson_everywhere,
    ),
    "seed_creates_active_conventions": ScenarioDefinition(
        name="seed_creates_active_conventions",
        description="CLAUDE.md seeding produces active conventions immediately.",
        live_claude=False,
        runner=_scenario_seed_creates_active_conventions,
    ),
    "gate_statistics_accumulate": ScenarioDefinition(
        name="gate_statistics_accumulate",
        description="Gate decisions accumulate stats and keep allow/ask/deny behavior aligned.",
        live_claude=False,
        runner=_scenario_gate_statistics_accumulate,
    ),
    "boundary_pack_refreshes_after_repeated_negative_feedback": ScenarioDefinition(
        name="boundary_pack_refreshes_after_repeated_negative_feedback",
        description="Repeated negative feedback refreshes boundary packs and startup context.",
        live_claude=False,
        runner=_scenario_boundary_pack_refreshes_after_repeated_negative_feedback,
    ),
    "projection_rebuild_converges": ScenarioDefinition(
        name="projection_rebuild_converges",
        description="Full cache rebuild converges to the canonical projection.",
        live_claude=False,
        runner=_scenario_projection_rebuild_converges,
    ),
    "capacity_enforcement_at_300": ScenarioDefinition(
        name="capacity_enforcement_at_300",
        description="The active corpus enforces the 300-lesson cap.",
        live_claude=False,
        runner=_scenario_capacity_enforcement_at_300,
    ),
    "merge_lessons_consolidates_scores": ScenarioDefinition(
        name="merge_lessons_consolidates_scores",
        description="Merge consolidates scores and supersedes the source lessons.",
        live_claude=False,
        runner=_scenario_merge_lessons_consolidates_scores,
    ),
    "doctor_rejects_false_ready": ScenarioDefinition(
        name="doctor_rejects_false_ready",
        description="Doctor rejects config-only readiness when hook commands are missing from PATH.",
        live_claude=False,
        runner=_scenario_doctor_rejects_false_ready,
    ),
}


DEFAULT_SCENARIO_ORDER = [
    "hook_mcp_retrieval_consistency",
    "feedback_creates_active_lesson",
    "compare_lesson_logs_without_storage",
    "commit_correction_promotes_active_lesson",
    "session_dedup_prevents_repeat_injection",
    "demote_retires_lesson_everywhere",
    "seed_creates_active_conventions",
    "gate_statistics_accumulate",
    "boundary_pack_refreshes_after_repeated_negative_feedback",
    "projection_rebuild_converges",
    "capacity_enforcement_at_300",
    "merge_lessons_consolidates_scores",
    "doctor_rejects_false_ready",
]


def get_scenario_definitions(names: list[str] | None = None) -> list[ScenarioDefinition]:
    selected = names or list(DEFAULT_SCENARIO_ORDER)
    missing = [name for name in selected if name not in SCENARIOS]
    if missing:
        raise ValueError(f"Unknown scenario(s): {', '.join(sorted(missing))}")
    return [SCENARIOS[name] for name in selected]
