# SmartAssist Memory

> Draft package README for the upcoming npm release. `smartassist-memory` is not published to npm yet, so this is not the current supported public install path.

AI memory that learns from developer feedback. Planned package support targets Claude Code first, then the broader agent matrix through setup surfaces.

## Install

```bash
npx smartassist-memory init
```

Planned one-command flow for the npm package once that release channel is live.

## Planned Agent Setup Surfaces

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
- **Targets Claude Code, Codex, Gemini, ChatGPT, Amp, and OpenCode**
- **Planned zero-config npm flow** once the package is published
- **Project-scoped** — each project has its own knowledge base

## Links

- Website: https://smartassist-memory.com
