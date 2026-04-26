"""Scheduler observability: structured logging + per-job health tracking.

Subscribes to APScheduler's `JobReleased` event so every finished job (success,
error, missed deadline) updates a per-job health record in Redis. The
`/api/health/scheduler` endpoint reads these records to surface scheduler
state without blowing up the main `/api/health` (which checks DB+Redis).

**What gets tracked, per `schedule_id`:**

    {
      "schedule_id":           str,
      "last_run":              ISO8601,        # release timestamp
      "last_status":           "success"|"error"|"missed_start_deadline"|...,
      "last_error":            str | None,
      "last_duration_ms":      int | None,     # started_at -> released
      "consecutive_failures":  int,
      "total_runs":            int,
      "total_failures":        int,
    }

Redis key: `scheduler_health:{schedule_id}` (TTL: 30 days).

Why this design:
- Per-schedule rather than global so we can tell "memory eviction works but DB
  sync is failing." Aggregate views are computed by the endpoint at read time.
- Redis (not Postgres) — the data is high-write/low-read, ephemeral, and the
  app already requires Redis. No new persistence layer.
- 30-day TTL — long enough for ops to investigate a stale failure, short enough
  that orphaned schedule entries (e.g. removed jobs) age out.
"""

import json
import logging
from datetime import datetime, timezone
from typing import TYPE_CHECKING, Any, Optional

from apscheduler import JobOutcome, JobReleased

if TYPE_CHECKING:
    from apscheduler import AsyncScheduler

logger = logging.getLogger(__name__)

_HEALTH_KEY_PREFIX = "scheduler_health:"
_HEALTH_TTL_SECONDS = 30 * 86400  # 30 days


def _health_key(schedule_id: str) -> str:
    return f"{_HEALTH_KEY_PREFIX}{schedule_id}"


def _outcome_to_status(outcome: JobOutcome) -> str:
    """Map JobOutcome enum to a stable string for log/JSON consumption."""
    return outcome.name if isinstance(outcome, JobOutcome) else str(outcome)


async def _read_record(redis: Any, schedule_id: str) -> dict:
    """Load the existing health record or return an empty default.

    Network/JSON failures fall through to a fresh record — observability code
    must never block job execution or surface its own bugs as job failures.
    """
    try:
        raw = await redis.get(_health_key(schedule_id))
        if raw:
            return json.loads(raw)
    except Exception as exc:  # pragma: no cover — best-effort
        logger.debug("scheduler_health read failed for %s: %s", schedule_id, exc)
    return {
        "schedule_id": schedule_id,
        "last_run": None,
        "last_status": None,
        "last_error": None,
        "last_duration_ms": None,
        "consecutive_failures": 0,
        "total_runs": 0,
        "total_failures": 0,
    }


def _apply_event(record: dict, event: JobReleased) -> dict:
    """Pure-function update — no I/O. Easy to unit-test."""
    status = _outcome_to_status(event.outcome)
    is_success = event.outcome == JobOutcome.success

    duration_ms: Optional[int] = None
    if event.started_at is not None:
        duration_ms = int(
            (event.timestamp - event.started_at).total_seconds() * 1000
        )

    record["last_run"] = event.timestamp.isoformat() if event.timestamp else None
    record["last_status"] = status
    record["last_error"] = (
        f"{event.exception_type}: {event.exception_message}"
        if event.exception_type or event.exception_message
        else None
    )
    record["last_duration_ms"] = duration_ms
    record["total_runs"] = int(record.get("total_runs", 0)) + 1
    if is_success:
        record["consecutive_failures"] = 0
    else:
        record["consecutive_failures"] = int(record.get("consecutive_failures", 0)) + 1
        record["total_failures"] = int(record.get("total_failures", 0)) + 1
    return record


async def _persist_record(redis: Any, schedule_id: str, record: dict) -> None:
    try:
        await redis.set(
            _health_key(schedule_id),
            json.dumps(record),
            ex=_HEALTH_TTL_SECONDS,
        )
    except Exception as exc:  # pragma: no cover — best-effort
        logger.warning(
            "scheduler_health persist failed for %s: %s",
            schedule_id, exc,
        )


