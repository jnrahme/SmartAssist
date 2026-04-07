#!/usr/bin/env python3
"""
Generate a live HTML dashboard from current SmartAssist data.
Opens in the default browser when run.

Usage:
    smartassist dashboard [--output PATH]
"""

import html
import json
import os
import webbrowser
import sys
from pathlib import Path
from datetime import datetime, timedelta
from collections import Counter

from smartassist.claude_config import get_mcp_status as get_shared_mcp_status
from smartassist.config import get_storage_path, get_db_path, EMBEDDING_DIM, EMBEDDING_MODEL


def get_db_stats():
    """Get vector database stats and all lessons for search."""
    db_path = get_db_path()
    try:
        import lancedb
        db = lancedb.connect(str(db_path))
        table = db.open_table("documents")
        count = table.count_rows()
        # Only fetch a sample instead of the full table (M15 scalability fix)
        sample_size = min(count, 200)
        results = table.search([0.0] * EMBEDDING_DIM).limit(sample_size).to_list()
        cats = Counter(r.get("category", "unknown") for r in results)
        sample = results[:20]
        correction_pct = sum(1 for r in sample if r.get("text", "").startswith("[")) / len(sample) * 100 if sample else 0

        lessons = []
        for r in results:
            raw = r.get("text", "")
            cat = r.get("category", "unknown")
            lesson_text = ""
            context = ""

            if raw.startswith("["):
                bracket_end = raw.find("] ")
                if bracket_end > 0:
                    lesson_text = raw[bracket_end + 2:]
                    if " Context: " in lesson_text:
                        parts = lesson_text.split(" Context: ", 1)
                        lesson_text = parts[0]
                        context = parts[1]
                else:
                    lesson_text = raw
            else:
                for line in raw.split("\n"):
                    s = line.strip()
                    if s.startswith("Correction:"):
                        lesson_text = s.replace("Correction:", "").strip()
                    elif s.startswith("Context:"):
                        context = s.replace("Context:", "").strip()

            if lesson_text:
                lessons.append({
                    "lesson": lesson_text[:500],
                    "category": cat,
                    "query": "",
                    "response": "",
                    "context": context[:200],
                })

        return {
            "count": count,
            "categories": dict(sorted(cats.items(), key=lambda x: -x[1])),
            "correction_pct": correction_pct,
            "healthy": True,
            "lessons": lessons,
        }
    except Exception as e:
        return {"count": 0, "categories": {}, "correction_pct": 0, "healthy": False, "error": str(e), "lessons": []}


def get_feedback_stats():
    """Get feedback log stats."""
    storage_path = get_storage_path()
    feedback_log = storage_path / "feedback_log.jsonl"
    clean_log = storage_path / "feedback_log_clean.jsonl"

    if not feedback_log.exists():
        return {"total": 0, "by_cat": {}, "by_sig": {}, "dupes": 0}

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

    clean_count = 0
    if clean_log.exists():
        clean_count = sum(1 for line in open(clean_log) if line.strip())

    return {
        "total": total,
        "clean": clean_count,
        "by_cat": dict(sorted(by_cat.items(), key=lambda x: -x[1])),
        "by_sig": dict(sorted(by_sig.items(), key=lambda x: -x[1])),
        "dupes": dupes,
    }


def get_reliability_scores():
    """Get Thompson Sampling scores."""
    storage_path = get_storage_path()
    reliability_file = storage_path / "reliability_scores.json"

    if not reliability_file.exists():
        return {}
    with open(reliability_file) as f:
        data = json.load(f)
    scores = {}
    for k, v in data.items():
        if isinstance(v, dict) and "alpha" in v:
            alpha = v.get("alpha", 1)
            beta = v.get("beta", 1)
            scores[k] = alpha / (alpha + beta)
    return dict(sorted(scores.items(), key=lambda x: -x[1]))


