"""Packaging: data files the CLI references must ship in the wheel."""

import fnmatch
import tomllib
from pathlib import Path

ROOT = Path(__file__).parent.parent


def test_every_adapter_file_is_covered_by_package_data():
    config = tomllib.loads((ROOT / "pyproject.toml").read_text())
    patterns = config["tool"]["setuptools"]["package-data"]["smartassist"]
    package = ROOT / "smartassist"

    files = [p.relative_to(package).as_posix() for p in (package / "adapters").rglob("*") if p.is_file()]
    uncovered = [f for f in files if not any(fnmatch.fnmatch(f, pattern) for pattern in patterns)]

    assert files
    assert uncovered == []
