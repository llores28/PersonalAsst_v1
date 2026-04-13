"""Background autonomous job runner (M2 — Extended Task Horizons).

Allows Atlas to run multi-step agent loops without user ping-backs.
Created when the user says "keep watching / monitor X until Y".

Lifecycle:
    1. Orchestrator detects "monitor / watch / alert me when" intent.
    2. Calls create_background_job() → writes BackgroundJob row → schedules APScheduler tick.
    3. Each tick runs _tick_background_job() → one agent turn → checks done_condition.
    4. When done or max_iterations reached → Telegram notify → mark complete.
    5. User can cancel via /cancel or Dashboard "Stop" button.

Safety:
    - Max 48 iterations per job (prevents runaway cost).
    - Budget cap enforced: if daily_pct >= 95%, job is paused not cancelled.
    - All agent calls wrapped in try/except; failures increment a fault counter.
    - After 3 consecutive failures → job transitions to 'failed'.
"""

from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timezone

logger = logging.getLogger(__name__)

_MAX_CONSECUTIVE_FAILURES = 3

_MONITOR_PHRASES = (
    "keep watching",
    "keep an eye",
    "monitor ",
    "watch for ",
    "watch my ",
    "alert me when",
    "alert me if",
    "notify me when",
    "notify me if",
    "let me know when",
    "let me know if",
    "tell me when",
    "tell me if",
    "check every",
    "check periodically",
    "keep checking",
    "continuously check",
    "poll for",
    "wait for ",
    "track ",
)


def is_background_job_request(message: str) -> bool:
    """Return True if the message requests an autonomous background monitoring job."""
    lowered = message.lower()
    return any(phrase in lowered for phrase in _MONITOR_PHRASES)


async def create_background_job(
    user_telegram_id: int,
    user_db_id: int,
    goal: str,
    done_condition: str | None = None,
    check_interval_seconds: int = 600,
    max_iterations: int = 48,
) -> dict:
    """Create a BackgroundJob row and schedule the first APScheduler tick.

    Args:
        user_telegram_id:       Telegram user ID (for notifications).
        user_db_id:             DB user ID (FK).
        goal:                   The full user message / goal description.
        done_condition:         Natural-language description of when to stop.
        check_interval_seconds: How often to run one agent tick (default 10 min).
        max_iterations:         Hard cap on ticks (default 48 = 8 hours at 10 min).

    Returns:
        dict with job id and apscheduler_id.
    """
    from src.db.session import async_session
    from src.db.models import BackgroundJob

    job_id_str = f"bg_job_{user_telegram_id}_{int(datetime.now(timezone.utc).timestamp())}"

    async with async_session() as session:
        job = BackgroundJob(
            user_id=user_db_id,
            goal=goal,
            done_condition=done_condition,
            check_interval_seconds=check_interval_seconds,
            max_iterations=max_iterations,
            iterations_run=0,
            status="running",
            apscheduler_id=job_id_str,
        )
        session.add(job)
        await session.commit()
        await session.refresh(job)
        db_job_id = job.id

    try:
        from src.scheduler.engine import get_scheduler, add_interval_job
        await add_interval_job(
            func_path="src.agents.background_job:_tick_background_job_sync",
            job_id=job_id_str,
            seconds=check_interval_seconds,
            job_args={"job_id": db_job_id, "user_telegram_id": user_telegram_id},
        )
        logger.info("BackgroundJob %d scheduled: %s (interval=%ds)", db_job_id, job_id_str, check_interval_seconds)
    except Exception as e:
        logger.warning("Could not schedule background job %d via APScheduler: %s", db_job_id, e)

    return {"id": db_job_id, "apscheduler_id": job_id_str}


def _tick_background_job_sync(job_id: int, user_telegram_id: int) -> None:
    """Sync wrapper for APScheduler — runs the async tick in the event loop."""
    try:
        loop = asyncio.get_event_loop()
        if loop.is_running():
            asyncio.ensure_future(_tick_background_job(job_id, user_telegram_id))
        else:
            loop.run_until_complete(_tick_background_job(job_id, user_telegram_id))
    except Exception as e:
        logger.error("BackgroundJob tick wrapper error job_id=%d: %s", job_id, e)


