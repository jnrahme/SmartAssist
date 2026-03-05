"""Tests for claude_sa.py — the tmux-based launcher."""

import os
import shutil
import subprocess
from pathlib import Path
from unittest.mock import patch, MagicMock

import pytest

from smartassist.claude_sa import (
    find_data_dir,
    _has_tty,
    _inside_tmux,
    _get_terminal_cols,
    _compute_monitor_cols,
    _build_tmux_session,
    main,
    SESSION_NAME,
    MIN_COLS_FOR_SIDE_BY_SIDE,
    MONITOR_MIN_COLS,
    MONITOR_MAX_COLS,
)


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


class TestHasTty:
    def test_no_tty_in_test_env(self):
        assert isinstance(_has_tty(), bool)

    def test_handles_os_error(self):
        with patch("os.isatty", side_effect=OSError):
            assert _has_tty() is False

    def test_handles_value_error(self):
        with patch("os.isatty", side_effect=ValueError):
            assert _has_tty() is False


class TestInsideTmux:
    def test_false_without_env(self):
        with patch.dict(os.environ, {}, clear=True):
            assert _inside_tmux() is False

    def test_true_with_env(self):
        with patch.dict(os.environ, {"TMUX": "/tmp/tmux-501/default,12345,0"}):
            assert _inside_tmux() is True


class TestGetTerminalCols:
    def test_returns_int(self):
        result = _get_terminal_cols()
        assert isinstance(result, int)
        assert result > 0

    def test_fallback_on_error(self):
        with patch("os.get_terminal_size", side_effect=OSError):
            assert _get_terminal_cols() == 200


class TestComputeMonitorCols:
    def test_narrow_terminal_clamps_to_min(self):
        assert _compute_monitor_cols(100) == MONITOR_MIN_COLS

    def test_wide_terminal_clamps_to_max(self):
        assert _compute_monitor_cols(400) == MONITOR_MAX_COLS

    def test_medium_terminal_scales(self):
        result = _compute_monitor_cols(200)
        assert MONITOR_MIN_COLS <= result <= MONITOR_MAX_COLS
        assert result == 60

    def test_very_narrow_uses_min(self):
        assert _compute_monitor_cols(50) == MONITOR_MIN_COLS


class TestBuildTmuxSession:
    @pytest.fixture
    def skip_without_tmux(self):
        if not shutil.which("tmux"):
            pytest.skip("tmux not installed")

    def test_creates_session(self, tmp_path, skip_without_tmux):
        log = tmp_path / "test.log"
        log.touch()
        subprocess.run(
            ["tmux", "kill-session", "-t", SESSION_NAME], capture_output=True
        )
        _build_tmux_session(log, "-h", "50")
        result = subprocess.run(
            ["tmux", "has-session", "-t", SESSION_NAME],
            capture_output=True,
        )
        assert result.returncode == 0
        subprocess.run(
            ["tmux", "kill-session", "-t", SESSION_NAME], capture_output=True
        )

    def test_creates_two_panes(self, tmp_path, skip_without_tmux):
        log = tmp_path / "test.log"
        log.touch()
        subprocess.run(
            ["tmux", "kill-session", "-t", SESSION_NAME], capture_output=True
        )
        _build_tmux_session(log, "-h", "50")
        result = subprocess.run(
            ["tmux", "list-panes", "-t", SESSION_NAME],
            capture_output=True,
            text=True,
        )
        pane_count = len(result.stdout.strip().split("\n"))
        assert pane_count == 2
        subprocess.run(
            ["tmux", "kill-session", "-t", SESSION_NAME], capture_output=True
        )

    def test_pane_titles_set(self, tmp_path, skip_without_tmux):
        log = tmp_path / "test.log"
        log.touch()
        subprocess.run(
            ["tmux", "kill-session", "-t", SESSION_NAME], capture_output=True
        )
        _build_tmux_session(log, "-h", "50")
        result = subprocess.run(
            ["tmux", "list-panes", "-t", SESSION_NAME, "-F", "#{pane_title}"],
            capture_output=True,
            text=True,
        )
        titles = result.stdout.strip().split("\n")
        assert "Claude Code" in titles
        assert "RAG Monitor" in titles
        subprocess.run(
            ["tmux", "kill-session", "-t", SESSION_NAME], capture_output=True
        )

    def test_vertical_split(self, tmp_path, skip_without_tmux):
        log = tmp_path / "test.log"
        log.touch()
        subprocess.run(
            ["tmux", "kill-session", "-t", SESSION_NAME], capture_output=True
        )
        _build_tmux_session(log, "-v", "30%")
        result = subprocess.run(
            ["tmux", "list-panes", "-t", SESSION_NAME],
            capture_output=True,
            text=True,
        )
        assert len(result.stdout.strip().split("\n")) == 2
        subprocess.run(
            ["tmux", "kill-session", "-t", SESSION_NAME], capture_output=True
        )

    def test_idempotent_kills_existing(self, tmp_path, skip_without_tmux):
        log = tmp_path / "test.log"
        log.touch()
        _build_tmux_session(log, "-h", "50")
        _build_tmux_session(log, "-h", "50")
        result = subprocess.run(
            ["tmux", "list-panes", "-t", SESSION_NAME],
            capture_output=True,
            text=True,
        )
        assert len(result.stdout.strip().split("\n")) == 2
        subprocess.run(
            ["tmux", "kill-session", "-t", SESSION_NAME], capture_output=True
        )

    def test_left_pane_is_active(self, tmp_path, skip_without_tmux):
        log = tmp_path / "test.log"
        log.touch()
        subprocess.run(
            ["tmux", "kill-session", "-t", SESSION_NAME], capture_output=True
        )
        _build_tmux_session(log, "-h", "50")
        result = subprocess.run(
            [
                "tmux",
                "list-panes",
                "-t",
                SESSION_NAME,
                "-F",
                "#{pane_title}:#{pane_active}",
            ],
            capture_output=True,
            text=True,
        )
        for line in result.stdout.strip().split("\n"):
            title, active = line.split(":")
            if title == "Claude Code":
                assert active == "1"
            elif title == "RAG Monitor":
                assert active == "0"
        subprocess.run(
            ["tmux", "kill-session", "-t", SESSION_NAME], capture_output=True
        )


class TestMainNoDataDir:
    def test_returns_1_without_data_dir(self, tmp_path, capsys):
        with patch("smartassist.claude_sa.find_data_dir", return_value=None):
            assert main() == 1
        assert "smartassist init" in capsys.readouterr().out


class TestMainFallback:
    def test_prints_instructions_on_linux(self, tmp_path, capsys):
        data = tmp_path / ".claude" / "smartassist" / "data"
        data.mkdir(parents=True, exist_ok=True)
        log = data / "rag_live.log"
        with (
            patch("smartassist.claude_sa.find_data_dir", return_value=data),
            patch("shutil.which", return_value=None),
            patch("smartassist.claude_sa._has_tty", return_value=True),
            patch("smartassist.claude_sa._inside_tmux", return_value=False),
            patch("sys.platform", "linux"),
        ):
            result = main()
        assert result == 0
        out = capsys.readouterr().out
        assert "Terminal 1" in out
        assert "tail -f" in out