def get_usage_stats():
    """Get usage evidence stats."""
    storage_path = get_storage_path()
    usage_log = storage_path / "usage_log.jsonl"

    if not usage_log.exists():
        return {"total": 0, "searches": 0, "dashboards": 0, "hit_rate": 0,
                "avg_latency": 0, "recent": [], "last_7d": 0, "last_24h": 0,
                "avg_lessons": 0, "enriched_count": 0}

    events = []
    with open(usage_log, "r") as f:
        for line in f:
            if line.strip():
                try:
                    events.append(json.loads(line))
                except json.JSONDecodeError:
                    continue

    searches = [e for e in events if e.get("tool") == "rag_search"]
    dashboards = [e for e in events if e.get("tool") == "rag_dashboard"]
    with_results = sum(1 for s in searches if s.get("results_count", 0) > 0)
    hit_rate = with_results / len(searches) * 100 if searches else 0
    latencies = [s.get("latency_ms", 0) for s in searches if s.get("latency_ms")]
    avg_lat = sum(latencies) / len(latencies) if latencies else 0

    now = datetime.now()
    last_7d = sum(1 for e in events if datetime.fromisoformat(e["timestamp"]) > now - timedelta(days=7))
    last_24h = sum(1 for e in events if datetime.fromisoformat(e["timestamp"]) > now - timedelta(hours=24))

    recent = []
    for s in searches[-10:]:
        entry = {
            "time": s.get("timestamp", "?")[11:19],
            "query": s.get("query", "?")[:60],
            "results": s.get("results_count", 0),
            "latency": s.get("latency_ms", 0),
        }
        if "lessons" in s:
            entry["lessons"] = s["lessons"]
        if "search_meta" in s:
            entry["search_meta"] = s["search_meta"]
        recent.append(entry)

    enriched = [e for e in events if "search_meta" in e]
    avg_lessons = 0
    if enriched:
        avg_lessons = sum(len(e.get("lessons", [])) for e in enriched) / len(enriched)

    return {
        "total": len(events),
        "searches": len(searches),
        "dashboards": len(dashboards),
        "hit_rate": hit_rate,
        "avg_latency": avg_lat,
        "recent": recent,
        "last_7d": last_7d,
        "last_24h": last_24h,
        "avg_lessons": avg_lessons,
        "enriched_count": len(enriched),
    }


def get_sync_status():
    """Get vectorization sync status."""
    storage_path = get_storage_path()
    vectorization_log = storage_path / "vectorization_log.json"
    feedback_log = storage_path / "feedback_log.jsonl"

    if not vectorization_log.exists():
        return {"synced": False, "last_time": "never", "vectorized": 0, "in_db": 0, "pending": 0}

    with open(vectorization_log) as f:
        vlog = json.load(f)

    last_time = vlog.get("last_vectorization", "unknown")
    total_vectorized = vlog.get("total_vectorized", 0)
    total_in_db = vlog.get("total_documents_in_rag", 0)

    current_count = 0
    if feedback_log.exists():
        current_count = sum(1 for line in open(feedback_log) if line.strip())

    return {
        "synced": current_count <= total_vectorized,
        "last_time": last_time[:19] if len(last_time) > 19 else last_time,
        "vectorized": total_vectorized,
        "in_db": total_in_db,
        "pending": max(0, current_count - total_vectorized),
    }


def get_mcp_status():
    """Check MCP registration."""
    status = get_shared_mcp_status()
    if not status["registered"]:
        return {"registered": False}
    return {
        "registered": True,
        "entry": status.get("entry", "?"),
        "source": status.get("source_label", "?"),
        "duplicates": status.get("duplicate_sources", []),
    }


