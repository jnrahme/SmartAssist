# SmartAssist Memory

Date: 2026-04-03
Purpose: preserve the codebase invariants that were easy to flatten by mistake during the SQLite migration.

## Non-Negotiable Architecture

SmartAssist is not just:

- a SQLite store
- a prompt hook
- an MCP server

It is a layered learning system with **two Thompson loops** and **two memory types**.

## The Two Thompson Systems

### 1. Category-Level Thompson Reliability

File: [smartassist/thompson_sampling.py](smartassist/thompson_sampling.py)

This tracks reliability by category such as `testing`, `git`, and `code_edit`.

It must stay because it powers:

- `rag_dashboard`
- `rag_feedback`
- weak-category detection
- boundary packs
- session-start guidance
- reliability exports in `reliability_scores.json`

This is **not** the same thing as retrieval reranking.

### 2. Per-Lesson Thompson Reranking

File: [smartassist/thompson_rerank.py](smartassist/thompson_rerank.py)

This is the actual RLHF ranking loop for retrieval:

1. retrieve candidate lessons
2. rerank them with Thompson
3. inject them
4. record which lessons were injected
5. attribute later feedback back onto those injected lessons
6. improve future ranking

Do not remove these functions without replacing the behavior:

- `thompson_rerank()`
- `attribute_feedback()`
- `load_thompson_batch()`
- `update_thompson_batch()`
- `record_injection()`
- `migrate_from_lesson_scores()`

## The Two Memory Types

### 1. Semantic Memory

These are durable project rules and lessons.

Examples:

- “Always use semantic color tokens from the theme”
- “Write table-driven tests for validator edge cases”

### 2. Episodic Memory

These are past corrections and feedback events.

Examples:

- “Last time inline styles were corrected”
- “This test pattern failed in a prior attempt”

SmartAssist works best when both appear together:

- semantic memory tells the agent what to do
- episodic memory reminds the agent what went wrong before

This is the MemAlign-style dual-memory pattern.

## What Was Accidentally Flattened

During the SQLite migration, these important behaviors were flattened out:

- per-lesson Thompson reranking in the hook path
- per-lesson Thompson reranking in `rag_search`
- injection tracking via `lesson_thompson`
- feedback attribution from `:)` / `:(` back to the injected lessons
- dual-memory formatting in hook injection
- dual-memory labeling in MCP search output
- the overview “success story” that explained the RLHF loop and dual-memory behavior

If a future refactor simplifies retrieval down to “SQLite lexical search + category reliability only”, that is a regression.

## Mistakes That Happened Here

These are not abstract warnings. These are the actual mistakes that happened during this refactor and must be remembered next time:

- Working behavior was treated as “extra complexity” before it was fully mapped.
- The migration prioritized a cleaner architecture story over preserving the retrieval learning loop that was already working.
- The two Thompson systems were treated as if they were interchangeable, even though they solve different problems.
- Dual-memory behavior was treated like presentation formatting instead of product behavior.
- The overview/success-story explanation was simplified to match a flattened implementation instead of preserving the real system behavior.
- Completion/confidence was stated too early, before a full re-audit of the code paths that had been changed.
- Local user work and restored behavior were not treated as protected invariants at the beginning of the refactor.

### What That Means Operationally

The failure mode was:

1. simplify the architecture
2. assume the removed parts were incidental
3. update docs to fit the simplification
4. realize later that real product behavior was deleted

That pattern is now explicitly banned for this area of the codebase.

## Hard Guardrails For Future Refactors

If a change touches retrieval, memory, ranking, feedback attribution, or overview/docs for those systems, all of the following are required before the change is acceptable:

- Run `git status` and inspect local/staged user work first.
- Review these files before deleting or simplifying logic:
  - [smartassist/hooks/prompt_inject.py](smartassist/hooks/prompt_inject.py)
  - [smartassist/mcp_server.py](smartassist/mcp_server.py)
  - [smartassist/thompson_sampling.py](smartassist/thompson_sampling.py)
  - [smartassist/thompson_rerank.py](smartassist/thompson_rerank.py)
  - [smartassist-overview.html](smartassist-overview.html)
