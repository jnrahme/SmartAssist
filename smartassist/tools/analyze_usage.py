#!/usr/bin/env python3
"""
Usage Analytics - Reads usage_log.jsonl and produces key metrics.

Metrics:
  - Search hit rate (% of searches returning results)
  - Latency distribution (p50/p95/p99)
  - Category trends
  - Query patterns
  - Tool call distribution

Usage:
    smartassist analyze
"""

import json
import sys
from datetime import datetime, timedelta
from collections import Counter

from smartassist.config import get_storage_path

BOLD = "\033[1m"
DIM = "\033[2m"
GREEN = "\033[32m"
RED = "\033[31m"
YELLOW = "\033[33m"
CYAN = "\033[36m"
RESET = "\033[0m"


def load_events():
    """Load all usage events."""
    storage_path = get_storage_path()
    usage_log = storage_path / "usage_log.jsonl"
    if not usage_log.exists():
        print(f"{RED}No usage_log.jsonl found{RESET}")
        return []
    events = []
    with open(usage_log, "r", encoding="utf-8") as f:
        for line in f:
            if line.strip():
                try:
                    events.append(json.loads(line))
                except json.JSONDecodeError:
                    continue
    return events


def analyze():
    events = load_events()
    if not events:
        return

    print(f"\n{CYAN}{BOLD}{'=' * 60}")
    print("  RAG USAGE ANALYTICS")
    print(f"{'=' * 60}{RESET}")
    print(f"  {DIM}Total events: {len(events):,}{RESET}\n")

    # Tool distribution
    by_tool = Counter(e.get("tool", "?") for e in events)
    searches = [e for e in events if e.get("tool") == "rag_search"]
    dashboards = [e for e in events if e.get("tool") == "rag_dashboard"]
    feedbacks = [e for e in events if e.get("tool") == "rag_feedback"]

    print(f"{BOLD}Tool Distribution:{RESET}")
    for tool, count in by_tool.most_common():
        pct = count / len(events) * 100
        print(f"  {tool:<20} {count:>6}  ({pct:.1f}%)")

    # Search hit rate
    print(f"\n{BOLD}Search Performance:{RESET}")
    if searches:
        hits = sum(1 for s in searches if s.get("results_count", 0) > 0)
        hit_rate = hits / len(searches) * 100
        color = GREEN if hit_rate >= 70 else YELLOW if hit_rate >= 50 else RED
        print(f"  Hit rate:        {color}{hit_rate:.1f}%{RESET} ({hits}/{len(searches)})")

        latencies = sorted(s.get("latency_ms", 0) for s in searches if s.get("latency_ms"))
        if latencies:
            p50 = latencies[len(latencies) // 2]
            p95 = latencies[int(len(latencies) * 0.95)]
            p99 = latencies[int(len(latencies) * 0.99)]
            print(f"  Latency p50:     {p50:.0f}ms")
            print(f"  Latency p95:     {p95:.0f}ms")
            print(f"  Latency p99:     {p99:.0f}ms")

        avg_results = sum(s.get("results_count", 0) for s in searches) / len(searches)
        print(f"  Avg results:     {avg_results:.1f}")
    else:
        print(f"  {DIM}No searches recorded{RESET}")

    # Category trends
    print(f"\n{BOLD}Category Distribution (from search results):{RESET}")
    enriched = [e for e in searches if "lessons" in e]
    if enriched:
        cat_counter = Counter()
        for e in enriched:
            for lesson in e.get("lessons", []):
                cat_counter[lesson.get("category", "unknown")] += 1
        for cat, count in cat_counter.most_common():
            print(f"  {cat:<20} {count:>6}")
    else:
        print(f"  {DIM}No enriched entries{RESET}")

    # Search funnel
    if enriched:
        print(f"\n{BOLD}Search Funnel (avg over {len(enriched)} enriched searches):{RESET}")
        avg_raw = sum(e.get("search_meta", {}).get("raw_count", 0) for e in enriched) / len(enriched)
        avg_dist = sum(e.get("search_meta", {}).get("distance_filtered", 0) for e in enriched) / len(enriched)
        avg_cat = sum(e.get("search_meta", {}).get("category_filtered", 0) for e in enriched) / len(enriched)
        avg_returned = sum(len(e.get("lessons", [])) for e in enriched) / len(enriched)
        print(f"  Candidates fetched:    {avg_raw:.1f}")
        print(f"  Filtered by distance:  {avg_dist:.1f}")
        print(f"  Filtered by category:  {avg_cat:.1f}")
        print(f"  Lessons returned:      {avg_returned:.1f}")

    # Time distribution
    print(f"\n{BOLD}Activity Over Time:{RESET}")
    now = datetime.now()
    periods = [
        ("Last 24 hours", timedelta(hours=24)),
        ("Last 7 days", timedelta(days=7)),
        ("Last 30 days", timedelta(days=30)),
    ]
    for label, delta in periods:
        cutoff = now - delta
        count = sum(
            1 for e in events
            if datetime.fromisoformat(e.get("timestamp", "1970-01-01")) > cutoff
        )
        print(f"  {label:<20} {count:>6} events")

    # Top queries
    print(f"\n{BOLD}Top 10 Queries:{RESET}")
    query_counter = Counter(s.get("query", "")[:80] for s in searches if s.get("query"))
    for query, count in query_counter.most_common(10):
        print(f"  [{count:>3}x] {query}")

    # Feedback summary
    if feedbacks:
        print(f"\n{BOLD}Feedback Summary:{RESET}")
        helpful = sum(1 for f in feedbacks if "helpful=True" in f.get("query", ""))
        not_helpful = sum(1 for f in feedbacks if "helpful=False" in f.get("query", ""))
        print(f"  Helpful:     {GREEN}{helpful}{RESET}")
        print(f"  Not helpful: {RED}{not_helpful}{RESET}")

    print(f"\n{DIM}Analysis complete.{RESET}\n")


if __name__ == "__main__":
    analyze()
