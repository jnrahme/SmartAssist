# ADR 0002: Opt-in semantic retrieval through a local embedding service

- **Status:** Accepted (opt-in; default off until evaluated on more projects)
- **Date:** 2026-10-15

## Context

After ADR 0001's fixes, lexical retrieval is precise (0% noise) but cannot
match paraphrased prompts. On a held-out set of 14 labeled prompts from a real
177-lesson store it injects the right lesson 9% of the time. A threshold sweep
showed no lexical setting above 36% held-out recall without 33–67% noise.

Offline comparison on the same sets (top-3, cosine threshold sweep):

| Retriever | Tuned recall / noise | Held-out recall / noise |
|---|---|---|
| Lexical (shipped) | 65% / 0% | 9% / 0% |
| bge-m3, semantic + lexical | 90% / 0% | 36% / 0% |
| **bge-small-en-v1.5, semantic + lexical (0.60)** | **100% / 25%** | **64% / 0%** |

Loading any embedding model costs 2–5 s (torch import + weights), so it
cannot run inside a per-prompt hook process.

## Decision

Add a small **local embedding service**:

- `python -m smartassist.semantic.server` keeps `bge-small-en-v1.5` warm and
  listens on a Unix socket in a user-only directory (`~/.cache/smartassist`,
  mode 0700). It exits after 30 minutes idle.
- The prompt hook sends the query plus `(id, text)` for active lessons; the
  service embeds lessons it has not seen (cached by text hash, in memory and
  on disk) and returns cosine similarities. The hook stays free of torch and
  numpy.
- The hook fuses scores: `cosine + 0.15 · min(lexical, 1)`, keeps candidates
  ≥ 0.60, and applies the existing 3-lesson / 1200-char budget.
- **Fail-safe:** if the service is down, slow (> 400 ms) or errors, the hook
  uses lexical retrieval for that prompt and starts the service in the
  background (single-instance lock). A prompt is never blocked on the model.
- **Opt-in:** enabled by `SMARTASSIST_SEMANTIC=1` or
  `{"semantic_retrieval": true}` in `<project>/.claude/smartassist/data/settings.json`.

## Alternatives considered

- **Load the model in the hook.** Rejected: 2–5 s per prompt.
- **Use the long-lived MCP server as the embedder.** Rejected: the MCP server
  is per-client and not always running when hooks fire.
- **ONNX / fastembed runtime.** Lower memory, but a new dependency; revisit
  if the torch footprint (~400 MB RSS) is a problem.
- **Reuse bge-m3 (already used for LanceDB).** Rejected on measured quality
  (36% vs 64% held-out) and 2× load time.

## Consequences

- One background process per user while active (~400 MB RSS, idles out).
- First use downloads `bge-small-en-v1.5` (~130 MB) to the Hugging Face cache.
- Lesson embeddings are recomputed only when lesson text changes.
- Default stays off until held-out results are confirmed on a second project.
