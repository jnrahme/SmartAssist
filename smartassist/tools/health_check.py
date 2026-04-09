#!/usr/bin/env python3
"""
SmartAssist Health Check - Comprehensive system status dashboard.

Usage:
    smartassist health
"""

import json
import subprocess
import sys
import time
from pathlib import Path
from datetime import datetime, timedelta, timezone
from collections import Counter

from smartassist.claude_config import get_mcp_status
from smartassist.config import (
    EMBEDDING_MODEL,
    EMBEDDING_DIM,
    get_storage_path,
    get_db_path,
)

# ANSI colors for terminal output
BOLD = "\033[1m"
DIM = "\033[2m"
GREEN = "\033[32m"
RED = "\033[31m"
YELLOW = "\033[33m"
BLUE = "\033[34m"
CYAN = "\033[36m"
MAGENTA = "\033[35m"
RESET = "\033[0m"
BG_GREEN = "\033[42m"
BG_RED = "\033[41m"
BG_YELLOW = "\033[43m"
BG_BLUE = "\033[44m"


def banner(title: str, color: str = CYAN):
    w = 70
    print(f"\n{color}{BOLD}{'=' * w}")
    print(f"  {title}")
    print(f"{'=' * w}{RESET}")


def section(title: str):
    print(f"\n{BOLD}{BLUE}+{'=' * 58}+{RESET}")
    print(f"{BOLD}{BLUE}|  {title:<56}|{RESET}")
    print(f"{BOLD}{BLUE}+{'=' * 58}+{RESET}")


def ok(msg: str):
    print(f"  {GREEN}[OK]{RESET}  {msg}")


def warn(msg: str):
    print(f"  {YELLOW}[!!]{RESET}  {msg}")


def fail(msg: str):
    print(f"  {RED}[FAIL]{RESET}  {msg}")


def info(msg: str):
    print(f"     {DIM}{msg}{RESET}")


def metric(label: str, value: str, color: str = CYAN):
    print(f"  {DIM}{label:<30}{RESET}{color}{BOLD}{value}{RESET}")


def bar_chart(label: str, value: float, width: int = 30, threshold: float = 0.7):
    filled = int(value * width)
    empty = width - filled
    if value >= threshold:
        color = GREEN
    elif value >= 0.5:
        color = YELLOW
    else:
        color = RED
    pct_str = f"{value:.1%}"
    bar = f"{color}{'#' * filled}{DIM}{'.' * empty}{RESET}"
    print(f"  {label:<15} {bar} {color}{BOLD}{pct_str:>6}{RESET}")


def check_database():
    """Check LanceDB health and return stats."""
    db_path = get_db_path()
    section("VECTOR DATABASE")

    if not db_path.exists():
        fail("LanceDB directory missing")
        return False, {}

    try:
        import lancedb

        db = lancedb.connect(str(db_path))

        try:
            table = db.open_table("documents")
        except Exception:
            fail("'documents' table not found")
            return False, {}

        count = table.count_rows()
        if count == 0:
            fail("Table is empty (0 documents)")
            return False, {}

        ok(f"LanceDB operational - {BOLD}{count:,}{RESET} documents")

        # Sample instead of loading entire table (M15 scalability fix)
        sample_size = min(count, 500)
        results = table.search([0.0] * EMBEDDING_DIM).limit(sample_size).to_list()
        cats = Counter(r.get("category", "unknown") for r in results)
        general_pct = cats.get("general", 0) / count * 100

        if general_pct > 50:
            warn(f"{general_pct:.0f}% have category='general' (should be specific)")
        else:
            ok("Categories are properly assigned")
            for cat, cnt in sorted(cats.items(), key=lambda x: -x[1]):
                pct = cnt / count * 100
                print(f"     {cat:<20} {cnt:>5}  ({pct:.1f}%)")

        sample = results[:20]
        well_formatted = sum(
            1
            for r in sample
            if "Correction:" in r.get("text", "") or r.get("text", "").startswith("[")
        )
        pct = well_formatted / len(sample) * 100
        if pct >= 90:
            ok(f"{pct:.0f}% of docs have proper text format")
        else:
            warn(f"Only {pct:.0f}% have proper format (should be >90%)")

        return True, {"count": count, "categories": dict(cats), "table": table}

    except ImportError:
        fail("lancedb not installed in this Python environment")
        info("Run: pip install smartassist[dev] or pipx reinstall smartassist")
        return False, {}
    except Exception as e:
        fail(f"Database error: {e}")
        return False, {}


