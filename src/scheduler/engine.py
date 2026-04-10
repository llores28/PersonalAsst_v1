"""APScheduler engine — async scheduler with PostgreSQL job store.

Jobs persist across container restarts (PRD §12).
"""

import logging
from typing import Optional

from apscheduler import AsyncScheduler
from apscheduler.datastores.sqlalchemy import SQLAlchemyDataStore
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
    from zoneinfo import ZoneInfo

    scheduler = await get_scheduler()
    dt = datetime.fromisoformat(run_at)
    # Ensure timezone-aware — attach configured TZ if the LLM omits it
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=ZoneInfo(settings.default_timezone))
    trigger = DateTrigger(run_time=dt)
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
                select(ScheduledTask).where(ScheduledTask.is_active == True)
            )
            tasks = result.scalars().all()

            # Get current jobs in scheduler
            scheduler = await get_scheduler()
            current_schedules = await scheduler.get_schedules()
            current_job_ids = {s.id for s in current_schedules}

            for task in tasks:
                if task.apscheduler_id in current_job_ids:
                    skipped.append(task.apscheduler_id)
                    continue

                # This task needs to be added to scheduler
                try:
                    if task.trigger_type == "cron":
                        cron = task.trigger_config.get("cron", {})
                        await add_cron_job(
                            func_path="src.scheduler.jobs:send_reminder",
                            job_id=task.apscheduler_id,
                            cron_kwargs={
                                "hour": cron.get("hour", 9),
                                "minute": cron.get("minute", 0),
                                "day_of_week": cron.get("day_of_week", "*"),
                            },
                            kwargs={"user_id": task.user_id, "message": task.description},
                        )
                    elif task.trigger_type == "interval":
                        interval = task.trigger_config.get("interval", {})
                        seconds = interval.get("seconds", 3600)
                        await add_interval_job(
                            func_path="src.scheduler.jobs:send_reminder",
                            job_id=task.apscheduler_id,
                            seconds=seconds,
                            kwargs={"user_id": task.user_id, "message": task.description},
                        )
                    elif task.trigger_type == "once":
                        once = task.trigger_config.get("once", {})
                        run_at_str = once.get("run_at")
                        if run_at_str:
                            from datetime import datetime, timezone
                            run_at = datetime.fromisoformat(run_at_str.replace("Z", "+00:00"))
                            await add_one_shot_job(
                                func_path="src.scheduler.jobs:send_reminder",
                                job_id=task.apscheduler_id,
                                run_at=run_at,
                                kwargs={"user_id": task.user_id, "message": task.description},
                            )

                    added.append(task.apscheduler_id)
                    logger.info("Synced task %s to scheduler", task.apscheduler_id)

                except Exception as e:
                    logger.error("Failed to sync task %s: %s", task.apscheduler_id, e)
                    errors.append(f"{task.apscheduler_id}: {e}")

    except Exception as e:
        logger.error("DB sync failed: %s", e)
        return {"error": str(e)}

    return {
        "added": len(added),
        "skipped": len(skipped),
        "errors": len(errors),
        "added_jobs": added,
        "error_details": errors,
    }
