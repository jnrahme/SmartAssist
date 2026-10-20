# SmartAssist North Star

> **SmartAssist remembers the mistakes you and your agent made, learns verified
> lessons from your code and corrections, and puts the right one in front of the
> agent at the moment it matters — so the same mistake is never made twice.**

## What "working as intended" means

| Principle | Concretely |
|---|---|
| **Right lesson, right moment** | Retrieval is task-aware. A relevant, known lesson is injected when the prompt touches its topic; nothing is injected for unrelated prompts. |
| **Maximum useful intelligence per token** | Hard token budget per injection. No duplicates, no content the agent already has (e.g. CLAUDE.md). |
| **Only verified knowledge** | A lesson needs evidence: an explicit user correction, a confirmed fix, or user approval. Praise, chit-chat and speculation are never stored as lessons. |
| **The user is the authority** | Explicit user instructions outrank inferred preferences. Nothing is deleted or rewritten silently; supersession and retirement are recorded and reversible. |
| **Isolated by project** | Every event and lesson records the project and session it came from. Lessons never leak between repositories unless explicitly shared. |
| **Invisible when healthy** | Hooks are fast (p50 < 150 ms, no blocking background work), never crash the agent, and log failures instead of swallowing them. |

## The learning loop

```
Observe ─► Classify ─► Learn ─► Validate ─► Store ─► Retrieve ─► Apply ─► Verify
 hooks     is this a    extract   dedupe,     scoped,   budgeted,  agent     feedback
 capture   correction?  a rule    conflict,   versioned ranked     uses it   adjusts
                                  evidence                                   that lesson
```

Each stage has a single owner module and a test that pins its contract.

## Scorecard

Measured by `benchmarks/retrieval_eval.py` against a labeled case set.
Targets are the bar for calling the system "working as intended".

| Metric | Definition | Target |
|---|---|---|
| Recall | Expected lesson injected for a relevant prompt | ≥ 90% |
| Noise rate | Any injection for an unrelated prompt | ≤ 5% |
| Forbidden hits | Cross-project or retired lessons injected | 0 |
| Tokens / prompt | Mean injected context | ≤ 250 |
| Hook latency | p50 / max of the prompt hook | < 150 ms / < 1 s |
| Data safety | Existing events/lessons lost by any operation | 0 |

### Status (2026-10-19, bt-mobile-app store, semantic retrieval on)

| Metric | Before (2026-10-14) | Now | Target |
|---|---|---|---|
| Recall, tuned set | 38% | **90%** | ≥ 90% ✅ |
| Recall, held-out set | 12% | **64%** | ≥ 90% ❌ |
| Noise rate | 0% | **0%** | ≤ 5% ✅ |
| Forbidden hits | 0 | **0** | 0 ✅ |
| Tokens / prompt | ~120 (mostly irrelevant on held-out) | **~76–81** (relevant) | ≤ 250 ✅ |
| Hook latency p50 / max | 108 ms / 6.9 s | **~90 ms / 102 ms** | < 150 ms / < 1 s ✅ |
| Data lost | — | **0** (33 lessons retired, reversible) | 0 ✅ |

Held-out recall is the open gap: the misses are paraphrases with weak semantic
overlap (see ADR 0002). Next candidates: a larger embedding model, and lesson
text enriched with the context it was learned in.

The benchmark history and decisions live in [`docs/adr/`](adr/).
