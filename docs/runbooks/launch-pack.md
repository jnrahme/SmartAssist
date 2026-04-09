---
title: SmartAssist Launch Pack
owner: Joey Rahme
last_updated: 2026-04-07
review_schedule: monthly
---

# SmartAssist Launch Pack

> **TL;DR:** Use these templates to talk about SmartAssist like a maintainer,
> not a growth bot.

## Definition of Done

This launch pack is being used well when:

- [ ] every post leads with the user problem first
- [ ] install instructions are accurate for the current release state
- [ ] X and Reddit posts stay honest about SmartAssist's strongest path today
- [ ] every launch asks for concrete feedback, not generic hype

## Current Truths To Keep In Copy

- Claude Code is the strongest SmartAssist experience today.
- The currently supported install path is the GitHub `pipx` install path unless
  PyPI is live and verified.
- SmartAssist learns from developer feedback and brings it back later.
- The ask is installs, real workflow feedback, and contributor interest.

## X Launch Template

```text
I built SmartAssist because I was tired of Claude Code repeating the same
mistakes across sessions.

It adds persistent memory that learns from feedback like
":( don't mock the database here" and brings that lesson back later.

Current install path:
pipx install git+https://github.com/jnrahme/SmartAssist.git
smartassist setup

If you try it, I’d love 3 things:
1. where setup was rough
2. whether the feedback loop felt natural
3. whether it changed a later prompt in your repo
```

## Reddit Launch Template

```text
I built SmartAssist because I kept seeing the same failure pattern with coding
agents: you correct them, they do better for five minutes, then they forget.

SmartAssist is a repo-local memory layer that learns from developer feedback and
injects relevant lessons back into later prompts. Claude Code is the most
validated path right now.

Current install path:
pipx install git+https://github.com/jnrahme/SmartAssist.git
smartassist setup

I’m not mainly looking for stars. I want honest feedback on:
- setup friction
- whether the feedback loop felt useful or awkward
- what would make you keep using it
```

## Show HN Template

### Title

```text
Show HN: SmartAssist – persistent memory for Claude Code that learns from feedback
```

### Body

```text
I built SmartAssist after getting frustrated with correcting the same coding
agent mistakes over and over.

The idea is simple: when you give feedback like ":) good call using the theme"
or ":( don't hardcode colors here", SmartAssist stores reusable lessons and
surfaces them again later in the same repo.

Claude Code is the best-supported path today. I’d especially love feedback on:
- setup friction
- whether the feedback syntax is natural
- whether the memory actually improves later prompts
```

## Weekly Posting Rhythm

### Monday

- check inbound mentions and repo feedback
- turn repeated friction into docs or issue updates

### Wednesday

- publish one maintainer update on X
- share one setup or product improvement

### Friday

- post a proof point, example, or milestone
- thank contributors or users who gave useful feedback

## Post Ideas That Usually Work

- before-and-after workflow examples
- setup improvements that remove real friction
- lessons learned from user feedback
- milestone posts with one concrete proof point

## Things To Avoid

- vague AI hype
- pretending the install path is easier than it is
- claiming broad support where validation is still thin
- asking for stars without asking for useful feedback

## Questions?

For repo updates that support this launch pack, use
[`docs/runbooks/growth-engine.md`](growth-engine.md).
