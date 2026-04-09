from __future__ import annotations

import html
import webbrowser
from pathlib import Path

from smartassist.telemetry import get_aggregate_db_path, get_aggregate_summary


def _pct(value: float) -> str:
    return f"{value * 100:.1f}%"


def _render_pairs(pairs: list[tuple[str, int]], empty_label: str) -> str:
    if not pairs:
        return f'<div class="empty">{html.escape(empty_label)}</div>'
    items = []
    for name, count in pairs:
        items.append(
            f"<tr><td><strong>{html.escape(str(name))}</strong></td><td>{int(count)}</td></tr>"
        )
    return "".join(items)


def _render_versions(rows: list[dict]) -> str:
    if not rows:
        return '<div class="empty">No version rollups yet</div>'
    items = []
    for row in rows:
        items.append(
            "<tr>"
            f"<td><strong>{html.escape(row['version'])}</strong></td>"
            f"<td>{row['installs']}</td>"
            f"<td>{_pct(row['setup_conversion'])}</td>"
            f"<td>{_pct(row['ready_rate'])}</td>"
            f"<td>{_pct(row['search_success_rate'])}</td>"
            f"<td>{_pct(row['satisfaction_ratio'])}</td>"
            f"<td>{row['uninstall_requested']}</td>"
            f"<td>{html.escape(row['latest_period'])}</td>"
            "</tr>"
        )
    return (
        "<table><thead><tr>"
        "<th>Version</th><th>Installs</th><th>Setup</th><th>Ready</th>"
        "<th>Search Success</th><th>Satisfaction</th><th>Uninstalls</th><th>Latest Period</th>"
        "</tr></thead><tbody>" + "".join(items) + "</tbody></table>"
    )


def generate_html(summary: dict) -> str:
    funnel = summary["funnel"]
    agent_rows = _render_pairs(
        summary["agent_activation"], "No agent activation events yet"
    )
    weak_rows = _render_pairs(summary["weak_categories"], "No weak categories reported")
    failure_rows = _render_pairs(
        summary["failure_clusters"], "No churn or failure clusters yet"
    )
    version_table = _render_versions(summary["versions"])
    title = "SmartAssist Aggregate KPI Dashboard"
    return f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>{title}</title>
