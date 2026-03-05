<p align="center">
  <img src="https://img.shields.io/badge/python-3.10+-blue?style=flat-square&logo=python&logoColor=white" alt="Python 3.10+">
  <img src="https://img.shields.io/badge/tests-59%20passing-brightgreen?style=flat-square" alt="59 tests passing">
  <img src="https://img.shields.io/badge/license-BSL--1.1-blue?style=flat-square" alt="BSL 1.1 License">
  <img src="https://img.shields.io/badge/version-1.0.0-blue?style=flat-square" alt="Version 1.0.0">
</p>

<h1 align="center">SmartAssist</h1>

<p align="center">
  <strong>A portable RLHF + RAG + MCP learning system for Claude Code</strong><br>
  <em>An AI assistant that remembers every mistake and never repeats them — on any codebase.</em>
</p>

<p align="center">
  <a href="https://smartassist-ai.netlify.app/">Interactive Documentation</a> ·
  <a href="https://smartassist-ai.netlify.app/dashboard.html">Live Dashboard</a> ·
  <a href="https://github.com/jnrahme/SmartAssist/wiki">Wiki</a>
</p>

---

## What Is SmartAssist?

SmartAssist adds a **persistent learning layer** to Claude Code. It combines three technologies into a single pip-installable package that works on any project:

- **RLHF** — Learns from your explicit feedback (thumbs up/down, corrections). Tracks reliability per category with Thompson Sampling (Beta-Bernoulli with 30-day exponential decay).
- **RAG** — Hybrid semantic search (1024-dim BAAI/bge-m3 vectors + BM25 keyword matching) with cross-encoder reranking over curated lessons stored in LanceDB.
- **MCP Server** — Exposes 3 tools (`rag_search`, `rag_dashboard`, `rag_feedback`) via the Model Context Protocol. Claude decides when to search — zero overhead on simple prompts.

### System Architecture

```mermaid
graph TB
    subgraph INPUT["📥 FEEDBACK SOURCES"]
        direction LR
        A["🗣️ Manual Feedback<br/><i>thumbs up/down, corrections</i>"]
        B["🔍 Commit Hook<br/><i>scans git diffs for anti-patterns</i>"]
        C["📝 PR Harvester<br/><i>GitHub review comments</i>"]
    end

    subgraph ENGINE["⚙️ SMARTASSIST ENGINE"]
        direction TB
        D["📊 Thompson Sampling<br/><i>Beta-Bernoulli · 30-day decay</i>"]
        E["🧹 Cleanup Pipeline<br/><i>20+ filters · sanitize · dedup</i>"]
        F["🧠 BAAI/bge-m3 Embedder<br/><i>1024-dim vectors</i>"]
    end

    subgraph STORAGE["💾 PER-PROJECT DATA — .claude/smartassist/"]
        direction LR
        G[("📋 feedback_log.jsonl<br/><i>1,991 events</i>")]
        H[("📈 reliability_scores.json<br/><i>6 categories</i>")]
        I[("🗄️ LanceDB<br/><i>100 curated lessons</i>")]
    end

    subgraph DELIVERY["🚀 KNOWLEDGE DELIVERY"]
        direction LR
        J["🌅 Session Start<br/><i>inject weak categories < 70%</i>"]
        K["🔎 MCP: rag_search<br/><i>hybrid search + cross-encoder</i>"]
        L["📊 MCP: rag_dashboard<br/><i>reliability scores & stats</i>"]
        M["👍 MCP: rag_feedback<br/><i>real-time quality signals</i>"]
    end

    N["🤖 Claude Code<br/><i>Better responses, fewer mistakes</i>"]

    A --> G
    B --> G
    C --> G
    G --> D --> H
    G --> E --> F --> I
    H --> J
    I --> K
    I --> L
    H --> M

    J --> N
    K --> N
    L --> N
    M --> N
    N -.->|"feedback loop"| A

    style INPUT fill:#1a1a2e,stroke:#38bdf8,stroke-width:2px,color:#e6edf3
    style ENGINE fill:#1a1a2e,stroke:#a78bfa,stroke-width:2px,color:#e6edf3
    style STORAGE fill:#1a1a2e,stroke:#fb923c,stroke-width:2px,color:#e6edf3
    style DELIVERY fill:#1a1a2e,stroke:#34d399,stroke-width:2px,color:#e6edf3
    style N fill:#1a1a2e,stroke:#f472b6,stroke-width:3px,color:#e6edf3
```

### Key Innovation: Portable by Design

Install once globally. Run on any codebase. Per-project data stays isolated:

```
Code  → pipx install (one global install)
Data  → <any-project>/.claude/smartassist/  (auto-detected from cwd)
```

No virtual environments. No hardcoded paths. No per-project MCP configuration.

---

