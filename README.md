<p align="center">
  <img src="https://img.shields.io/badge/python-3.10+-blue?style=flat-square&logo=python&logoColor=white" alt="Python 3.10+">
  <img src="https://img.shields.io/badge/license-BUSL--1.1-blue?style=flat-square" alt="BUSL 1.1 License">
  <img src="https://img.shields.io/badge/version-1.0.0-blue?style=flat-square" alt="Version 1.0.0">
</p>

<h1 align="center">SmartAssist</h1>

<p align="center">
  <strong>Persistent coding memory, retrieval, and lesson reinforcement for Claude Code</strong><br>
  <em>Learn from feedback, retrieve project-specific lessons, and keep that knowledge attached to each repo.</em>
</p>

<p align="center">
  <a href="https://smartassist-ai.netlify.app/">Interactive Documentation</a> ·
  <a href="https://smartassist-ai.netlify.app/dashboard.html">Live Dashboard</a> ·
  <a href="https://github.com/jnrahme/SmartAssist/wiki">Wiki</a>
</p>

---

## What SmartAssist Does

SmartAssist adds a project-scoped memory layer to Claude Code.

- It captures feedback signals such as `:)`, `:(`, thumbs up/down, and direct corrections.
- It stores lessons and analytics in `<project>/.claude/smartassist/`.
- It injects context through Claude Code hooks and exposes 8 MCP tools for retrieval and lesson management.
- It uses `BAAI/bge-m3`, LanceDB, hybrid retrieval, and reranking to surface project-specific lessons when Claude needs them.

Install once, then use it across repos:

- Global install: `pipx install git+https://github.com/jnrahme/SmartAssist.git`
- One-time Claude Code setup: `smartassist setup`
- Additional repo setup: `smartassist init`

---

## Install

Use the GitHub install path below, or install from a local checkout.

After the first PyPI release lands, the install command will become:

```bash
pipx install smartassist
```

Recommended:

```bash
pipx install git+https://github.com/jnrahme/SmartAssist.git
pipx ensurepath
```

For local development:

```bash
git clone https://github.com/jnrahme/SmartAssist.git
cd SmartAssist
pipx install .
```

Prerequisites:

- Python 3.10+
- `pipx`
- Claude Code installed locally

---

## Quick Start

For your first project:

```bash
cd ~/your-project
smartassist setup
smartassist doctor
smartassist seed
smartassist health
claude-sa
```

`smartassist setup` is the first-project bootstrap command. It installs the shared Claude hooks once, then registers SmartAssist for the current repo using project-scoped MCP config.

For additional projects:

```bash
cd ~/another-project
smartassist init
smartassist doctor
smartassist seed
smartassist health
```

---

## What `smartassist setup` Does

`smartassist setup` performs these actions automatically:

- registers the `smartassist` MCP server for the current repo with `claude mcp add -s project`
- falls back to writing `<project>/.mcp.json` if the Claude CLI cannot register it directly
- removes stale SmartAssist registrations from `~/.claude.json` and `~/.claude/mcp.json` when present
- installs SmartAssist hooks into `~/.claude/settings.json`
- ensures `~/.local/bin` is available on `PATH`
- initializes `.claude/smartassist/` in the current repo
- writes a setup log to `~/.claude/smartassist_setup.log`

No manual MCP config editing is required for the normal path.

---

## Verify It Is Working

Run:

```bash
smartassist doctor
smartassist health
```

Expected results:

- the current repo contains `.mcp.json`
- the current repo contains `.claude/smartassist/`
- `~/.claude/settings.json` contains five SmartAssist hook registrations
- `smartassist doctor` reports `Status: ready`
- `smartassist health` completes without blocking errors

If you want a live sidecar monitor while using Claude Code:

```bash
claude-sa
```

When `tmux` is available, `claude-sa` opens Claude Code beside the SmartAssist live log.

---

## How It Works

1. `UserPromptSubmit` and `SessionStart` hooks inject relevant lessons and feedback context.
2. `PreToolUse` watches Bash activity and can capture git-related learnings.
3. `rag_search` retrieves project-specific lessons from the vector store.
4. Feedback signals update lesson scores and category reliability.
5. Lesson-management tools keep the corpus useful over time.

### Retrieval Stack

- Embeddings: `BAAI/bge-m3` (1024 dimensions)
- Vector store: LanceDB
- Query path: hybrid search when available, vector fallback otherwise
- Filtering: distance threshold plus optional category filter
- Reranking: cross-encoder rerank when available
- Logging: usage, returned lessons, latency, and lesson comparison entries

---

## MCP Tools

| Tool | What it does |
| --- | --- |
| `rag_search` | Search the project knowledge base for relevant lessons, corrections, and conventions. |
| `rag_dashboard` | Show category reliability, corpus stats, and feedback metrics. |
| `rag_feedback` | Record whether a prior suggestion was helpful or not. |
| `create_lesson` | Store a new project-specific lesson from feedback. |
| `compare_lesson` | Draft a lesson for A/B comparison without storing it. |
| `boost_lesson` | Increase a lesson's priority after positive feedback. |
| `demote_lesson` | Reduce a lesson's priority or retire it after negative feedback. |
| `merge_lessons` | Combine overlapping lessons into one stronger lesson. |

---

## Hook Lifecycle