<link rel="preconnect" href="https://fonts.googleapis.com">
<link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
<link href="https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700;800&display=swap" rel="stylesheet">
<style>
  :root {{
    --bg: #030712;
    --card: #111827;
    --card-alt: #0f172a;
    --border: #1f2937;
    --text: #e5e7eb;
    --muted: #94a3b8;
    --blue: #38bdf8;
    --green: #34d399;
    --amber: #fbbf24;
    --red: #f87171;
    --purple: #a78bfa;
  }}
  * {{ box-sizing: border-box; }}
  body {{
    margin: 0;
    font-family: 'Inter', sans-serif;
    background: linear-gradient(180deg, #020617 0%, #030712 100%);
    color: var(--text);
    padding: 40px 24px 80px;
  }}
  .wrap {{ max-width: 1180px; margin: 0 auto; }}
  h1 {{ margin: 0; font-size: 34px; letter-spacing: -1px; }}
  .sub {{ margin-top: 10px; color: var(--muted); font-size: 14px; }}
  .hero {{ margin-bottom: 28px; }}
  .metrics {{ display: grid; grid-template-columns: repeat(6, minmax(0, 1fr)); gap: 14px; margin-bottom: 28px; }}
  .metric {{ background: var(--card); border: 1px solid var(--border); border-radius: 16px; padding: 18px; }}
  .metric .label {{ color: var(--muted); font-size: 12px; text-transform: uppercase; letter-spacing: .08em; }}
  .metric .value {{ font-size: 28px; font-weight: 800; margin-top: 6px; }}
  .grid {{ display: grid; grid-template-columns: repeat(2, minmax(0, 1fr)); gap: 16px; }}
  .panel {{ background: var(--card); border: 1px solid var(--border); border-radius: 16px; overflow: hidden; }}
  .panel h2 {{ margin: 0; font-size: 16px; padding: 18px 20px; background: var(--card-alt); border-bottom: 1px solid var(--border); }}
  .panel .body {{ padding: 18px 20px; }}
  table {{ width: 100%; border-collapse: collapse; }}
  th, td {{ padding: 10px 0; border-bottom: 1px solid rgba(148, 163, 184, 0.12); text-align: left; font-size: 14px; }}
  th {{ color: var(--muted); font-size: 12px; text-transform: uppercase; letter-spacing: .08em; }}
  tr:last-child td {{ border-bottom: none; }}
  strong {{ color: var(--text); }}
  .badge {{ display: inline-flex; padding: 6px 10px; border-radius: 999px; font-size: 12px; font-weight: 700; }}
  .badge.blue {{ background: rgba(56, 189, 248, 0.12); color: var(--blue); }}
  .badge.green {{ background: rgba(52, 211, 153, 0.12); color: var(--green); }}
  .badge.amber {{ background: rgba(251, 191, 36, 0.12); color: var(--amber); }}
  .badge.red {{ background: rgba(248, 113, 113, 0.12); color: var(--red); }}
  .badge.purple {{ background: rgba(167, 139, 250, 0.12); color: var(--purple); }}
  .funnel {{ display: grid; grid-template-columns: repeat(4, minmax(0, 1fr)); gap: 12px; }}
  .step {{ background: rgba(255,255,255,0.02); border: 1px solid var(--border); border-radius: 14px; padding: 14px; }}
  .step-label {{ color: var(--muted); font-size: 12px; text-transform: uppercase; letter-spacing: .08em; }}
  .step-value {{ font-size: 24px; font-weight: 800; margin-top: 8px; }}
  .empty {{ color: var(--muted); font-size: 14px; }}
  @media (max-width: 980px) {{
    .metrics {{ grid-template-columns: repeat(3, minmax(0, 1fr)); }}
    .grid {{ grid-template-columns: 1fr; }}
    .funnel {{ grid-template-columns: repeat(2, minmax(0, 1fr)); }}
  }}
  @media (max-width: 640px) {{
    .metrics {{ grid-template-columns: repeat(2, minmax(0, 1fr)); }}
    .funnel {{ grid-template-columns: 1fr; }}
  }}
</style>
</head>
<body>
  <div class="wrap">
    <section class="hero">
      <span class="badge purple">Latest week: {html.escape(summary["latest_week"])}</span>
      <h1>{title}</h1>
      <div class="sub">Generated {html.escape(summary["generated_at"])} · Source DB: {html.escape(summary["db_path"])}</div>
    </section>

    <section class="metrics">
      <div class="metric"><div class="label">Tracked installs</div><div class="value">{summary["installs_total"]}</div></div>
      <div class="metric"><div class="label">Active installs</div><div class="value">{summary["active_installs_latest_week"]}</div></div>
      <div class="metric"><div class="label">Setup conversion</div><div class="value">{_pct(summary["setup_conversion"])}</div></div>
      <div class="metric"><div class="label">Ready rate</div><div class="value">{_pct(summary["ready_rate"])}</div></div>
      <div class="metric"><div class="label">Search success</div><div class="value">{_pct(summary["search_success_rate"])}</div></div>
      <div class="metric"><div class="label">Satisfaction</div><div class="value">{_pct(summary["satisfaction_ratio"])}</div></div>
    </section>

    <section class="panel" style="margin-bottom: 16px;">
      <h2>Current Week Funnel</h2>
      <div class="body">
        <div class="funnel">
          <div class="step"><div class="step-label">Install Started</div><div class="step-value">{funnel["install_started"]}</div></div>
          <div class="step"><div class="step-label">Setup Completed</div><div class="step-value">{funnel["setup_completed"]}</div></div>
          <div class="step"><div class="step-label">Doctor Ready</div><div class="step-value">{funnel["doctor_ready"]}</div></div>
          <div class="step"><div class="step-label">Seed Completed</div><div class="step-value">{funnel["seed_completed"]}</div></div>
        </div>
      </div>
    </section>

    <section class="grid">
      <div class="panel">
        <h2>Activation by Agent</h2>
        <div class="body"><table><tbody>{agent_rows}</tbody></table></div>
      </div>
      <div class="panel">
        <h2>Retention</h2>
        <div class="body">
          <div class="funnel">
            <div class="step"><div class="step-label">D7 installs</div><div class="step-value">{summary["retention"]["d7"]}</div></div>
            <div class="step"><div class="step-label">D30 installs</div><div class="step-value">{summary["retention"]["d30"]}</div></div>
            <div class="step"><div class="step-label">Uninstalls</div><div class="step-value">{funnel["uninstall_requested"]}</div></div>
            <div class="step"><div class="step-label">Raw events</div><div class="step-value">{summary["raw_events"]}</div></div>
          </div>
        </div>
      </div>
      <div class="panel">
        <h2>Top Weak Categories</h2>
        <div class="body"><table><tbody>{weak_rows}</tbody></table></div>
      </div>
      <div class="panel">
        <h2>Failure Clusters</h2>
        <div class="body"><table><tbody>{failure_rows}</tbody></table></div>
      </div>
    </section>

    <section class="panel" style="margin-top: 16px;">
      <h2>Version Comparison</h2>
      <div class="body">{version_table}</div>
    </section>
  </div>
</body>
</html>"""


def generate_dashboard(
    db_path: Path | str | None = None,
    *,
    output_path: Path | str | None = None,
    open_browser: bool = False,
) -> Path:
    aggregate_db = Path(db_path or get_aggregate_db_path()).expanduser().resolve()
    summary = get_aggregate_summary(aggregate_db)
    destination = (
        Path(output_path).expanduser().resolve()
        if output_path is not None
        else Path.cwd().resolve() / "smartassist-aggregate-dashboard.html"
    )
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_text(generate_html(summary), encoding="utf-8")
    if open_browser:
        webbrowser.open(destination.as_uri())
    return destination
