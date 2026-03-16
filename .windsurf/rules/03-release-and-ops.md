# Release & Ops

## Deployment Model

- **Single environment:** Local Docker Compose stack on user's machine or VPS.
- No CI/CD pipeline (single-user project) — manual `docker compose up -d`.
- Watchtower container auto-pulls new images daily (if published).

## Release Checklist

1. All tests pass (`pytest tests/ -v`).
2. `ruff check` and `mypy` clean.
3. Docker Compose builds: `docker compose build --no-cache`.
4. `docker compose up -d` starts all services — bot responds to `/start`.
5. Database migrations applied: `alembic upgrade head` (runs in entrypoint).
6. No regressions in existing Telegram commands.
7. Cost tracking still functional (`/stats` returns data).

## Operational Commands

```bash
# Start everything
docker compose up -d

# View logs
docker compose logs -f assistant

# Restart just the bot
docker compose restart assistant

# Apply database migrations
docker compose exec assistant alembic upgrade head

# Backup PostgreSQL
docker compose exec postgres pg_dump -U assistant assistant > backup.sql

# Check service health
docker compose ps
```

## Monitoring

- Bot sends daily health ping to owner (configurable).
- `/stats` shows: today's API cost, total interactions, active tools, scheduled jobs.
- All errors logged to `audit_log` table with full context.
- Container restart policy: `unless-stopped` (auto-recovers from crashes).

## Backup Strategy

- PostgreSQL: Daily `pg_dump` to Docker volume (automated via scheduled job).
- Optional: Upload backup to Google Drive (Phase 6, requires Phase 2).
- Fallback: Local volume backup always works regardless of Google integration.
- Qdrant: Data in Docker volume — backed up via volume snapshot.
- Redis: Ephemeral cache — no backup needed (sessions rebuild from memory).

## Incident Response

1. Check `docker compose logs -f assistant` for errors.
2. Check `docker compose ps` for crashed containers.
3. Query `audit_log` table for recent error entries.
4. If DB corruption: restore from latest `pg_dump` backup.
5. If token expired: user runs `/connect google` to re-authorize.
6. If cost cap hit: wait until midnight reset or increase `DAILY_COST_CAP_USD` in `.env`.
