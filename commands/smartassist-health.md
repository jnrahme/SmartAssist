---
name: smartassist-health
description: Run SmartAssist health checks and report status
---

Run `smartassist health` and report the results to the user. If any checks fail, suggest remediation steps:

- **Missing data directory**: Run `smartassist setup` for the first project on this machine, or `smartassist init` for additional repos
- **Empty lesson database**: Run `smartassist seed`
- **Vector/cache issues**: Run `smartassist vectorize`
- **`lancedb` import failure**: Reinstall SmartAssist with its full dependencies, then rerun `smartassist vectorize`
- **MCP registration missing**: Run `smartassist setup` or `smartassist init`, then rerun `smartassist doctor`
- **Hook/runtime readiness issues**: Run `smartassist doctor` and use that output before changing config manually
- **Package not installed**: Run the `smartassist-setup` helper, or follow the current install steps from `README.md`
