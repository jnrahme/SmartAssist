# SmartAssist — Agent Handoff Document

> **Date**: 2026-03-05
> **Repo**: https://github.com/jnrahme/SmartAssist > **Status**: Feature code complete, 174 tests pass, blocked on MCP server connectivity

---

<!-- ============================================================
     GOAL — LOCKED — DO NOT MODIFY THIS SECTION
     This section was set by the project owner. Agents MUST read it
     fully before beginning any work. Agents MUST NOT edit, reword,
     reorder, or remove anything between these comment fences.
     ============================================================ -->

## Goal

**Objective:** Verify that SmartAssist creates lessons from user feedback context and that the full feedback loop works end-to-end — from a user sending `:)` with context, through hook-based lesson creation, to Claude drafting a comparison lesson via the MCP `compare_lesson` tool.

### Scope In

- Fix the MCP server connectivity issue (SmartAssist tools must appear in Claude Code)
- Verify lesson creation works when user sends `:) <context>` feedback
- Verify the A/B comparison pipeline: hook creates a lesson, Claude drafts one via `compare_lesson`, both log to `lesson_comparison.jsonl`
- Run `smartassist compare-lessons` CLI to confirm paired entries
- Push working code to https://github.com/jnrahme/SmartAssist for distribution as a Claude Code tool

### Scope Out

- Changing the lesson quality gates or scoring algorithm
- ShieldCortex or MemAlign integration (future phases, see `docs/`)
- Modifying the target project (bt-mobile-app) code
- Changing hook behavior beyond what's needed for the comparison feature

### Done When

- [ ] Fresh Claude Code session in `/Users/joeyrahme/GitHubWorkspace/bt-mobile-app/` shows `mcp__smartassist__compare_lesson` in deferred tools
- [ ] Sending `:) good use of semantic colors instead of hardcoded values` triggers Claude to call `compare_lesson`
- [ ] `smartassist compare-lessons` shows paired entries (hook + claude) for the same feedback context
- [ ] `cmd_setup()` in `cli.py` produces a working mcp.json config automatically (includes HOME, PATH, TMPDIR in env block)
- [ ] `pipx install --force .` installs cleanly with all changes
- [ ] All 174+ tests pass: `uv run --with pytest pytest tests/test_feedback_lesson.py -v`
- [ ] Code pushed to https://github.com/jnrahme/SmartAssist

<!-- END LOCKED SECTION — DO NOT MODIFY ABOVE THIS LINE -->

---

## Document Rules

| Section                  | Mutability  | Rule                                                               |
| ------------------------ | ----------- | ------------------------------------------------------------------ |
| **Goal** (above)         | LOCKED      | Never modify. Read at every session start.                         |
| **Progress Log** (below) | APPEND ONLY | Add new entries at the TOP. Never edit or delete existing entries. |
| **All other sections**   | WRITABLE    | May be updated with new evidence, hypotheses, or instructions.     |

---

## Project Overview

SmartAssist is a portable RAG (Retrieval-Augmented Generation) learning system for Claude Code. It has two halves:

- **Hooks** (shell commands): Fire on events like `UserPromptSubmit`. They detect feedback signals (`:)`, `:(`, etc.), create lessons, inject context via `additionalContext`. These work perfectly.
- **MCP Server** (stdio transport): Exposes tools like `rag_search`, `create_lesson`, `compare_lesson` over the MCP protocol. Claude Code spawns this as a subprocess. **This is what's broken.**

The hooks and MCP server are both installed via `pipx install .` which creates entry points in `~/.local/bin/`.

### Key Directories

