#!/usr/bin/env python3
"""Black-box retrieval benchmark for the prompt-inject hook.

Runs ``smartassist-prompt-inject`` against a throwaway copy of a data
directory for each labeled case and reports injected tokens, latency,
recall of expected lessons, and forbidden (irrelevant) hits.

Cases file (JSON list):
    [{"prompt": "...", "expect": ["L174"], "forbid": ["L120"]}, ...]
An empty ``expect`` means nothing relevant exists, so any injection is noise.

Usage:
    python benchmarks/retrieval_eval.py --data <project>/.claude/smartassist \
        --cases cases.json [--repeat 3] [--json out.json]

Pass ``--command`` as an absolute path (e.g. ``.venv/bin/smartassist-prompt-inject``)
to benchmark a working tree: the hook runs with the project as cwd, so
``uv run`` would resolve a different environment.
"""

import argparse
import json
import os
import re
import shutil
import statistics
import subprocess
import sys
import tempfile
import time
from pathlib import Path

LESSON_ID = re.compile(r"\[(L\d+)\]")


def run_case(command, data_dir, cwd, prompt):
    payload = json.dumps({"prompt": prompt, "cwd": str(cwd)})
    env = {**os.environ, "SMARTASSIST_DATA_DIR": str(data_dir)}
    start = time.perf_counter()
    proc = subprocess.run(
        command, input=payload, capture_output=True, text=True, env=env, cwd=cwd
    )
    elapsed_ms = (time.perf_counter() - start) * 1000
    context = ""
    if proc.stdout.strip():
        try:
            context = json.loads(proc.stdout)["hookSpecificOutput"]["additionalContext"]
        except (json.JSONDecodeError, KeyError):
            context = proc.stdout
    return context, elapsed_ms, proc.returncode


def score_case(case, context):
    injected = set(LESSON_ID.findall(context))
    expect = set(case.get("expect", []))
    forbid = set(case.get("forbid", []))
    hits = injected & expect
    return {
        "injected_ids": sorted(injected),
        "recall": (len(hits) / len(expect)) if expect else None,
        "noise_injection": bool(context) and not expect,
        "forbidden_hits": sorted(injected & forbid),
        "tokens": round(len(context) / 4),
    }


def evaluate(data_src, cases, command, cwd):
    results = []
    with tempfile.TemporaryDirectory() as tmp:
        for case in cases:
            data_copy = Path(tmp) / "data"
            if data_copy.exists():
                shutil.rmtree(data_copy)
            shutil.copytree(data_src, data_copy)
            context, ms, code = run_case(command, data_copy, cwd, case["prompt"])
            results.append({"prompt": case["prompt"], "ms": round(ms), "exit": code,
                            **score_case(case, context)})
    return results


def summarize(results):
    recalls = [r["recall"] for r in results if r["recall"] is not None]
    no_expect = [r for r in results if r["recall"] is None]
    return {
        "cases": len(results),
        "mean_recall": round(statistics.mean(recalls), 3) if recalls else None,
        "noise_rate": round(sum(r["noise_injection"] for r in no_expect) / len(no_expect), 3)
        if no_expect else None,
        "forbidden_hits": sum(len(r["forbidden_hits"]) for r in results),
        "mean_tokens": round(statistics.mean(r["tokens"] for r in results), 1),
        "p50_ms": round(statistics.median(r["ms"] for r in results)),
        "max_ms": max(r["ms"] for r in results),
        "errors": sum(r["exit"] != 0 for r in results),
    }


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--data", required=True, help="SmartAssist data dir (copied, never modified)")
    parser.add_argument("--cases", required=True)
    parser.add_argument("--cwd", default=None, help="Project root passed to the hook")
    parser.add_argument("--command", default="smartassist-prompt-inject")
    parser.add_argument("--repeat", type=int, default=1, help="Runs per case (reranking is stochastic)")
    parser.add_argument("--json", dest="json_out")
    args = parser.parse_args(argv)

    data_src = Path(args.data).expanduser()
    cwd = Path(args.cwd).expanduser() if args.cwd else data_src.parent.parent
    cases = json.loads(Path(args.cases).read_text())
    results = evaluate(data_src, cases * args.repeat, args.command.split(), cwd)
    summary = summarize(results)

    for r in results:
        recall = "-" if r["recall"] is None else f"{r['recall']:.0%}"
        flag = " NOISE" if r["noise_injection"] else ""
        flag += f" FORBID{r['forbidden_hits']}" if r["forbidden_hits"] else ""
        print(f"{recall:>5} {r['tokens']:>4}t {r['ms']:>5}ms  {r['prompt'][:60]}{flag}")
    print(json.dumps(summary, indent=2))
    if args.json_out:
        Path(args.json_out).write_text(json.dumps({"summary": summary, "results": results}, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
