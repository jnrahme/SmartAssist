# SmartAssist Adapters

One SmartAssist server plus agent-specific setup surfaces.

Current status: these adapter files are real, but the package-registry install channels are not published yet. Use the source install from the repo README first, then run the adapter-specific setup commands below. Claude has the strongest runtime validation today; the other agent paths are setup-supported but still need broader end-to-end proof.

## Quick Setup

```bash
# Install SmartAssist first
pipx install git+https://github.com/jnrahme/SmartAssist.git

# Claude Code
smartassist setup

# Or specify another agent
smartassist setup-agent codex
smartassist setup-agent all
```

## Manual Registration

### Claude Code
```bash
smartassist setup
```
Or copy `claude/.mcp.json` to your project root.

### Codex
```bash
smartassist setup-agent codex
```
This registers MCP, writes SmartAssist guidance into `~/.codex/AGENTS.md`, and ensures Codex can still fall back to `CLAUDE.md` when a repo has no `AGENTS.md`.

### Gemini
Import `gemini/function-declarations.json` as tool definitions.
Paste the generated `.smartassist/gemini-system-instructions.md` into your Gemini system instructions.
Point the HTTP endpoints at your local SmartAssist server.

### ChatGPT
Import `chatgpt/openapi.yaml` as a Custom Action in your GPT.
Paste the generated `.smartassist/chatgpt-instructions.md` into your GPT instructions.
Start the server locally: `smartassist serve`

### Amp
Run `smartassist setup-agent amp` to install a SmartAssist skill into `.agents/skills/smartassist-memory/SKILL.md`.

### OpenCode
Run `smartassist setup-agent opencode`, or copy `opencode/opencode.json` to your project root and merge it manually. The setup command also writes `.smartassist/opencode-instructions.md` and wires it into `opencode.json`.

## What Each Agent Gets

| Feature | Claude | Codex | Gemini | ChatGPT | Amp | OpenCode |
|---|---|---|---|---|---|---|
| MCP tools (search, dashboard, feedback, lesson workflow, compare, boost, demote, merge) | Yes | Yes | Via HTTP | Via HTTP | Via CLI | Yes |
| Auto-injection every prompt (hooks) | Yes | Via global Codex AGENTS | No | No | Via workspace skill | Via `opencode.json` instructions |
| Feedback signals (:) :() | Yes (hook) | Via `apply_feedback_protocol` | Via `apply_feedback_protocol` | Via `apply_feedback_protocol` | Via `apply_feedback_protocol` | Via `apply_feedback_protocol` |
| Session boundary packs | Yes (hook) | No | No | No | No | No |
| Gate enforcement | Yes (PreToolUse) | No | No | No | No | No |
| Thompson Sampling RLHF | Yes | Yes | Yes | Yes | Yes | Yes |
| Dual-memory (semantic + episodic) | Yes | Yes | Yes | Yes | Yes | Yes |

Claude Code gets the richest experience because of hooks. The other agents should pick up SmartAssist through their native instruction surfaces, but the actual lesson workflow is server-owned through `apply_feedback_protocol` so you do not have to re-teach every agent the full duplicate/merge/create sequence.
