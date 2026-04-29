# Runbook — PersonalAsst

## First-time setup (local dev machine)

Atlas runs entirely from Docker Compose at runtime. The host venv is only needed for local dev tasks (running tests, regenerating the dashboard, scaffolding tools). The CLI tooling lives in a sibling repo so Atlas's tree stays clean for Docker builds and Cloud Run deployment.

```bash
# 1. Clone Atlas (consumer) and Nexus (dev-time CLI) as siblings.
cd d:/PyProjects   # (or wherever you keep checkouts)
git clone https://github.com/llores28/PersonalAsst.git
git clone https://github.com/llores28/Nexus.git

# 2. Set up Atlas's venv and install both runtime + dev deps.
cd PersonalAsst
python -m venv .venv
.venv/Scripts/activate          # Windows (Git Bash). Use `source .venv/bin/activate` on Unix.
pip install -r requirements-dev.txt
pip install -e ../Nexus         # host-only — the dev Docker stage skips this
                                # because ../Nexus is outside the build context.

# 3. Verify nexus CLI is wired to the sibling clone.
nexus --version                  # → nexus, version 0.2.0
nexus journal status             # reads .nexus/state.json from this repo
python -c "import json; from importlib.metadata import distribution; print(json.loads(distribution('nexus-bootstrap').read_text('direct_url.json')))"
# → {'dir_info': {'editable': True}, 'url': 'file:///D:/PyProjects/Nexus'}

# 4. First-time secrets + Docker bring-up.
cp .env.example .env             # then fill in TELEGRAM_BOT_TOKEN, OPENAI_API_KEY, etc.
python scripts/ensure_workspace_mcp_key.py
docker compose up -d
```

**Why sibling, not nested**: Nexus is a separately-versioned dev tool with its own GitHub repo, monthly release cadence, and tests. Co-locating it inside Atlas's tree caused two `.git/` dirs under one root, IDE context bleed, accidental cross-repo commits, and Dockerfile bloat (the production image was at risk of bundling Nexus's tooling). The sibling layout makes the boundary physical: `requirements.txt` (production runtime) does NOT pull Nexus; `requirements-dev.txt` (dev only) does. The Cloud Run / GitHub-Actions Docker build never sees Nexus.

If you accidentally end up with a nested `Nexus/` checkout under PersonalAsst/, run the migration steps in `docs/CHANGELOG.md` (entry dated 2026-04-29) — it's reversible.

## Service Map

| Service | Container | Health Check | Restart Command |
|---------|-----------|-------------|-----------------|
| Bot + Agents | `personal-assistant` | Send `/help` in Telegram | `docker compose restart assistant` |
| PostgreSQL | `assistant-postgres` | `pg_isready -U assistant` | `docker compose restart postgres` |
| Qdrant | `assistant-qdrant` | `curl http://localhost:6333/health` | `docker compose restart qdrant` |
| Redis | `assistant-redis` | `redis-cli ping` | `docker compose restart redis` |
| Google Workspace MCP | `workspace-mcp` | Container running | `docker compose restart workspace-mcp` |

## Cloud Run deploy (production)

Atlas's production target is Google Cloud Run. The Docker `prod` stage already
builds clean (audited 2026-04-29 — Nexus dev-time tooling is NOT bundled).
The deploy pipeline lives in [cloudbuild.yaml](../cloudbuild.yaml) +
[.gcloudignore](../.gcloudignore) and fires on every push to `main` once the
GitHub trigger is connected.

### Architecture

