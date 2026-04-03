# SmartAssist — Agent Handoff Document

> **Date**: 2026-03-05
> **Repo**: https://github.com/jnrahme/SmartAssist > **Status**: Phase 1 COMPLETE ✅ — MCP connected, E2E feedback loop verified. Phase 2 in progress.

---

## Phase 1 Goal — ✅ COMPLETE

**Objective:** Verify that SmartAssist creates lessons from user feedback context and that the full feedback loop works end-to-end — from a user sending `:)` with context, through hook-based lesson creation, to Claude drafting a comparison lesson via the MCP `compare_lesson` tool.

### Done When (all verified 2026-03-05)

- [x] Fresh Claude Code session shows `mcp__smartassist__compare_lesson` in deferred tools
- [x] Sending `:) good job on using the new theme folder to do this` triggers Claude to call `compare_lesson`
- [x] `compare_lesson` logs A/B comparison entry (`"not stored — A/B"`)
- [x] `cmd_setup()` in `cli.py` registers via `claude mcp add -s user` (correct config target)
- [x] `pipx install --force .` installs cleanly
- [x] 296 tests pass (pre-push gate)
- [x] Code pushed to https://github.com/jnrahme/SmartAssist
- [x] Live monitor shows lesson injection (43% hit rate, 162+ prompts tracked)
- [x] Hook-side reinforcement working (boost/demote on feedback signals)

---

## Phase 2 Goal — Reusable Tool for All Claude Developers

**Objective:** Turn SmartAssist from a personal tool into a distributable, zero-config learning system that any Claude Code developer can install and use immediately. SmartAssist should be the standard way Claude Code learns from user feedback across projects.

### Scope In

- **One-command install**: `pipx install smartassist && smartassist setup` should work on any machine with Claude Code
- **Zero-config per project**: `smartassist init` in any repo should set up the data directory, hooks fire automatically
- **Multi-project support**: The MCP server should work across projects without hardcoded paths (dynamic `SMARTASSIST_DATA_DIR` per project)
- **Documentation**: README that gets a developer from zero to working in under 5 minutes
- **PyPI distribution**: Publish to PyPI so `pipx install smartassist` works globally
- **Robustness**: Graceful degradation when hooks or MCP server hit edge cases
- **Onboarding experience**: First-run feedback that shows the system is working (e.g. seed lessons, live monitor)

### Scope Out

- ShieldCortex or MemAlign integration (future phases, see `docs/`)
- Web UI or desktop app — CLI only for now
- Paid features or SaaS hosting — open source tool
- Supporting non-Claude Code editors (VS Code extension, etc.)

### Done When

- [ ] `pipx install smartassist` works from PyPI (not just local install)
- [ ] `smartassist setup` works on a fresh machine with Claude Code installed (no manual config)
- [ ] Works on at least 2 different projects without reconfiguration
- [ ] README has quickstart that takes <5 minutes from install to first lesson
- [ ] MCP server auto-discovers project data directory (no hardcoded `SMARTASSIST_DATA_DIR`)
- [ ] CI passes on GitHub (lint, tests, compile check)
- [ ] At least one external user has installed and used it successfully

### Key Technical Challenges

1. **`SMARTASSIST_DATA_DIR` is hardcoded per project** — the MCP server env in `claude mcp add` points to one specific project's data dir. Need: either CWD-based discovery, or per-project MCP registration, or a config file the server reads at startup.
2. **`claude mcp add -s user` registers globally** — but each project needs its own data dir. Need: either dynamic path resolution in the MCP server, or project-scoped registration.
3. **Hook commands must be on PATH** — `pipx ensurepath` handles this, but new terminals need to be opened. Setup should detect and warn.
4. **Python version compatibility** — currently tested on 3.14.2 only. Need to verify 3.10-3.13.
5. **First-run experience** — user needs to see the system working immediately after setup.

---

## Document Rules

| Section                  | Mutability  | Rule                                                               |
| ------------------------ | ----------- | ------------------------------------------------------------------ |
| **Goal** (above)         | LOCKED      | Never modify. Read at every session start.                         |
| **Progress Log** (below) | APPEND ONLY | Add new entries at the TOP. Never edit or delete existing entries. |
| **All other sections**   | WRITABLE    | May be updated with new evidence, hypotheses, or instructions.     |

