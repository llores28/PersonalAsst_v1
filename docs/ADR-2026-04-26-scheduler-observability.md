# ADR-2026-04-26 — Scheduler Observability via JobReleased Events

**Status:** Accepted
**Date:** April 26, 2026
**Deciders:** Owner

## Context

We added two background jobs in this iteration: `_internal_nightly_memory_eviction` (daily 03:00 UTC) and `_internal_weekly_oauth_heartbeat` (Mon 09:00 UTC). Both are critical-path: a silent failure in eviction means runaway memory growth and embedding-API cost; a silent failure in the heartbeat means we miss revocation events and don't nudge users.

APScheduler 4.x catches exceptions raised by job callables and surfaces them as `EVENT_JOB_ERROR` events, but if no listener is subscribed, the failure is invisible. The default behavior is "the job ran, returned (or raised), and we move on." For a personal assistant that the operator only checks occasionally, that's not enough — we need a record of *when* each job ran, *whether* it succeeded, *how long* it took, and *how many consecutive failures* have accumulated, queryable on demand.

There is no existing scheduler-health endpoint; the orchestration API exposes per-tool / per-agent state but nothing about the scheduler itself.

## Decision

### Subscribe to APScheduler's `JobReleased` event
[`src/scheduler/observability.py`](../src/scheduler/observability.py:144) registers a single listener at scheduler startup. `JobReleased` fires after every finished job — success OR failure OR missed-deadline — with structured fields (`outcome`, `started_at`, `exception_type`, `exception_message`, etc.). Using `JobReleased` rather than the older event union (`EVENT_JOB_EXECUTED | EVENT_JOB_ERROR`) gives us a single uniform handler regardless of outcome.

### Per-job state in Redis at `scheduler_health:{schedule_id}` (30-day TTL)
Each record is a JSON blob:

```json
{
  "schedule_id": "memory_eviction",
  "last_run": "2026-04-26T03:00:12+00:00",
  "last_status": "success",
  "last_error": null,
  "last_duration_ms": 432,
  "consecutive_failures": 0,
  "total_runs": 5,
  "total_failures": 0
}
```

Why Redis over Postgres:
- Job state is ephemeral observability data, not source-of-truth — losing it on Redis flush is acceptable (next run repopulates).
- Read-side is `/api/health/scheduler` — needs to be fast (Redis) for dashboard polling.
- Already a dependency; adding a Postgres table for ~10 records is overkill.

The 30-day TTL is a backstop: jobs that stop running entirely will eventually drop out of the snapshot. If a job runs at least monthly, its record is always fresh.

### Pure-function `_apply_event(record, event)` for the state transition
The listener does I/O (read existing record, apply event, write new record). Splitting the apply-event logic into a pure function makes it directly testable without mocking Redis: feed an old record + a `JobReleased` event, get the new record back. 12 of the 15 observability tests target this pure function; only 3 test the I/O wrapper.

### Aggregate `get_health_snapshot()` for the API endpoint
SCAN over `scheduler_health:*` and return a snapshot with three rollups:
- `status` — `"healthy"` (no failures), `"degraded"` (any job ≥ `_DEGRADED_THRESHOLD = 3` consecutive failures), or `"unknown"` (no records).
- `summary` — `total_jobs`, `any_failing`, `max_consecutive_failures`.
- `jobs` — array of per-job records.

Exposed via `/api/health/scheduler` in [src/orchestration/api.py](../src/orchestration/api.py) as a public endpoint (no auth required — health endpoints should be reachable for monitoring tools).

### Listener never raises
Every exception path in `_on_job_released` is caught and logged. If Redis is down, observability degrades silently — but the scheduler itself keeps running. The job that just released is unaffected by our listener's failure. This is the cardinal rule for observability hooks: they must never break the thing they're observing.

### Adhoc-job naming
Some APScheduler invocations have `schedule_id=None` (e.g., one-shot jobs added via `add_one_shot_job` without an explicit ID). For those we synthesize `schedule_id = "adhoc:" + str(uuid)` so the record exists but is clearly distinguishable from named recurring jobs.

## Consequences

### Positive
- Per-job health is queryable via a single endpoint — operator answers "is the scheduler healthy?" with a curl, not a `docker logs | grep`.
- The `consecutive_failures` counter gives a real signal: 1 transient failure = noise, 3+ = action needed.
- Pure-function design makes the whole module testable without standing up APScheduler in tests.
- The listener is registered once at startup; adding new jobs gets observability "for free" — no per-job wiring.
- A failing job won't take down the scheduler because of our listener.

### Trade-offs / Limitations
- **In-memory cache = none.** Every event triggers a Redis read+write. At scheduler-event volumes (a handful per hour for our jobs), this is negligible, but a high-frequency job (every-second cron) would benefit from batching.
- **30-day TTL means stale jobs eventually disappear.** A job that runs only once a year would always be marked `"unknown"`. Acceptable for our cadences (daily, weekly).
- **No alerting.** `degraded` is reported on the health endpoint but doesn't auto-page. Pairing with the OAuth-nudge style helper would be a natural next step (Telegram alert on 3+ consecutive failures).
- **Single Redis instance is a SPOF for observability.** If Redis is down, both `/api/health/scheduler` and the listener silently degrade. Acceptable since the rest of the system also depends on Redis.

## Alternatives Considered

- **Store records in Postgres.** Rejected — adds a migration for ~10 records that are intrinsically ephemeral.
- **Subscribe to `EVENT_JOB_ERROR` only.** Rejected — would lose success-path data (last-run time, duration). Recording only failures means we can't distinguish "job working fine" from "job hasn't run."
- **Push to a metrics service** (Prometheus / OpenTelemetry). Considered for the future; rejected for now per HC-1 (self-hosted, no external observability deps yet) and KISS.
- **Embed observability calls inside each job body.** Rejected — adds boilerplate to every job, easy to forget, couples observability to job code. The listener pattern is decoupled.
- **Different degraded threshold (1, 5, 10 consecutive failures).** 3 chosen as the canonical "noise vs. signal" line: one transient is normal, three in a row is rarely transient. Tunable; revisit if we get false positives.
