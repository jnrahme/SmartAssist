"""Tests for project-fact based stale lesson detection."""

import json

import pytest

from smartassist.project_facts import (
    _commit_convention,
    detect_commit_convention,
    detect_package_manager,
    stale_reason,
)


@pytest.fixture
def pnpm_project(tmp_path):
    (tmp_path / "package.json").write_text(json.dumps({"packageManager": "pnpm@11.27.0"}))
    detect_package_manager.cache_clear()
    return tmp_path


@pytest.mark.parametrize(
    "files,expected",
    [
        ({"package.json": '{"packageManager": "pnpm@9.0.0"}'}, "pnpm"),
        ({"package.json": "{}", "yarn.lock": ""}, "yarn"),
        ({"package.json": "{}", "package-lock.json": "{}"}, "npm"),
        ({}, None),
    ],
)
def test_detect_package_manager(tmp_path, files, expected):
    for name, body in files.items():
        (tmp_path / name).write_text(body)
    detect_package_manager.cache_clear()

    assert detect_package_manager(str(tmp_path)) == expected


@pytest.mark.parametrize(
    "lesson,stale",
    [
        ("`yarn test:ci` - Run tests for CI with coverage reports", True),
        ("Always commit yarn.lock and ios/Podfile.lock changes", True),
        ("Use yarn test --no-coverage for quick runs; never use npm", True),
        ("Never use npm here, use pnpm instead", False),
        ("Never use npm here", False),
        ("Don't run yarn install in this repo", False),
        ("Avoid npx for local scripts", False),
        ("Run npm install before starting", True),
        ("Use npm ci on CI, never yarn", True),
        ("Run `pnpm dlx lefthook install` before committing", False),
        ("Use semantic theme colors instead of hex values", False),
    ],
)
def test_stale_reason_in_pnpm_project(pnpm_project, lesson, stale):
    assert bool(stale_reason(lesson, pnpm_project)) is stale


def test_non_js_project_has_no_package_manager_facts(tmp_path):
    detect_package_manager.cache_clear()

    assert stale_reason("Run yarn test", tmp_path) is None


@pytest.mark.parametrize(
    "files,lesson,stale",
    [
        ({"commitlint.config.js": ""}, "Use format: [TICKET-XXX] Description", True),
        ({"package.json": '{"commitlint": {}}'}, "Commit as [TICKET-XXX] summary", True),
        ({"commitlint.config.js": ""}, "Format commit messages as [BTMAPP-XXX] Description", True),
        ({}, "Use format: [TICKET-XXX] Description", False),
        ({"commitlint.config.js": ""}, "Use feat(TICKET): message for commits", False),
    ],
)
def test_bracket_ticket_format_is_stale_under_commitlint(tmp_path, files, lesson, stale):
    for name, body in files.items():
        (tmp_path / name).write_text(body)
    detect_package_manager.cache_clear()
    _commit_convention.cache_clear()

    assert bool(stale_reason(lesson, tmp_path)) is stale


def test_detect_commit_convention_without_root():
    assert detect_commit_convention(None) is None
