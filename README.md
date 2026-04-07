<p align="center">
  <img src="https://img.shields.io/badge/python-3.10+-blue?style=flat-square&logo=python&logoColor=white" alt="Python 3.10+">
  <img src="https://img.shields.io/badge/license-BUSL--1.1-blue?style=flat-square" alt="BUSL 1.1 License">
  <img src="https://img.shields.io/badge/agents-Claude%20%7C%20Codex%20%7C%20Gemini%20%7C%20ChatGPT%20%7C%20Amp%20%7C%20OpenCode-violet?style=flat-square" alt="Multi-Agent">
</p>

<h1 align="center">SmartAssist Memory</h1>

<p align="center">
  <strong>AI memory that learns from developer feedback. Best-validated today on Claude Code, with setup surfaces for other coding agents.</strong><br>
  <em>Per-lesson reinforcement learning, dual-memory injection, and hybrid search — one knowledge base, any agent.</em>
</p>

<p align="center">
  <a href="https://smartassist-memory.com">Website</a> ·
  <a href="smartassist-overview.html">Architecture Overview</a> ·
  <a href="https://github.com/jnrahme/SmartAssist/discussions">Discussions</a> ·
  <a href="https://github.com/jnrahme/SmartAssist/issues/new/choose">Feedback</a> ·
  <a href="CONTRIBUTING.md">Contributing</a> ·
  <a href="SECURITY.md">Security</a>
</p>

---

## Install

```bash
pipx install git+https://github.com/jnrahme/SmartAssist.git
smartassist setup
```

This is the supported install path today.

Local checkout for development:

```bash
git clone https://github.com/jnrahme/SmartAssist.git
cd SmartAssist
pipx install .
```

Not live yet:

- `pipx install smartassist`
- `npm install -g smartassist-memory`
- `brew install smartassist-memory/tap/smartassist`
- `curl -fsSL https://smartassist-memory.com/install | sh`

Prerequisites: Python 3.10+

---

## Agent Setup Surfaces

```bash
# First install SmartAssist from this repo
pipx install git+https://github.com/jnrahme/SmartAssist.git

# Claude Code
smartassist setup

# Codex
smartassist setup-agent codex

# Register everything SmartAssist currently supports
smartassist setup-agent all
```

| Agent | MCP Tools | Auto-Injection | Hooks | Setup |
|---|---|---|---|---|
| **Claude Code** | Yes | Yes (every prompt) | Yes (5 hooks) | `smartassist setup` |
| **Codex** | Yes | Via global Codex AGENTS | No | `smartassist setup-agent codex` |
| **Gemini** | Via HTTP | Via generated system instructions | No | Function declarations + system instructions |
| **ChatGPT** | Via HTTP | Via GPT instructions | No | OpenAPI custom action + GPT instructions |
| **Amp** | Via CLI | Via workspace skill | No | `smartassist setup-agent amp` |
| **OpenCode** | Yes | Via `opencode.json` instructions | No | `smartassist setup-agent opencode` |

Claude Code gets the richest experience with automatic hook injection on every prompt. Codex has setup plus targeted validation in this repo. The other agents rely on their native instruction surfaces and the same SmartAssist feedback workflow through `apply_feedback_protocol`, but they still need broader end-to-end runtime validation before the whole matrix should be treated as equally production-ready.

OpenCode setup writes a project-local `opencode.json` that includes the SmartAssist MCP
server, the generated `.smartassist/opencode-instructions.md` file, and recommended model
defaults: `anthropic/claude-sonnet-4-5` for the main model and
`anthropic/claude-haiku-4-5` for `small_model`. If your team uses another provider, edit the
top-level `model` and `small_model` strings after setup.

---

## How It Works

1. **You give feedback** — `:)` or `:(` with context
2. **Your AI stores reusable feedback** — Claude hooks can auto-create lessons from live feedback, while non-hook agents use `apply_feedback_protocol` to dedupe, boost, merge, or create the right lesson
3. **Thompson Sampling learns** — per-lesson Beta-Bernoulli bandits rank lessons by proven impact
4. **Best lessons injected** — dual-memory: project rules (semantic) + past corrections (episodic)
5. **Your AI gets it right** — the system improves with every interaction

### The RLHF Reinforcement Loop

Every feedback signal makes the system smarter:

- Positive feedback (`:)`) → `alpha += fractional credit` → lesson ranks higher
- Negative feedback (`:(`) → `beta += fractional credit` → lesson ranks lower
- New lessons get fair exploration (Beta(1,1) = uniform prior)
- Stale lessons gradually re-enter exploration (30-day decay)
- The system never stops learning

---

## MCP Tools

| Tool | What it does |
|---|---|
| `rag_search` | Search for lessons with hybrid keyword + semantic + cross-encoder reranking |
| `rag_dashboard` | View Thompson reliability scores, corpus stats, feedback metrics |
| `rag_feedback` | Record whether a suggestion was helpful or not |
| `apply_feedback_protocol` | Normalize feedback, dedupe against existing lessons, boost overlaps, and create only when needed |
| `create_lesson` | Store a new lesson with quality gates and Thompson update |
| `compare_lesson` | Draft a lesson for A/B comparison without storing |
| `boost_lesson` | Increase a lesson's Thompson priority |
| `demote_lesson` | Reduce a lesson's priority or retire it |
| `merge_lessons` | Consolidate overlapping lessons into one principle |

