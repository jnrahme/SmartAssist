"""Helpers for resolving SmartAssist runtime invocation modes."""

from __future__ import annotations

import os
import shutil
import sys
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class CliInvocation:
    """Resolved SmartAssist CLI entrypoint."""

    argv: tuple[str, ...]
    mode: str
    label: str
    env: dict[str, str]


def find_source_checkout_root(start_path: Path | None = None) -> Path | None:
    """Return the nearest SmartAssist source checkout root when present."""
    candidates = []
    if start_path is not None:
        candidates.append(start_path)
    else:
        candidates.append(Path.cwd())
    candidates.append(Path(__file__).resolve())

    seen = set()
    for candidate_root in candidates:
        current = candidate_root.resolve()
        for parent in [current, *current.parents]:
            if parent in seen:
                continue
            seen.add(parent)
            if (parent / "pyproject.toml").is_file() and (
                parent / "smartassist" / "__init__.py"
            ).is_file():
                return parent
    return None


def resolve_cli_invocation(
    *,
    prefer_source_checkout: bool = False,
    start_path: Path | None = None,
) -> CliInvocation:
    """Resolve how to invoke SmartAssist for the current environment.

    When running QA from a source checkout, prefer the current checkout over any
    globally installed binary so the verification path exercises the code under
    test rather than whatever happens to be on PATH.
    """
    repo_root = find_source_checkout_root(start_path)

    if prefer_source_checkout and repo_root is not None:
        pythonpath = os.environ.get("PYTHONPATH")
        merged_pythonpath = str(repo_root)
        if pythonpath:
            merged_pythonpath = merged_pythonpath + os.pathsep + pythonpath
        return CliInvocation(
            argv=(sys.executable, "-m", "smartassist.cli"),
            mode="source_checkout",
            label=f"{sys.executable} -m smartassist.cli",
            env={"PYTHONPATH": merged_pythonpath},
        )

    installed = shutil.which("smartassist")
    if installed:
        return CliInvocation(
            argv=(installed,),
            mode="installed",
            label=installed,
            env={},
        )

    if repo_root is not None:
        pythonpath = os.environ.get("PYTHONPATH")
        merged_pythonpath = str(repo_root)
        if pythonpath:
            merged_pythonpath = merged_pythonpath + os.pathsep + pythonpath
        return CliInvocation(
            argv=(sys.executable, "-m", "smartassist.cli"),
            mode="source_checkout",
            label=f"{sys.executable} -m smartassist.cli",
            env={"PYTHONPATH": merged_pythonpath},
        )

    raise RuntimeError(
        "SmartAssist CLI not found on PATH and no source checkout was detected."
    )
