---
name: prereqs-check
description: Check and guide setup for prerequisites (Docker, extensions, Windsurf Docker Extension, Python, Git, Node)
---
# Prerequisites Check

## Trigger
- First run of any workflow that needs Docker, scraping, or external tools
- Auto-runs at start of `/local-env`, `/smoketest --level full`, `/scrape`
- User runs `/prereqs`

## Prechecks
None — this IS the precheck.

## Command
```
nexus prereqs --format json
```

## Guide for a specific missing component
```
nexus prereqs --guide --component <name>
```

Available components: `python`, `git`, `node`, `docker`, `docker-compose`, `docker-extensions`, `docker-extension`

## Auto-installable components
- Docker Desktop extensions (ngrok, Release Share) can be installed via CLI with user approval
- Windsurf Docker Extension requires manual install via Extensions panel — tool provides steps

## Expected output
Structured JSON with per-component status, missing list, and next steps.

## Stop conditions
- If Docker is missing and the workflow requires it, stop and instruct user
- If Docker Extension is missing, continue with CLI fallback (still works, just no IDE integration)
