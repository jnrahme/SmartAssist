"""Run a module only if no other copy holding the same lock is running.

Usage: python -m smartassist.run_exclusive [--wait] <lock_path> <module> [args...]

Background jobs (vectorization, cleanup) are spawned detached from short-lived
hooks; this keeps at most one per store so concurrent hooks cannot pile up
writers on the same LanceDB/SQLite files.

Without --wait, a request that finds the job running leaves a pending marker and
exits; the running copy re-runs until no request is pending, so no request is
lost. With --wait, the request queues for the lock instead.
"""

import fcntl
import os
import subprocess
import sys
from pathlib import Path


def _try_lock(fd, wait):
    try:
        fcntl.flock(fd, fcntl.LOCK_EX if wait else fcntl.LOCK_EX | fcntl.LOCK_NB)
        return True
    except BlockingIOError:
        return False


def _run_until_drained(module, args, pending):
    code = 0
    while True:
        pending.unlink(missing_ok=True)
        code = subprocess.call([sys.executable, "-m", module, *args])
        if not pending.exists():
            return code


def main(argv=None):
    argv = sys.argv[1:] if argv is None else argv
    wait = bool(argv) and argv[0] == "--wait"
    if wait:
        argv = argv[1:]
    if len(argv) < 2:
        return 2
    lock_path, module, args = Path(argv[0]), argv[1], argv[2:]
    pending = lock_path.with_name(lock_path.name + ".pending")
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    fd = os.open(lock_path, os.O_RDWR | os.O_CREAT, 0o600)
    code = 0
    try:
        if not _try_lock(fd, wait):
            pending.touch()
            # The holder may have released between our attempt and the touch
            if not _try_lock(fd, wait=False):
                return 0
        while True:
            code = _run_until_drained(module, args, pending)
            fcntl.flock(fd, fcntl.LOCK_UN)
            if not pending.exists() or not _try_lock(fd, wait=False):
                return code
    finally:
        os.close(fd)


if __name__ == "__main__":
    sys.exit(main())
