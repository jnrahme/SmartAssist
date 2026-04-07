<p align="center">
  <img src="https://img.shields.io/badge/python-3.10+-blue?style=flat-square&logo=python&logoColor=white" alt="Python 3.10+">
  <img src="https://img.shields.io/badge/npm-smartassist--memory-red?style=flat-square&logo=npm" alt="npm">
  <img src="https://img.shields.io/badge/license-BUSL--1.1-blue?style=flat-square" alt="BUSL 1.1 License">
  <img src="https://img.shields.io/badge/agents-Claude%20%7C%20Codex%20%7C%20Gemini%20%7C%20ChatGPT%20%7C%20Amp%20%7C%20OpenCode-violet?style=flat-square" alt="Multi-Agent">
</p>

<h1 align="center">SmartAssist Memory</h1>

<p align="center">
  <strong>AI memory that learns from developer feedback. Works with every coding agent.</strong><br>
  <em>Per-lesson reinforcement learning, dual-memory injection, and hybrid search — one knowledge base, any agent.</em>
</p>

<p align="center">
  <a href="https://smartassist-memory.com">Website</a> ·
  <a href="https://smartassist-memory.com/docs">Documentation</a> ·
  <a href="https://github.com/jnrahme/SmartAssist/issues">Issues</a>
</p>

---

## Install

```bash
npx smartassist-memory init
```

One command. Installs SmartAssist and sets up MCP in your current project.

Or install directly:

```bash
pipx install git+https://github.com/jnrahme/SmartAssist.git
smartassist setup
```

Prerequisites: Python 3.10+, Node.js 18+ (for npx)

---

## Works With Every Agent

```bash
# Claude Code
claude mcp add smartassist -- npx -y smartassist-memory serve

# Codex
codex mcp add smartassist -- npx -y smartassist-memory serve

# Any agent
npx smartassist-memory init --agent all
```

| Agent | MCP Tools | Auto-Injection | Hooks | Setup |
|---|---|---|---|---|
| **Claude Code** | Yes | Yes (every prompt) | Yes (5 hooks) | `smartassist setup` |
| **Codex** | Yes | Via AGENTS.md | No | `setup-agent codex` |
| **Gemini** | Via HTTP | No | No | Function declarations |
| **ChatGPT** | Via HTTP | No | No | OpenAPI custom action |
| **Amp** | Via CLI | No | No | Skill template |
| **OpenCode** | Yes | No | No | `setup-agent opencode` |

Claude Code gets the richest experience with automatic hook injection on every prompt. All agents share the same MCP tools, RLHF loop, and knowledge base.

---

## How It Works

1. **You give feedback** — `:)` or `:(` with context
2. **Your AI creates a lesson** — the LLM analyzes full conversation context and calls `create_lesson`
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

## Commands

```bash
smartassist setup              # Full Claude Code setup (MCP + hooks + init)
smartassist setup-agent <agent> # Register with: claude, codex, gemini, chatgpt, amp, opencode, all
smartassist doctor             # Audit install readiness
smartassist init               # Initialize current project
smartassist serve              # Start MCP server (stdio)
smartassist health             # Run health checks
smartassist seed               # Seed lessons from CLAUDE.md
smartassist vectorize          # Rebuild vector cache
smartassist maintenance        # Run staleness + compaction
smartassist analyze            # Show usage analytics
smartassist dashboard          # Generate HTML dashboard
smartassist uninstall          # Remove from Claude config
smartassist version            # Show version
claude-sa                      # Launch Claude Code with SmartAssist monitor
```

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
