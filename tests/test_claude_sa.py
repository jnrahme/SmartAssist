import os
import shutil
import subprocess
from pathlib import Path
from unittest.mock import patch, MagicMock

import pytest

from smartassist.claude_sa import (
    find_data_dir,
    _kill_existing_session,
    _auto_setup,
    _launch_tmux,
    _launch_fallback,
    main,
    SESSION_NAME,
    MONITOR_COLS,
)


@pytest.fixture(autouse=True)
def _cleanup_tmux_session():
    yield
    subprocess.run(["tmux", "kill-session", "-t", SESSION_NAME], capture_output=True)


class TestFindDataDir:
    def test_finds_data_dir_in_cwd(self, tmp_path):
        data = tmp_path / ".claude" / "smartassist" / "data"
        data.mkdir(parents=True, exist_ok=True)
        with patch("smartassist.claude_sa.Path.cwd", return_value=tmp_path):
            assert find_data_dir() == data

    def test_finds_data_dir_in_parent(self, tmp_path):
        data = tmp_path / ".claude" / "smartassist" / "data"
        data.mkdir(parents=True, exist_ok=True)
        child = tmp_path / "src" / "deep"
        child.mkdir(parents=True, exist_ok=True)
        with patch("smartassist.claude_sa.Path.cwd", return_value=child):
            assert find_data_dir() == data

    def test_returns_none_when_missing(self, tmp_path):
        isolated = tmp_path / "empty_root"
        isolated.mkdir(exist_ok=True)
        with patch("smartassist.claude_sa.Path.cwd", return_value=isolated):
            result = find_data_dir()
            if result is not None:
                assert not str(result).startswith(str(isolated))


class TestKillExistingSession:
    def test_does_not_raise_on_missing_session(self):
        _kill_existing_session()


class TestConstants:
    def test_session_name(self):
        assert SESSION_NAME == "claude-sa"

    def test_monitor_width(self):
        assert isinstance(MONITOR_COLS, int)
        assert 10 <= MONITOR_COLS <= 50


