"""Review the lesson corpus: report, retire (reversibly) and restore.

    smartassist review                       report stale, unverified and duplicate lessons (dry run)
    smartassist review --apply               retire the reported lessons
    smartassist review --retire L173 L175 --reason "belongs to another project"
    smartassist review --restore L173        undo a retirement
    smartassist review --rebuild-reliability [--apply]
                                             recompute category reliability from user evidence

Nothing is deleted. Every write first copies the data directory to
data/backups/review-<timestamp>/.
"""

import argparse
import math
import shutil
import time
from collections import defaultdict
from datetime import datetime
from pathlib import Path

from smartassist.config import get_storage_path
from smartassist.project_facts import stale_reason
from smartassist.store import (
    SYNTHETIC_EVENT_SOURCES,
    event_source,
    list_feedback_events,
    list_lessons,
    normalize_lesson_text,
    project_root_for_storage,
    restore_lesson,
    retire_lesson,
)

SUCCESS_SIGNALS = {"thumbs_up", "happy"}
FAILURE_SIGNALS = {"thumbs_down", "angry", "sad"}
HALF_LIFE_DAYS = 30
DECAY_FLOOR = 0.01


def find_findings(storage):
    """Return [(lesson_id, reason)] for stale, unpromoted commit-hook and duplicate lessons."""
    from smartassist.hooks.commit_hook import PROMOTED_LESSONS

    root = project_root_for_storage(storage)
    lessons = sorted(list_lessons(storage), key=lambda l: _id_number(l["id"]))
    findings = []
    first_by_text = {}
    for lesson in lessons:
        reason = stale_reason(lesson["lesson"], root)
        if reason:
            findings.append((lesson["id"], f"stale: {reason}"))
            continue
        if lesson.get("origin") == "commit_hook" and lesson["lesson"] not in PROMOTED_LESSONS:
            findings.append((lesson["id"], "commit-hook heuristic, not user-verified"))
            continue
        key = normalize_lesson_text(lesson["lesson"])
        if key in first_by_text:
            findings.append((lesson["id"], f"duplicate of {first_by_text[key]}"))
        else:
            first_by_text[key] = lesson["id"]
    return findings


def rebuild_reliability(storage, now=None):
    """Category (alpha, beta) from user-sourced events only, decayed by age."""
    now = time.time() if now is None else now
    decay_lambda = math.log(2) / (HALF_LIFE_DAYS * 86400)
    root = project_root_for_storage(storage)
    counts = defaultdict(lambda: [1.0, 1.0, 0])
    for event in list_feedback_events(storage):
        if event_source(event) in SYNTHETIC_EVENT_SOURCES:
            continue
        recorded_root = (event.get("provenance") or {}).get("project_root")
        if recorded_root and root and recorded_root != root:
            continue
        signal = event.get("signal", "")
        intensity = float(event.get("intensity") or 3)
        age = max(0.0, now - float(event.get("timestamp") or now))
        weight = max(DECAY_FLOOR, math.exp(-decay_lambda * age))
        entry = counts[event.get("category") or "unknown"]
        if signal in SUCCESS_SIGNALS:
            entry[0] += weight * intensity
        elif signal in FAILURE_SIGNALS:
            entry[1] += weight * intensity
        elif signal == "correction":
            entry[1] += weight * (intensity // 2)
        else:
            continue
        entry[2] += 1
    return {cat: tuple(values) for cat, values in counts.items()}


def backup(storage):
    target = Path(storage) / "backups" / f"review-{datetime.now():%Y%m%d-%H%M%S}"
    target.mkdir(parents=True, exist_ok=True)
    for item in Path(storage).iterdir():
        if item.is_file():
            shutil.copy2(item, target / item.name)
    return target


def _id_number(lesson_id):
    return int(lesson_id[1:]) if lesson_id[1:].isdigit() else 10**9


def _write_reliability(storage, scores):
    from smartassist.thompson_sampling import CategoryReliability, ThompsonSamplingModel

    model = ThompsonSamplingModel(str(storage))
    now = time.time()
    model.reliabilities = {
        cat: CategoryReliability(category=cat, alpha=a, beta=b, last_updated=now, total_samples=n)
        for cat, (a, b, n) in scores.items()
    }
    model._save_reliabilities()


def main(argv=None):
    parser = argparse.ArgumentParser(prog="smartassist review", description=__doc__.splitlines()[0])
    parser.add_argument("--apply", action="store_true", help="write the changes (default: dry run)")
    parser.add_argument("--retire", nargs="+", metavar="ID", help="retire these lessons")
    parser.add_argument("--reason", default="retired in review", help="reason recorded with --retire")
    parser.add_argument("--restore", nargs="+", metavar="ID", help="reactivate retired lessons")
    parser.add_argument("--rebuild-reliability", action="store_true")
    args = parser.parse_args(argv)
    storage = get_storage_path()

    if args.restore:
        print(f"Backup: {backup(storage)}")
        for lesson_id in args.restore:
            print(f"{'restored' if restore_lesson(storage, lesson_id) else 'not retired'}: {lesson_id}")
        return 0

    if args.retire:
        print(f"Backup: {backup(storage)}")
        for lesson_id in args.retire:
            retire_lesson(storage, lesson_id, retired_reason=args.reason)
            print(f"retired: {lesson_id} ({args.reason})")
        return 0

    if args.rebuild_reliability:
        scores = rebuild_reliability(storage)
        for cat, (a, b, n) in sorted(scores.items()):
            print(f"  {cat:<14} {a / (a + b):6.1%}  from {n} user event(s)")
        if args.apply:
            print(f"Backup: {backup(storage)}")
            _write_reliability(storage, scores)
            print("Reliability rebuilt from user evidence.")
        else:
            print("Dry run. Re-run with --apply to write.")
        return 0

    findings = find_findings(storage)
    lessons = {l["id"]: l["lesson"] for l in list_lessons(storage)}
    for lesson_id, reason in findings:
        print(f"  {lesson_id:<6} {reason:<45} {lessons.get(lesson_id, '')[:70]}")
    print(f"{len(findings)} lesson(s) to retire.")
    if args.apply and findings:
        print(f"Backup: {backup(storage)}")
        for lesson_id, reason in findings:
            retire_lesson(storage, lesson_id, retired_reason=f"review: {reason}")
        print(f"Retired {len(findings)}. Undo with: smartassist review --restore <ID>")
    elif findings:
        print("Dry run. Re-run with --apply to retire them.")
    return 0
