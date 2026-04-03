"""Tests for the SmartAssist gate engine and PreToolUse hook."""

from __future__ import annotations

import io
import json

from smartassist.gates import (
    build_pretool_hook_output,
    evaluate_pretool_gate,
    get_prevention_rules_path,
    load_gate_stats,
    load_prevention_rules,
)
from smartassist.hooks import commit_hook


def _storage_path(data_dir):
    return data_dir / "data"


def _hook_stdin(payload: dict) -> io.StringIO:
    stream = io.StringIO(json.dumps(payload))
    stream.isatty = lambda: False  # type: ignore[attr-defined]
    return stream


class TestGateEvaluation:
    def test_denies_force_push(self, set_data_dir):
        decision = evaluate_pretool_gate(
            "Bash",
            {"command": "git push --force origin HEAD"},
            storage_path=_storage_path(set_data_dir),
        )

        assert decision is not None
        assert decision.action == "deny"
        assert decision.gate_id == "deny-force-push"

        stats = load_gate_stats(_storage_path(set_data_dir))
        assert stats["blocked"] == 1
        assert stats["by_gate"]["deny-force-push"]["deny"] == 1

    def test_denies_force_with_lease(self, set_data_dir):
        decision = evaluate_pretool_gate(
            "Bash",
            {"command": "git push --force-with-lease origin main"},
            storage_path=_storage_path(set_data_dir),
        )

        assert decision is not None
        assert decision.action == "deny"
        assert decision.gate_id == "deny-force-push"

    def test_does_not_false_positive_on_echo_force_push_text(self, set_data_dir):
        decision = evaluate_pretool_gate(
            "Bash",
            {"command": 'echo "git push --force would be bad"'},
            storage_path=_storage_path(set_data_dir),
        )

        assert decision is None
        stats = load_gate_stats(_storage_path(set_data_dir))
        assert stats["passed"] == 1

    def test_asks_on_explicit_protected_branch_push(self, set_data_dir):
        decision = evaluate_pretool_gate(
            "Bash",
            {"command": "git push origin main"},
            storage_path=_storage_path(set_data_dir),
        )

        assert decision is not None
        assert decision.action == "ask"
        assert decision.gate_id == "ask-protected-branch-push"

    def test_asks_on_implicit_push_from_protected_branch(self, monkeypatch, set_data_dir):
        monkeypatch.setattr("smartassist.gates._get_current_branch", lambda project_root=None: "main")

        decision = evaluate_pretool_gate(
            "Bash",
            {"command": "git push"},
            storage_path=_storage_path(set_data_dir),
        )

        assert decision is not None
        assert decision.action == "ask"
        assert decision.gate_id == "ask-protected-branch-push"

    def test_allows_implicit_push_from_feature_branch(self, monkeypatch, set_data_dir):
        monkeypatch.setattr("smartassist.gates._get_current_branch", lambda project_root=None: "feature/test")

        decision = evaluate_pretool_gate(
            "Bash",
            {"command": "git push origin"},
            storage_path=_storage_path(set_data_dir),
        )

        assert decision is None

    def test_asks_on_head_to_main_push(self, monkeypatch, set_data_dir):
        monkeypatch.setattr("smartassist.gates._get_current_branch", lambda project_root=None: "main")

        decision = evaluate_pretool_gate(
            "Bash",
            {"command": "git push origin HEAD"},
            storage_path=_storage_path(set_data_dir),
        )

        assert decision is not None
        assert decision.gate_id == "ask-protected-branch-push"

    def test_asks_on_lockfile_checkout_reset(self, set_data_dir):
        decision = evaluate_pretool_gate(
            "Bash",
            {"command": "git checkout -- package-lock.json"},
            storage_path=_storage_path(set_data_dir),
        )

        assert decision is not None
        assert decision.action == "ask"
        assert decision.gate_id == "ask-lockfile-reset"

    def test_asks_on_lockfile_restore_with_source(self, set_data_dir):
        decision = evaluate_pretool_gate(
            "Bash",
            {"command": "git restore --source=HEAD~1 package-lock.json"},
            storage_path=_storage_path(set_data_dir),
        )

        assert decision is not None
        assert decision.action == "ask"
        assert decision.gate_id == "ask-lockfile-reset"

    def test_does_not_flag_branch_checkout_as_lockfile_reset(self, set_data_dir):
        decision = evaluate_pretool_gate(
            "Bash",
            {"command": "git checkout feature/refactor"},
            storage_path=_storage_path(set_data_dir),
        )

        assert decision is None

    def test_asks_on_sensitive_env_write_tool(self, set_data_dir):
        decision = evaluate_pretool_gate(
            "Write",
            {"file_path": "/tmp/project/.env.local"},
            storage_path=_storage_path(set_data_dir),
        )

        assert decision is not None
        assert decision.action == "ask"
        assert decision.gate_id == "ask-sensitive-env-edit"

    def test_asks_on_sensitive_env_edit_using_path_field(self, set_data_dir):
        decision = evaluate_pretool_gate(
            "Edit",
            {"path": "/tmp/project/.env"},
            storage_path=_storage_path(set_data_dir),
        )

        assert decision is not None
        assert decision.gate_id == "ask-sensitive-env-edit"

    def test_allows_env_template_files(self, set_data_dir):
        decision = evaluate_pretool_gate(
            "Write",
            {"file_path": "/tmp/project/.env.example"},
            storage_path=_storage_path(set_data_dir),
        )

        assert decision is None

    def test_allows_read_only_env_bash_access(self, set_data_dir):
        decision = evaluate_pretool_gate(
            "Bash",
            {"command": "cat .env"},
            storage_path=_storage_path(set_data_dir),
        )

        assert decision is None

    def test_asks_on_shell_redirect_to_env(self, set_data_dir):
        decision = evaluate_pretool_gate(
            "Bash",
            {"command": "echo API_KEY=test >> .env"},
            storage_path=_storage_path(set_data_dir),
        )

        assert decision is not None
        assert decision.gate_id == "ask-sensitive-env-shell-write"

    def test_asks_on_shell_copy_to_env(self, set_data_dir):
        decision = evaluate_pretool_gate(
            "Bash",
            {"command": "cp .env.example .env"},
            storage_path=_storage_path(set_data_dir),
        )

        assert decision is not None
        assert decision.gate_id == "ask-sensitive-env-shell-write"

    def test_loads_project_prevention_rules(self, set_data_dir):
        rules_path = get_prevention_rules_path(_storage_path(set_data_dir))
        assert rules_path is not None
        rules_path.write_text(
            json.dumps(
                {
                    "version": 1,
                    "rules": [
                        {
                            "id": "deny-node-modules-delete",
                            "action": "deny",
                            "message": "Don't delete node_modules blindly.",
                            "matcher": "regex",
                            "tool_names": ["Bash"],
                            "target": "command",
                            "pattern": r"\brm\s+-rf\s+node_modules\b",
                        }
                    ],
                }
            )
        )

        decision = evaluate_pretool_gate(
            "Bash",
            {"command": "rm -rf node_modules"},
            storage_path=_storage_path(set_data_dir),
        )

        assert decision is not None
        assert decision.gate_id == "deny-node-modules-delete"
        assert load_prevention_rules(_storage_path(set_data_dir))[0].id == "deny-node-modules-delete"

    def test_ignores_invalid_prevention_rules(self, set_data_dir):
        rules_path = get_prevention_rules_path(_storage_path(set_data_dir))
        assert rules_path is not None
        rules_path.write_text("{not-valid-json")

        decision = evaluate_pretool_gate(
            "Bash",
            {"command": "git status"},
            storage_path=_storage_path(set_data_dir),
        )

        assert decision is None
        assert load_prevention_rules(_storage_path(set_data_dir)) == []

    def test_skips_bad_rule_entries_without_failing(self, set_data_dir):
        rules_path = get_prevention_rules_path(_storage_path(set_data_dir))
        assert rules_path is not None
        rules_path.write_text(
            json.dumps(
                {
                    "version": 1,
                    "rules": [
                        {"id": "missing-fields"},
                        {
                            "id": "warn-todo",
                            "action": "warn",
                            "message": "TODO markers need a second look.",
                            "matcher": "regex",
                            "tool_names": ["Bash"],
                            "target": "command",
                            "pattern": r"TODO",
                        },
                    ],
                }
            )
        )

        decision = evaluate_pretool_gate(
            "Bash",
            {"command": "echo TODO"},
            storage_path=_storage_path(set_data_dir),
        )

        assert decision is not None
        assert decision.action == "warn"
        assert decision.gate_id == "warn-todo"