class TestLaunchTmux:
    @pytest.fixture
    def skip_without_tmux(self):
        if not shutil.which("tmux"):
            pytest.skip("tmux not installed")
        probe_session = f"{SESSION_NAME}-probe"
        probe = subprocess.run(
            ["tmux", "new-session", "-d", "-s", probe_session, "-x", "80", "-y", "24"],
            capture_output=True,
            text=True,
        )
        if probe.returncode != 0:
            detail = (probe.stderr or probe.stdout or "tmux session creation unavailable").strip()
            pytest.skip(detail)
        subprocess.run(["tmux", "kill-session", "-t", probe_session], capture_output=True)

    def test_creates_session(self, tmp_path, skip_without_tmux):
        log = tmp_path / "test.log"
        log.touch()
        _kill_existing_session()
        subprocess.run(
            ["tmux", "new-session", "-d", "-s", SESSION_NAME, "-x", "200", "-y", "50"],
            check=True,
        )
        result = subprocess.run(
            ["tmux", "has-session", "-t", SESSION_NAME],
            capture_output=True,
        )
        assert result.returncode == 0

    def test_creates_two_panes(self, tmp_path, skip_without_tmux):
        log = tmp_path / "test.log"
        log.touch()
        _kill_existing_session()
        subprocess.run(
            ["tmux", "new-session", "-d", "-s", SESSION_NAME, "-x", "200", "-y", "50"],
            check=True,
        )
        subprocess.run(
            [
                "tmux",
                "split-window",
                "-h",
                "-t",
                SESSION_NAME,
                "-l",
                str(MONITOR_COLS),
            ],
            check=True,
        )
        result = subprocess.run(
            ["tmux", "list-panes", "-t", SESSION_NAME],
            capture_output=True,
            text=True,
        )
        pane_count = len(result.stdout.strip().split("\n"))
        assert pane_count == 2

    def test_select_pane_focuses_left(self, tmp_path, skip_without_tmux):
        log = tmp_path / "test.log"
        log.touch()
        _kill_existing_session()
        subprocess.run(
            ["tmux", "new-session", "-d", "-s", SESSION_NAME, "-x", "200", "-y", "50"],
            check=True,
        )
        subprocess.run(
            [
                "tmux",
                "split-window",
                "-h",
                "-t",
                SESSION_NAME,
                "-l",
                str(MONITOR_COLS),
            ],
            check=True,
        )
        subprocess.run(
            ["tmux", "select-pane", "-t", SESSION_NAME, "-L"],
            check=True,
        )
        result = subprocess.run(
            [
                "tmux",
                "list-panes",
                "-t",
                SESSION_NAME,
                "-F",
                "#{pane_index}:#{pane_active}",
            ],
            capture_output=True,
            text=True,
        )
        lines = result.stdout.strip().split("\n")
        first_pane = lines[0]
        _, active = first_pane.split(":")
        assert active == "1"

    def test_idempotent_kills_existing(self, tmp_path, skip_without_tmux):
        log = tmp_path / "test.log"
        log.touch()
        subprocess.run(
            ["tmux", "new-session", "-d", "-s", SESSION_NAME, "-x", "200", "-y", "50"],
            capture_output=True,
        )
        _kill_existing_session()
        subprocess.run(
            ["tmux", "new-session", "-d", "-s", SESSION_NAME, "-x", "200", "-y", "50"],
            check=True,
        )
        subprocess.run(
            [
                "tmux",
                "split-window",
                "-h",
                "-t",
                SESSION_NAME,
                "-l",
                str(MONITOR_COLS),
            ],
            check=True,
        )
        result = subprocess.run(
            ["tmux", "list-panes", "-t", SESSION_NAME],
            capture_output=True,
            text=True,
        )
        assert len(result.stdout.strip().split("\n")) == 2

    def test_send_keys_targeted(self, tmp_path, skip_without_tmux):
        log = tmp_path / "test.log"
        log.touch()
        _kill_existing_session()
        subprocess.run(
            ["tmux", "new-session", "-d", "-s", SESSION_NAME, "-x", "200", "-y", "50"],
            check=True,
        )
        subprocess.run(
            [
                "tmux",
                "split-window",
                "-h",
                "-t",
                SESSION_NAME,
                "-l",
                str(MONITOR_COLS),
            ],
            check=True,
        )
        result = subprocess.run(
            ["tmux", "send-keys", "-t", SESSION_NAME, "echo test", "Enter"],
            capture_output=True,
        )
        assert result.returncode == 0

    def test_quotes_cwd_in_tmux_command(self, tmp_path):
        log = tmp_path / "test.log"
        log.touch()
        calls = []

        def fake_run(cmd, check=False, capture_output=False, text=False):
            calls.append(cmd)

            class Result:
                returncode = 0
                stdout = ""

            return Result()

        with (
            patch("smartassist.claude_sa._kill_existing_session"),
            patch("smartassist.claude_sa.os.getcwd", return_value="/tmp/space dir/project"),
            patch("smartassist.claude_sa.subprocess.run", side_effect=fake_run),
            patch("smartassist.claude_sa.os.execvp", side_effect=SystemExit(0)),
        ):
            with pytest.raises(SystemExit):
                _launch_tmux(log)

        send_keys_call = calls[1]
        assert "cd '/tmp/space dir/project' && claude" in send_keys_call[4]


