from unittest.mock import MagicMock, patch

from smartassist.codex_sa import _auto_setup, _start_codex_sync


class TestCodexSa:
    def test_auto_setup_initializes_project_then_registers_codex(self, tmp_path):
        calls = []

        def fake_run(cmd, check=False, timeout=0, **kwargs):
            calls.append(cmd)

            class Result:
                returncode = 0

            return Result()

        data_dir = tmp_path / ".claude" / "smartassist" / "data"
        data_dir.mkdir(parents=True, exist_ok=True)

        with (
            patch("smartassist.codex_sa.subprocess.run", side_effect=fake_run),
            patch("smartassist.codex_sa.Path.cwd", return_value=tmp_path),
        ):
            assert _auto_setup() is True

        assert calls[0] == ["smartassist", "init"]
        assert calls[1] == ["smartassist", "setup-agent", "codex"]
        assert calls[2] == ["smartassist", "seed"]

    def test_start_codex_sync_reuses_existing_pid(self, tmp_path):
        data_dir = tmp_path / ".claude" / "smartassist" / "data"
        data_dir.mkdir(parents=True, exist_ok=True)
        (data_dir / "codex_sync.pid").write_text("4242")

        with (
            patch("smartassist.codex_sa._pid_running", return_value=True),
            patch("smartassist.codex_sa.subprocess.Popen") as mock_popen,
        ):
            assert _start_codex_sync(data_dir) == 4242

        mock_popen.assert_not_called()

    def test_start_codex_sync_spawns_bridge_when_missing(self, tmp_path):
        data_dir = tmp_path / ".claude" / "smartassist" / "data"
        data_dir.mkdir(parents=True, exist_ok=True)

        with patch(
            "smartassist.codex_sa.subprocess.Popen",
            return_value=MagicMock(pid=777),
        ) as mock_popen:
            pid = _start_codex_sync(data_dir)

        assert pid == 777
        assert (data_dir / "codex_sync.pid").read_text() == "777"
        mock_popen.assert_called_once()