---

## Architecture

### Retrieval Stack

- **Keyword search**: IDF-weighted token matching with synonym expansion (<50ms)
- **Semantic search**: BAAI/bge-m3 embeddings (1024 dim) + LanceDB hybrid retrieval
- **Reranking**: Cross-encoder (ms-marco-MiniLM-L-6-v2) + Thompson Sampling
- **Dual-memory**: MemAlign pattern — project rules (lessons) + past corrections (episodes)
- **Storage**: SQLite canonical store (smartassist.db) with FTS5 search projection

### Hook Lifecycle (Claude Code)

| Event | Command | Purpose |
|---|---|---|
| `UserPromptSubmit` | `smartassist-prompt-inject` | Inject lessons + detect feedback + Thompson rerank |
| `SessionStart` | `smartassist-session-start` | Inject boundary pack for weak categories |
| `PreToolUse` | `smartassist-commit-hook` | Gate enforcement + commit analysis |
| `PostToolUse` | `smartassist-show-lessons` | Display retrieved lessons after search |
| `SessionEnd` | `smartassist-session-end` | Save session analytics + refresh boundary pack |

### Data Layout

| Path | Purpose |
|---|---|
| `<project>/.claude/smartassist/data/smartassist.db` | Canonical SQLite store (lessons, scores, events, Thompson, search projection) |
| `<project>/.claude/smartassist/data/` | Compatibility exports (curated_lessons.json, feedback_log.jsonl) |
| `<project>/.claude/smartassist/lancedb/` | LanceDB vector cache |
| `<project>/.mcp.json` | Project-scoped MCP registration |

---

## Deep Seed — LLM-Powered Codebase Analysis

```bash
smartassist seed --deep              # auto-detects Claude/Codex/Ollama
smartassist seed --deep --llm claude # use Claude Code CLI (zero config)
smartassist seed --deep --llm codex  # use Codex CLI (zero config)
smartassist seed --deep --llm ollama # use local Ollama (free)
smartassist seed --deep --llm anthropic --model claude-opus-4-20250514
smartassist seed --deep --llm custom --model meta-llama/Llama-3-70b
```

Analyzes your codebase (git history, PR reviews, configs, test utilities, CI pipelines, code structure) and dynamically calculates how many lessons to create based on project complexity:

- 973 source files → 46 lessons
- 50 source files → 20 lessons
- Monorepo with CI + custom test utils → more testing and build lessons
- Simple project → fewer, more focused lessons

Works with any LLM: Claude, Codex, Anthropic API, OpenAI API, Ollama (local), or any OpenAI-compatible endpoint (Together, Groq, Mistral, Fireworks, LM Studio).

---

## Commands

```bash
smartassist setup              # Full Claude Code setup (MCP + hooks + init)
smartassist setup-agent <agent> # Register with: claude, codex, gemini, chatgpt, amp, opencode, all
smartassist doctor             # Audit install readiness
smartassist init               # Initialize current project
smartassist serve              # Start MCP server (stdio)
smartassist health             # Run health checks
smartassist seed               # Seed lessons from CLAUDE.md
smartassist seed --deep        # LLM-powered codebase analysis (architect-level lessons)
smartassist vectorize          # Rebuild vector cache
smartassist maintenance        # Run staleness + compaction
smartassist analyze            # Show usage analytics
smartassist dashboard          # Generate HTML dashboard
smartassist uninstall          # Remove from Claude config
smartassist version            # Show version
claude-sa                      # Launch Claude Code with SmartAssist monitor
```

---

## Feedback, Support, and Contributing

| Need | Best path |
|---|---|
| Setup friction | [Open the setup issue form](https://github.com/jnrahme/SmartAssist/issues/new/choose) |
| Product feedback | [Use the feedback chooser](https://github.com/jnrahme/SmartAssist/issues/new/choose) |
| Bugs | [File a bug report](https://github.com/jnrahme/SmartAssist/issues/new/choose) |
| Questions and discussion | [Use GitHub Discussions](https://github.com/jnrahme/SmartAssist/discussions) |
| Contributing | [Read CONTRIBUTING.md](CONTRIBUTING.md) |
| Community expectations | [Read CODE_OF_CONDUCT.md](CODE_OF_CONDUCT.md) |
| Security issues | [Follow SECURITY.md](SECURITY.md) |

Good places to start contributing:

- issues labeled `good first issue`
- issues labeled `help wanted`
- onboarding, docs, and QA workflow improvements

Maintainers planning outreach or contributor follow-up can use the runbook at
[`docs/runbooks/growth-engine.md`](docs/runbooks/growth-engine.md).

Exact launch copy for X, Reddit, and Show HN lives in
[`docs/runbooks/launch-pack.md`](docs/runbooks/launch-pack.md).

---

## Troubleshooting

### `smartassist` not found

```bash
pipx ensurepath
# Open a new terminal, then:
smartassist setup
```

### Doctor says MCP registration missing

```bash
smartassist setup          # For first project
smartassist init           # For additional projects
```

### Remove SmartAssist

```bash
smartassist uninstall      # Remove from Claude config
pipx uninstall smartassist # Remove the package
```
