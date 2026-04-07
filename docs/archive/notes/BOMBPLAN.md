# BOMBPLAN — SmartAssist Next-Gen Features

> Technologies nobody is using for developer tools yet.
> Every item has a paper, a formula, and a build estimate.

---

## Tier 1: Deploy This Week (2-line to 1-day changes)

### 1. Prospect Theory Loss Aversion

**What:** Negative feedback counts 2.25x more than positive feedback.

**Why:** Kahneman & Tversky proved humans weight losses more than gains. When a user types `:(`, they're MORE certain the lesson was bad than when they type `:)` that it was good. One bad injection that wastes a user's time does more damage than one good injection that saves time.

**Formula:**
```
v(x) = x^0.88              if x >= 0 (gains)
v(x) = -2.25 * (-x)^0.88   if x < 0  (losses)
```

**Change:** `thompson_rerank.py` line 152 — multiply negative beta_delta by 2.25:
```python
LOSS_AVERSION = 2.25  # Tversky & Kahneman 1992

if sentiment == "negative":
    results.append((lesson_id, 0.0, weight * LOSS_AVERSION))
```

**Impact:** Bad lessons get demoted 2.25x faster. With sparse feedback (10-50 signals/day), this means a single `:( ` on a bad lesson has the same effect as ~2 positive signals on good lessons. The system learns to STOP showing bad lessons much faster.

