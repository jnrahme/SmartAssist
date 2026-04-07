# SmartAssist Production Plan — Historical Reference

Date: 2026-04-02
Status: Historical planning reference
Scope: Planning snapshot for the SQLite migration and production-shape design work

> This document is reference material, not the current product contract.
> The current canonical docs are `README.md`, `smartassist-overview.html`, and `MEMORY.md`.
> Any future/production surfaces listed below should be treated as planning targets unless they also appear in the canonical docs and shipped code.

---

## Executive Decision

SmartAssist moves to a single canonical SQLite database with deterministic projections.

- `smartassist.db` is the only source of truth
- SQLite stores events, lessons, rules, scores, reliability, and gate history
- Prompt injection and MCP retrieval read from projections derived from the same source
- LanceDB is optional and disposable — rebuilt from canonical projection rows
- `curated_lessons.json` and `feedback_log.jsonl` become export-only, not runtime dependencies
- Claude and Codex share the same MCP core
- `claude-sa` keeps working as the launcher and monitor sidecar

---

## Review Tags For Recent Claude Updates

These tags exist so review feedback is visible in the canonical plan instead of scattered across side documents.

- `[ACCURATE]` = the update is materially correct and matches the code
- `[TIGHTEN]` = the code change is real, but the wording or product claim is too broad
- `[FIX]` = there is a real correctness or contract gap that must be addressed
- `[WHY]` = why the tag matters operationally

### [ACCURATE] Project-scoped MCP registration and config cleanup

- `smartassist/cli.py` now prefers project-scoped MCP registration through `.mcp.json` or `claude mcp add -s project`
- `smartassist/claude_config.py` now resolves project-local, project-state, user, and legacy registrations with current-project awareness
- targeted config and CLI regression coverage passed during review

`[WHY]` This is a real Phase 2 improvement and should be preserved as part of the final cutover.

### [ACCURATE] Expanded PreToolUse coverage and gate wiring

- `PreToolUse` now targets `Bash|Edit|Write`
- the hook evaluates gate decisions first, then preserves silent commit-capture behavior for allowed Bash events
- the gate engine and tests are internally consistent

`[WHY]` This is the correct near-term direction for rule enforcement and should feed into the final DB-backed gate model.

### [ACCURATE] Boundary-pack session wiring

- `SessionStart` now injects a rendered boundary pack
- `SessionEnd` refreshes the pack before vectorization
- the implementation and regression coverage are coherent

`[WHY]` This is a good UX bridge toward the final rule/boundary system, even though the storage model will change.

### [FIX] `smartassist doctor` is still a config-shape audit, not a full runtime-readiness check

- it verifies config files, hook registrations, MCP registration, and project data presence
- it does **not** prove that the SmartAssist hook commands are actually executable from the current `PATH`
- it can report `ready` even when the installed hook binaries are unavailable

`[WHY]` If `doctor` says `ready`, users will trust it. False-ready results are worse than honest warnings during onboarding and troubleshooting.

### [TIGHTEN] "Promoted prevention rules" is stronger than what shipped

- the boundary-pack code writes promoted items into `prevention_rules.json` under `promoted_boundaries`
- the gate engine only loads the `rules` array for actual PreToolUse enforcement
- the promoted items are currently structured guidance, not live executable gate rules

`[WHY]` Naming matters. If the product says "promoted prevention rules," the runtime should actually enforce them. Otherwise call them promoted boundaries until the gate contract is complete.

### [ACCURATE] The reviewed update set compiled and passed targeted regression coverage

- updated SmartAssist modules compiled cleanly
- targeted regression suite passed during review

`[WHY]` These updates are not hypothetical. They are real code changes, mostly solid, with a few important contract gaps called out above.

---

## Why the Current Model Must Be Replaced

The current system has three mutable runtime surfaces that serve different readers, are written to inconsistently, and can drift apart:

| Role | File | Written by | Read by |
|---|---|---|---|
| Active Corpus | `curated_lessons.json` | Paths 1, 2, 4 (NOT path 3) | Hook keyword search, full rebuild vectorizer |
| Event Journal | `feedback_log.jsonl` | All paths | Incremental vectorizer, analytics, Thompson, boundary packs |
| Semantic Index | LanceDB | Two vectorizers (different formats) | MCP `rag_search` |

