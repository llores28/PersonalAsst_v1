# Runbook — PersonalAsst

## Service Map

| Service | Container | Health Check | Restart Command |
|---------|-----------|-------------|-----------------|
| Bot + Agents | `personal-assistant` | Send `/help` in Telegram | `docker compose restart assistant` |
| PostgreSQL | `assistant-postgres` | `pg_isready -U assistant` | `docker compose restart postgres` |
| Qdrant | `assistant-qdrant` | `curl http://localhost:6333/health` | `docker compose restart qdrant` |
| Redis | `assistant-redis` | `redis-cli ping` | `docker compose restart redis` |
| Google Workspace MCP | `workspace-mcp` | Container running | `docker compose restart workspace-mcp` |

## Common Operations

### Start Everything
```bash
docker compose up -d
```

### Stop Everything
```bash
docker compose down --remove-orphans
```

### View Logs
```bash
# All services
docker compose logs -f

# Just the bot
docker compose logs -f assistant

# Last 50 lines
docker compose logs --tail=50 assistant
```

### Rebuild Assistant After Source Changes
```bash
docker compose down --remove-orphans
docker compose build
docker compose up -d
```

The assistant container copies `src/`, `tests/`, and config files into the image at build time. Only `src/tools/plugins/` is bind-mounted live, so Python code changes in agents, routing, and handlers do not reach Telegram until you rebuild or restart the assistant container with a fresh image.

### Apply Database Migrations
```bash
docker compose exec assistant alembic upgrade head
```

### Startup Migration Gate

- Startup migrations are disabled by default.
- To enable startup Alembic execution, set:

```bash
STARTUP_MIGRATIONS_ENABLED=true
```

- Recommended operational pattern remains explicit migrations via `alembic upgrade head`.

### Backup Database
```bash
docker compose exec postgres pg_dump -U assistant assistant > backup_$(date +%Y%m%d).sql
```

### Restore Database
```bash
cat backup_20260316.sql | docker compose exec -T postgres psql -U assistant assistant
```

## Troubleshooting

### Bot Not Responding

1. Check container is running: `docker compose ps assistant`
2. Check logs for errors: `docker compose logs --tail=50 assistant`
3. Verify Telegram token: ensure `TELEGRAM_BOT_TOKEN` in `.env` is correct
4. Restart: `docker compose restart assistant`

### Google Workspace Not Working

1. Check MCP container: `docker compose ps workspace-mcp`
2. Check logs: `docker compose logs --tail=20 workspace-mcp`
3. Token may be expired → tell user to run `/connect google` in Telegram
4. Verify OAuth credentials in `.env`

### Cost Cap Reached

1. Check current spend: `/stats` in Telegram
2. Or query directly: `docker compose exec postgres psql -U assistant -c "SELECT * FROM daily_costs ORDER BY date DESC LIMIT 3;"`
3. Resets at midnight automatically
4. To increase: edit `DAILY_COST_CAP_USD` in `.env` and restart

### Database Issues

1. Check health: `docker compose exec postgres pg_isready -U assistant`
2. Check disk space: `docker compose exec postgres df -h`
3. Check connections: `docker compose exec postgres psql -U assistant -c "SELECT count(*) FROM pg_stat_activity;"`

### Memory/Qdrant Issues

1. Check Qdrant: `docker compose logs --tail=20 qdrant`
2. Restart: `docker compose restart qdrant`
3. Qdrant data persists in `qdrant_data` Docker volume

### Stale SDK Sessions / "Something Went Wrong"

If the bot gives generic errors or "No tool call found" 400 errors:

1. Check logs: `docker compose logs --tail=50 assistant | findstr "BadRequestError\|No tool call found"`
2. Clear stale SDK sessions:
   ```bash
   docker compose exec assistant python -c "
   import asyncio, redis.asyncio as aioredis
   async def clear():
       r = aioredis.from_url('redis://assistant-redis:6379/0', decode_responses=True)
       keys = await r.keys('agent_session:*')
       if keys:
           await r.delete(*keys)
           print(f'Cleared {len(keys)} stale session keys')
       else:
           print('No stale session keys')
       await r.aclose()
   asyncio.run(clear())
   "
   ```
3. The bot auto-recovers from stale sessions (catch + clear + retry), but manual clearing helps if sessions are badly corrupted.

### Scheduler / Reminder Issues

1. Check scheduler started: `docker compose logs assistant | findstr "Scheduler started"`
2. Check for job errors: `docker compose logs assistant | findstr "Failed to create reminder\|scheduler"`
3. List active APScheduler jobs:
   ```bash
   docker compose exec assistant python -c "
   import asyncio
   from src.scheduler.engine import start_scheduler, get_all_jobs, stop_scheduler
   async def check():
       await start_scheduler()
       jobs = await get_all_jobs()
       for j in jobs: print(j)
       await stop_scheduler()
   asyncio.run(check())
   "
   ```
4. Common issues:
   - **`DateTrigger` errors** → ensure `run_time=` parameter (not `run_date=`, APScheduler 4.x API)
   - **One-shot DB sync not firing** → ensure `scheduled_tasks.trigger_config.once.run_at` is valid ISO datetime
   - **`FunctionTool object is not callable`** → bound tools must call `_*_impl` functions, not `@function_tool` objects
   - **Naive datetime rejected** → engine auto-attaches default timezone, but verify `DEFAULT_TIMEZONE` in `.env`

