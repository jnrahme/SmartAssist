# SmartAssist Memory

> Draft package README for the upcoming npm release. `smartassist-memory` is not published to npm yet.

AI memory that learns from developer feedback. Works with every AI coding agent.

## Install

```bash
npx smartassist-memory init
```

One command. Installs SmartAssist and registers the MCP server with your current project.

## Works With Every Agent

```bash
# Claude Code
claude mcp add smartassist -- npx -y smartassist-memory serve

# Codex
codex mcp add smartassist -- npx -y smartassist-memory serve

# Any agent — auto-detect
npx smartassist-memory init

# Specific agent
npx smartassist-memory init --agent codex
npx smartassist-memory init --agent gemini
npx smartassist-memory init --agent all
```

| Agent | MCP Tools | Auto-Injection | Hooks | Setup |
|---|---|---|---|---|
| Claude Code | Yes | Yes (every prompt) | Yes | `smartassist setup` |
| Codex | Yes | Via global Codex AGENTS | No | `setup-agent codex` |
| Gemini | Via HTTP | Via generated system instructions | No | Function declarations |
| ChatGPT | Via HTTP | Via GPT instructions | No | OpenAPI custom action |
| Amp | Via CLI | Via workspace skill | No | `setup-agent amp` |
| OpenCode | Yes | Via `opencode.json` instructions | No | `setup-agent opencode` |

## How It Works

1. You give feedback (`:)` or `:(` with context)
2. Your AI agent uses `apply_feedback_protocol` to dedupe, boost, merge, or create the right lesson
3. Per-lesson Thompson Sampling learns which lessons actually help
4. Next prompt — the best lessons are injected automatically
5. Your AI agent gets it right the first time

## Features

- **Per-lesson reinforcement learning** — Thompson Sampling with Beta-Bernoulli bandits
- **Dual-memory injection** — project rules + past corrections (MemAlign pattern)
- **Hybrid search** — keyword + semantic vectors + cross-encoder reranking
- **Works with Claude Code, Codex, Gemini, ChatGPT, Amp, OpenCode**
- **Zero config** — one command setup
- **Project-scoped** — each project has its own knowledge base

## Links

- Website: https://smartassist-memory.com
