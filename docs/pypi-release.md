# PyPI Release

SmartAssist is set up to publish from GitHub Actions via trusted publishing.

## One-time setup

1. Create the `smartassist` project on PyPI.
2. In PyPI, configure a trusted publisher for:
   - owner: `jnrahme`
   - repository: `SmartAssist`
   - workflow: `publish.yml`
   - environment: `pypi`
3. In GitHub, create an environment named `pypi`.
4. Make sure the version in `pyproject.toml` matches the release you want to publish.

## Local verification before release

Run:

```bash
./.venv/bin/python -m pytest
bash scripts/qa_package_smoke.sh
bash scripts/qa_pipx_smoke.sh
```

`qa_package_smoke.sh` verifies that SmartAssist can build an sdist/wheel and that the wheel exposes the expected console scripts.

`qa_pipx_smoke.sh` verifies that a built wheel can be installed with `pipx`, then runs `smartassist init` and `smartassist doctor --json` inside a clean temp workspace.

## Publish

1. Bump the version in `pyproject.toml`.
2. Commit the release.
3. Create and push a tag:

```bash
git tag v1.0.0
git push origin v1.0.0
```

That triggers `.github/workflows/publish.yml`, which builds the distributions and publishes them to PyPI using GitHub OIDC.

## Post-publish verification

On a clean machine or temp environment:

```bash
pipx install smartassist
smartassist version
cd /path/to/project
smartassist setup
smartassist doctor
```

Expected result:

- `smartassist version` prints the released version
- `smartassist setup` initializes the repo
- `smartassist doctor` returns `Status: ready`
