"""APScheduler engine — async scheduler with PostgreSQL job store.

Jobs persist across container restarts (PRD §12).
"""

import logging
from typing import Optional, Callable

from apscheduler import AsyncScheduler, ConflictPolicy
from apscheduler.datastores.sqlalchemy import SQLAlchemyDataStore
from apscheduler.triggers.cron import CronTrigger
from apscheduler.triggers.interval import IntervalTrigger
from apscheduler.triggers.date import DateTrigger

from src.settings import settings
import importlib

logger = logging.getLogger(__name__)

_scheduler: Optional[AsyncScheduler] = None


def _get_sync_db_url() -> str:
    """Convert async DB URL to sync for APScheduler's SQLAlchemy data store."""
    return settings.database_url.replace("postgresql+asyncpg://", "postgresql://")


async def get_scheduler() -> AsyncScheduler:
    """Get or create the singleton scheduler instance."""
    global _scheduler
    if _scheduler is not None:
        return _scheduler

    data_store = SQLAlchemyDataStore(engine_or_url=_get_sync_db_url())

    _scheduler = AsyncScheduler(data_store=data_store)
    logger.info("APScheduler initialized with PostgreSQL job store")
    return _scheduler


async def start_scheduler() -> None:
    """Start the scheduler (call from main.py startup)."""
    scheduler = await get_scheduler()
    await scheduler.__aenter__()

    # Wire observability BEFORE registering jobs so the very first run is
    # captured in the health record (otherwise the listener misses startup-
    # adjacent fires).
    try:
        from src.scheduler.observability import register_scheduler_health_listener
        register_scheduler_health_listener(scheduler)
    except Exception as e:
        logger.warning("Could not register scheduler health listener: %s", e)

    # Add a periodic sync job to pick up tasks from DB (created by orchestration API)
    try:
        await add_interval_job(
            func_path="src.scheduler.engine:sync_tasks_from_db",
            job_id="_internal_sync_tasks_from_db",
            seconds=30,
            kwargs={},
        )
        logger.info("Added periodic DB sync job (every 30s)")
    except Exception as e:
        logger.warning("Could not add sync job: %s", e)

    # Nightly system maintenance: memory eviction (Mem0 cap enforcement).
    # `replace_existing` ensures schedule/args updates from code take effect
    # on every startup. UTC explicitly to dodge DST.
    try:
        await add_cron_job(
            func_path="src.scheduler.maintenance:nightly_memory_eviction",
            job_id="_internal_nightly_memory_eviction",
            cron_kwargs={"hour": 3, "minute": 0},
            kwargs={},
            timezone="UTC",
            replace_existing=True,
        )
        logger.info("Added nightly memory eviction job (03:00 UTC)")
    except Exception as e:
        logger.warning("Could not add nightly eviction job: %s", e)

    # Weekly OAuth heartbeat: prevents Google's 6-month idle revocation of
    # refresh tokens by exercising each connected user's workspace-mcp path
    # with a low-cost call. Per Google docs, a successful refresh-token
    # exchange resets the idle clock; the follow-on userinfo call confirms
    # the access token works (catches scope-revoke / password-change).
    try:
        await add_cron_job(
            func_path="src.scheduler.maintenance:weekly_oauth_heartbeat",
            job_id="_internal_weekly_oauth_heartbeat",
            cron_kwargs={"day_of_week": "mon", "hour": 9, "minute": 0},
            kwargs={},
            timezone="UTC",
            replace_existing=True,
        )
        logger.info("Added weekly OAuth heartbeat job (Mon 09:00 UTC)")
    except Exception as e:
        logger.warning("Could not add OAuth heartbeat job: %s", e)

    # Ensure the scheduler actually runs jobs
    try:
        await scheduler.start_in_background()
        logger.info("Scheduler started (background loop running)")
    except Exception as e:
        logger.error("Failed to start scheduler background loop: %s", e)
        raise


async def stop_scheduler() -> None:
    """Stop the scheduler gracefully."""
    global _scheduler
    if _scheduler is not None:
        await _scheduler.__aexit__(None, None, None)
        _scheduler = None
        logger.info("Scheduler stopped")


