# SmartAssist KPI, Growth, and Feedback Plan

Date: 2026-04-07
Status: Proposed
Owner: SmartAssist core

## Purpose

This plan answers two product questions:

1. How do we measure whether SmartAssist is actually working?
2. How do we grow distribution and collect useful feedback without breaking the local-first product promise?

## Definition of Done

- We have one weekly scorecard with acquisition, activation, value, retention, and trust metrics.
- Every KPI maps to either an existing repo signal or an explicit instrumentation gap.
- Feedback collection has named channels, prompts, and a review cadence.
- Growth focuses on the real target market: developers already using AI coding agents.

## Current Truth From The Repo

SmartAssist already has more internal product evidence than most early devtools:

- `smartassist analyze` reads `usage_log.jsonl` and shows search hit rate, latency, top queries, and feedback summaries.
- `rag_dashboard` shows category reliability, corpus stats, and feedback counts.
- `smartassist dashboard` generates an HTML dashboard from usage, feedback, reliability, and runtime state.
- The QA harness and CI workflows already publish deterministic product proof, not just unit tests.
- The website deploy, QA demo publish, npm release workflow, and PyPI publish workflow already exist.

The current limitation is not missing local data. The limitation is missing cross-install aggregation.

Today SmartAssist is still primarily measured per project and per repo checkout. That is good for user privacy, but it means repo maintainers cannot yet answer questions like:

- how many teams completed setup successfully
- which agent integrations convert best
- where new users drop off in onboarding
- which features create repeat usage across many installs

That gap should be solved with explicit opt-in product telemetry, not hidden collection.

## KPI Stack

Use two layers:

1. **Track now** from existing repo and runtime evidence.
2. **Add later** only where cross-user understanding truly requires opt-in aggregation.

### Layer 1: KPIs We Can Track Now

| Area | KPI | Why it matters | Current source |
|---|---|---|---|
| Awareness | GitHub repo views, clones, stars, forks, release page views | Tells us whether distribution is growing at all | GitHub traffic + releases |
| Acquisition | Website visits to install pages and architecture overview | Shows whether messaging is pulling people in | Website analytics once enabled |
| Activation | Successful `smartassist setup` / `smartassist init` in QA and dogfood projects | Confirms onboarding works in real environments | QA harness, smoke scripts, `doctor --json` |
| Time to first value | First seed, first search, first feedback event | Measures whether users reach the core learning loop | `usage_log.jsonl`, `feedback_log.jsonl` |
| Product value | Search hit rate, helpful vs not-helpful feedback, lesson create/boost/demote mix | Tells us whether retrieval and learning are helping | `smartassist analyze`, `rag_dashboard` |
| Trust | PR checks pass rate, QA scenario pass rate, package smoke pass rate, false-ready doctor rate | Devtools spread when setup is reliable | GitHub Actions + QA harness |
| Retention proxy | 7-day and 30-day event activity inside dogfood projects | Tells us whether usage repeats after day one | `usage_log.jsonl` activity windows |
| Quality by category | Weak categories, reliability scores, repeated negative feedback | Shows what SmartAssist gets wrong most often | `rag_dashboard`, boundary packs |

### Starter Targets For Layer 1

These are operating targets, not claims about current performance:

| KPI | Starter target |
|---|---|
| Search hit rate | Keep above 70% |
| Helpful feedback share | Keep positive feedback above negative feedback in dogfood projects |
| Activation proof | Every release should pass project-init, package smoke, and pipx smoke |
| QA trust | Deterministic QA scenario suite stays green on every main push |
| Time to first value | A new user should be able to install, initialize, and see SmartAssist working in under 10 minutes |
| Feedback freshness | Review new feedback, issues, and discussion posts at least weekly |

## Layer 2: KPIs That Require Opt-In Aggregation

If SmartAssist is going to spread across many developers, the maintainer view cannot rely on project-local logs alone. The current repo already proves local product health. What it does not have is a safe way to learn across installs.

The rule is strict:

- no prompt contents
- no code contents
- no lesson text
- no file paths
- no raw search queries uploaded
- no hidden telemetry
- opt-in only

