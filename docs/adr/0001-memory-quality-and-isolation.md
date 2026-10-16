# ADR 0001: Memory quality, retrieval correctness and project isolation

- **Status:** Accepted
- **Date:** 2026-10-14
- **Context owner:** @jnrahme

## Context

An audit of the hooks, store and retrieval path (October 2026) against a real
project store (177 lessons, 242 events) found that SmartAssist's core loop was
broken in ways the 495-test suite did not catch. Baseline measured with
`benchmarks/retrieval_eval.py` on 14 labeled prompts:

| Recall | Noise | Tokens/prompt | p50 latency |
|---|---|---|---|
| 45% | 0% | 130 | 108 ms (≈9 s on feedback prompts) |

Root causes, grouped by the loop stage they break:

**Retrieve / Apply**
1. Per-lesson Thompson state decays toward 0, not the prior, so idle lessons
   become coin flips; reranked scores below 0.10 are dropped. Relevant lessons
   are randomly excluded (`thompson_rerank.py`).
2. Lexical search has no stemming and ignores provenance, so CLAUDE.md seed
   bullets outrank real corrections.

**Classify / Learn**
3. Feedback detection treats any prompt beginning with `+1`/`-1` as feedback;
   lesson extraction accepts any text containing an action verb; unknown
   sentiment defaults to negative. Praise and narrative become lessons.
4. Feedback reinforcement targets a global "last injection" record shared by
   all sessions, and infers the new lesson's category by majority vote of
   whatever was injected last, not from the feedback itself.

**Store / Isolate**
5. The data directory is resolved from the process cwd. Events record no
   project or session, so cross-project writes cannot be detected or rejected.
6. The session-start boundary pack is built from the raw event log, so retired
   and narrative events are re-injected forever.
7. The commit hook records every non-`[TICKET-XXX]` commit as a git failure,
   which produces a synthetic "weak git" signal and a contradicting lesson.
8. Re-seeding from CLAUDE.md truncates `feedback_log.jsonl`, which the sync layer
   then treats as the source of truth, erasing real corrections.

**Operate**
9. The prompt hook waits up to 10 s at exit for a background vectorizer.

## Decision

Keep the existing architecture (SQLite projection + JSON legacy files + hooks +
MCP server). The problems are correctness defects, not a wrong architecture; a
rewrite would risk the user's data for no measured gain. Fix in small,
independently reviewable PRs, each with regression tests and a benchmark run:

| PR | Scope | Primary metric |
|---|---|---|
| SA-053 | Banner, category decay, pack versioning, episode dedupe | tokens at session start |
| SA-054 | North Star, this ADR, benchmark harness | — |
| SA-055 | Per-lesson decay to prior, no stochastic drop of unscored lessons, stemming, provenance weighting | recall |
| SA-056 | Strict feedback classifier; per-session, overlap-scoped reinforcement; category from feedback text | false lessons |
| SA-057 | Detached background work; hook latency | p50/max latency |
| SA-058 | Project/session provenance on events; cwd from hook input; pack from active lessons | forbidden hits |
| SA-059 | Commit-hook heuristics not scored as failures; non-destructive seeding | data safety |

Later cycle: supersession/contradiction detection, row-level writes with
`BEGIN IMMEDIATE`, single lesson-creation path with similarity dedupe.

## Data policy

- No existing event or lesson is deleted by these changes. Seed lessons are
  down-weighted or excluded from injection, never removed.
- Any cleanup of existing data is a separate, user-approved operation that
  retires (reversible) rather than deletes.

## Alternatives considered

- **Replace lexical search with embeddings only.** Rejected for now: the
  embedding path costs seconds of model load per hook; lexical + stemming +
  provenance is deterministic and fast. Revisit if recall stays < 90%.
- **Drop Thompson sampling.** Rejected: the bandit is a sound way to learn
  from feedback once its prior is correct and unscored lessons are not
  sampled.
- **Global store with project tags.** Rejected: per-project directories already
  give isolation; the gap is attribution, not layout.

## Consequences

- Benchmark becomes part of the definition of done for retrieval changes.
- Some lessons that were previously injected (seeded CLAUDE.md bullets) will
  appear less often; this is intended.