def _structured_log(event: JobReleased, record: dict) -> None:
    """Emit one structured log line per job release.

    Format chosen so log aggregators can extract `consecutive_failures` for
    alerting (e.g. "alert when ANY job >= 3 consecutive failures").
    """
    fields = {
        "evt": "scheduler.job_released",
        "schedule_id": record["schedule_id"],
        "status": record["last_status"],
        "duration_ms": record["last_duration_ms"],
        "consecutive_failures": record["consecutive_failures"],
        "total_runs": record["total_runs"],
    }
    if record["last_error"]:
        fields["error"] = record["last_error"]
    is_failure = event.outcome != JobOutcome.success
    log_fn = logger.warning if is_failure else logger.info
    log_fn("scheduler.job_released %s", json.dumps(fields, default=str))


async def _on_job_released(event: JobReleased) -> None:
    """Single subscription callback. Importing redis lazily keeps tests
    decoupled (they patch get_redis directly)."""
    schedule_id = event.schedule_id or f"adhoc:{event.task_id}"
    try:
        from src.memory.conversation import get_redis
        redis = await get_redis()
        record = await _read_record(redis, schedule_id)
        record["schedule_id"] = schedule_id
        record = _apply_event(record, event)
        await _persist_record(redis, schedule_id, record)
        _structured_log(event, record)
    except Exception as exc:
        # Observability must never crash the scheduler — log and swallow.
        logger.error("scheduler observability listener failed: %s", exc)


def register_scheduler_health_listener(scheduler: "AsyncScheduler") -> None:
    """Subscribe the health listener to the scheduler's event bus.

    Idempotent: APScheduler's subscribe returns a fresh Subscription object
    each call. Multiple registrations would result in N copies of every log
    line, so callers (currently `start_scheduler`) should only call this once
    per scheduler instance.
    """
    scheduler.subscribe(_on_job_released, JobReleased)
    logger.info("Scheduler health listener registered (JobReleased)")


# --------------------------------------------------------------------------
# Read API — used by the FastAPI /api/health/scheduler endpoint and by tests.
# --------------------------------------------------------------------------

async def get_health_snapshot() -> dict[str, Any]:
    """Read all `scheduler_health:*` records from Redis and return an
    aggregate snapshot.

    Returns:
        {
          "status":  "healthy" | "degraded" | "unknown",
          "jobs":    [ <per-schedule record>, ... ],
          "summary": {
            "total_jobs": int,
            "any_failing": bool,
            "max_consecutive_failures": int,
          }
        }

    Aggregate status rules:
      - "unknown"    if no health records exist (scheduler hasn't fired any
                     jobs yet, or Redis was just flushed)
      - "degraded"   if any job has consecutive_failures >= 3 (alert threshold)
      - "healthy"    otherwise
    """
    from src.memory.conversation import get_redis
    redis = await get_redis()

    pattern = f"{_HEALTH_KEY_PREFIX}*"
    jobs: list[dict] = []
    try:
        async for key in redis.scan_iter(match=pattern, count=100):
            raw = await redis.get(key)
            if not raw:
                continue
            try:
                jobs.append(json.loads(raw))
            except json.JSONDecodeError:
                continue
    except Exception as exc:
        logger.warning("scheduler_health snapshot scan failed: %s", exc)
        return {"status": "unknown", "jobs": [], "summary": {"error": str(exc)}}

    if not jobs:
        return {
            "status": "unknown",
            "jobs": [],
            "summary": {"total_jobs": 0, "any_failing": False,
                        "max_consecutive_failures": 0},
        }

    max_consec = max((int(j.get("consecutive_failures", 0)) for j in jobs),
                     default=0)
    any_failing = max_consec > 0
    status = "degraded" if max_consec >= 3 else "healthy"

    return {
        "status": status,
        "jobs": jobs,
        "summary": {
            "total_jobs": len(jobs),
            "any_failing": any_failing,
            "max_consecutive_failures": max_consec,
            "checked_at": datetime.now(timezone.utc).isoformat(),
        },
    }
