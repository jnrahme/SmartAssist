# SmartAssist Adapters

One MCP server, every AI coding agent.

## Quick Setup

```bash
# Auto-detect your agent
npx smartassist-memory init

# Or specify
smartassist setup --agent claude
smartassist setup --agent codex
smartassist setup --agent all
```

## Manual Registration

### Claude Code
```bash
claude mcp add smartassist -- npx -y smartassist-memory serve
```
Or copy `claude/.mcp.json` to your project root.

### Codex
```bash
codex mcp add smartassist -- npx -y smartassist-memory serve
```
Or merge `codex/config.toml` into `~/.codex/config.toml`.

### Gemini
Import `gemini/function-declarations.json` as tool definitions.
Point the HTTP endpoints at your local SmartAssist server.

### ChatGPT
Import `chatgpt/openapi.yaml` as a Custom Action in your GPT.
Start the server locally: `smartassist serve`

### Amp
Copy `amp/SKILL.md` into your Amp skills directory.

### OpenCode
Copy `opencode/opencode.json` to your project root or merge into `~/.opencode/config.json`.

## What Each Agent Gets

| Feature | Claude | Codex | Gemini | ChatGPT | Amp | OpenCode |
|---|---|---|---|---|---|---|
| MCP tools (search, create, boost, demote, merge) | Yes | Yes | Via HTTP | Via HTTP | Via CLI | Yes |
| Auto-injection every prompt (hooks) | Yes | No | No | No | No | No |
| Feedback signals (:) :() | Yes (hook) | Manual | Manual | Manual | Manual | Manual |
| Session boundary packs | Yes (hook) | No | No | No | No | No |
| Gate enforcement | Yes (PreToolUse) | No | No | No | No | No |
| Thompson Sampling RLHF | Yes | Yes | Yes | Yes | Yes | Yes |
| Dual-memory (semantic + episodic) | Yes | Yes | Yes | Yes | Yes | Yes |

Claude Code gets the richest experience because of hooks. All other agents get the same MCP tools and learning loop — they just need to call the tools proactively instead of having them triggered automatically.
