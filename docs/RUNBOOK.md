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
docker compose down
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
docker compose build assistant
docker compose up -d assistant
```

The assistant container copies `src/`, `tests/`, and config files into the image at build time. Only `src/tools/plugins/` is bind-mounted live, so Python code changes in agents, routing, and handlers do not reach Telegram until you rebuild or restart the assistant container with a fresh image.

### Apply Database Migrations
```bash
docker compose exec assistant alembic upgrade head
```

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
   - **`FunctionTool object is not callable`** → bound tools must call `_*_impl` functions, not `@function_tool` objects
   - **Naive datetime rejected** → engine auto-attaches default timezone, but verify `DEFAULT_TIMEZONE` in `.env`

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
```
