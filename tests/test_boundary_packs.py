"""Tests for prevention-rule promotion and startup boundary packs."""

from __future__ import annotations

import json
import os
import time

from smartassist.boundary_packs import (
    build_recent_mistakes,
    build_promoted_boundaries,
    ensure_boundary_pack,
    format_boundary_pack_for_session,
    get_boundary_pack_path,
    refresh_boundary_pack,
)
from smartassist.gates import get_prevention_rules_path
from smartassist.hooks.session_end import capture_session_learning
from smartassist.hooks.session_start import format_lessons_for_session


def _storage_path(data_dir):
    return data_dir / "data"


def _write_feedback_events(storage, events):
    feedback_log = storage / "feedback_log.jsonl"
    with open(feedback_log, "w", encoding="utf-8") as handle:
        for event in events:
            handle.write(json.dumps(event) + "\n")


def _append_feedback_event(storage, event):
    feedback_log = storage / "feedback_log.jsonl"
    with open(feedback_log, "a", encoding="utf-8") as handle:
        handle.write(json.dumps(event) + "\n")


def _write_reliabilities(storage, categories):
    now = time.time()
    payload = {}
    for category, (alpha, beta) in categories.items():
        payload[category] = {
            "category": category,
            "alpha": alpha,
            "beta": beta,
            "last_updated": now,
            "total_samples": int(alpha + beta - 2),
        }
    (storage / "reliability_scores.json").write_text(
        json.dumps(payload), encoding="utf-8"
    )


def _event(
    *,
    category,
    correction,
    timestamp,
    signal="correction",
    intensity=4,
    query="",
    context="",
):
    return {
        "signal": signal,
        "intensity": intensity,
        "category": category,
        "context": context,
        "query": query,
        "response": "Old behavior",
        "correction": correction,
        "timestamp": timestamp,
        "session_id": "test-session",
    }


class TestPromotionEngine:
    def test_promotes_repeated_lessons_and_preserves_manual_rules(self, set_data_dir):
        storage = _storage_path(set_data_dir)
        repeated = (
            "Use feature branches and open a PR instead of pushing directly to main "
            "when working on repository changes."
        )
        singleton = (
            "Run the focused test file for touched code before asking for review so "
            "failures are caught close to the change."
        )

        _write_feedback_events(
            storage,
            [
                _event(
                    category="git",
                    correction=repeated,
                    timestamp=100.0,
                    query="push the fix",
                    context="release branch cleanup",
                ),
                _event(
                    category="testing",
                    correction=singleton,
                    timestamp=200.0,
                    query="finish tests",
                    context="unit coverage",
                ),
                _event(
                    category="git",
                    correction=repeated,
                    timestamp=300.0,
                    query="push the follow-up",
                    context="main branch hotfix",
                ),
            ],
        )
        _write_reliabilities(storage, {"git": (1.0, 4.0), "testing": (4.0, 1.0)})

        rules_path = get_prevention_rules_path(storage)
        assert rules_path is not None
        rules_path.write_text(
            json.dumps(
                {
                    "version": 1,
                    "notes": "keep-me",
                    "rules": [
                        {
                            "id": "deny-node-modules-delete",
                            "action": "deny",
                            "message": "Do not delete node_modules blindly.",
                            "matcher": "regex",
                            "tool_names": ["Bash"],
                            "target": "command",
                            "pattern": r"\brm\s+-rf\s+node_modules\b",
                        }
                    ],
                }
            ),
            encoding="utf-8",
        )

        pack = refresh_boundary_pack(storage)

        promoted = pack["promoted_boundaries"]
        assert len(promoted) == 1
        assert promoted[0]["category"] == "git"
        assert promoted[0]["count"] == 2
        assert promoted[0]["weak_category"] is True
        assert promoted[0]["sample_context"] == "main branch hotfix"

        updated = json.loads(rules_path.read_text())
        assert updated["notes"] == "keep-me"
        assert updated["rules"][0]["id"] == "deny-node-modules-delete"
        assert updated["promoted_boundaries"][0]["id"] == promoted[0]["id"]

    def test_legacy_rule_list_is_preserved_when_pack_refreshes(self, set_data_dir):
        storage = _storage_path(set_data_dir)
        repeated = (
            "Use feature branches and open a PR instead of pushing directly to main "
            "when working on repository changes."
        )
        _write_feedback_events(
            storage,
            [
                _event(category="git", correction=repeated, timestamp=100.0),
                _event(category="git", correction=repeated, timestamp=200.0),
            ],
        )
        _write_reliabilities(storage, {"git": (1.0, 4.0)})

        rules_path = get_prevention_rules_path(storage)
        assert rules_path is not None
        rules_path.write_text(
            json.dumps(
                [
                    {
                        "id": "deny-node-modules-delete",
                        "action": "deny",
                        "message": "Do not delete node_modules blindly.",
                        "matcher": "regex",
                        "tool_names": ["Bash"],
                        "target": "command",
                        "pattern": r"\brm\s+-rf\s+node_modules\b",
                    }
                ]
            ),
            encoding="utf-8",
        )

        refresh_boundary_pack(storage)

        updated = json.loads(rules_path.read_text())
        assert isinstance(updated, dict)
        assert updated["rules"][0]["id"] == "deny-node-modules-delete"
        assert len(updated["promoted_boundaries"]) == 1

    def test_build_promoted_boundaries_ignores_singletons_and_non_actionable_feedback(
        self,
    ):
        repeated = (
            "Use semantic color tokens from the shared theme instead of hardcoded hex "
            "values in component styles."
        )
        events = [
            _event(
                category="code_edit",
                correction="Done - fixed in the next commit",
                timestamp=100.0,
            ),
            _event(category="code_edit", correction=repeated, timestamp=200.0),
            _event(category="code_edit", correction=repeated, timestamp=300.0),
            _event(
                category="testing",
                correction=(
                    "Run the focused test file for touched code before asking for review "
                    "so failures are caught close to the change."
                ),
                timestamp=400.0,
            ),
        ]

        promoted = build_promoted_boundaries(events, weak_categories={"code_edit"})

        assert len(promoted) == 1
        assert promoted[0]["category"] == "code_edit"
        assert promoted[0]["count"] == 2