def check_feedback_log():
    """Check feedback log quality."""
    storage_path = get_storage_path()
    feedback_log = storage_path / "feedback_log.jsonl"
    clean_log = storage_path / "feedback_log_clean.jsonl"

    section("FEEDBACK DATA")

    if not feedback_log.exists():
        fail("feedback_log.jsonl missing")
        return False

    total = 0
    dupes = 0
    by_cat = Counter()
    by_sig = Counter()

    with open(feedback_log, "r") as f:
        for line in f:
            if not line.strip():
                continue
            try:
                e = json.loads(line)
                total += 1
                by_cat[e.get("category", "unknown")] += 1
                by_sig[e.get("signal", "unknown")] += 1
                if e.get("response") == e.get("correction") and e.get("correction"):
                    dupes += 1
            except json.JSONDecodeError:
                continue

    metric("Raw feedback events", f"{total:,}")

    print(f"\n  {BOLD}By Signal:{RESET}")
    for sig, cnt in sorted(by_sig.items(), key=lambda x: -x[1]):
        pct = cnt / total * 100
        print(f"     {sig:<20} {cnt:>5}  ({pct:.1f}%)")

    print(f"\n  {BOLD}By Category:{RESET}")
    for cat, cnt in sorted(by_cat.items(), key=lambda x: -x[1]):
        pct = cnt / total * 100
        print(f"     {cat:<20} {cnt:>5}  ({pct:.1f}%)")

    dupe_pct = dupes / total * 100 if total else 0
    if dupe_pct > 95:
        info(
            f"Response == Correction in {dupe_pct:.0f}% (PR harvester pattern - expected)"
        )
    else:
        ok(f"Duplicate response/correction: {dupe_pct:.0f}%")

    if clean_log.exists():
        clean_count = sum(1 for line in open(clean_log) if line.strip())
        removed = total - clean_count
        ok(
            f"After cleanup: {GREEN}{BOLD}{clean_count:,}{RESET} clean lessons ({removed:,} junk removed)"
        )
    else:
        info("No clean log yet (run smartassist vectorize)")

    return True


def check_reliability_scores():
    """Check Thompson Sampling scores."""
    storage_path = get_storage_path()
    reliability_file = storage_path / "reliability_scores.json"

    section("RELIABILITY SCORES (Thompson Sampling)")

    if not reliability_file.exists():
        fail("reliability_scores.json missing")
        return False

    with open(reliability_file) as f:
        data = json.load(f)

    categories = {k: v for k, v in data.items() if isinstance(v, dict) and "alpha" in v}
    if not categories:
        fail("No categories in reliability scores")
        return False

    print(f"\n  {BOLD}{'Category':<15} {'Score Bar':<38} {'Status'}{RESET}")
    print(f"  {'=' * 15} {'=' * 38} {'=' * 10}")

    weak_count = 0
    for cat, info_data in sorted(
        categories.items(),
        key=lambda x: (
            x[1].get("alpha", 1) / (x[1].get("alpha", 1) + x[1].get("beta", 1))
        ),
        reverse=True,
    ):
        alpha = info_data.get("alpha", 1)
        beta = info_data.get("beta", 1)
        score = alpha / (alpha + beta)
        bar_chart(cat, score)
        if score < 0.7:
            weak_count += 1

    print()
    if weak_count > 0:
        warn(f"{weak_count} categories below 70% threshold")
    else:
        ok("All categories above 70%")

    info(
        f"Formula: Reliability = a / (a + b)  |  Threshold: 70%  |  Decay: 30-day half-life"
    )

    return True