| Path                                                                  | Purpose                                            |
| --------------------------------------------------------------------- | -------------------------------------------------- |
| `/Users/joeyrahme/Github/SmartAssist/`                                | Source code (this repo)                            |
| `/Users/joeyrahme/GitHubWorkspace/bt-mobile-app/`                     | Target project where SmartAssist is used           |
| `~/.claude/mcp.json`                                                  | MCP server configuration (user-level)              |
| `~/.claude/settings.json`                                             | Claude Code hooks configuration                    |
| `~/.local/pipx/venvs/smartassist/`                                    | Installed pipx virtual environment (Python 3.14.2) |
| `~/.local/bin/smartassist`                                            | Symlink → pipx venv entry point                    |
| `/Users/joeyrahme/GitHubWorkspace/bt-mobile-app/.claude/smartassist/` | Project-specific data (lessons, vector DB, logs)   |

---

## A/B Lesson Quality Comparison (Feature Context)

We built a system to compare hook-generated lessons vs Claude-generated lessons side-by-side:

1. **Hook path** (existing, production): `create_lesson_from_feedback()` in `prompt_inject.py` — regex pipeline, sub-millisecond, no LLM
2. **Claude path** (new): Claude calls `compare_lesson` MCP tool — uses conversation context to craft a lesson

Design: Hook still creates lessons (production unchanged). Claude also drafts a lesson via `compare_lesson` that logs but does NOT store. Both log to `lesson_comparison.jsonl` for review via `smartassist compare-lessons` CLI command.

### What's Complete (all code changes done, 174 tests pass)

| File                                 | Change                                                                                                                                                     |
| ------------------------------------ | ---------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `smartassist/lesson_feedback.py`     | Added `log_comparison_entry()` shared helper                                                                                                               |
| `smartassist/mcp_server.py`          | Added `compare_lesson` MCP tool (quality gates, no storage, comparison logging)                                                                            |
| `smartassist/hooks/prompt_inject.py` | Modified `build_rich_feedback_context()` to instruct Claude to call `compare_lesson`; removed `created_lesson` param; added hook-side comparison logging   |
| `smartassist/cli.py`                 | Added `cmd_compare_lessons()` CLI command; fixed `cmd_setup()` to include `SMARTASSIST_DATA_DIR` env var, absolute paths, and HOME/PATH/TMPDIR in mcp.json |
| `tests/test_feedback_lesson.py`      | Added `TestComparisonLogging`, `TestCompareLessonTool`; updated `TestBuildRichFeedbackContext`, `TestPromptInjectMainFeedback`                             |

**Verification**: `uv run --with pytest pytest tests/test_feedback_lesson.py -v` — all 174 pass.

---

## The Blocking Issue: MCP Server Not Connecting in Claude Code

### Symptom

Claude Code sessions cannot see ANY SmartAssist MCP tools. When Claude Code starts, it spawns MCP servers defined in `~/.claude/mcp.json`. The jira server connects fine (49 tools visible). The SmartAssist server **never connects** — zero `mcp__smartassist__*` tools appear in Claude's deferred tools list.

The hook side works perfectly (feedback detection, lesson creation, RAG injection, comparison logging). Only the MCP server side is broken.

### Current mcp.json Configuration (H1 fix applied 2026-03-05)

```json
{
  "mcpServers": {
    "smartassist": {
      "command": "/Users/joeyrahme/.local/pipx/venvs/smartassist/bin/python",
      "args": ["-m", "smartassist.mcp_server"],
      "env": {
        "SMARTASSIST_DATA_DIR": "/Users/joeyrahme/GitHubWorkspace/bt-mobile-app/.claude/smartassist",
        "HOME": "/Users/joeyrahme",
        "PATH": "/usr/local/bin:/usr/bin:/bin:/opt/homebrew/bin:/Users/joeyrahme/.local/bin",
        "TMPDIR": "/tmp"
      }
    }
  }
}
```

### What Works (tested manually)

The MCP server works perfectly when tested outside of Claude Code:

```bash
echo '{"jsonrpc":"2.0","id":1,"method":"initialize","params":{"protocolVersion":"2024-11-05","capabilities":{},"clientInfo":{"name":"test","version":"1.0"}}}' | \
  SMARTASSIST_DATA_DIR=/Users/joeyrahme/GitHubWorkspace/bt-mobile-app/.claude/smartassist \
  /Users/joeyrahme/.local/pipx/venvs/smartassist/bin/python -m smartassist.mcp_server
```

Response: Correct JSON-RPC initialize response with `serverInfo: {name: "smartassist", version: "1.26.0"}`. The `tools/list` returns 8 tools: `rag_search`, `rag_dashboard`, `rag_feedback`, `create_lesson`, `compare_lesson`, `boost_lesson`, `demote_lesson`, `merge_lessons`.

### What Doesn't Work

When Claude Code spawns the server (or when we spawn a nested `claude -p` instance), SmartAssist tools never appear. A debug wrapper script placed as the MCP command was **never executed** — its log file remained empty. This means Claude Code isn't even attempting to start the process.

---

## Evidence Collected

### Fact 1: Manual MCP server test passes

Server initializes in <500ms, responds to `initialize`, `tools/list`, and `tools/call` correctly. No stderr output. Works from any CWD with `SMARTASSIST_DATA_DIR` set.

### Fact 2: Jira MCP works in the same mcp.json

A nested `claude -p` instance can see all 49 `mcp__jira__*` tools. Same mcp.json, same Claude Code version.

### Fact 3: Hostinger MCP also doesn't work

The `hostinger` entry uses `"command": "hostinger-api-mcp"` (relative path) and also has zero tools visible. This might be a separate issue (missing binary) or the same root cause.

### Fact 4: Debug wrapper was never called

A shell script wrapper (`smartassist-mcp-wrapper.sh`) placed as the MCP command logged nothing — not even "started". Claude Code never executed it. This was tested with `claude -p --max-turns 2 --no-session-persistence`.

### Fact 5: Python 3.14.2 in the pipx venv

The jira server uses `uvx --python=3.12`. SmartAssist's pipx venv uses Python 3.14.2. Unknown if this causes issues with Claude Code's process spawning.

### Fact 6: MCP library version is 1.26.0

`mcp` package version 1.26.0 with `anyio 4.12.1`, `httpx 0.28.1`. The `FastMCP` server uses stdio transport.

### Fact 7: The entry point is a pipx symlink

`/Users/joeyrahme/.local/bin/smartassist` is a symlink → `/Users/joeyrahme/.local/pipx/venvs/smartassist/bin/smartassist`, which is a Python script with shebang `#!/Users/joeyrahme/.local/pipx/venvs/smartassist/bin/python`.

### Fact 8: Import time is fast

`from smartassist.mcp_server import mcp` takes 0.47s. Not a timeout issue.

### Fact 9: Server works with minimal environment

`env -i HOME=/Users/joeyrahme PATH=/usr/bin:/bin SMARTASSIST_DATA_DIR=... python -m smartassist.mcp_server` works fine.

### Fact 10: H1 fix applied (2026-03-05)

Added HOME, PATH, TMPDIR to mcp.json env block. Also updated `cmd_setup()` in cli.py to produce this config automatically. **Not yet verified in a fresh Claude Code session** — requires restart.

---

## Hypotheses (Ranked by Likelihood)

### H1: `env` field in mcp.json REPLACES environment instead of merging ← MOST LIKELY, FIX APPLIED

If Claude Code passes ONLY the env vars specified (not merged with parent), the Python interpreter would lack `HOME`, `PATH`, `TMPDIR`, etc. The jira server works because `uvx` is more resilient to minimal environments, OR because jira's `env` block contains enough for `uvx` to function.

**Status**: Fix applied to mcp.json (HOME, PATH, TMPDIR added). Needs verification in fresh Claude Code session.

### H2: Claude Code rejects Python 3.14 executables

Python 3.14.2 is very new. Claude Code might have compatibility checks or the Node.js child_process spawning might behave differently with 3.14.

**Test**: Create a pipx venv with `--python=3.12` and test.