| Event | Matcher | Command | Purpose |
| --- | --- | --- | --- |
| `UserPromptSubmit` | none | `smartassist-prompt-inject` | Inject context and detect feedback signals. |
| `SessionStart` | `startup` | `smartassist-session-start` | Inject lessons for weak categories at session start. |
| `PreToolUse` | `Bash\|Edit\|Write` | `smartassist-commit-hook` | Inspect Bash and file-write activity and capture git-related learnings. |
| `PostToolUse` | `mcp__smartassist__rag_search` | `smartassist-show-lessons` | Display retrieved lessons after search. |
| `SessionEnd` | `other` | `smartassist-session-end` | Save session analytics. |

---

## Data Layout

| Path | Purpose |
| --- | --- |
| `<project>/.claude/smartassist/data/` | Feedback logs, curated lessons, scores, analytics, and live log output |
| `<project>/.claude/smartassist/lancedb/` | LanceDB vector store |
| `<project>/.mcp.json` | Canonical project-scoped SmartAssist MCP registration |
| `~/.claude/settings.json` | Hook configuration |
| `~/.claude.json` | Claude Code user/project state; SmartAssist cleans stale old registrations here |
| `~/.claude/mcp.json` | Legacy MCP config that SmartAssist cleans up if present |

### Data Directory Resolution Order

SmartAssist resolves the active project data directory in this order:

1. `SMARTASSIST_DATA_DIR`
2. walk up from the current working directory to find `.claude/smartassist/`
3. fail with a clear error telling you to run `smartassist init`

---

## Commands

```bash
smartassist setup           # One-time Claude config + init current project
smartassist doctor         # Audit install readiness for current project
smartassist uninstall       # Remove SmartAssist from Claude config
smartassist init            # Initialize current project
smartassist serve           # Start the MCP server over stdio
smartassist health          # Run health checks
smartassist migrate PATH    # Import data from an older rag-setup directory
smartassist vectorize       # Rebuild lesson embeddings
smartassist maintenance     # Run staleness and compaction tasks
smartassist analyze         # Show usage analytics
smartassist dashboard       # Generate the HTML dashboard
smartassist seed            # Seed lessons from CLAUDE.md
smartassist compare-lessons # Review hook-vs-Claude lesson comparisons
smartassist qa              # Run deterministic runtime contracts and generate demo artifacts
smartassist version         # Show package version
claude-sa                   # Launch Claude Code with the SmartAssist monitor
smartassist-monitor         # Check MCP + hook status (tails a log if you pass a path)
```

---

## QA Automation And Live Demo

SmartAssist now ships one shared proof system for both regression protection and showcase demos.

Run the deterministic contract suite locally:

```bash
smartassist qa list-scenarios
smartassist qa run
smartassist qa run --watch --open
```

Generate a static demo page from any recorded run:

```bash
smartassist qa demo --run-dir qa-artifacts/<run-id>
smartassist qa demo --run-dir qa-artifacts/<run-id> --output qa-artifacts/<run-id>/index.html
```

What this gives you:

- a scenario artifact bundle under `qa-artifacts/<run-id>/`
- contract assertions that read canonical SQLite state first
- a static HTML demo generated from the same artifacts CI uses
- a watch mode that refreshes the demo while scenarios are still running

GitHub Actions also runs the deterministic QA suite and uploads the artifact bundle. On `main` and manual workflow runs, the same bundle is published as the latest demo site through GitHub Pages.

---

## Website Deploy

The marketing site in [`website/`](website/) auto-deploys to `https://smartassist-memory.com`.

How it works:

- pushing a commit to `main` that changes `website/**` triggers the `Deploy Website` GitHub Actions workflow
- the workflow runs [`scripts/deploy_website.sh`](scripts/deploy_website.sh)
- that script syncs the contents of `website/` to the VPS path `/opt/smartassist-memory/site`
- after sync, the workflow smoke-checks `https://smartassist-memory.com`

Normal workflow:

```bash
git add website/
git commit -m "website: update landing page"
git push origin main
```

Important:

- only committed changes are deployed
- local uncommitted edits in `website/` do not go live
- the site is served separately from the GitHub Pages QA demo

---

## Troubleshooting

### `smartassist` or hook commands are not found

Run:

```bash
pipx ensurepath
```

Then open a new terminal and rerun `smartassist setup`.

### `smartassist setup` says `~/.claude/` is missing

Install Claude Code first and make sure it has been launched at least once.

### `smartassist doctor` says MCP registration is missing

Rerun:

```bash
smartassist setup
```

If this is an additional repo, `smartassist init` is enough.

SmartAssist prefers project-scoped registration and removes stale legacy registrations when it can.

### You opened a second repo and nothing is being retrieved

Run:

```bash
cd /path/to/repo
smartassist init
smartassist doctor
smartassist seed
smartassist health
```

### You want to remove SmartAssist from Claude Code

Run:

```bash
smartassist uninstall
```

Project data is left in place. To remove the package too:

```bash
pipx uninstall smartassist
```

---

## Testing

```bash
uv run pytest -q
uv run python -m smartassist.cli qa run --run-dir qa-artifacts/local
bash scripts/qa_package_smoke.sh
bash scripts/qa_pipx_smoke.sh
```

Use `smartassist doctor` and `smartassist health` after install if you want an environment-level verification inside a real project.

For the PyPI release flow, see [docs/pypi-release.md](docs/pypi-release.md).

---

## License

[Business Source License 1.1](LICENSE)

<p align="center">
  <strong>Built by Joey Rahme</strong><br>
  <a href="https://smartassist-ai.netlify.app/">Interactive Docs</a> · <a href="https://smartassist-ai.netlify.app/dashboard.html">Live Dashboard</a> · <a href="https://github.com/jnrahme/SmartAssist/wiki">Wiki</a>
</p>