Verified structural failures:
- Commit-extracted lessons write to feedback_log only — invisible to hook injection
- Incremental vectorizer writes `[category] text Context: ctx`, full rebuild writes `[category] text` — different embeddings for the same content
- Full rebuild uses `mode="overwrite"`, wiping anything the incremental added that isn't in curated
- MCP `rag_search` doesn't apply lesson boost or check blocked status — demoted lessons surface at full relevance
- Thompson `get_reliability()` returns stale pre-decay values on read paths

---

## Production Invariants

1. Every accepted lesson exists as one canonical row in the database
2. Every lesson and rule has provenance back to source events
3. Every runtime read derives from canonical state, not side files
4. Full rebuild and incremental update produce the same active searchable corpus
5. All ingestion paths pass through the same validator
6. Lesson boost and blocked status apply in ALL retrieval paths
7. Thompson reads apply time decay before returning scores
8. Hook prompt injection completes in <50ms with no ML model loading
9. Gate decisions are auditable and explainable
10. Codex and Claude use the same memory and rule base through MCP

---

## Architecture

### 1. Canonical Store

`<project>/.claude/smartassist/data/smartassist.db`

- Python built-in `sqlite3` — zero new dependencies
- WAL mode for concurrent reader/writer (hook reads while MCP writes)
- All writes through transactions — no partial state on crash
- Replaces `atomic_write_json()` and `locked_update_json()` patterns

### 2. Core Tables

**`events`** — append-only audit stream
- Event types: feedback, lesson_created, lesson_promoted, lesson_merged, lesson_retired, rule_created, rule_updated, gate_blocked, gate_warned, gate_bypassed, gate_satisfied, comparison_logged, seed_imported, commit_captured

**`lessons`** — canonical lesson entities
- `lesson_id`, `text`, `category_key`, `state` (candidate/active/superseded/retired), `origin` (hook/mcp/commit/seed/migration), `created_at`, `updated_at`, `superseded_by`, `retired_reason`, `content_hash`

**`lesson_sources`** — links lessons to source events (many-to-many provenance)

**`lesson_scores`** — per-lesson reinforcement
- `lesson_id`, `boost`, `ups`, `downs`, `blocked`, `retired`, `retired_reason`, `retired_at`

**`categories`** — canonical category registry with `category_key`, `display_name`, `status`

**`category_aliases`** — maps raw labels/synonyms onto canonical categories

**`category_reliability`** — Thompson/Beta-Bernoulli per category

**`rules`** — promoted prevention rules
- `rule_id`, `title`, `category_key`, `matcher_type`, `matcher_payload`, `severity` (warn/block), `confidence`, `state` (active/disabled/superseded), `corrective_action`, `created_at`, `updated_at`

**`rule_evidence`** — links rules to source lessons and events

**`gate_decisions`** — one row per gate evaluation
- `decision_id`, `rule_id`, `tool_name`, `tool_input_hash`, `decision` (allow/warn/block/satisfy/bypass), `reason`, `evidence`, `session_id`, `created_at`

**`search_documents`** — canonical retrieval projection
- `doc_id`, `source_type` (lesson/rule/boundary), `source_id`, `category_key`, `text`, `search_text`, `active`, `version`, `content_hash`, `updated_at`

**`projection_state`** — version counters and rebuild metadata

**`schema_migrations`** — migration bookkeeping

### 3. Category Model

- Store raw input label, normalize onto canonical `category_key`
- Compute reliability, boundary packs, gates, and ranking against canonical categories
- Domain extension through alias mapping, not uncontrolled free-form sprawl
- Engineering defaults ship as seed categories; projects can add their own

### 4. Retrieval Contract

One contract, two ranking modes:

**Hook injection** — reads `search_documents` via SQLite FTS5, only active rows, no ML model, must stay <50ms

**MCP `rag_search`** — reads same `search_documents`, may use semantic reranking via LanceDB, only active rows unless debug flag

