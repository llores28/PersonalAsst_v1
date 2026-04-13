# ADR-2026-04-11 — Org security and startup hardening

## Status
Accepted

## Context
Audit remediation identified reliability and security gaps in scheduler sync, startup migration execution, organization ownership enforcement, and observability of destructive org actions.

## Decision
1. **Scheduler one-shot contract normalization**
   - `sync_tasks_from_db()` now normalizes `once.run_at` to canonical ISO string before calling `add_one_shot_job()`.
   - Contract remains string-based at the scheduler API boundary.

2. **Startup migration gate**
   - Startup migrations run only when `STARTUP_MIGRATIONS_ENABLED=true`.
   - Default behavior is skip-with-log, to avoid accidental startup migration side effects.

3. **Organization ownership enforcement in dashboard API**
   - Org endpoints resolve request user from `X-Telegram-Id` (with owner fallback for single-user deployments).
   - Org reads/writes require owned organization resolution (`_get_owned_org_or_404`).

4. **Durable delete audit trail**
   - Org deletes now write `AuditLog` entries before delete commit in both dashboard API and Telegram `/orgs delete` flow.
   - This preserves a durable trace even when `OrgActivity` rows are removed by cascade.

5. **Exception-swallowing reduction in bot routing**
   - Critical `except Exception: pass` paths were replaced with warning logs while preserving graceful fallback behavior.

6. **CORS hardening**
   - Dashboard API CORS is now environment-driven via `cors_allowed_origins`.
   - Wildcard `*` is ignored, and empty/invalid config falls back to localhost dashboard origins.

## Consequences
- Better startup safety and lower risk of unintended migration execution.
- Stronger org access control for dashboard endpoints.
- Improved post-incident traceability for destructive org actions.
- Better debuggability of Telegram routing edge failures without changing UX fallback semantics.
- Safer default API exposure posture.
