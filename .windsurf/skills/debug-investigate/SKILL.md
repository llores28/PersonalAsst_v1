---
name: debug-investigate
description: Systematic debugging using CLI tools — reproduce, inspect, narrow, patch, verify
---
# Debug Investigation

## Trigger
- Error occurs during development
- Test failures
- Server startup failures
- User runs `/debug`

## Method
1. **Reproduce** — get the smallest input that triggers the error
2. **Inspect** — use `debug logs`, `debug trace`, `debug env`, `debug ports`
3. **Narrow** — identify root cause file and line
4. **Patch** — minimal fix targeting root cause
5. **Verify** — re-run failing test or smoketest

## Commands
```
nexus debug logs <path>
nexus debug trace "<error message>"
nexus debug deps
nexus debug env
nexus debug ports
nexus debug secrets-scan
```

## Pre-commit check
Run `debug secrets-scan` before suggesting any commit to catch leaked secrets.

## Stop conditions
- If secrets-scan finds leaks, stop and alert — never commit secrets
- If root cause is outside the project (system dep, service down), escalate to user
