# SmartAssist Memory

AI memory system that learns from developer feedback. Works with Claude Code and Codex.

## Install

```bash
npm install -g smartassist-memory
```

## Setup

```bash
cd your-project
smartassist setup
```

That's it. SmartAssist learns from your feedback and injects relevant lessons into every prompt.

## How It Works

1. You give feedback (`:)` or `:(` with context)
2. Your AI agent analyzes the conversation and creates a specific lesson
3. Thompson Sampling learns which lessons actually help
4. Next prompt — the best lessons are injected automatically
5. Your AI agent gets it right the first time

## Features

- **Per-lesson reinforcement learning** — Thompson Sampling with Beta-Bernoulli bandits
- **Dual-memory injection** — project rules + past corrections (MemAlign pattern)
- **Hybrid search** — keyword matching + semantic vectors + cross-encoder reranking
- **Works with Claude Code and Codex** — same MCP tools, same memory
- **Zero config** — `smartassist setup` handles everything
- **Project-scoped** — each project has its own knowledge base
- **SQLite canonical store** — one file, ACID transactions, no data loss

## Links

- Website: https://smartassist-memory.com
- Issues: https://github.com/jnrahme/SmartAssist/issues
