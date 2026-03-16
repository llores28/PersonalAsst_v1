"""APScheduler engine — async scheduler with PostgreSQL job store.

Jobs persist across container restarts (PRD §12).
"""

import logging
from typing import Optional

from apscheduler import AsyncScheduler
from apscheduler.datastores.sqlalchemy import SQLAlchemyDataStore
from apscheduler.eventbrokers.asyncpg import AsyncpgEventBroker
from apscheduler.triggers.cron import CronTrigger
from apscheduler.triggers.interval import IntervalTrigger
from apscheduler.triggers.date import DateTrigger

from src.settings import settings

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
    logger.info("Scheduler started")


async def stop_scheduler() -> None:
    """Stop the scheduler gracefully."""
    global _scheduler
    if _scheduler is not None:
        await _scheduler.__aexit__(None, None, None)
        _scheduler = None
        logger.info("Scheduler stopped")


async def add_cron_job(
    func_path: str,
    job_id: str,
    cron_kwargs: dict,
    args: Optional[list] = None,
    kwargs: Optional[dict] = None,
) -> str:
    """Add a cron-triggered job.

    Args:
        func_path: Dotted path to the async callable (e.g. 'src.scheduler.jobs:send_reminder')
        job_id: Unique job identifier
        cron_kwargs: CronTrigger kwargs (day_of_week, hour, minute, etc.)
        args: Positional args for the callable
        kwargs: Keyword args for the callable

    Returns:
        The job ID.
    """
    scheduler = await get_scheduler()
    trigger = CronTrigger(**cron_kwargs, timezone=settings.default_timezone)
    await scheduler.add_schedule(
        func_or_task_id=func_path,
        trigger=trigger,
        id=job_id,
        args=args or [],
        kwargs=kwargs or {},
    )
    logger.info("Cron job added: %s (%s)", job_id, cron_kwargs)
    return job_id


async def add_interval_job(
    func_path: str,
    job_id: str,
    seconds: Optional[int] = None,
    minutes: Optional[int] = None,
    hours: Optional[int] = None,
    args: Optional[list] = None,
    kwargs: Optional[dict] = None,
) -> str:
    """Add an interval-triggered job."""
    scheduler = await get_scheduler()
    trigger_kwargs = {}
    if seconds:
        trigger_kwargs["seconds"] = seconds
    if minutes:
        trigger_kwargs["minutes"] = minutes
    if hours:
        trigger_kwargs["hours"] = hours

    trigger = IntervalTrigger(**trigger_kwargs)
    await scheduler.add_schedule(
        func_or_task_id=func_path,
        trigger=trigger,
        id=job_id,
        args=args or [],
        kwargs=kwargs or {},
    )
    logger.info("Interval job added: %s (every %s)", job_id, trigger_kwargs)
    return job_id


async def add_one_shot_job(
    func_path: str,
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

    scheduler = await get_scheduler()
    dt = datetime.fromisoformat(run_at)
    trigger = DateTrigger(run_date=dt)
    await scheduler.add_schedule(
        func_or_task_id=func_path,
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
