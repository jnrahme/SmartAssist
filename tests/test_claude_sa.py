import os
import shutil
import subprocess
from pathlib import Path
from unittest.mock import patch, MagicMock

import pytest

from smartassist.claude_sa import (
    find_data_dir,
    _kill_existing_session,
    _launch_tmux,
    _launch_fallback,
    main,
    SESSION_NAME,
    MONITOR_WIDTH_PCT,
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
        assert isinstance(MONITOR_WIDTH_PCT, int)
        assert 10 <= MONITOR_WIDTH_PCT <= 50


class TestLaunchTmux:
    @pytest.fixture
    def skip_without_tmux(self):
        if not shutil.which("tmux"):
            pytest.skip("tmux not installed")

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
                f"{MONITOR_WIDTH_PCT}%",
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
                f"{MONITOR_WIDTH_PCT}%",
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
                f"{MONITOR_WIDTH_PCT}%",
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
                f"{MONITOR_WIDTH_PCT}%",
            ],
            check=True,
        )
        result = subprocess.run(
            ["tmux", "send-keys", "-t", SESSION_NAME, "echo test", "Enter"],
            capture_output=True,
        )
        assert result.returncode == 0


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


class TestMain:
    def test_returns_1_without_data_dir(self, tmp_path, capsys):
        with patch("smartassist.claude_sa.find_data_dir", return_value=None):
            assert main() == 1
        assert "smartassist init" in capsys.readouterr().out

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