- Write down, explicitly, which behaviors are being preserved, changed, or removed.
- If a behavior is being removed, state it plainly and get explicit approval before shipping it.
- Do not collapse category-level Thompson and per-lesson Thompson into one mechanism unless the replacement preserves both jobs.
- Do not remove dual-memory output unless the replacement still preserves semantic memory plus episodic memory.
- Do not rewrite the overview doc to fit simplified code if the simplified code removed real behavior.
- Do not claim “done”, “fixed”, or “production-ready” without fresh verification evidence from the full commands in this file.

## Regression Checklist Before Shipping Memory Changes

A retrieval/memory refactor is not acceptable unless all of these are still true afterward:

- Hook retrieval still has a learning loop, not just lexical matching.
- `rag_search` still has a learning loop, not just static ranking.
- Feedback can still flow back onto previously injected lessons.
- Past corrections still remain distinguishable from project rules.
- The overview still explains the real learning loop, not a watered-down placeholder.
- The test suite still covers the preserved behavior.

## Stop Conditions

Stop immediately and do not continue the refactor without review if any of these happen:

- a change removes `thompson_rerank.py` behavior without a direct replacement
- a change removes injection tracking or feedback attribution
- a change makes `rag_search` unable to distinguish lessons from events
- a change removes the dual-memory hook format
- a change makes the docs claim less behavior than the system is supposed to provide
- a change is justified mainly with words like “simpler”, “cleaner”, or “more elegant” without a behavior-preservation matrix

## Required Change Record

For any future memory/retrieval refactor, the author should leave a short note in the PR, commit message, or planning doc covering:

- what behavior existed before
- what behavior exists after
- what was intentionally removed, if anything
- what test proves the important behavior still works

If that note does not exist, the review is incomplete.

## Files That Must Be Reviewed Before Refactoring Retrieval

- [smartassist/hooks/prompt_inject.py](smartassist/hooks/prompt_inject.py)
- [smartassist/mcp_server.py](smartassist/mcp_server.py)
- [smartassist/thompson_sampling.py](smartassist/thompson_sampling.py)
- [smartassist/thompson_rerank.py](smartassist/thompson_rerank.py)
- [smartassist/store.py](smartassist/store.py)
- [smartassist-overview.html](smartassist-overview.html)

## Current Truths

- SQLite is the canonical runtime store.
- LanceDB is still a derived semantic cache, not the source of truth.
- The hook path should stay fast, but “fast” does not mean “throw away the learning loop”.
- `rag_search` should remain better than plain lexical matching.
- The public overview doc should describe the real learning loop, not a simplified placeholder.

## Known Caveats

- Category-level Thompson and per-lesson Thompson solve different problems. Do not merge them casually.
- Current feedback attribution in the hook path uses recent injection IDs plus a timestamp; it does not yet persist per-result relevance scores in `last_injection`. That means the attribution is real, but only approximately relevance-weighted in the current implementation.
- Semantic search results must preserve `source_type`, or past corrections can be mislabeled as rules.
- Long-lived cached LanceDB table handles are risky because the vector cache is rebuilt by subprocesses.

## Required Verification After Any Retrieval / Memory Change

Run all of these:

```bash
python3 -m compileall -q smartassist tests
pytest -q
python3 -m smartassist.cli qa run --run-dir qa-artifacts/memory-check
bash scripts/qa_package_smoke.sh
bash scripts/qa_pipx_smoke.sh
```

If the change touches the live integration path, also run:

```bash
bash scripts/qa_preflight.sh
bash scripts/qa_mcp_protocol.sh --timeout 5
bash scripts/qa_claude_headless_smoke.sh --timeout 60
```

## Practical Rule

Before removing any “extra” retrieval logic, ask:

“Am I removing complexity, or am I deleting one of the learning loops?”

If the answer is “learning loop”, stop and verify first.