Both paths:
- Filter inactive, blocked, superseded, and retired lessons before ranking
- Apply lesson boost and block state
- Carry structured identity fields (no parsing IDs from text blobs)
- Validate cached handles against projection version (no stale reads)

Performance notes:
- FTS5 on 300 rows: ~1-5ms (faster than current TF-IDF)
- Hook subprocess SQLite connection open: ~2-5ms overhead
- Current synonym expansion must be replicated as FTS5 OR queries
- Benchmark before/after migration to verify <50ms invariant

### 5. Ingestion Contract

All paths call one shared service:

```
ingest_feedback_event(...)  → record event
promote_lesson(...)         → create/update canonical lesson
promote_rule(...)           → create/update prevention rule
record_gate_decision(...)   → log gate outcome
```

Shared pipeline for every knowledge-producing path:
1. Normalize input
2. Classify category (via alias resolution)
3. Validate quality (same gates for all paths)
4. Record immutable event
5. Create or update canonical lesson (with content-hash dedup)
6. Update lesson score and category reliability
7. Update projections
8. Enqueue search sync
9. Emit audit record

### 6. Lesson Lifecycle

| State | Meaning | Searchable? |
|---|---|---|
| `candidate` | Extracted but not validated/promoted | No |
| `active` | Eligible for injection and retrieval | Yes |
| `superseded` | Replaced by merge or better version | No |
| `retired` | Demoted below threshold or obsolete | No |

- Commit-derived items enter as `candidate` unless they pass quality gates
- MCP and hook-created lessons enter as `active` (they pass gates at creation time)
- Merges create new `active` lesson, mark predecessors `superseded`
- Retirement preserves audit trail (never deletes)

### 7. Rules and Gates

Rules promote repeated failures into explicit prevention:
- Every rule has a matcher, severity, linked evidence, and corrective action
- Every block can be satisfied or bypassed with a reason

Gate outcomes: `allow`, `warn`, `block`, `satisfy`, `bypass`

### 8. Agent Adapters

Core: SQLite store, projection builder, optional LanceDB sync, MCP server, doctor/eval tooling

Claude adapter: SessionStart, UserPromptSubmit, PreToolUse, PostToolUse, SessionEnd, boundary packs, fast prompt-time retrieval, gate enforcement

Codex adapter: MCP registration, AGENTS.md guidance, same tools/memory/rules

Setup: `smartassist setup --agent claude|codex|both`

`claude-sa` compatibility: launcher keeps working, `.claude/smartassist/data` path preserved, `rag_live.log` remains as monitor export

---

## Workstream 0: Fix Current Bugs + Freeze Drift

Before the migration begins, fix the bugs that affect users today. These won't "go away by design" until workstreams 4-6 are complete.

### Bug Fixes (code-verified locations)

**0.1 Thompson read-path decay** — `thompson_sampling.py:177-182`
- `get_reliability()` returns stale alpha/beta without applying time decay
- Add `self._apply_decay(category)` + save before return
- Same fix for `get_weak_categories()` and `get_all_reliabilities()`

**0.2 rag_search ignores boost/blocked** — `mcp_server.py:317-439`
- Hook applies boost from `lesson_scores.json`; MCP vector search does not
- Interim fix: embed lesson ID in vectorized text `[L042] [category] lesson`
- After reranking, load lesson_scores, filter blocked, multiply relevance by boost

**0.3 doctor overclaims readiness** — `smartassist/tools/doctor.py`, `smartassist/runtime.py`
- `doctor` currently proves config shape, not command executability
- add explicit checks that SmartAssist hook commands are resolvable/runnable from the current environment
- if not, downgrade to `warn` or `fail` instead of reporting `ready`

**0.4 Commit hook visibility + validation gap** — `commit_hook.py:244-260`
- commit-derived lessons currently write to the feedback journal but do not join the active lesson corpus
- they bypass the shared lesson-creation validator used by hook and MCP paths
- rewrite extracted corrections as proper imperative lessons
- run them through the shared quality gates
- promote passing items into the active corpus, not just the event stream

