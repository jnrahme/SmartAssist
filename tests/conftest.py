"""Shared test fixtures for SmartAssist.

Every test gets an isolated temporary data directory via the
SMARTASSIST_DATA_DIR env var, so no test touches real project data.
"""

import os
import pytest
from pathlib import Path


@pytest.fixture(autouse=True)
def set_data_dir(tmp_path, monkeypatch):
    """Create a temporary SmartAssist data directory and point config at it.

    Structure:
        tmp_path/
          .claude/smartassist/
            data/
            lancedb/
    """
    data_dir = tmp_path / ".claude" / "smartassist"
    data_dir.mkdir(parents=True)
    (data_dir / "data").mkdir()
    (data_dir / "lancedb").mkdir()
    monkeypatch.setenv("SMARTASSIST_DATA_DIR", str(data_dir))
    return data_dir