def _resolve_callable(func_path_or_callable: str | Callable) -> Callable:
    """Resolve a dotted path like 'pkg.mod:func' to a callable; pass through callables."""
    if callable(func_path_or_callable):
        return func_path_or_callable  # type: ignore[return-value]
    if ":" not in func_path_or_callable:
        raise ValueError(f"Invalid func path '{func_path_or_callable}' (expected 'module:func')")
    module_path, func_name = func_path_or_callable.split(":", 1)
    module = importlib.import_module(module_path)
    func = getattr(module, func_name, None)
    if not callable(func):
        raise ValueError(f"Resolved '{func_path_or_callable}' but attribute is not callable")
    return func  # type: ignore[return-value]


async def add_cron_job(
    func_path: str | Callable,
    job_id: str,
    cron_kwargs: dict,
    args: Optional[list] = None,
    kwargs: Optional[dict] = None,
    *,
    timezone: Optional[str] = None,
    replace_existing: bool = False,
) -> str:
    """Add a cron-triggered job.

    Args:
        func_path: Dotted path to the async callable (e.g. 'src.scheduler.jobs:send_reminder')
        job_id: Unique job identifier
        cron_kwargs: CronTrigger kwargs (day_of_week, hour, minute, etc.)
        args: Positional args for the callable
        kwargs: Keyword args for the callable
        timezone: Override the scheduler's default timezone. Pass "UTC" for
            system-level maintenance jobs to avoid DST gotchas (per APScheduler
            maintainer guidance — DST transitions can skip or double-fire jobs
            on tz-aware schedules).
        replace_existing: If True, re-register the job even when one with this
            ID already exists in the data store. Useful for system jobs that
            are re-installed on every startup so code changes (new schedule,
            new args) take effect.

    Returns:
        The job ID.
    """
    func = _resolve_callable(func_path)  # Validate early — raises ValueError if path is broken
    scheduler = await get_scheduler()
    tz = timezone or settings.default_timezone
    trigger = CronTrigger(**cron_kwargs, timezone=tz)
    add_kwargs: dict = {
        "func_or_task_id": func,
        "trigger": trigger,
        "id": job_id,
        "args": args or [],
        "kwargs": kwargs or {},
    }
    if replace_existing:
        add_kwargs["conflict_policy"] = ConflictPolicy.replace
    await scheduler.add_schedule(**add_kwargs)
    logger.info("Cron job added: %s (%s, tz=%s)", job_id, cron_kwargs, tz)
    return job_id


async def add_interval_job(
    func_path: str | Callable,
    job_id: str,
    seconds: Optional[int] = None,
    minutes: Optional[int] = None,
    hours: Optional[int] = None,
    args: Optional[list] = None,
    kwargs: Optional[dict] = None,
) -> str:
    """Add an interval-triggered job."""
    func = _resolve_callable(func_path)  # Validate early — raises ValueError if path is broken
    scheduler = await get_scheduler()
    trigger_kwargs = {}
    if seconds is not None:
        trigger_kwargs["seconds"] = seconds
    if minutes is not None:
        trigger_kwargs["minutes"] = minutes
    if hours is not None:
        trigger_kwargs["hours"] = hours

    trigger = IntervalTrigger(**trigger_kwargs)
    await scheduler.add_schedule(
        func_or_task_id=func,
        trigger=trigger,
        id=job_id,
        args=args or [],
        kwargs=kwargs or {},
    )
    logger.info("Interval job added: %s (every %s)", job_id, trigger_kwargs)
    return job_id


async def add_one_shot_job(
    func_path: str | Callable,
    job_id: str,
    run_at: str,
    args: Optional[list] = None,
    kwargs: Optional[dict] = None,
) -> str:
    """Add a one-shot job that runs at a specific datetime.

    Args:
        run_at: ISO format datetime string.
    """
    from datetime import datetime
    from zoneinfo import ZoneInfo

    scheduler = await get_scheduler()
    dt = datetime.fromisoformat(run_at)
    # Ensure timezone-aware — attach configured TZ if the LLM omits it
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=ZoneInfo(settings.default_timezone))
    trigger = DateTrigger(run_time=dt)
    func = _resolve_callable(func_path)
    await scheduler.add_schedule(
        func_or_task_id=func,
        trigger=trigger,
        id=job_id,
        args=args or [],
        kwargs=kwargs or {},
    )
    logger.info("One-shot job added: %s (at %s)", job_id, run_at)
    return job_id


async def remove_job(job_id: str) -> bool:
    """Remove a scheduled job by ID."""
    scheduler = await get_scheduler()
    try:
        await scheduler.remove_schedule(job_id)
        logger.info("Job removed: %s", job_id)
        return True
    except Exception as e:
        logger.error("Failed to remove job %s: %s", job_id, e)
        return False


