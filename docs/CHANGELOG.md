# Changelog

## 2026-04-12 — Agentic Upgrade (M1–M4) + Repo Cleanup

### Added — M3: Explainable Observability
- `AgentTrace` SQLAlchemy model + Alembic migration 006 (`agent_traces` table)
- Trace persistence in `orchestrator.py`: every tool call step recorded after `Runner.run()`
- `GET /api/traces` and `GET /api/traces/sessions` API endpoints
- Dashboard **Activity** tab: Timeline icon on each row → side drawer with step-by-step agent thought trace (tool name, args, result preview, duration)

### Added — M4: Tightened Self-Healing Loop
- `classify_repair_risk(plan)` in `src/repair/engine.py` → `low | medium | high` based on action types and file extensions
- `propose_low_risk_fix` tool on `RepairAgent`: auto-applies operational fixes (Redis key clears, schedule re-injections) immediately without owner approval gate
- `src/repair/verifier.py`: `run_quick_smoke()`, `verify_repair()`, `rollback_repair()` for post-apply verification
- `risk_level` and `auto_applied` columns on `RepairTicket` (migration 006)
- Dashboard **Repairs** tab: risk-level chips + green "Auto-applied" / yellow "Pending approval" badges
- `AUTO_REPAIR_LOW_RISK` env var (default `true`) to disable auto-apply if needed

### Added — M1: Parallel Multi-Agent Fan-Out
- `src/agents/parallel_runner.py`: `run_parallel_tasks()` with `asyncio.gather()`, max 3 branches, budget guard (falls back to sequential if daily spend ≥ 80%)
- `detect_parallel_domains()` in `routing_hardened.py`: conjunction + multi-domain keyword detection
- `PARALLEL` intent added to `TaskIntent` enum
- Orchestrator pre-flight: multi-domain messages fan-out before single-agent path

### Added — M2: Autonomous Background Jobs
- `src/agents/background_job.py`: `create_background_job()`, APScheduler tick loop, fault counter, Telegram notifications on complete/fail
- `BackgroundJob` SQLAlchemy model + migration 006
- Orchestrator: detects "monitor / watch / keep an eye / alert me when" phrases → creates background job, returns confirmation with interval + iteration cap
- Dashboard **Jobs** tab: progress bar, tick counter, cancel (Stop) button, status chips; tab badge shows active job count

### Changed — Repo Cleanup
- Deleted 4 orphan backup folders: `bootstrap-backup-/`, `bootstrap-backup-20260401-111517/`, `bootstrap_backup/`, `bootstrap_backup_/`
- Expanded `.gitignore`: `.windsurf/`, `bootstrap/`, `orchestration-ui/node_modules`, `orchestration-ui/build/`, `.tmp/`, cache dirs, `gmail-filters.xml`
- Created root `.dockerignore` for lean Docker build context (excludes docs, bootstrap, tests, node_modules, .windsurf, .git)
- Fixed `Dockerfile` (bot): added missing `COPY alembic/ ./alembic/` so container can run migrations
- Removed narrow `./src/orchestration` bind-mount from `docker-compose.yml` `orchestration-api` service
- Untracked `.windsurf/`, `bootstrap/`, `gmail-filters.xml` from git index (`git rm --cached`)

### Operational Notes
- Rebuild sequence:
  - `docker compose down --remove-orphans`
  - `docker compose build`
  - `docker compose up -d`
  - `docker compose exec assistant alembic upgrade head`

---

## 2026-04-11 — Reliability & Security Hardening (Audit Phase 1/2)

### Added
- Telegram organization command coverage with `/orgs` lifecycle support:
  - `/orgs create`, `/orgs info <id>`, `/orgs pause <id>`, `/orgs resume <id>`, `/orgs delete <id>`
- Durable delete audit trail for organization deletes in:
  - Dashboard API delete path
  - Telegram `/orgs delete` path
- New focused regression tests:
  - `tests/test_main.py`
  - `tests/test_orchestration_api_org_auth.py`
  - `tests/test_orchestration_api_cors.py`
  - `tests/test_bot_handlers_resilience.py`
  - `tests/test_bot_orgs_handlers.py`

### Changed
- Scheduler one-shot DB sync now normalizes `trigger_config.once.run_at` to canonical ISO string before scheduling.
- Startup migrations are now controlled by `STARTUP_MIGRATIONS_ENABLED` (default disabled).
- Dashboard API organization endpoints now enforce ownership resolution and org-scoped access checks.
- Dashboard API CORS moved from wildcard to env-driven allowlist via `CORS_ALLOWED_ORIGINS`.
- Bot routing resilience improved: critical fallback paths now emit structured warning logs instead of swallowing exceptions silently.

### Security
- Wildcard dashboard CORS origin (`*`) is rejected in parser logic.
- Org delete operations now preserve durable audit evidence in `audit_log` even when org activity rows cascade delete.

### Operational Notes
- Recommended rebuild sequence:
  - `docker compose down --remove-orphans`
  - `docker compose build`
  - `docker compose up -d`
- Recommended migration operation remains explicit:
  - `docker compose exec assistant alembic upgrade head`

### Verification Snapshot
- Targeted remediation suite passes:
  - `21 passed, 2 skipped`
- Full repository suite currently includes unrelated pre-existing failures outside this remediation scope (e.g., legacy org-agent `FunctionTool` callable expectations and complexity classifier expectation drift).