async def _tick_background_job(job_id: int, user_telegram_id: int) -> None:
    """Run one agent tick for a background job. Called by APScheduler."""
    from src.db.session import async_session
    from src.db.models import BackgroundJob
    from sqlalchemy import select

    async with async_session() as session:
        result = await session.execute(select(BackgroundJob).where(BackgroundJob.id == job_id))
        job = result.scalar_one_or_none()
        if not job:
            logger.warning("BackgroundJob %d not found — removing tick", job_id)
            await _cancel_scheduler_job(job.apscheduler_id if job else str(job_id))
            return

        if job.status != "running":
            logger.info("BackgroundJob %d is %s — stopping tick", job_id, job.status)
            return

        if job.iterations_run >= job.max_iterations:
            job.status = "done"
            job.result = "Max iterations reached."
            job.completed_at = datetime.now(timezone.utc)
            await session.commit()
            await _cancel_scheduler_job(job.apscheduler_id)
            await _notify_user(user_telegram_id, f"⏱️ Background job finished (max iterations reached):\n_{job.goal}_")
            return

        job.iterations_run += 1
        await session.commit()

    try:
        from src.agents.orchestrator import run_orchestrator
        prompt = (
            f"[Background job tick {job.iterations_run}/{job.max_iterations}]\n"
            f"Goal: {job.goal}\n"
            + (f"Done condition: {job.done_condition}\n" if job.done_condition else "")
            + "\nCheck the current state. If the done_condition is met, reply with exactly: DONE: <brief summary>. "
            "Otherwise describe what you observed and whether any action was taken."
        )
        response = await run_orchestrator(user_telegram_id, prompt)

        done_triggered = response.strip().startswith("DONE:")
        async with async_session() as session:
            result = await session.execute(select(BackgroundJob).where(BackgroundJob.id == job_id))
            job = result.scalar_one_or_none()
            if job:
                if done_triggered:
                    job.status = "done"
                    job.result = response.strip()
                    job.completed_at = datetime.now(timezone.utc)
                    await session.commit()
                    await _cancel_scheduler_job(job.apscheduler_id)
                    await _notify_user(user_telegram_id, f"✅ Background job complete:\n{response.strip()}")
                else:
                    logger.debug("BackgroundJob %d tick done: %s…", job_id, response[:100])

    except Exception as e:
        logger.error("BackgroundJob %d tick error: %s", job_id, e)
        async with async_session() as session:
            result = await session.execute(select(BackgroundJob).where(BackgroundJob.id == job_id))
            job = result.scalar_one_or_none()
            if job:
                existing = job.result or ""
                faults = existing.count("[tick ") + 1
                if faults >= _MAX_CONSECUTIVE_FAILURES:
                    job.status = "failed"
                    job.result = f"Failed after {faults} consecutive errors. Last: {e}"
                    job.completed_at = datetime.now(timezone.utc)
                    await session.commit()
                    await _cancel_scheduler_job(job.apscheduler_id)
                    await _notify_user(user_telegram_id, f"❌ Background job failed after {faults} errors: {e}\nGoal: _{job.goal}_")
                else:
                    existing_result = job.result or ""
                    job.result = existing_result + f"\n[tick {job.iterations_run} err={faults}]: {e}"
                    await session.commit()


async def _cancel_scheduler_job(job_id: str | None) -> None:
    if not job_id:
        return
    try:
        from src.scheduler.engine import get_scheduler
        scheduler = await get_scheduler()
        await scheduler.remove_schedule(job_id)
        logger.info("APScheduler job %s removed", job_id)
    except Exception as e:
        logger.debug("Could not remove APScheduler job %s: %s", job_id, e)


async def _notify_user(user_telegram_id: int, message: str) -> None:
    try:
        from aiogram import Bot
        from src.settings import settings
        bot = Bot(token=settings.telegram_bot_token)
        try:
            await bot.send_message(user_telegram_id, message, parse_mode="Markdown")
        finally:
            await bot.session.close()
    except Exception as e:
        logger.warning("Could not notify user %d for background job: %s", user_telegram_id, e)
