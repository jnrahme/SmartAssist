# ADR 0003: One retrieval path for the prompt hook and `rag_search`

- **Status:** Accepted
- **Date:** 2026-10-20

## Context

A fresh-install audit ran every hook and MCP tool from a clean HOME and
project. `rag_search` used a second retrieval stack (bge-m3 + LanceDB +
cross-encoder) that the prompt hook does not use:

| | Tuned recall | Held-out recall | First call |
|---|---|---|---|
| `rag_search` (bge-m3 + LanceDB + cross-encoder) | 55% | 27% | 12–35 s, 2–4 GB download on a new machine |
| Prompt hook ranking (ADR 0001/0002) | 90% | 64% | ~25 ms |

It also returned retired lessons and synthetic commit-hook events still
present in the vector table. The table lagged new lessons until the next
vectorization. Every relevance score showed as 100%, because unbounded
cross-encoder logits were clamped. `smartassist health` loaded the same models
(72 s) and failed on a fresh install.

## Decision

- `rag_search` ranks lessons with the prompt hook's pipeline: semantic
  through the shared embedding service when enabled, lexical otherwise.
  Past corrections come from the SQLite projection and use the hook's
  episode filter: at least two prompt terms, within half the top score,
  user evidence only, not stale. Lessons and events are deduplicated.
- The LanceDB cache becomes optional and is off by default. Enable it
  with `SMARTASSIST_VECTOR_CACHE=1` or `{"vector_cache": true}` in
  `data/settings.json`. When it is off, session end and lesson changes no
  longer rebuild it.
- `smartassist health` checks retrieval on the live store: sampled learned
  lessons must find themselves, and an unrelated query must find nothing.

## Consequences

- `rag_search` measures the same as the hook (90% / 64%) at ~25 ms. Neither
  path downloads a model unless semantic retrieval is enabled.
- Existing LanceDB directories are left in place, not deleted.