### H3: Claude Code has a startup timeout that's too aggressive

Even though import takes 0.47s, maybe Claude Code expects a response within a few hundred ms.

**Test**: Create a minimal MCP server (no smartassist imports) and see if it connects.

### H4: FastMCP 1.26.0 protocol incompatibility

The MCP Python SDK might use a protocol feature that Claude Code's MCP client doesn't support.

**Test**: Check if a simple `mcp` server (not using FastMCP) connects.

### H5: Claude Code doesn't execute the MCP command at all for some config reason

Maybe there's a validation step that silently rejects the config entry.

**Test**: Check Claude Code source/docs for MCP config validation rules.

---

## Quick Wins to Try (Priority Order)

1. **Verify H1 fix** — Restart Claude Code and check for `mcp__smartassist__*` tools. The mcp.json already has HOME, PATH, TMPDIR added.

2. **Try a minimal MCP server** — Isolates whether the issue is SmartAssist-specific or general:

   ```python
   # /tmp/test_mcp.py
   from mcp.server.fastmcp import FastMCP
   mcp = FastMCP("test")

   @mcp.tool()
   def ping() -> str:
       """Return pong."""
       return "pong"

   mcp.run(transport="stdio")
   ```

   ```json
   "test": {
     "command": "/Users/joeyrahme/.local/pipx/venvs/smartassist/bin/python",
     "args": ["/tmp/test_mcp.py"],
     "env": {
       "HOME": "/Users/joeyrahme",
       "PATH": "/usr/local/bin:/usr/bin:/bin",
       "TMPDIR": "/tmp"
     }
   }
   ```

3. **Try Python 3.12** — If minimal server also fails with 3.14:

   ```bash
   pipx install --python=python3.12 --force .
   ```

4. **Check Claude Code MCP debug output** — Look for MCP-related error reporting in Claude Code's internals.

---

## Files to Read First

If you're picking up this task, read these files in order:

1. `~/.claude/mcp.json` — Current MCP server config (H1 fix already applied)
2. `smartassist/mcp_server.py` — The MCP server code (lines 1-80 for setup, line 1060-1066 for entry point)
3. `smartassist/config.py` — How `SMARTASSIST_DATA_DIR` is resolved (critical for understanding why env var is needed)
4. `smartassist/cli.py` — `cmd_setup()` around line 396 (how mcp.json gets configured)

---

## Reproduction Steps

### Verify the issue exists

```bash
# Spawn a Claude Code instance and check for SmartAssist tools
claude -p --max-turns 3 --no-session-persistence \
  "Search ToolSearch for 'smartassist'. List tool names or say NONE."
# Expected after H1 fix: 8 tools listed
# If still NONE: H1 wasn't the cause, move to H2/H3
```

### Verify the server works manually

```bash
SMARTASSIST_DATA_DIR=/Users/joeyrahme/GitHubWorkspace/bt-mobile-app/.claude/smartassist \
  /Users/joeyrahme/.local/pipx/venvs/smartassist/bin/python -m smartassist.mcp_server
# Then paste this JSON and press Enter:
# {"jsonrpc":"2.0","id":1,"method":"initialize","params":{"protocolVersion":"2024-11-05","capabilities":{},"clientInfo":{"name":"test","version":"1.0"}}}
# Should get a valid JSON response back
```

### End-to-end lesson test (after MCP is working)

```bash
# In a Claude Code session in bt-mobile-app:
# 1. Do some coding work so Claude has conversation context
# 2. Send: :) good use of semantic colors instead of hardcoded values
# 3. Verify hook creates a lesson (check live log)
# 4. Verify Claude calls compare_lesson MCP tool
# 5. Run: smartassist compare-lessons
# 6. Confirm paired entries (hook + claude) with matching context
```

---

<!-- ============================================================
     PROGRESS LOG — APPEND ONLY
     Add new entries at the TOP. Never edit or delete existing entries.
     Format: ### [YYYY-MM-DD] Session N — summary
     ============================================================ -->