def generate_html(db_stats, feedback, scores, usage, sync, mcp):
    """Generate the full HTML dashboard."""
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    # Determine project path from storage path
    project_path = html.escape(str(get_storage_path().parent.parent.parent))

    # Build score rows with progress bars
    score_rows = ""
    for cat, score in scores.items():
        safe_cat = html.escape(str(cat))
        pct = score * 100
        color = "#34d399" if score >= 0.7 else "#fbbf24" if score >= 0.5 else "#f87171"
        status = "WEAK" if score < 0.7 else "OK"
        score_rows += (
            f'<tr>'
            f'<td><strong>{safe_cat}</strong></td>'
            f'<td><div class="progress-track"><div class="progress-fill" style="width:{pct:.1f}%;background:{color};"></div></div></td>'
            f'<td style="color:{color};font-weight:600;white-space:nowrap;">{pct:.1f}%&nbsp;&nbsp;<span style="font-size:11px;opacity:0.7;">({status})</span></td>'
            f'</tr>'
        )

    # Health checks
    checks = {
        "Vector Database": db_stats["healthy"],
        "Feedback Data": feedback["total"] > 0,
        "Reliability Scores": len(scores) > 0,
        "Usage Evidence": usage["total"] > 0,
        "Vectorization Sync": sync["synced"],
        "MCP Server": mcp["registered"],
    }
    checks_passed = sum(1 for v in checks.values() if v)
    checks_total = len(checks)

    check_rows = ""
    for name, passed in checks.items():
        if passed:
            badge = '<span class="status-badge status-pass">PASS</span>'
        else:
            badge = '<span class="status-badge status-fail">FAIL</span>'
        check_rows += f"<tr><td><strong>{name}</strong></td><td>{badge}</td></tr>"

    summary_class = "summary-pass" if checks_passed == checks_total else "summary-warn"

    page = f'''<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>SmartAssist Dashboard</title>
<link rel="preconnect" href="https://fonts.googleapis.com">
<link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
<link href="https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700;800&display=swap" rel="stylesheet">
<style>
  :root {{
    --bg: #030712;
    --bg-card: #131a27;
    --bg-alt: #0d1420;
    --border: #1e2d44;
    --border-subtle: #162032;
    --text: #e6edf3;
    --dim: #94a3b8;
    --muted: #64748b;
    --blue: #38bdf8;
    --green: #34d399;
    --orange: #fb923c;
    --red: #f87171;
    --purple: #a78bfa;
    --yellow: #fbbf24;
    --cyan: #22d3ee;
  }}
  * {{ box-sizing: border-box; margin: 0; padding: 0; }}
  body {{
    background: var(--bg);
    color: var(--text);
    font-family: 'Inter', -apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif;
    font-size: 14px;
    line-height: 1.6;
    max-width: 1140px;
    margin: 0 auto;
    padding: 40px 28px 80px;
    -webkit-font-smoothing: antialiased;
    -moz-osx-font-smoothing: grayscale;
  }}

  /* Header */
  .header {{
    margin-bottom: 36px;
  }}
  .header h1 {{
    font-size: 28px;
    font-weight: 800;
    color: var(--text);
    letter-spacing: -0.5px;
  }}
  .header .project-path {{
    color: var(--muted);
    font-size: 13px;
    margin-top: 4px;
    font-family: 'SF Mono', 'Fira Code', 'Cascadia Code', monospace;
    font-weight: 400;
  }}
  .header-meta {{
    display: flex;
    align-items: center;
    gap: 16px;
    margin-top: 8px;
  }}
  .header-meta .timestamp {{
    color: var(--muted);
    font-size: 12px;
  }}
  .header-meta .health-pill {{
    display: inline-flex;
    align-items: center;
    gap: 6px;
    padding: 4px 12px;
    border-radius: 20px;
    font-size: 12px;
    font-weight: 600;
  }}
  .health-pass {{
    background: rgba(52, 211, 153, 0.1);
    color: var(--green);
    border: 1px solid rgba(52, 211, 153, 0.2);
  }}
  .health-warn {{
    background: rgba(251, 146, 60, 0.1);
    color: var(--orange);
    border: 1px solid rgba(251, 146, 60, 0.2);
  }}
  .health-dot {{
    width: 6px;
    height: 6px;
    border-radius: 50%;
    display: inline-block;
  }}
  .health-pass .health-dot {{ background: var(--green); }}
  .health-warn .health-dot {{ background: var(--orange); }}

  /* Metrics bar */
  .metrics {{
    display: grid;
    grid-template-columns: repeat(6, 1fr);
    gap: 12px;
    margin: 0 0 32px;
  }}
  @media (max-width: 768px) {{
    .metrics {{ grid-template-columns: repeat(3, 1fr); }}
  }}
  .metric {{
    background: var(--bg-card);
    border: 1px solid var(--border);
    border-radius: 12px;
    padding: 20px 16px;
    text-align: center;
    transition: border-color 0.15s ease;
  }}
  .metric:hover {{
    border-color: rgba(56, 189, 248, 0.3);
  }}
  .metric-val {{
    font-size: 26px;
    font-weight: 800;
    letter-spacing: -0.5px;
    line-height: 1.2;
  }}
  .metric-lbl {{
    font-size: 11px;
    color: var(--muted);
    text-transform: uppercase;
    letter-spacing: 0.5px;
    margin-top: 4px;
    font-weight: 500;
  }}

  /* Section headers */
  .section-title {{
    font-size: 16px;
    font-weight: 700;
    color: var(--text);
    margin: 32px 0 12px;
    display: flex;
    align-items: center;
    gap: 8px;
    letter-spacing: -0.2px;
  }}
  .section-title::after {{
    content: '';
    flex: 1;
    height: 1px;
    background: var(--border);
  }}

  /* Cards */
  .card {{
    background: var(--bg-card);
    border: 1px solid var(--border);
    border-radius: 12px;
    padding: 0;
    margin: 0 0 12px;
    overflow: hidden;
  }}
  .card-body {{
    padding: 20px;
  }}

  /* Tables */
  table {{
    width: 100%;
    border-collapse: collapse;
    font-size: 13px;
  }}
  th {{
    background: var(--bg-alt);
    color: var(--muted);
    padding: 10px 16px;
    text-align: left;
    font-size: 11px;
    font-weight: 600;
    text-transform: uppercase;
    letter-spacing: 0.5px;
    border-bottom: 1px solid var(--border);
  }}
  td {{
    padding: 10px 16px;
    border-bottom: 1px solid var(--border-subtle);
    color: var(--dim);
  }}
  tr:last-child td {{
    border-bottom: none;
  }}
  td strong {{
    color: var(--text);
    font-weight: 600;
  }}

  /* Status badges */
  .status-badge {{
    display: inline-block;
    padding: 3px 10px;
    border-radius: 20px;
    font-size: 11px;
    font-weight: 600;
    letter-spacing: 0.3px;
  }}
  .status-pass {{
    background: rgba(52, 211, 153, 0.12);
    color: #34d399;
  }}
  .status-fail {{
    background: rgba(248, 113, 113, 0.12);
    color: #f87171;
  }}

  /* Progress bars */
  .progress-track {{
    background: #0d1420;
    border-radius: 6px;
    overflow: hidden;
    height: 8px;
  }}
  .progress-fill {{
    height: 100%;
    border-radius: 6px;
    transition: width 0.3s ease;
  }}

  /* Live activity feed */
  .feed-container {{
    background: var(--bg-card);
    border: 1px solid var(--border);
    border-radius: 12px;
    overflow: hidden;
    margin: 0 0 12px;
  }}
  .feed-header {{
    display: flex;
    align-items: center;
    justify-content: space-between;
    padding: 14px 20px;
    border-bottom: 1px solid var(--border);
  }}
  .feed-header-left {{
    display: flex;
    align-items: center;
    gap: 8px;
  }}
  .feed-title {{
    font-size: 13px;
    font-weight: 600;
    color: var(--text);
  }}
  .feed-live-dot {{
    width: 8px;
    height: 8px;
    border-radius: 50%;
    background: var(--green);
    animation: pulse 2s ease-in-out infinite;
  }}
  @keyframes pulse {{
    0%, 100% {{ opacity: 1; }}
    50% {{ opacity: 0.4; }}
  }}
  .feed-count {{
    font-size: 12px;
    color: var(--muted);
  }}
  .feed-body {{
    max-height: 420px;
    overflow-y: auto;
    scrollbar-width: thin;
    scrollbar-color: var(--border) transparent;
  }}
  .feed-body::-webkit-scrollbar {{
    width: 6px;
  }}
  .feed-body::-webkit-scrollbar-track {{
    background: transparent;
  }}
  .feed-body::-webkit-scrollbar-thumb {{
    background: var(--border);
    border-radius: 3px;
  }}
  .feed-empty {{
    padding: 48px 20px;
    text-align: center;
    color: var(--muted);
    font-size: 13px;
  }}
  .event {{
    display: flex;
    align-items: flex-start;
    gap: 10px;
    padding: 10px 20px;
    border-bottom: 1px solid var(--border-subtle);
    transition: background 0.1s ease;
  }}
  .event:last-child {{
    border-bottom: none;
  }}
  .event:hover {{
    background: rgba(255, 255, 255, 0.02);
  }}
  .event-time {{
    font-size: 12px;
    color: var(--muted);
    font-family: 'SF Mono', 'Fira Code', 'Cascadia Code', monospace;
    white-space: nowrap;
    padding-top: 1px;
    min-width: 62px;
  }}
  .event-badge {{
    display: inline-block;
    padding: 2px 8px;
    border-radius: 4px;
    font-size: 10px;
    font-weight: 700;
    letter-spacing: 0.5px;
    white-space: nowrap;
    min-width: 64px;
    text-align: center;
  }}
  .event-inject {{
    background: rgba(52, 211, 153, 0.12);
    color: #34d399;
  }}
  .event-create {{
    background: rgba(56, 189, 248, 0.12);
    color: #38bdf8;
  }}
  .event-feedback {{
    background: rgba(167, 139, 250, 0.12);
    color: #a78bfa;
  }}
  .event-search {{
    background: rgba(34, 211, 238, 0.12);
    color: #22d3ee;
  }}
  .event-prompt {{
    background: rgba(251, 191, 36, 0.12);
    color: #fbbf24;
  }}
  .event-text {{
    font-size: 13px;
    color: var(--dim);
    line-height: 1.5;
    flex: 1;
    min-width: 0;
  }}

  /* Search */
  .search-wrap {{
    position: relative;
    margin: 0 0 8px;
  }}
  .search-input {{
    width: 100%;
    padding: 12px 16px 12px 42px;
    background: var(--bg-card);
    border: 1px solid var(--border);
    border-radius: 10px;
    color: var(--text);
    font-size: 14px;
    outline: none;
    font-family: inherit;
    transition: border-color 0.15s ease;
  }}
  .search-input:focus {{
    border-color: rgba(56, 189, 248, 0.5);
  }}
  .search-input::placeholder {{
    color: var(--muted);
  }}
  .search-icon {{
    position: absolute;
    left: 14px;
    top: 50%;
    transform: translateY(-50%);
    color: var(--muted);
    font-size: 14px;
    pointer-events: none;
  }}
  .lesson-card {{
    background: var(--bg-card);
    border: 1px solid var(--border);
    border-radius: 10px;
    padding: 14px 18px;
    margin: 8px 0;
    transition: border-color 0.15s ease;
  }}
  .lesson-card:hover {{
    border-color: rgba(56, 189, 248, 0.35);
  }}
  .lesson-cat {{
    display: inline-block;
    padding: 2px 8px;
    border-radius: 4px;
    font-size: 10px;
    font-weight: 700;
    letter-spacing: 0.3px;
    margin-bottom: 6px;
    text-transform: uppercase;
  }}
  .lesson-text {{
    font-size: 13px;
    line-height: 1.55;
    color: var(--dim);
  }}
  .lesson-text mark {{
    background: rgba(56, 189, 248, 0.15);
    color: var(--blue);
    border-radius: 2px;
    padding: 0 2px;
  }}
  .lesson-context {{
    font-size: 12px;
    color: var(--muted);
    margin-top: 6px;
  }}
  #search-results {{
    max-height: 600px;
    overflow-y: auto;
    scrollbar-width: thin;
    scrollbar-color: var(--border) transparent;
  }}
  #search-results::-webkit-scrollbar {{
    width: 6px;
  }}
  #search-results::-webkit-scrollbar-track {{
    background: transparent;
  }}
  #search-results::-webkit-scrollbar-thumb {{
    background: var(--border);
    border-radius: 3px;
  }}
  .no-results {{
    text-align: center;
    padding: 40px;
    color: var(--muted);
    font-size: 13px;
  }}

  /* Two-column layout for lower sections */
  .grid-2 {{
    display: grid;
    grid-template-columns: 1fr 1fr;
    gap: 16px;
  }}
  @media (max-width: 768px) {{
    .grid-2 {{ grid-template-columns: 1fr; }}
  }}

  /* Footer */
  .footer {{
    margin-top: 48px;
    text-align: center;
    color: var(--muted);
    font-size: 12px;
    padding-top: 20px;
    border-top: 1px solid var(--border);
  }}
</style>
</head>
<body>

<!-- Header -->
<div class="header">
  <h1>SmartAssist Dashboard</h1>
  <div class="project-path">{project_path}</div>
  <div class="header-meta">
    <span class="timestamp">{now}</span>
    <span class="health-pill {"health-pass" if checks_passed == checks_total else "health-warn"}">
      <span class="health-dot"></span>
      {checks_passed}/{checks_total} checks passing
    </span>
  </div>
</div>

<!-- Metrics bar -->
<div class="metrics">
  <div class="metric">
    <div class="metric-val" style="color:var(--green);">{db_stats["count"]:,}</div>
    <div class="metric-lbl">Lessons</div>
  </div>
  <div class="metric">
    <div class="metric-val" style="color:var(--blue);">{feedback["total"]:,}</div>
    <div class="metric-lbl">Feedback Events</div>
  </div>
  <div class="metric">
    <div class="metric-val" style="color:var(--purple);">{len(scores)}</div>
    <div class="metric-lbl">Categories</div>
  </div>
  <div class="metric">
    <div class="metric-val" style="color:var(--cyan);">{usage["total"]}</div>
    <div class="metric-lbl">Tool Calls</div>
  </div>
  <div class="metric">
    <div class="metric-val" style="color:var(--yellow);">{usage["hit_rate"]:.0f}%</div>
    <div class="metric-lbl">Hit Rate</div>
  </div>
  <div class="metric">
    <div class="metric-val" style="color:var(--orange);">{usage["avg_latency"]:.0f}<span style="font-size:14px;font-weight:500;">ms</span></div>
    <div class="metric-lbl">Avg Latency</div>
  </div>
</div>

<!-- Live Activity Feed -->
<div class="section-title">Live Activity</div>
<div class="feed-container">
  <div class="feed-header">
    <div class="feed-header-left">
      <div class="feed-live-dot"></div>
      <span class="feed-title">Event Stream</span>
    </div>
    <span class="feed-count" id="feed-count"></span>
  </div>
  <div class="feed-body" id="live-feed">
    <div class="feed-empty">Connecting to event stream...</div>
  </div>
</div>

<!-- Search Lessons -->
<div class="section-title">Search Lessons</div>
<div class="search-wrap">
  <span class="search-icon">
    <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><circle cx="11" cy="11" r="8"/><path d="m21 21-4.3-4.3"/></svg>
  </span>
  <input type="text" class="search-input" id="searchInput" placeholder="Search {db_stats["count"]:,} lessons..." autocomplete="off">
</div>
<div style="font-size:12px;color:var(--muted);margin:4px 4px 8px;" id="searchCount"></div>
<div id="search-results"></div>

<!-- Health Checks + Reliability Scores side by side -->
<div class="grid-2">
  <div>
    <div class="section-title">Health Checks</div>
    <div class="card">
      <table>
        <thead><tr><th>Check</th><th>Status</th></tr></thead>
        <tbody>{check_rows}</tbody>
      </table>
    </div>
  </div>
  <div>
    <div class="section-title">Reliability Scores</div>
    <div class="card">
      <table>
        <thead><tr><th>Category</th><th>Score</th><th>Value</th></tr></thead>
        <tbody>{score_rows}</tbody>
      </table>
    </div>
  </div>
</div>

<div class="footer">SmartAssist &middot; {datetime.now().strftime("%B %Y")}</div>

<script>
/* ---------- Live Activity Feed ---------- */
const BADGE_MAP = {{
  inject:   {{ cls: "event-inject",   label: "INJECT" }},
  create:   {{ cls: "event-create",   label: "CREATE" }},
  feedback: {{ cls: "event-feedback", label: "FEEDBACK" }},
  search:   {{ cls: "event-search",   label: "SEARCH" }},
  prompt:   {{ cls: "event-prompt",   label: "PROMPT" }},
}};

function escapeHtml(s) {{
  return s.replace(/&/g,"&amp;").replace(/</g,"&lt;").replace(/>/g,"&gt;");
}}

function renderEvents(events) {{
  const feed = document.getElementById("live-feed");
  const countEl = document.getElementById("feed-count");

  if (!events || events.length === 0) {{
    feed.innerHTML = '<div class="feed-empty">No recent activity</div>';
    countEl.textContent = "";
    return;
  }}

  countEl.textContent = events.length + " events";
  feed.innerHTML = events.slice(0, 50).map(ev => {{
    const badge = BADGE_MAP[ev.type] || {{ cls: "event-prompt", label: ev.type.toUpperCase() }};
    return '<div class="event">'
      + '<span class="event-time">' + escapeHtml(ev.time || "") + '</span>'
      + '<span class="event-badge ' + badge.cls + '">' + badge.label + '</span>'
      + '<span class="event-text">' + escapeHtml(ev.description || "") + '</span>'
      + '</div>';
  }}).join("");
}}

async function fetchLive() {{
  try {{
    const res = await fetch('/api/live');
    if (!res.ok) throw new Error(res.statusText);
    const events = await res.json();
    renderEvents(events);
  }} catch (e) {{
    const feed = document.getElementById("live-feed");
    if (feed.querySelector(".feed-empty")) {{
      feed.innerHTML = '<div class="feed-empty">Unable to load activity feed</div>';
    }}
  }}
}}
setInterval(fetchLive, 3000);
fetchLive();

/* Heartbeat — keeps server alive while browser is open */
setInterval(() => fetch("/api/heartbeat").catch(() => {{}}), 5000);

/* ---------- Lesson Search ---------- */
const LESSONS = {json.dumps(db_stats.get("lessons", []))};
const CAT_COLORS = {{
  code_edit:     {{ bg: "rgba(56,189,248,0.12)",  fg: "#38bdf8" }},
  pr_review:     {{ bg: "rgba(167,139,250,0.12)", fg: "#a78bfa" }},
  testing:       {{ bg: "rgba(52,211,153,0.12)",  fg: "#34d399" }},
  architecture:  {{ bg: "rgba(251,146,60,0.12)",  fg: "#fb923c" }},
  git:           {{ bg: "rgba(251,191,36,0.12)",  fg: "#fbbf24" }},
  security:      {{ bg: "rgba(248,113,113,0.12)", fg: "#f87171" }},
  debugging:     {{ bg: "rgba(34,211,238,0.12)",  fg: "#22d3ee" }},
  documentation: {{ bg: "rgba(148,163,184,0.12)", fg: "#94a3b8" }},
}};

function highlight(text, terms) {{
  if (!terms.length) return escapeHtml(text);
  let result = escapeHtml(text);
  terms.forEach(t => {{
    const re = new RegExp("(" + t.replace(/[.*+?^${{}}()|[\\]\\\\]/g, "\\\\$&") + ")", "gi");
    result = result.replace(re, "<mark>$1</mark>");
  }});
  return result;
}}

function doSearch() {{
  const q = document.getElementById("searchInput").value.trim().toLowerCase();
  const terms = q.split(/\\s+/).filter(t => t.length > 1);
  const container = document.getElementById("search-results");
  const countEl = document.getElementById("searchCount");
  if (!q) {{ container.innerHTML = ""; countEl.textContent = ""; return; }}

  const scored = LESSONS.map(l => {{
    const hay = (l.lesson + " " + l.context + " " + l.category).toLowerCase();
    let score = 0;
    terms.forEach(t => {{
      if (hay.includes(t)) score++;
      if (l.lesson.toLowerCase().includes(t)) score += 2;
    }});
    return {{ ...l, score }};
  }}).filter(l => l.score > 0).sort((a, b) => b.score - a.score).slice(0, 50);

  countEl.textContent = scored.length + " result" + (scored.length !== 1 ? "s" : "");
  if (scored.length === 0) {{
    container.innerHTML = '<div class="no-results">No lessons match your search.</div>';
    return;
  }}

  container.innerHTML = scored.map(l => {{
    const c = CAT_COLORS[l.category] || {{ bg: "rgba(148,163,184,0.12)", fg: "#94a3b8" }};
    let h = '<div class="lesson-card">';
    h += '<span class="lesson-cat" style="background:' + c.bg + ';color:' + c.fg + ';">' + escapeHtml(l.category) + '</span>';
    h += '<div class="lesson-text">' + highlight(l.lesson, terms) + '</div>';
    if (l.context) h += '<div class="lesson-context">' + highlight(l.context, terms) + '</div>';
    h += '</div>';
    return h;
  }}).join("");
}}

document.getElementById("searchInput").addEventListener("input", doSearch);
</script>
</body>
</html>'''
    return page


