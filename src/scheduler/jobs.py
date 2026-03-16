"""Job callables — async functions invoked by APScheduler.

All jobs follow the same pattern: look up user, run task, send result via Telegram.
Resolves PRD gap A5 (job callable signatures).
"""

import asyncio
import logging
from typing import Optional

from src.settings import settings

logger = logging.getLogger(__name__)


async def _get_bot():
    """Get the Telegram bot instance for sending messages."""
    from aiogram import Bot
    return Bot(token=settings.telegram_bot_token)


async def _get_telegram_id(user_id: int) -> Optional[int]:
    """Look up Telegram ID from internal user ID."""
    from sqlalchemy import select
    from src.db.session import async_session
    from src.db.models import User

    async with async_session() as session:
        result = await session.execute(
            select(User.telegram_id).where(User.id == user_id)
        )
        return result.scalar_one_or_none()


async def send_reminder(user_id: int, message: str) -> None:
    """Send a text reminder to the user via Telegram."""
    telegram_id = await _get_telegram_id(user_id)
    if not telegram_id:
        logger.error("send_reminder: user %d not found", user_id)
        return

    bot = await _get_bot()
    try:
        await bot.send_message(telegram_id, f"⏰ **Reminder:** {message}", parse_mode="Markdown")
    finally:
        await bot.session.close()

    logger.info("Reminder sent to user %d: %s", user_id, message[:50])


async def run_agent_task(user_id: int, prompt: str) -> None:
    """Run the orchestrator agent with a prompt and send results to user."""
    telegram_id = await _get_telegram_id(user_id)
    if not telegram_id:
        logger.error("run_agent_task: user %d not found", user_id)
        return

    from src.agents.orchestrator import run_orchestrator

    try:
        result = await asyncio.wait_for(
            run_orchestrator(telegram_id, prompt),
            timeout=settings.agent_timeout_seconds,
        )
        bot = await _get_bot()
        try:
            await bot.send_message(telegram_id, result, parse_mode="Markdown")
        finally:
            await bot.session.close()
    except asyncio.TimeoutError:
        logger.error("run_agent_task timed out for user %d", user_id)
    except Exception as e:
        logger.exception("run_agent_task failed for user %d: %s", user_id, e)


async def summarize_new_emails(user_id: int) -> None:
    """Check for new emails and send summary to user."""
    await run_agent_task(
        user_id,
        "Check for new unread emails since last check. Summarize the important ones briefly.",
    )


async def morning_brief(user_id: int) -> None:
    """Daily morning briefing: calendar + email + reminders."""
    await run_agent_task(
        user_id,
        "Give me my morning brief: today's calendar events, important unread emails, "
        "and any pending tasks or reminders.",
    )


async def safe_job_wrapper(job_func, *args, **kwargs) -> None:
    """Wrap any job callable with error handling and timeout."""
    try:
        await asyncio.wait_for(
            job_func(*args, **kwargs),
            timeout=settings.agent_timeout_seconds,
        )
    except asyncio.TimeoutError:
        logger.error("Job %s timed out after %ds", job_func.__name__, settings.agent_timeout_seconds)
    except Exception as e:
        logger.exception("Job %s failed: %s", job_func.__name__, e)
        # Attempt to notify user of critical job failure
        if args and isinstance(args[0], int):
            try:
                telegram_id = await _get_telegram_id(args[0])
                if telegram_id:
                    bot = await _get_bot()
                    try:
                        await bot.send_message(
                            telegram_id,
                            f"⚠️ A scheduled task failed: {job_func.__name__}\nError: {str(e)[:200]}",
                        )
                    finally:
                        await bot.session.close()
            except Exception:
                pass