**Source:** [Prospect Theory meta-analysis 2024](https://www.sciencedirect.com/science/article/pii/S0167487024000485)

**Build time:** 5 minutes. Literally 2 lines.

---

### 2. Normalized Compression Distance for Lesson Dedup

**What:** Detect semantically duplicate lessons even when worded completely differently, using information theory instead of embeddings.

**Why:** Current dedup uses text hashing or embedding cosine similarity. Neither catches:
- "Always run pytest before committing" vs "Execute the test suite as a pre-commit check"
- Same information content, different words. Hash: different. Embeddings: maybe 0.7.

**Formula:** Approximate Kolmogorov complexity via compression:
```
NCD(x, y) = [C(xy) - min(C(x), C(y))] / max(C(x), C(y))
```
Where C(x) = compressed size of string x. NCD ∈ [0, 1]. Below 0.3 = informationally redundant.

**Implementation:**
```python
import zlib

def ncd(x: str, y: str) -> float:
    xb, yb = x.encode(), y.encode()
    cx, cy = len(zlib.compress(xb)), len(zlib.compress(yb))
    cxy = len(zlib.compress(xb + yb))
    return (cxy - min(cx, cy)) / max(cx, cy)
```

**Impact:** For 300 lessons, pairwise check = 44,850 pairs × 0.1ms = 4.5 seconds. Run at `smartassist maintenance`. Flags redundant lessons that hash-based and embedding-based dedup miss entirely.

**Source:** [CVPR 2024 — Complexity-Constrained Similarity outperforms zero-shot embedding methods](https://openaccess.thecvf.com/content/CVPR2024/papers/Achille_Interpretable_Measures_of_Conceptual_Similarity_by_Complexity-Constrained_Descriptive_Auto-Encoding_CVPR_2024_paper.pdf)

**Build time:** 1 hour.

---

## Tier 2: Deploy This Month (1-7 days each)

### 3. Spaced Repetition via FSRS Forgetting Curves

**What:** Lessons that haven't been reinforced recently decay — and the system proactively re-injects them before they're forgotten. Like Anki for AI agents.

**Why:** Current Thompson decay is a flat 30-day half-life. FSRS (Free Spaced Repetition Scheduler) uses a real forgetting curve that accounts for lesson difficulty, number of successful recalls, and time since last review. A lesson seen and confirmed 5 times has a LONGER stability than one seen once. A lesson that was hard to learn (got negative feedback before positive) has SHORTER stability.

**Formula (FSRS v5):**
```
Retrievability:    R(t, S) = (1 + 19/81 * t/S)^(-0.5)
Initial stability: S_0(G) = w[G-1]    (G = feedback grade 1-4)
Stability after recall success:
  S'_r = S * (1 + e^w8 * (11-D) * S^(-w9) * (e^((1-R)*w10) - 1))
Stability after failure:
  S'_f = w11 * D^(-w12) * ((S+1)^w13 - 1) * e^(w14*(1-R))
```

**How it works:**
- Each lesson gets a Stability (S) and Difficulty (D) value
- When injected and feedback is positive: S increases (the lesson was "remembered" — the system correctly surfaced it)
- When injected and feedback is negative: S decreases (wrong lesson for this context)
- When NOT injected for a while: Retrievability R decays following the power-law forgetting curve
- Lessons with low R get PRIORITY for re-injection — they're "about to be forgotten"

**Impact:** Instead of flat decay, the system adapts to each lesson's individual learning curve. Well-established lessons (many successful injections) maintain relevance for months. Fragile lessons (few or mixed signals) need frequent re-injection. This is provably optimal for scheduling what to show when.

**Source:** [FSRS — 21 trainable parameters, open-source](https://github.com/open-spaced-repetition/free-spaced-repetition-scheduler), [Duolingo Half-Life Regression (ACL 2016)](https://github.com/duolingo/halflife-regression)

**Build time:** 3-5 days. New `lesson_memory.py` module with FSRS scoring, integrated into `thompson_rerank.py` as an additional factor.

---

### 4. Bayesian Surprise for Feedback Weighting

**What:** When the system expects a lesson to be helpful but gets negative feedback, that SURPRISE amplifies the learning signal. Expected outcomes teach less; surprising outcomes teach more.

**Formula:**
```
Surprise = KL(Posterior || Prior)
         = KL(Beta(α, β+1) || Beta(α, β))    [for unexpected negative]
         = KL(Beta(α+1, β) || Beta(α, β))    [for unexpected positive]

KL(Beta(a',b') || Beta(a,b)) = 
  ln(B(a,b)/B(a',b')) + (a'-a)*ψ(a') + (b'-b)*ψ(b') - (a'+b'-a-b)*ψ(a'+b')
```
Where ψ is the digamma function and B is the beta function.

**How it works:**
- Lesson has Thompson state Beta(20, 2) — strong belief it's good (91% mean)
- User gives `:(` — this is SURPRISING
- Surprise = KL(Beta(20, 3) || Beta(20, 2)) = 0.043 nats
- Compare: lesson with Beta(2, 2) gets `:(` — Surprise = 0.125 nats
- The uncertain lesson's negative feedback is 3x more surprising!
- MULTIPLY the Thompson update by the surprise value

**Impact:** The system learns faster from unexpected outcomes. A proven lesson getting its FIRST negative feedback triggers a much larger investigation than a mediocre lesson getting yet another negative. This prevents the system from becoming overconfident in established lessons.

**Source:** [Bayesian Surprise — Itti & Baldi, USC iLab](https://www.emergentmind.com/topics/bayesian-surprise), [Curiosity-Driven Exploration via Latent Bayesian Surprise (AAAI 2022)](https://arxiv.org/abs/2104.07495)

**Build time:** 2-3 days. Add surprise computation to `attribute_feedback()`, multiply update deltas.

---

### 5. Developer Flow State Detection

**What:** Detect whether the developer is in "flow" (productive) or "struggling" (needs help) from conversation signals, and adjust injection aggressiveness automatically.

**Why:** A [field study with 229 AI interventions](https://arxiv.org/abs/2601.10253) found that mid-task interruptions were dismissed 62% of the time. But boundary-triggered interventions (post-commit, session start) achieved 52% engagement. Another study found AI tools make experienced developers [19% slower](https://www.augmentcode.com/guides/why-ai-coding-tools-make-experienced-developers-19-slower-and-how-to-fix-it) due to cognitive load from context-switching.

**Signals (all available in the existing hooks):**
```
inter_prompt_interval < 30s          → flow (rapid work)
inter_prompt_interval > 300s         → struggling or context-switched
prompt_length increasing over session → struggling (providing more context)
negative_feedback_density > 2 in 5min → struggling
correction_patterns ("no", "wrong")   → struggling
```

**Flow score (0-1):**
```
flow = sigmoid(
    w1 * log(inter_prompt_interval) +
    w2 * prompt_length_delta +
    w3 * recent_negative_count +
    w4 * recent_correction_count
)
```

**Behavior:**
- Flow score > 0.7: inject max 2 lessons, only high-confidence ones
- Flow score 0.3-0.7: normal injection (top 5)
- Flow score < 0.3: aggressive injection — add more lessons, surface boundary pack warnings, increase lesson diversity

**Impact:** The system stops interrupting productive developers and doubles down on helping struggling ones. No OS-level access needed — all signals come from the existing `UserPromptSubmit` hook.

**Source:** [Developer Field Study 2025](https://arxiv.org/abs/2601.10253), [Keystroke Fatigue Detection 2025 — 91% accuracy](https://www.aijfr.com/papers/2025/5/1370.pdf)

**Build time:** 3-5 days. New flow detector in `prompt_inject.py`, session-state persisted score.

---

### 6. "What Would Have Helped" — Retroactive Retrieval Analysis

**What:** After a session with problems, retroactively check which lessons SHOULD have been injected but weren't.

**Why:** Every time a user struggles and the system didn't help, that's a missed opportunity AND a training signal. The system has the lessons — it just didn't surface them because the keywords didn't match.

**How it works:**
1. At session end, collect all prompts that received negative feedback or were followed by corrections
2. For each, run full retrieval (keyword + semantic) against the complete lesson corpus
3. If relevant lessons exist that were NOT injected during the session, flag them as "missed opportunities"
4. Report: "Lesson #42 was relevant to 3 prompts but never injected. Missing synonyms: [component, style]"
5. Auto-suggest: add missing keywords to the lesson text or to the synonym map

**Impact:** This creates a self-healing retrieval system. Gaps in keyword coverage are automatically identified and can be auto-fixed. Over time, the retrieval net gets tighter — fewer lessons slip through.

**Source:** [Conceptual Counterfactual Explanations (CCE)](https://arxiv.org/abs/2106.12723) — systematically explains model mistakes in terms of human-understandable concepts

**Build time:** 5-7 days. New `session_end` analysis pass, report generation, optional auto-fix for synonym gaps.

---

### 7. Conversation-Aware Retrieval (Topic Buffer)

**What:** Match retrieval against the trajectory of the entire conversation, not just the current prompt.

**Why:** [IBM's MTRAG benchmark](https://arxiv.org/abs/2501.03468) found that even state-of-the-art RAG systems struggle on later turns in multi-turn conversations. [CID-GraphRAG](https://arxiv.org/abs/2506.19385) showed 58% improvement using intent transition graphs that capture conversational trajectories.

**How it works:**
- Maintain a sliding window of the last 5-10 prompt topics in session state
- Union their tokens (weighted by recency) for retrieval
- A single prompt about "header styling" is ambiguous. But if the previous 3 prompts were about "React components", "theme configuration", and "CSS modules" — the intent is clear

**Formula:**
```
expanded_query = union(
    tokens(prompt_t) * 1.0,
    tokens(prompt_{t-1}) * 0.7,
    tokens(prompt_{t-2}) * 0.5,
    tokens(prompt_{t-3}) * 0.3,
)
```

**Impact:** Retrieval precision increases dramatically on later prompts in a session. The system understands conversational context, not just individual prompts.

**Source:** [CID-GraphRAG — 58% improvement](https://arxiv.org/abs/2506.19385), [MTRAG benchmark](https://github.com/IBM/mt-rag-benchmark)

**Build time:** 2-3 days. Modify `prompt_inject.py` to maintain topic buffer in session state.

---

## Tier 3: Deploy Next Quarter (2-6 weeks each)

### 8. Gittins Index — Provably Optimal Lesson Selection

**What:** Replace Thompson Sampling with the Gittins Index — the ONLY provably optimal algorithm for multi-armed bandits with discounted rewards.

**Why:** Thompson Sampling is a heuristic — it works well but provides no optimality guarantee. The Gittins Index is mathematically proven to minimize regret in the discounted setting. For SmartAssist with 300 lessons, Gittins converges to the optimal injection set FASTER because it never over- or under-explores.

**Formula:**
```
G(α, β) = sup_{τ>0} E[Σ_{t=0}^{τ-1} γ^t R(Z(t))] / E[Σ_{t=0}^{τ-1} γ^t]

For Beta-Bernoulli:
  V(α, β, λ) = max(0, α/(α+β) - λ + γ[α/(α+β) · V(α+1,β,λ) + β/(α+β) · V(α,β+1,λ)])
  G(α, β) = λ* where V(α, β, λ*) = 0
```

Pre-compute a lookup table for all (α, β) pairs in practical range. Then replace `random.betavariate()` with `gittins_table[α][β]`.

**Impact:** Faster convergence to optimal lesson set. Formal regret bound instead of empirical hope.

**Source:** [Practical Gittins Index Calculation — Niño-Mora 2019](https://arxiv.org/abs/1909.05075)

**Build time:** 2-3 weeks. Dynamic programming for lookup table + integration.

---

### 9. Knowledge Graph with Personalized PageRank

**What:** Represent lessons as a graph. When one lesson is relevant, its neighbors get a boost via Personalized PageRank.

**Why:** [HippoRAG (NeurIPS 2024)](https://github.com/osu-nlp-group/hipporag) used Personalized PageRank over a knowledge graph for retrieval and outperformed state-of-the-art by 20% on multi-hop QA while being 10-20x cheaper.

**Formula:**
```
PPR(i; q) = (1-d) · e_q + d · Σ_{j∈B_i} PPR(j; q) / L(j)
```
Where d=0.85, e_q is a query-seeded distribution over nodes.

**Graph edges come from:**
- Co-injection patterns (lessons frequently shown together that both get positive feedback)
- Conceptual overlap (embedding similarity > 0.7)
- Feedback chains (negative feedback on lesson A leads to creation of lesson B)

For 300 nodes, PageRank converges in <1ms via NetworkX.

**Impact:** When a query matches "testing", related lessons about "mocking", "fixtures", and "CI setup" get a graph-traversal boost — even if they share no keywords with the query.

**Source:** [HippoRAG — NeurIPS 2024](https://github.com/osu-nlp-group/hipporag)

**Build time:** 2-3 weeks. Graph construction + NetworkX PageRank + integration.

---

### 10. Automated Lesson Synthesis + Genealogy

**What:** When 3+ similar lessons exist, automatically merge them into a higher-level principle. Track the full genealogy: created → corrected → merged → refined.

**Why:** [Graphusion (ACM 2025)](https://arxiv.org/abs/2410.17600) showed 9.2% accuracy improvement on knowledge completion through entity merging + conflict resolution + novel inference. [Mastra's Reflector agent](https://mastra.ai/research/observational-memory) achieves 5-40x compression by combining related observations.

**How it works:**
1. At `smartassist maintenance`, compute pairwise similarity of all lessons using existing embeddings + NCD
2. When 3+ lessons have similarity > 0.85, flag as merge candidates
3. Use the LLM (via `create_lesson` MCP tool) to draft a synthesized principle
4. Store the synthesis with links to originals (genealogy)
5. Mark originals as `superseded_by` the synthesis

**Genealogy schema:**
```json
{
  "id": "L042",
  "history": [
    {"event": "created", "timestamp": "...", "source": "feedback", "context": "..."},
    {"event": "boosted", "timestamp": "...", "delta": 0.3},
    {"event": "merged_from", "timestamp": "...", "sources": ["L039", "L041"]},
    {"event": "synthesized", "timestamp": "...", "parent": "L042", "child": "L058"}
  ]
}
```

**Impact:** The lesson corpus self-compresses over time. Instead of 5 similar lessons about "use theme colors", one precise principle emerges. Genealogy provides full audit trail of how knowledge evolved.

**Source:** [Graphusion (ACM 2025)](https://arxiv.org/abs/2410.17600), [Zep bi-temporal knowledge graph](https://arxiv.org/abs/2501.13956)

**Build time:** 3-4 weeks. Similarity detection + LLM synthesis + genealogy tracking.

---

## The Vision

After all 10 features, SmartAssist becomes:

**A self-improving knowledge system that:**
- Learns from every interaction (Thompson + Prospect Theory + Bayesian Surprise)
- Remembers like a human brain (FSRS forgetting curves + spaced repetition)
- Understands conversation context (topic buffer + flow detection)
- Heals its own retrieval gaps ("What Would Have Helped")
- Compresses knowledge into principles (automated synthesis)
- Navigates knowledge relationships (PageRank on lesson graphs)
- Makes provably optimal decisions (Gittins Index)
- Deduplicates at the information level (NCD)
- Respects developer flow state (fewer interruptions when productive)
- Tracks knowledge evolution (lesson genealogy)

No other developer tool does ANY of these. Most AI coding assistants are stateless — they don't learn, don't remember, and don't improve. SmartAssist would be the first AI developer tool with a genuine, mathematically grounded memory that gets smarter every day.

---

## Research Sources

### Papers
- Tversky & Kahneman 1992 — Prospect Theory value function (lambda = 2.25)
- FSRS v5/v6 — Free Spaced Repetition Scheduler (21 trainable parameters)
- Duolingo Half-Life Regression (ACL 2016) — p = 2^(-delta/h)
- Bayesian Surprise — Itti & Baldi (KL divergence between prior and posterior)
- HippoRAG (NeurIPS 2024) — Personalized PageRank for RAG retrieval
- Graphusion (ACM 2025) — Zero-shot KG construction with entity fusion
- CID-GraphRAG (2025) — 58% improvement via intent transition graphs
- MTRAG (ACL 2025) — Multi-turn RAG benchmark (IBM)
- Niño-Mora 2019 — Practical Gittins Index calculation
- SmartRAG (ICLR 2025) — RL for adaptive retrieval
- SePer (ICLR 2025) — Semantic Perplexity for information gain
- CDF-RAG (2025) — Causal Dynamic Feedback for RAG
- RAAT (ACL 2024) — Adaptive adversarial training for RAG robustness
- AutoRAG-HP (EMNLP 2024) — Hierarchical MAB for hyperparameter tuning
- Mastra Observational Memory (2026) — 95% on LongMemEval, 5-40x compression
- Zep/Graphiti (2025) — Bi-temporal knowledge graphs, 94.8% deep memory retrieval

### Field Studies
- Developer Interaction Patterns (2025) — 229 interventions, boundary-triggered = 52% engagement
- Longitudinal Developer Study (2025) — 800 developers, 24 months, AI changes behavior unconsciously
- AI Tools Slow Experienced Developers 19% — cognitive load from context-switching

### Implementations
- [FSRS GitHub](https://github.com/open-spaced-repetition/free-spaced-repetition-scheduler)
- [HippoRAG GitHub](https://github.com/osu-nlp-group/hipporag)
- [SmartRAG GitHub](https://github.com/gaojingsheng/SmartRAG)
- [CDF-RAG GitHub](https://github.com/elakhatibi/CDF-RAG)
- [MTRAG GitHub](https://github.com/IBM/mt-rag-benchmark)
- [Mem0 GitHub](https://github.com/mem0ai/mem0)
- [pyncd GitHub](https://github.com/alephmelo/pyncd)
- [BoTorch](https://botorch.readthedocs.io/) (Gaussian Process bandits)
- [NetworkX](https://networkx.org/) (PageRank)