### Why a separate telemetry layer is required

The existing local analytics surfaces are useful, but they are not safe to ship upstream as-is:

- `smartassist/mcp_server.py` writes local runtime evidence to `usage_log.jsonl` via `_log_usage()`
- `smartassist/codex_activity.py` appends session metadata like `cwd` and prompt-derived previews for local dashboards
- `smartassist/tools/analyze_usage.py` and `smartassist/tools/generate_dashboard.py` summarize those local files for a single project or developer
- `smartassist/cli.py` already owns the install and activation lifecycle (`setup`, `init`, `setup-agent`, `doctor`, `seed`, `uninstall`)

That means the right design is **not** “upload `usage_log.jsonl`.”

The right design is:

1. keep current local logs local
2. add a new sanitized telemetry helper for shared KPI events
3. upload only anonymous lifecycle events and weekly aggregates

## How Shared KPI Collection Will Work

### Step 1: Explicit consent and anonymous identity

During `smartassist setup` and `smartassist setup-agent`, SmartAssist should ask one clear question:

> Do you want to share anonymous product metrics to improve SmartAssist?

If the user says yes:

- create an anonymous `install_id`
- store consent and the install id in a **user-level** SmartAssist config outside project workspaces
- default to disabled unless the user opts in

This user-level location is important. Cross-project product telemetry should not live in tracked repositories or in project-local SmartAssist data.

### Step 2: Emit sanitized lifecycle events from existing code paths

Add one shared helper such as `smartassist/telemetry.py` and route all shared KPI writes through it.

Recommended insertion points:

| File / function | Event | Why it matters |
|---|---|---|
| `smartassist/cli.py:cmd_setup()` | `install_started`, `setup_completed`, `setup_failed` | acquisition → onboarding conversion |
| `smartassist/cli.py:cmd_init()` | `project_initialized` | confirms project-level activation |
| `smartassist/cli.py:cmd_setup_agent()` | `agent_configured` | shows which agent integrations convert |
| `smartassist/cli.py:cmd_doctor()` | `doctor_ready`, `doctor_not_ready` | best setup quality signal |
| `smartassist/cli.py:cmd_seed()` | `seed_completed` | first meaningful activation |
| `smartassist/mcp_server.py:_log_usage()` | local rollup only, not raw upload | derive shared search/usage summaries safely |
| `smartassist/mcp_server.py:rag_feedback()` and `apply_feedback_protocol()` | feedback counters for rollup | product value and quality trends |
| `smartassist/tools/generate_dashboard.py` or launcher paths | `dashboard_opened` | operator engagement |
| `smartassist/cli.py:cmd_uninstall()` | `uninstall_requested` | churn and friction |

### Step 3: Build local weekly rollups instead of uploading raw usage

Shared telemetry should be summarized on the user's machine first.

Use current local state as inputs:

- `usage_log.jsonl`
- feedback metrics from the store
- category reliability / weak-category outputs
- CLI lifecycle events

Then write a sanitized rollup payload such as:

```json
{
  "install_id": "anon_123",
  "week": "2026-W15",
  "smartassist_version": "1.1.0b1",
  "agent_mix": {"claude": 1, "codex": 1},
  "setup_completed": 1,
  "doctor_ready": 1,
  "seed_completed": 1,
  "searches": 42,
  "searches_with_results": 31,
  "positive_feedback": 9,
  "negative_feedback": 2,
  "dashboard_opened": 3,
  "weak_categories": ["testing", "git"]
}
```

This gives maintainers useful product truth without leaking private project data.

### Step 4: Upload by batch, not in real time

The uploader should be boring and resilient:

- queue events locally first
- flush on setup completion, session end, dashboard open, or an explicit `smartassist telemetry flush`
- retry with backoff on failure
- keep upload status visible with `smartassist telemetry status`
- allow `smartassist telemetry disable` and `smartassist telemetry export`

If hosted telemetry is not ready yet, the export command can generate a shareable sanitized bundle as a bridge.

### Step 5: Maintain a small hosted collector, not a big analytics platform

The minimum shared KPI backend is:

1. one HTTPS ingestion endpoint
2. one raw sanitized events table
3. one daily rollup job
4. one maintainer dashboard for KPI trends

This does not need real-time streaming, user profiling, or prompt capture. Daily aggregates are enough.

## What Useful KPI We Will Get

| KPI | Derived from | Decision it unlocks |
|---|---|---|
| Setup conversion | `setup_completed / install_started` | improve onboarding flow and docs |
| Ready rate | `doctor_ready / setup_completed` | fix environment and registration failures |
| Activation by agent | `agent_configured` + `doctor_ready` | prioritize Claude/Codex/OpenCode integration work |
| Time to first value | time from `setup_completed` to `seed_completed` or first feedback/search rollup | shorten onboarding path |
| Search success rate | `searches_with_results / searches` from weekly rollups | improve retrieval quality |
| Satisfaction ratio | `positive_feedback / total_feedback` | improve lesson quality and ranking |
| D7 / D30 retention | repeated weekly rollups by `install_id` | tell whether SmartAssist becomes sticky |
| Churn rate | `uninstall_requested / active installs` | identify friction and trust breaks |
| Weak category frequency | aggregated weak categories | focus product improvements by category |
| Version regression rate | KPI deltas grouped by version | catch bad releases quickly |

## Where We Will View KPIs

The KPI system needs distinct viewing surfaces for distinct jobs.

### 1. Local operator view — existing today

This is the per-project, per-developer view. It should stay local.

Use:

- `smartassist dashboard`
- `smartassist analyze`
- `rag_dashboard`

This view is for:

- debugging onboarding problems in one workspace
- understanding retrieval quality in dogfood projects
- spotting weak categories and feedback trends locally

This is **not** the right place for cross-developer product decisions.

### 2. Maintainer aggregate dashboard — new primary KPI surface

This should be the main place where SmartAssist maintainers view shared KPIs.

The simplest valid implementation is a generated HTML dashboard backed by the shared telemetry collector. It can be a private maintainer page at first. It does not need to be a complex analytics app.

The aggregate dashboard should answer these questions with filters for time range, SmartAssist version, agent, and OS family:

- acquisition: installs started, setup completed
- activation: project initialized, doctor ready, seed completed
- value: search success rate, satisfaction ratio, dashboard engagement
- retention: weekly active installs, D7/D30 retention
- quality: weak categories, top failing agents, version regressions
- churn: uninstall requests and failure clusters

This is the surface to use for real product and growth decisions.

### 3. Weekly scorecard report — decision ritual

Dashboards are useful, but they are easy to ignore. SmartAssist should also generate one weekly scorecard summary from the aggregate dashboard.

That summary can be a Markdown or HTML report reviewed in a standing maintainer check-in. The point is to turn KPI reading into a repeatable decision loop.

Each weekly report should include:

- the five core operating questions
- KPI deltas versus the previous week
- biggest onboarding failure theme
- biggest product-quality regression or win
- one explicit decision or next action

### 4. Release readiness view — version comparison

Before every release, maintainers should review a version-sliced KPI view.

This can be a tab or filter inside the aggregate dashboard. It should compare the current version against the previous stable release for:

- setup conversion
- doctor-ready rate
- search success rate
- negative feedback share
- uninstall requests

This is how SmartAssist catches bad releases with product evidence, not only test evidence.

## Decision Cadence

Use the KPI surfaces on three rhythms:

| Cadence | Surface | Main decision |
|---|---|---|
| Daily / ad hoc | Local dashboard + aggregate dashboard | investigate issues, monitor onboarding, inspect regressions |
| Weekly | Weekly scorecard report | choose the next product/growth priority |
| Per release | Version comparison view | ship, hold, or rollback product changes |

## How We Should Set Up The Aggregate Dashboard

The aggregate dashboard should read from **sanitized rollup tables**, not directly from
raw client events and never from project-local files like `usage_log.jsonl`.

### Recommended setup

1. **Client telemetry helper**
   - Add a shared helper that writes opt-in lifecycle events to a local user-level queue.
   - That helper should also build daily and weekly KPI rollups from local usage and
     feedback data.

