"""Artifact helpers for SmartAssist QA runs."""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from typing import Any
import shutil

from smartassist.gates import load_gate_stats
from smartassist.store import (
    get_store_db_path,
    list_feedback_events,
    list_lessons,
    list_search_documents,
    load_feedback_metrics_dict,
    load_last_injection_map,
    load_lesson_scores_dict,
    load_reliabilities_dict,
)


def build_run_id() -> str:
    return datetime.now().strftime("qa-%Y%m%d_%H%M%S")


def ensure_run_dir(run_dir: Path | str | None = None, *, clean: bool = False) -> Path:
    if run_dir is None:
        path = Path("qa-artifacts") / build_run_id()
    else:
        path = Path(run_dir)
    if clean and path.exists():
        for child in (
            path / "scenarios",
            path / "workspaces",
            path / "demo",
        ):
            if child.exists():
                shutil.rmtree(child)
        for file_name in ("manifest.json", "summary.json", "summary.txt", "index.html"):
            target = path / file_name
            if target.exists():
                target.unlink()
    path.mkdir(parents=True, exist_ok=True)
    return path


def write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def write_jsonl(path: Path, rows: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    text = "".join(json.dumps(row, sort_keys=True) + "\n" for row in rows)
    path.write_text(text, encoding="utf-8")


def copy_if_exists(source: Path, destination: Path) -> None:
    if not source.exists():
        return
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_bytes(source.read_bytes())


def read_json_if_exists(path: Path) -> Any:
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return None


def read_jsonl_if_exists(path: Path) -> list[Any]:
    if not path.exists():
        return []
    rows = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        try:
            rows.append(json.loads(line))
        except json.JSONDecodeError:
            continue
    return rows


def snapshot_storage(storage_path: Path) -> dict[str, Any]:
    exports = {}
    export_names = [
        "curated_lessons.json",
        "lesson_scores.json",
        "feedback_log.jsonl",
        "reliability_scores.json",
        "last_injection.json",
        "rag_session_state.json",
        "feedback_metrics.json",
        "vectorization_log.json",
        "gate_stats.json",
    ]
    for name in export_names:
        path = storage_path / name
        if name.endswith(".jsonl"):
            exports[name] = read_jsonl_if_exists(path)
        else:
            exports[name] = read_json_if_exists(path)

    return {
        "paths": {
            "storage_path": str(storage_path),
            "store_db_path": str(get_store_db_path(storage_path)),
        },
        "canonical": {
            "lessons": list_lessons(storage_path, include_inactive=True),
            "active_lessons": list_lessons(storage_path),
            "lesson_scores": load_lesson_scores_dict(storage_path),
            "feedback_events": list_feedback_events(storage_path),
            "reliabilities": load_reliabilities_dict(storage_path),
            "feedback_metrics": load_feedback_metrics_dict(storage_path),
            "last_injection": load_last_injection_map(storage_path),
            "search_documents": list_search_documents(storage_path),
            "gate_stats": load_gate_stats(storage_path),
        },
        "exports": exports,
    }