| Service | Cloud Run config | Why |
|---|---|---|
| `atlas-bot` | min=1, max=1, no-cpu-throttling, 2vCPU/2GiB, $no-allow-unauthenticated$ | Telegram long-polling: getUpdates returns 409 with two consumers; CPU throttling stalls polling between calls |
| `atlas-api` | min=0, max=5, 1vCPU/1GiB, $no-allow-unauthenticated$ | Scale-to-zero FastAPI; serves the React dashboard via private service-to-service call |
| `workspace-mcp` | min=0, max=1, ingress=internal | Sidecar OAuth token store; private; bot calls via service-to-service ID token |
| Cloud SQL Postgres | `db-f1-micro`, ~$9/mo | Atlas's relational store (orgs/agents/tasks/etc.) |
| Redis (recommended: Upstash) | Serverless HTTP, ~$0–3/mo | Session state + meta-reflector counter + repair checkpoint. Upstash beats Memorystore by ~$25/mo for single-user (no VPC connector tax). |
| Qdrant Cloud | Free tier | Mem0 vector store |

### One-time provisioning (run once per project)

#### Step 1 — GCP project + IAM + Secret Manager

```bash
# Create the project (or use an existing one)
gcloud projects create atlas-prod-XXXX --name="Atlas (PersonalAsst)"
gcloud config set project atlas-prod-XXXX
gcloud auth login
gcloud auth application-default login    # for Application Default Credentials

# One-shot provisioning of APIs + Artifact Registry + IAM + Secret Manager.
# Reads .env if present so secrets are pre-populated.
bash scripts/provision-gcp.sh us-central1
```

The script enables the 5 required APIs (cloudbuild, run, artifactregistry,
secretmanager, sqladmin), creates the `atlas-images` Artifact Registry repo,
grants the Cloud Build service account 4 IAM roles, and creates 11 Secret
Manager entries (populated from your local `.env` if available, or
`REPLACE_ME_*` placeholders otherwise).

#### Step 2 — Provision the data layer

**Cloud SQL Postgres** (~$9/mo for db-f1-micro):
```bash
gcloud sql instances create atlas-pg \
    --database-version=POSTGRES_17 \
    --tier=db-f1-micro \
    --region=us-central1 \
    --root-password=TEMP_CHANGE_ME

gcloud sql databases create atlas --instance=atlas-pg
gcloud sql users create assistant --instance=atlas-pg --password=GENERATED_PASSWORD

# Build the DATABASE_URL and store in Secret Manager.
INSTANCE_CONN="$(gcloud sql instances describe atlas-pg --format='value(connectionName)')"
DB_URL="postgresql://assistant:GENERATED_PASSWORD@/atlas?host=/cloudsql/${INSTANCE_CONN}"
echo -n "$DB_URL" | gcloud secrets versions add database-url --data-file=-
```

Then add `--add-cloudsql-instances=$INSTANCE_CONN` to the bot + api `gcloud
run deploy` steps in `cloudbuild.yaml` (substitution variable `_DB_INSTANCE`).

