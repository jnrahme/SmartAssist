# Contributing to SmartAssist

Thanks for helping SmartAssist get sharper for real developer workflows.

Please read [CODE_OF_CONDUCT.md](CODE_OF_CONDUCT.md) before participating.

## Best First Contributions

These are the easiest places to help without needing deep system context.

- Docs, onboarding, and copy fixes
- Setup-friction fixes in CLI flows or docs
- QA harness coverage and smoke-script improvements
- Small CLI or workflow quality-of-life fixes

If you are new to the repo, start with issues labeled `good first issue` or
`help wanted` when they are available.

## Before You Start

- Small doc or template fixes can go straight to a pull request.
- For larger changes, open an issue first so we can agree on scope.
- If you touch retrieval, memory, ranking, or feedback attribution, read
  [MEMORY.md](MEMORY.md) before changing code.
- By submitting a contribution, you agree that it will be licensed under the
  repository's current BUSL-1.1 terms.

## Local Setup

### Recommended path

```bash
git clone https://github.com/jnrahme/SmartAssist.git
cd SmartAssist
uv sync --extra dev
```

### Alternative without `uv`

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -e ".[dev]"
```

### Optional: install the local pre-push hook

```bash
bash scripts/install-git-hooks.sh
```

That hook runs `scripts/pre-push-main.sh` before pushes.

## Repo Landmarks

Use these files to get oriented quickly.

- [`README.md`](README.md) — install path, product story, and commands
- [`smartassist-overview.html`](smartassist-overview.html) — architecture tour
- [`MEMORY.md`](MEMORY.md) — protected memory and retrieval invariants
- [`smartassist/cli.py`](smartassist/cli.py) — main CLI surface
- [`smartassist/hooks/`](smartassist/hooks/) — Claude Code hook lifecycle
- [`tests/`](tests/) — regression coverage

## Verification Before Opening a PR

Run the smallest command set that proves your change, then add more when your
change touches packaging, setup, or integration paths.

### Baseline for most code changes

```bash
python3 -m compileall -q smartassist tests
pytest -q
```

### Also run these when you touch packaging, setup, or QA workflows

```bash
bash scripts/qa_package_smoke.sh
bash scripts/qa_pipx_smoke.sh
```

### Also run these when you touch the live Claude integration path

```bash
bash scripts/qa_preflight.sh
bash scripts/qa_mcp_protocol.sh --timeout 5
bash scripts/qa_claude_headless_smoke.sh --timeout 60
```

### If you touch memory or retrieval behavior

Follow the extra verification commands in [MEMORY.md](MEMORY.md).

## Pull Request Expectations

Keep pull requests small, specific, and easy to review.

Include:

- What problem you changed
- Why this approach fits SmartAssist
- Exactly how you verified it
- Docs updates when the behavior or developer workflow changed

Avoid mixing unrelated fixes into one PR.

## Need Help?

- Setup friction or onboarding pain: use the issue chooser
- Product bugs: open a bug report
- Ideas or contributor interest: open a feature request or feedback issue
- Security issues: follow [SECURITY.md](SECURITY.md) instead of opening a
  public issue

## Maintainer Growth Runbook

If you are helping with distribution, feedback capture, or contributor
conversion, use the maintainer playbook at
[`docs/runbooks/growth-engine.md`](docs/runbooks/growth-engine.md).

For exact launch copy and posting templates, use
[`docs/runbooks/launch-pack.md`](docs/runbooks/launch-pack.md).
