---
name: smartassist-health
description: Run SmartAssist health checks and report status
---

Run `smartassist health` and report the results to the user. If any checks fail, suggest remediation steps:

- **Missing data directory**: Run `smartassist init`
- **Empty lesson database**: Run `smartassist seed`
- **No vector index**: Run `smartassist vectorize`
- **Stale vectors**: Run `smartassist vectorize` to rebuild
- **Package not installed**: Run `/smartassist-setup`