---

## Project Overview

SmartAssist is a portable RAG (Retrieval-Augmented Generation) learning system for Claude Code. It learns from user feedback (`:)`, `:(`, thumbs up/down) and injects relevant lessons into Claude's context on every prompt.

### Architecture

- **Hooks** (5 shell commands): Fire on Claude Code events (`UserPromptSubmit`, `SessionStart`, `PreToolUse`, `PostToolUse`, `SessionEnd`). They detect feedback signals, create lessons, inject context via `additionalContext`, and track session metrics.
- **MCP Server** (stdio transport): Exposes 8 tools (`rag_search`, `rag_dashboard`, `rag_feedback`, `create_lesson`, `compare_lesson`, `boost_lesson`, `demote_lesson`, `merge_lessons`). Claude calls these directly for lesson management.
- **CLI** (`smartassist` command): Setup, health checks, analytics, dashboard, seeding, and A/B comparison review.

Install: `pipx install .` → creates entry points in `~/.local/bin/`.

### Key Directories

| Path                                   | Purpose                                                                    |
| -------------------------------------- | -------------------------------------------------------------------------- |
| `/Users/joeyrahme/Github/SmartAssist/` | Source code (this repo)                                                    |
| `~/.claude.json`                       | Where `claude mcp add` registers the MCP server (NOT `~/.claude/mcp.json`) |
| `~/.claude/settings.json`              | Claude Code hooks configuration                                            |
| `~/.local/pipx/venvs/smartassist/`     | Installed pipx virtual environment                                         |
| `<project>/.claude/smartassist/`       | Per-project data (lessons, vector DB, logs, comparison data)               |

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

## Resolved: MCP Server Connectivity (Phase 1)

<details>
<summary>Root cause and fix (click to expand)</summary>

**Root cause:** `cmd_setup()` wrote to `~/.claude/mcp.json`, but Claude Code loads MCP servers from `~/.claude.json` (internal project state). The server was never spawned because it was registered in the wrong config file. H1-H5 hypotheses were all wrong.

**Fix:** Use `claude mcp add -s user` instead of writing to `mcp.json` directly. Applied in Session 4.

**Key lesson for Phase 2:** The `claude mcp add` CLI is the only reliable way to register MCP servers. Never write to `~/.claude/mcp.json` directly — Claude Code may not read it.

</details>

---

## Files to Read First

1. `smartassist/mcp_server.py` — MCP server (8 tools, FastMCP stdio transport)
2. `smartassist/hooks/prompt_inject.py` — Main hook (feedback detection, lesson injection, reinforcement)
3. `smartassist/cli.py` — `cmd_setup()` uses `claude mcp add`, `cmd_uninstall()` for cleanup
4. `smartassist/config.py` — `SMARTASSIST_DATA_DIR` resolution (key challenge for multi-project support)
5. `tests/test_feedback_lesson.py` — 180 tests covering the feedback/lesson system

---

<!-- ============================================================
     PROGRESS LOG — APPEND ONLY
     Add new entries at the TOP. Never edit or delete existing entries.
     Format: ### [YYYY-MM-DD] Session N — summary
     ============================================================ -->

## Progress Log

### [2026-04-03] Session 9 — SQLite-backed QA automation and live demo harness implemented and verified

**Completed:**

- Added a `smartassist qa` command surface for deterministic runtime-contract scenarios, demo rendering, and artifact cleanup
- Added a reusable QA harness under `smartassist/qa/` with scenario execution, artifact capture, static demo generation, and watch-mode refresh
- Expanded the contract suite to cover retrieval parity, feedback lesson creation, comparison logging without storage, commit promotion, session dedup, retirement behavior, seeding, gate stats, boundary-pack refresh, rebuild convergence, lesson-cap enforcement, merge consolidation, and `doctor` false-ready rejection
- Updated `smartassist-overview.html` and `README.md` with an automation + live demo section that explains the QA flow and public demo artifacts
- Added CI wiring in `.github/workflows/pr-checks.yml` and `.github/workflows/qa.yml` so deterministic QA runs generate artifact bundles and a publishable static demo
- Hardened `scripts/qa_package_smoke.sh` to build from an isolated temp source copy so packaging checks do not race on shared `build/` or `smartassist.egg-info/` state during concurrent local runs
- Verified the repo as a real SmartAssist project with `smartassist setup`, so preflight, MCP protocol, and Claude headless smoke run against an actually registered project

