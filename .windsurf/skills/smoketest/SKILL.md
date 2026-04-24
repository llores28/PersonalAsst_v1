---
name: smoketest
description: Run tiered smoke tests to verify project health after changes or bootstrap
---
# Smoketest

## Trigger
- After bootstrap completes
- After major code changes
- Before PR or release
- When user runs `/smoketest`

## Prechecks
- Dev dependencies installed (check via `debug deps`)
- For `--level full`: Docker may be needed for server health check

## Commands
```
python bootstrap/cli/bs_cli.py smoketest --level quick --format json
python bootstrap/cli/bs_cli.py smoketest --level full --format json
```

## Levels
- **quick**: deps verify, lint/typecheck, unit tests
- **full**: quick + build + server start + health check

## Integration with browser_preview
For web apps, after a successful `--level full` smoketest, invoke Cascade's `browser_preview` tool on the reported localhost URL for visual verification.

## Interpreting results
- Each step reports pass/fail/skip with duration and truncated output
- On failure: read stderr, suggest targeted fix
- On skip: note which command was missing, suggest adding it

## Stop conditions
- If deps-verify fails, remaining steps are unreliable
- If all steps skip, project type detection failed — ask user for stack info
