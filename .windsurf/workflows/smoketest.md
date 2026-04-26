---
description: Run tiered smoke tests to verify project health
---
# Smoketest

## 1) Quick smoketest (deps, lint, tests)
// turbo
```
nexus smoketest --level quick --format json
```

## 2) Review results
- If all steps pass: project is healthy
- If steps fail: read the stderr output and suggest targeted fixes
- If steps skip: note which commands are missing, suggest adding them

## 3) Full smoketest (includes build + server health check)
```
nexus smoketest --level full --format json
```

## 4) Visual verification (web apps only)
If server health check passes, use Cascade's `browser_preview` tool:
- Start the dev server via `run_command` (non-blocking)
- Call `browser_preview` with the localhost URL from the smoketest output
- Check console logs for errors

## 5) Report
Summarize: steps passed, steps failed, steps skipped, suggested fixes.