def check_usage_evidence():
    """Check usage log for evidence the system is being used."""
    storage_path = get_storage_path()
    usage_log = storage_path / "usage_log.jsonl"

    section("USAGE EVIDENCE")

    if not usage_log.exists():
        warn("No usage_log.jsonl yet (system hasn't been used via MCP)")
        info(
            "Usage will be recorded automatically when Claude calls rag_search or rag_dashboard"
        )
        return True

    events = []
    with open(usage_log, "r") as f:
        for line in f:
            if line.strip():
                try:
                    events.append(json.loads(line))
                except json.JSONDecodeError:
                    continue

    if not events:
        warn("Usage log is empty")
        return True

    by_tool = Counter(e.get("tool", "?") for e in events)
    searches = [e for e in events if e.get("tool") == "rag_search"]
    dashboards = [e for e in events if e.get("tool") == "rag_dashboard"]
    with_results = sum(1 for s in searches if s.get("results_count", 0) > 0)
    hit_rate = with_results / len(searches) * 100 if searches else 0
    latencies = [s.get("latency_ms", 0) for s in searches if s.get("latency_ms")]
    avg_lat = sum(latencies) / len(latencies) if latencies else 0

    now = datetime.now(tz=timezone.utc)
    last_7d = [
        e
        for e in events
        if datetime.fromisoformat(e["timestamp"]).astimezone(timezone.utc)
        > now - timedelta(days=7)
    ]
    last_24h = [
        e
        for e in events
        if datetime.fromisoformat(e["timestamp"]).astimezone(timezone.utc)
        > now - timedelta(hours=24)
    ]

    metric("Total tool calls", f"{len(events)}")
    metric("  rag_search calls", f"{len(searches)}")
    metric("  rag_dashboard calls", f"{len(dashboards)}")
    metric("Search hit rate", f"{hit_rate:.0f}% ({with_results}/{len(searches)})")
    metric("Average search latency", f"{avg_lat:.0f}ms")
    metric("Calls in last 24 hours", f"{len(last_24h)}")
    metric("Calls in last 7 days", f"{len(last_7d)}")

    recent_searches = [e for e in events if e.get("tool") == "rag_search"][-5:]
    if recent_searches:
        print(f"\n  {BOLD}Last 5 Searches:{RESET}")
        for s in recent_searches:
            ts = s.get("timestamp", "?")[11:19]
            q = s.get("query", "?")[:55]
            n = s.get("results_count", 0)
            color = GREEN if n > 0 else DIM
            print(f'     {DIM}{ts}{RESET}  {color}[{n} results]{RESET}  "{q}"')

    return True


def check_vectorization_sync():
    """Check if vectorization is in sync with feedback."""
    storage_path = get_storage_path()
    vectorization_log = storage_path / "vectorization_log.json"
    feedback_log = storage_path / "feedback_log.jsonl"

    section("VECTORIZATION STATUS")

    if not vectorization_log.exists():
        warn("No vectorization_log.json (run smartassist vectorize)")
        return False

    with open(vectorization_log) as f:
        vlog = json.load(f)

    last_time = vlog.get("last_vectorization", "unknown")
    total_vectorized = vlog.get("total_vectorized", 0)
    total_in_db = vlog.get("total_documents_in_rag", 0)

    metric("Last vectorization", last_time[:19] if len(last_time) > 19 else last_time)
    metric("Events processed", f"{total_vectorized:,}")
    if total_in_db:
        metric("Documents in DB", f"{total_in_db:,}")

    if feedback_log.exists():
        current_count = sum(1 for line in open(feedback_log) if line.strip())
        if current_count > total_vectorized:
            diff = current_count - total_vectorized
            warn(f"{diff} new events not yet vectorized")
            info("Run: smartassist vectorize")
        else:
            ok("Database is in sync with feedback log")

    return True


def check_mcp_registration():
    """Check if the MCP server is registered."""
    section("MCP SERVER")

    status = get_mcp_status()
    if not status["registered"]:
        fail("SmartAssist MCP server not registered in Claude Code config")
        return False

    ok(f"MCP server registered: {status['server_name']}")
    info(f"Config: {status['source_label']}")
    info(f"Transport: stdio")
    if status.get("entry"):
        info(f"Entry: {status['entry']}")
    if status["duplicate_sources"]:
        warn("Multiple SmartAssist MCP registrations found")
        for source in status["duplicate_sources"]:
            info(f"Also found: {source}")

    return True