class TestBoundaryPackAssembly:
    def test_recent_mistakes_prioritize_weak_categories_and_dedupe(self):
        git_rule = (
            "Use feature branches and open a PR instead of pushing directly to main "
            "when working on repository changes."
        )
        testing_rule = (
            "Run the focused test file for touched code before asking for review so "
            "failures are caught close to the change."
        )
        events = [
            _event(category="testing", correction=testing_rule, timestamp=400.0),
            _event(category="git", correction=git_rule, timestamp=300.0),
            _event(category="git", correction=git_rule, timestamp=200.0),
        ]

        recent = build_recent_mistakes(events, weak_categories={"git"}, limit=3)

        assert [item["category"] for item in recent] == ["git", "testing"]
        assert len([item for item in recent if item["category"] == "git"]) == 1

    def test_ensure_boundary_pack_rebuilds_when_missing_or_stale(self, set_data_dir):
        storage = _storage_path(set_data_dir)
        repeated = (
            "Use feature branches and open a PR instead of pushing directly to main "
            "when working on repository changes."
        )
        _write_feedback_events(
            storage,
            [
                _event(category="git", correction=repeated, timestamp=100.0),
                _event(category="git", correction=repeated, timestamp=200.0),
            ],
        )
        _write_reliabilities(storage, {"git": (1.0, 4.0)})

        pack = ensure_boundary_pack(storage)
        assert pack["promoted_boundaries"][0]["count"] == 2

        pack_path = get_boundary_pack_path(storage)
        assert pack_path is not None
        assert pack_path.exists()

        _append_feedback_event(
            storage, _event(category="git", correction=repeated, timestamp=300.0)
        )
        os.utime(storage / "feedback_log.jsonl", None)

        refreshed = ensure_boundary_pack(storage)
        assert refreshed["promoted_boundaries"][0]["count"] >= 2

    def test_ensure_boundary_pack_recovers_from_corrupt_pack(self, set_data_dir):
        storage = _storage_path(set_data_dir)
        repeated = (
            "Use feature branches and open a PR instead of pushing directly to main "
            "when working on repository changes."
        )
        _write_feedback_events(
            storage,
            [
                _event(category="git", correction=repeated, timestamp=100.0),
                _event(category="git", correction=repeated, timestamp=200.0),
            ],
        )
        _write_reliabilities(storage, {"git": (1.0, 4.0)})

        pack_path = get_boundary_pack_path(storage)
        assert pack_path is not None
        pack_path.write_text("{not json", encoding="utf-8")

        pack = ensure_boundary_pack(storage)
        assert pack["promoted_boundaries"][0]["count"] == 2
        assert json.loads(pack_path.read_text())["promoted_boundaries"][0]["count"] == 2

    def test_formats_boundary_pack_for_session(self):
        pack = {
            "weak_categories": [{"category": "git", "reliability": 0.2}],
            "promoted_boundaries": [
                {
                    "category": "git",
                    "count": 2,
                    "weak_category": True,
                    "lesson": "Use feature branches instead of pushing directly to main.",
                }
            ],
            "recent_mistakes": [
                {
                    "category": "testing",
                    "signal": "correction",
                    "lesson": "Run focused tests for touched files before review.",
                }
            ],
        }

        rendered = format_boundary_pack_for_session(pack)

        assert "SMARTASSIST BOUNDARY PACK" in rendered
        assert "Promoted prevention rules" in rendered
        assert "GIT x2 [weak]" in rendered
        assert "Run focused tests for touched files before review." in rendered