## Quick Start

```bash
# 1. Install globally (pick one)
pipx install git+https://github.com/jnrahme/SmartAssist.git
# or: pip install git+https://github.com/jnrahme/SmartAssist.git

# 2. Initialize in any project
cd ~/your-project
smartassist init

# 3. Done. MCP server and hooks auto-detect the data directory.
```

### One-Time Setup

Add to `~/.claude/mcp.json`:

```json
{
  "mcpServers": {
    "smartassist": {
      "command": "smartassist",
      "args": ["serve"]
    }
  }
}
```

Add hooks to `~/.claude/settings.json`:

```json
{
  "hooks": {
    "SessionStart": [{"hooks": [{"type": "command", "command": "python3 -m smartassist.hooks.session_start"}]}],
    "SessionEnd": [{"hooks": [{"type": "command", "command": "python3 -m smartassist.hooks.session_end"}]}],
    "PreToolUse": [{"matcher": "Bash", "hooks": [{"type": "command", "command": "python3 -m smartassist.hooks.commit_hook"}]}],
    "PostToolUse": [{"matcher": "mcp__smartassist__rag_search", "hooks": [{"type": "command", "command": "python3 -m smartassist.hooks.show_lessons"}]}],
    "UserPromptSubmit": [{"hooks": [{"type": "command", "command": "python3 -m smartassist.hooks.prompt_inject"}]}]
  }
}
```

---

## How It Works

```mermaid
graph LR
    A["🗣️ You Give<br/>Feedback"] -->|"thumbs up/down<br/>corrections"| B["📊 Score &<br/>Store"]
    B -->|"Thompson Sampling<br/>cleanup pipeline"| C["🧠 Knowledge<br/>Base"]
    C -->|"hybrid search<br/>cross-encoder"| D["🔎 Claude<br/>Searches"]
    D -->|"relevant lessons<br/>with relevance %"| E["✅ Better<br/>Responses"]
    E -.->|"continuous improvement"| A

    style A fill:#1a1a2e,stroke:#38bdf8,stroke-width:2px,color:#e6edf3
    style B fill:#1a1a2e,stroke:#fb923c,stroke-width:2px,color:#e6edf3
    style C fill:#1a1a2e,stroke:#f87171,stroke-width:2px,color:#e6edf3
    style D fill:#1a1a2e,stroke:#34d399,stroke-width:2px,color:#e6edf3
    style E fill:#1a1a2e,stroke:#a78bfa,stroke-width:2px,color:#e6edf3
```

| Step | What Happens |
|------|-------------|
| **Capture** | Feedback (thumbs up/down) or auto-detected anti-patterns in commits |
| **Score** | Thompson Sampling updates reliability (0-100%) per category |
| **Clean** | 20+ filter functions remove junk, sanitize to imperative lessons |
| **Vectorize** | 1024-dim BAAI/bge-m3 embeddings stored in LanceDB |
| **Inject** | Weak categories (<70%) get lessons injected at session start |
| **Search** | Claude calls `rag_search` → hybrid search + cross-encoder rerank |
| **Log** | Every call logged with decision funnel, returned lessons, latency |

---

## Architecture

### Search Pipeline

```mermaid
graph LR
    A["🔤 User Query"] --> B["✨ Query Enhancement<br/><i>+ correction prefix</i>"]
    B --> C["🧠 BAAI/bge-m3<br/><i>1024-dim embedding</i>"]
    C --> D["🔎 Hybrid Search<br/><i>Vector + BM25</i>"]
    D --> E{"📏 Distance<br/>≤ 1.30?"}
    E -->|"Yes"| F["🎯 Cross-Encoder<br/><i>ms-marco-MiniLM</i>"]
    E -->|"No"| G["❌ Filtered Out"]
    F --> H["📋 Return Lessons<br/><i>with relevance %</i>"]

    style A fill:#1a1a2e,stroke:#38bdf8,stroke-width:2px,color:#e6edf3
    style B fill:#1a1a2e,stroke:#22d3ee,stroke-width:2px,color:#e6edf3
    style C fill:#1a1a2e,stroke:#a78bfa,stroke-width:2px,color:#e6edf3
    style D fill:#1a1a2e,stroke:#a78bfa,stroke-width:2px,color:#e6edf3
    style E fill:#1a1a2e,stroke:#fbbf24,stroke-width:2px,color:#e6edf3
    style F fill:#1a1a2e,stroke:#f472b6,stroke-width:2px,color:#e6edf3
    style G fill:#1a1a2e,stroke:#f87171,stroke-width:2px,color:#e6edf3
    style H fill:#1a1a2e,stroke:#34d399,stroke-width:2px,color:#e6edf3
```

### Session Lifecycle