**0.5 Path 1 new lessons skip Thompson** — `lesson_feedback.py:493-528`
- `create_lesson_from_feedback()` never calls `thompson.record_success/failure`
- Add Thompson update after successful `add_to_curated()`

**0.6 Boundary promotion contract mismatch** — `smartassist/boundary_packs.py`, `smartassist/gates.py`
- decide explicitly whether promoted boundaries should become live gate rules
- if yes, map them into executable rule rows with matcher payloads and severity
- if no, rename product/docs/code language to "boundaries" and stop implying enforcement

### Drift Prevention

**0.7 Remove bt-mobile-app seed lessons** — `seed_from_claudemd.py:338-525`
- 16 of 19 hardcoded lessons are project-specific (yarn, snapshot testing, Firebase, tsx)
- Replace with 8 generic engineering lessons (test coverage, commit quality, no secrets, error handling)
- Remove "detox" from `_CATEGORY_KEYWORDS`

**0.8 Clean up synonyms** — `prompt_inject.py:74-93`
- Remove `color → {theme, hex}` (design-system-specific)
- Remove `component → render` (React-specific)
- Keep all generic engineering synonyms

**0.9 Async vectorization everywhere** — `commit_hook.py:277`, `seed_from_claudemd.py:660`
- Replace `subprocess.run()` (blocking 1-4s) with `spawn_managed()` (async <10ms)

**0.10 Canonical categories with aliases, not free-form sprawl**
- keep a controlled canonical category registry
- allow raw input labels through alias resolution and normalization
- do not let product behavior fragment across arbitrary one-off category strings

**0.11 No new JSON coupling**
- Stop any new feature work from depending on runtime JSON reads
- Add baseline eval snapshots before migration begins

### Exit Criteria
- All bug fixes above are complete
- Seed is domain-neutral
- Synonyms and keywords are generic
- Vectorization is async on all paths
- `doctor` does not return false-ready on broken hook binaries
- Boundary terminology matches runtime behavior
- Categories normalize onto canonical keys
- Baseline evals exist

---

## Workstream 1: Freeze Schema and Invariants

- Define SQLite schema (tables above)
- Define lifecycle states and transitions
- Define rule model and gate contract
- Define projection contracts
- Define event taxonomy
- Document invariants as test assertions

Exit: schema frozen, migration script skeleton exists, invariants in tests

---

## Workstream 2: Build Canonical Store

- DB bootstrap and migrations
- Write transactions for all entity types
- Repository layer: create, update, merge, retire, query
- Projection builder (active_lessons, active_rules, boundary_pack, search_documents)

Exit: fresh project `smartassist setup` initializes DB cleanly, repository supports all flows

---

## Workstream 3: Import Current Data

Field mapping from legacy files:

```
curated_lessons.json → lessons (state="active")
  .id           → lesson_id
  .lesson       → text
  .category     → category_key
  .origin       → origin (if present, else "migration")
  .created_at   → created_at (if present, else file mtime)
  .content_hash → content_hash (md5 of normalized text)

feedback_log.jsonl → events
  .timestamp    → created_at
  .signal       → event_type ("correction"→"feedback", "thumbs_up"→"feedback")
  .category     → category
  .correction   → lesson text candidate (if not in curated → state="candidate")

lesson_scores.json → lesson_scores
  key           → lesson_id
  .boost/.ups/.downs/.blocked/.retired → direct mapping

reliability_scores.json → category_reliability
  key           → category
  .alpha/.beta/.last_updated → direct mapping

commit_captures.json → events (event_type="commit_captured")
```

Rules:
- JSON source files are NEVER modified or deleted during import
- Importer is idempotent
- Feedback corrections not in curated become `candidate` lessons
- Content-hash dedup prevents duplicates

Exit: imported corpus matches current guidance, candidates visible but not active, JSON untouched

---

## Workstream 4: Replace All Writers

Current writers to replace:

| Writer | Current location | Replacement |
|---|---|---|
| Hook lesson creation | `lesson_feedback.py:493` `create_lesson_from_feedback()` | `ingest_feedback_event()` → `promote_lesson()` |
| MCP create_lesson | `mcp_server.py:615` | `ingest_feedback_event()` → `promote_lesson()` |
| MCP boost/demote | `mcp_server.py:814-951` | `update_lesson_score()` |
| MCP merge | `mcp_server.py:955` | `merge_lessons()` (DB transaction) |
| Commit hook | `commit_hook.py:246` | `ingest_feedback_event()` → conditional `promote_lesson()` |
| CLAUDE.md seed | `seed_from_claudemd.py:569` | `seed_from_source()` (DB transaction) |
| rag_feedback | `mcp_server.py:550` | `ingest_feedback_event()` |

Transition strategy:
- During migration, DB becomes canonical immediately
- Any compatibility JSON outputs are exported from DB/projections, not independently dual-written by business logic
- JSON compatibility exports removed only after workstream 5 (readers) is complete
- Thompson updates centralized in ingestion pipeline

Exit: no writer appends to JSON directly, quality gates shared, origin recorded, Thompson centralized

---

## Workstream 5: Replace All Readers

- Hook injection reads `search_documents` projection via FTS5
- Boundary packs read projection + reliability tables
- MCP `rag_search` reads projection + optional LanceDB semantic index
- Gates read active rules from DB
- Dashboard/health read from DB

Exit: no production reader depends on curated_lessons.json or feedback_log.jsonl

---

## Workstream 6: Rebuild Search

- FTS5 projection indexing for hook path
- LanceDB sync over projection rows (keyed by doc_id + version, not log line count)
- Lesson boost + blocked applied in both retrieval paths
- Candidate/evidence items excluded from active search
- Search freshness: version-checked handles, no stale caching
- Benchmark hook path: must stay <50ms

Exit: full rebuild and incremental converge to identical state, both paths have parity on boost/blocked

---

## Workstream 7: Rules and Gates

- Rule promotion model (repeated failures → explicit rules)
- `explain_gate` — show which rule, evidence, corrective action
- Satisfy and bypass flows with audit trail
- Gate decisions logged with provenance

Exit: every block is explainable, every bypass is audited

---

## Workstream 8: Agent Adapters

- Claude adapter: preserve hook depth, gate enforcement, boundary packs
- Codex adapter: MCP-first setup, AGENTS.md guidance
- `claude-sa` compatibility: launcher, monitor, rag_live.log
- Shared: same DB, same tools, same rules

Exit: same project memory usable from both agents, claude-sa still works

---

## Workstream 9: Eval Harness and Cutover

- Retrieval parity eval (hook vs MCP see same active corpus)
- Gate false-positive eval
- Repeated-mistake regression eval
- Migration correctness eval (legacy data preserved)
- Fresh-install smoke: Claude, Codex, claude-sa
- Remove JSON compatibility shims
- Remove legacy vectorizer

Exit: all evals green, migration validated on real data, JSON no longer runtime

---

## MCP Surface (Production)

`rag_search`, `rag_dashboard`, `rag_feedback`, `create_lesson`, `compare_lesson`, `boost_lesson`, `demote_lesson`, `merge_lessons`, `list_lessons`, `list_rules`, `explain_gate`, `satisfy_gate`, `bypass_gate`, `memory_health`

## CLI Surface (Production)

`smartassist setup`, `smartassist uninstall`, `smartassist init`, `smartassist serve`, `smartassist health`, `smartassist migrate`, `smartassist rebuild-projections`, `smartassist rebuild-search`, `smartassist list-lessons`, `smartassist list-rules`, `smartassist explain-gate`, `smartassist eval`, `smartassist doctor`, `smartassist seed`, `smartassist vectorize`, `smartassist maintenance`, `smartassist analyze`, `smartassist dashboard`

---

## Risks

- **Migration data loss** — JSON files never modified/deleted during import; kept as read-only backups
- **Hook latency regression** — FTS5 must be benchmarked; keep JSON fallback until proven
- **Hybrid state** — writers move to DB before all readers switch; temporary compatibility exports can drift if they are not derived from projections
- **Commit lesson over-promotion** — candidate state prevents auto-injection of unvalidated observations
- **Embedding model cold start** — ~4s first `rag_search` call is inherent to sentence-transformers, not fixable by storage layer
- **LanceDB text format inconsistency** — existing LanceDB has mixed formats; migration does full rebuild
- **Gate overblocking** — satisfy/bypass UX required before gates can block in production