**Verification:**

- `python3 -m compileall -q smartassist tests`
- `pytest -q` → `403 passed`
- `python3 -m smartassist.cli qa run --run-dir qa-artifacts/final-contracts` → PASS
- `python3 -m smartassist.cli qa demo --run-dir qa-artifacts/final-contracts --output qa-artifacts/final-contracts/index.html` → PASS
- `python3 -m smartassist.cli qa run --scenario feedback_creates_active_lesson --watch --run-dir qa-artifacts/watch-smoke` → PASS
- `python3 -m smartassist.cli qa clean --run-dir qa-artifacts/watch-smoke` → PASS
- `python3 -m smartassist.cli setup` → PASS
- `bash scripts/qa_preflight.sh` → PASS
- `bash scripts/qa_mcp_protocol.sh --timeout 5` → PASS
- `bash scripts/qa_claude_headless_smoke.sh --timeout 60` → PASS
- `bash scripts/qa_autodiagnose.sh --max-attempts 1 --run-dir qa-artifacts/autodiagnose-final` → PASS
- `bash scripts/qa_package_smoke.sh` → PASS
- `bash scripts/qa_pipx_smoke.sh` → PASS

**Remaining:**

- Gate satisfaction / temporary evidence flow on top of the V1 gate engine
- PyPI publish + verification against the published package
- External-user validation on a machine outside this development environment

### [2026-03-24] Session 8 — Local fresh-machine and two-project setup validation verified

**Completed:**

- Verified the real Claude CLI behavior in a temp HOME: `claude mcp add -s project` writes project-scoped `.mcp.json`
- Extended `scripts/qa_pipx_smoke.sh` to validate packaged install plus `smartassist setup` across two isolated temp projects in one temp HOME
- Added post-setup checks that both projects get their own `.mcp.json`, avoid hardcoded `SMARTASSIST_DATA_DIR`, and report `doctor --json` as `ready`
- Added CLI regression coverage for running `cmd_setup()` in two different projects with project-scoped `.mcp.json` creation
- Confirmed project A remains ready after project B is set up in the same home

**Verification:**

- Real Claude CLI temp-home probe:
  - `HOME=/tmp/... claude mcp add smartassist -s project -- /bin/echo hello`
  - observed `File modified: <project>/.mcp.json`
- `python3 -m py_compile tests/test_cli.py`
- `.venv/bin/pytest tests/test_cli.py tests/test_qa_workflow.py -q` → `15 passed`
- `bash scripts/qa_pipx_smoke.sh` → PASS
- `.venv/bin/pytest -q` → `388 passed`

**Remaining:**

- External-machine / external-user validation of the packaged install flow
- Gate satisfaction / temporary evidence flow on top of the V1 gate engine
- PyPI publish + verification against the published package

### [2026-03-24] Session 7 — Prevention-rule promotion and boundary packs implemented

**Completed:**

- Added `smartassist/boundary_packs.py` to promote repeated actionable negative feedback into structured prevention boundaries
- Added `boundary_pack.json` generation with weak-category context, promoted prevention rules, and recent mistakes to avoid
- Rewired `SessionStart` to inject the boundary pack instead of only raw weak-category feedback snippets
- Rewired `SessionEnd` to refresh the boundary pack after session logging and before vectorization
- Preserved manual `prevention_rules.json` gate rules while appending promoted boundaries into the same structured file
- Added automatic boundary-pack rebuild when the stored pack is missing, stale, or corrupt
- Added comprehensive regression coverage for promotion, pack formatting, session-start injection, and session-end refresh

**Verification:**

