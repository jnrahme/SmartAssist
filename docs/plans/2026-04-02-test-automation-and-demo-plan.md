# SmartAssist Test Automation And Live Demo Plan

Date: 2026-04-02
Status: Proposed execution plan
Owner: SmartAssist core

## Purpose

This plan covers two goals at the same time:

1. Prevent future regressions by turning SmartAssist behavior into enforceable automated contracts.
2. Generate a polished live showcase from the same runs so demos are evidence-backed, not staged.

The core decision is simple:

- Build one scenario system.
- Use it for CI enforcement.
- Use the same artifacts to power the live demo.

SmartAssist should not have one set of logic for tests and another set of logic for demos.

## Product Claims This Plan Must Protect

These are the claims that need automated proof:

- `smartassist.db` is the canonical runtime truth.
- Hook injection and MCP retrieval derive from the same logical knowledge base.
- Feedback can create active lessons when it passes quality gates.
- Commit-derived corrections can become active lessons and influence future prompts.
- Seeded conventions are active immediately after setup.
- Demotion, retirement, and blocking remove lessons from active retrieval where expected.
- Auto-retirement triggers when a lesson reaches zero boost and zero positive signals.
- Reliability and lesson-score changes remain consistent after mutations.
- Rebuild and cache refresh converge to the same active searchable corpus (including LanceDB vectorized cache).
- Feedback signals detected in user prompts trigger reinforcement of recent lessons.
- Session deduplication prevents the same lesson from being injected twice in one session.
- Gate enforcement blocks risky operations and accumulates statistics.
- Boundary packs refresh on session end and promote weak-category lessons to prevention rules.
- Lesson corpus respects the MAX_CURATED_LESSONS capacity (300).
- Merged lessons consolidate scores correctly and remove the source lessons.
- Setup works in isolated projects without global cross-project leakage.
- `claude-sa` still works as the launcher + monitor sidecar.

## Design Principles

- Canonical assertions must read SQLite first.
- Compatibility JSON and LanceDB are secondary evidence, not truth.
- Every scenario must emit machine-readable artifacts.
- Every scenario must be replayable locally.
- The live demo must be generated from recorded artifacts, not a handcrafted page.
- Deterministic tests should be the main gate. Live-model checks should be narrower and scheduled.

## Existing Assets To Reuse

The repo already has the right foundation:

- `smartassist/store.py` for canonical runtime state
- `smartassist/monitor.py` for local live terminal feedback
- `smartassist/tools/generate_dashboard.py` as the starting point for HTML output
- `scripts/qa_preflight.sh` for environment/config preflight
- `scripts/qa_mcp_protocol.sh` for MCP protocol checks
- `scripts/qa_claude_headless_smoke.sh` for real Claude smoke runs
- `scripts/qa_autodiagnose.sh` for staged execution and artifact capture
- `.github/workflows/pr-checks.yml` and `.github/workflows/qa.yml` for CI integration

Existing test files that already cover some of the ground this plan targets:

- `tests/test_gates.py` — force push, protected branch, lockfile, env edit gates
- `tests/test_boundary_packs.py` — promotion engine, assembly, session integration
- `tests/test_cleanup.py` — vectorization cleanup
- `tests/test_claude_sa.py` — CLI launcher
- `tests/test_claude_config.py` — MCP registration
- `tests/test_cli.py` — setup, health, doctor, analyze, dashboard subcommands
- `tests/test_config.py` — data directory resolution
- `tests/test_doctor.py` — doctor checks
- `tests/test_feedback_lesson.py` — some lesson feedback paths
- `tests/test_thompson_sampling.py` — reliability scoring
- `tests/test_seed_from_claudemd.py` — markdown parsing and fallback lessons
- `tests/test_prompt_inject_search.py` — prompt injection keyword search
- `tests/test_qa_workflow.py` — QA preflight and MCP protocol

New scenarios should extend coverage, not duplicate what these already verify.

Important cleanup note:

- `smartassist/tools/generate_dashboard.py` currently reads legacy compatibility surfaces and LanceDB-oriented stats.
- The new demo generator should read canonical SQLite state and scenario artifacts first.

## New Vs Existing

This plan creates net-new infrastructure. Nothing below exists yet:

- `smartassist/qa/` — new package (scenario runner, assertions, artifacts, demo generator)
- `smartassist qa` — new CLI subcommand group (the CLI currently has no `qa` command)
- `tests/test_runtime_contracts.py` — new test file for runtime contract assertions
- `tests/test_qa_runner.py` — new test file for the runner itself
- `tests/test_qa_demo.py` — new test file for demo generation

Existing QA shell scripts (`scripts/qa_*.sh`) and existing pytest files (`tests/test_*.py`) remain as-is.
The new scenario runner complements them — it does not replace them.

## Executive Decision

Build a new QA surface with three commands:

```bash
smartassist qa run
smartassist qa run --scenario feedback_creates_active_lesson
smartassist qa demo --run-dir qa-artifacts/<run-id>
```

Optional convenience:

```bash
smartassist qa run --watch
```

`--watch` should run scenarios and keep a local demo page open while artifacts update.

## Architecture

### 1. Scenario Runner

Create a scenario runner that executes named cases in temporary project sandboxes.

Responsibilities:

- create isolated temp project
- initialize SmartAssist in that project
- seed fixtures
- run scripted actions
- capture before/after state
- execute assertions
- emit structured artifacts
- return a single pass/fail result

Each scenario should define:

- scenario name
- purpose
- setup steps
- actions
- assertions
- required environment
- whether Claude live access is required

### 2. Artifact Bundle

Each run should write a bundle like:

```text
qa-artifacts/<run-id>/
  manifest.json
  summary.json
  scenarios/
    feedback_creates_active_lesson/
      scenario.json
      steps.jsonl
      assertions.json
      before_state.json
      after_state.json
      sqlite_snapshot.json
      export_snapshot.json
      rag_live.log
      usage_log.jsonl
      claude_output.json
      claude_stream.jsonl
      screenshots/
      traces/
  demo/
    index.html
    assets/
```

Required properties:

- stable JSON schema
- diff-friendly text files
- one folder per scenario
- enough evidence to explain a failure without rerunning immediately

### 3. Assertion Layers

The runner should evaluate assertions in four layers:

#### Layer A: canonical state

Read `smartassist.db` and assert:

- lesson rows
- lesson state (active, retired, blocked)
- lesson scores (boost, ups, downs)
- category reliability (Thompson alpha/beta)
- search projection rows
- projection activeness
- feedback event rows
- session state (injection tracking)
- lesson count within MAX_CURATED_LESSONS cap
- gate statistics (`gate_stats.json`)

#### Layer B: derived surfaces

Check compatibility exports and cache surfaces:

- expected JSON exports exist
- exported state matches canonical state where required
- LanceDB/cache rebuild reflects canonical projection
- LanceDB document count matches active lesson count in SQLite
- incremental vectorization matches full rebuild output
- prevention rules reflect boundary pack promotions

#### Layer C: runtime behavior

Run the real code paths and assert:

- prompt hook returns expected `additionalContext`
- prompt hook detects feedback signals (`:)`, `:(`, `thumbs_up`, etc.) and triggers reinforcement
- prompt hook deduplicates lessons within a session
- prompt hook applies per-lesson boost/block scoring to retrieval results
- MCP tool output shape and content are correct
- MCP retrieval returns the same lessons as hook injection for the same query
- `create_lesson` enforces quality gates (min 30 chars, action verb, no generic starts)
- `demote_lesson` triggers auto-retirement at zero boost + zero ups
- `merge_lessons` combines scores and triggers vectorization
- gate hooks block risky operations and record statistics
- boundary pack loads at SessionStart and refreshes at SessionEnd
- CLI setup and doctor behavior are correct
- `claude-sa` still launches and tails live output correctly

#### Layer D: user-visible claims

Assert what the product tells the user:

- `doctor` should not say `ready` when commands are not executable
- dashboard/demo labels should match actual runtime behavior
- no feature should be described as enforced unless it is actually enforced

### 4. Demo Generator

The demo generator should render the scenario bundle into a static site.

The page should show:

- overall run status
- scenario cards with pass/fail
- step timeline per scenario
- prompt/action input
- hook/tool events
- state changes
- final assertions
- downloadable artifacts