class TestSessionHookIntegration:
    def test_session_start_uses_boundary_pack_context(self, set_data_dir):
        storage = _storage_path(set_data_dir)
        repeated = (
            "Use feature branches and open a PR instead of pushing directly to main "
            "when working on repository changes."
        )
        _write_feedback_events(
            storage,
            [
                _event(category="git", correction=repeated, timestamp=100.0),
                _event(category="git", correction=repeated, timestamp=200.0),
            ],
        )
        _write_reliabilities(storage, {"git": (1.0, 4.0)})

        output = format_lessons_for_session()

        assert "SMARTASSIST BOUNDARY PACK" in output
        assert "SMARTASSIST FEEDBACK PROTOCOL" in output
        assert "Areas needing attention" in output
        assert (
            "Use feature branches and open a PR instead of pushing directly to main"
            in output
        )

    def test_session_start_includes_feedback_protocol_without_boundary_pack(
        self, set_data_dir
    ):
        output = format_lessons_for_session()

        assert "SMARTASSIST FEEDBACK PROTOCOL" in output
        assert "apply_feedback_protocol" in output

    def test_session_end_refreshes_boundary_pack_and_logs_summary(
        self, monkeypatch, capsys, set_data_dir
    ):
        storage = _storage_path(set_data_dir)
        repeated = (
            "Use feature branches and open a PR instead of pushing directly to main "
            "when working on repository changes."
        )
        _write_feedback_events(
            storage,
            [
                _event(category="git", correction=repeated, timestamp=100.0),
                _event(category="git", correction=repeated, timestamp=200.0),
            ],
        )
        _write_reliabilities(storage, {"git": (1.0, 4.0)})

        captured = {}

        def _fake_run(args, capture_output, timeout):
            captured["args"] = args
            captured["capture_output"] = capture_output
            captured["timeout"] = timeout
            return None

        monkeypatch.setattr("smartassist.hooks.session_end.subprocess.run", _fake_run)

        capture_session_learning()

        output = capsys.readouterr().out
        assert (
            "Updated boundary pack: 1 promoted rule(s), 1 recent lesson(s) carried forward"
            in output
        )
        assert "Updating RAG database with new learnings..." in output
        assert captured["args"][-1] == "smartassist.hooks.vectorize_learnings"

        pack_path = get_boundary_pack_path(storage)
        assert pack_path is not None and pack_path.exists()

        session_log = storage / "session_log.jsonl"
        assert session_log.exists()
        assert len(session_log.read_text().strip().splitlines()) == 1

        rules_path = get_prevention_rules_path(storage)
        assert rules_path is not None
        updated = json.loads(rules_path.read_text())
        assert len(updated["promoted_boundaries"]) == 1