### Dashboard API Access / CORS

1. Configure dashboard origins in `.env`:
   - `CORS_ALLOWED_ORIGINS=http://localhost:3001,http://127.0.0.1:3001`
2. Wildcard `*` is ignored for dashboard API CORS.
3. If using multiple users or explicit request scoping, dashboard API accepts `X-Telegram-Id` header for org ownership resolution.

### Dashboard Layout Not Saving

1. Layout is stored in Redis at `dashboard_layout:{telegram_id}` with 1-year TTL.
2. If Redis is flushed, layout resets to defaults — user can re-arrange and save.
3. Check Redis key: `docker compose exec redis redis-cli KEYS "dashboard_layout:*"`
4. The frontend debounces saves (1.2s delay) — rapid drag/resize may not persist if browser is closed immediately.

### Org Deletion Issues

1. **Preview before delete:** Use `GET /api/orgs/{id}/delete-preview` to see what will be removed.
2. **Selective retention:** Send `retain_agent_ids` and/or `retain_task_ids` in the DELETE body to move those entities to the `__retained__` holding org instead of deleting them.
3. **Holding org hidden:** The `__retained__` org is filtered out from listing endpoints. To inspect it directly: `docker compose exec postgres psql -U assistant -c "SELECT id, name FROM organizations WHERE name = '__retained__';"`

### Organization Management

- Telegram org lifecycle commands are available:
  - `/orgs create`
  - `/orgs info <id>`
  - `/orgs pause <id>`
  - `/orgs resume <id>`
  - `/orgs delete <id>`
- Org deletes write durable `audit_log` entries in addition to org activity feed.

### Repair Pipeline

#### View Open Tickets (Telegram)
```
/tickets
```
Lists all open repair tickets with status icons, priority, and created timestamp.

#### Approve a Verified Fix (Telegram)
```
/ticket approve <id>
```
Merges the verified branch to main. Owner-only. Triggers security challenge gate.

#### Close a Ticket Without Deploying (Telegram)
```
/ticket close <id>
```
Marks the ticket `closed` in the DB. Branch is NOT merged.

#### Force-clear the Pipeline Retry Counter
If a valid error is being incorrectly blocked by the max-retries guard:
```bash
docker compose exec assistant python -c "
from src.repair.engine import _PIPELINE_ATTEMPT_COUNTS
_PIPELINE_ATTEMPT_COUNTS.clear()
print('Retry counters cleared')
"
```
Note: this counter is in-memory and resets automatically on container restart.

#### Check Pending Repair in Redis
```bash
docker compose exec assistant python -c "
import asyncio, redis.asyncio as aioredis, json
async def check():
    r = aioredis.from_url('redis://assistant-redis:6379/0', decode_responses=True)
    keys = await r.keys('pending_repair:*')
    for k in keys:
        v = await r.get(k)
        print(k, json.loads(v) if v else None)
    await r.aclose()
asyncio.run(check())
"
```

#### Verification Failed Because Runner Is Wrong / Missing
Symptom: a repair patch is rolled back with `No module named ruff` (or
similar) in the verification stderr, even though the patch itself looks
correct. Most common when patching `SKILL.md` or other non-Python files
— ruff is a dev-only dep and is not installed in the runtime container.

What to do:
1. Reply `fix it` in Telegram. The repair agent now reads
   `failure_kind: missing_tool` from the stored error context and calls
   `refine_pending_verification` instead of re-proposing the patch.
2. The agent auto-picks file-type-correct verification commands via
   `python -m src.repair.verify_file <path>` (works for `.py`,
   `SKILL.md`, `.yaml`, `.json`, `.toml`, `.md`).
3. Reply `apply patch` to retry. The original patch is reapplied with the
   new verification step.

Manual sanity check from a shell:
```bash
docker compose exec assistant python -m src.repair.verify_file src/user_skills/<skill>/SKILL.md
```
Exit code 0 means the file is valid.

#### Repair Notification Email Not Arriving
1. Verify Gmail is connected: `/connect google` in Telegram.
2. Check `docker compose logs assistant | findstr "Repair email"` for send status.
3. If `[ERROR]` or `[CONNECTION ERROR]` in workspace tool result, MCP is disconnected — reconnect via `/connect google`.

## Monitoring Queries

```sql
-- Recent errors
SELECT timestamp, agent_name, error FROM audit_log 
WHERE error IS NOT NULL ORDER BY timestamp DESC LIMIT 10;

-- Today's costs
SELECT * FROM daily_costs WHERE date = CURRENT_DATE;

-- Active scheduled tasks
SELECT description, trigger_type, next_run_at FROM scheduled_tasks 
WHERE is_active = true ORDER BY next_run_at;

-- Tool usage stats
SELECT name, use_count, last_used_at FROM tools 
WHERE is_active = true ORDER BY use_count DESC;

-- Open repair tickets
SELECT id, title, status, priority, risk_level, auto_applied, created_at
FROM repair_tickets
WHERE status NOT IN ('deployed', 'closed')
ORDER BY created_at DESC;

-- Repair tickets ready to deploy
SELECT id, title, branch_name, created_at
FROM repair_tickets
WHERE status = 'ready_for_deploy'
ORDER BY created_at DESC;
```
