import json
import signal
import time
from pathlib import Path
from unittest.mock import patch

from smartassist.codex_activity import (
    get_sync_state_path,
    reap_legacy_project_watcher,
    sync_codex_activity,
)


def _write_session_file(path: Path, entries: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8") as handle:
        for entry in entries:
            handle.write(json.dumps(entry) + "\n")


class TestCodexActivitySync:
    def test_sync_logs_prompts_for_current_project(
        self,
        monkeypatch,
        set_data_dir,
        tmp_path,
    ):
        codex_home = tmp_path / ".codex"
        session_file = (
            codex_home
            / "sessions"
            / "2026"
            / "04"
            / "07"
            / "rollout-current-project.jsonl"
        )
        _write_session_file(
            session_file,
            [
                {
                    "timestamp": "2026-04-07T05:09:45.000Z",
                    "type": "session_meta",
                    "payload": {
                        "id": "session-1",
                        "cwd": str(tmp_path),
                    },
                },
                {
                    "timestamp": "2026-04-07T05:09:47.000Z",
                    "type": "event_msg",
                    "payload": {
                        "type": "user_message",
                        "message": "check dashboard sync",
                    },
                },
            ],
        )
        monkeypatch.setenv("SMARTASSIST_CODEX_HOME", str(codex_home))

        result = sync_codex_activity(
            storage_path=set_data_dir / "data",
            project_root=tmp_path,
        )

        assert result["sessions_logged"] == 1
        assert result["prompts_logged"] == 1

        usage_lines = (
            (set_data_dir / "data" / "usage_log.jsonl").read_text().splitlines()
        )
        assert len(usage_lines) == 2
        prompt_event = json.loads(usage_lines[1])
        assert prompt_event["tool"] == "codex_prompt"
        assert prompt_event["query"] == "check dashboard sync"

        live_log = (set_data_dir / "data" / "rag_live.log").read_text()
        assert "CODEX SESSION START" in live_log
        assert '[codex] "check dashboard sync"' in live_log
        assert not (set_data_dir / "data" / "codex_sync_state.json").exists()
        assert get_sync_state_path(
            set_data_dir / "data",
            project_root=tmp_path,
            codex_home=codex_home,
        ).exists()

    def test_sync_skips_sessions_outside_current_project(
        self,
        monkeypatch,
        set_data_dir,
        tmp_path,
    ):
        codex_home = tmp_path / ".codex"
        other_project = tmp_path.parent / "other-project"
        other_project.mkdir(exist_ok=True)
        session_file = (
            codex_home
            / "sessions"
            / "2026"
            / "04"
            / "07"
            / "rollout-other-project.jsonl"
        )
        _write_session_file(
            session_file,
            [
                {
                    "timestamp": "2026-04-07T05:09:45.000Z",
                    "type": "session_meta",
                    "payload": {
                        "id": "session-2",
                        "cwd": str(other_project),
                    },
                },
                {
                    "timestamp": "2026-04-07T05:09:47.000Z",
                    "type": "event_msg",
                    "payload": {
                        "type": "user_message",
                        "message": "ignore me",
                    },
                },
            ],
        )
        monkeypatch.setenv("SMARTASSIST_CODEX_HOME", str(codex_home))

        result = sync_codex_activity(
            storage_path=set_data_dir / "data",
            project_root=tmp_path,
        )

        assert result["sessions_logged"] == 0
        assert result["prompts_logged"] == 0
        assert not (set_data_dir / "data" / "usage_log.jsonl").exists()
        assert not (set_data_dir / "data" / "rag_live.log").exists()

    def test_sync_is_incremental_for_existing_session(
        self,
        monkeypatch,
        set_data_dir,
        tmp_path,
    ):
        codex_home = tmp_path / ".codex"
        session_file = (
            codex_home / "sessions" / "2026" / "04" / "07" / "rollout-incremental.jsonl"
        )
        _write_session_file(
            session_file,
            [
                {
                    "timestamp": "2026-04-07T05:09:45.000Z",
                    "type": "session_meta",
                    "payload": {
                        "id": "session-3",
                        "cwd": str(tmp_path),
                    },
                },
                {
                    "timestamp": "2026-04-07T05:09:47.000Z",
                    "type": "event_msg",
                    "payload": {
                        "type": "user_message",
                        "message": "first prompt",
                    },
                },
            ],
        )
        monkeypatch.setenv("SMARTASSIST_CODEX_HOME", str(codex_home))

        first = sync_codex_activity(
            storage_path=set_data_dir / "data",
            project_root=tmp_path,
        )
        second = sync_codex_activity(
            storage_path=set_data_dir / "data",
            project_root=tmp_path,
        )

        with open(session_file, "a", encoding="utf-8") as handle:
            handle.write(
                json.dumps(
                    {
                        "timestamp": "2026-04-07T05:10:12.000Z",
                        "type": "event_msg",
                        "payload": {
                            "type": "user_message",
                            "message": "second prompt",
                        },
                    }
                )
                + "\n"
            )

        third = sync_codex_activity(
            storage_path=set_data_dir / "data",
            project_root=tmp_path,
        )

        assert first["prompts_logged"] == 1
        assert second["prompts_logged"] == 0
        assert third["prompts_logged"] == 1

        usage_lines = (
            (set_data_dir / "data" / "usage_log.jsonl").read_text().splitlines()
        )
        prompt_tools = [json.loads(line)["tool"] for line in usage_lines]
        assert prompt_tools == [
            "codex_session_start",
            "codex_prompt",
            "codex_prompt",
        ]

    def test_sync_does_not_rewrite_state_on_idle_poll(
        self,
        monkeypatch,
        set_data_dir,
        tmp_path,
    ):
        codex_home = tmp_path / ".codex"
        session_file = (
            codex_home / "sessions" / "2026" / "04" / "07" / "rollout-idle.jsonl"
        )
        _write_session_file(
            session_file,
            [
                {
                    "timestamp": "2026-04-07T05:09:45.000Z",
                    "type": "session_meta",
                    "payload": {
                        "id": "session-4",
                        "cwd": str(tmp_path),
                    },
                },
                {
                    "timestamp": "2026-04-07T05:09:47.000Z",
                    "type": "event_msg",
                    "payload": {
                        "type": "user_message",
                        "message": "idle check",
                    },
                },
            ],
        )
        monkeypatch.setenv("SMARTASSIST_CODEX_HOME", str(codex_home))

        sync_codex_activity(storage_path=set_data_dir / "data", project_root=tmp_path)
        state_path = get_sync_state_path(
            set_data_dir / "data",
            project_root=tmp_path,
            codex_home=codex_home,
        )
        first_mtime = state_path.stat().st_mtime_ns

        time.sleep(0.02)
        sync_codex_activity(storage_path=set_data_dir / "data", project_root=tmp_path)

        assert state_path.stat().st_mtime_ns == first_mtime

    def test_reaps_legacy_project_watcher_and_removes_pid_file(self, set_data_dir):
        pid_path = set_data_dir / "data" / "codex_sync.pid"
        pid_path.write_text("4242")

        with (
            patch(
                "smartassist.codex_activity._read_process_command",
                side_effect=[
                    "python -m smartassist.codex_activity --watch",
                    "",
                ],
            ),
            patch("smartassist.codex_activity.os.kill") as mock_kill,
            patch("smartassist.codex_activity.time.sleep"),
        ):
            assert reap_legacy_project_watcher(set_data_dir / "data") is True

        mock_kill.assert_called_once_with(4242, signal.SIGTERM)
        assert not pid_path.exists()
