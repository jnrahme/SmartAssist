"""Static HTML demo generation for SmartAssist QA runs."""

from __future__ import annotations

import html
import json
import os
import webbrowser
from pathlib import Path


def _rel_link(destination: Path, target: Path) -> str:
    return html.escape(os.path.relpath(target, destination.parent))


def render_demo_site(
    run_dir: Path | str,
    *,
    output_path: Path | str | None = None,
    open_browser: bool = False,
    auto_refresh_seconds: int | None = None,
) -> Path:
    run_path = Path(run_dir)
    summary_path = run_path / "summary.json"
    manifest_path = run_path / "manifest.json"
    if not summary_path.exists() or not manifest_path.exists():
        raise FileNotFoundError("Run directory is missing summary.json or manifest.json")

    summary = json.loads(summary_path.read_text(encoding="utf-8"))
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    destination = Path(output_path) if output_path is not None else run_path / "demo" / "index.html"
    destination.parent.mkdir(parents=True, exist_ok=True)

    pass_count = sum(1 for item in summary.get("scenarios", []) if item.get("status") == "pass")
    fail_count = sum(1 for item in summary.get("scenarios", []) if item.get("status") == "fail")
    pending_count = sum(1 for item in summary.get("scenarios", []) if item.get("status") == "pending")

    scenario_sections = []
    for scenario in manifest.get("scenarios", []):
        scenario_name = str(scenario["name"])
        scenario_dir = run_path / "scenarios" / scenario_name
        scenario_path = scenario_dir / "scenario.json"
        payload = json.loads(scenario_path.read_text(encoding="utf-8")) if scenario_path.exists() else {}
        assertions = payload.get("assertions", [])
        steps = payload.get("steps", [])
        status_value = scenario.get("success")
        if status_value is True:
            status_label = "PASS"
            status_class = "pass"
        elif status_value is False:
            status_label = "FAIL"
            status_class = "fail"
        else:
            status_label = "PENDING"
            status_class = "pending"

        before_active = len(payload.get("before_state", {}).get("canonical", {}).get("active_lessons", []))
        after_active = len(payload.get("after_state", {}).get("canonical", {}).get("active_lessons", []))
        before_events = len(payload.get("before_state", {}).get("canonical", {}).get("feedback_events", []))
        after_events = len(payload.get("after_state", {}).get("canonical", {}).get("feedback_events", []))

        assertion_rows = "".join(
            f"<li class=\"{'pass' if item.get('passed') else 'fail'}\">"
            f"<strong>{html.escape(str(item.get('name', 'assertion')))}</strong>: "
            f"{html.escape(str(item.get('detail', '')))}</li>"
            for item in assertions
        )
        step_rows = "".join(
            f"<li><strong>{html.escape(str(step.get('title', 'step')))}</strong>: "
            f"{html.escape(str(step.get('detail', '')))}</li>"
            for step in steps
        )
        artifact_links = []
        for file_name in (
            "scenario.json",
            "assertions.json",
            "steps.jsonl",
            "before_state.json",
            "after_state.json",
            "sqlite_snapshot.json",
            "export_snapshot.json",
            "rag_live.log",
            "usage_log.jsonl",
        ):
            target = scenario_dir / file_name
            if target.exists():
                artifact_links.append(
                    f"<a href=\"{_rel_link(destination, target)}\">{html.escape(file_name)}</a>"
                )
        artifact_html = " · ".join(artifact_links) if artifact_links else "<span class=\"muted\">No artifacts yet.</span>"

        scenario_sections.append(
            f"""
            <section class="scenario {status_class}">
              <div class="scenario-header">
                <div>
                  <h2>{html.escape(scenario_name)}</h2>
                  <p class="scenario-description">{html.escape(str(scenario.get('description', '')))}</p>
                </div>
                <span class="badge {status_class}">{status_label}</span>
              </div>
              <div class="stats-grid">
                <div><span class="label">Active lessons</span><strong>{before_active} → {after_active}</strong></div>
                <div><span class="label">Feedback events</span><strong>{before_events} → {after_events}</strong></div>
                <div><span class="label">Assertions</span><strong>{len(assertions)}</strong></div>
                <div><span class="label">Artifacts</span><strong>{len(artifact_links)}</strong></div>
              </div>
              <div class="artifact-row">{artifact_html}</div>
              <div class="columns">
                <div>
                  <h3>Assertions</h3>
                  <ul>{assertion_rows or '<li class="muted">No assertions recorded yet.</li>'}</ul>
                </div>
                <div>
                  <h3>Steps</h3>
                  <ul>{step_rows or '<li class="muted">Scenario has not run yet.</li>'}</ul>
                </div>
              </div>
            </section>
            """
        )

    head_refresh = (
        f'<meta http-equiv="refresh" content="{int(auto_refresh_seconds)}">'
        if auto_refresh_seconds and auto_refresh_seconds > 0
        else ""
    )

    html_text = f"""<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  {head_refresh}
  <title>SmartAssist QA Demo</title>
  <style>
    :root {{
      --bg: #0b1220;
      --panel: rgba(14, 23, 39, 0.86);
      --panel-strong: rgba(16, 28, 48, 0.96);
      --text: #e5eefb;
      --muted: #8ba0bf;
      --border: rgba(132, 153, 190, 0.24);
      --pass: #22c55e;
      --fail: #ef4444;
      --pending: #f59e0b;
      --accent: #38bdf8;
      --shadow: 0 24px 60px rgba(0, 0, 0, 0.35);
    }}
    * {{ box-sizing: border-box; }}
    body {{
      margin: 0;
      color: var(--text);
      font-family: ui-sans-serif, -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif;
      background:
        radial-gradient(circle at top left, rgba(56, 189, 248, 0.16), transparent 28%),
        radial-gradient(circle at top right, rgba(34, 197, 94, 0.12), transparent 24%),
        linear-gradient(180deg, #08111d 0%, #0b1220 100%);
      min-height: 100vh;
    }}
    main {{
      max-width: 1180px;
      margin: 0 auto;
      padding: 32px 20px 56px;
    }}
    .hero, .scenario {{
      background: var(--panel);
      border: 1px solid var(--border);
      border-radius: 18px;
      box-shadow: var(--shadow);
      backdrop-filter: blur(12px);
    }}
    .hero {{
      padding: 28px;
      margin-bottom: 20px;
    }}
    h1 {{
      margin: 0 0 10px;
      font-size: 32px;
      line-height: 1.1;
    }}
    .hero p {{
      margin: 0 0 14px;
      color: var(--muted);
      max-width: 760px;
      line-height: 1.55;
    }}
    .hero-links {{
      display: flex;
      flex-wrap: wrap;
      gap: 10px;
      margin-top: 16px;
    }}
    .hero-links a {{
      color: var(--text);
      text-decoration: none;
      padding: 8px 12px;
      border-radius: 999px;
      border: 1px solid rgba(132, 153, 190, 0.32);
      background: rgba(15, 23, 42, 0.5);
    }}
    .meta, .muted {{
      color: var(--muted);
    }}
    .hero-stats {{
      display: grid;
      grid-template-columns: repeat(auto-fit, minmax(170px, 1fr));
      gap: 12px;
      margin-top: 18px;
    }}
    .hero-stats .card {{
      padding: 14px 16px;
      border-radius: 14px;
      border: 1px solid rgba(132, 153, 190, 0.24);
      background: rgba(9, 15, 27, 0.44);
    }}
    .label {{
      display: block;
      color: var(--muted);
      font-size: 12px;
      letter-spacing: 0.04em;
      text-transform: uppercase;
      margin-bottom: 6px;
    }}
    .card strong {{
      font-size: 20px;
    }}
    .scenario {{
      padding: 22px;
      margin-bottom: 18px;
    }}
    .scenario-header {{
      display: flex;
      justify-content: space-between;
      gap: 16px;
      align-items: flex-start;
      margin-bottom: 14px;
    }}
    .scenario-description {{
      margin: 6px 0 0;
      color: var(--muted);
      line-height: 1.45;
    }}
    .badge {{
      display: inline-flex;
      align-items: center;
      justify-content: center;
      min-width: 90px;
      padding: 8px 12px;
      border-radius: 999px;
      font-size: 12px;
      font-weight: 700;
      letter-spacing: 0.08em;
      text-transform: uppercase;
    }}
    .badge.pass {{
      color: #86efac;
      background: rgba(34, 197, 94, 0.16);
      border: 1px solid rgba(34, 197, 94, 0.28);
    }}
    .badge.fail {{
      color: #fca5a5;
      background: rgba(239, 68, 68, 0.16);
      border: 1px solid rgba(239, 68, 68, 0.28);
    }}
    .badge.pending {{
      color: #fcd34d;
      background: rgba(245, 158, 11, 0.16);
      border: 1px solid rgba(245, 158, 11, 0.28);
    }}
    .stats-grid {{
      display: grid;
      grid-template-columns: repeat(auto-fit, minmax(160px, 1fr));
      gap: 12px;
      margin-bottom: 14px;
    }}
    .stats-grid > div {{
      padding: 12px 14px;
      border-radius: 12px;
      background: rgba(10, 17, 28, 0.56);
      border: 1px solid rgba(132, 153, 190, 0.18);
    }}
    .stats-grid strong {{
      display: block;
      font-size: 18px;
      margin-top: 4px;
    }}
    .artifact-row {{
      color: var(--muted);
      margin-bottom: 16px;
      font-size: 14px;
    }}
    .artifact-row a {{
      color: #7dd3fc;
      text-decoration: none;
    }}
    .columns {{
      display: grid;
      grid-template-columns: repeat(auto-fit, minmax(300px, 1fr));
      gap: 20px;
    }}
    ul {{
      margin: 0;
      padding-left: 20px;
    }}
    li {{
      margin: 8px 0;
      line-height: 1.45;
    }}
    li.pass strong {{ color: #86efac; }}
    li.fail strong {{ color: #fca5a5; }}
    @media (max-width: 720px) {{
      .scenario-header {{
        flex-direction: column;
      }}
      .badge {{
        min-width: 0;
      }}
    }}
  </style>
</head>
<body>
  <main>
    <section class="hero">
      <h1>SmartAssist QA Demo</h1>
      <p>This page was generated from a real SmartAssist QA run. The same artifact bundle backs local demos, CI enforcement, and the published showcase, so the evidence you show is the same evidence that blocks regressions.</p>
      <p class="meta">
        Run ID: {html.escape(str(summary.get('run_id', '?')))} |
        Status: {html.escape(str(summary.get('final_status', '?')))} |
        Completed: {html.escape(str(summary.get('completed_count', '?')))} / {html.escape(str(summary.get('scenario_count', '?')))}
      </p>
      <div class="hero-stats">
        <div class="card"><span class="label">Passed</span><strong>{pass_count}</strong></div>
        <div class="card"><span class="label">Failed</span><strong>{fail_count}</strong></div>
        <div class="card"><span class="label">Pending</span><strong>{pending_count}</strong></div>
        <div class="card"><span class="label">Run Directory</span><strong style="font-size:14px">{html.escape(str(run_path))}</strong></div>
      </div>
      <div class="hero-links">
        <a href="{_rel_link(destination, summary_path)}">summary.json</a>
        <a href="{_rel_link(destination, manifest_path)}">manifest.json</a>
        <a href="{_rel_link(destination, run_path / 'summary.txt')}">summary.txt</a>
      </div>
      {"<p class='meta' style='margin-top:14px'>Live watch mode is active. This page refreshes automatically while scenarios complete.</p>" if auto_refresh_seconds else ""}
    </section>
    {''.join(scenario_sections)}
  </main>
</body>
</html>
"""

    destination.write_text(html_text, encoding="utf-8")
    if open_browser:
        webbrowser.open(destination.as_uri())
    return destination
