# SmartAssist Release Strategy

## Version Scheme

```
v1.1.0-beta.1    ← testing (npm install smartassist-memory@beta)
v1.1.0-beta.2    ← fix, test again
v1.1.0-beta.N    ← keep iterating
v1.1.0            ← stable (npm install smartassist-memory)
```

## How It Works

### Beta releases (current phase)

Tagged with `-beta.N` suffix. npm publishes with `--tag beta`.

- Default `npm install smartassist-memory` does NOT get beta versions
- Only explicit `npm install smartassist-memory@beta` gets them
- We can break things, fix, re-release without affecting anyone
- Each beta gets its own GitHub Release with binaries

### Stable releases (when ready)

Tagged without suffix. npm publishes with `--tag latest` (default).

- `npm install smartassist-memory` gets this version
- Only tag stable after all checks pass on a beta

## Release Commands

### Push a new beta
```bash
# First beta
git tag v1.1.0-beta.1
git push --tags

# After fixes
git tag v1.1.0-beta.2
git push --tags
```

### Promote to stable
```bash
git tag v1.1.0
git push --tags
```

### What happens on tag push

GitHub Actions (`.github/workflows/release.yml`) automatically:
1. Builds native binaries (macOS ARM, macOS Intel, Linux x64)
2. Tests each binary (`smartassist version`)
3. Creates GitHub Release with binaries attached
4. Publishes to npm with correct tag (beta or latest)

## Testing Checklist (run before each beta)

```bash
# Local
python -m pytest tests/ -x -q          # all tests pass
smartassist version                      # version correct
smartassist doctor                       # status: ready

# After npm publish
npm install -g smartassist-memory@beta   # installs clean
smartassist version                      # matches tag
smartassist setup                        # MCP registered
smartassist seed                         # lessons created
smartassist health                       # 6/7+ checks pass
```

## npm Package Version Sync

The `npm-package/package.json` version MUST match the git tag.

Before tagging:
1. Update `npm-package/package.json` → `"version": "1.1.0-beta.1"`
2. Update `pyproject.toml` → `version = "1.1.0b1"` (Python beta format)
3. Commit
4. Tag
5. Push tag

The release workflow reads the version from `package.json`.

## Current Status

| Version | Status | Notes |
|---|---|---|
| v1.0.0 | Released (pipx only) | On GitHub, no npm |
| v1.1.0-beta.1 | Next | First npm + binary release |
| v1.1.0 | Future | Stable after beta testing |