def check_search_quality():
    """Run live search quality tests."""
    db_path = get_db_path()
    section("LIVE SEARCH QUALITY")

    try:
        import lancedb
        from sentence_transformers import SentenceTransformer

        model = SentenceTransformer(EMBEDDING_MODEL)
        db = lancedb.connect(str(db_path))
        table = db.open_table("documents")
    except ImportError as e:
        if "lancedb" in str(e):
            fail("lancedb not installed in this Python environment")
            info("Run: pip install smartassist[dev] or pipx reinstall smartassist")
        else:
            fail(f"Missing dependency: {e}")
        return False
    except Exception as e:
        fail(f"Cannot load search components: {e}")
        return False

    MAX_DISTANCE = 1.30
    test_queries = [
        (
            "how to style components with theme colors",
            ["semantic", "theme", "color"],
            True,
        ),
        ("how to write unit tests", ["test", "mock", "jest", "tobevisible"], True),
        ("git commit message format", ["commit", "git"], True),
        ("quantum physics dark matter theory", [], False),
    ]

    all_passed = True
    for query, expected_words, should_have_results in test_queries:
        enhanced = f"Correction for this project: {query}" if query else query
        vec = model.encode(enhanced)
        results = table.search(vec).limit(5).to_list()
        results = [r for r in results if r.get("_distance", 99) <= MAX_DISTANCE]

        if should_have_results:
            if results:
                texts = " ".join(r.get("text", "") for r in results).lower()
                has_match = any(w in texts for w in expected_words)
                if has_match:
                    cats = [r.get("category", "?") for r in results]
                    ok(f'"{query[:45]}"')
                    info(
                        f"-> {len(results)} results, categories: {', '.join(set(cats))}"
                    )
                else:
                    warn(f'"{query[:45]}" - results found but no keyword match')
                    all_passed = False
            else:
                fail(f'"{query[:45]}" - expected results, got none')
                all_passed = False
        else:
            if not results:
                ok(f'"{query[:45]}"')
                info(f"-> Correctly returned 0 results (irrelevant query filtered)")
            else:
                warn(f'"{query[:45]}" - should return nothing, got {len(results)}')
                all_passed = False

    return all_passed


def main():
    start_time = time.time()

    banner("SMARTASSIST - HEALTH CHECK", CYAN)
    print(f"  {DIM}Date: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}{RESET}")
    try:
        storage_path = get_storage_path()
        print(f"  {DIM}Data: {storage_path}{RESET}")
    except RuntimeError as e:
        print(f"  {RED}Error: {e}{RESET}")
        return 1

    checks = [
        ("Database", check_database),
        ("Feedback", check_feedback_log),
        ("Scores", check_reliability_scores),
        ("Usage", check_usage_evidence),
        ("Sync", check_vectorization_sync),
        ("MCP", check_mcp_registration),
        ("Search", check_search_quality),
    ]

    results = {}
    for name, fn in checks:
        try:
            result = fn()
            if isinstance(result, tuple):
                results[name] = result[0]
            else:
                results[name] = result
        except Exception as e:
            fail(f"Check crashed: {e}")
            results[name] = False

    # Summary
    elapsed = time.time() - start_time
    passed = sum(1 for v in results.values() if v)
    total = len(results)

    banner("SUMMARY", GREEN if passed == total else RED)

    for name, ok_val in results.items():
        if ok_val:
            print(f"  {GREEN}[OK]{RESET}  {name}")
        else:
            print(f"  {RED}[FAIL]{RESET}  {name}")

    print()
    if passed == total:
        print(f"  {BG_GREEN}{BOLD} {passed}/{total} ALL CHECKS PASSED {RESET}")
        print(f"\n  {GREEN}System is healthy and operational.{RESET}")
    else:
        print(
            f"  {BG_RED}{BOLD} {passed}/{total} CHECKS PASSED - {total - passed} NEED ATTENTION {RESET}"
        )

    print(f"\n  {DIM}Health check completed in {elapsed:.1f}s{RESET}\n")

    return 0 if passed == total else 1


if __name__ == "__main__":
    sys.exit(main())
