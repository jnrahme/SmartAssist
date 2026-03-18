#!/usr/bin/env python3
"""
Generate a live HTML dashboard from current SmartAssist data.
Opens in the default browser when run.

Usage:
    smartassist dashboard [--output PATH]
"""

import html
import json
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

    cat_rows = ""
    for cat, cnt in db_stats["categories"].items():
        safe_cat = html.escape(str(cat))
        pct = cnt / db_stats["count"] * 100 if db_stats["count"] else 0
        cat_rows += f"<tr><td><strong>{safe_cat}</strong></td><td>{cnt:,}</td><td>{pct:.1f}%</td></tr>"

    score_rows = ""
    weak_count = 0
    for cat, score in scores.items():
        safe_cat = html.escape(str(cat))
        pct = score * 100
        color = "#34d399" if score >= 0.7 else "#fbbf24" if score >= 0.5 else "#f87171"
        status = "WEAK" if score < 0.7 else "OK"
        if score < 0.7:
            weak_count += 1
        score_rows += f'<tr><td><strong>{safe_cat}</strong></td><td><div style="background:#1e2d44;border-radius:5px;overflow:hidden;height:10px;"><div style="width:{pct:.1f}%;height:100%;background:{color};"></div></div></td><td style="color:{color};font-weight:600;">{pct:.1f}% ({status})</td></tr>'

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
        badge = '<span style="background:rgba(52,211,153,0.15);color:#34d399;padding:3px 10px;border-radius:12px;font-size:11px;font-weight:600;">PASS</span>' if passed else '<span style="background:rgba(248,113,113,0.15);color:#f87171;padding:3px 10px;border-radius:12px;font-size:11px;font-weight:600;">FAIL</span>'
        check_rows += f"<tr><td><strong>{name}</strong></td><td>{badge}</td></tr>"

    html = f'''<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>SmartAssist Dashboard</title>
<style>
  :root {{ --bg:#0a0e17; --bg-card:#131a27; --bg-alt:#162032; --border:#1e2d44; --text:#e6edf3; --dim:#94a3b8; --muted:#64748b; --blue:#38bdf8; --green:#34d399; --orange:#fb923c; --red:#f87171; --purple:#a78bfa; --yellow:#fbbf24; --cyan:#22d3ee; }}
  * {{ box-sizing:border-box; margin:0; padding:0; }}
  body {{ background:var(--bg); color:var(--text); font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',Inter,sans-serif; font-size:14px; line-height:1.6; max-width:1100px; margin:0 auto; padding:32px 24px 80px; }}
  .hero {{ text-align:center; margin-bottom:32px; }}
  .hero h1 {{ font-size:32px; font-weight:800; background:linear-gradient(135deg,#38bdf8,#a78bfa); -webkit-background-clip:text; -webkit-text-fill-color:transparent; }}
  .hero .sub {{ color:var(--dim); font-size:14px; margin-top:4px; }}
  .metrics {{ display:grid; grid-template-columns:repeat(auto-fit,minmax(140px,1fr)); gap:12px; margin:20px 0; }}
  .metric {{ background:var(--bg-card); border:1px solid var(--border); border-radius:10px; padding:16px; text-align:center; }}
  .metric-val {{ font-size:24px; font-weight:800; }}
  .metric-lbl {{ font-size:11px; color:var(--muted); text-transform:uppercase; }}
  h2 {{ font-size:18px; font-weight:700; margin:32px 0 12px; padding-bottom:6px; border-bottom:1px solid var(--border); }}
  .card {{ background:var(--bg-card); border:1px solid var(--border); border-radius:10px; padding:20px; margin:12px 0; }}
  table {{ width:100%; border-collapse:collapse; margin:8px 0; font-size:13px; }}
  th {{ background:var(--bg-alt); color:var(--dim); padding:8px 12px; text-align:left; font-size:11px; font-weight:600; text-transform:uppercase; border:1px solid var(--border); }}
  td {{ padding:8px 12px; border:1px solid var(--border); color:var(--dim); }}
  td strong {{ color:var(--text); }}
  .summary-banner {{ text-align:center; padding:16px; border-radius:10px; margin:24px 0; font-size:18px; font-weight:700; }}
  .summary-pass {{ background:rgba(52,211,153,0.1); border:2px solid rgba(52,211,153,0.3); color:var(--green); }}
  .summary-warn {{ background:rgba(251,146,60,0.1); border:2px solid rgba(251,146,60,0.3); color:var(--orange); }}
  .search-wrap {{ position:relative; margin:24px 0 8px; }}
  .search-input {{ width:100%; padding:14px 18px 14px 44px; background:var(--bg-card); border:2px solid var(--border); border-radius:12px; color:var(--text); font-size:15px; outline:none; font-family:inherit; }}
  .search-input:focus {{ border-color:var(--blue); }}
  .search-input::placeholder {{ color:var(--muted); }}
  .search-icon {{ position:absolute; left:16px; top:50%; transform:translateY(-50%); color:var(--muted); font-size:16px; pointer-events:none; }}
  .lesson-card {{ background:var(--bg-card); border:1px solid var(--border); border-radius:10px; padding:16px 20px; margin:8px 0; }}
  .lesson-card:hover {{ border-color:rgba(56,189,248,0.4); }}
  .lesson-cat {{ display:inline-block; padding:2px 8px; border-radius:10px; font-size:10px; font-weight:600; margin-bottom:6px; }}
  .lesson-text {{ font-size:14px; line-height:1.5; }}
  .lesson-text mark {{ background:rgba(56,189,248,0.2); color:var(--blue); border-radius:2px; padding:0 2px; }}
  .lesson-context {{ font-size:12px; color:var(--muted); margin-top:6px; }}
  #search-results {{ max-height:600px; overflow-y:auto; }}
  .no-results {{ text-align:center; padding:40px; color:var(--muted); }}
  .footer {{ margin-top:40px; text-align:center; color:var(--muted); font-size:12px; padding-top:16px; border-top:1px solid var(--border); }}
</style>
</head>
<body>
<div class="hero">
  <h1>SmartAssist Dashboard</h1>
  <p class="sub">RLHF + RAG + MCP Intelligent Learning System</p>
  <p style="color:var(--muted);font-size:12px;">Generated: {now}</p>
</div>

<div class="summary-banner {"summary-pass" if checks_passed == checks_total else "summary-warn"}">
  {checks_passed}/{checks_total} Health Checks Passed
</div>

<div class="metrics">
  <div class="metric"><div class="metric-val" style="color:var(--blue);">{feedback["total"]:,}</div><div class="metric-lbl">Raw Events</div></div>
  <div class="metric"><div class="metric-val" style="color:var(--green);">{db_stats["count"]:,}</div><div class="metric-lbl">Clean Lessons</div></div>
  <div class="metric"><div class="metric-val" style="color:var(--purple);">{len(scores)}</div><div class="metric-lbl">Categories</div></div>
  <div class="metric"><div class="metric-val" style="color:var(--cyan);">{usage["total"]}</div><div class="metric-lbl">Tool Calls</div></div>
  <div class="metric"><div class="metric-val" style="color:var(--yellow);">{usage["hit_rate"]:.0f}%</div><div class="metric-lbl">Hit Rate</div></div>
  <div class="metric"><div class="metric-val" style="color:var(--orange);">{usage["avg_latency"]:.0f}ms</div><div class="metric-lbl">Avg Latency</div></div>
</div>

<h2>Search Lessons</h2>
<div class="search-wrap">
  <span class="search-icon">&#128269;</span>
  <input type="text" class="search-input" id="searchInput" placeholder="Search lessons..." autocomplete="off">
</div>
<div style="font-size:12px;color:var(--muted);margin:8px 4px;" id="searchCount"></div>
<div id="search-results"></div>

<h2>System Health</h2>
<div class="card">
  <table><tr><th>Check</th><th>Status</th></tr>{check_rows}</table>
</div>

<h2>Reliability Scores</h2>
<div class="card">
  <table><tr><th>Category</th><th>Score</th><th>Status</th></tr>{score_rows}</table>
</div>

<h2>Vector Database</h2>
<div class="card">
  <table><tr><th>Category</th><th>Documents</th><th>Percentage</th></tr>{cat_rows}</table>
</div>

<div class="footer">SmartAssist &bull; {datetime.now().strftime("%B %Y")}</div>

<script>
const LESSONS = {json.dumps(db_stats.get("lessons", []))};
const CAT_COLORS = {{
  code_edit: {{bg:"rgba(56,189,248,0.12)", fg:"#38bdf8"}},
  pr_review: {{bg:"rgba(167,139,250,0.12)", fg:"#a78bfa"}},
  testing: {{bg:"rgba(52,211,153,0.12)", fg:"#34d399"}},
  architecture: {{bg:"rgba(251,146,60,0.12)", fg:"#fb923c"}},
  git: {{bg:"rgba(251,191,36,0.12)", fg:"#fbbf24"}},
  security: {{bg:"rgba(248,113,113,0.12)", fg:"#f87171"}},
}};
function escapeHtml(s) {{ return s.replace(/&/g,"&amp;").replace(/</g,"&lt;").replace(/>/g,"&gt;"); }}
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
    terms.forEach(t => {{ if (hay.includes(t)) score++; if (l.lesson.toLowerCase().includes(t)) score += 2; }});
    return {{ ...l, score }};
  }}).filter(l => l.score > 0).sort((a, b) => b.score - a.score).slice(0, 50);
  countEl.textContent = scored.length + " results";
  if (scored.length === 0) {{ container.innerHTML = '<div class="no-results">No lessons match.</div>'; return; }}
  container.innerHTML = scored.map(l => {{
    const c = CAT_COLORS[l.category] || {{bg:"rgba(148,163,184,0.12)", fg:"#94a3b8"}};
    let h = '<div class="lesson-card"><span class="lesson-cat" style="background:'+c.bg+';color:'+c.fg+';">'+escapeHtml(l.category)+'</span>';
    h += '<div class="lesson-text">'+highlight(l.lesson, terms)+'</div>';
    if (l.context) h += '<div class="lesson-context">'+highlight(l.context, terms)+'</div>';
    h += '</div>';
    return h;
  }}).join("");
}}
document.getElementById("searchInput").addEventListener("input", doSearch);
document.getElementById("searchInput").focus();
</script>
</body>
</html>'''
    return html


def main():
    output_path = None
    for i, arg in enumerate(sys.argv):
        if arg == "--output" and i + 1 < len(sys.argv):
            output_path = Path(sys.argv[i + 1])
            break

    if output_path is None:
        output_path = Path.home() / "Desktop" / "SmartAssist Dashboard.html"

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
    html = generate_html(db_stats, feedback, scores, usage, sync, mcp)

    output_path.write_text(html, encoding="utf-8")
    print(f"  Written to: {output_path}")

    print("Opening in browser...")
    webbrowser.open(f"file://{output_path}")

    return 0


if __name__ == "__main__":
    sys.exit(main())