```mermaid
sequenceDiagram
    participant User as 👤 Developer
    participant Hook as 🪝 Hooks
    participant Claude as 🤖 Claude Code
    participant MCP as 🔎 SmartAssist MCP
    participant DB as 🗄️ LanceDB

    Note over Hook: ⚡ Session Start (63ms)
    Hook->>Hook: Load reliability scores
    Hook->>Claude: Inject lessons for weak categories (<70%)

    User->>Claude: Ask about styling components
    Note over Claude: Relates to project knowledge...
    Claude->>MCP: rag_search("style components")
    MCP->>MCP: Enhance query + embed (1024-dim)
    MCP->>DB: Hybrid search (vector + BM25)
    DB-->>MCP: 20 candidates
    MCP->>MCP: Distance filter → Cross-encoder rerank
    MCP-->>Claude: 3 lessons with relevance %
    Note over Hook: 👁️ PostToolUse: show lessons
    Claude-->>User: Response using project-specific knowledge

    User->>Claude: Commit changes
    Note over Hook: 🔍 PreToolUse: scan diff
    Hook->>Hook: Detect anti-patterns → record feedback

    Note over Hook: 📊 Session End: save analytics
```

### Five Lifecycle Hooks

| Hook | Trigger | Purpose |
|------|---------|---------|
| `session_start` | SessionStart | Inject lessons for weak categories (<70%) |
| `session_end` | SessionEnd | Save session analytics |
| `commit_hook` | PreToolUse (Bash) | Scan git diffs for anti-patterns |
| `show_lessons` | PostToolUse (rag_search) | Display search results to user |
| `prompt_inject` | UserPromptSubmit | Context injection |

### Data Path Resolution

Every module imports from `smartassist.config` — the architectural keystone:

1. **`SMARTASSIST_DATA_DIR`** env var (tests, explicit config)
2. **Walk up from cwd** to find `.claude/smartassist/`
3. **RuntimeError** with helpful message

---

## CLI Reference

```bash
smartassist init          # Create .claude/smartassist/ in current project
smartassist serve         # Start MCP server (stdio transport)
smartassist health        # Run 6-check system health dashboard
smartassist migrate PATH  # Copy data from old rag-setup location
smartassist vectorize     # Re-vectorize all lessons
smartassist maintenance   # Staleness check + LanceDB compaction
smartassist analyze       # Usage analytics (hit rate, latency, trends)
smartassist dashboard     # Generate HTML dashboard
smartassist seed          # Seed lessons from CLAUDE.md
```

---

## Live Dashboard

SmartAssist generates a **real-time interactive dashboard** with searchable lessons, reliability scores, category breakdowns, and system health — all in a dark-themed HTML page.

