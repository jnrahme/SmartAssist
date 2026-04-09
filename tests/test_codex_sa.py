from unittest.mock import patch

from smartassist.codex_sa import _auto_setup, main


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

    def test_main_reaps_legacy_watcher_before_launching(self, tmp_path):
        data_dir = tmp_path / ".claude" / "smartassist" / "data"
        data_dir.mkdir(parents=True, exist_ok=True)

        with (
            patch("smartassist.codex_sa.find_data_dir", return_value=data_dir),
            patch("smartassist.codex_sa.reap_legacy_project_watcher") as mock_reap,
            patch(
                "smartassist.codex_sa._start_dashboard",
                return_value="http://localhost:3000",
            ),
            patch("smartassist.codex_sa.shutil.which", return_value=None),
            patch(
                "smartassist.codex_sa._launch_fallback", return_value=0
            ) as mock_launch,
        ):
            assert main() == 0

        mock_reap.assert_called_once_with(data_dir)
        mock_launch.assert_called_once()