The showcase should answer:

- what happened
- what SmartAssist learned
- what was injected later
- what retrieval saw
- why the scenario passed or failed

### 5. Watch Mode

Local watch mode should:

- run the selected scenarios
- stream scenario progress to terminal
- update the HTML demo in place
- optionally open the page in the default browser

This should feel like a product rehearsal tool, not just CI plumbing.

## Scenario Catalog

The first milestone should include these scenarios.

### Core deterministic scenarios

- `feedback_creates_active_lesson`
- `feedback_next_prompt_injects_lesson`
- `compare_lesson_logs_without_storage`
- `commit_correction_promotes_active_lesson`
- `seed_creates_active_conventions`
- `demote_retires_lesson_everywhere`
- `projection_rebuild_converges`
- `two_project_setup_isolated`
- `claude_sa_launcher_smoke`
- `doctor_rejects_false_ready`

### Hook and MCP consistency scenarios

- `hook_mcp_retrieval_consistency`
- `feedback_signal_triggers_reinforcement`
- `session_dedup_prevents_repeat_injection`
- `auto_retirement_on_zero_boost_zero_ups`
- `merge_lessons_consolidates_correctly`
- `gate_statistics_accumulate`
- `boundary_pack_refreshes_on_session_end`
- `lesson_capacity_enforced_at_300`
- `boost_rejected_on_retired_lesson`
- `category_inference_from_text`
- `vectorization_incremental_matches_full_rebuild`

### Narrow live-Claude scenarios

- `claude_can_see_smartassist_tools`
- `claude_feedback_prompt_triggers_expected_tool_usage`
- `claude_session_uses_injected_context`

Live-Claude scenarios should be fewer, slower, and reserved for nightly, manual, or release runs.

## Scenario Contract Details

### feedback_creates_active_lesson

Actions:

- initialize temp project
- send feedback-like input through the prompt path

Assertions:

- feedback event recorded
- active lesson created
- lesson score initialized
- search projection updated
- next prompt retrieves/injects the lesson

### compare_lesson_logs_without_storage

Assertions:

- comparison artifact exists
- no active lesson row created from comparison-only path
- comparison remains reviewable without polluting the active corpus

### commit_correction_promotes_active_lesson

Note: `commit_hook.py` is a PreToolUse hook that enforces gates before risky Bash/Edit/Write actions.
Commit-capture is a side effect — it fires when the hook detects a Bash tool call containing a git commit.
This scenario must trigger the hook via a simulated Bash tool event, not a raw `git commit`.

Assertions:

- commit hook records the event
- accepted correction becomes an active lesson
- future prompt injection can see it

### demote_retires_lesson_everywhere

Assertions:

- lesson marked inactive/retired
- hook path no longer injects it
- MCP retrieval no longer returns it in normal mode
- compatibility exports reflect the state

### projection_rebuild_converges

Assertions:

- canonical projection before rebuild equals projection after rebuild
- cache rebuild does not lose accepted searchable content

### doctor_rejects_false_ready

Assertions:

- when hook commands or CLI commands are absent from `PATH`, doctor fails or warns
- config shape alone is not enough for `ready`
- hook completeness is verified (all 5 expected hooks must be registered)
- data directory structure is validated

### hook_mcp_retrieval_consistency

This is the single most important end-to-end scenario. It proves that hook injection and MCP retrieval
draw from the same knowledge base — the core architectural claim of SmartAssist.

Actions:

- initialize temp project
- create a lesson via `create_lesson` MCP tool
- wait for vectorization to complete
- send a prompt containing keywords matching the lesson through the hook path
- call `rag_search` MCP tool with the same keywords

Assertions:

- the hook injects the lesson into `additionalContext`
- `rag_search` returns the same lesson
- both paths reference the same underlying SQLite row
- removing the lesson via `demote_lesson` removes it from both paths

### feedback_signal_triggers_reinforcement

Actions:

- initialize temp project with seeded lessons
- send a prompt that triggers lesson injection
- send a follow-up prompt containing `:)` (positive feedback signal)

Assertions:

- `prompt_inject.py` detects the feedback signal
- `reinforce_recent_lessons()` is called with sentiment="positive"
- the most recently injected lesson's boost score increases
- Thompson Sampling reliability for the lesson's category is updated

