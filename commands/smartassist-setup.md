---
name: smartassist-setup
description: Install and initialize SmartAssist in this project
---

Guide the user through SmartAssist installation and initialization. Run each step and report results.

## Step 1: Check if smartassist is installed

Run `smartassist version` to check. If it fails, proceed to Step 2. If it succeeds, skip to Step 3.

## Step 2: Install the package

Use a supported install path:

- `pipx install git+https://github.com/jnrahme/SmartAssist.git` (recommended)
- local checkout: `pipx install .`

Do not suggest `pipx install smartassist`, npm, Homebrew, or the `/install` script unless the user explicitly says those channels are already published and verified.

## Step 3: Set up the current project

For the first SmartAssist repo on this machine, run:

- `smartassist setup`

For additional repos after SmartAssist is already installed/configured, run:

- `smartassist init`

## Step 4: Verify wiring

Run:

- `smartassist doctor`
- `smartassist health`

## Step 5: Seed lessons when useful

Run `smartassist seed` if the project has conventions in `CLAUDE.md` or if the user wants a first-pass memory corpus.

## Step 6: Rebuild cache only when needed

Run `smartassist vectorize` only if:

- the user explicitly wants a cache rebuild
- `smartassist health` reports stale or missing vector/cache data

If `doctor` is ready and `health` is acceptable for the current environment, tell the user SmartAssist is ready for this project.