class TestLaunchFallback:
    def test_prints_instructions_on_linux(self, tmp_path, capsys):
        data = tmp_path / ".claude" / "smartassist" / "data"
        data.mkdir(parents=True, exist_ok=True)
        log = data / "rag_live.log"
        log.touch()
        with patch("sys.platform", "linux"):
            result = _launch_fallback(log)
        assert result == 0
        out = capsys.readouterr().out
        assert "Terminal 1" in out
        assert "tail -f" in out

    def test_returns_zero_on_macos(self, tmp_path):
        data = tmp_path / ".claude" / "smartassist" / "data"
        data.mkdir(parents=True, exist_ok=True)
        log = data / "rag_live.log"
        log.touch()
        with (
            patch("sys.platform", "darwin"),
            patch("subprocess.run") as mock_run,
        ):
            result = _launch_fallback(log)
        assert result == 0
        mock_run.assert_called_once()

    def test_quotes_paths_in_macos_applescript(self, tmp_path):
        data = tmp_path / ".claude" / "smartassist" / "data"
        data.mkdir(parents=True, exist_ok=True)
        log = data / "rag live.log"
        log.touch()
        calls = []

        def fake_run(cmd, check=False):
            calls.append(cmd)

            class Result:
                returncode = 0

            return Result()

        with (
            patch("sys.platform", "darwin"),
            patch("smartassist.claude_sa.os.getcwd", return_value="/tmp/space dir/project"),
            patch("smartassist.claude_sa.subprocess.run", side_effect=fake_run),
        ):
            result = _launch_fallback(log)

        assert result == 0
        script = calls[0][2]
        assert "cd '/tmp/space dir/project' && clear && claude" in script
        assert "tail -f '/" in script


class TestMain:
    def test_returns_1_when_setup_fails(self, tmp_path, capsys):
        with (
            patch("smartassist.claude_sa.find_data_dir", return_value=None),
            patch("smartassist.claude_sa._auto_setup", return_value=False),
        ):
            assert main() == 1
        assert "setup" in capsys.readouterr().out.lower()

    def test_uses_tmux_when_available(self, tmp_path):
        data = tmp_path / ".claude" / "smartassist" / "data"
        data.mkdir(parents=True, exist_ok=True)
        with (
            patch("smartassist.claude_sa.find_data_dir", return_value=data),
            patch("shutil.which", return_value="/usr/bin/tmux"),
            patch("smartassist.claude_sa._launch_tmux", return_value=0) as mock_tmux,
        ):
            result = main()
        assert result == 0
        mock_tmux.assert_called_once()

    def test_uses_fallback_without_tmux(self, tmp_path):
        data = tmp_path / ".claude" / "smartassist" / "data"
        data.mkdir(parents=True, exist_ok=True)
        with (
            patch("smartassist.claude_sa.find_data_dir", return_value=data),
            patch("shutil.which", return_value=None),
            patch("smartassist.claude_sa._launch_fallback", return_value=0) as mock_fb,
        ):
            result = main()
        assert result == 0
        mock_fb.assert_called_once()

    def test_auto_setup_runs_when_no_data_dir(self, tmp_path):
        data = tmp_path / ".claude" / "smartassist" / "data"
        data.mkdir(parents=True, exist_ok=True)
        call_count = 0

        def find_data_dir_side_effect():
            nonlocal call_count
            call_count += 1
            return None if call_count == 1 else data

        with (
            patch(
                "smartassist.claude_sa.find_data_dir",
                side_effect=find_data_dir_side_effect,
            ),
            patch("smartassist.claude_sa._auto_setup", return_value=True) as mock_setup,
            patch("shutil.which", return_value="/usr/bin/tmux"),
            patch("smartassist.claude_sa._launch_tmux", return_value=0),
        ):
            result = main()
        assert result == 0
        mock_setup.assert_called_once()


class TestMonitor:
    def test_check_hooks_returns_dict(self):
        from smartassist.monitor import _check_hooks

        result = _check_hooks()
        assert isinstance(result, dict)
        assert len(result) == 5

    def test_check_mcp_returns_bool(self):
        from smartassist.monitor import _check_mcp

        assert isinstance(_check_mcp(), bool)

    def test_monitor_prints_status(self, capsys):
        from smartassist.monitor import main as monitor_main

        with patch("sys.argv", ["smartassist-monitor"]):
            result = monitor_main()
        assert result == 0
        out = capsys.readouterr().out
        assert "SmartAssist Monitor" in out
        assert "MCP" in out
        assert "Hooks" in out
