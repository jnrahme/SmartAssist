"""Tests for smartassist.config path resolution and I/O utilities."""

import json
import os
import sys
import threading
from pathlib import Path

from smartassist.config import (
    get_data_dir, get_storage_path, get_db_path, get_project_root,
    atomic_write_json, locked_update_json, spawn_managed,
)


class TestConfigPaths:
    """Test that config resolves paths correctly."""

    def test_get_data_dir_from_env(self, set_data_dir):
        """SMARTASSIST_DATA_DIR env var should be respected."""
        data_dir = get_data_dir()
        assert data_dir == set_data_dir

    def test_get_data_dir_from_cwd_when_env_missing(self, monkeypatch, tmp_path):
        project_root = tmp_path / "project"
        data_dir = project_root / ".claude" / "smartassist"
        (data_dir / "data").mkdir(parents=True)
        (data_dir / "lancedb").mkdir()
        nested = project_root / "nested"
        nested.mkdir(parents=True, exist_ok=True)
        monkeypatch.delenv("SMARTASSIST_DATA_DIR", raising=False)
        monkeypatch.chdir(nested)

        assert get_data_dir() == data_dir

    def test_get_storage_path_returns_data_subdir(self, set_data_dir):
        storage = get_storage_path()
        assert storage == set_data_dir / "data"
        assert storage.exists()

    def test_get_db_path_returns_lancedb_subdir(self, set_data_dir):
        db = get_db_path()
        assert db == set_data_dir / "lancedb"
        assert db.exists()

    def test_storage_path_is_directory(self, set_data_dir):
        storage = get_storage_path()
        assert storage.is_dir()

    def test_db_path_is_directory(self, set_data_dir):
        db = get_db_path()
        assert db.is_dir()

    def test_get_project_root_uses_cwd_for_custom_data_dir(self, monkeypatch, tmp_path):
        project_root = tmp_path / "project"
        data_dir = project_root / ".claude" / "smartassist"
        (data_dir / "data").mkdir(parents=True)
        (data_dir / "lancedb").mkdir()

        custom_data_dir = tmp_path / "external-data"
        custom_data_dir.mkdir()
        nested = project_root / "nested"
        nested.mkdir(parents=True, exist_ok=True)

        monkeypatch.setenv("SMARTASSIST_DATA_DIR", str(custom_data_dir))
        monkeypatch.chdir(nested)

        assert get_project_root() == project_root


class TestAtomicWriteJson:
    """Tests for atomic_write_json (C2 fix)."""

    def test_writes_valid_json(self, tmp_path):
        target = tmp_path / "test.json"
        atomic_write_json(target, {"key": "value"})
        assert json.loads(target.read_text()) == {"key": "value"}

    def test_no_partial_write_on_error(self, tmp_path):
        target = tmp_path / "test.json"
        atomic_write_json(target, {"original": True})

        # Try to write a non-serializable object — should raise, original intact
        class NotSerializable:
            pass
        try:
            atomic_write_json(target, {"bad": NotSerializable()})
        except TypeError:
            pass

        assert json.loads(target.read_text()) == {"original": True}

    def test_no_leftover_tmp_files_on_error(self, tmp_path):
        target = tmp_path / "test.json"
        class NotSerializable:
            pass
        try:
            atomic_write_json(target, {"bad": NotSerializable()})
        except TypeError:
            pass

        tmp_files = list(tmp_path.glob("*.tmp"))
        assert tmp_files == []

    def test_creates_parent_directories(self, tmp_path):
        target = tmp_path / "nested" / "deep" / "test.json"
        atomic_write_json(target, [1, 2, 3])
        assert json.loads(target.read_text()) == [1, 2, 3]


class TestLockedUpdateJson:
    """Tests for locked_update_json (C3 fix)."""

    def test_basic_read_modify_write(self, tmp_path):
        target = tmp_path / "counter.json"
        atomic_write_json(target, {"count": 0})

        def increment(data):
            data["count"] += 1
            return data

        result = locked_update_json(target, increment, default={"count": 0})
        assert result == {"count": 1}
        assert json.loads(target.read_text()) == {"count": 1}

    def test_uses_default_when_file_missing(self, tmp_path):
        target = tmp_path / "new.json"

        def add_key(data):
            data["added"] = True
            return data

        result = locked_update_json(target, add_key, default={})
        assert result == {"added": True}

    def test_skips_write_when_updater_returns_none(self, tmp_path):
        target = tmp_path / "test.json"
        atomic_write_json(target, {"original": True})

        result = locked_update_json(target, lambda _: None)
        assert result is None
        assert json.loads(target.read_text()) == {"original": True}

    def test_concurrent_updates_are_serialized(self, tmp_path):
        target = tmp_path / "counter.json"
        atomic_write_json(target, {"count": 0})
        n_threads = 10

        def increment(data):
            data["count"] += 1
            return data

        def worker():
            locked_update_json(target, increment, default={"count": 0})

        threads = [threading.Thread(target=worker) for _ in range(n_threads)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        assert json.loads(target.read_text()) == {"count": n_threads}

    def test_recovers_from_corrupted_file(self, tmp_path):
        target = tmp_path / "bad.json"
        target.write_text("{corrupt")

        result = locked_update_json(target, lambda d: d or {"recovered": True}, default=None)
        assert result == {"recovered": True}


class TestSpawnManaged:
    """Tests for spawn_managed (H5 fix)."""

    def test_spawns_and_tracks_process(self, tmp_path):
        from smartassist.config import _CHILD_PROCS, _reap_children
        initial_count = len(_CHILD_PROCS)

        marker = tmp_path / "marker.txt"
        proc = spawn_managed(
            [sys.executable, "-c", f"from pathlib import Path; Path('{marker}').write_text('done')"],
        )
        assert proc is not None
        proc.wait(timeout=5)
        assert marker.read_text() == "done"

        _reap_children()
        # After reaping a finished process, it should be removed
        assert len(_CHILD_PROCS) <= initial_count
