# Ultimate SmartAssist Roadmap

Date: 2026-04-02
Status: Draft
Owner: SmartAssist

## Working Assumptions

- Keep the core local-first and open-source.
- Optimize for Claude Code first. Do not let cross-editor ambition derail the core loop.
- Treat this as context engineering, retrieval, and enforcement, not model-weight training.
- Preserve SmartAssist's current advantage: tight coding workflow integration.

## Research Summary

### SmartAssist today

SmartAssist already has the right base architecture:

- project-scoped storage
- prompt-time lesson injection
- MCP tools for lesson management
- per-lesson reinforcement
- startup boundary packs
- pre-action gates
- vector retrieval for deeper search

The main weakness is not missing the category. The weakness is that the system is split across several good subsystems that have not yet been unified into one opinionated product surface.

### What ICM gets right

- A clear dual-memory model: short-lived episodic memory plus permanent structured knowledge
- Strong memory hygiene: dedup, consolidation hints, health audits, decay rules
- Better packaging and broader client coverage
- More explicit extraction strategy, including session-boundary capture

### What ThumbGate gets right

- Very clear positioning: memory plus enforcement, not fake "RLHF"
- Strong prevention loop: repeated failure becomes a rule, then a gate
- Better product framing around reliability and guardrails
- Stronger evidence, eval, and operator-facing language

## Recommendation

Build SmartAssist into a Claude-native Memory + Prevention OS.

Do not copy ICM wholesale.
Do not copy ThumbGate wholesale.

Instead, combine:

- SmartAssist's project-aware coding memory
- ICM's memory hygiene and dual-lane model
- ThumbGate's prevention-first enforcement and evidence-first product framing

## Approaches Considered

### Option A: Memory-first

Add deeper long-term memory first: concepts, graphs, better semantic recall, richer extraction.

Pros:

- strongest long-term differentiation
- improves recall quality and cross-session continuity

Cons:

- slower visible user impact
- does not solve "same mistake happened again" fast enough

### Option B: Gate-first

Double down on prevention rules, satisfaction loops, and hard enforcement.

Pros:

- immediate reliability improvement
- easiest user-visible value

Cons:

- risks becoming only a guardrail tool
- underuses SmartAssist's existing retrieval and lesson corpus

### Option C: Recommended Hybrid

Ship a three-lane architecture:

1. fast lane: cheap prompt-time lessons
2. deep lane: structured memory and semantic retrieval
3. prevention lane: rules, gates, and satisfaction evidence

This is the best fit for the current codebase.

## Target Architecture

### 1. Fast lane

Keep the existing lightweight hook path, but make it more deliberate:

- search only promoted, high-signal lessons
- inject compact, sanitized, ranked context
- explain why each lesson was selected
- track whether injected lessons were later reinforced, ignored, or contradicted

### 2. Deep lane

Add a second memory surface above raw lessons:

- episodic lane: concrete corrections, failures, recent decisions
- principle lane: promoted reusable rules distilled from repeated events
- graph-lite lane: relations such as `supersedes`, `depends_on`, `alternative_to`, `caused_by`

Do not start with a full knowledge-graph product. Start with relation-aware lesson objects and only expand if the usage proves it is needed.

### 3. Prevention lane

Make the gate engine a first-class subsystem:

- every promoted prevention rule has provenance
- every gate has a source lesson or rule
- every blocked action can be satisfied, bypassed, or escalated with evidence
- gate outcomes feed back into lesson quality, not only gate counters

### 4. Evidence lane

SmartAssist needs stronger proof, not more adjectives:

- benchmark harnesses
- before/after evals
- promotion precision metrics
- gate fire rate and false-positive rate
- retrieval precision on seeded scenarios

## Phased Backlog

### Phase 0: Product truth and operator clarity

- Replace loose "RLHF" framing in user-facing docs with accurate language: feedback learning, retrieval, memory, and prevention.
- Add a concise system model to the README: fast lane, deep lane, prevention lane.
- Add `smartassist doctor --explain` to show exactly what is wired, what is missing, and which project is active.
- Add `list_lessons`, `list_rules`, and `explain_gate` MCP tools so Claude can inspect the system instead of guessing.

### Phase 1: Memory hygiene and lifecycle

- Add lesson lifecycle states: draft, active, promoted, suppressed, retired, superseded.
- Add auto-consolidation hints when a category accumulates too many overlapping lessons.
- Add dedup across curated lessons before vectorization, not only inside vector search.
- Add principle promotion: repeated compatible lessons become a reusable rule object.
- Add relation metadata between lessons and rules.

### Phase 2: Better extraction

- Expand commit capture into a general action-review capture pipeline.
- Distill session-end summaries into structured principles and unresolved weaknesses.
- Add compaction-aware extraction hooks where the runtime allows it.
- Add post-tool outcome capture for failed commands, reverted edits, and repeated retries.

### Phase 3: Prevention and satisfaction loop

- Add gate satisfaction records: what evidence resolved the warning or block.
- Add bypass with reason codes and audit trail.
- Add rule confidence bands based on sample size and recency.
- Add "next best corrective action" to every blocked or warned event.
- Feed satisfied gates back into reliability scores so the system learns not only from mistakes, but from successful recoveries.

### Phase 4: Retrieval unification

- Unify hook-time search and MCP semantic search behind the same ranking contract.
- Add retrieval health audits: stale lessons, overlapping lessons, empty categories, weak promotion quality.
- Add hybrid recall that returns both concrete episodes and promoted principles.
- Add compact recall payloads for prompt injection and rich recall payloads for tool use.

### Phase 5: Evidence and benchmarks

- Create seeded eval sets for styling, testing, git safety, env safety, and refactor hygiene.
- Measure:
  - lesson hit rate
  - reinforcement rate
  - gate prevention rate
  - false-positive gate rate
  - reduction in repeated mistakes across sessions
- Add reproducible QA commands and CI checks around these evals.

### Phase 6: Packaging and rollout

- Keep Claude Code first until the core loop is stable.
- After the core loop is stable, add optional install profiles:
  - `essential`: hooks + memory + gates
  - `full`: essential + dashboard + evals + monitor
- Only then consider adapters for other MCP clients.

## Concrete First 12 Tasks

1. Add a product-truth pass across `README.md`, CLI copy, and help text.
2. Add MCP tools: `list_lessons`, `list_rules`, `explain_gate`.
3. Add lesson lifecycle fields and relation metadata.
4. Add principle promotion from repeated compatible lessons.
5. Add consolidation and overlap audit tooling.
6. Add gate satisfaction records and bypass reasons.
7. Add "corrective action" output on gate block or warn.
8. Unify hook retrieval and MCP retrieval ranking inputs and outputs.
9. Add retrieval health and corpus hygiene reporting to `doctor`.
10. Add seeded eval harnesses with before/after scoring.
11. Add false-positive and repeat-mistake metrics to the dashboard.
12. Keep cross-client support out of the critical path until the above is stable.

## What To Build Next

If we want the highest leverage next implementation wave, start here:

1. Product-truth cleanup
2. Inspectability tools (`list_lessons`, `list_rules`, `explain_gate`)
3. Gate satisfaction loop
4. Principle promotion
5. Eval harness

That sequence gives the fastest jump in trust, reliability, and product coherence.

## Success Criteria

SmartAssist becomes "amazing" when all of the following are true:

- it remembers high-signal project lessons without spamming context
- it turns repeated failures into auditable prevention rules
- it blocks known-bad actions before execution
- it can explain where every rule and gate came from
- it proves improvement with evals instead of anecdotes
- it remains simple enough that one developer can install and trust it in under five minutes