def _collect_and_generate():
    """Collect all data and generate HTML."""
    db_stats = get_db_stats()
    feedback = get_feedback_stats()
    scores = get_reliability_scores()
    usage = get_usage_stats()
    sync = get_sync_status()
    mcp = get_mcp_status()
    return generate_html(db_stats, feedback, scores, usage, sync, mcp)


def _parse_live_log():
    """Parse rag_live.log into structured events for the live activity feed.

    Reads the last 100 lines of the log and returns a list of event dicts
    with keys: time, type, description.  Events are returned newest-first.
    """
    import re

    log_path = get_storage_path() / "rag_live.log"
    if not log_path.exists():
        return []

    try:
        with open(log_path, "r", errors="replace") as f:
            lines = f.readlines()
    except OSError:
        return []

    # Keep only the last 100 lines
    lines = lines[-100:]

    # Strip ANSI escape codes from log output
    ansi_re = re.compile(r"\x1b\[[0-9;]*m")

    events = []
    current_time = None
    current_block = []

    def _flush_block(ts, block):
        """Convert an accumulated block of lines into zero or more events."""
        if not block:
            return

        # Filter out Stats summary lines before classification — they
        # contain words like "injected" that would cause false positives.
        content_lines = [ln for ln in block if not ln.strip().startswith("Stats:")]
        if not content_lines:
            return

        text = "\n".join(content_lines)
        text_lower = text.lower()

        # Determine event type from content (order matters — most specific first)
        if re.search(r"injected?\s+\d+\s+lesson", text_lower):
            events.append({
                "time": ts,
                "type": "inject",
                "description": _clean_description(text),
            })
        elif "no relevant lessons" in text_lower:
            events.append({
                "time": ts,
                "type": "search",
                "description": _clean_description(text),
            })
        elif "lesson" in text_lower and "creat" in text_lower:
            events.append({
                "time": ts,
                "type": "create",
                "description": _clean_description(text),
            })
        elif "feedback" in text_lower:
            events.append({
                "time": ts,
                "type": "feedback",
                "description": _clean_description(text),
            })
        elif "PROMPT" in text or re.search(r"Prompt\s+#\d+", text):
            events.append({
                "time": ts,
                "type": "prompt",
                "description": _clean_description(text),
            })
        elif text.strip():
            # Default: treat as prompt activity
            events.append({
                "time": ts,
                "type": "prompt",
                "description": _clean_description(text),
            })

    def _clean_description(text):
        """Produce a single-line readable description from a log block."""
        # Strip ANSI codes and whitespace
        text = ansi_re.sub("", text)
        parts = [ln.strip() for ln in text.split("\n") if ln.strip()]
        # Remove standalone "PROMPT" lines (type is captured in badge)
        parts = [p for p in parts if p != "PROMPT"]
        # Remove quoted prompt lines (the raw question) — keep if short
        cleaned = []
        for p in parts:
            # Skip stats summary lines
            if p.startswith("Stats:"):
                continue
            # Clean up leading pipe separators
            p = re.sub(r"^\|\s*", "", p)
            if p:
                cleaned.append(p)
        desc = " | ".join(cleaned[:3])  # Join at most 3 meaningful parts
        if len(desc) > 200:
            desc = desc[:197] + "..."
        return desc

    for raw_line in lines:
        line = ansi_re.sub("", raw_line.rstrip("\n")).strip()
        # Detect timestamp header lines like "22:07:39  |  Prompt #718"
        ts_match = re.match(r"(\d{2}:\d{2}:\d{2})\s*\|\s*(.*)", line)
        if ts_match:
            # Flush previous block
            if current_time is not None:
                _flush_block(current_time, current_block)
            current_time = ts_match.group(1)
            remainder = ts_match.group(2).strip()
            current_block = [remainder] if remainder else []
        elif line.strip():
            # Continuation line
            if current_time is not None:
                current_block.append(line)
            else:
                # Orphan line without timestamp — try to extract timestamp
                orphan_ts_match = re.match(r"^(\d{2}:\d{2}:\d{2})\s+(.*)", line)
                if orphan_ts_match:
                    if current_time is not None:
                        _flush_block(current_time, current_block)
                    current_time = orphan_ts_match.group(1)
                    current_block = [orphan_ts_match.group(2)]
                else:
                    current_block.append(line)

    # Flush last block
    if current_time is not None:
        _flush_block(current_time, current_block)

    # Return newest first, capped at 50
    events.reverse()
    return events[:50]


