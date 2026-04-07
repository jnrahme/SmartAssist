from unittest.mock import patch

from smartassist.mcp_server import _write_to_live_log
from smartassist.tools.generate_dashboard import main, _parse_live_log


class TestGenerateDashboardMain:
    def test_default_mode_ensures_running(self, capsys):
        with (
            patch("sys.argv", ["smartassist", "dashboard"]),
            patch(
                "smartassist.tools.generate_dashboard.ensure_dashboard_running",
                return_value={"url": "http://localhost:3005"},
            ) as mock_ensure,
        ):
            assert main() == 0

        mock_ensure.assert_called_once_with(preferred_port=3000, open_browser=True)
        assert "Dashboard ready at: http://localhost:3005" in capsys.readouterr().out

    def test_stop_mode_uses_runtime_stop(self, capsys):
        with (
            patch("sys.argv", ["smartassist", "dashboard", "--stop"]),
            patch(
                "smartassist.tools.generate_dashboard.stop_dashboard",
                return_value=(True, "Dashboard stopped."),
            ) as mock_stop,
        ):
            assert main() == 0

        mock_stop.assert_called_once_with()
        assert "Dashboard stopped." in capsys.readouterr().out


class TestGenerateDashboardLiveFeed:
    def test_parses_timestamped_mcp_search_event(self, tmp_path):
        _write_to_live_log(
            tmp_path, "search", 'rag_search "theme colors" -> 2 result(s)'
        )

        with (
            patch(
                "smartassist.tools.generate_dashboard.get_storage_path",
                return_value=tmp_path,
            ),
            patch(
                "smartassist.tools.generate_dashboard.sync_codex_activity"
            ) as mock_sync,
        ):
            events = _parse_live_log()

        assert len(events) == 1
        assert events[0]["type"] == "search"
        assert "theme colors" in events[0]["description"]
        mock_sync.assert_called_once_with()

    def test_parses_timestamped_mcp_dashboard_event(self, tmp_path):
        _write_to_live_log(tmp_path, "dashboard", "rag_dashboard snapshot opened")

        with (
            patch(
                "smartassist.tools.generate_dashboard.get_storage_path",
                return_value=tmp_path,
            ),
            patch(
                "smartassist.tools.generate_dashboard.sync_codex_activity"
            ) as mock_sync,
        ):
            events = _parse_live_log()

        assert len(events) == 1
        assert events[0]["type"] == "tool"
        assert "rag_dashboard" in events[0]["description"]
        mock_sync.assert_called_once_with()