> **[View the Live Dashboard](https://smartassist-ai.netlify.app/dashboard.html)**

Generate your own anytime:

```bash
smartassist dashboard --output ~/Desktop/dashboard.html
```

Or double-click `Lessons Dashboard.command` on your Desktop for one-click generation.

The dashboard includes:
- **Searchable lessons** — type to filter across all 100 curated lessons with highlighted matches
- **Category filters** — click to filter by testing, code_edit, git, architecture, pr_review, security
- **Reliability scores** — Thompson Sampling scores per category with visual bars
- **Feedback breakdown** — signal and category distribution charts
- **Usage evidence** — tool call counts, search hit rate, latency stats
- **Health check summary** — 6/6 subsystem checks

---

## Metrics (Production Deployment)

| Metric | Value |
|--------|-------|
| Raw Feedback Events | 1,991 |
| Curated Lessons | 100 |
| Categories | 6 (testing, code_edit, git, architecture, pr_review, security) |
| Tool Calls Logged | 20,070+ |
| Search Hit Rate | 54% |
| Avg Search Latency | 838ms |
| Median Search Latency | 804ms |
| Session Start Hook | 63ms |
| Tests | 59 passing in 0.09s |

---

## Project Structure

```
smartassist/
├── config.py                  # Path resolution + embedding config (keystone)
├── cli.py                     # CLI entry point (9 subcommands)
├── mcp_server.py              # MCP server (3 tools)
├── thompson_sampling.py       # Beta-Bernoulli with 30-day decay
├── feedback_system.py         # FeedbackCapture + JSONL storage
├── context_injection.py       # Lesson formatting + injection
├── lesson_feedback.py         # Per-lesson boost/demote/block scoring
├── hooks/
│   ├── session_start.py       # Inject weak-category lessons (63ms)
│   ├── session_end.py         # Save analytics
│   ├── vectorize_learnings.py # Auto-vectorize new lessons
│   ├── prompt_inject.py       # Context injection
│   ├── commit_hook.py         # Scan diffs for anti-patterns
│   ├── show_lessons.py        # Display search results
│   └── seed_from_claudemd.py  # Bootstrap from CLAUDE.md
└── tools/
    ├── cleanup_and_vectorize.py  # 20+ filter functions, dedup, rebuild
    ├── maintenance.py            # Staleness + compaction
    ├── health_check.py           # 6-check system health
    ├── analyze_usage.py          # Usage analytics
    └── generate_dashboard.py     # HTML dashboard
```

---

## Three-Layer Knowledge Stack

SmartAssist is designed to complement — not replace — other knowledge sources:

```mermaid
graph TB
    USER["👤 You Ask a Question"] --> CLAUDE["🤖 Claude Code"]

    subgraph L1["📄 LAYER 1 — CLAUDE.md"]
        CMD["Team-wide standards<br/><i>path aliases · testing thresholds · architecture</i>"]
    end

    subgraph L2["📦 LAYER 2 — Skills"]
        SK["Generic domain expertise<br/><i>React Native best practices · profiling · upgrades</i>"]
    end

    subgraph L3["🧠 LAYER 3 — SmartAssist"]
        SA["Project-specific lessons<br/><i>from real PRs · commits · team feedback</i>"]
    end

    CLAUDE -->|"always loaded"| L1
    CLAUDE -->|"description match"| L2
    CLAUDE -->|"MCP search"| L3

    L1 --> R["✅ Response with all three layers of knowledge"]
    L2 --> R
    L3 --> R

    style L1 fill:#1a1a2e,stroke:#38bdf8,stroke-width:2px,color:#e6edf3
    style L2 fill:#1a1a2e,stroke:#f472b6,stroke-width:2px,color:#e6edf3
    style L3 fill:#1a1a2e,stroke:#34d399,stroke-width:2px,color:#e6edf3
    style R fill:#1a1a2e,stroke:#fbbf24,stroke-width:3px,color:#e6edf3
    style CLAUDE fill:#1a1a2e,stroke:#a78bfa,stroke-width:3px,color:#e6edf3
```

| Layer | System | What It Provides |
|-------|--------|-----------------|
| **1. CLAUDE.md** | Static file | Team-wide standards, path aliases, testing thresholds |
| **2. Skills** | Markdown plugins | Generic domain expertise from industry experts |
| **3. SmartAssist** | MCP + LanceDB + RLHF | Project-specific lessons from real code reviews |

Each layer adds specificity. CLAUDE.md says *what*. Skills say *how*. SmartAssist says *what we learned*.

---

## Testing

```bash
python -m pytest tests/ -v
```

### Push Safety Gate (Local + CI)

Before pushing to `main`, run the same checks locally that CI enforces:

```bash
bash scripts/pre-push-main.sh
```

Install a local git `pre-push` hook once:

```bash
bash scripts/install-git-hooks.sh
```

This repository also includes GitHub Actions PR checks at
`.github/workflows/pr-checks.yml`:
- Python compile validation (`python -m compileall`)
- Full test suite (`uv run pytest -q`)

### QA Autodiagnose Harness (MCP + Claude + E2E)

Run the automated QA workflow locally:

```bash
# full mode (requires smartassist + claude CLIs available)
bash scripts/qa_autodiagnose.sh

# deterministic local check for harness logic
bash scripts/qa_autodiagnose.sh --dry-run --max-attempts 2
```

Key scripts:
- `scripts/qa_preflight.sh` — config and environment checks
- `scripts/qa_mcp_protocol.sh` — MCP startup + required tool contract probe
- `scripts/qa_claude_headless_smoke.sh` — headless Claude smoke check
- `scripts/qa_autodiagnose.sh` — staged retries, diagnostics, and metrics capture

Output artifacts are written to `qa-artifacts/qa-<timestamp>/`:
- `metrics.jsonl` — per-stage pass/fail + durations
- `summary.json` / `summary.txt` — final status and aggregate metrics

59 tests covering:
- **test_cleanup.py** (46 tests) — All 20+ filter functions, sanitization, normalization
- **test_thompson_sampling.py** (7 tests) — Reliability scoring, persistence, decay
- **test_config.py** (5 tests) — Path resolution, env var override, directory creation
- **conftest.py** — Shared `set_data_dir` fixture with `SMARTASSIST_DATA_DIR` monkeypatch

---

## License

[Business Source License 1.1](LICENSE) — free for individual and personal use.
Commercial embedding or hosted service use requires a [commercial license](mailto:joey@rahme.dev).
Converts to Apache-2.0 on 2030-03-03.

---

<p align="center">
  <strong>Built by Joey Rahme</strong><br>
  <a href="https://smartassist-ai.netlify.app/">Interactive Docs</a> · <a href="https://smartassist-ai.netlify.app/dashboard.html">Live Dashboard</a> · <a href="https://github.com/jnrahme/SmartAssist/wiki">Wiki</a>
</p>