2. **Collector API**
   - Stand up one small HTTPS ingestion endpoint.
   - Its only job is to accept anonymous lifecycle events and weekly rollup payloads.

3. **Shared KPI store**
   - Use one relational database as the source of truth for shared metrics.
   - The minimum useful tables are:
     - `telemetry_events_raw`
     - `telemetry_daily_rollups`
     - `telemetry_weekly_rollups`
     - `telemetry_release_rollups`

4. **Aggregation job**
   - Run a scheduled job hourly or daily.
   - Build product-level summaries grouped by version, agent, OS family, and time range.
   - Derive the funnel and quality metrics the dashboard needs.

5. **Dashboard generator**
   - Generate one maintainer-facing HTML dashboard from the rollup tables.
   - Reuse the same approach SmartAssist already uses for local HTML dashboards:
     generate static HTML from Python rather than building a heavy analytics UI first.

6. **Hosting**
   - Publish the aggregate dashboard to a private maintainer URL first.
   - The simplest path is to deploy it alongside the existing SmartAssist website host,
     but behind maintainer-only access.

### What the dashboard should read from

The dashboard should prefer these sources in order:

1. `telemetry_weekly_rollups` for high-level KPI trends
2. `telemetry_daily_rollups` for shorter-term regressions and recovery
3. `telemetry_release_rollups` for version comparisons
4. `telemetry_events_raw` only for diagnostic drill-downs when a trend needs explaining

That read pattern matters. Maintainers should make decisions from stable aggregates, not
from raw noisy events.

### What the first dashboard version should show

The first maintainer dashboard only needs six panels:

1. install → setup → doctor-ready funnel
2. activation by agent
3. D7 / D30 retention trend
4. search success rate and satisfaction ratio by version
5. top weak categories and top failing agents
6. churn signals (`uninstall_requested`, setup failure clusters)

### How this setup scales properly

The scalable part of this design is the separation of concerns:

- clients emit the same small anonymous event contract
- the dashboard reads rollups, not raw events
- ingestion, storage, and aggregation can evolve independently

Use a phased scale path:

#### Phase 1 — simple and sufficient

- local batching on clients
- one stateless HTTPS collector
- one relational database
- scheduled rollup jobs
- generated HTML maintainer dashboard

This is enough for early adoption because the dashboard is not querying noisy client data directly.

#### Phase 2 — higher ingest volume

If direct writes from the collector start to contend with dashboard queries or rollup jobs:

- insert a message queue between the collector and the database
- move ingestion to async workers
- keep the dashboard pointed at rollup tables only

The client protocol does not need to change.

#### Phase 3 — larger analytics footprint

If raw telemetry becomes large enough that historical queries or version comparisons become slow:

- keep raw events in cold storage or an append-only analytics store
- retain daily, weekly, and release rollups in the fast KPI database
- continue serving the dashboard from the precomputed rollups

Again, the dashboard contract and client contract stay the same.

### What triggers the next scaling step

Do not upgrade architecture on instinct. Upgrade when one of these becomes true:

- ingestion latency becomes noticeable
- rollup jobs miss their SLA window
- dashboard load time becomes slow for maintainers
- version-comparison or time-range queries stop being responsive
- database cost grows faster than KPI value

That is how SmartAssist scales properly without prematurely building a full analytics platform.

### How decisions come out of it

The aggregate dashboard should not be treated as a passive report.

- Product decisions read from the weekly and release views
- Onboarding decisions read from the funnel and failure clusters
- Integration prioritization reads from agent-level activation and failure rates
- Retrieval improvements read from search success and weak-category trends

## Weekly Scorecard

The weekly scorecard should combine two sources:

1. **Public distribution signals** — GitHub traffic, website traffic, release traffic
2. **Shared product signals** — opt-in setup, activation, usage, satisfaction, and retention aggregates

Review these questions once per week:

### 1. Are more people discovering SmartAssist?

- GitHub views
- clones
- stars
- website visits
- release page traffic

### 2. Are they getting through setup?

- QA and smoke pass rate on main
- number of setup-related issues/discussion posts
- external reports of `doctor ready`
- install-to-activation conversion once opt-in events exist

