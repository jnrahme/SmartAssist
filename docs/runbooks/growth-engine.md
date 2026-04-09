---
title: SmartAssist Growth Engine
owner: Joey Rahme
last_updated: 2026-04-07
review_schedule: monthly
---

# SmartAssist Growth Engine

> **TL;DR:** Use this runbook to turn attention into installs, feedback, and
> contributors without spam bots.

## Definition of Done

This growth loop is working when:

- [ ] SmartAssist has clear feedback and contribution paths inside the repo
- [ ] Inbound interest from X, Reddit, HN, or GitHub lands on structured forms
- [ ] First-time issues and PRs get a warm automated welcome
- [ ] The maintainer runs a weekly human-approved outreach loop
- [ ] No outbound automation violates X or Reddit anti-spam rules

## When to Use This

Use this when you want more:

- installs
- product feedback
- contributor interest
- repeatable launch discipline

## Core Rules

1. Optimize for installs and feedback, not impressions.
2. Use automation for listening, drafting, and reminders.
3. Keep judgment with a human before anything gets posted.
4. Never auto-reply on Reddit.
5. Never auto-like, auto-follow, or auto-DM on X.
6. Respond to first-time contributors like future collaborators.

## Recommended Tool Stack

| Job | Best starting tool | Why |
| --- | --- | --- |
| Social listening | F5Bot | Free, fast alerts for Reddit and Hacker News |
| Broader monitoring | Syften | Adds X, GitHub, and more sources |
| X drafting | Typefully | Draft-first workflow with human approval |
| Post scheduling | Buffer | Simple queue for maintainer-led posting |
| Contributor analytics | GitHub traffic + OSS Insight | Good enough before paid tooling |
| Repo automation | GitHub Actions | First-response automation stays in-repo |

## Repo Bootstrap

Use the repo bootstrap script when you want to re-apply the GitHub-side growth
setup from a fresh clone or after settings drift.

```bash
bash scripts/bootstrap_github_growth.sh --dry-run
bash scripts/bootstrap_github_growth.sh
```

That script enables Discussions, sets repo metadata, and creates the labels used
by issue forms, PR labeling, and stale triage.

The repo also creates a weekly maintainer checklist issue automatically through
`.github/workflows/weekly-growth-checklist.yml`.

## Analytics

SmartAssist now has three analytics layers, and they answer different
questions.

### 1. Local usage analytics

- `smartassist analyze`
- `smartassist dashboard`

Use these when you want to understand what happened inside one project.

### 2. Opt-in aggregate product telemetry

- `smartassist telemetry enable`
- `smartassist telemetry export`
- `smartassist telemetry dashboard`

Use this when you want cross-install KPI rollups without turning on default
cloud tracking.

### 3. GitHub-native growth analytics

- `.github/workflows/growth-analytics.yml`
- `python -m smartassist.tools.github_growth_snapshot --repo OWNER/REPO`

This layer measures discovery and contributor flow using GitHub's own data:
views, clones, referrers, popular paths, open setup issues, feedback issues,
contributor-interest issues, and contributor-friendly backlog counts.

Important limit: GitHub traffic endpoints only keep a 14-day window, so the
weekly workflow preserves a recurring snapshot as an artifact.

## The Weekly Loop

### 1. Listen for real pain

Check alerts for:

- SmartAssist mentions
- Claude Code setup pain
- MCP workflow questions
- complaints about agents forgetting feedback

Ignore low-signal chatter. Only act on posts where SmartAssist can genuinely
help.

### 2. Reply manually

When a thread is relevant:

- answer the question first
- mention SmartAssist only if it fits naturally
- link to the repo or issue chooser only when it helps the person

### 3. Batch original posts

Ship 2 to 3 maintainer posts per week.

Good post types:

- a release or milestone
- a real setup improvement
- a before/after workflow example
- a lesson learned from user feedback

Use the copy templates in [`launch-pack.md`](launch-pack.md) to keep the
message consistent with the current install path and product maturity.

## The Launch Window

For a release or major milestone, use a coordinated 48-hour window.

### Day 1

- Publish the GitHub release
- Post on X
- Submit a targeted Reddit post where self-promotion is welcome
- Submit Show HN if the milestone is meaningful enough

### Day 2

- Follow up on every comment or issue
- Open a short feedback thread in the repo
- Convert repeated questions into docs or issue-template improvements

## Safe Automation Boundaries

### Good automation

- scheduled posts that you approved
- alerts for keyword mentions
- AI-assisted draft generation that you review
- first-response comments for new GitHub issues and PRs

### Bad automation

- Reddit auto-posting or auto-commenting
- X auto-replies to keywords or trends
- auto-likes, auto-follows, or auto-DMs
- multiple accounts posting the same message
- engagement buying or seeding services

## Convert Users Into Contributors

Do these every time someone shows serious interest:

1. Point them to `CONTRIBUTING.md`
2. Suggest a focused issue or doc fix
3. Respond quickly on first PRs
4. Thank them publicly in the thread or PR

If a user reports good product feedback, ask one follow-up question that helps
improve the onboarding or install path.

## Verify Completion

At the end of each week, confirm:

- [ ] We answered the highest-signal inbound threads
- [ ] We published at least one real product update
- [ ] New issues were routed through the right templates
- [ ] First-time contributors received a welcome comment
- [ ] We did not use any spammy automation

## When Things Go Wrong

### Traffic is coming in but installs are low

- tighten the README quickstart
- simplify the first-value path
- link directly to the setup-friction issue form

### People install but never follow up

- ask for product feedback explicitly
- add stronger next steps in docs and release posts
- show proof of value faster

### Inbound support volume gets noisy

- improve issue templates
- publish answers in README or docs
- enable GitHub Discussions manually when ready

## Questions?

For repo changes that support this runbook, open an issue or PR in this repo.