---

## Acceptance Criteria

The system is production-ready when:

- `smartassist.db` alone reproduces all active lessons, rules, and search documents
- Prompt injection and MCP retrieval see the same active knowledge
- Incremental and full rebuild converge to identical state
- Lesson boost/blocked applies in all paths
- Every block is explainable; every bypass is audited
- Claude and Codex share the same project intelligence
- Repeated mistakes become inspectable rules
- Seed content is domain-neutral
- `claude-sa` still works
- All eval suites are green

Until these conditions are true, the system is transitional.

---

## Research Provenance

Every bug, line number, and behavioral claim in this plan was verified by reading the actual source code — not inferred from documentation or assumed from naming conventions.

### How the research was conducted

Five parallel research agents each read full source files and traced execution paths:

1. **Vectorization architecture** — read `vectorize_learnings.py` (full), `cleanup_and_vectorize.py` (full), `mcp_server.py` (rag_search + _trigger_vectorization), `config.py` (EMBEDDING_MODEL, EMBEDDING_DIM). Found: two vectorizers write different text formats (`[cat] text Context: ctx` vs `[cat] text`), use different dedup (vector distance vs mode=overwrite), read different sources (feedback_log vs curated).

2. **Commit hook** — read `commit_hook.py` (full), `lesson_feedback.py` (_context_to_lesson, add_to_curated, quality gate constants). Found: 5 pattern families, corrections are raw observations not imperative lessons, they bypass the shared lesson validator, and they never call `add_to_curated()`, which makes them invisible to the fast hook path.

3. **rag_search boost gap** — read `mcp_server.py:317-439` (full rag_search), `prompt_inject.py:123-169` (search_lessons with boost). Found: hook loads lesson_scores and multiplies by boost (line 158-159), filters blocked (line 134-135). MCP rag_search never loads lesson_scores, never checks blocked. LanceDB documents don't store lesson IDs, making cross-reference impossible without text parsing.

4. **Seed portability** — read `seed_from_claudemd.py` (full). Found: 19 hardcoded lessons, 16 are bt-mobile-app-specific (yarn, snapshot testing, Firebase, tsx container patterns). `create_hardcoded_lessons()` fires when no CLAUDE.md found OR when CLAUDE.md has zero actionable bullets. Synonyms: 3 of 18 root terms are React/design-system-specific.

5. **Thompson + metrics** — read `thompson_sampling.py` (full), all 4 paths' Thompson interactions. Found: `get_reliability()` at line 177 does not call `_apply_decay()` before returning — returns stale values. Path 1's `create_lesson_from_feedback()` creates lesson but never calls `record_success/failure`. `compare_lesson` DOES validate sentiment (lines 764-767) — that older finding is already fixed and is not part of this plan.

### Verification commands any agent can run

```bash
# Verify Thompson decay bug (line 177 — no _apply_decay call before return)
grep -n "_apply_decay\|get_reliability" smartassist/thompson_sampling.py

# Verify rag_search never loads lesson_scores
grep -n "lesson_scores\|load_lesson_scores\|boost\|blocked" smartassist/mcp_server.py | grep -i "rag_search" 

# Verify commit hook never writes to curated
grep -n "add_to_curated\|curated_lessons" smartassist/hooks/commit_hook.py

# Verify two different text formats in vectorizers
grep -n "format_text_for_vector\|text = f" smartassist/hooks/vectorize_learnings.py smartassist/tools/cleanup_and_vectorize.py

# Verify hardcoded lesson count
grep -c "lesson.*:" smartassist/hooks/seed_from_claudemd.py | head -5

# Verify compare_lesson DOES validate sentiment
sed -n '764,767p' smartassist/mcp_server.py
```

These commands produce the evidence. No claims in this plan depend on memory, documentation, or inference — they come from reading the files.