### 3. Are they getting value?

- search hit rate
- helpful vs not-helpful feedback ratio
- first feedback events
- repeat searches after first day
- recurring weak categories

### 4. Where are we failing?

- top negative feedback themes
- top setup failures
- top agents with onboarding problems
- categories with low reliability or repeated boundary-pack promotion

### 5. Is trust increasing?

- green QA demo artifacts on every push
- package smoke success
- pipx smoke success
- fewer “doctor said ready but setup was broken” reports

## Feedback System

Use three feedback lanes, each with a distinct job:

### 1. In-product feedback

This already exists and is the highest-signal loop:

- `:)` and `:(` with context
- `rag_feedback`
- `apply_feedback_protocol`
- lesson creation, boosting, demotion, and comparison logging

This is best for product quality.

### 2. Public operator feedback

Use GitHub for structured external feedback:

- **Issues** for bugs and broken setup
- **Discussions** for onboarding reports, use cases, feature requests, and wins
- **Releases** for collecting version-specific regressions

Recommended discussion categories:

- Setup success reports
- Setup blockers
- Show your workflow
- Feature requests
- What SmartAssist got wrong

### 3. Campaign feedback

Whenever SmartAssist is posted publicly, use one consistent CTA:

> Install it, run setup, use it for one real task, and tell us what worked, what broke, and which agent you used.

That is much better than asking for generic opinions.

## Growth Strategy

SmartAssist should not try to reach “everyone.” It should first win the people already using coding agents seriously.

Primary audience:

- Claude Code users
- Codex users
- OpenCode users
- developers experimenting with multi-agent workflows
- teams that already care about prompt quality, repeat mistakes, and engineering feedback loops

### Positioning

Lead with the simplest durable message:

> SmartAssist is AI memory that learns from developer feedback and works across coding agents.

Support that with three proof points already present in the repo:

- local-first and project-scoped
- measurable learning loop through feedback and reliability scoring
- evidence-backed QA demos, not staged marketing claims

### Channel Priority

| Priority | Channel | Why it fits now |
|---|---|---|
| 1 | README + website + architecture overview | This is the conversion surface users already hit first |
| 2 | GitHub releases and release notes | Best place to announce product proof and improvements |
| 3 | QA demo artifacts and short demo clips | SmartAssist is easier to trust when people can see it work |
| 4 | Claude Code, MCP, and AI-devtool communities | Warm audience with immediate need |
| 5 | Broader launch posts after PyPI/npm paths are fully live | Wider reach matters more after onboarding is stable |

### What To Market

Market outcomes, not architecture jargon:

- fewer repeated mistakes
- memory that survives sessions
- project-specific lessons instead of generic prompting
- feedback that turns into reusable behavior
- one knowledge base across multiple coding agents

### Proof Assets To Reuse

The repo already has assets that should become the marketing spine:

- the main README quickstart
- `smartassist-overview.html`
- deterministic QA demo artifacts
- release workflows for npm and PyPI
- multi-agent setup paths in the CLI and docs

## Recommended Immediate Moves

These are the highest-leverage moves now that the shared KPI mechanism is explicit:

1. Add one centralized telemetry helper and user-level consent state outside project workspaces.
2. Instrument `cmd_setup`, `cmd_init`, `cmd_setup_agent`, `cmd_doctor`, `cmd_seed`, and `cmd_uninstall` first.
3. Convert existing local usage and feedback signals into sanitized weekly rollups instead of uploading raw logs.
4. Add `smartassist telemetry status`, `enable`, `disable`, `flush`, and `export` commands.
5. Stand up a minimal ingestion endpoint and maintainer dashboard for shared KPI trends.
6. Keep the QA demo and GitHub Discussions loop as the human layer on top of the telemetry.

## Decision Rule

If a proposed metric cannot change a product or distribution decision, do not track it.

The core questions are:

- Are more developers discovering SmartAssist?
- Are they getting through setup?
- Are they reaching the learning loop?
- Is SmartAssist helping more than it hurts?
- Do we know where it fails by category, agent, and onboarding step?

If the scorecard answers those five questions clearly every week, the KPI system is doing its job.
