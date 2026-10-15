"""Tests for detached, single-instance background jobs."""

import fcntl
import os
import subprocess
import sys
import time

from smartassist.config import spawn_background_job


def _hold_lock(path):
    fd = os.open(path, os.O_RDWR | os.O_CREAT, 0o600)
    fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
    return fd


def _run_exclusive(*args, timeout=10):
    return subprocess.run(
        [sys.executable, "-m", "smartassist.run_exclusive", *args],
        capture_output=True, text=True, timeout=timeout,
    )


def test_second_copy_exits_immediately_while_lock_is_held(tmp_path):
    lock = tmp_path / ".job.lock"
    fd = _hold_lock(lock)
    try:
        start = time.monotonic()
        result = _run_exclusive(str(lock), "this.module.would.fail")
        assert result.returncode == 0
        assert time.monotonic() - start < 5
    finally:
        os.close(fd)


def test_job_runs_when_lock_is_free(tmp_path):
    result = _run_exclusive(str(tmp_path / ".job.lock"), "json.tool", "--help")

    assert result.returncode == 0
    assert "usage" in result.stdout.lower()


def test_wait_mode_runs_after_lock_is_released(tmp_path):
    lock = tmp_path / ".job.lock"
    fd = _hold_lock(lock)
    proc = subprocess.Popen(
        [sys.executable, "-m", "smartassist.run_exclusive", "--wait", str(lock), "json.tool", "--help"],
        stdout=subprocess.PIPE, text=True,
    )
    time.sleep(0.5)
    assert proc.poll() is None
    os.close(fd)

    out, _ = proc.communicate(timeout=10)
    assert proc.returncode == 0
    assert "usage" in out.lower()


def test_spawn_background_job_returns_without_waiting(set_data_dir):
    start = time.monotonic()
    proc = spawn_background_job("json.tool", "--help", lock_name="json")

    assert proc is not None
    assert time.monotonic() - start < 1
    proc.wait(timeout=10)


def test_request_during_a_run_triggers_one_more_run(tmp_path):
    (tmp_path / "slow_job.py").write_text(
        "import sys, time\n"
        "open(sys.argv[1], 'a').write('run\\n')\n"
        "time.sleep(1)\n"
    )
    env = {**os.environ, "PYTHONPATH": f"{tmp_path}{os.pathsep}{os.environ.get('PYTHONPATH', '')}"}
    lock, log = str(tmp_path / ".job.lock"), str(tmp_path / "runs.log")
    cmd = [sys.executable, "-m", "smartassist.run_exclusive", lock, "slow_job", log]

    holder = subprocess.Popen(cmd, env=env)
    time.sleep(0.4)
    for _ in range(3):
        assert subprocess.run(cmd, env=env, timeout=10).returncode == 0
    holder.wait(timeout=15)

    assert (tmp_path / "runs.log").read_text().count("run") == 2
