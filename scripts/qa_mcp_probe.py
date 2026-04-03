#!/usr/bin/env python3
"""Probe SmartAssist MCP server startup and required tool contract."""

from __future__ import annotations

import argparse
import importlib
import json
import os
import subprocess
import sys
import time
from pathlib import Path

from smartassist.runtime import resolve_cli_invocation


def _check_required_symbols(required_tools: set[str]) -> tuple[bool, list[str]]:
    module = importlib.import_module("smartassist.mcp_server")
    missing = [name for name in sorted(required_tools) if not hasattr(module, name)]
    return len(missing) == 0, missing


def run_probe(timeout_s: float, required_tools: set[str]) -> dict:
    t0 = time.time()
    ok, missing = _check_required_symbols(required_tools)
    if not ok:
        raise RuntimeError(f"Missing required MCP tool symbols: {', '.join(missing)}")

    repo_root = Path(__file__).resolve().parents[1]
    invocation = resolve_cli_invocation(
        prefer_source_checkout=True,
        start_path=repo_root,
    )
    env = os.environ.copy()
    env.update(invocation.env)

    proc = subprocess.Popen(
        [*invocation.argv, "serve"],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        env=env,
    )
    try:
        warmup_deadline = time.time() + timeout_s
        while time.time() < warmup_deadline:
            if proc.poll() is not None:
                if proc.returncode == 0:
                    return {
                        "status": "pass",
                        "latency_ms": int((time.time() - t0) * 1000),
                        "required_tools": sorted(required_tools),
                        "startup": "graceful_exit",
                        "command": invocation.label,
                    }
                stderr = (proc.stderr.read() if proc.stderr else "")[:600]
                raise RuntimeError(
                    f"{invocation.label} serve exited early with code {proc.returncode}. stderr: {stderr}"
                )
            time.sleep(0.2)

        return {
            "status": "pass",
            "latency_ms": int((time.time() - t0) * 1000),
            "required_tools": sorted(required_tools),
            "startup": "healthy",
            "command": invocation.label,
        }
    finally:
        proc.terminate()
        try:
            proc.wait(timeout=2)
        except subprocess.TimeoutExpired:
            proc.kill()


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--timeout", type=float, default=3.0, help="Startup probe duration in seconds")
    parser.add_argument(
        "--required-tools",
        default="rag_search,rag_feedback,rag_dashboard",
        help="Comma-separated required MCP tool names",
    )
    args = parser.parse_args()
    required = {t.strip() for t in args.required_tools.split(",") if t.strip()}

    try:
        result = run_probe(args.timeout, required)
        print(json.dumps(result))
        return 0
    except Exception as e:
        print(json.dumps({"status": "fail", "error": str(e)}))
        return 1


if __name__ == "__main__":
    sys.exit(main())