- `python3 -m py_compile smartassist/boundary_packs.py smartassist/hooks/session_start.py smartassist/hooks/session_end.py tests/test_boundary_packs.py`
- `.venv/bin/pytest tests/test_boundary_packs.py tests/test_gates.py tests/test_cli.py tests/test_doctor.py -q` → `42 passed`
- `.venv/bin/pytest -q` → `387 passed`

**Remaining:**

- Fresh-machine / external-machine validation of the Phase 2 install flow
- Gate satisfaction / temporary evidence flow on top of the V1 gate engine
- PyPI distribution and external user validation

### [2026-03-24] Session 6 — README and package-distribution path tightened

**Completed:**

- Updated `README.md` quickstart, verification, troubleshooting, hook matcher, and data-layout docs to reflect the Phase 2 project-scoped MCP model
- Added `scripts/qa_package_smoke.sh` to build an sdist/wheel offline via `setuptools.build_meta`, install the wheel into a clean temp venv without dependencies, and verify SmartAssist console entry points
- Added package smoke coverage to CI in `.github/workflows/pr-checks.yml` and `.github/workflows/qa.yml`
- Added QA workflow regression coverage for the new package smoke script
- Updated `pyproject.toml` license metadata to the modern SPDX string form and removed the deprecated license classifier

**Verification:**

- `./.venv/bin/python -m pytest` → `372 passed, 5 skipped`
- `UV_NO_SYNC=1 UV_CACHE_DIR=/tmp/uv-cache uv run pytest tests/test_qa_workflow.py` → `8 passed`
- `bash scripts/qa_package_smoke.sh` → PASS
- `python3 -m py_compile smartassist/cli.py smartassist/claude_config.py smartassist/tools/doctor.py`

**Notes:**

- In this sandbox, `uv run pytest` without `UV_NO_SYNC=1` now tries to rebuild the local package and fetch build requirements from PyPI, so the full-suite verification was run through the repo venv instead
- CI should still use `uv sync --extra dev` before `uv run`, so GitHub Actions remains on the standard path

### [2026-03-24] Session 5 — Phase 2 multi-project MCP registration implemented and verified

**Completed:**

- Replaced single-project user-scoped MCP registration with project-scoped registration in `.mcp.json`
- Updated `cmd_setup()` and `cmd_init()` so current-project setup writes or refreshes project-local MCP config instead of pinning `SMARTASSIST_DATA_DIR` globally
- Added cleanup for stale SmartAssist registrations in project-local `.mcp.json`, `~/.claude.json` user config, `~/.claude.json` project state, and legacy `~/.claude/mcp.json`
- Made shared MCP status detection context-aware so unrelated project-scoped entries no longer count as “installed” for the current repo
- Updated doctor and QA preflight messaging to treat project-local `.mcp.json` as the canonical Phase 2 registration path
- Hardened `scripts/qa_preflight.sh` to require a Python 3.10+ interpreter cleanly instead of crashing on older `python3`
- Added regression coverage for project-local config detection, CLI setup/init/uninstall lifecycle, doctor readiness, and QA preflight acceptance

**Verification:**

- `UV_CACHE_DIR=/tmp/uv-cache uv run pytest` → `371 passed, 5 skipped`
- `python3 -m py_compile smartassist/cli.py smartassist/claude_config.py smartassist/tools/doctor.py`
- Fresh temp-home smoke:
  - `uv run --project /Users/joeyrahme/Github/SmartAssist python -m smartassist.cli setup`
  - `uv run --project /Users/joeyrahme/Github/SmartAssist python -m smartassist.cli doctor --json` → `overall_status: ready`
  - `bash /Users/joeyrahme/Github/SmartAssist/scripts/qa_preflight.sh` → PASS
  - `bash scripts/qa_mcp_protocol.sh --timeout 2` → PASS
- Smoke inspection confirmed:
  - current project `.mcp.json` contains the SmartAssist stdio server
  - stale SmartAssist entries were removed from `~/.claude.json` and legacy `~/.claude/mcp.json`
  - unrelated `playwright` registrations were preserved

**Remaining:**

- Update README / quickstart so project-scoped registration is documented as the primary install path
- Validate the Phase 2 flow on another external machine or user account
- Publish to PyPI and verify `pipx install smartassist && smartassist setup` against the packaged distribution

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