class TestGateHookOutput:
    def test_builds_deny_hook_output(self):
        output = build_pretool_hook_output(
            evaluate_pretool_gate("Bash", {"command": "git push --force"}, storage_path=None)
        )

        hook = output["hookSpecificOutput"]
        assert hook["hookEventName"] == "PreToolUse"
        assert hook["permissionDecision"] == "deny"
        assert "deny-force-push" in hook["permissionDecisionReason"]
        assert "DENY" in hook["additionalContext"]

    def test_builds_warn_hook_output(self, set_data_dir):
        rules_path = get_prevention_rules_path(_storage_path(set_data_dir))
        assert rules_path is not None
        rules_path.write_text(
            json.dumps(
                {
                    "rules": [
                        {
                            "id": "warn-todo",
                            "action": "warn",
                            "message": "TODO markers need a second look.",
                            "matcher": "regex",
                            "tool_names": ["Bash"],
                            "target": "command",
                            "pattern": r"TODO",
                        }
                    ]
                }
            )
        )
        decision = evaluate_pretool_gate(
            "Bash",
            {"command": "echo TODO"},
            storage_path=_storage_path(set_data_dir),
        )

        output = build_pretool_hook_output(decision)
        hook = output["hookSpecificOutput"]
        assert hook["hookEventName"] == "PreToolUse"
        assert "permissionDecision" not in hook
        assert "WARNING" not in hook["additionalContext"]  # format is stable and concise
        assert "warn-todo" in hook["additionalContext"]