def main():
    import http.server
    import threading

    port = 3000
    serve = True
    output_path = None

    args = sys.argv[1:]
    for i, arg in enumerate(args):
        if arg == "--output" and i + 1 < len(args):
            output_path = Path(args[i + 1])
            serve = False
        elif arg == "--port" and i + 1 < len(args):
            port = int(args[i + 1])
        elif arg == "--no-serve":
            serve = False
        elif arg == "--stop":
            # Kill any running dashboard server
            import signal
            pid_file = get_storage_path() / "dashboard.pid"
            if pid_file.exists():
                try:
                    pid = int(pid_file.read_text().strip())
                    os.kill(pid, signal.SIGTERM)
                    pid_file.unlink()
                    print("Dashboard stopped.")
                except (ProcessLookupError, ValueError):
                    pid_file.unlink(missing_ok=True)
                    print("Dashboard was not running.")
            else:
                print("No dashboard running.")
            return 0

    print("Collecting data...")

    db_stats = get_db_stats()
    print(f"  Database: {db_stats['count']:,} documents")

    feedback = get_feedback_stats()
    print(f"  Feedback: {feedback['total']:,} events")

    scores = get_reliability_scores()
    print(f"  Scores: {len(scores)} categories")

    usage = get_usage_stats()
    print(f"  Usage: {usage['total']} tool calls logged")

    sync = get_sync_status()
    print(f"  Sync: {'in sync' if sync['synced'] else 'pending'}")

    mcp = get_mcp_status()
    print(f"  MCP: {'registered' if mcp['registered'] else 'NOT registered'}")

    print("Generating HTML dashboard...")
    page = generate_html(db_stats, feedback, scores, usage, sync, mcp)

    if not serve:
        if output_path is None:
            output_path = Path.home() / "Desktop" / "SmartAssist Dashboard.html"
        output_path.write_text(page, encoding="utf-8")
        print(f"  Written to: {output_path}")
        webbrowser.open(f"file://{output_path}")
        return 0

    # Serve dashboard with heartbeat-based auto-shutdown
    import time as _time
    last_heartbeat = [_time.time()]
    HEARTBEAT_TIMEOUT = 60  # shutdown after 60s of no browser connection

    class DashboardHandler(http.server.BaseHTTPRequestHandler):
        def do_GET(self):
            last_heartbeat[0] = _time.time()
            if self.path == "/" or self.path == "/index.html":
                fresh_page = _collect_and_generate()
                self.send_response(200)
                self.send_header("Content-Type", "text/html; charset=utf-8")
                self.send_header("Cache-Control", "no-cache, no-store, must-revalidate")
                self.end_headers()
                self.wfile.write(fresh_page.encode("utf-8"))
            elif self.path == "/api/live":
                events = _parse_live_log()
                payload = json.dumps(events)
                self.send_response(200)
                self.send_header("Content-Type", "application/json; charset=utf-8")
                self.send_header("Cache-Control", "no-cache, no-store, must-revalidate")
                self.end_headers()
                self.wfile.write(payload.encode("utf-8"))
            elif self.path == "/api/heartbeat":
                self.send_response(200)
                self.send_header("Content-Type", "text/plain")
                self.end_headers()
                self.wfile.write(b"ok")
            else:
                self.send_response(404)
                self.end_headers()

        def log_message(self, format, *args):
            pass

    def _watchdog(srv):
        """Shutdown server if no heartbeat received for HEARTBEAT_TIMEOUT seconds."""
        while True:
            _time.sleep(10)
            if _time.time() - last_heartbeat[0] > HEARTBEAT_TIMEOUT:
                srv.shutdown()
                return

    # Try the requested port, fall back if busy
    server = None
    for try_port in range(port, port + 10):
        try:
            server = http.server.HTTPServer(("127.0.0.1", try_port), DashboardHandler)
            port = try_port
            break
        except OSError:
            continue

    if server is None:
        print("Error: Could not find an available port.")
        return 1

    url = f"http://localhost:{port}"
    print(f"\n  Dashboard running at: {url}")
    print(f"  Data refreshes on each page load")
    print(f"  Stop with: smartassist dashboard --stop\n")

    # Save PID for --stop
    pid_file = get_storage_path() / "dashboard.pid"
    pid_file.write_text(str(os.getpid()))

    webbrowser.open(url)

    # Start watchdog — auto-shutdown if browser closes (no heartbeat for 60s)
    import threading
    watchdog = threading.Thread(target=_watchdog, args=(server,), daemon=True)
    watchdog.start()

    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
        pid_file.unlink(missing_ok=True)
        print("\nDashboard stopped.")

    return 0


if __name__ == "__main__":
    sys.exit(main())
