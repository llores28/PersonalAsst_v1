---
description: Run the Google Workspace audit harness and review coverage gaps
---
# Google Workspace Audit

## 1) Preconditions
- Confirm the Docker stack is running: `docker compose ps`
- Confirm Google Workspace is connected for the target Telegram user
- Use this audit after OAuth changes, routing changes, or Google tool bugfixes

## 2) Run the read-only audit inside the assistant container
```
docker compose exec assistant python -m src.google_audit --format json
```

## 3) Optional: target a specific connected account or user ID
```
docker compose exec assistant python -m src.google_audit --format human --user-id <telegram_user_id>
docker compose exec assistant python -m src.google_audit --format human --email <connected_google_email>
```

## 4) Review the result sections
- `status`: overall audit result
- `steps`: sequential Gmail, Calendar, and Tasks checks
- `coverage`: what is directly covered, partially covered, or uncovered in this repo
- `details.audit_mode`: this audit is intentionally read-only today

## 5) Interpret the result
- `pass`: all directly audited Google checks passed and coverage is complete
- `warn`: the directly audited checks passed, but some granted Google services are only partially covered or not integrated in this repo
- `fail`: at least one directly audited Google check failed; inspect the failing step message and compare with `docker compose logs workspace-mcp --tail 200`

## 6) Follow-up actions
- If Gmail fails: re-run `/connect google` and retest
- If Calendar fails: inspect the exact `get_events` response and OAuth scopes
- If Tasks fails: inspect the exact `list_task_lists` / `list_tasks` response and sidecar logs
- If coverage is `partial` or `uncovered`: add deterministic direct tool contracts before claiming those Google services are audited
