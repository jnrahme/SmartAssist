---
name: smartassist-memory-architect
description: Use when changing SmartAssist memory, retrieval, ranking, feedback attribution, vectorization, or docs that explain those systems.
---

# SmartAssist Memory Architect

## Overview

Use this when working on the part of SmartAssist that decides what to remember, what to surface, and how that reaches the model.

Core rule: keep these layers separate in your head and in the code:

1. **Memory model** — semantic memory, episodic memory, working memory
2. **Learning loops** — category-level Thompson and per-lesson Thompson
3. **Storage** — canonical SQLite store plus the search projection
4. **Delivery surfaces** — prompt hook injection and MCP `rag_search`

If you flatten those into one vague “memory system,” you usually break either the product behavior, the docs, or both.

## When to Use

- Retrieval or reranking changes
- `prompt_inject.py`, `mcp_server.py`, `store.py`, `thompson_sampling.py`, or `thompson_rerank.py`
- Feedback attribution or lesson promotion logic
- SQLite / LanceDB / vectorization discussions
- Docs that explain how SmartAssist memory works

Do **not** use this for unrelated CLI polish, UI tweaks, or setup-only changes that do not touch memory behavior.

## The Mental Model

### 1. Memory model (MemAlign-style)

- **Semantic memory** = durable project rules
- **Episodic memory** = concrete past corrections and failures
- **Working memory** = the small prompt-time mix of both

MemAlign fits here. It explains the **shape of memory**, not the whole SmartAssist implementation.

### 2. Learning loops

- **Category-level Thompson** tracks reliability by domain (`testing`, `git`, `code_edit`, etc.)
- **Per-lesson Thompson** learns which specific lessons should rank higher

These loops answer **what to surface first**, not **what memory is**.

### 3. Storage

- **SQLite is canonical**
- `search_documents` is the canonical projection for retrieval
- **LanceDB is a derived cache**, not a second source of truth

### 4. Delivery surfaces

- **Hook path** injects fast lesson context on every prompt
- **MCP `rag_search` path** searches the broader canonical projection on demand

They can differ in scope and latency, but both must preserve the real memory behavior.

## Non-Negotiable Invariants

- Keep **two memory types**: semantic + episodic
- Keep **working-memory assembly** at prompt time
- Keep **two Thompson systems** separate
- Keep **SQLite as canonical**
- Treat **LanceDB as a cache only**
- Do not update docs to fit an oversimplified implementation

## Files to Read First

1. `MEMORY.md`
2. `smartassist-overview.html`
3. `smartassist/hooks/prompt_inject.py`
4. `smartassist/mcp_server.py`
5. `smartassist/thompson_sampling.py`
6. `smartassist/thompson_rerank.py`
7. `smartassist/store.py`
8. `tests/test_feedback_lesson.py`
9. `tests/test_prompt_inject_search.py`

## Working Checklist

Before changing anything, answer these:

1. Am I changing the **memory model**, the **ranking logic**, the **storage layer**, or the **delivery surface**?
2. Which user-visible behavior depends on that distinction?
3. Am I accidentally flattening two systems that solve different problems?
4. Do both the **hook path** and **MCP path** still make sense after this change?
5. Do the docs describe the real system, or a simplified fiction?

## Common Mistakes

- Treating category-level Thompson and per-lesson Thompson as interchangeable
- Treating LanceDB as the runtime source of truth
- Simplifying docs first and “fixing the implementation later”
- Preserving rules but dropping the past-correction path
- Preserving retrieval but breaking feedback attribution or injection tracking

## Verification

- Run targeted tests for both hook and MCP retrieval behavior
- Re-read changed docs after code changes
- Check that the explanation still distinguishes:
  - memory model
  - learning loops
  - storage
  - delivery