## Progress Log

### [2026-03-05] Session 4 — Root cause found and fixed, MCP server connected

**Root Cause:** SmartAssist was never registered in Claude Code's actual runtime config. `cmd_setup()` wrote to `~/.claude/mcp.json`, but Claude Code loads MCP servers from `~/.claude.json` (the project state file, managed internally). The `claude mcp add` CLI command is the proper registration method.

**Evidence:** Debug log (`~/.claude/debug/latest`) showed Claude Code starting jira, figma, cypress-mcp, playwright (all in `~/.claude.json` project config) but never attempting smartassist (only in `~/.claude/mcp.json`). H1-H5 hypotheses were all wrong — the server was never spawned, not failing to connect.

**Completed:**

- Identified root cause via `.claude.json` project state analysis and Claude Code debug log correlation
- Registered smartassist via `claude mcp add -s user` — `claude mcp list` shows `smartassist: ✓ Connected`
- Fixed `cmd_setup()` in cli.py to use `claude mcp add` with fallback to legacy mcp.json path
- Added `subprocess` import to cli.py
- All 180 tests pass (up from 174)

**Remaining:**

- Restart Claude Code session and verify `mcp__smartassist__*` tools in deferred tools
- Run end-to-end lesson comparison test (`:)` feedback → `compare_lesson` → `compare-lessons` CLI)
- Push to GitHub

---

### [2026-03-05] Session 3 — H1 fix applied, document restructured

**Completed:**

- Applied H1 fix: added HOME, PATH, TMPDIR to mcp.json env block for smartassist
- Updated `cmd_setup()` in cli.py to auto-generate the fixed config (resolves venv Python path, includes system env vars)
- Reinstalled via `pipx install --force .`
- Restructured AGENTS.md with locked Goal section, append-only Progress Log, and document rules
- Verified MCP server works with stripped environment (`env -i` with only the 4 specified vars)

**Blockers:**

- H1 fix not yet verified in a fresh Claude Code session (requires restart)

**Next session should:**

- Restart Claude Code and check for `mcp__smartassist__*` tools
- If H1 worked: run the end-to-end lesson test, then push to GitHub
- If H1 failed: try H2 (Python 3.12) or H3 (minimal MCP server)

---

### [2026-03-05] Session 2 — Feature code complete, MCP debugging

**Completed:**

- All A/B comparison code implemented (mcp_server.py, prompt_inject.py, lesson_feedback.py, cli.py)
- 174 tests passing
- Identified root cause: `config.py` walks CWD to find `.claude/smartassist/` but MCP server CWD is not the project dir
- Added `SMARTASSIST_DATA_DIR` env var to mcp.json
- Made `_get_storage()` and `_get_db()` in mcp_server.py resilient with better error messages
- Created initial AGENTS.md debugging handoff
- Created HTML docs: SmartAssist-Overview.html, shieldcortex-integration.html, memalign-integration.html, integration-roadmap.html
- Researched ShieldCortex (v0.1.0 Python SDK, thin wrapper) and MemAlign (Experimental in MLflow)

**Blockers:**

- MCP server never connects in Claude Code despite working manually
- Debug wrapper script was never executed by Claude Code
- Nested `claude -p` instances couldn't see SmartAssist tools

---

### [2026-03-05] Session 1 — A/B comparison design and implementation

**Completed:**

- Designed A/B lesson comparison architecture
- Implemented `log_comparison_entry()` shared helper
- Implemented `compare_lesson` MCP tool (quality gates, no storage, comparison logging)
- Modified `build_rich_feedback_context()` to instruct Claude to call `compare_lesson`
- Added `smartassist compare-lessons` CLI command
- Added comprehensive tests (TestComparisonLogging, TestCompareLessonTool, updated existing tests)

**Blockers:**

- None at code level — feature is complete
