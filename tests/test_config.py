"""Tests for smartassist.config path resolution."""

import os
from pathlib import Path

from smartassist.config import get_data_dir, get_storage_path, get_db_path


class TestConfigPaths:
    """Test that config resolves paths correctly."""

    def test_get_data_dir_from_env(self, set_data_dir):
        """SMARTASSIST_DATA_DIR env var should be respected."""
        data_dir = get_data_dir()
        assert data_dir == set_data_dir

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