### session_dedup_prevents_repeat_injection

Actions:

- initialize temp project with seeded lessons
- send a first prompt that triggers injection of lesson X
- send a second prompt with the same keywords

Assertions:

- lesson X is injected on the first prompt
- lesson X is NOT re-injected on the second prompt within the same session
- session state file tracks the injected lesson ID
- a new session (different session ID) would inject lesson X again

### auto_retirement_on_zero_boost_zero_ups

Actions:

- initialize temp project
- create a lesson with initial boost
- demote the lesson repeatedly until boost reaches 0.0
- verify the lesson has zero positive signals (ups=0)

Assertions:

- lesson is marked inactive/retired in SQLite
- lesson is removed from search projections
- hook path no longer injects it
- MCP retrieval no longer returns it
- vectorized cache no longer contains it after refresh

### merge_lessons_consolidates_correctly

Actions:

- initialize temp project
- create two overlapping lessons (same category, similar text)
- call `merge_lessons` MCP tool

Assertions:

- merged lesson retains the higher boost score (max preserved)
- positive signal counts are combined (ups summed)
- source lessons are removed from active corpus
- merged lesson is searchable via both original keyword sets
- full vectorization is triggered after merge

### gate_statistics_accumulate

Actions:

- initialize temp project
- trigger a gate violation (e.g. simulate force-push via PreToolUse hook)
- trigger a gate pass (e.g. normal Bash tool usage)

Assertions:

- `gate_stats.json` records the blocked event
- `gate_stats.json` records the passed event
- counts increment correctly on repeated events
- `rag_live.log` contains the gate event entry

### boundary_pack_refreshes_on_session_end

Actions:

- initialize temp project with a weak category (reliability < 0.70)
- record repeated negative feedback for a lesson in that category
- trigger SessionEnd hook

Assertions:

- boundary pack is rebuilt
- weak-category lesson is promoted to a prevention rule
- `prevention_rules.json` contains the new rule
- next SessionStart hook injects the boundary pack with the new rule
- recent mistakes list is deduplicated

### lesson_capacity_enforced_at_300

Actions:

- initialize temp project
- insert 300 lessons into the corpus
- attempt to add lesson 301 via `create_lesson` or feedback path

Assertions:

- the 301st lesson is rejected or the oldest/lowest-scored lesson is evicted
- active corpus count does not exceed MAX_CURATED_LESSONS (300)
- SQLite row count reflects the cap
- search projections reflect the cap

### boost_rejected_on_retired_lesson

Actions:

- initialize temp project
- create and then retire a lesson (demote to zero)
- attempt to boost the retired lesson

Assertions:

- boost operation is rejected or has no effect
- lesson remains retired
- no zombie lessons re-enter the active corpus

### category_inference_from_text

Actions:

- submit lesson texts containing keywords for each of the 8 categories:
  CODE_EDIT, GIT, TESTING, PR_REVIEW, SEARCH, ARCHITECTURE, SECURITY, DEBUGGING

Assertions:

- each lesson is assigned the correct category
- fallback to "general" when no keywords match
- majority-vote from reinforced lessons overrides keyword inference when available

### vectorization_incremental_matches_full_rebuild

Actions:

- initialize temp project with seeded lessons
- run incremental vectorization
- snapshot the LanceDB document set
- run full rebuild (`full_rebuild=True`)
- snapshot the LanceDB document set again

Assertions:

- both snapshots contain the same document IDs
- both snapshots contain the same lesson texts
- retired lessons are absent from both snapshots
- document count matches active lesson count in SQLite

## CI Plan

### PR gate

Run on every pull request:

- compile checks
- fast pytest
- runtime contract suite
- package smoke
- pipx smoke

This lane must be deterministic and should not depend on a live Claude session.

### Main branch gate

Run on merge to `main`:

- everything from PR gate
- scenario runner full deterministic suite
- demo artifact generation
- upload `qa-artifacts`

### Nightly or manual gate

Run on schedule or `workflow_dispatch`:

- temp-home setup
- MCP protocol probe
- live Claude smoke scenarios
- demo artifact generation
- optional Pages deploy of latest passing demo