class TestCommitHookMain:
    def test_emits_gate_json_for_force_push(self, monkeypatch, capsys):
        monkeypatch.setattr(
            commit_hook.sys,
            "stdin",
            _hook_stdin({"tool_name": "Bash", "tool_input": {"command": "git push --force"}}),
        )

        commit_hook.main()

        output = json.loads(capsys.readouterr().out)
        assert output["hookSpecificOutput"]["permissionDecision"] == "deny"
        assert "deny-force-push" in output["hookSpecificOutput"]["permissionDecisionReason"]

    def test_runs_commit_capture_silently_for_allowed_bash(self, monkeypatch, capsys):
        calls = []

        def fake_capture(*, verbose):
            calls.append(verbose)

        monkeypatch.setattr(commit_hook, "capture_commit_lessons", fake_capture)
        monkeypatch.setattr(
            commit_hook.sys,
            "stdin",
            _hook_stdin({"tool_name": "Bash", "tool_input": {"command": "git status"}}),
        )

        commit_hook.main()

        assert calls == [False]
        assert capsys.readouterr().out == ""

    def test_skips_commit_capture_for_edit_tool(self, monkeypatch, capsys):
        calls = []

        def fake_capture(*, verbose):
            calls.append(verbose)

        monkeypatch.setattr(commit_hook, "capture_commit_lessons", fake_capture)
        monkeypatch.setattr(
            commit_hook.sys,
            "stdin",
            _hook_stdin({"tool_name": "Edit", "tool_input": {"file_path": ".env"}}),
        )

        commit_hook.main()

        output = json.loads(capsys.readouterr().out)
        assert output["hookSpecificOutput"]["permissionDecision"] == "ask"
        assert calls == []

    def test_manual_mode_keeps_verbose_commit_capture(self, monkeypatch):
        calls = []

        class FakeTty(io.StringIO):
            def isatty(self):
                return True

        def fake_capture(*, verbose):
            calls.append(verbose)

        monkeypatch.setattr(commit_hook, "capture_commit_lessons", fake_capture)
        monkeypatch.setattr(commit_hook.sys, "stdin", FakeTty())

        commit_hook.main()

        assert calls == [True]
