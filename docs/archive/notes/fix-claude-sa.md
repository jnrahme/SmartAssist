# claude-sa — Goals & Status

## What claude-sa Does

`claude-sa` is the SmartAssist launcher. It starts Claude Code with the full SmartAssist system running alongside it:

1. **Claude Code** — interactive AI coding assistant (left tmux pane)
2. **SmartAssist Monitor** — real-time log of lesson injections, feedback, searches (right tmux pane)
3. **Dashboard** — web UI at http://localhost:3000 showing lessons, metrics, and live activity (opens in browser)

## How It Should Work

```bash
cd ~/my-project
claude-sa
```

This single command should:
- Auto-setup SmartAssist if not initialized (`smartassist setup` + `smartassist seed`)
- Start the dashboard server (detached, survives Claude exit)
- Open tmux with Claude Code + monitor side by side
- Open dashboard in browser
- Dashboard auto-shuts down 60s after browser tab is closed (heartbeat)
- Tmux stays open after Claude exits so user can restart

## Current Issues

### claude-sa exits randomly
- **Fixed (SA-016):** `tmux kill-session` was chained to Claude exit — removed
- **Fixed (SA-016):** `finally` block was terminating dashboard and cascading — removed
- **Status:** Needs user testing to confirm fix

### Dashboard not opening properly
- **Fixed (SA-015):** Dashboard now serves on localhost:3000 with live API
- **Fixed (SA-016):** Dashboard process fully detached (`start_new_session=True`)
- **Status:** Needs user testing to confirm

### Live activity feed was empty
- **Fixed (SA-015):** ANSI escape codes in rag_live.log were breaking the parser
- **Status:** Confirmed working — events now parse correctly

### Lessons not injecting on generic prompts
- **Known:** Generic prompts like "what are best practices?" don't match lessons semantically
- **Expected behavior:** Injection only triggers on strong semantic match
- **SessionStart injection works:** 2 lessons injected on session start via Thompson Sampling
- **TODO:** Consider lowering injection threshold or adding keyword fallback

## Architecture

```
claude-sa (launcher)
├── smartassist dashboard    (detached process, port 3000)
│   ├── GET /               HTML dashboard (regenerated each request)
│   ├── GET /api/live        Live event feed (parses rag_live.log)
│   └── GET /api/heartbeat   Keeps server alive
├── tmux session "claude-sa"
│   ├── Left pane:  claude   (interactive Claude Code)
│   └── Right pane: smartassist-monitor (tails rag_live.log)
```

## Dashboard Features

- **Metrics bar:** Lessons count, feedback events, categories, tool calls, hit rate, latency
- **Live activity feed:** Auto-refreshes every 3s, shows INJECT/SEARCH/PROMPT/CREATE/FEEDBACK events
- **Lesson search:** Full-text search across all lessons with category badges
- **Health checks:** 6 system checks (DB, feedback, scores, usage, sync, MCP)
- **Reliability scores:** Thompson Sampling scores per category with progress bars
- **Heartbeat auto-shutdown:** Server stops 60s after last browser ping

## Files

| File | Purpose |
|------|---------|
| `smartassist/claude_sa.py` | Launcher — tmux setup, dashboard start |
| `smartassist/tools/generate_dashboard.py` | Dashboard server + HTML generation + live API |
| `smartassist/monitor.py` | Terminal monitor (right tmux pane) |
| `.claude/smartassist/data/rag_live.log` | Live log file tailed by monitor and parsed by dashboard |

## TODO

- [ ] Confirm claude-sa no longer exits randomly
- [ ] Confirm dashboard stays open after Claude exit
- [ ] Confirm live feed shows events in real-time during active Claude session
- [ ] Lower injection threshold for better lesson matching on prompts
- [ ] Add lesson creation events to live feed (when user gives :) or :( feedback)
- [ ] Add "reason" field to live feed showing WHY a lesson was created
- [ ] Make dashboard show which lessons were injected at session start
- [ ] Consider adding WebSocket for true real-time updates (replace polling)
