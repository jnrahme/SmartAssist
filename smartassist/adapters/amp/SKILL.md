---
name: smartassist-memory
description: Project memory that learns from feedback — search lessons, capture corrections, prevent repeated mistakes
---

# SmartAssist Memory Skill

## Before major implementation

Check for project-specific rules with `rag_search` before code edits, tests, commits, or architecture work.

## On user feedback

LESSON CREATION PROTOCOL
============================================================
When the user gives feedback during this session, use SmartAssist to
persist it for future sessions.

TRIGGERS — Use SmartAssist when:
- The user corrects your approach
- The user states a project rule or preference
- The user rejects generated code and explains the preferred pattern
- A PR review or code discussion reveals a team convention
- You discover a project-specific pattern by reading code, configs, or docs

PRIMARY WORKFLOW:
1. Call `apply_feedback_protocol` with the user's feedback or rule.
2. Let SmartAssist decide whether to create a lesson, boost an existing one, or suggest a merge.
3. Only call `merge_lessons` manually when SmartAssist returns `merge_suggested` and the overlap is real.

HOW TO WRITE LESSONS WHEN MANUAL INPUT IS NEEDED:
- Use imperative actions: 'Use semantic colors instead of hardcoded hex values'
- Keep lessons project-specific, not generic programming advice
- Categories: testing | code_edit | git | architecture | pr_review | security | debugging
- Use intensity 4-5 for hard rules ('never', 'always'), 2-3 for softer preferences
- Add brief context about what triggered the lesson

DO NOT CREATE LESSONS FOR:
- Generic programming knowledge
- One-time task instructions
- Duplicates of existing lessons
============================================================