async def get_all_jobs() -> list[dict]:
    """List all scheduled jobs with their metadata."""
    scheduler = await get_scheduler()
    try:
        schedules = await scheduler.get_schedules()
        return [
            {
                "id": s.id,
                "task_id": s.task_id,
                "next_fire_time": str(s.next_fire_time) if s.next_fire_time else None,
            }
            for s in schedules
        ]
    except Exception as e:
        logger.error("Failed to list jobs: %s", e)
        return []


async def sync_tasks_from_db() -> dict:
    """Sync tasks from scheduled_tasks table to APScheduler.

    This is called periodically by the assistant to pick up tasks
    created by the orchestration API or other sources.

    Returns summary of actions taken.
    """
    from sqlalchemy import select
    from src.db.session import async_session
    from src.db.models import ScheduledTask

    added = []
    skipped = []
    errors = []

    try:
        async with async_session() as session:
            # Get all active tasks that should be in the scheduler
            result = await session.execute(
                select(ScheduledTask)
            )
            tasks = result.scalars().all()

            # Get current jobs in scheduler
            scheduler = await get_scheduler()
            current_schedules = await scheduler.get_schedules()
            current_job_ids = {s.id for s in current_schedules}

            for task in tasks:
                # If paused in DB, ensure it's not scheduled live
                if not task.is_active:
                    if task.apscheduler_id in current_job_ids:
                        try:
                            await scheduler.remove_schedule(task.apscheduler_id)
                            logger.info("Removed paused job from scheduler: %s", task.apscheduler_id)
                        except Exception as e:
                            logger.warning("Could not remove paused job %s: %s", task.apscheduler_id, e)
                    skipped.append(task.apscheduler_id)
                    continue

                # Active: if already present, skip; else add
                if task.apscheduler_id in current_job_ids:
                    skipped.append(task.apscheduler_id)
                    continue

                try:
                    func_path = task.job_function
                    kwargs = task.job_args or {"user_id": task.user_id, "message": task.description}
                    if task.trigger_type == "cron":
                        cron = task.trigger_config.get("cron", {})
                        await add_cron_job(
                            func_path=func_path,
                            job_id=task.apscheduler_id,
                            cron_kwargs={
                                "hour": cron.get("hour", 9),
                                "minute": cron.get("minute", 0),
                                "day_of_week": cron.get("day_of_week", "*"),
                            },
                            kwargs=kwargs,
                        )
                    elif task.trigger_type == "interval":
                        interval = task.trigger_config.get("interval", {})
                        seconds = interval.get("seconds", 3600)
                        await add_interval_job(
                            func_path=func_path,
                            job_id=task.apscheduler_id,
                            seconds=seconds,
                            kwargs=kwargs,
                        )
                    elif task.trigger_type == "once":
                        once = task.trigger_config.get("once", {})
                        run_at_str = once.get("run_at")
                        if run_at_str:
                            from datetime import datetime
                            run_at_iso = datetime.fromisoformat(
                                run_at_str.replace("Z", "+00:00")
                            ).isoformat()
                            await add_one_shot_job(
                                func_path=func_path,
                                job_id=task.apscheduler_id,
                                run_at=run_at_iso,
                                kwargs=kwargs,
                            )

                    added.append(task.apscheduler_id)
                    logger.info("Synced task %s to scheduler", task.apscheduler_id)

                except Exception as e:
                    logger.error("Failed to sync task %s: %s", task.apscheduler_id, e)
                    errors.append(f"{task.apscheduler_id}: {e}")
                    try:
                        from src.db.models import AuditLog
                        session.add(AuditLog(
                            user_id=task.user_id,
                            direction="outbound",
                            platform="scheduler",
                            agent_name="apscheduler",
                            message_text=f"Failed to sync job {task.apscheduler_id}",
                            tools_used={
                                "error": True,
                                "job_id": task.apscheduler_id,
                                "trigger_type": task.trigger_type,
                                "trigger_config": task.trigger_config,
                                "exception": str(e),
                            },
                        ))
                        await session.commit()
                    except Exception:
                        # Avoid crashing sync due to logging issues
                        pass

    except Exception as e:
        logger.error("DB sync failed: %s", e)
        return {"error": str(e)}

    summary = {
        "added": len(added),
        "skipped": len(skipped),
        "errors": len(errors),
        "added_jobs": added,
        "error_details": errors,
    }
    logger.info("Scheduler DB sync summary: added=%d skipped=%d errors=%d", len(added), len(skipped), len(errors))
    return summary
