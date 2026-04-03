# SmartAssist Memory

AI memory that learns from developer feedback. Works with Claude Code and Codex.

## Install

```bash
npx smartassist-memory init
```

That's it. One command installs SmartAssist and sets up MCP in your current project.

## Register with your AI agent

```bash
# Claude Code
claude mcp add smartassist -- npx -y smartassist-memory serve

# Codex
codex mcp add smartassist -- npx -y smartassist-memory serve
```

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
- **Zero config** — one command setup
- **Project-scoped** — each project has its own knowledge base

## Requirements

- Node.js 18+ (for npx)
- Python 3.10+ (for the SmartAssist runtime)
- pipx or pip3

## Links

- Website: https://smartassist-memory.com
- Documentation: https://smartassist-memory.com/docs