## Demo Publishing Model

There should be two demo modes:

### Local demo

Developer command:

```bash
smartassist qa run --watch
```

Output:

- terminal progress
- browser page with live-updating scenario cards
- full run bundle in `qa-artifacts`

### Published demo

GitHub Actions should upload the run bundle as an artifact and optionally publish the rendered demo site for:

- latest passing `main`
- tagged releases
- manually selected nightly runs

The published page should be static HTML so it is cheap, easy to share, and easy to archive.

## Proposed File Layout

Implementation should roughly land here:

```text
smartassist/qa/
  __init__.py
  runner.py
  scenarios.py
  assertions.py
  artifacts.py
  demo.py
  fixtures.py

tests/
  test_runtime_contracts.py
  test_qa_runner.py
  test_qa_demo.py

scripts/
  qa_runtime_contracts.sh
```

Possible CLI additions:

- `smartassist qa run`
- `smartassist qa list-scenarios`
- `smartassist qa demo`
- `smartassist qa clean`

## Dashboard And Demo Cleanup Requirements

Before calling the demo production-ready:

- replace legacy-LanceDB-first dashboard reads with SQLite-first reads
- make demo rendering consume scenario artifact JSON, not ad hoc file scraping
- keep `rag_live.log` as a local event feed, not the canonical data source
- ensure all displayed counts and labels are backed by scenario assertions

## Phased Execution

### Phase 1: contract foundation

- add scenario runner skeleton
- define artifact schema
- implement core deterministic scenarios (target: 8-10, prioritizing `hook_mcp_retrieval_consistency`, `feedback_creates_active_lesson`, `auto_retirement_on_zero_boost_zero_ups`, `session_dedup_prevents_repeat_injection`, `projection_rebuild_converges`, `gate_statistics_accumulate`, `doctor_rejects_false_ready`, `seed_creates_active_conventions`)
- add runtime contract pytest suite

### Phase 2: live demo

- build static demo generator
- add local `--watch` mode
- add CI artifact upload

### Phase 3: Claude integration showcase

- add bounded real-Claude scenarios
- publish latest passing demo artifact

### Phase 4: polish

- improve visuals
- add downloadable traces/logs
- tighten failure explanations

## Acceptance Criteria

This plan is done when:

- every major SmartAssist product claim maps to an automated scenario or contract test
- hook injection and MCP retrieval are proven consistent by at least one end-to-end scenario
- a failing runtime contract blocks CI
- the demo page is generated from real run artifacts
- the demo page can explain at least 5 end-to-end SmartAssist behaviors
- local watch mode works for developers
- published demo artifacts can be shared without manual editing
- deterministic runs remain the primary enforcement layer
- live-Claude runs provide extra confidence without becoming the only proof
- auto-retirement, session dedup, and capacity enforcement are each covered by at least one scenario
- gate statistics and boundary pack refresh have automated proof
- no new scenario duplicates coverage already provided by existing `tests/test_*.py` files

## Anti-Goals

Do not do these:

- do not create a fake demo page with manually written examples
- do not make LanceDB or compatibility JSON the truth source for assertions
- do not require live Claude access for every PR
- do not create separate logic for "demo mode" and "test mode"
- do not hide failures behind soft warnings when the product contract is actually broken
- do not duplicate assertions already covered by existing `tests/test_*.py` unit tests — scenarios should test end-to-end flows, not re-test individual functions

## First Build Order

If implementation starts immediately, do it in this order:

1. `tests/test_runtime_contracts.py`
2. `smartassist/qa/runner.py`
3. `hook_mcp_retrieval_consistency` scenario (proves the #1 architectural claim first)
4. remaining core deterministic scenarios (feedback, retirement, dedup, gates, capacity, merge, boundary packs, vectorization convergence)
5. artifact bundle writer
6. `smartassist qa run` CLI subcommand (new — does not exist yet)
7. demo renderer
8. CI artifact upload
9. bounded live-Claude scenarios

## Final Standard

SmartAssist should be able to say:

- here is the behavior we promise
- here are the scenarios that prove it
- here is the artifact bundle from the latest run
- here is the live page that shows those results

That is the bar for "we are not hallucinating."
