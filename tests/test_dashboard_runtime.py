import signal
from unittest.mock import MagicMock, patch

from smartassist.dashboard_runtime import (
    dashboard_url,
    ensure_dashboard_running,
    get_dashboard_pid_path,
    get_dashboard_state_path,
    get_running_dashboard,
    stop_dashboard,
    write_dashboard_state,
)


class TestDashboardRuntime:
    def test_write_dashboard_state_updates_pid_file(self):
        write_dashboard_state(
            {
                "pid": 321,
                "port": 3001,
                "url": dashboard_url(3001),
                "status": "running",
            }
        )

        assert get_dashboard_state_path().exists()
        assert get_dashboard_pid_path().read_text() == "321"

    def test_get_running_dashboard_uses_legacy_pid_file(self):
        get_dashboard_pid_path().write_text("555")

        with (
            patch("smartassist.dashboard_runtime.is_pid_running", return_value=True),
            patch(
                "smartassist.dashboard_runtime.fetch_dashboard_status",
                side_effect=[
                    {
                        "pid": 555,
                        "port": 3000,
                        "url": dashboard_url(3000),
                        "active_clients": 2,
                    }
                ],
            ),
        ):
            dashboard = get_running_dashboard()

        assert dashboard is not None
        assert dashboard["url"] == dashboard_url(3000)
        assert dashboard["active_clients"] == 2
        assert get_dashboard_state_path().exists()

    def test_ensure_dashboard_running_reuses_existing_server(self):
        dashboard = {
            "pid": 777,
            "port": 3002,
            "url": dashboard_url(3002),
            "active_clients": 1,
            "status": "running",
        }

        with (
            patch(
                "smartassist.dashboard_runtime.get_running_dashboard",
                return_value=dashboard,
            ),
            patch("smartassist.dashboard_runtime.webbrowser.open") as mock_open,
            patch("smartassist.dashboard_runtime.subprocess.Popen") as mock_popen,
        ):
            result = ensure_dashboard_running()

        assert result == dashboard
        mock_open.assert_called_once_with(dashboard["url"])
        mock_popen.assert_not_called()

    def test_ensure_dashboard_running_spawns_when_missing(self):
        proc = MagicMock(pid=999)
        running = {
            "pid": 999,
            "port": 3003,
            "url": dashboard_url(3003),
            "active_clients": 1,
            "status": "running",
        }

        with (
            patch(
                "smartassist.dashboard_runtime.get_running_dashboard",
                side_effect=[None, None, running],
            ),
            patch(
                "smartassist.dashboard_runtime.subprocess.Popen", return_value=proc
            ) as mock_popen,
            patch("smartassist.dashboard_runtime.webbrowser.open") as mock_open,
            patch("smartassist.dashboard_runtime.time.sleep"),
        ):
            result = ensure_dashboard_running(preferred_port=3003)

        assert result == running
        mock_popen.assert_called_once()
        mock_open.assert_called_once_with(running["url"])

    def test_stop_dashboard_kills_process_and_clears_state(self):
        write_dashboard_state(
            {
                "pid": 4242,
                "port": 3004,
                "url": dashboard_url(3004),
                "status": "running",
            }
        )

        with (
            patch(
                "smartassist.dashboard_runtime.is_pid_running",
                side_effect=[True, False],
            ),
            patch("smartassist.dashboard_runtime.os.kill") as mock_kill,
        ):
            stopped, message = stop_dashboard()

        assert stopped is True
        assert message == "Dashboard stopped."
        mock_kill.assert_called_once_with(4242, signal.SIGTERM)
        assert not get_dashboard_state_path().exists()
        assert not get_dashboard_pid_path().exists()
