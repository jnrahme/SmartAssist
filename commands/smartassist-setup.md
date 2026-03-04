---
name: smartassist-setup
description: Install and initialize SmartAssist in this project
---

Guide the user through SmartAssist installation and initialization. Run each step and report results.

## Step 1: Check if smartassist is installed

Run `smartassist version` to check. If it fails, proceed to Step 2. If it succeeds, skip to Step 3.

## Step 2: Install the package

Ask the user which method they prefer, then run the appropriate command:
- `pipx install git+https://github.com/jnrahme/SmartAssist.git` (recommended — isolated environment)
- `pip install git+https://github.com/jnrahme/SmartAssist.git`
- `uv pip install git+https://github.com/jnrahme/SmartAssist.git`

## Step 3: Initialize SmartAssist

Run `smartassist init` in the current project directory. This creates `.claude/smartassist/` with data and vector storage.

## Step 4: Seed the database

Run `smartassist seed` to populate the lesson database from the project's CLAUDE.md conventions.

## Step 5: Vectorize lessons

Run `smartassist vectorize` to build the vector index for RAG search.

## Step 6: Health check

Run `smartassist health` to verify everything is working. Report the results to the user.

If all steps pass, tell the user SmartAssist is ready. Their coding sessions will now automatically receive relevant lessons via RAG injection.