**Redis — Upstash (recommended, single-user)**:
1. Sign up at [upstash.com](https://upstash.com) (free tier covers single-user usage).
2. Create a Redis database (region: `us-central1` to match GCP).
3. Copy the "Redis Connect URL" (rediss:// scheme — TLS).
4. `echo -n "rediss://default:PASSWORD@HOST:PORT" | gcloud secrets versions add redis-url --data-file=-`

**Redis — Memorystore (alternative, ~$44/mo)**:
```bash
# Memorystore requires a Serverless VPC Connector (~$9/mo even idle).
gcloud compute networks vpc-access connectors create atlas-vpc \
    --region=us-central1 --range=10.8.0.0/28
gcloud redis instances create atlas-redis \
    --size=1 --region=us-central1 --tier=basic --redis-version=redis_7_0

REDIS_HOST="$(gcloud redis instances describe atlas-redis --region=us-central1 --format='value(host)')"
REDIS_PORT="$(gcloud redis instances describe atlas-redis --region=us-central1 --format='value(port)')"
echo -n "redis://${REDIS_HOST}:${REDIS_PORT}" | gcloud secrets versions add redis-url --data-file=-
```
Add `--vpc-connector=atlas-vpc --vpc-egress=private-ranges-only` to the
`gcloud run deploy` steps in cloudbuild.yaml.

**Qdrant Cloud (free tier covers single-user)**:
1. Sign up at [cloud.qdrant.io](https://cloud.qdrant.io).
2. Create a free-tier cluster (region nearest us-central1).
3. Copy the cluster URL + API key.
4. `echo -n "https://YOUR-CLUSTER.qdrant.io" | gcloud secrets versions add qdrant-url --data-file=-`
5. `echo -n "YOUR_API_KEY" | gcloud secrets versions add qdrant-api-key --data-file=-`

#### Step 3 — Connect Cloud Build to GitHub

This requires interactive GitHub OAuth — do it in the GCP Console:
1. Open Cloud Build → Triggers → **Connect Repository** → GitHub.
2. Authorize Google Cloud Build for `llores28/PersonalAsst_v1`.
3. Create a trigger:
   - Event: Push to branch
   - Source: `^main$`
   - Configuration: Cloud Build configuration file (yaml or json)
   - Location: Repository / `cloudbuild.yaml`

Or via CLI (after the GitHub connection is authorized once via Console):
```bash
gcloud builds triggers create github \
    --repo-name=PersonalAsst_v1 \
    --repo-owner=llores28 \
    --branch-pattern=^main$ \
    --build-config=cloudbuild.yaml \
    --name=atlas-deploy-main
```

#### Step 4 — Verify and deploy

```bash
# Check that all secrets have real values (not REPLACE_ME_*).
for s in telegram-bot-token openai-api-key google-oauth-client-id \
         google-oauth-client-secret workspace-mcp-signing-key mem0-api-key \
         database-url redis-url qdrant-url qdrant-api-key dashboard-api-key; do
    val=$(gcloud secrets versions access latest --secret=$s 2>/dev/null)
    if [[ $val == REPLACE_ME_* ]]; then echo "  TODO: $s"; fi
done

# Trigger the first deploy by pushing an empty commit (or any push to main).
git commit --allow-empty -m "trigger: first Cloud Run deploy"
git push origin main

# Tail the build:
gcloud builds list --limit=1 --format='value(id)' | xargs -I {} gcloud builds log {} --stream

# After build succeeds, smoke-test the bot by sending /help in Telegram.
# Smoke-test the API:
gcloud run services describe atlas-api --region=us-central1 --format='value(status.url)'
# → curl that URL with X-API-Key header.
```

### Cost expectations (single-user, ~100 messages/day)

| Component | Monthly cost |
|---|---|
| atlas-bot (min=1, always-on) | ~$14–18 |
| atlas-api (scale-to-zero) | ~$1 |
| workspace-mcp (scale-to-zero) | ~$0.50 |
| Cloud SQL Postgres (db-f1-micro) | ~$9 |
| Redis — **Upstash** | ~$0–3 |
| Redis — Memorystore + VPC connector | ~$44 |
| Qdrant Cloud (free tier) | $0 |
| Secret Manager (10 secrets, low access) | <$0.30 |
| Cloud Build (within free 2,500 min/month) | $0 |
| **Total with Upstash** | **~$25–32/mo** |
| **Total with Memorystore** | **~$66–73/mo** |

### Three deploy pitfalls to avoid

1. **Forgetting `--no-cpu-throttling` on the bot** → polling stalls mid-getUpdates → 30–60s response gaps. Already pinned in cloudbuild.yaml.
2. **Letting `--max-instances` default to 100** → during redeploy, old+new instance both call getUpdates → Telegram returns 409 Conflict → bot bans polling for ~10 min. Already pinned to `--max-instances=1` in cloudbuild.yaml.
3. **Using Memorystore + VPC connector for single-user** → +$25/mo for zero functional benefit. Use Upstash unless your traffic justifies VPC-private Redis.

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

### Workspace-MCP token persistence

**Why this matters:** The OAuth heartbeat (`_internal_weekly_oauth_heartbeat`) sends a Telegram nudge to any user it classifies as `auth_failed`. If workspace-mcp loses its tokens on container rebuild, *every* connected user gets nudged on the next Monday — looking exactly like a mass token revocation. We pin three settings in [docker-compose.yml](../docker-compose.yml) to prevent this:

| Variable | Value | Why |
|---|---|---|
| `WORKSPACE_MCP_OAUTH_PROXY_STORAGE_BACKEND` | `disk` | Image's Linux default is `memory` — proxy state evaporates on restart. |
| `WORKSPACE_MCP_OAUTH_PROXY_DISK_DIRECTORY` | `/data/oauth-proxy` | Lands inside the named volume. |
| `WORKSPACE_MCP_CREDENTIALS_DIR` | `/data/credentials` | Image's default is `~/.google_workspace_mcp/credentials` — written to the writable layer (not the volume). |
| `FASTMCP_SERVER_AUTH_GOOGLE_JWT_SIGNING_KEY` | from `WORKSPACE_MCP_SIGNING_KEY` | Without this, the image derives the Fernet key from `GOOGLE_OAUTH_CLIENT_SECRET` — silent token loss on client-secret rotation. |

The named `workspace_tokens` volume is mounted at `/data` (covers both `/data/credentials` and `/data/oauth-proxy`).

**First-time setup:**
```bash
cp .env.example .env       # if you haven't already
python scripts/ensure_workspace_mcp_key.py   # generates WORKSPACE_MCP_SIGNING_KEY
docker compose up -d
```

`scripts/ensure_workspace_mcp_key.py` is idempotent — it only writes if the key is missing/empty. Re-run any time without risk.

**Recovery procedures:**

*If you lost your `.env` (and therefore `WORKSPACE_MCP_SIGNING_KEY`):*
1. Generate a new key via the script.
2. The persisted tokens in the `workspace_tokens` volume are now unreadable. Don't try to "rotate" — just have all users run `/connect google` again. The Mon 09:00 UTC heartbeat will detect the auth failures and nudge them automatically; you can also force the cycle by running `weekly_oauth_heartbeat()` manually (see "Run a job manually" below).

*If `docker compose down -v` was run and the volume was deleted:*
- All persisted tokens are gone. Same recovery as above.

*To verify persistence is actually working:*
```bash
# Restart workspace-mcp and confirm tokens survive.
docker compose restart workspace-mcp
# Then run a heartbeat scoped to one connected user — should report "ok".
docker compose exec assistant python -c "
import asyncio
from src.scheduler.maintenance import weekly_oauth_heartbeat
print(asyncio.run(weekly_oauth_heartbeat(user_ids=[<your_telegram_id>])))
"
```

### System maintenance jobs

Two internal jobs run automatically on every scheduler start:

| Schedule ID | Cadence | Purpose |
|---|---|---|
| `_internal_nightly_memory_eviction` | Daily 03:00 UTC | Caps each user's Mem0 memories (default 8000) by summarize-then-delete. Per-user retry (tenacity, 3 attempts) + per-user try/except — one user's failure cannot abort the batch. |
| `_internal_weekly_oauth_heartbeat` | Mon 09:00 UTC | Calls `get_user_profile` per connected Google user to reset Google's 6-month idle clock and detect revoked tokens. On `auth_failed`, sends a Telegram nudge asking the user to run `/connect google` (Redis-deduped 6 days). |

**Inspect health:** `GET /api/health/scheduler` returns the per-job snapshot persisted by the `JobReleased` listener. Status is `healthy` (no failures), `degraded` (any job ≥3 consecutive failures), or `unknown` (no runs recorded yet).

**Run a job manually (debugging):**
```bash
docker compose exec assistant python -c "
import asyncio
from src.scheduler.maintenance import weekly_oauth_heartbeat
print(asyncio.run(weekly_oauth_heartbeat(user_ids=[<telegram_id>])))
"
```

**Inspect / reset OAuth nudge dedup:**
```bash
# Is a user currently in the dedup window?
docker compose exec redis redis-cli GET "notification_sent:<telegram_id>:oauth_reauth"

# Force re-nudge on the next heartbeat (rarely needed — heartbeat re-nudges weekly anyway):
docker compose exec redis redis-cli DEL "notification_sent:<telegram_id>:oauth_reauth"
```

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
