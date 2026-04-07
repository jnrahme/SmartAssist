---
name: smartassist-memory
description: Project memory that learns from feedback — search lessons, capture corrections, prevent repeated mistakes
---

# SmartAssist Memory Skill

## On user feedback

When the user gives explicit feedback (thumbs up/down, corrections, praise):

```bash
npx -y smartassist-memory serve <<< '{"method":"tools/call","params":{"name":"create_lesson","arguments":{"lesson":"...","category":"...","sentiment":"negative"}}}'
```

Or use the CLI directly:

```bash
smartassist create-lesson --lesson "Always use theme tokens for colors" --category code_edit --sentiment negative
```

Do not store bare signals without context. Ask for one sentence explaining what worked or failed.

## Before major implementation

Check for project-specific rules:

```bash
npx -y smartassist-memory serve <<< '{"method":"tools/call","params":{"name":"rag_search","arguments":{"query":"how to handle testing in this project"}}}'
```

## At session start

Review current reliability scores and weak categories:

```bash
npx -y smartassist-memory serve <<< '{"method":"tools/call","params":{"name":"rag_dashboard","arguments":{}}}'
```

Apply lessons from weak categories as guardrails for this session.
